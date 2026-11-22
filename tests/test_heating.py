"""Heating mode: the same toggle controller, with the temperature direction flipped.

Winter 2026: the whole-house panel is switched to heating, and each room's
Fingerbot toggle then turns its unit on to HEAT. In Heating the controller
presses ON when the room is colder than target - upper_offset and OFF once it
is warmer than target + lower_offset (the cooling thresholds mirrored around
the target). Every temperature rule mirrors: residual heating after OFF,
"not heating" after an acknowledged ON, and drift reconciliation. Vent
evidence is cooling-only. Off never presses.

Unless stated, target 23: heating ON below 22.3, OFF above 22.5.
"""
import pytest

from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
ZIGBEE = INPUTS | {"command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode"}
WITH_VENT = ZIGBEE | {"vent_sensor": "sensor.vent", "vent_at_press": "input_number.vent_base",
                      "vent_enabled": "input_boolean.vent_enabled"}


async def install(rig, belief="off", mode="Heating", target=23, inputs=ZIGBEE):
    await rig.call("input_select", "set_options",
                   {"entity_id": "input_select.mode", "options": ["Cooling", "Heating", "Off"]})
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": mode})
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": target})
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, inputs) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust(belief)


async def report(rig, *temperatures, step=300):
    for value in temperatures:
        await rig.advance(step)
        await rig.set("sensor.temperature", value)
        await rig.tick()


def belief(rig):
    return rig.hass.states.get("input_boolean.cooling").state


async def acknowledged_on(rig, temperature=22.2, **kwargs):
    await install(rig, "off", **kwargs)
    await rig.set("sensor.temperature", temperature)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"


# ---------------------------------------------------------------- thresholds


async def test_cold_room_is_turned_on(rig):
    await install(rig, "off")
    await rig.set("sensor.temperature", 22.4)  # inside the band
    assert not rig.presses
    await rig.set("sensor.temperature", 22.2)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"
    assert belief(rig) == "on"


async def test_warm_room_is_turned_off(rig):
    await install(rig, "on")
    await rig.set("sensor.temperature", 22.5)  # not yet above target + lower_offset
    assert not rig.presses
    await rig.set("sensor.temperature", 22.6)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "off" and rig.record["reason"] == "zigbee_ack"
    assert belief(rig) == "off"


async def test_hot_room_is_never_turned_on_in_heating(rig):
    # Cooling would press ON above 21.7 with target 21.
    await install(rig, "off", target=21)
    await report(rig, 22.3, 22.9, 22.9)  # under 1.0 above the snapshot: no adoption
    assert not rig.presses


async def test_cold_room_is_never_turned_off_in_heating(rig):
    # Cooling would press OFF below 21.5 with target 21.
    await install(rig, "on", target=21)
    await rig.set("sensor.temperature", 20.4)
    await rig.tick()
    assert not rig.presses
    assert rig.record["expected"] == "on"


async def test_heating_cycle_on_then_off(rig):
    await acknowledged_on(rig)
    await report(rig, 22.3, 22.4, 22.6)  # 15 minutes: warm enough
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off" and belief(rig) == "off"


# ---------------------------------------------------------------- Off and Cooling


@pytest.mark.parametrize("start", ["on", "off"])
async def test_off_mode_never_presses(rig, start):
    await install(rig, start, mode="Off", target=21)
    await report(rig, 18.0, 18.0, 26.0, 26.0, 18.0, 26.0, *([26.0] * 12))
    assert not rig.presses


async def test_off_mode_holds_heating_demand_until_heating_resumes(rig):
    await install(rig, "off", mode="Off")
    await report(rig, 21.5, 21.5)
    assert not rig.presses
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": "Heating"})
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"


async def test_cooling_is_unchanged_with_heating_available(rig):
    await install(rig, "off", mode="Cooling", target=21)
    await rig.set("sensor.temperature", 21.2)  # cool room: cooling stays off
    await rig.tick()
    assert not rig.presses
    await rig.advance(60)
    await rig.set("sensor.temperature", 22.3)
    assert len(rig.presses) == 1 and rig.record["expected"] == "on"
    await rig.advance(300)
    await rig.set("sensor.temperature", 21.4)
    await rig.tick()
    assert len(rig.presses) == 2 and rig.record["expected"] == "off"


async def test_switching_to_cooling_stops_heating_demand(rig):
    await install(rig, "off")
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": "Cooling"})
    await rig.set("sensor.temperature", 21.0)
    await rig.tick()
    assert not rig.presses


# ---------------------------------------------------------------- not heating


async def test_acknowledged_on_that_never_warms_is_pressed_again(rig):
    await acknowledged_on(rig, 22.2)
    await report(rig, 22.1, 21.9, 22.0, 22.1, 22.1, 22.1)  # 30 minutes
    await report(rig, 22.1)  # 35 minutes: colder for 5 minutes only
    assert len(rig.presses) == 1
    await report(rig, 22.1)  # 40 minutes: colder for 10 minutes
    await rig.advance(30)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on" and belief(rig) == "on"


async def test_not_heating_estimate_becomes_off_before_the_restart_press(rig):
    await acknowledged_on(rig)
    await rig.set("select.fingerbot_mode", "SWITCH")
    await report(rig, *([22.1] * 8))
    assert rig.record["expected"] == "off" and rig.record["reason"] == "not_heating"
    assert belief(rig) == "off"
    assert len(rig.presses) == 1
    await rig.set("select.fingerbot_mode", "CLICK")
    assert len(rig.presses) == 2


async def test_working_heater_is_never_second_guessed(rig):
    await acknowledged_on(rig, 22.0)
    await report(rig, 22.0, 22.1, 22.2, 22.3, 22.3, 22.4, 22.4, 22.4, 22.4)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on"


async def test_believed_on_cooling_after_seventeen_minutes_re_estimates_off(rig):
    await acknowledged_on(rig, 22.2)
    await rig.set("select.fingerbot_mode", "SWITCH")
    await report(rig, 22.0, 21.9, 21.8)  # 15 minutes, 0.4 colder
    assert rig.record["expected"] == "on"
    await rig.advance(120)
    await rig.tick()
    assert rig.record["expected"] == "off" and rig.record["reason"] == "temperature"


# ---------------------------------------------------------------- reconciliation


async def test_believed_off_but_warming_is_adopted_on_then_turned_off(rig):
    await install(rig, "off", target=21)  # heating ON below 20.3, OFF above 20.5
    await rig.set("sensor.temperature", 22.9)
    assert rig.record["expected"] == "off"
    await rig.set("sensor.temperature", 23.0)  # 1.0 warmer than the snapshot
    assert rig.record["expected"] == "on" and rig.record["reason"] == "temperature"
    assert belief(rig) == "on" and not rig.presses
    await rig.advance(299)
    await rig.tick()
    assert not rig.presses
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"


async def test_believed_on_but_cooling_is_adopted_off_then_turned_on(rig):
    await install(rig, "on")
    await rig.advance(300)
    await rig.set("sensor.temperature", 21.7)  # 0.3 colder: not yet
    assert rig.record["expected"] == "on"
    await rig.set("sensor.temperature", 21.6)
    assert rig.record["expected"] == "off" and rig.record["reason"] == "temperature"
    assert belief(rig) == "off" and not rig.presses
    await rig.advance(779)
    await rig.tick()
    assert not rig.presses
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 1 and rig.record["expected"] == "on"


# ---------------------------------------------------------------- unconfirmed presses


async def failed_off(rig):
    rig.fail_press = True
    await install(rig, "on", target=21)
    await rig.set("sensor.temperature", 22.1)
    assert rig.record["phase"] == "pending" and rig.record["expected"] == "off"


async def test_unconfirmed_off_is_retried_when_the_room_keeps_warming(rig):
    await failed_off(rig)
    rig.fail_press = False
    await rig.advance(779)
    await rig.set("sensor.temperature", 22.7)  # 0.6 warmer, at its peak
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off" and rig.record["reason"] == "zigbee_ack"


async def test_residual_heat_after_off_is_not_retried(rig):
    await failed_off(rig)
    await rig.advance(780)
    await rig.set("sensor.temperature", 22.6)  # 0.5 residual heating
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"


async def test_unconfirmed_off_is_confirmed_by_a_fall_from_its_peak(rig):
    await failed_off(rig)
    await rig.advance(60)
    await rig.set("sensor.temperature", 22.4)
    await rig.advance(720)
    await rig.set("sensor.temperature", 22.2)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready" and rig.record["expected"] == "off"
    assert rig.record["reason"] == "temperature" and belief(rig) == "off"


async def failed_on(rig):
    rig.fail_press = True
    await install(rig, "off", target=24)  # ON below 23.3, OFF above 23.5
    await rig.set("sensor.temperature", 22.2)
    assert rig.record["phase"] == "pending" and rig.record["expected"] == "on"


async def test_unconfirmed_on_is_confirmed_by_warming(rig):
    await failed_on(rig)
    await rig.advance(780)
    await rig.set("sensor.temperature", 22.6)
    await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready" and rig.record["expected"] == "on"
    assert rig.record["reason"] == "temperature" and belief(rig) == "on"


async def test_unconfirmed_on_is_retried_when_the_room_keeps_cooling(rig):
    await failed_on(rig)
    rig.fail_press = False
    await rig.advance(779)
    await rig.set("sensor.temperature", 21.7)  # 0.5 colder, at its low
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"


# ---------------------------------------------------------------- vent evidence


async def test_vent_evidence_is_off_in_heating(rig):
    # A flat vent after an acknowledged ON is "vent_not_cooling" in Cooling.
    await install(rig, "off", inputs=WITH_VENT)
    await rig.set("sensor.vent", 22.0)
    await rig.set("sensor.temperature", 22.2)
    assert len(rig.presses) == 1 and rig.record["reason"] == "zigbee_ack"
    assert float(rig.hass.states.get("input_number.vent_base").state) == 22.0
    for i in range(40):
        await rig.advance(30)
        await rig.set("sensor.vent", 21.9 + (0.01 if i % 2 else 0))
        await rig.set("sensor.temperature", 22.2)
        await rig.tick()
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"


# ---------------------------------------------------------------- Off freezes the estimate


async def test_off_freezes_the_estimate_then_heating_starts_at_once(rig):
    # Winter, panel off: the room cools 1.5°C over three hours. Nothing is
    # running, so the OFF belief must not flip to ON.
    await install(rig, "off", mode="Off")
    await report(rig, *[round(22.0 - 1.5 * (i + 1) / 36, 2) for i in range(36)])
    assert rig.hass.states.get("sensor.temperature").state == "20.5"
    assert not rig.presses
    assert belief(rig) == "off" and rig.record["expected"] == "off"
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": "Heating"})
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"


async def test_off_keeps_an_on_belief_while_the_room_warms(rig):
    # Cooling-direction reconciliation would call a warming room OFF.
    await install(rig, "on", mode="Off", target=21)
    await report(rig, *[round(22.0 + 1.5 * (i + 1) / 36, 2) for i in range(36)])
    assert not rig.presses
    assert belief(rig) == "on" and rig.record["expected"] == "on"
