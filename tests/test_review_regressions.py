"""Independent PR #17 probes; no real HA or hardware access."""
from datetime import timedelta
from pathlib import Path
import json

import pytest
from homeassistant.setup import async_setup_component

from conftest import expand, INPUTS
from test_nursery_feedback import pending_off
from test_nursery_package import prepare


@pytest.mark.asyncio
async def test_manual_correction_during_feedback_is_not_overwritten(rig):
    await pending_off(rig)
    await rig.advance(30)
    original = rig.hass.services.async_services()["input_number"]["set_value"]
    corrected = False

    async def write_then_manual_correction(call):
        nonlocal corrected
        await original.job.target(call)
        if corrected:
            return
        corrected = True
        rig.now += timedelta(seconds=1)
        rig.clock.move_to(rig.now)
        # A physical check writes BOTH helpers while feedback handling yielded.
        await rig.hass.services.async_call("input_number", "set_value", {
            "entity_id": "input_number.snapshot", "value": 21.3,
        }, blocking=True)
        await rig.hass.services.async_call("input_boolean", "turn_on", {
            "entity_id": "input_boolean.cooling",
        }, blocking=True)

    rig.hass.services.async_register("input_number", "set_value", write_then_manual_correction,
                                      schema=original.schema)
    await rig.set("sensor.feedback", "0500010154000300")
    assert corrected
    assert rig.hass.states.get("input_boolean.cooling").state == "on", rig.record
    assert rig.record["phase"] == "needs_verification"


@pytest.mark.asyncio
async def test_actual_reload_cannot_reuse_old_ready_record(rig):
    config = expand("hvac_guarded_thermostat.yaml") | {
        "id": "test_0", "alias": "Test 0", "initial_state": False,
    }
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await rig.trust("off")
    assert rig.record["phase"] == "ready"
    config["alias"] = "Test 0 reloaded"
    Path(rig.hass.config.path("configuration.yaml")).write_text(json.dumps({"automation": [config]}))
    await rig.call("automation", "reload", {})
    assert rig.hass.states.get("automation.test_0").state == "off"
    after_reload = rig.record.copy()
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await rig.tick()
    assert not rig.presses, {"after_reload": after_reload, "current": rig.record}
    assert rig.record["phase"] == "needs_verification"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "stale"])
async def test_raw_probe_failure_during_preparation_prevents_actuation(rig, failure):
    await rig.set("sensor.raw_probe", "22")
    config = expand("hvac_guarded_thermostat.yaml", INPUTS | {"raw_temp_sensor": "sensor.raw_probe"})
    config.update({"id": "test_0", "alias": "Test 0"})
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust()
    original = rig.hass.services.async_services()["timer"]["start"]
    starts = 0

    async def start_then_raw_probe_loss(call):
        nonlocal starts
        await original.job.target(call)
        starts += 1
        if starts == 2:
            if failure == "unavailable":
                rig.hass.states.async_set("sensor.raw_probe", "unavailable")
            else:
                rig.now += timedelta(seconds=2101)
                rig.clock.move_to(rig.now)
                rig.hass.states.async_set("sensor.temperature", "21.3")

    rig.hass.services.async_register("timer", "start", start_then_raw_probe_loss, schema=original.schema)
    await rig.set("sensor.temperature", "21.3")
    assert not rig.presses, rig.record


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["belief", "journal"])
async def test_correction_after_feedback_helper_writes_cannot_restore_trust(rig, boundary):
    await pending_off(rig)
    await rig.advance(30)
    domain, service = ("input_boolean", "turn_off") if boundary == "belief" else ("input_text", "set_value")
    original = rig.hass.services.async_services()[domain][service]
    corrected = False

    async def write_then_correct(call):
        nonlocal corrected
        await original.job.target(call)
        if corrected:
            return
        corrected = True
        rig.now += timedelta(seconds=1)
        rig.clock.move_to(rig.now)
        await rig.hass.services.async_call("input_number", "set_value", {
            "entity_id": "input_number.snapshot", "value": 20.8,
        }, blocking=True)
        await rig.hass.services.async_call("input_boolean", "turn_on", {
            "entity_id": "input_boolean.cooling",
        }, blocking=True)

    rig.hass.services.async_register(domain, service, write_then_correct, schema=original.schema)
    await rig.set("sensor.feedback", "0500010154000300")
    assert corrected
    assert rig.hass.states.get("input_boolean.cooling").state == "on"
    assert rig.hass.states.get("input_number.snapshot").state == "20.8"
    assert rig.record["phase"] == "needs_verification"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_feedback_with_unchanged_snapshot_can_book_normally(rig):
    await pending_off(rig)
    await rig.advance(30)
    # The raw temperature matches the previously recorded snapshot. HA retains
    # its prior context on a same-value service call; this is still our write.
    await rig.set("sensor.temperature", 22)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "ready"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_disable_enable_requires_new_check_even_without_reload(rig, pending):
    if pending:
        await pending_off(rig)
    else:
        await rig.install("hvac_guarded_thermostat.yaml")
        await rig.trust("off")
    presses_before = len(rig.presses)
    await rig.call("automation", "turn_off", {"entity_id": "automation.test_0", "stop_actions": True})
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    assert rig.record["phase"] == "needs_verification"
    assert rig.record["reason"] == "controller_reactivated"
    await rig.tick()
    assert len(rig.presses) == presses_before


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", ["snapshot", "belief"])
async def test_same_value_external_write_during_feedback_invalidates_trust(rig, helper):
    await pending_off(rig)
    await rig.advance(30)
    domain, service, data = (
        ("input_number", "set_value", {"entity_id": "input_number.snapshot", "value": 21.3})
        if helper == "snapshot" else
        ("input_boolean", "turn_off", {"entity_id": "input_boolean.cooling"})
    )
    original = rig.hass.services.async_services()[domain][service]
    corrected = False

    async def repeat_with_external_context(call):
        nonlocal corrected
        await original.job.target(call)
        if corrected:
            return
        corrected = True
        rig.now += timedelta(seconds=1)
        rig.clock.move_to(rig.now)
        await rig.hass.services.async_call(domain, service, data, blocking=True)

    rig.hass.services.async_register(domain, service, repeat_with_external_context, schema=original.schema)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.tick()
    assert corrected
    assert rig.record["phase"] == "needs_verification", rig.record


@pytest.mark.asyncio
async def test_unchanged_reload_while_disabled_invalidates_verification(rig):
    await prepare(rig)
    config = expand("hvac_guarded_thermostat.yaml") | {
        "id": "test_0", "alias": "Test 0", "initial_state": False,
    }
    Path(rig.hass.config.path("configuration.yaml")).write_text(json.dumps({"automation": [config]}))
    await rig.call("automation", "reload", {})
    await rig.call("script", "nursery_prepare_verified_belief", {"physically_verified": True, "cooling": False})
    assert rig.record["phase"] == "verified"
    origin = rig.record["origin"]
    await rig.call("automation", "reload", {})
    after_reload = rig.hass.states.get("automation.test_0")
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await rig.advance(800)
    await rig.tick()
    assert not rig.presses, {"origin": origin, "after_reload_context": after_reload.context.id, "record": rig.record}
    assert rig.record["phase"] == "needs_verification"
