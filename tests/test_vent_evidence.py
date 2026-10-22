"""Supply-vent probe evidence (nursery, 22 Oct 2026).

Night 2 (21-22 Oct, probe deep in the airflow): every ON took the vent 2 °C
colder within 2.6 min (median 1.5) and every OFF took it 1 °C warmer within
6.6 min (median 5.0). On 22 Oct 07:01 a Zigbee-wide blip left the probe stuck
for 55 minutes, so a probe that has not reported within 3 minutes is ignored.

Inputs follow conftest: target 21, ON above 21.7, OFF below 21.5.
"""
import json

import pytest

from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
VENT = "sensor.vent"
BASE = "input_number.vent_base"
ZIGBEE = INPUTS | {"command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode"}
WITH_VENT = ZIGBEE | {"vent_sensor": VENT, "vent_at_press": BASE, "vent_enabled": "input_boolean.vent_enabled"}


async def install(rig, inputs=WITH_VENT, belief="off"):
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, inputs) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust(belief)


async def press_on(rig, inputs=WITH_VENT, vent=22.0):
    await install(rig, inputs, "off")
    if vent is not None:
        await rig.set(VENT, vent)
    await rig.set("sensor.temperature", 22.3)
    assert len(rig.presses) == 1 and rig.record["expected"] == "on"


async def press_off(rig, vent=13.0):
    await install(rig, WITH_VENT, "on")
    await rig.set(VENT, vent)
    await rig.set("sensor.temperature", 21.4)
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"


async def minutes(rig, n, vent=None, temperature=None, step=30):
    """Advance n minutes; the probe reports every 30 s (with tiny jitter so it changes)."""
    for i in range(int(n * 60 / step)):
        await rig.advance(step)
        if vent is not None:
            v = vent(i) if callable(vent) else vent
            await rig.set(VENT, round(v + (0.01 if i % 2 else 0), 2))
        if temperature is not None:
            await rig.set("sensor.temperature", temperature)
        await rig.tick()


def num(rig, entity):
    return float(rig.hass.states.get(entity).state)


# ---------------------------------------------------------------- baseline and freshness


async def test_vent_baseline_is_recorded_at_the_press(rig):
    await press_on(rig)
    assert num(rig, BASE) == 22.0
    assert rig.record["reason"] == "zigbee_ack"


async def test_stale_probe_at_the_press_records_no_baseline(rig):
    await rig.set(VENT, 22.0)
    await rig.advance(240)  # last report 4 minutes old at the press
    await press_on(rig, vent=None)
    assert num(rig, BASE) == -50


async def test_working_on_is_left_alone(rig):
    await press_on(rig)
    await minutes(rig, 20, vent=lambda i: max(13.0, 22.0 - 2 * i), temperature=22.2)
    assert len(rig.presses) == 1
    assert rig.record["expected"] == "on" and rig.record["reason"] == "zigbee_ack"


# ---------------------------------------------------------------- acknowledged ON that did not start the AC


async def test_acknowledged_on_without_colder_vent_is_retried_at_the_floor(rig):
    await press_on(rig)
    await minutes(rig, 6.5, vent=21.9, temperature=22.3)
    assert rig.record["expected"] == "off" and rig.record["reason"] == "vent_not_cooling"
    assert len(rig.presses) == 1  # never before the 13-minute floor
    await minutes(rig, 6, vent=21.9, temperature=22.3)
    assert len(rig.presses) == 1
    await minutes(rig, 1, vent=21.9, temperature=22.3)
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "on"
    assert rig.presses[1][0] - rig.presses[0][0] >= __import__("datetime").timedelta(seconds=780)


async def test_vent_retry_happens_at_most_once_an_hour(rig):
    await press_on(rig)
    await minutes(rig, 14, vent=21.9, temperature=22.3)
    assert len(rig.presses) == 2
    await minutes(rig, 20, vent=21.9, temperature=22.3)  # the retry also looks flat
    assert len(rig.presses) == 2 and rig.record["reason"] == "zigbee_ack"
    assert "vx" in rig.record and len(json.dumps(rig.record, separators=(",", ":"))) <= 255


async def test_stuck_probe_gives_no_verdict(rig):
    # 22 Oct 07:01: one report after the blip, then silence.
    await press_on(rig)
    await rig.advance(60)
    await rig.set(VENT, 21.9)
    await minutes(rig, 20, temperature=22.3)
    assert len(rig.presses) == 1 and rig.record["reason"] == "zigbee_ack"


async def test_switch_off_disables_vent_evidence(rig):
    await press_on(rig)
    await rig.call("input_boolean", "turn_off", {"entity_id": "input_boolean.vent_enabled"})
    await minutes(rig, 20, vent=21.9, temperature=22.3)
    assert len(rig.presses) == 1 and rig.record["reason"] == "zigbee_ack"


async def test_no_probe_configured_behaves_as_before(rig):
    await press_on(rig, ZIGBEE)
    await minutes(rig, 20, vent=21.9, temperature=22.3)
    assert len(rig.presses) == 1 and rig.record["reason"] == "zigbee_ack"


async def test_flat_vent_without_demand_only_corrects_the_estimate(rig):
    await press_on(rig)
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 21.7})
    await minutes(rig, 20, vent=21.9, temperature=22.3)
    assert rig.record["expected"] == "off" and rig.record["reason"] == "vent_not_cooling"
    assert len(rig.presses) == 1


# ---------------------------------------------------------------- acknowledged OFF that left the AC running


async def test_working_off_is_left_alone(rig):
    await press_off(rig)
    await minutes(rig, 20, vent=lambda i: min(19.0, 13.0 + 0.3 * i), temperature=21.4)
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"


async def test_acknowledged_off_with_vent_still_cold_is_retried(rig):
    await press_off(rig)
    await minutes(rig, 9.5, vent=13.0, temperature=21.4)
    assert rig.record["expected"] == "off" and len(rig.presses) == 1
    await minutes(rig, 1, vent=13.0, temperature=21.4)
    assert rig.record["expected"] == "on" and rig.record["reason"] == "vent_still_cooling"
    await minutes(rig, 2, vent=13.0, temperature=21.4)
    assert len(rig.presses) == 1  # still inside 13 minutes of the press
    await minutes(rig, 4, vent=13.0, temperature=21.4)
    assert len(rig.presses) == 2 and rig.record["expected"] == "off"
    assert rig.presses[1][0] - rig.presses[0][0] >= __import__("datetime").timedelta(seconds=780)


# ---------------------------------------------------------------- unacknowledged presses


async def test_pending_on_confirmed_by_the_vent_keeps_the_13_minute_floor(rig):
    rig.fail_press = True
    await press_on(rig)
    rig.fail_press = False
    assert rig.record["phase"] == "pending"
    await minutes(rig, 2, vent=19.5, temperature=22.3)
    assert rig.record["phase"] == "ready" and rig.record["reason"] == "vent"
    assert rig.record["expected"] == "on"
    await minutes(rig, 8, vent=13.0, temperature=21.3)  # already below the OFF threshold
    assert len(rig.presses) == 1
    await minutes(rig, 4, vent=13.0, temperature=21.3)
    assert len(rig.presses) == 2 and rig.record["expected"] == "off"
    assert rig.presses[1][0] - rig.presses[0][0] >= __import__("datetime").timedelta(seconds=780)


@pytest.mark.parametrize("inputs,retried", [(WITH_VENT, True), (ZIGBEE, False)])
async def test_pending_on_with_flat_vent_is_retried_at_13_minutes(rig, inputs, retried):
    # The Fingerbot moved (its switch report came too late to count as an ack), so the
    # no-response rule does not apply; without a probe the room shows no evidence yet.
    rig.fail_press = True
    await press_on(rig, inputs)
    rig.fail_press = False
    await rig.advance(100)
    await rig.set("switch.fingerbot", "on")
    assert rig.record["phase"] == "pending"
    await minutes(rig, 11, vent=21.9, temperature=22.3)
    assert len(rig.presses) == 1
    await minutes(rig, 1, vent=21.9, temperature=22.3)
    assert (len(rig.presses) == 2) is retried
    if retried:
        assert rig.presses[1][0] - rig.presses[0][0] >= __import__("datetime").timedelta(seconds=780)


async def test_pending_off_confirmed_by_the_vent(rig):
    rig.fail_press = True
    await press_off(rig)
    rig.fail_press = False
    await minutes(rig, 6, vent=lambda i: 13.0 + 0.2 * i, temperature=21.4)
    assert rig.record["phase"] == "ready" and rig.record["reason"] == "vent"
    assert rig.record["expected"] == "off"
    await minutes(rig, 20, vent=19.0, temperature=21.4)
    assert len(rig.presses) == 1


async def test_stuck_cold_probe_never_undoes_a_landed_off(rig):
    # The dangerous case: a stuck cold reading after a real OFF must not cause another press.
    await press_off(rig)
    await rig.advance(60)
    await rig.set(VENT, 13.05)
    await minutes(rig, 25, temperature=21.4)
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"


async def test_unavailable_probe_gives_no_verdict(rig):
    await press_off(rig)
    await minutes(rig, 2, vent=13.0, temperature=21.4)
    await rig.set(VENT, "unavailable")
    await minutes(rig, 20, temperature=21.4)
    assert len(rig.presses) == 1 and rig.record["expected"] == "off"
