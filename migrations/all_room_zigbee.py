"""Build an offline, fail-closed migration for the four remaining HVAC rooms.

Existing temperature sources, targets, thresholds, helpers, timers and entity IDs
are retained. The nursery controller and all unrelated automations are untouched.
The returned helper definitions are created through HA's storage helper API, not
by reloading input_text (which would reset the nursery's YAML journal).
"""
from copy import deepcopy
import json

ROOMS = {
    "study": {"id": "1776159967286", "controller": "automation.hvac_toggle_thermostat",
              "listener": "1787025332042", "old": "switch.adp_cn_976148245_adgw_switch_a_p_2_2"},
    "master_bedroom": {"id": "1776167491572", "controller": "automation.master_bedroom_hvac_automation",
                       "listener": "1787025332046", "old": "switch.adp_cn_976148245_adgw_switch_c_p_2_10"},
    "dining_room": {"id": "1778380443908", "controller": "automation.dining_room_hvac_automation",
                    "listener": "1787025332048", "old": "switch.adp_cn_975356665_adgw_switch_a_p_2_2"},
    "living_room": {"id": "1780020719265", "controller": "automation.living_room_hvac_automation",
                    "listener": "1787025332050", "old": "switch.adp_cn_975356665_adgw_switch_b_p_2_6"},
}
LEGACY_PREFIXES = ("adp_cn_976148245", "adp_cn_975356665")
LIFECYCLE = "sensor.hvac_command_lifecycle"
KEEP = ("temp_sensor", "target_temp", "cooling_active", "temp_at_toggle",
        "cooldown_timer", "off_cooldown_timer", "hvac_mode_select", "upper_offset", "lower_offset")


def retire(config, reason):
    """Retain registry identity but make obsolete writers inert, even if enabled."""
    return {"id": config["id"], "alias": config["alias"], "description": reason,
            "initial_state": False, "triggers": [], "conditions": [{"condition": "template", "value_template": "{{ false }}"}],
            "actions": [], "mode": "single"}


def build_controllers(automations):
    """Return (automations, helper_creations, lifecycle_package), without mutation.

    helper_creations: list of {domain, id, config}; config has the exact fields
    for input_text/create or input_number/create. Existing IDs must be checked
    before creating, and conflicting helpers must never be overwritten.
    """
    result = deepcopy(automations)
    ids = [a.get("id") for a in result]
    required = [r[k] for r in ROOMS.values() for k in ("id", "listener")] + ["1788478600000"]
    for ident in required:
        if ids.count(ident) != 1:
            raise ValueError(f"Expected one automation {ident}")
    by_id = {a["id"]: a for a in result}
    replacements = {}
    helpers = []
    for room, info in ROOMS.items():
        old = by_id[info["id"]]
        bp = old.get("use_blueprint", {})
        inp = bp.get("input", {})
        if bp.get("path") != "panghy/hvac_toggle_thermostat.yaml" or inp.get("gateway_switch") != info["old"]:
            raise ValueError(f"Unexpected actuator/controller for {room}")
        if inp.get("post_on_cooldown_minutes", 10) != 5:
            raise ValueError(f"Unexpected minimum ON interval for {room}")
        interval = inp.get("cooldown_minutes", 10)
        if not isinstance(interval, (float, int)) or not 5 <= interval <= 13:
            raise ValueError(f"Unsupported normal OFF interval for {room}")
        if not all(k in inp for k in KEEP):
            raise ValueError(f"Incomplete existing controller for {room}")
        name = room.replace("_", " ").title()
        chosen = {k: deepcopy(inp[k]) for k in KEEP}
        chosen.update(controller_entity=info["controller"], lifecycle_marker=LIFECYCLE,
                      raw_temp_sensor=inp["temp_sensor"], command_transport="zha_click",
                      gateway_switch=f"switch.{room}_hvac_toggle",
                      fingerbot_mode=f"select.{room}_hvac_toggle_mode",
                      command_record=f"input_text.{room}_hvac_command",
                      temp_at_off_press=f"input_number.{room}_hvac_temp_at_off_press",
                      zha_min_off_minutes=interval)
        new = {k: deepcopy(v) for k, v in old.items() if k not in ("use_blueprint", "mode", "max", "max_exceeded", "triggers", "trigger", "conditions", "condition", "actions", "action")}
        new["initial_state"] = True
        new["description"] = (old.get("description", "") + "\n2026-10-12: Zigbee CLICK migration; one toggle per command. Existing thermal policy and normal minimum OFF interval retained; uncertain commands wait 13 minutes. The effective temperature sensors retain their existing source/fallback availability policies. Zigbee acknowledgement is not physical AC-state confirmation.").strip()
        new["use_blueprint"] = {"path": "panghy/hvac_recovering_thermostat.yaml", "input": chosen}
        replacements[info["id"]] = new
        listener = by_id[info["listener"]]
        if listener.get("use_blueprint", {}).get("input", {}).get("gateway_switch") != info["old"]:
            raise ValueError(f"Unexpected listener for {room}")
        replacements[info["listener"]] = retire(listener, "Retired after Zigbee migration. The recovering thermostat owns command acknowledgement and cooling estimates.")
        helpers.extend([
            {"domain": "input_text", "id": f"{room}_hvac_command", "config": {"name": f"{name} HVAC Command", "max": 255}},
            {"domain": "input_number", "id": f"{room}_hvac_temp_at_off_press", "config": {"name": f"{name} HVAC Temp At Off Press", "min": -20, "max": 99, "step": 0.1, "mode": "box", "unit_of_measurement": "°C"}},
        ])
    startup = by_id["1788478600000"]
    if {r["key"] for r in startup.get("variables", {}).get("rooms", [])} != set(ROOMS):
        raise ValueError("Unexpected startup resync rooms")
    replacements[startup["id"]] = retire(startup, "Retired after all rooms migrated to the recovering controller, which owns startup reconciliation and power commands.")
    result = [replacements.get(a["id"], a) for a in result]
    lifecycle = {"template": [{"triggers": [{"trigger": "event", "event_type": ["homeassistant_started", "automation_reloaded"]}],
                               "sensor": [{"name": "HVAC Command Lifecycle", "unique_id": "hvac_command_lifecycle",
                                           "state": "{% set sequence = this.state.split(':')[-1] | int(0) %}{{ trigger.event.context.id ~ ':' ~ (sequence + 1) }}"}]}]}
    return result, helpers, lifecycle


def assert_no_active_ble(automations):
    """Fail deployment if a still-active automation retains an old gateway."""
    for item in automations:
        if item.get("initial_state") is False:
            continue
        if any(p in json.dumps(item) for p in LEGACY_PREFIXES):
            raise ValueError(f"Unmigrated active BLE reference: {item.get('id')}")
