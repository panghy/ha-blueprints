"""Build reviewable files without HA access: python -m migrations.build_remaining_zigbee BASELINE OUTPUT."""
import argparse
import hashlib
import json
from pathlib import Path
import yaml
from migrations.all_room_zigbee import build_controllers, assert_no_active_ble
from migrations.fingerbot_battery import build_battery


def patch_automations(text, replacements):
    """Replace only changed top-level YAML blocks, retaining other source bytes."""
    node = yaml.compose(text)
    original = yaml.safe_load(text)
    if not isinstance(node, yaml.SequenceNode) or len(node.value) != len(original):
        raise ValueError("Expected a YAML automation sequence")
    ids = [a["id"] for a in original]
    if len(set(ids)) != len(ids) or not set(replacements).issubset(ids):
        raise ValueError("Unknown or duplicate automation IDs")
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines: offsets.append(offsets[-1] + len(line))
    spans = [(offsets[n.start_mark.line], offsets[node.value[i+1].start_mark.line] if i+1 < len(node.value) else len(text)) for i,n in enumerate(node.value)]
    result = text
    for item,(start,end) in reversed(list(zip(original,spans,strict=True))):
        if item["id"] in replacements:
            result = result[:start] + yaml.safe_dump([replacements[item["id"]]],allow_unicode=True,sort_keys=False,width=100) + result[end:]
    expected=[replacements.get(a["id"],a) for a in original]
    if yaml.safe_load(result) != expected:
        raise ValueError("Patched YAML failed semantic readback")
    return result


def build(baseline, output):
    baseline,output=Path(baseline),Path(output)
    automation_text=(baseline/"automations.yaml.before").read_text()
    original=yaml.safe_load(automation_text)
    migrated,helpers,lifecycle=build_controllers(original)
    live=json.loads((baseline/"live-hvac-before.json").read_text())
    entries=json.loads((baseline/"template-configs-before.json").read_text())
    battery_entries,battery_configs=build_battery(entries,{e:c["config"] for e,c in live.items()})
    battery_by_id={c["id"]:c for e,c in battery_configs.items() if e.startswith("automation.fingerbot_")}
    for a in original:
        if a["id"] in battery_by_id:
            source=next(c["config"] for c in live.values() if c["config"]["id"]==a["id"])
            if a!=source:raise ValueError("Battery baseline changed between snapshots")
    migrated=[battery_by_id.get(a["id"],a) for a in migrated]
    assert_no_active_ble(migrated)
    changed={a["id"]:a for old,a in zip(original,migrated,strict=True) if old!=a}
    configuration=(baseline/"configuration.yaml.before").read_text()
    anchor="    nursery_guarded: !include nursery_guarded.yaml"
    if configuration.count(anchor)!=1 or "zigbee_hvac_lifecycle" in configuration:
        raise ValueError("Unexpected package include baseline")
    configuration=configuration.replace(anchor,anchor+"\n    zigbee_hvac_lifecycle: !include zigbee_hvac_lifecycle.yaml")
    files={"automations.yaml":patch_automations(automation_text,changed),
           "configuration.yaml":configuration,
           "zigbee_hvac_lifecycle.yaml":yaml.safe_dump(lifecycle,allow_unicode=True,sort_keys=False),
           "helper-creations.json":json.dumps(helpers,indent=2)+"\n",
           "battery-entries.json":json.dumps(battery_entries,indent=2,ensure_ascii=False)+"\n",
           "automation-updates.json":json.dumps(changed,indent=2,ensure_ascii=False)+"\n"}
    output.mkdir(parents=True,exist_ok=True)
    for name,content in files.items():
        tmp=output/(name+".tmp");tmp.write_text(content);tmp.replace(output/name)
        if (output/name).read_text()!=content:raise OSError(f"Readback failed: {name}")
    digest=lambda b:hashlib.sha256(b).hexdigest()
    manifest={"changed_automation_ids":list(changed),"baseline":{name:digest((baseline/(name+".before")).read_bytes()) for name in ["automations.yaml","configuration.yaml","scripts.yaml","nursery_guarded.yaml"]},"candidate":{name:digest(content.encode()) for name,content in files.items()}}
    text=json.dumps(manifest,indent=2)+"\n";tmp=output/"manifest.json.tmp";tmp.write_text(text);tmp.replace(output/"manifest.json")
    assert json.loads((output/"manifest.json").read_text())==manifest
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("baseline");parser.add_argument("output")
    args=parser.parse_args();print(json.dumps(build(args.baseline,args.output),indent=2))
