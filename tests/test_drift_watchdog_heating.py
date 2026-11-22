"""The drift watchdog runs mirrored in Heating and is unchanged in Cooling.

Winter 2026: the house panel switches to heating and each room's AC button
then runs its unit in HEAT. cooling_active keeps meaning "the unit is on".
Heating forward (believed OFF): room >= 1.0 above comfort, at or above the
heating floor, not falling, setpoint stable. Heating reverse (believed ON):
room >= 0.8 below target, not risen 0.5 above the toggle temperature, not
rising faster than 0.5°C/h, setpoint stable. Off keeps the watchdog silent.
The Cooling tests pin the exact pre-heating alerts and gates.
"""
import pytest

from homeassistant.setup import async_setup_component
from conftest import expand

BLUEPRINT = "hvac_drift_watchdog.yaml"
TREND = "binary_sensor.trend"
WATCHDOG = {
    "temp_sensor": "sensor.temperature",
    "target_temp": "input_number.target",
    "cooling_active": "input_boolean.cooling",
    "hvac_mode_select": "input_select.mode",
    "room_name": "Study",
    "temp_trend": TREND,
    "temp_at_toggle": "input_number.snapshot",
    "notify_service": "notify.test",
    "notify_service_2": "notify.second",
    "tts_media_player": "media_player.speaker",
}
SETBACK = {"setback_flag": "input_boolean.setback", "setback_snapshot": "input_number.pre_idle"}
# Not rising (cooling) and not falling (heating): a flat room.
FLAT = 0.0


class Calls(list):
    def of(self, service):
        return [data for name, data in self if name == service]


async def watchdog(rig, mode, belief, temp, *, target=22, toggle=22, gradient=FLAT,
                   trend=None, setback=None, **inputs):
    calls = Calls()

    async def record(call):
        calls.append((f"{call.domain}.{call.service}", dict(call.data)))

    for domain, service in (("persistent_notification", "create"), ("notify", "second"),
                            ("notify", "test"), ("tts", "cloud_say")):
        rig.hass.services.async_register(domain, service, record)
    await rig.call("input_select", "set_options",
                   {"entity_id": "input_select.mode", "options": ["Cooling", "Heating", "Off"]})
    await rig.call("input_select", "select_option", {"entity_id": "input_select.mode", "option": mode})
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": target})
    await rig.call("input_number", "set_value", {"entity_id": "input_number.snapshot", "value": toggle})
    await rig.call("input_boolean", "turn_" + belief, {"entity_id": "input_boolean.cooling"})
    if setback is not None:
        flag, comfort = setback
        await rig.set("input_boolean.setback", flag)
        await rig.set("input_number.pre_idle", comfort)
    await rig.set("sensor.temperature", temp)
    await set_trend(rig, gradient, trend)
    config = expand(BLUEPRINT, WATCHDOG | inputs) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    # Past the setpoint grace (45), drift window (20) and reverse window (45).
    await rig.advance(46 * 60)
    return calls


async def set_trend(rig, gradient, state=None, min_gradient=0.0001):
    attributes = {"min_gradient": min_gradient}
    if gradient is not None:
        attributes["gradient"] = gradient
    state = state or ("on" if gradient is not None and gradient > min_gradient else "off")
    rig.hass.states.async_set(TREND, state, attributes)
    await rig.settle()


async def check(rig):
    await rig.call("automation", "trigger", {"entity_id": "automation.test_0", "skip_condition": False})


def titles(calls):
    return [data.get("title") for name, data in calls if name != "tts.cloud_say"]


# --- Heating forward: believed OFF, heater stuck on --------------------------

async def test_heating_stuck_on_heater_alerts_every_wired_output(rig):
    calls = await watchdog(rig, "Heating", "off", 25.5)
    await check(rig)
    assert [name for name, _ in calls] == [
        "persistent_notification.create", "notify.test", "notify.second", "tts.cloud_say"]
    assert set(titles(calls)) == {"HVAC desync suspected: Study"}
    persistent = calls.of("persistent_notification.create")[0]["message"]
    assert persistent.startswith(
        "Study is 25.5°C, 3.5°C above the comfort setpoint 22.0°C, but heating has been "
        "believed off (cooling_active off) for 46 minutes and the room is not falling")
    assert "physically ON heating" in persistent
    phone = ("Study: 25.5°C vs setpoint 22.0°C with heating believed OFF and temp not "
             "falling. AC probably stuck ON heating — needs a manual check.")
    assert calls.of("notify.test")[0]["message"] == phone
    assert calls.of("notify.second")[0]["message"] == phone
    assert "stuck on heating" in calls.of("tts.cloud_say")[0]["message"]


@pytest.mark.parametrize("temp", [24.9, 25.0])
async def test_heating_floor_gate(rig, temp):
    calls = await watchdog(rig, "Heating", "off", temp)
    await check(rig)
    assert bool(calls) == (temp >= 25.0)


@pytest.mark.parametrize("temp", [22.9, 23.0])
async def test_heating_drift_threshold_is_above_target(rig, temp):
    calls = await watchdog(rig, "Heating", "off", temp, stuck_on_heating_floor=22.0)
    await check(rig)
    assert bool(calls) == (temp >= 23.0)


@pytest.mark.parametrize("gradient, alerts", [
    (FLAT, True),
    (0.0005, True),        # Rising: a stuck heater pushes the room up.
    (-0.00009, True),      # Inside the helper's own min_gradient band.
    (-0.0001, True),       # Exactly -min_gradient: mirror of the "on" test.
    (-0.00011, False),     # Falling: the room is cooling off on its own.
    (None, False),         # Gradient missing: fail-quiet.
])
async def test_heating_not_falling_gate(rig, gradient, alerts):
    calls = await watchdog(rig, "Heating", "off", 25.5, gradient=gradient)
    await check(rig)
    assert bool(calls) == alerts


@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_heating_trend_unreadable_is_quiet(rig, state):
    calls = await watchdog(rig, "Heating", "off", 25.5, trend=state)
    await check(rig)
    assert not calls


async def test_heating_missing_min_gradient_is_quiet(rig):
    calls = await watchdog(rig, "Heating", "off", 25.5)
    rig.hass.states.async_set(TREND, "off", {"gradient": FLAT})
    await check(rig)
    assert not calls


async def test_heating_setpoint_grace_defers_the_alert(rig):
    calls = await watchdog(rig, "Heating", "off", 25.5)
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 21})
    await rig.advance(44 * 60)
    await check(rig)
    assert not calls
    await rig.advance(60)
    await check(rig)
    assert calls


async def test_heating_believed_off_for_too_short_is_quiet(rig):
    calls = await watchdog(rig, "Heating", "off", 25.5)
    await rig.call("input_boolean", "turn_on", {"entity_id": "input_boolean.cooling"})
    await rig.advance(60)
    await rig.call("input_boolean", "turn_off", {"entity_id": "input_boolean.cooling"})
    await rig.advance(19 * 60)
    await check(rig)
    assert not calls


async def test_heating_ignores_the_cooling_signature(rig):
    # Cold, flat room believed OFF: a stuck-on AC in summer, normal in winter.
    calls = await watchdog(rig, "Heating", "off", 19.5)
    await check(rig)
    assert not calls


async def test_heating_setback_compares_against_the_comfort_value(rig):
    # Idle setback lowered the live target to 18; comfort is 22. 22.5 is 4.5
    # above the live target but only 0.5 above comfort: not adrift.
    calls = await watchdog(rig, "Heating", "off", 22.5, target=18, setback=("on", 22),
                           stuck_on_heating_floor=22.0, **SETBACK)
    await check(rig)
    assert not calls


async def test_heating_setback_live_target_without_wiring(rig):
    calls = await watchdog(rig, "Heating", "off", 22.5, target=18, setback=("on", 22),
                           stuck_on_heating_floor=22.0)
    await check(rig)
    assert calls


async def test_heating_setback_churn_does_not_rearm_the_grace(rig):
    calls = await watchdog(rig, "Heating", "off", 25.5, setback=("off", 22), **SETBACK)
    # The idle setback engages: marker on and target 22 -> 18 in one step;
    # the snapshot is a same-value write, so its last_changed stays old.
    await rig.set("input_boolean.setback", "on")
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 18})
    await rig.advance(5 * 60)
    await check(rig)
    persistent = calls.of("persistent_notification.create")[0]["message"]
    assert persistent.startswith("Study is 25.5°C, 3.5°C above the comfort setpoint 22.0°C "
                                 "(live target 18.0°C, held by an active setback)")


async def test_heating_independent_target_change_still_arms_the_grace(rig):
    calls = await watchdog(rig, "Heating", "off", 25.5, setback=("off", 22), **SETBACK)
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 21})
    await rig.advance(5 * 60)
    await check(rig)
    assert not calls


# --- Heating reverse: believed ON, heating not working -----------------------

async def test_heating_not_warming_alerts(rig):
    calls = await watchdog(rig, "Heating", "on", 20.0, toggle=20.0)
    await check(rig)
    assert [name for name, _ in calls] == [
        "persistent_notification.create", "notify.test", "notify.second", "tts.cloud_say"]
    assert set(titles(calls)) == {"HVAC not heating: Study"}
    persistent = calls.of("persistent_notification.create")[0]
    assert persistent["notification_id"] == "hvac_reverse_drift_automation.test_0"
    assert persistent["message"].startswith(
        "Study is 20.0°C, 2.0°C BELOW target 22.0°C, and has not warmed since heating "
        "(cooling_active) turned on 46 minutes ago at 20.0°C")
    phone = ("Study: 20.0°C vs target 22.0°C with heating believed ON but the room not "
             "warming (20.0°C at the toggle). AC probably physically OFF — needs a manual check.")
    assert calls.of("notify.test")[0]["message"] == phone
    assert calls.of("notify.second")[0]["message"] == phone
    assert "the room is not heating" in calls.of("tts.cloud_say")[0]["message"]


@pytest.mark.parametrize("temp, alerts", [
    (19.4, True),   # Fell 0.6 below the toggle temperature.
    (20.5, True),   # Risen exactly reverse_min_drop: still not warming.
    (20.6, False),  # Risen 0.6: the heater is adding heat.
])
async def test_heating_reverse_rise_gate(rig, temp, alerts):
    calls = await watchdog(rig, "Heating", "on", temp, toggle=20.0)
    await check(rig)
    assert bool(calls) == alerts


@pytest.mark.parametrize("temp, alerts", [(21.2, True), (21.3, False)])
async def test_heating_reverse_below_target_gate(rig, temp, alerts):
    calls = await watchdog(rig, "Heating", "on", temp, toggle=21.0)
    await check(rig)
    assert bool(calls) == alerts


@pytest.mark.parametrize("per_hour, alerts", [(0.4, True), (0.6, False), (-3.0, True)])
async def test_heating_reverse_rising_rate_gate(rig, per_hour, alerts):
    calls = await watchdog(rig, "Heating", "on", 20.0, toggle=20.0, gradient=per_hour / 3600)
    await check(rig)
    assert bool(calls) == alerts


async def test_heating_reverse_ignores_the_cooling_signature(rig):
    # Warm room, believed ON, no drop: not cooling in summer, fine in winter.
    calls = await watchdog(rig, "Heating", "on", 23.5, toggle=23.5)
    await check(rig)
    assert not calls


async def test_heating_reverse_waits_for_the_setpoint_grace(rig):
    calls = await watchdog(rig, "Heating", "on", 20.0, toggle=20.0)
    await rig.call("input_number", "set_value", {"entity_id": "input_number.target", "value": 22.5})
    await rig.advance(30 * 60)
    await check(rig)
    assert not calls


# --- Off ----------------------------------------------------------------------

@pytest.mark.parametrize("belief, temp, toggle", [
    ("off", 25.5, 22), ("off", 19.5, 22), ("on", 20.0, 20.0), ("on", 23.5, 23.5)])
async def test_off_mode_never_alerts(rig, belief, temp, toggle):
    calls = await watchdog(rig, "Off", belief, temp, toggle=toggle,
                           stuck_on_heating_floor=20.0, stuck_on_ceiling=24.0)
    await check(rig)
    assert not calls


# --- Cooling: exactly as before ----------------------------------------------

COOLING_FORWARD = [
    ("persistent_notification.create", {
        "notification_id": "hvac_drift_automation.test_0",
        "title": "HVAC desync suspected: Study",
        "message": (
            "Study is 20.5°C, 1.5°C below the comfort setpoint 22.0°C, but cooling_active "
            "has been off for 46 minutes (trend: off, gradient 0.0). The AC is likely "
            "physically ON while HA believes it is OFF. Reconcile: check the room, press "
            "the AC power if needed, and make sure the cooling_active boolean matches reality.")}),
    ("notify.test", {
        "title": "HVAC desync suspected: Study",
        "message": ("Study: 20.5°C vs setpoint 22.0°C with cooling believed OFF and temp not "
                    "rising. AC probably stuck ON — needs a manual check."),
        "data": {}}),
    ("notify.second", {
        "title": "HVAC desync suspected: Study",
        "message": ("Study: 20.5°C vs setpoint 22.0°C with cooling believed OFF and temp not "
                    "rising. AC probably stuck ON — needs a manual check."),
        "data": {}}),
    ("tts.cloud_say", {
        "cache": False, "entity_id": "media_player.speaker",
        "message": ("Attention: possible H V A C desync in the Study. The room is 20.5 degrees "
                    "against a setpoint of 22.0. The AC may be stuck on and needs a manual check.")}),
]

COOLING_REVERSE = [
    ("persistent_notification.create", {
        "notification_id": "hvac_reverse_drift_automation.test_0",
        "title": "HVAC not cooling: Study",
        "message": (
            "Study is 23.5°C, 1.5°C ABOVE target 22.0°C, and has not cooled since "
            "cooling_active turned on 46 minutes ago at 23.5°C (trend: off, gradient 0.0). "
            "The AC is likely physically OFF while HA believes it is ON. Reconcile: check "
            "the room, press the AC power if needed, and make sure the cooling_active "
            "boolean matches reality.")}),
    ("notify.test", {
        "title": "HVAC not cooling: Study",
        "message": ("Study: 23.5°C vs target 22.0°C with cooling believed ON but the room not "
                    "cooling (23.5°C at the toggle). AC probably physically OFF — needs a "
                    "manual check."),
        "data": {}}),
    ("notify.second", {
        "title": "HVAC not cooling: Study",
        "message": ("Study: 23.5°C vs target 22.0°C with cooling believed ON but the room not "
                    "cooling (23.5°C at the toggle). AC probably physically OFF — needs a "
                    "manual check."),
        "data": {}}),
    ("tts.cloud_say", {
        "cache": False, "entity_id": "media_player.speaker",
        "message": ("Attention: the Study air conditioner is believed on but the room is not "
                    "cooling. The room is 23.5 degrees against a target of 22.0. The unit may "
                    "be physically off and needs a manual check.")}),
]


async def test_cooling_forward_alert_is_unchanged(rig):
    calls = await watchdog(rig, "Cooling", "off", 20.5)
    await check(rig)
    assert list(calls) == COOLING_FORWARD


async def test_cooling_reverse_alert_is_unchanged(rig):
    calls = await watchdog(rig, "Cooling", "on", 23.5, toggle=23.5)
    await check(rig)
    assert list(calls) == COOLING_REVERSE


async def test_cooling_setback_message_is_unchanged(rig):
    calls = await watchdog(rig, "Cooling", "off", 20.5, target=24.5, setback=("on", 22), **SETBACK)
    await check(rig)
    assert calls.of("persistent_notification.create")[0]["message"].startswith(
        "Study is 20.5°C, 1.5°C below the comfort setpoint 22.0°C (live target 24.5°C, "
        "held by an active setback), but cooling_active has been off for 46 minutes")


@pytest.mark.parametrize("temp, gradient, alerts", [
    (20.5, FLAT, True),
    (21.0, FLAT, True),      # Exactly drift_threshold below target.
    (21.1, FLAT, False),
    (21.6, FLAT, False),     # Above the stuck-on ceiling (target raised).
    (20.5, 0.0002, False),   # Rising.
    (25.5, FLAT, False),     # Heating's stuck-on signature.
])
async def test_cooling_forward_gates_are_unchanged(rig, temp, gradient, alerts):
    target = 22.7 if temp == 21.6 else 22
    calls = await watchdog(rig, "Cooling", "off", temp, target=target, gradient=gradient)
    await check(rig)
    assert bool(calls) == alerts


@pytest.mark.parametrize("temp, toggle, per_hour, alerts", [
    (23.5, 23.5, 0.0, True),
    (23.0, 23.5, 0.0, True),    # Fell exactly reverse_min_drop.
    (22.9, 23.5, 0.0, False),   # Fell 0.6: cooling.
    (22.7, 22.7, 0.0, False),   # Only 0.7 above target.
    (23.5, 23.5, -0.6, False),  # Visibly pulling down.
    (20.0, 20.0, 0.0, False),   # Heating's not-warming signature.
])
async def test_cooling_reverse_gates_are_unchanged(rig, temp, toggle, per_hour, alerts):
    calls = await watchdog(rig, "Cooling", "on", temp, toggle=toggle, gradient=per_hour / 3600)
    await check(rig)
    assert bool(calls) == alerts
