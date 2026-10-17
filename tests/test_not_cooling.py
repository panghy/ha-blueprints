"""An acknowledged ON that never cools the room is treated as not running.

17 Oct 2026, dining room: the 12:06 ON was acknowledged over Zigbee but the
Fingerbot did not work the AC button. Every later press then went the wrong
way and the room sat at 24–24.7°C against 23°C until Clement pressed manually.
A Zigbee acknowledgement proves receipt, not a press: if the room stays at
least 0.1°C warmer than at the press for 10 minutes, first seen 30–45 minutes
after it, the estimate becomes OFF and the toggle is pressed once more while
cooling is still needed.

Inputs follow conftest: target 21, ON above 21.7, OFF below 21.5.
"""

from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
ZIGBEE = INPUTS | {"command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode"}


async def acknowledged_on(rig, temperature=22.3):
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, ZIGBEE) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust("off")
    await rig.set("sensor.temperature", temperature)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["reason"] == "zigbee_ack"
    assert rig.record["expected"] == "on"


async def report(rig, *temperatures, step=300):
    for value in temperatures:
        await rig.advance(step)
        await rig.set("sensor.temperature", value)
        await rig.tick()


async def test_dining_replay_acknowledged_on_without_cooling_is_pressed_again(rig):
    # 12:31 dining ON at 24.0: 24.1, 24.3, 24.2, 24.1 ... and never cooler.
    await acknowledged_on(rig, 22.3)
    await report(rig, 22.4, 22.6, 22.5, 22.4, 22.4, 22.4)  # 30 minutes
    await report(rig, 22.4)  # 35 minutes: warm for 5 minutes only.
    assert len(rig.presses) == 1
    await report(rig, 22.4)  # 40 minutes: warm for 10 minutes.
    await rig.advance(30)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"


async def test_estimate_becomes_off_before_the_restart_press(rig):
    await acknowledged_on(rig)
    # Block the restart press so the intermediate estimate is observable.
    await rig.set("select.fingerbot_mode", "SWITCH")
    await report(rig, *([22.4] * 8))
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "off"
    assert rig.record["reason"] == "not_cooling"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    assert len(rig.presses) == 1
    await rig.set("select.fingerbot_mode", "CLICK")
    assert len(rig.presses) == 2


async def test_working_on_is_never_second_guessed(rig):
    await acknowledged_on(rig)
    await report(rig, 22.3, 22.2, 22.1, 22.0, 21.9, 21.8, 21.8, 21.8)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"


async def test_early_cooling_then_plateau_counts_as_working(rig):
    # AC at capacity: the room cools 0.2°C, then holds above the ON threshold.
    await acknowledged_on(rig, 22.6)
    await report(rig, 22.5, 22.4, 22.5, 22.6, 22.6, 22.6, 22.6, 22.6)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"


async def test_not_cooling_without_demand_changes_estimate_without_pressing(rig):
    # Believed ON and flat, but the target was raised (idle setback): the
    # estimate is corrected and no press is needed.
    await acknowledged_on(rig, 22.3)
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 21.7})  # Band 22.2–22.4.
    await report(rig, *([22.4] * 9))
    assert rig.record["expected"] == "off"
    assert rig.record["reason"] == "not_cooling"
    assert len(rig.presses) == 1


async def test_adopted_estimates_are_not_second_guessed(rig):
    # A restart adopts the helper estimate mid-run (no press, possibly at an
    # AC capacity plateau); only press acknowledgements are checked.
    await rig.set("select.fingerbot_mode", "CLICK")
    await rig.call("input_boolean", "turn_on", {"entity_id": "input_boolean.cooling"})
    config = expand(BLUEPRINT, ZIGBEE) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    assert rig.record["reason"] == "estimate_adopted"
    await report(rig, *([22.0] * 8))
    assert not rig.presses
    assert rig.record["expected"] == "on"


async def test_stale_sensor_never_triggers_not_cooling(rig):
    await acknowledged_on(rig)
    await rig.advance(1801)
    await rig.tick()
    assert rig.record["expected"] == "on"
    assert len(rig.presses) == 1


async def test_brief_dip_does_not_hide_a_press_that_did_not_cool(rig):
    # 17 Oct 14:04 dining: 24.0 at the press, a residual dip to 23.7, then
    # 24.3 by 14:34 while believed ON.
    await acknowledged_on(rig, 22.3)
    await report(rig, 22.0, 22.1, 22.2, 22.3, 22.4, 22.6, 22.6, 22.7)
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"


async def test_slow_night_cooling_is_not_flagged(rig):
    # Master bedroom 14–15 Oct: 18.5°C at the press, still 18.5°C after 30
    # minutes, then 18.0°C — slow but working.
    await acknowledged_on(rig, 22.3)
    await report(rig, 22.4, 22.4, 22.3, 22.3, 22.3, 22.3, 22.2, 22.1)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"


async def test_single_warm_reading_from_a_sensor_jump_is_ignored(rig):
    # Study/master blended sensors can step when they change source.
    await acknowledged_on(rig, 22.3)
    await report(rig, 22.2, 22.2, 22.3, 22.3, 22.3, 22.4, 22.3, 22.3, 22.4, 22.3)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"


async def test_working_ac_falling_behind_later_is_not_second_guessed(rig):
    # Hot afternoon: cools at first, then loses ground after 45 minutes.
    await acknowledged_on(rig, 22.3)
    await report(rig, 22.2, 22.1, 22.0, 22.0, 22.1, 22.2, 22.2, 22.3, 22.3)
    await report(rig, *([22.5] * 12))  # 45–105 minutes, warmer than the press.
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"
