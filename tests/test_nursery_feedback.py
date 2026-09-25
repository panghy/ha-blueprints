"""Regression for the 24–25 September delayed-feedback/retry incident."""
import pytest
import asyncio
import json


@pytest.mark.asyncio
async def test_legacy_retries_before_790_second_feedback(rig):
    """Characterize the production failure using the unchanged old controller."""
    await rig.advance(801)
    await rig.install("hvac_toggle_thermostat.yaml", "hvac_press_listener.yaml")
    await rig.set("sensor.temperature", 21.3)
    assert len(rig.presses) == 1
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 2  # A second physical toggle while outcome unknown.


@pytest.mark.asyncio
async def test_recorded_late_off_feedback_books_once_without_retry(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust()
    await rig.set("sensor.temperature", 21.3)
    assert len(rig.presses) == 1
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(10.8)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    assert rig.record["phase"] == "ready"
    await rig.advance(1.35)
    await rig.set("sensor.feedback", "0500000154000300")
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    await rig.advance(1200)
    await rig.set("sensor.temperature", 19.5)
    await rig.tick()
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    assert len(rig.presses) == 1


async def pending_off(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust()
    await rig.set("sensor.temperature", 21.3)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"


@pytest.mark.asyncio
async def test_timeout_locks_once_and_late_packet_cannot_unlock(rig):
    await pending_off(rig)
    await rig.advance(901)
    await rig.set("sensor.temperature", 20.6)
    assert rig.record["reason"] == "feedback_timeout"
    assert len(rig.alerts) == 1
    await rig.tick()
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.set("sensor.temperature", 19.1)
    await rig.tick()
    assert rig.record["phase"] == "needs_verification"
    assert len(rig.presses) == 1
    assert len(rig.alerts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entity,service,data", [
    ("input_boolean", "turn_on", {"entity_id": "input_boolean.cooling"}),
    ("input_boolean", "turn_off", {"entity_id": "input_boolean.cooling"}),
    ("input_number", "set_value", {"entity_id": "input_number.snapshot", "value": 22}),
])
async def test_even_same_value_manual_correction_invalidates_pending(rig, entity, service, data):
    await pending_off(rig)
    await rig.advance(60)
    await rig.call(entity, service, data)
    prior = rig.hass.states.get("input_boolean.cooling").state
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "needs_verification"
    assert rig.hass.states.get("input_boolean.cooling").state == prior
    assert len(rig.presses) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", ["switch.fingerbot", "sensor.feedback"])
async def test_reconnect_cannot_refresh_command_age_or_unlock(rig, entity):
    await pending_off(rig)
    await rig.advance(60)
    await rig.set(entity, "unavailable")
    await rig.set(entity, "on" if entity.startswith("switch") else "01")
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.tick()
    assert rig.record["reason"] == "gateway_unavailable"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_reload_invalidates_pending(rig):
    await pending_off(rig)
    rig.hass.bus.async_fire("automation_reloaded")
    await rig.settle()
    await rig.advance(800)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "needs_verification"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_startup_invalidates_even_a_preexisting_ready_record(rig):
    await rig.trust()
    await rig.install("hvac_guarded_thermostat.yaml")
    assert rig.record["phase"] == "needs_verification"
    await rig.set("sensor.temperature", 19.1)
    assert not rig.presses


@pytest.mark.asyncio
async def test_failed_send_does_not_retry_or_assume_success(rig):
    rig.fail_press = True
    await pending_off(rig)
    await rig.advance(780)
    await rig.tick()
    assert rig.record["phase"] == "pending"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_overlapping_triggers_and_feedback_while_send_is_running(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust()
    rig.hold_press = asyncio.Event()
    rig.hass.states.async_set("sensor.temperature", "21.3")
    for _ in range(30):
        await asyncio.sleep(0)
        if rig.presses:
            break
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"
    assert rig.hass.states.get("timer.cooldown").state == "active"
    rig.hass.states.async_set("sensor.temperature", "21.2")
    rig.hass.states.async_set("sensor.feedback", "0500010154000300")
    rig.hold_press.set()
    await rig.settle()
    assert rig.record["phase"] == "ready"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["not json", "[]", "null", '{}', '{"v":1,"phase":"ready"}'])
async def test_invalid_journal_fails_closed(rig, raw):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.call("input_text", "set_value", {"entity_id": "input_text.command", "value": raw})
    await rig.set("sensor.temperature", 19.1)
    assert rig.record["phase"] == "needs_verification"
    assert len(rig.presses) == 0


@pytest.mark.asyncio
async def test_duplicate_and_unsolicited_packets_never_toggle_belief(rig):
    await pending_off(rig)
    await rig.advance(30)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.advance(.3)
    await rig.set("sensor.feedback", "0500000154000300")
    await rig.advance(29.7)
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "ready"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    await rig.advance(60)
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["reason"] == "unsolicited_feedback"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_off_feedback_preserves_full_13_minute_cooldown(rig):
    await pending_off(rig)
    await rig.advance(60)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.temperature", 22)
    await rig.tick()  # Even manually cancelled timers cannot bypass the floor.
    assert len(rig.presses) == 1
    await rig.advance(779)
    await rig.set("sensor.temperature", 22)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"


@pytest.mark.asyncio
async def test_on_feedback_preserves_5_minute_stop_floor(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust("off")
    await rig.set("sensor.temperature", 22.1)
    assert len(rig.presses) == 1
    await rig.advance(40)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.temperature", 21.3)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(299)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off"


@pytest.mark.asyncio
async def test_mode_off_still_books_feedback_but_cannot_issue_power(rig):
    await pending_off(rig)
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": "Off"})
    await rig.advance(60)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.set("sensor.feedback", "01")
    await rig.advance(800)
    await rig.set("sensor.temperature", 23)
    await rig.tick()
    assert rig.record["phase"] == "ready"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("entity,value", [("sensor.temperature", "unavailable"),
    ("input_number.target", "nan"), ("timer.cooldown", "unavailable")])
async def test_missing_inputs_fail_closed(rig, entity, value):
    await pending_off(rig)
    await rig.set(entity, value)
    await rig.tick()
    assert rig.record["phase"] == "needs_verification"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_stale_probe_cannot_trigger_power(rig):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust("off")
    await rig.advance(1400)
    await rig.tick()
    assert rig.record["reason"] == "temperature_unavailable"
    assert not rig.presses
