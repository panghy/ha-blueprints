"""Room-band scope and real Home Assistant threshold/timing behavior."""
from copy import deepcopy

import pytest

from migrations.all_room_zigbee import ROOMS, build_controllers
from migrations.room_temperature_band import BLUEPRINT, NURSERY_BAND, build_bands
from test_all_room_migration import BASE, rooms_rig

REFERENCE = {"id": "nursery_guarded_thermostat", "use_blueprint": {
    "path": BLUEPRINT, "input": {"controller_entity": "automation.nursery_guarded_thermostat",
    "command_transport": "zha_click", "upper_offset": 0.4, "lower_offset": -0.1,
    "zha_min_off_minutes": 5}}}
TARGETS = {"study": 23.5, "master_bedroom": 21.5, "dining_room": 23.0, "living_room": 22.0}


def tune(configs):
    return build_bands(configs, REFERENCE)


def test_changes_only_temperature_offsets_and_is_idempotent():
    baseline, _, _ = build_controllers(BASE)
    baseline += [deepcopy(REFERENCE), {"id": "lighting", "actions": [{"stop": "untouched"}]}]
    before, reference = deepcopy(baseline), deepcopy(REFERENCE)
    result = build_bands(baseline, REFERENCE)
    assert baseline == before and REFERENCE == reference
    assert build_bands(result, REFERENCE) == result
    room_ids = {info["id"] for info in ROOMS.values()}
    for old, new in zip(before, result, strict=True):
        if old["id"] not in room_ids:
            assert new == old
            continue
        actual = new["use_blueprint"]["input"]
        assert actual["upper_offset"] == 0.4 and actual["lower_offset"] == -0.1
        restored = deepcopy(new)
        for key in NURSERY_BAND:
            restored["use_blueprint"]["input"][key] = old["use_blueprint"]["input"][key]
        assert restored == old
        assert actual["zha_min_off_minutes"] == old["use_blueprint"]["input"]["zha_min_off_minutes"]
        assert actual["zha_min_off_minutes"] in (10, 13)
    result[-1]["actions"].append({"stop": "copy isolation"})
    assert baseline == before


@pytest.mark.parametrize("field,value", [("upper_offset", 0.7), ("lower_offset", -0.5),
    ("lower_offset", None), ("upper_offset", True), ("command_transport", "xiaomi_ble"),
    ("controller_entity", "automation.another_room")])
def test_unreviewed_nursery_reference_is_rejected(field, value):
    baseline, _, _ = build_controllers(BASE)
    reference = deepcopy(REFERENCE)
    reference["use_blueprint"]["input"][field] = value
    with pytest.raises(ValueError, match="nursery"):
        build_bands(baseline, reference)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "switch", "mode", "controller", "transport", "blueprint", "nan"])
def test_unexpected_room_sources_are_rejected_without_mutation(mutation):
    baseline, _, _ = build_controllers(BASE)
    if mutation == "missing": baseline.pop(0)
    elif mutation == "duplicate": baseline.append(deepcopy(baseline[0]))
    elif mutation == "blueprint": baseline[0]["use_blueprint"]["path"] = "wrong.yaml"
    else:
        key, value = {"switch": ("gateway_switch", "switch.nursery"),
            "mode": ("fingerbot_mode", "select.nursery"),
            "controller": ("controller_entity", "automation.nursery"),
            "transport": ("command_transport", "xiaomi_ble"),
            "nan": ("upper_offset", float("nan"))}[mutation]
        baseline[0]["use_blueprint"]["input"][key] = value
    before = repr(baseline)
    with pytest.raises(ValueError): build_bands(baseline, REFERENCE)
    assert repr(baseline) == before


@pytest.mark.parametrize("rooms_rig", [tune], indirect=True)
async def test_relative_band_edges_keep_room_targets_and_normal_protection(rooms_rig):
    r = rooms_rig
    for room, inputs in r.configs.items():
        await r.hass.services.async_call("input_number", "set_value",
            {"entity_id": inputs["target_temp"], "value": TARGETS[room]}, blocking=True)
        await r.set(inputs["temp_sensor"], TARGETS[room] + 0.4)
    await r.advance(780)
    await r.tick()
    assert not r.presses  # Equality at the upper edge is not demand.
    for count, (room, inputs) in enumerate(r.configs.items(), 1):
        await r.set(inputs["temp_sensor"], TARGETS[room] + 0.5)
        assert len(r.presses) == count
        assert r.presses[-1] == inputs["gateway_switch"]
    for room, inputs in r.configs.items():
        await r.set(inputs["temp_sensor"], TARGETS[room])
    await r.advance(299)
    await r.tick()
    assert len(r.presses) == 4  # Existing five-minute minimum ON applies.
    for room, inputs in r.configs.items():
        await r.set(inputs["temp_sensor"], TARGETS[room] + 0.1)
    await r.advance(1)
    await r.tick()
    assert len(r.presses) == 4  # Equality at the lower edge is not OFF demand.
    for room, inputs in r.configs.items():
        await r.set(inputs["temp_sensor"], TARGETS[room])
    assert len(r.presses) == 8
    assert all(r.record(room)["reason"] == "zigbee_ack" for room in ROOMS)
    for room, inputs in r.configs.items():
        assert float(r.hass.states.get(inputs["target_temp"]).state) == TARGETS[room]
        await r.set(inputs["temp_sensor"], TARGETS[room] + 0.5)
    await r.advance(599)
    await r.tick()
    assert len(r.presses) == 8
    await r.advance(1)
    await r.tick()
    assert len(r.presses) == 11
    assert r.record("living_room")["expected"] == "off"
    await r.advance(180)
    await r.tick()
    assert len(r.presses) == 12


@pytest.mark.parametrize("rooms_rig", [tune], indirect=True)
async def test_tuned_failed_command_keeps_thirteen_minute_floor_and_room_isolation(rooms_rig):
    r = rooms_rig
    failed = r.configs["study"]["gateway_switch"]
    r.fail.add(failed)
    await r.advance(780)
    await r.tick()
    assert r.record("study")["phase"] == "pending"
    assert len(r.presses) == 4
    assert all(r.record(room)["reason"] == "zigbee_ack" for room in ROOMS if room != "study")
    await r.advance(779)
    await r.tick()
    assert r.presses.count(failed) == 1
    r.fail.clear()
    await r.advance(1)
    await r.tick()
    assert r.presses.count(failed) == 2
    assert len(r.presses) == 5
    assert r.record("study")["reason"] == "zigbee_ack"
