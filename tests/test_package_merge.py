"""Exercise the package loader used by an actual YAML deployment."""
import copy

import pytest
from homeassistant.config import merge_packages_config
from homeassistant.components.script.config import SCRIPT_ENTITY_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util

from conftest import ROOT


@pytest.mark.asyncio
async def test_verification_script_loads_through_package_merge(rig):
    package = yaml_util.load_yaml(str(ROOT / "examples/nursery_guarded.yaml"))
    config = {"script": {}}
    await merge_packages_config(rig.hass, config, {"nursery_guarded": copy.deepcopy(package)})
    script = config["script"]["nursery_prepare_verified_belief"]
    validated = SCRIPT_ENTITY_SCHEMA(script)
    for field in ("physically_verified", "cooling"):
        assert "boolean" in validated["fields"][field]["selector"]
    assert await async_setup_component(rig.hass, "script", config)
    await rig.settle()
    assert rig.hass.services.has_service("script", "nursery_prepare_verified_belief")
    assert not rig.presses
