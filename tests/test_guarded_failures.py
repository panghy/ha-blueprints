"""Fault injection at service boundaries, with the actual HA automation queue."""
import asyncio
import json
from datetime import timedelta

import pytest
from homeassistant.core import State
from homeassistant.util import dt as dt_util

from test_nursery_feedback import pending_off


async def evaluate(rig):
    rig.hass.bus.async_fire("timer.finished", {"entity_id": "timer.cooldown"})
    await rig.settle()


@pytest.mark.asyncio
async def test_legacy_rejects_incident_feedback_outside_720_seconds(rig):
    await rig.advance(801)
    await rig.install("hvac_toggle_thermostat.yaml", "hvac_press_listener.yaml")
    await rig.set("sensor.temperature", 21.3)
    await rig.advance(790.8)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.hass.states.get("input_boolean.cooling").state == "on"
    assert len(rig.alerts) == 1
    assert "outside the 720 s window" in rig.alerts[0]["message"]


@pytest.mark.asyncio
async def test_gateway_property_timestamp_does_not_renew_pending_deadline(rig):
    await pending_off(rig)
    issued = rig.record["issued"]
    await rig.advance(850)
    await rig.set("switch.fingerbot", "off")
    assert rig.record["issued"] == issued
    await rig.advance(51)
    await evaluate(rig)
    assert rig.record["reason"] == "feedback_timeout"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_old_queued_packet_cannot_ack_a_new_command(rig):
    await pending_off(rig)
    before_command = dt_util.utc_from_timestamp(rig.record["issued"] - 1)
    rig.hass.bus.async_fire("state_changed", {
        "entity_id": "sensor.feedback",
        "old_state": State("sensor.feedback", "01", last_changed=before_command),
        "new_state": State("sensor.feedback", "0500010154000300", last_changed=before_command),
    })
    await rig.settle()
    assert rig.record["phase"] == "pending"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_cancelled_send_keeps_persisted_pending_record(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust()
    rig.hold_press = asyncio.Event()
    rig.hass.states.async_set("sensor.temperature", "21.3")
    for _ in range(30):
        await asyncio.sleep(0)
        if rig.presses:
            break
    assert len(rig.presses) == 1
    await rig.hass.services.async_call("automation", "turn_off", {
        "entity_id": "automation.test_0", "stop_actions": True,
    }, blocking=True)
    rig.hold_press.set()
    await rig.settle()
    assert rig.record["phase"] == "pending"
    await rig.advance(1000)
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await evaluate(rig)
    # Reactivation now invalidates immediately, before the timeout evaluation.
    assert rig.record["reason"] == "controller_reactivated"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_partial_bookkeeping_failure_cannot_authorize_another_toggle(rig):
    await pending_off(rig)
    await rig.advance(30)
    # Fail journal completion after the two belief writes. Pending remains,
    # so the next run detects the newer helper writes and requires a check.
    async def fail_write(call):
        raise RuntimeError("simulated helper storage failure")
    rig.hass.services.async_register("input_text", "set_value", fail_write)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "pending"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    await rig.advance(2000)
    await rig.set("sensor.temperature", 23)
    await rig.tick()
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_notify_failure_leaves_lockout_persisted(rig):
    await pending_off(rig)
    async def fail_notify(call):
        raise RuntimeError("simulated notification failure")
    rig.hass.services.async_register("notify", "test", fail_notify)
    await rig.advance(901)
    await evaluate(rig)
    assert rig.record["phase"] == "needs_verification"
    await rig.tick()
    assert len(rig.presses) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"issued": 999999999999}, {"booked": 999999999999},
    {"issued": -1}, {"expected": "unknown"}, {"booked": "garbage"},
])
async def test_corrupt_journal_fields_fail_closed(rig, patch):
    await pending_off(rig)
    record = rig.record | patch
    await rig.call("input_text", "set_value", {"entity_id": "input_text.command", "value": json.dumps(record)})
    await evaluate(rig)
    assert rig.record["reason"] == "invalid_journal"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_uncommanded_switch_edge_while_ready_requires_check(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust("off")
    await rig.set("switch.fingerbot", "on")
    assert rig.record["reason"] == "unsolicited_switch_edge"
    assert not rig.presses


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["manual", "mode", "temperature", "journal", "disable"])
async def test_changed_inputs_during_command_preparation_prevent_actuation(rig, change):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust()
    original = rig.hass.services.async_services()["timer"]["start"]
    starts = 0

    async def start_and_change(call):
        nonlocal starts
        await original.job.target(call)
        starts += 1
        if starts != 2:
            return
        rig.now += timedelta(seconds=1)
        rig.clock.move_to(rig.now)
        if change == "manual":
            await rig.hass.services.async_call("input_boolean", "turn_off", {
                "entity_id": "input_boolean.cooling",
            }, blocking=True)
        elif change == "mode":
            rig.hass.states.async_set("input_select.mode", "Off")
        elif change == "temperature":
            rig.hass.states.async_set("sensor.temperature", "23")
        elif change == "disable":
            await rig.hass.services.async_call("automation", "turn_off", {
                "entity_id": "automation.test_0", "stop_actions": False,
            }, blocking=True)
        else:
            await rig.hass.services.async_call("input_text", "set_value", {
                "entity_id": "input_text.command", "value": '{"v":1,"phase":"needs_verification"}',
            }, blocking=True)

    rig.hass.services.async_register("timer", "start", start_and_change, schema=original.schema)
    await rig.set("sensor.temperature", 21.3)
    assert not rig.presses
    assert rig.record["phase"] in ["pending", "needs_verification"]
    if change == "disable":
        await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await rig.advance(1000)
    await evaluate(rig)
    assert not rig.presses
    assert rig.record["phase"] == "needs_verification"
