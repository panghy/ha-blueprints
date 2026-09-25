"""Run real Home Assistant blueprints against isolated in-memory hardware."""
from datetime import datetime, timedelta, UTC
from pathlib import Path
import json
import asyncio
import time_machine

import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant import loader, config_entries
from homeassistant.bootstrap import async_load_base_functionality
from homeassistant.setup import async_setup_component
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.util import dt as dt_util, yaml as yaml_util

ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "controller_entity": "automation.test_0",
    "temp_sensor": "sensor.temperature",
    "raw_temp_sensor": "sensor.temperature",
    "target_temp": "input_number.target",
    "cooling_active": "input_boolean.cooling",
    "temp_at_toggle": "input_number.snapshot",
    "temp_at_off_press": "input_number.off_snapshot",
    "cooldown_timer": "timer.cooldown",
    "off_cooldown_timer": "timer.off_cooldown",
    "hvac_mode_select": "input_select.mode",
    "gateway_switch": "switch.fingerbot",
    "gateway_status": "sensor.feedback",
    "command_record": "input_text.command",
    "upper_offset": 0.7,
    "lower_offset": -0.5,
    "cooldown_minutes": 13,
    "post_on_cooldown_minutes": 5,
    "alert_notify_service": "notify.test",
}


def expand(name, inputs=None):
    blueprint = Blueprint(yaml_util.load_yaml(str(ROOT / name)),
                          schema=AUTOMATION_BLUEPRINT_SCHEMA,
                          expected_domain="automation")
    chosen = {k: v for k, v in (inputs or INPUTS).items() if k in blueprint.inputs}
    instance = BlueprintInputs(blueprint, {"use_blueprint": {"path": name, "input": chosen}})
    instance.validate()
    return instance.async_substitute()


class Rig:
    def __init__(self, hass):
        self.hass = hass
        self.now = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)
        self.presses = []
        self.alerts = []
        self.fail_press = False
        self.hold_press = None

    async def settle(self):
        await self.hass.async_block_till_done()

    async def set(self, entity, state):
        self.hass.states.async_set(entity, str(state))
        await self.settle()

    async def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.clock.move_to(self.now)
        await self.settle()

    async def call(self, domain, service, data):
        await self.hass.services.async_call(domain, service, data, blocking=True)
        await self.settle()

    async def tick(self):
        # Real timer.finished trigger; expiry alone must not authorize a retry.
        for entity in ("timer.cooldown", "timer.off_cooldown"):
            self.hass.states.async_set(entity, "idle")
            self.hass.bus.async_fire("timer.finished", {"entity_id": entity})
        await self.settle()

    @property
    def record(self):
        return json.loads(self.hass.states.get("input_text.command").state)

    async def trust(self, cooling="on"):
        # Models a physical check with both belief helpers written, then journal.
        controller = self.hass.states.get("automation.test_0")
        if controller is not None:
            await self.call("automation", "turn_off", {"entity_id": "automation.test_0", "stop_actions": True})
        await self.call("input_number", "set_value", {"entity_id": "input_number.snapshot", "value": 22})
        await self.call("input_boolean", "turn_" + cooling, {"entity_id": "input_boolean.cooling"})
        await self.call("input_text", "set_value", {"entity_id": "input_text.command", "value": json.dumps({
            "v": 1, "phase": "verified", "expected": cooling, "issued": 0,
            "booked": self.now.timestamp(), "reason": "physical_check",
            "origin": self.hass.states.get("automation.test_0").context.id if controller is not None else "before_install",
        })})
        if controller is not None:
            await self.call("automation", "turn_on", {"entity_id": "automation.test_0"})
        await self.advance(801)

    async def install(self, *names):
        config = {"automation": [dict(expand(name), id=f"test_{n}", alias=f"Test {n}")
                                  for n, name in enumerate(names)]}
        assert await async_setup_component(self.hass, "automation", config)
        await self.hass.async_start()
        await self.settle()
        assert all(self.hass.states.get(f"automation.test_{n}") for n in range(len(names)))


@pytest_asyncio.fixture
async def rig(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    rig = Rig(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    assert await async_load_base_functionality(hass)
    with time_machine.travel(rig.now, tick=False) as clock:
        rig.clock = clock
        config = {
            "input_boolean": {"cooling": {"initial": True}},
            "input_number": {name: {"min": -10, "max": 99, "initial": value}
                             for name, value in (("target", 21), ("snapshot", 22), ("off_snapshot", 99))},
            "input_text": {"command": {"max": 255, "initial": '{"v":1,"phase":"needs_verification"}'}},
            "input_select": {"mode": {"options": ["Cooling", "Off"], "initial": "Cooling"}},
            "timer": {"cooldown": {}, "off_cooldown": {}},
        }
        for domain in config:
            assert await async_setup_component(hass, domain, config)
        await rig.set("sensor.temperature", "22")
        await rig.set("sensor.feedback", "01")
        await rig.set("switch.fingerbot", "off")

        async def press(call):
            rig.presses.append((rig.now, dict(call.data)))
            if rig.hold_press is not None:
                await rig.hold_press.wait()
            if rig.fail_press:
                raise RuntimeError("simulated transport failure after uncertain send")
            entity = call.data["entity_id"]
            if isinstance(entity, list):
                entity = entity[0]
            hass.states.async_set(entity, "off" if hass.states.get(entity).state == "on" else "on", context=call.context)

        async def notify(call):
            rig.alerts.append(dict(call.data))

        async def log(call):
            pass

        hass.services.async_register("switch", "toggle", press)
        hass.services.async_register("notify", "test", notify)
        hass.services.async_register("logbook", "log", log)
        yield rig
        await hass.async_stop(force=True)
