"""Automatic recovery uses the actual HA engine and fake physical service boundaries."""
import asyncio
import json
import pytest

BLUEPRINT = "hvac_recovering_thermostat.yaml"

async def pending(rig, desired="off"):
    await rig.install(BLUEPRINT)
    await rig.trust("on" if desired == "off" else "off")
    await rig.set("sensor.temperature", 21.3 if desired == "off" else 22.3)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"
    assert rig.record["expected"] == desired

@pytest.mark.asyncio
@pytest.mark.parametrize("desired", ["on", "off"])
async def test_missing_feedback_retries_without_a_physical_reset(rig, desired):
    await pending(rig, desired)
    await rig.advance(779)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["phase"] == "pending"
    assert not rig.alerts

@pytest.mark.asyncio
@pytest.mark.parametrize("desired", ["on", "off"])
async def test_repeated_losses_keep_recovering_at_the_protection_floor(rig, desired):
    await pending(rig, desired)
    for count in range(2, 5):
        await rig.advance(780)
        await rig.set("sensor.temperature", 22.3 if desired == "on" else 21.3)
        await rig.tick()
        assert len(rig.presses) == count
    assert all((b[0] - a[0]).total_seconds() >= 780
               for a, b in zip(rig.presses, rig.presses[1:]))
    assert rig.record["phase"] == "pending"
    assert not rig.alerts

@pytest.mark.asyncio
@pytest.mark.parametrize("desired,temperature", [("on", 21.9), ("off", 21.5)])
async def test_temperature_evidence_avoids_repeating_a_landed_command(rig, desired, temperature):
    await pending(rig, desired)
    await rig.advance(780)
    await rig.set("sensor.temperature", temperature)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == desired
    assert rig.record["reason"] == "temperature"
    # A delayed report for the already inferred press cannot invert the estimate.
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["expected"] == desired
    assert len(rig.presses) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("desired", ["on", "off"])
async def test_late_feedback_after_old_timeout_is_still_booked(rig, desired):
    await pending(rig, desired)
    # No evaluation/retry in between: a first report remains useful after 15 min.
    await rig.advance(1000)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == desired
    assert rig.record["reason"] == "feedback"
    assert len(rig.presses) == 1
    await rig.advance(0.15)
    await rig.set("sensor.feedback", "0500000154000300")
    await rig.advance(50)
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["expected"] == desired
    assert len(rig.presses) == 1

@pytest.mark.asyncio
async def test_changed_target_can_cancel_need_for_a_retry(rig):
    await pending(rig, "on")
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 26.5})
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"

@pytest.mark.asyncio
@pytest.mark.parametrize("entity,restored", [
    ("sensor.feedback", "01"), ("switch.fingerbot", "off"),
    ("sensor.temperature", "21.3"), ("input_number.target", "21"),
    ("sensor.nursery_hvac_lifecycle", "restored:42"),
])
async def test_temporary_unavailability_recovers_without_manual_reset(rig, entity, restored):
    await pending(rig)
    await rig.set(entity, "unavailable")
    await rig.advance(800)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.set(entity, restored)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["phase"] == "pending"
    assert not rig.alerts

@pytest.mark.asyncio
async def test_stale_temperature_holds_then_recovers_on_a_fresh_report(rig):
    await pending(rig)
    await rig.advance(2101)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.set("sensor.temperature", 21.3)
    await rig.tick()
    assert len(rig.presses) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["unknown", "unavailable", "nan", "inf", "not-a-number"])
async def test_invalid_temperature_never_actuates(rig, raw):
    await pending(rig)
    await rig.advance(780)
    await rig.set("sensor.temperature", raw)
    await rig.tick()
    assert len(rig.presses) == 1

@pytest.mark.asyncio
async def test_transport_error_retains_protection_but_allows_a_later_retry(rig):
    rig.fail_press = True
    await pending(rig)
    await rig.advance(779)
    await rig.tick()
    assert len(rig.presses) == 1
    rig.fail_press = False
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

@pytest.mark.asyncio
async def test_starts_automatically_without_a_verification_ticket(rig):
    await rig.call("input_boolean", "turn_off", {"entity_id": "input_boolean.cooling"})
    await rig.install(BLUEPRINT)
    assert rig.record["phase"] == "ready"
    assert not rig.presses
    await rig.advance(779)
    await rig.tick()
    assert not rig.presses
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 1

@pytest.mark.asyncio
async def test_disabled_controller_stays_disabled_until_user_enables_it(rig):
    await pending(rig)
    await rig.call("automation", "turn_off", {"entity_id": "automation.test_0", "stop_actions": True})
    await rig.advance(800)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await rig.tick()
    assert len(rig.presses) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False])
async def test_actual_changed_config_reload_retains_pending_age_and_auto_recovers(rig, tmp_path, changed):
    from conftest import expand
    await pending(rig)
    original_issue = rig.record["issued"]
    config = expand(BLUEPRINT) | {"id": "test_0", "alias": "Test 0 reloaded" if changed else "Test 0", "initial_state": True}
    (tmp_path / "configuration.yaml").write_text(json.dumps({"automation": [config]}))
    await rig.call("automation", "reload", {})
    await rig.tick()
    assert rig.hass.states.get("automation.test_0").state == "on"
    assert rig.record["issued"] == original_issue
    assert rig.record["phase"] == "pending"
    assert len(rig.presses) == 1
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["not json", "[]", "null", '{}', '{"v":1,"phase":"needs_verification"}',
    '{"v":2,"phase":"ready","expected":"on","issued":"1","booked":"1","t":"22","generation":"x"}'])
async def test_invalid_journal_adopts_estimate_and_recovers_with_protection(rig, raw):
    await pending(rig)
    await rig.call("input_text", "set_value", {"entity_id": "input_text.command", "value": raw})
    await rig.tick()
    assert rig.record["phase"] == "ready"
    assert len(rig.presses) == 1
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize("initial,temperature,expected", [("on", 22.4, "off"), ("off", 21, "on")])
async def test_drift_reconciles_an_idle_incorrect_estimate(rig, initial, temperature, expected):
    await rig.install(BLUEPRINT)
    await rig.trust(initial)
    await rig.set("sensor.temperature", temperature)
    await rig.tick()
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == expected
    assert rig.record["reason"] == "temperature"
    assert not rig.presses

@pytest.mark.asyncio
async def test_no_retry_burst_while_the_actuator_service_is_yielding(rig):
    await rig.install(BLUEPRINT)
    await rig.trust()
    rig.hold_press = asyncio.Event()
    rig.hass.states.async_set("sensor.temperature", "21.3")
    for _ in range(50):
        await asyncio.sleep(0)
        if rig.presses:
            break
    assert len(rig.presses) == 1
    assert rig.hass.states.get("timer.cooldown").state == "active"
    assert rig.hass.states.get("timer.off_cooldown").state == "active"
    rig.hass.states.async_set("sensor.temperature", "21.2")
    rig.hass.states.async_set("sensor.feedback", "0500010154000300")
    rig.hold_press.set()
    await rig.settle()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "off"

@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["snapshot", "belief"])
async def test_external_correction_during_feedback_is_preserved_then_auto_adopted(rig, boundary):
    from datetime import timedelta
    await pending(rig)
    await rig.advance(30)
    domain, service = ("input_number", "set_value") if boundary == "snapshot" else ("input_boolean", "turn_off")
    original = rig.hass.services.async_services()[domain][service]
    injected = False
    async def corrected(call):
        nonlocal injected
        await original.job.target(call)
        if injected:
            return
        injected = True
        rig.now += timedelta(seconds=1)
        rig.clock.move_to(rig.now)
        await rig.hass.services.async_call("input_number", "set_value", {
            "entity_id": "input_number.snapshot", "value": 20.8}, blocking=True)
        await rig.hass.services.async_call("input_boolean", "turn_on", {
            "entity_id": "input_boolean.cooling"}, blocking=True)
    rig.hass.services.async_register(domain, service, corrected, schema=original.schema)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.tick()
    assert injected
    assert rig.hass.states.get("input_number.snapshot").state == "20.8"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "on"
    assert len(rig.presses) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["raw_probe", "lifecycle", "target", "mode"])
async def test_input_changes_during_timer_preparation_prevent_actuation(rig, failure):
    from conftest import expand, INPUTS
    from homeassistant.setup import async_setup_component
    await rig.set("sensor.raw_probe", "22")
    config = expand(BLUEPRINT, INPUTS | {"raw_temp_sensor": "sensor.raw_probe"})
    config.update({"id": "test_0", "alias": "Test 0"})
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust()
    original = rig.hass.services.async_services()["timer"]["start"]
    count = 0
    async def change_input(call):
        nonlocal count
        await original.job.target(call)
        count += 1
        if count == 2:
            entity, value = {
                "raw_probe": ("sensor.raw_probe", "unavailable"),
                "lifecycle": ("sensor.nursery_hvac_lifecycle", "changed:44"),
                "target": ("input_number.target", "19"),
                "mode": ("input_select.mode", "Off"),
            }[failure]
            rig.hass.states.async_set(entity, value)
    rig.hass.services.async_register("timer", "start", change_input, schema=original.schema)
    await rig.set("sensor.temperature", 21.3)
    assert not rig.presses
    assert rig.record["phase"] == "pending"

@pytest.mark.asyncio
async def test_retry_then_double_delivery_can_reconcile_and_recover(rig):
    await pending(rig, "on")
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 2
    # Both delayed toggles land: AC is actually OFF, first feedback estimates ON.
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["expected"] == "on"
    await rig.advance(50)
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.set("sensor.feedback", "01")
    await rig.advance(780)
    await rig.set("sensor.temperature", 22.7)
    await rig.tick()
    assert rig.record["expected"] == "off"
    assert rig.record["reason"] == "temperature"
    await rig.advance(780)
    await rig.set("sensor.temperature", 22.8)
    await rig.tick()
    assert len(rig.presses) == 3
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["expected"] == "on"
    assert rig.record["phase"] == "ready"
    assert not rig.alerts


@pytest.mark.asyncio
@pytest.mark.parametrize("desired", ["on", "off"])
async def test_actual_handoff_from_guard_preserves_pending_intent_and_command_age(rig, tmp_path, desired):
    from conftest import expand
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.trust("off" if desired == "on" else "on")
    await rig.set("sensor.temperature", 22.3 if desired == "on" else 21.3)
    old = rig.record.copy()
    assert old["phase"] == "pending"
    assert len(rig.presses) == 1
    await rig.advance(100)
    config = expand(BLUEPRINT) | {"id": "test_0", "alias": "Test 0 recovery", "initial_state": True}
    (tmp_path / "configuration.yaml").write_text(json.dumps({"automation": [config]}))
    await rig.call("automation", "reload", {})
    await rig.tick()
    assert rig.record["v"] == 2
    assert rig.record["phase"] == "pending"
    assert rig.record["expected"] == desired
    assert rig.record["issued"] == old["issued"]
    assert rig.record["booked"] == old["booked"]
    assert len(rig.presses) == 1
    await rig.advance(679)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("desired", ["on", "off"])
async def test_lost_command_history_and_timer_state_preserve_full_repeat_floor(rig, desired):
    await pending(rig, desired)
    await rig.advance(1)
    await rig.call("input_text", "set_value", {
        "entity_id": "input_text.command",
        "value": '{"v":1,"phase":"needs_verification","reason":"startup"}'})
    await rig.tick()  # Represents a restart with both timers restored idle.
    assert rig.record["reason"] == "estimate_adopted"
    await rig.advance(300)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(479)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

@pytest.mark.asyncio
async def test_established_on_feedback_retains_five_minute_normal_off_minimum(rig):
    await pending(rig, "on")
    await rig.advance(30)
    await rig.set("sensor.feedback", "0500010154000300")
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.temperature", 21.3)
    await rig.advance(299)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off"


@pytest.mark.asyncio
async def test_pending_on_thermal_booking_accepts_float_serialization_noise(rig):
    await pending(rig, "on")
    await rig.advance(779)
    await rig.set("sensor.temperature", 22.3 - 0.4)
    await rig.tick()
    assert rig.record["phase"] == "pending"
    await rig.advance(1)
    await rig.tick()
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "on"
    assert rig.record["reason"] == "temperature"
    assert len(rig.presses) == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [22.3 - 0.4, 22 + 4e-15])
async def test_feedback_booking_handles_normalized_changed_or_unchanged_snapshot(rig, temperature):
    await pending(rig, "on")
    await rig.advance(30)
    await rig.set("sensor.temperature", temperature)
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "on"
    assert rig.record["reason"] == "feedback"
    assert len(rig.presses) == 1
