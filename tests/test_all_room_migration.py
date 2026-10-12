"""Migration wiring and real HA engine coverage for concurrent room control."""
from copy import deepcopy
from datetime import datetime, timedelta, UTC
import json
from pathlib import Path

import pytest
import pytest_asyncio
import time_machine
from homeassistant import config_entries, loader
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from conftest import expand
from migrations.all_room_zigbee import ROOMS, KEEP, LIFECYCLE, build_controllers, assert_no_active_ble

BASE = json.loads((Path(__file__).parent / "fixtures/remaining_ble_controllers.json").read_text())


def test_migration_preserves_policy_identity_and_unrelated_configuration():
    original = deepcopy(BASE) + [{"id": "unrelated", "alias": "Lighting", "triggers": [], "actions": []}]
    before = deepcopy(original)
    migrated, helpers, package = build_controllers(original)
    assert original == before
    lookup = {x["id"]: x for x in migrated}
    for room, info in ROOMS.items():
        source = next(x for x in original if x["id"] == info["id"])
        candidate = lookup[info["id"]]
        old, new = source["use_blueprint"]["input"], candidate["use_blueprint"]["input"]
        assert {k: new[k] for k in KEEP} == {k: old[k] for k in KEEP}
        assert candidate["alias"] == source["alias"]
        assert new["gateway_switch"] == f"switch.{room}_hvac_toggle"
        assert new["fingerbot_mode"] == f"select.{room}_hvac_toggle_mode"
        assert new["controller_entity"] == info["controller"]
        assert new["zha_min_off_minutes"] == old.get("cooldown_minutes", 10)
        assert new["raw_temp_sensor"] == old["temp_sensor"]
        assert "gateway_connect" not in new and "gateway_status" not in new
        assert "mode" not in candidate  # Inherit queued ownership from blueprint.
        assert lookup[info["listener"]]["initial_state"] is False
        assert lookup[info["listener"]]["actions"] == []
        assert lookup[info["listener"]]["conditions"][0]["value_template"] == "{{ false }}"
    assert migrated[-1] == before[-1]
    assert lookup["1788478600000"]["actions"] == []
    assert_no_active_ble(migrated)
    assert len({(h["domain"],h["id"]) for h in helpers}) == 8
    assert all("initial" not in h["config"] for h in helpers)  # Journals restore.
    assert package["template"][0]["sensor"][0]["unique_id"] == "hvac_command_lifecycle"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_switch", "wrong_interval", "unknown_room"])
def test_unexpected_baselines_are_rejected(mutation):
    source = deepcopy(BASE)
    if mutation == "missing": source.pop(0)
    elif mutation == "duplicate": source.append(deepcopy(source[0]))
    elif mutation == "wrong_switch": source[0]["use_blueprint"]["input"]["gateway_switch"] = "switch.wrong_room"
    elif mutation == "wrong_interval": source[0]["use_blueprint"]["input"]["post_on_cooldown_minutes"] = 2
    else: source[-1]["variables"]["rooms"].append({"key":"unexpected"})
    with pytest.raises(ValueError): build_controllers(source)


def test_unhandled_direct_legacy_writer_blocks_deployment():
    candidate, _, _ = build_controllers(BASE)
    candidate.append({"id":"rogue", "actions":[{"action":"switch.toggle", "target":{"entity_id":ROOMS["study"]["old"]}}]})
    with pytest.raises(ValueError, match="rogue"): assert_no_active_ble(candidate)


class RoomsRig:
    def __init__(self, hass, clock, configs):
        self.hass, self.clock, self.configs = hass, clock, configs
        self.now = datetime(2026,10,12,7,0,tzinfo=UTC)
        self.presses = []
        self.fail = set()
    async def settle(self): await self.hass.async_block_till_done()
    async def set(self, eid, value):
        self.hass.states.async_set(eid,str(value)); await self.settle()
    async def advance(self, seconds):
        self.now += timedelta(seconds=seconds); self.clock.move_to(self.now); await self.settle()
    async def tick(self):
        for inp in self.configs.values():
            for k in ("cooldown_timer","off_cooldown_timer"):
                self.hass.states.async_set(inp[k],"idle")
                self.hass.bus.async_fire("timer.finished", {"entity_id":inp[k]})
        await self.settle()
    async def temperatures(self, value):
        for inp in self.configs.values(): await self.set(inp["temp_sensor"],value)
    def record(self, room): return json.loads(self.hass.states.get(self.configs[room]["command_record"]).state)


@pytest_asyncio.fixture
async def rooms_rig(tmp_path):
    hass = HomeAssistant(str(tmp_path)); loader.async_setup(hass); hass.config.skip_pip = True
    hass.config_entries = config_entries.ConfigEntries(hass,{})
    assert await async_load_base_functionality(hass)
    candidates, helpers, lifecycle = build_controllers(BASE)
    by_id={x["id"]:x for x in candidates}
    inputs={room:by_id[info["id"]]["use_blueprint"]["input"] for room,info in ROOMS.items()}
    with time_machine.travel(datetime(2026,10,12,7,0,tzinfo=UTC),tick=False) as clock:
        rig=RoomsRig(hass,clock,inputs)
        config={"input_text":{},"input_number":{},"input_boolean":{},"input_select":{"house_hvac_mode":{"options":["Cooling","Off"],"initial":"Cooling"}},"timer":{}}
        for helper in helpers: config[helper["domain"]][helper["id"]]=helper["config"]
        for inp in inputs.values():
            config["input_boolean"][inp["cooling_active"].split(".",1)[1]]={"initial":False}
            for k,value in (("target_temp",21),("temp_at_toggle",22)):
                config["input_number"][inp[k].split(".",1)[1]]={"min":-20,"max":99,"initial":value}
            for k in ("cooldown_timer","off_cooldown_timer"):
                config["timer"][inp[k].split(".",1)[1]]={}
        for domain in config: assert await async_setup_component(hass,domain,config)
        assert await async_setup_component(hass,"template",lifecycle)
        for n,(room,inp) in enumerate(inputs.items()):
            await rig.set(inp["temp_sensor"],22)
            await rig.set(inp["fingerbot_mode"],"CLICK")
            await rig.set(inp["gateway_switch"],"on" if n%2 else "off")
        async def press(call):
            eid=call.data["entity_id"];eid=eid[0] if isinstance(eid,list) else eid
            rig.presses.append(eid)
            if eid in rig.fail: raise RuntimeError("simulated lost Zigbee command")
            hass.states.async_set(eid,"off" if hass.states.get(eid).state=="on" else "on",context=call.context)
        hass.services.async_register("switch","toggle",press)
        autos=[dict(expand("hvac_recovering_thermostat.yaml",inputs[room]),id=info["id"],alias=by_id[info["id"]]["alias"],initial_state=True) for room,info in ROOMS.items()]
        assert await async_setup_component(hass,"automation",{"automation":autos})
        await hass.async_start();await rig.settle()
        yield rig
        await hass.async_stop(force=True)


async def test_all_rooms_adopt_without_press_then_control_independently(rooms_rig):
    r=rooms_rig
    assert not r.presses
    assert all(r.record(room)["reason"]=="estimate_adopted" for room in ROOMS)
    await r.advance(779);await r.tick();assert not r.presses
    await r.advance(1);await r.tick()
    assert sorted(r.presses)==sorted(inp["gateway_switch"] for inp in r.configs.values())
    for room in ROOMS:
        assert r.record(room)["expected"]=="on"
        assert r.record(room)["reason"]=="zigbee_ack"
    await r.temperatures(20.5)
    await r.advance(299);await r.tick();assert len(r.presses)==4
    await r.advance(1);await r.tick();assert len(r.presses)==8
    assert all(r.record(room)["expected"]=="off" for room in ROOMS)
    await r.temperatures(22)
    await r.advance(599);await r.tick();assert len(r.presses)==8
    await r.advance(1);await r.tick();assert len(r.presses)==11
    assert r.record("living_room")["expected"]=="off"
    await r.advance(180);await r.tick();assert len(r.presses)==12
    assert all(r.record(room)["expected"]=="on" for room in ROOMS)


async def test_one_failed_room_does_not_block_others_or_retry_early(rooms_rig):
    r=rooms_rig;failed=r.configs["study"]["gateway_switch"];r.fail.add(failed)
    await r.advance(780);await r.tick()
    assert r.record("study")["phase"]=="pending"
    assert all(r.record(room)["reason"]=="zigbee_ack" for room in ROOMS if room!="study")
    await r.advance(779);await r.tick();assert r.presses.count(failed)==1
    r.fail.clear();await r.advance(1);await r.tick()
    assert r.presses.count(failed)==2 and r.record("study")["reason"]=="zigbee_ack"


async def test_wrong_mode_recovers_without_cross_room_bookkeeping(rooms_rig):
    r=rooms_rig;inp=r.configs["master_bedroom"]
    await r.set(inp["fingerbot_mode"],"SWITCH")
    await r.advance(780);await r.tick()
    assert inp["gateway_switch"] not in r.presses
    assert len(r.presses)==3
    previous={room:r.record(room) for room in ROOMS if room!="master_bedroom"}
    await r.set(inp["fingerbot_mode"],"CLICK")
    assert len(r.presses)==4 and r.record("master_bedroom")["expected"]=="on"
    assert previous=={room:r.record(room) for room in previous}


def test_yaml_patch_keeps_unrelated_bytes_and_rejects_unknown_ids():
    from migrations.build_remaining_zigbee import patch_automations
    source = "# before\n- id: 'changed'\n  alias: Original\n  actions: []\n# untouched comment\n- id: 'unrelated'\n  alias: 'Keep   spacing'\n  actions: []\n"
    untouched=source[source.index("- id: 'unrelated'"):]
    result=patch_automations(source,{"changed":{"id":"changed","alias":"New","actions":[]}})
    assert result.startswith("# before\n") and result.endswith(untouched)
    with pytest.raises(ValueError):patch_automations(source,{"missing":{"id":"missing"}})
