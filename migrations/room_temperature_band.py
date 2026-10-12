"""Match the four migrated rooms to the reviewed nursery temperature offsets.

This changes temperature thresholds only. Targets, timing protections, transport,
helpers, lifecycle, automations outside the four rooms and the reference are kept.
"""
from copy import deepcopy
import math

from migrations.all_room_zigbee import ROOMS

BLUEPRINT = "panghy/hvac_recovering_thermostat.yaml"
NURSERY_BAND = {"upper_offset": 0.4, "lower_offset": -0.1}


def build_bands(automations, nursery):
    """Return a copy with only the four rooms' upper/lower offsets changed.

    Require the deployed nursery reference and each room's expected Zigbee
    identity. Unexpected or incomplete sources must be reviewed before writing.
    This deliberately does not copy the nursery's shorter normal OFF interval.
    """
    reference = nursery.get("use_blueprint", {})
    ref = reference.get("input", {})
    if (reference.get("path") != BLUEPRINT
            or ref.get("controller_entity") != "automation.nursery_guarded_thermostat"
            or ref.get("command_transport") != "zha_click"
            or any(type(ref.get(k)) not in (int, float) or ref[k] != v
                   for k, v in NURSERY_BAND.items())):
        raise ValueError("Unexpected nursery temperature-band reference")
    result = deepcopy(automations)
    for room, identity in ROOMS.items():
        matches = [a for a in result if a.get("id") == identity["id"]]
        if len(matches) != 1:
            raise ValueError(f"Expected one controller for {room}")
        bp = matches[0].get("use_blueprint", {})
        inputs = bp.get("input", {})
        required = {"controller_entity": identity["controller"],
                    "command_transport": "zha_click",
                    "gateway_switch": f"switch.{room}_hvac_toggle",
                    "fingerbot_mode": f"select.{room}_hvac_toggle_mode"}
        if (bp.get("path") != BLUEPRINT
                or any(inputs.get(k) != v for k, v in required.items())
                or any(type(inputs.get(k)) not in (int, float)
                       or not math.isfinite(inputs[k]) for k in NURSERY_BAND)):
            raise ValueError(f"Unexpected Zigbee controller for {room}")
        inputs.update(NURSERY_BAND)
    return result
