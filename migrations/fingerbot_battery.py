"""Offline, fail-closed migration of the 2026-10-12 Fingerbot battery snapshot."""
from copy import deepcopy
import hashlib
import json

# Entry identities preserve HA's existing canonical sensor entity registrations.
ROOMS = {
    "study": ("01M09Q1PEBFWTN91D9PTAJAMGT", "Study"),
    "nursery": ("01M09Q1PR1DTDDGKXY713E9T1N", "Nursery"),
    "master_bedroom": ("01M09Q1PW8YZ7SJ53FTWJQ8K00", "Master Bedroom"),
    "dining_room": ("01M09Q1Q0GMT5N55PHM0QBJ0Z9", "Dining Room"),
    "living_room": ("01M09Q1Q7PZWQRQCZS2QZHCHMC", "Living Room"),
}
SOURCES = {
    room: f"sensor.{('er_tong_fang_' if room == 'nursery' else '')}{room}_hvac_toggle_battery"
    for room in ROOMS
}

# Fingerprints cover the entire three known battery automation configs, including
# mappings, recipients, thresholds and dedup. Unexpected baselines need review;
# this one-shot migration deliberately rejects already-migrated configurations.
BASELINE_DIGESTS = {'automation.fingerbot_battery_drop_alert': ('1787032000003',
                                             '90ebd721cabba56798db70855317232bf245d354793cec55168eb709b2c7553b'),
 'automation.fingerbot_battery_tracker': ('1787032000001',
                                          '14c4f1fbeae2fd01992cf30db193c33585ea141e046daf0924db2a52205f7606'),
 'automation.fingerbot_low_battery_alert': ('1787032000002',
                                            '9b0549a5d711e3aec42f2afd3d8ba6a29c1f5750f4f06ed9f67d3bd77514c405')}


def _digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_battery(entries, automations):
    """Return independent ``(entries, automations)`` copies; never call HA.

    ``entries`` is a list of UI template config-entry dictionaries with
    ``entry_id``, ``title``, ``options`` and ``data`` (as in template-configs-before).
    Additional unrelated entries/metadata are preserved. ``automations`` maps
    automation entity IDs to raw HA configuration dictionaries, NOT the
    ``{state, config}`` wrappers in live-hvac-before.json; callers unwrap those.
    The three battery automations and five known template entries must each
    occur exactly once and match the reviewed baseline, or ValueError is raised.
    Other automations are copied unchanged. No entities are renamed or created.
    Availability is stored in ``options.additional_options`` to match the
    supported HA options flow; submit options without name/template_type.

    Numeric readings are accepted only in [0, 100]. Missing/invalid source
    readings render unavailable, with no BLE helper or previous-value fallback.
    Age alone is not evidence a sleepy Zigbee battery reading is invalid: ZHA
    availability is authoritative; no speculative report-age cutoff is imposed.
    """
    if not isinstance(entries, list) or not isinstance(automations, dict):
        raise ValueError("Expected a config-entry list and raw automation mapping")
    ids = [entry.get("entry_id") for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(entries) or len(set(ids)) != len(ids):
        raise ValueError("Missing or duplicate template entry identities")
    if None in ids:
        raise ValueError("Missing template entry identity")
    for room, (entry_id, label) in ROOMS.items():
        matches = [entry for entry in entries if entry["entry_id"] == entry_id]
        expected = {
            "device_class": "battery", "name": f"{label} Fingerbot Battery",
            "state": "{{ states('input_number." + room + "_fingerbot_battery_raw') | int(0) }}",
            "state_class": "measurement", "template_type": "sensor", "unit_of_measurement": "%",
        }
        if len(matches) != 1 or matches[0].get("options") != expected or matches[0].get("title") != expected["name"] or matches[0].get("data") != {}:
            raise ValueError(f"Unexpected or missing {room} battery template baseline")
        if any(entry["entry_id"] != entry_id and (entry.get("title") == expected["name"] or entry.get("options", {}).get("name") == expected["name"]) for entry in entries):
            raise ValueError(f"Duplicate {room} battery template mapping")
    for entity, (automation_id, digest) in BASELINE_DIGESTS.items():
        config = automations.get(entity)
        if not isinstance(config, dict) or _digest(config) != digest:
            raise ValueError(f"Unexpected or missing battery automation baseline: {entity}")
        if sum(isinstance(c, dict) and c.get("id") == automation_id for c in automations.values()) != 1:
            raise ValueError(f"Duplicate battery automation id: {automation_id}")

    new_entries, new_automations = deepcopy(entries), deepcopy(automations)
    by_id = {entry["entry_id"]: entry for entry in new_entries}
    for room, (entry_id, _) in ROOMS.items():
        options = by_id[entry_id]["options"]
        prefix = "{% set value = states('" + SOURCES[room] + "') %}"
        valid = "is_number(value) and 0 <= value | float <= 100"
        options["additional_options"] = {"availability": prefix + "{{ " + valid + " }}"}
        options["state"] = prefix + "{{ value | float if " + valid + " else none }}"

    tracker = new_automations["automation.fingerbot_battery_tracker"]
    tracker.update(initial_state=False, triggers=[], conditions=[], actions=[{
        "stop": "Retired BLE battery tracker; canonical battery sensors now read ZHA."
    }])
    tracker["description"] = "Retired: all five canonical Fingerbot battery sensors read ZHA directly. No helper or power writes."
    for entity, message in {
        "automation.fingerbot_low_battery_alert": "{{ 'Fingerbots reporting battery levels under 30%: ' ~ low_msg ~ '. Check the device before replacing the battery; this model can misreport 1%.' }}",
        "automation.fingerbot_battery_drop_alert": "{{ room ~ ' Fingerbot reports battery level ' ~ level ~ '% (below 30%). Check the device before replacing the battery; this model can misreport 1%.' }}",
    }.items():
        for action in new_automations[entity]["actions"]:
            if action.get("action", "").startswith("notify."):
                action["data"]["title"] = "Fingerbot reports low battery"
                action["data"]["message"] = message
    return new_entries, new_automations
