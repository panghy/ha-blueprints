"""Evidence-based recovery for toggle-only Fingerbots (17 October 2026 nursery).

Every Fingerbot command is one press of a toggle button. A missing
acknowledgement does not prove the press failed, and a second press can undo a
first one that landed. After the retained 13-minute floor, an uncertain command
is repeated only when the room's temperature still shows the requested change
did not happen; a temperature response or a late matching switch report
confirms it without another press.

Inputs follow conftest: target 21, ON above 21.7, OFF below 21.5.
"""
import pytest
from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
ZIGBEE = INPUTS | {"command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode"}


async def install(rig, **extra):
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, ZIGBEE | extra) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()


async def unacknowledged(rig, desired):
    """Issue one command whose radio call fails, leaving the outcome unknown."""
    await install(rig)
    await rig.trust("on" if desired == "off" else "off")
    rig.fail_press = True
    await rig.set("sensor.temperature", 21.3 if desired == "off" else 22.3)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"
    assert rig.record["expected"] == desired
    rig.fail_press = False


async def walk(rig, *temperatures, step=60):
    for value in temperatures:
        await rig.advance(step)
        await rig.set("sensor.temperature", value)


async def test_overnight_rebound_confirms_off_without_a_second_press(rig):
    # 17 Oct 02:48: OFF report not credited; the room coasted down, then
    # warmed, but stayed under command temperature + 0.2. The 03:01 retry
    # pressed the toggle again and restarted cooling (18.8°C by 03:28).
    await unacknowledged(rig, "off")
    await walk(rig, 21.1, 20.9, 20.8, 20.9, 21.0)
    await rig.advance(780 - 5 * 60)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "off"
    assert rig.record["reason"] == "temperature"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"


async def test_flat_room_after_unacknowledged_off_waits_instead_of_pressing(rig):
    await unacknowledged(rig, "off")
    for _ in range(4):
        await rig.advance(780)
        await rig.set("sensor.temperature", 21.3)
        await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"


async def test_still_falling_after_unacknowledged_off_retries_after_floor(rig):
    await unacknowledged(rig, "off")
    await walk(rig, 21.1, 20.9, 20.7)
    await rig.advance(779 - 3 * 60)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["reason"] == "zigbee_ack"
    assert rig.record["expected"] == "off"


async def test_small_fall_within_residual_cooling_is_not_failure_evidence(rig):
    await unacknowledged(rig, "off")
    await walk(rig, 21.1, 20.9, 20.8)
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"


async def test_off_retry_waits_while_temperature_turns_upward(rig):
    await unacknowledged(rig, "off")
    await walk(rig, 21.0, 20.7, 20.6, 20.7)
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1
    # A fresh new low is renewed failure evidence.
    await rig.set("sensor.temperature", 20.5)
    assert len(rig.presses) == 2


async def test_rising_room_after_unacknowledged_on_retries_after_floor(rig):
    await unacknowledged(rig, "on")
    await walk(rig, 22.5, 22.8)
    await rig.advance(779 - 120)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"


async def test_flat_room_after_unacknowledged_on_retries_only_after_thirty_minutes(rig):
    await unacknowledged(rig, "on")
    await rig.advance(780)
    await rig.set("sensor.temperature", 22.3)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1799 - 780)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2


async def test_thirty_minute_on_fallback_uses_current_temperature(rig):
    # Review of PR #24: a brief dip must not disable the fallback for good.
    await unacknowledged(rig, "on")
    await walk(rig, 22.1, 22.5, 22.7)
    await rig.advance(1799 - 180)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2


async def test_partial_cooling_waits_then_retries_at_sixty_minutes(rig):
    await unacknowledged(rig, "on")
    await walk(rig, 22.1)
    await rig.advance(1800)
    await rig.set("sensor.temperature", 22.1)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(3599 - 1800 - 60)
    await rig.set("sensor.temperature", 22.1)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"


async def test_unconfirmed_off_on_a_cold_plateau_retries_at_sixty_minutes(rig):
    # AC still running at its own setpoint: no rebound, no further fall.
    await unacknowledged(rig, "off")
    await walk(rig, 21.0, 20.8)
    for _ in range(3):
        await rig.advance(900)
        await rig.set("sensor.temperature", 20.8)
        await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(3599 - 120 - 2700)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off"


async def test_sixty_minute_fallback_never_overrides_a_satisfied_band(rig):
    await unacknowledged(rig, "on")
    await walk(rig, 22.1, 21.6)  # Cooled below the ON threshold; not needed.
    await rig.advance(3600)
    await rig.tick()
    assert len(rig.presses) == 1


async def test_fall_from_post_command_peak_confirms_on_without_a_second_press(rig):
    # Compressor start-up lag: the room warms briefly before cooling begins.
    await unacknowledged(rig, "on")
    await walk(rig, 22.5, 22.6, 22.4, 22.2)
    await rig.advance(780 - 4 * 60)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "on"
    assert rig.record["reason"] == "temperature"


async def test_start_up_warming_of_a_working_on_is_not_failure_evidence(rig):
    # Measured: a working ON can warm the room up to 0.4°C before cooling.
    await unacknowledged(rig, "on")
    await walk(rig, 22.5, 22.7)
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"


async def test_on_retry_waits_after_the_room_stops_rising(rig):
    await unacknowledged(rig, "on")
    await walk(rig, 22.6, 22.9, 22.8)
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"


@pytest.mark.parametrize("desired", ["on", "off"])
async def test_late_matching_switch_report_books_once(rig, desired):
    await install(rig)
    await rig.trust("on" if desired == "off" else "off")
    async def unreported(call):
        rig.presses.append((rig.now, dict(call.data)))
        raise RuntimeError("device did not respond")
    rig.hass.services.async_register("switch", "toggle", unreported)
    await rig.set("sensor.temperature", 21.3 if desired == "off" else 22.3)
    assert rig.record["phase"] == "pending"
    await rig.advance(5)
    await rig.set("switch.fingerbot", "on")  # Unattributed report of the press.
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == desired
    assert rig.record["reason"] == "zigbee_late_ack"
    assert rig.hass.states.get("input_boolean.cooling").state == desired
    await rig.advance(780)
    await rig.tick()
    assert len(rig.presses) == 1


async def test_late_acknowledgement_keeps_the_uncertain_restart_floor(rig):
    # Only an in-run acknowledgement earns the shorter opt-in restart interval.
    await install(rig, zha_min_off_minutes=5)
    await rig.trust("on")
    async def unreported(call):
        rig.presses.append((rig.now, dict(call.data)))
        raise RuntimeError("device did not respond")
    rig.hass.services.async_register("switch", "toggle", unreported)
    await rig.set("sensor.temperature", 21.3)
    await rig.advance(5)
    await rig.set("switch.fingerbot", "on")
    assert rig.record["reason"] == "zigbee_late_ack"
    await rig.set("sensor.temperature", 22.3)
    await rig.advance(774)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(6)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"


@pytest.mark.parametrize("delay,state", [(91, "on"), (5, "unavailable")])
async def test_out_of_window_or_invalid_switch_report_cannot_acknowledge(rig, delay, state):
    await install(rig)
    await rig.trust("on")
    async def unreported(call):
        rig.presses.append((rig.now, dict(call.data)))
        raise RuntimeError("device did not respond")
    rig.hass.services.async_register("switch", "toggle", unreported)
    await rig.set("sensor.temperature", 21.3)
    await rig.advance(delay)
    await rig.set("switch.fingerbot", state)
    assert rig.record["phase"] == "pending"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"


async def test_pending_journal_stays_within_input_text_limit(rig):
    await install(rig)
    rig.hass.states.async_set("sensor.nursery_hvac_lifecycle", "01M4X424FZ9H9H0ADZQM005PTC:6700")
    await rig.trust("on")
    rig.fail_press = True
    await rig.set("sensor.temperature", 21.3)
    await walk(rig, 21.25, 20.123456)
    assert rig.record["phase"] == "pending"
    assert len(rig.hass.states.get("input_text.command").state) <= 255
