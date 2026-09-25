"""The prepared package stays inactive and honors the existing two-helper rule."""
import copy
import json
from pathlib import Path

import jsonpatch
import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util

from conftest import ROOT, INPUTS, expand

PACKAGE = yaml_util.load_yaml(str(ROOT / "examples/nursery_guarded.yaml"))
MAPPING = {v: INPUTS.get(k, v)
           for k, v in PACKAGE["automation"][0]["use_blueprint"]["input"].items()
           if isinstance(v, str)}
MAPPING.update({
    "automation.nursery_guarded_thermostat": "automation.test_0",
    "automation.nursery_hvac_automation": "automation.legacy_controller",
    "automation.nursery_hvac_press_listener": "automation.legacy_listener",
    "input_boolean.nursery_lost_press_recovery": "input_boolean.recovery",
})


def mapped(value):
    if isinstance(value, str):
        for old, new in MAPPING.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [mapped(item) for item in value]
    if isinstance(value, dict):
        return {key: mapped(item) for key, item in value.items()}
    return value


async def prepare(rig, *, fail_correction=False):
    await rig.install("hvac_guarded_thermostat.yaml")
    await rig.call("automation", "turn_off", {"entity_id": "automation.test_0", "stop_actions": True})
    for entity in ("automation.legacy_controller", "automation.legacy_listener", "input_boolean.recovery"):
        await rig.set(entity, "off")
    corrections = []

    async def correct(call):
        corrections.append(dict(call.data))
        if fail_correction:
            return  # Existing script can stop normally when its sensor is invalid.
        await rig.hass.services.async_call("input_number", "set_value", {
            "entity_id": "input_number.snapshot", "value": 22,
        }, blocking=True)
        await rig.hass.services.async_call("input_boolean", "turn_on" if call.data["cooling"] else "turn_off", {
            "entity_id": "input_boolean.cooling",
        }, blocking=True)

    rig.hass.services.async_register("script", "hvac_set_belief", correct)
    assert await async_setup_component(rig.hass, "script", {"script": mapped(PACKAGE["script"])})
    await rig.settle()
    await rig.advance(1)
    return corrections


@pytest.mark.asyncio
async def test_package_starts_disabled_with_untrusted_journal(rig):
    instance = PACKAGE["automation"][0]
    assert instance["initial_state"] is False
    journal = PACKAGE["input_text"]["nursery_hvac_command"]
    assert json.loads(journal["initial"])["phase"] == "needs_verification"
    assert journal["max"] == 255
    config = expand("hvac_guarded_thermostat.yaml") | {"id": "test_0", "alias": "Test 0", "initial_state": instance["initial_state"]}
    assert await async_setup_component(rig.hass, "automation", {"automation": [config]})
    await rig.hass.async_start()
    await rig.settle()
    await rig.set("sensor.temperature", 19.1)
    assert rig.hass.states.get("automation.test_0").state == "off"
    assert not rig.presses


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_physical_check_script_never_enables_control(rig, verified):
    corrections = await prepare(rig)
    await rig.call("script", "nursery_prepare_verified_belief", {"physically_verified": verified, "cooling": False})
    assert corrections == ([{"room": "nursery", "cooling": False}] if verified else [])
    assert rig.record["phase"] == ("verified" if verified else "needs_verification")
    assert rig.hass.states.get("automation.test_0").state == "off"
    assert not rig.presses


@pytest.mark.asyncio
async def test_prepared_verification_allows_exactly_one_activation(rig):
    await prepare(rig)
    await rig.call("script", "nursery_prepare_verified_belief", {"physically_verified": True, "cooling": False})
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    assert rig.record["phase"] == "ready"
    assert not rig.presses  # Enabling itself cannot send a command.
    await rig.advance(800)
    await rig.tick()
    assert len(rig.presses) == 1  # Normal verified operation still works.
    await rig.call("automation", "turn_off", {"entity_id": "automation.test_0", "stop_actions": True})
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    assert rig.record["phase"] == "needs_verification"
    assert len(rig.presses) == 1


@pytest.mark.asyncio
async def test_reload_while_disabled_invalidates_unused_verification(rig):
    await prepare(rig)
    await rig.call("script", "nursery_prepare_verified_belief", {"physically_verified": True, "cooling": False})
    assert rig.record["phase"] == "verified"
    config = expand("hvac_guarded_thermostat.yaml") | {
        "id": "test_0", "alias": "Changed while disabled", "initial_state": False,
    }
    Path(rig.hass.config.path("configuration.yaml")).write_text(json.dumps({"automation": [config]}))
    await rig.call("automation", "reload", {})
    await rig.call("automation", "turn_on", {"entity_id": "automation.test_0"})
    await rig.tick()
    assert rig.record["phase"] == "needs_verification"
    assert not rig.presses


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["automation.legacy_controller", "automation.legacy_listener", "input_boolean.recovery"])
async def test_physical_check_script_refuses_competing_writers(rig, blocker):
    corrections = await prepare(rig)
    await rig.set(blocker, "on")
    await rig.call("script", "nursery_prepare_verified_belief", {"physically_verified": True, "cooling": False})
    assert not corrections
    assert rig.record["phase"] == "needs_verification"
    assert not rig.presses


@pytest.mark.asyncio
async def test_aborted_two_helper_correction_cannot_arm_journal(rig):
    await prepare(rig, fail_correction=True)
    await rig.call("script", "nursery_prepare_verified_belief", {"physically_verified": True, "cooling": True})
    assert rig.record["phase"] == "needs_verification"
    assert not rig.presses


def test_startup_patch_excludes_only_nursery_and_aborts_on_drift():
    patch = jsonpatch.JsonPatch(json.loads((ROOT / "examples/nursery_startup_resync.patch.json").read_text()))
    rooms = [{"key": key, "retained_data": [key, 42]} for key in
             ("study", "nursery", "master_bedroom", "dining_room", "living_room")]
    original = {"id": "1788478600000", "variables": {"rooms": rooms, "other": 42},
                "actions": [{"unchanged": "original startup actions"}]}
    revised = patch.apply(original)
    expected = copy.deepcopy(original)
    expected["variables"]["rooms"].pop(1)
    assert revised == expected
    assert len(original["variables"]["rooms"]) == 5
    original["variables"]["rooms"].reverse()
    with pytest.raises(jsonpatch.JsonPatchTestFailed):
        patch.apply(original)
