"""ZHA service acknowledgement is transport evidence, never AC state truth."""
import json
import pytest
from homeassistant.setup import async_setup_component
from conftest import expand, INPUTS

BLUEPRINT = "hvac_recovering_thermostat.yaml"
ZIGBEE = INPUTS | {"command_transport": "zha_click", "fingerbot_mode": "select.fingerbot_mode"}

async def install(rig):
    await rig.set("select.fingerbot_mode", "CLICK")
    config = expand(BLUEPRINT, ZIGBEE) | {"id": "test_0", "alias": "Test 0"}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()

async def demand(rig, desired="on"):
    await install(rig)
    await rig.trust("off" if desired == "on" else "on")
    await rig.set("sensor.temperature", 22.3 if desired == "on" else 21.3)

@pytest.mark.parametrize("desired", ["on", "off"])
async def test_success_books_intent_without_ble_feedback(rig, desired):
    await demand(rig, desired)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "ready"
    assert rig.record["expected"] == desired
    assert rig.record["reason"] == "zigbee_ack"
    assert rig.hass.states.get("input_boolean.cooling").state == desired

async def test_old_gateway_unavailable_does_not_hold_zigbee(rig):
    await rig.set("sensor.feedback", "unavailable")
    await demand(rig)
    assert len(rig.presses) == 1
    assert rig.record["reason"] == "zigbee_ack"

@pytest.mark.parametrize("desired", ["on", "off"])
async def test_failed_call_stays_pending_and_retries_after_full_floor(rig, desired):
    rig.fail_press = True
    await demand(rig, desired)
    assert rig.record["phase"] == "pending"
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record["phase"] == "pending"  # Old gateway cannot acknowledge ZHA.
    await rig.advance(779)
    await rig.tick()
    assert len(rig.presses) == 1
    rig.fail_press = False
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["reason"] == "zigbee_ack"
    assert rig.record["expected"] == desired

@pytest.mark.parametrize("mode", ["SWITCH", "PROGRAM", "unknown", "unavailable"])
async def test_non_click_mode_holds_then_recovers(rig, mode):
    await install(rig)
    await rig.trust("off")
    await rig.set("select.fingerbot_mode", mode)
    await rig.set("sensor.temperature", 22.3)
    assert not rig.presses
    await rig.set("select.fingerbot_mode", "CLICK")
    assert len(rig.presses) == 1
    assert rig.record["reason"] == "zigbee_ack"

async def test_unsolicited_switch_edges_do_not_change_ac_estimate(rig):
    await install(rig)
    await rig.trust("off")
    await rig.set("input_number.target", 26)
    before = rig.record.copy()
    await rig.set("switch.fingerbot", "on")
    await rig.set("switch.fingerbot", "unavailable")
    await rig.set("switch.fingerbot", "off")
    await rig.set("sensor.feedback", "0500010154000300")
    assert rig.record == before
    assert not rig.presses

async def test_switch_value_is_not_mapped_to_ac_state(rig):
    await install(rig)
    await rig.set("switch.fingerbot", "on")
    await rig.trust("off")
    await rig.set("sensor.temperature", 22.3)
    assert rig.hass.states.get("switch.fingerbot").state == "off"
    assert rig.record["expected"] == "on"
    assert rig.hass.states.get("input_boolean.cooling").state == "on"

@pytest.mark.parametrize("desired", ["on", "off"])
async def test_reload_preserves_ambiguous_command_age(rig, tmp_path, desired):
    rig.fail_press = True
    await demand(rig, desired)
    before = rig.record.copy()
    await rig.advance(100)
    config = expand(BLUEPRINT, ZIGBEE) | {"id": "test_0", "alias": "Test 0", "initial_state": True}
    (tmp_path / "configuration.yaml").write_text(json.dumps({"automation": [config]}))
    await rig.call("automation", "reload", {})
    assert rig.record["phase"] == "pending"
    assert rig.record["issued"] == before["issued"]
    assert rig.record["booked"] == before["booked"]
    await rig.advance(679)
    await rig.tick()
    assert len(rig.presses) == 1
    rig.fail_press = False
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["reason"] == "zigbee_ack"

async def test_zigbee_ack_retains_normal_on_off_protection(rig):
    await demand(rig)
    await rig.set("sensor.temperature", 21.3)
    await rig.advance(299)
    await rig.tick()
    assert len(rig.presses) == 1
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 2
    assert rig.record["expected"] == "off"
    await rig.set("sensor.temperature", 22.3)
    await rig.advance(779)
    await rig.tick()
    assert len(rig.presses) == 2
    await rig.advance(1)
    await rig.tick()
    assert len(rig.presses) == 3
    assert rig.record["expected"] == "on"

@pytest.mark.parametrize("change", ["belief", "snapshot", "lifecycle", "mode"])
async def test_correction_during_successful_call_is_not_overwritten(rig, change):
    await install(rig)
    await rig.trust("off")
    original = rig.hass.services.async_services()["switch"]["toggle"]
    async def press(call):
        await original.job.target(call)
        if change == "belief":
            await rig.hass.services.async_call("input_boolean", "turn_off", {"entity_id":"input_boolean.cooling"}, blocking=True)
        elif change == "snapshot":
            await rig.hass.services.async_call("input_number", "set_value", {"entity_id":"input_number.snapshot", "value":20}, blocking=True)
        else:
            rig.hass.states.async_set("sensor.nursery_hvac_lifecycle" if change == "lifecycle" else "select.fingerbot_mode", "changed:44" if change == "lifecycle" else "SWITCH")
    rig.hass.services.async_register("switch", "toggle", press)
    await rig.set("sensor.temperature", 22.3)
    assert len(rig.presses) == 1
    assert rig.record.get("reason") != "zigbee_ack"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"
    if change == "snapshot":
        assert rig.hass.states.get("input_number.snapshot").state == "20.0"

@pytest.mark.parametrize("result", ["no_op", "unavailable", "foreign_context"])
async def test_silently_skipped_or_unowned_state_change_cannot_acknowledge(rig, result):
    await install(rig)
    await rig.trust("off")
    async def skipped(call):
        rig.presses.append((rig.now, dict(call.data)))
        if result != "no_op":
            rig.hass.states.async_set("switch.fingerbot", "unavailable" if result == "unavailable" else "on")
    rig.hass.services.async_register("switch", "toggle", skipped)
    await rig.set("sensor.temperature", 22.3)
    assert len(rig.presses) == 1
    assert rig.record["phase"] == "pending"
    assert rig.hass.states.get("input_boolean.cooling").state == "off"

async def test_zigbee_instance_does_not_require_a_ble_sensor(rig):
    await rig.set("select.fingerbot_mode", "CLICK")
    inputs = {k:v for k,v in ZIGBEE.items() if k != "gateway_status"}
    config = expand(BLUEPRINT, inputs) | {"id":"test_0", "alias":"Test 0"}
    assert await async_setup_component(rig.hass,"automation",{"automation":[config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.trust("off")
    await rig.set("sensor.temperature",22.3)
    assert len(rig.presses) == 1
    assert rig.record["reason"] == "zigbee_ack"
