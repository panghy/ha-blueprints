"""Nursery controller in Heating (Phase 2 of the heating plan).

In heating a running unit makes the supply vent WARM, so every cooling vent
rule (ON = vent 2 °C colder, OFF = vent warmer) would read a working heater as
"not running" and press it off. No heating vent data exists yet (the house has
only ever been in Cooling since the probe went in on 22 Oct 2026), so vent
evidence is off in Heating; the baseline is still recorded at each press so the
first heating nights can calibrate inverted thresholds.

Inputs mirror examples/nursery_recovering.yaml: ZHA CLICK, offsets 0.4/-0.1,
5-minute acknowledged OFF, vent probe. Target 21.
"""
from datetime import timedelta

import pytest

from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
VENT = "sensor.vent"
BASE = "input_number.vent_base"
NURSERY = INPUTS | {
    "command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode",
    "upper_offset": 0.4, "lower_offset": -0.1, "zha_min_off_minutes": 5,
    "vent_sensor": VENT, "vent_at_press": BASE, "vent_enabled": "input_boolean.vent_enabled",
}
COLD, WARM = 19.5, 22.0  # well past both heating thresholds


async def install(rig, mode, belief):
    await rig.call("input_select", "set_options",
                   {"entity_id": "input_select.mode", "options": ["Cooling", "Heating", "Off"]})
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": mode})
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, NURSERY) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust(belief)


async def minutes(rig, n, vent, temperature, step=30):
    for i in range(int(n * 60 / step)):
        await rig.advance(step)
        v = vent(i) if callable(vent) else vent
        await rig.set(VENT, round(v + (0.01 if i % 2 else 0), 2))
        t = temperature(i) if callable(temperature) else temperature
        await rig.set("sensor.temperature", t)
        await rig.tick()


def warming_vent(start):
    return lambda i: min(38.0, start + 3 * i)


async def heat_on(rig, vent=COLD):
    await install(rig, "Heating", "off")
    await rig.set(VENT, vent)
    await rig.set("sensor.temperature", COLD)
    assert len(rig.presses) == 1 and rig.record["expected"] == "on"


async def test_heating_on_is_pressed_when_cold_and_records_the_vent_baseline(rig):
    await heat_on(rig)
    assert rig.record["reason"] == "zigbee_ack"
    assert float(rig.hass.states.get(BASE).state) == COLD


async def test_working_heater_with_a_warming_vent_is_left_alone(rig):
    # Cooling rules would read "vent not 0.5 °C colder" as not running and press again.
    await heat_on(rig)
    await minutes(rig, 25, vent=warming_vent(COLD), temperature=lambda i: min(20.2, COLD + 0.02 * i))
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"


async def test_unacknowledged_heating_on_with_a_warming_vent_is_not_retried_by_the_vent(rig):
    rig.fail_press = True
    await heat_on(rig)
    rig.fail_press = False
    await rig.advance(100)
    await rig.set("switch.fingerbot", "on")  # late report: the press happened
    assert rig.record["phase"] == "pending"
    await minutes(rig, 14, vent=warming_vent(COLD), temperature=COLD + 0.1)
    assert len(rig.presses) == 1
    assert rig.record["reason"] != "vent"


async def test_heating_off_is_pressed_when_warm_and_a_cooling_vent_is_ignored(rig):
    await install(rig, "Heating", "on")
    await rig.set(VENT, 36.0)
    await rig.set("sensor.temperature", WARM)
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"
    await minutes(rig, 25, vent=lambda i: max(WARM, 36.0 - 1.5 * i), temperature=WARM)
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"


@pytest.mark.parametrize("mode,flagged", [("Cooling", True), ("Heating", False)])
async def test_flat_vent_after_an_acknowledged_on(rig, mode, flagged):
    # Same probe trace in both modes: Cooling keeps the PR #27 verdict and retry.
    room = 22.3 if mode == "Cooling" else COLD
    await install(rig, mode, "off")
    await rig.set(VENT, room)
    await rig.set("sensor.temperature", room)
    assert len(rig.presses) == 1 and rig.record["expected"] == "on"
    await minutes(rig, 6.5, vent=room - 0.1, temperature=room)
    assert (rig.record["reason"] == "vent_not_cooling") is flagged
    await minutes(rig, 7.5, vent=room - 0.1, temperature=room)
    assert len(rig.presses) == (2 if flagged else 1)
    if flagged:
        assert rig.presses[1][0] - rig.presses[0][0] >= timedelta(seconds=780)


@pytest.mark.parametrize("belief,temperature", [("off", COLD), ("on", WARM), ("off", 24.0), ("on", 18.0)])
async def test_off_mode_never_presses(rig, belief, temperature):
    await install(rig, "Off", belief)
    await rig.set(VENT, 30.0)
    await rig.set("sensor.temperature", temperature)
    await minutes(rig, 40, vent=lambda i: 30.0 - 0.3 * i, temperature=temperature)
    assert not rig.presses

