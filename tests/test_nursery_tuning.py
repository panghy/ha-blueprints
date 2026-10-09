"""Tuned nursery thresholds and opt-in acknowledged ZHA restart protection."""
import json
import pytest
from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
TUNED = INPUTS | {"command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode",
                  "upper_offset": 0.4, "lower_offset": -0.1, "zha_min_off_minutes": 5}

async def prepare(rig, expected="off", **overrides):
    await rig.set("sensor.temperature", 21.1)
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, TUNED | overrides) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust(expected)
    return config

async def acknowledge_off(rig, **overrides):
    config = await prepare(rig, "on", **overrides)
    await rig.set("sensor.temperature", 21)
    assert len(rig.presses) == 1
    assert rig.record["reason"] == "zigbee_ack"
    assert rig.record["expected"] == "off"
    return config

async def test_tuned_thresholds_and_minimum_run_time(rig):
    await prepare(rig)
    await rig.set("sensor.temperature", 21.4)
    assert not rig.presses
    await rig.set("sensor.temperature", 21.5)
    assert len(rig.presses) == 1
    await rig.set("sensor.temperature", 21)
    await rig.advance(299)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.set("sensor.temperature", 21.1)
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 1  # Exact stop threshold is not crossed.
    await rig.set("sensor.temperature", 21)
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off"

async def test_acknowledged_off_can_restart_at_five_minutes(rig):
    await acknowledge_off(rig)
    await rig.set("sensor.temperature", 21.5)
    await rig.advance(299)
    await rig.tick()  # Even early timer cancellation cannot shorten timestamp floor.
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"

@pytest.mark.parametrize("desired", ["on", "off"])
async def test_tuned_failed_commands_still_wait_thirteen_minutes(rig, desired):
    await prepare(rig, "off" if desired == "on" else "on")
    rig.fail_press = True
    await rig.set("sensor.temperature", 21.5 if desired == "on" else 21)
    assert rig.record["phase"] == "pending"
    for delta in [300, 479]:
        await rig.advance(delta)
        await rig.tick()
        assert len(rig.presses) == 1
        assert rig.record["phase"] == "pending"
    rig.fail_press = False
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["reason"] == "zigbee_ack"

@pytest.mark.parametrize("reason", ["temperature", "estimate_adopted", "migration", "feedback"])
async def test_only_zigbee_ack_gets_short_restart_interval(rig, reason):
    await acknowledge_off(rig)
    await rig.call("input_text", "set_value", {"entity_id": "input_text.command",
        "value": json.dumps(rig.record | {"reason": reason})})
    await rig.set("sensor.temperature", 21.5)
    await rig.advance(300)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(479)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

async def test_normal_timers_expire_at_tuned_interval(rig):
    await acknowledge_off(rig)
    for entity in ["timer.cooldown", "timer.off_cooldown"]:
        assert rig.hass.states.get(entity).attributes["duration"] == "0:05:00"

@pytest.mark.parametrize("minutes,expected", [(0, 300), (1, 300), (8, 480), (13, 780), (99, 780)])
async def test_restart_interval_clamped_to_supported_protection(rig, minutes, expected):
    await acknowledge_off(rig, zha_min_off_minutes=minutes)
    await rig.set("sensor.temperature", 21.5)
    await rig.advance(expected - 1)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

async def test_ble_transport_ignores_zha_restart_setting(rig):
    await prepare(rig, "on", command_transport="xiaomi_ble")
    await rig.set("sensor.temperature", 21)
    assert len(rig.presses) == 1
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["reason"] == "feedback"
    await rig.set("sensor.feedback", "01")
    await rig.set("sensor.temperature", 21.5)
    await rig.advance(779)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

async def test_reload_preserves_ack_age_without_starting_early(rig, tmp_path):
    config = await acknowledge_off(rig)
    original = rig.record.copy()
    await rig.advance(100)
    (tmp_path / "configuration.yaml").write_text(json.dumps({"automation": [config | {"initial_state": True}]}))
    await rig.call("automation", "reload", {})
    assert rig.record["booked"] == original["booked"]
    await rig.set("sensor.temperature", 21.5)
    await rig.advance(199)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

async def test_five_minutes_is_measured_from_delayed_ack(rig):
    from datetime import timedelta
    original = rig.hass.services.async_services()["switch"]["toggle"]
    async def delayed_press(call):
        rig.now += timedelta(seconds=20)
        rig.clock.move_to(rig.now)
        await original.job.target(call)
    rig.hass.services.async_register("switch", "toggle", delayed_press)
    await acknowledge_off(rig)
    assert rig.record["booked"] - rig.record["issued"] == 20
    await rig.set("sensor.temperature", 21.5)
    await rig.advance(280)
    await rig.tick()  # Five minutes from issue is only 280 seconds from ACK.
    assert len(rig.presses) == 1
    await rig.advance(19)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

async def test_same_value_manual_correction_restores_full_adoption_floor(rig):
    await acknowledge_off(rig)
    await rig.advance(1)
    await rig.call("input_boolean", "turn_off", {"entity_id": "input_boolean.cooling"})
    await rig.set("sensor.temperature", 21.5)
    assert rig.record["reason"] == "estimate_adopted"
    await rig.advance(779)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2

async def test_tuned_pending_thermal_reconciliation_retains_full_floor(rig):
    await prepare(rig, "on")
    rig.fail_press = True
    await rig.set("sensor.temperature", 21)
    await rig.advance(300)
    await rig.set("sensor.temperature", 21.3)  # Warming supports OFF, but too early.
    await rig.tick()
    assert rig.record["phase"] == "pending"
    await rig.advance(479)
    await rig.tick()
    assert rig.record["phase"] == "pending"
    await rig.advance(1)
    await rig.tick()
    assert rig.record["phase"] == "ready"
    assert rig.record["reason"] == "temperature"
    assert len(rig.presses) == 1
