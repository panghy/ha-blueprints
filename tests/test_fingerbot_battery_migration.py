"""Frozen 2026-10-12 evidence, exercised entirely in isolated Home Assistant."""
from copy import deepcopy
import pytest
from homeassistant.helpers.template import Template
from homeassistant.components.template.sensor import SENSOR_CONFIG_ENTRY_SCHEMA
from homeassistant.setup import async_setup_component
from migrations.fingerbot_battery import build_battery, ROOMS, SOURCES

ENTRIES = [{'data': {},
  'entry_id': '01M09Q1PEBFWTN91D9PTAJAMGT',
  'options': {'device_class': 'battery',
              'name': 'Study Fingerbot Battery',
              'state': "{{ states('input_number.study_fingerbot_battery_raw') | int(0) }}",
              'state_class': 'measurement',
              'template_type': 'sensor',
              'unit_of_measurement': '%'},
  'title': 'Study Fingerbot Battery'},
 {'data': {},
  'entry_id': '01M09Q1PR1DTDDGKXY713E9T1N',
  'options': {'device_class': 'battery',
              'name': 'Nursery Fingerbot Battery',
              'state': "{{ states('input_number.nursery_fingerbot_battery_raw') | int(0) }}",
              'state_class': 'measurement',
              'template_type': 'sensor',
              'unit_of_measurement': '%'},
  'title': 'Nursery Fingerbot Battery'},
 {'data': {},
  'entry_id': '01M09Q1PW8YZ7SJ53FTWJQ8K00',
  'options': {'device_class': 'battery',
              'name': 'Master Bedroom Fingerbot Battery',
              'state': "{{ states('input_number.master_bedroom_fingerbot_battery_raw') | int(0) }}",
              'state_class': 'measurement',
              'template_type': 'sensor',
              'unit_of_measurement': '%'},
  'title': 'Master Bedroom Fingerbot Battery'},
 {'data': {},
  'entry_id': '01M09Q1Q0GMT5N55PHM0QBJ0Z9',
  'options': {'device_class': 'battery',
              'name': 'Dining Room Fingerbot Battery',
              'state': "{{ states('input_number.dining_room_fingerbot_battery_raw') | int(0) }}",
              'state_class': 'measurement',
              'template_type': 'sensor',
              'unit_of_measurement': '%'},
  'title': 'Dining Room Fingerbot Battery'},
 {'data': {},
  'entry_id': '01M09Q1Q7PZWQRQCZS2QZHCHMC',
  'options': {'device_class': 'battery',
              'name': 'Living Room Fingerbot Battery',
              'state': "{{ states('input_number.living_room_fingerbot_battery_raw') | int(0) }}",
              'state_class': 'measurement',
              'template_type': 'sensor',
              'unit_of_measurement': '%'},
  'title': 'Living Room Fingerbot Battery'}]
AUTOMATIONS = {'automation.fingerbot_battery_drop_alert': {'actions': [{'action': 'input_datetime.set_datetime',
                                                          'data': {'datetime': "{{ now().strftime('%Y-%m-%d "
                                                                               "%H:%M:%S') }}"},
                                                          'target': {'entity_id': '{{ gate }}'}},
                                                         {'action': 'notify.mobile_app_clements_iphone',
                                                          'data': {'message': "{{ room ~ ' Fingerbot battery "
                                                                              "dropped to ' ~ level ~ '% "
                                                                              "(below 30%). Replace soon.' "
                                                                              '}}',
                                                                   'title': 'Fingerbot battery low'}},
                                                         {'action': 'notify.mobile_app_grid',
                                                          'data': {'message': "{{ room ~ ' Fingerbot battery "
                                                                              "dropped to ' ~ level ~ '% "
                                                                              "(below 30%). Replace soon.' "
                                                                              '}}',
                                                                   'title': 'Fingerbot battery low'}}],
                                             'alias': 'Fingerbot Battery Drop Alert',
                                             'conditions': [{'condition': 'template',
                                                             'value_template': '{{ states(gate) in '
                                                                               "['unknown', 'unavailable'] "
                                                                               'or (now().timestamp() - '
                                                                               'as_timestamp(states(gate), '
                                                                               '0)) > 86400 }}'}],
                                             'description': 'Immediate alert the moment any of the five '
                                                            'sensor.*_fingerbot_battery levels crosses below '
                                                            '30% (numeric_state trigger), so a low battery '
                                                            'is caught at the reading that reveals it '
                                                            'instead of waiting for the daily 09:30 check '
                                                            '(added 2026-09-12 after the Nursery bot hit 22% '
                                                            'on 2026-09-11 with BLE link flapping before the '
                                                            'daily 20% check fired). Per-room dedup: at most '
                                                            'one push per room per 24h, gated by '
                                                            'input_datetime.<room>_fingerbot_battery_alerted '
                                                            'helpers stamped on each send. Sends to both '
                                                            "Clement's and Ingrid's phones (same notify "
                                                            'services as the daily alert). No TTS.',
                                             'id': '1787032000003',
                                             'max': 5,
                                             'mode': 'queued',
                                             'triggers': [{'below': 30,
                                                           'entity_id': ['sensor.study_fingerbot_battery',
                                                                         'sensor.nursery_fingerbot_battery',
                                                                         'sensor.master_bedroom_fingerbot_battery',
                                                                         'sensor.dining_room_fingerbot_battery',
                                                                         'sensor.living_room_fingerbot_battery'],
                                                           'trigger': 'numeric_state'}],
                                             'variables': {'gate': "{{ 'input_datetime.' ~ "
                                                                   "trigger.entity_id.split('.')[1] ~ "
                                                                   "'_alerted' }}",
                                                           'level': '{{ trigger.to_state.state }}',
                                                           'room': '{{ rooms[trigger.entity_id] }}',
                                                           'rooms': {'sensor.dining_room_fingerbot_battery': 'Dining '
                                                                                                             'Room',
                                                                     'sensor.living_room_fingerbot_battery': 'Living '
                                                                                                             'Room',
                                                                     'sensor.master_bedroom_fingerbot_battery': 'Master '
                                                                                                                'Bedroom',
                                                                     'sensor.nursery_fingerbot_battery': 'Nursery',
                                                                     'sensor.study_fingerbot_battery': 'Study'}}},
 'automation.fingerbot_battery_tracker': {'actions': [{'action': 'input_number.set_value',
                                                       'data': {'value': '{{ trigger.to_state.state[8:10] | '
                                                                         'int(0, 16) }}'},
                                                       'target': {'entity_id': 'input_number.{{ trigger.id '
                                                                               '}}_fingerbot_battery_raw'}}],
                                          'alias': 'Fingerbot Battery Tracker',
                                          'conditions': [{'condition': 'template',
                                                          'value_template': '{{ trigger.to_state is not none '
                                                                            'and trigger.to_state.state is '
                                                                            'string and '
                                                                            'trigger.to_state.state[:4] == '
                                                                            "'0500' and "
                                                                            'trigger.to_state.state | length '
                                                                            '>= 10 and '
                                                                            '(trigger.to_state.state[8:10] | '
                                                                            'int(-1, 16)) >= 0 and '
                                                                            '(trigger.to_state.state[8:10] | '
                                                                            'int(-1, 16)) <= 100 }}'}],
                                          'description': 'Decodes battery % (hex chars 8-9) from genuine '
                                                         '0500 press-feedback blips on each gateway slot '
                                                         "status sensor and stores it in the room's "
                                                         'input_number.*_fingerbot_battery_raw helper (which '
                                                         'feeds the sensor.*_fingerbot_battery template '
                                                         'sensors). Idle 00/01 values and non-0500 payloads '
                                                         'are ignored.',
                                          'id': '1787032000001',
                                          'max': 10,
                                          'mode': 'queued',
                                          'triggers': [{'entity_id': 'sensor.adp_cn_976148245_adgw_status_a_p_2_3',
                                                        'id': 'study',
                                                        'trigger': 'state'},
                                                       {'entity_id': 'sensor.adp_cn_976148245_adgw_status_b_p_2_7',
                                                        'id': 'nursery',
                                                        'trigger': 'state'},
                                                       {'entity_id': 'sensor.adp_cn_976148245_adgw_status_c_p_2_11',
                                                        'id': 'master_bedroom',
                                                        'trigger': 'state'},
                                                       {'entity_id': 'sensor.adp_cn_975356665_adgw_status_a_p_2_3',
                                                        'id': 'dining_room',
                                                        'trigger': 'state'},
                                                       {'entity_id': 'sensor.adp_cn_975356665_adgw_status_b_p_2_7',
                                                        'id': 'living_room',
                                                        'trigger': 'state'}]},
 'automation.fingerbot_low_battery_alert': {'actions': [{'action': 'notify.mobile_app_clements_iphone',
                                                         'data': {'message': "{{ 'Fingerbot batteries under "
                                                                             "30%: ' ~ low_msg ~ '. Replace "
                                                                             "soon.' }}",
                                                                  'title': 'Fingerbot battery low'}},
                                                        {'action': 'notify.mobile_app_grid',
                                                         'data': {'message': "{{ 'Fingerbot batteries under "
                                                                             "30%: ' ~ low_msg ~ '. Replace "
                                                                             "soon.' }}",
                                                                  'title': 'Fingerbot battery low'}}],
                                            'alias': 'Fingerbot Low Battery Alert',
                                            'conditions': [{'condition': 'template',
                                                            'value_template': '{{ low_msg | length > 0 }}'}],
                                            'description': 'Daily 09:30 HKT check of the five '
                                                           'sensor.*_fingerbot_battery levels. If any is '
                                                           'under 30%, sends ONE combined message naming the '
                                                           "low room(s) and level(s) to both Clement's and "
                                                           "Ingrid's phones (same verified notify services "
                                                           'as the nursery temperature alerts). At most one '
                                                           'message per day; re-alerts daily while any bot '
                                                           'stays low. Threshold raised 20% -> 30% on '
                                                           '2026-09-12 after the Nursery bot hit 22% with '
                                                           'BLE link flapping before the 20% check could '
                                                           'fire.',
                                            'id': '1787032000002',
                                            'mode': 'single',
                                            'triggers': [{'at': '09:30:00', 'trigger': 'time'}],
                                            'variables': {'low_msg': '{% set ns = namespace(items=[]) %}{% '
                                                                     'for name, eid in rooms.items() %}{% if '
                                                                     'states(eid) | int(101) < 30 %}{% set '
                                                                     "ns.items = ns.items + [name ~ ' at ' ~ "
                                                                     "states(eid) ~ '%'] %}{% endif %}{% "
                                                                     "endfor %}{{ ns.items | join(', ') }}",
                                                          'rooms': {'Dining Room': 'sensor.dining_room_fingerbot_battery',
                                                                    'Living Room': 'sensor.living_room_fingerbot_battery',
                                                                    'Master Bedroom': 'sensor.master_bedroom_fingerbot_battery',
                                                                    'Nursery': 'sensor.nursery_fingerbot_battery',
                                                                    'Study': 'sensor.study_fingerbot_battery'}}}}


def test_only_intended_fields_change_and_copies_are_independent():
    entries, automations = deepcopy(ENTRIES), deepcopy(AUTOMATIONS)
    entries.append({'entry_id': 'unrelated', 'options': {'state': 'untouched'}, 'data': {}})
    automations['automation.unrelated'] = {'id': 'other', 'actions': []}
    before = deepcopy((entries, automations))
    new_entries, new_automations = build_battery(entries, automations)
    assert (entries, automations) == before
    restored = deepcopy(new_entries)
    for old, new in zip(entries[:5], restored[:5]):
        new['options']['state'] = old['options']['state']
        del new['options']['additional_options']
    assert restored == entries
    for entity in ('automation.fingerbot_low_battery_alert', 'automation.fingerbot_battery_drop_alert'):
        restored = deepcopy(new_automations[entity])
        for old, new in zip(automations[entity]['actions'], restored['actions']):
            if old['action'].startswith('notify.'):
                assert 'reports' in new['data']['title']
                assert 'misreport 1%' in new['data']['message']
                new['data'] = old['data']
        assert restored == automations[entity]
    assert new_automations['automation.unrelated'] == automations['automation.unrelated']
    new_entries[-1]['options']['state'] = 'changed'
    new_automations['automation.unrelated']['actions'].append({})
    assert (entries, automations) == before


@pytest.mark.parametrize('mutation', ['missing_entry', 'duplicate_entry', 'wrong_source', 'wrong_name',
    'duplicate_name', 'missing_tracker', 'wrong_id', 'wrong_recipient', 'wrong_threshold', 'duplicate_id'])
def test_unexpected_baselines_fail_closed(mutation):
    entries, automations = deepcopy(ENTRIES), deepcopy(AUTOMATIONS)
    if mutation == 'missing_entry':
        entries.pop()
    elif mutation == 'duplicate_entry':
        entries.append(deepcopy(entries[0]))
    elif mutation == 'wrong_source':
        entries[0]['options']['state'] = entries[1]['options']['state']
    elif mutation == 'wrong_name':
        entries[0]['title'] = 'Wrong room'
    elif mutation == 'duplicate_name':
        entries.append(dict(deepcopy(entries[0]), entry_id='extra'))
    elif mutation == 'missing_tracker':
        del automations['automation.fingerbot_battery_tracker']
    elif mutation == 'wrong_id':
        automations['automation.fingerbot_battery_tracker']['id'] = 'wrong'
    elif mutation == 'wrong_recipient':
        automations['automation.fingerbot_low_battery_alert']['actions'][0]['action'] = 'notify.someone_else'
    elif mutation == 'wrong_threshold':
        automations['automation.fingerbot_battery_drop_alert']['triggers'][0]['below'] = 20
    else:
        automations['automation.duplicate'] = deepcopy(automations['automation.fingerbot_battery_tracker'])
    before = deepcopy((entries, automations))
    with pytest.raises(ValueError):
        build_battery(entries, automations)
    assert (entries, automations) == before


async def test_real_ha_templates_invalid_values_never_use_stale_ble_or_previous_state(rig):
    entries, _ = build_battery(ENTRIES, AUTOMATIONS)
    for room, entry in zip(ROOMS, entries):
        options = entry['options']
        schema_options = {k: v for k, v in options.items()
                          if k not in ('template_type', 'additional_options')}
        schema_options.update(options['additional_options'])
        SENSOR_CONFIG_ENTRY_SCHEMA(schema_options)
        availability = Template(options['additional_options']['availability'], rig.hass)
        state = Template(options['state'], rig.hass)
        await rig.set(f'input_number.{room}_fingerbot_battery_raw', 88)
        await rig.set(f'sensor.{room}_fingerbot_battery', 77)
        # Missing source must not use either the stored BLE helper or last value.
        assert availability.async_render() is False
        assert state.async_render() is None
        for value in ('0', '1', '29.5', '30', '100'):
            await rig.set(SOURCES[room], value)
            assert availability.async_render() is True
            assert state.async_render() == float(value)
        for value in ('unknown', 'unavailable', '', 'nonsense', '-1', '101', 'nan', 'inf', '-inf'):
            await rig.set(SOURCES[room], value)
            assert availability.async_render() is False
            assert state.async_render() is None
        # HA tracks only this room's ZHA sensor, so BLE updates cannot revive it.
        info = state.async_render_to_info()
        assert info.entities == {SOURCES[room]}
        await rig.set(f'input_number.{room}_fingerbot_battery_raw', 1)
        assert state.async_render() is None


async def test_retired_tracker_has_no_effect_even_manually_enabled_and_triggered(rig):
    _, automations = build_battery(ENTRIES, AUTOMATIONS)
    tracker = automations['automation.fingerbot_battery_tracker']
    config = {'automation': [tracker]}
    assert await async_setup_component(rig.hass, 'automation', config)
    await rig.hass.async_start()
    await rig.settle()
    entity = 'automation.fingerbot_battery_tracker'
    assert rig.hass.states.get(entity).state == 'off'
    await rig.call('automation', 'turn_on', {'entity_id': entity})
    await rig.call('automation', 'trigger', {'entity_id': entity, 'skip_condition': True})
    for room in ROOMS:
        assert rig.hass.states.get(f'input_number.{room}_fingerbot_battery_raw') is None
    assert not rig.presses
    assert not rig.alerts


async def test_alert_install_does_not_send_and_unavailable_is_not_low(rig):
    _, automations = build_battery(ENTRIES, AUTOMATIONS)
    sent = []
    async def notify(call):
        sent.append(dict(call.data))
    for service in ('mobile_app_clements_iphone', 'mobile_app_grid'):
        rig.hass.services.async_register('notify', service, notify)
    for room in ROOMS:
        await rig.set(f'sensor.{room}_fingerbot_battery', 'unavailable')
    alerts = [v for k, v in automations.items() if k != 'automation.fingerbot_battery_tracker']
    assert await async_setup_component(rig.hass, 'automation', {'automation': alerts})
    await rig.hass.async_start()
    await rig.settle()
    assert not sent
    daily = automations['automation.fingerbot_low_battery_alert']
    assert Template(daily['variables']['low_msg'], rig.hass).async_render({'rooms': daily['variables']['rooms']}) == ''
    await rig.call('automation', 'trigger', {'entity_id': 'automation.fingerbot_low_battery_alert', 'skip_condition': False})
    assert not sent


async def test_ui_config_entries_produce_unavailable_entities_and_recover(rig):
    from types import MappingProxyType
    from homeassistant.config_entries import ConfigEntry

    entries, _ = build_battery(ENTRIES, AUTOMATIONS)
    for entry in entries:
        config_entry = ConfigEntry(
            **entry, domain='template', source='user', version=1, minor_version=1,
            unique_id=None, discovery_keys=MappingProxyType({}), subentries_data=None,
        )
        await rig.hass.config_entries.async_add(config_entry)
        assert config_entry.state.value == "loaded"
    await rig.hass.async_start()
    await rig.settle()
    for room, source in SOURCES.items():
        canonical = f'sensor.{room}_fingerbot_battery'
        assert rig.hass.states.get(canonical).state == 'unavailable'
        await rig.set(source, 64)
        assert float(rig.hass.states.get(canonical).state) == 64
        for invalid in ('unavailable', 'unknown', 'invalid', '-1', '101', 'nan'):
            await rig.set(source, invalid)
            assert rig.hass.states.get(canonical).state == 'unavailable'
            await rig.set(f'input_number.{room}_fingerbot_battery_raw', 90)
            assert rig.hass.states.get(canonical).state == 'unavailable'
        await rig.set(source, 0)
        assert float(rig.hass.states.get(canonical).state) == 0
        await rig.set(source, 32.5)
        assert float(rig.hass.states.get(canonical).state) == 32.5


async def test_supported_options_flow_serializes_candidate_exactly(rig):
    import json
    from types import MappingProxyType
    from homeassistant.config_entries import ConfigEntry

    candidates, _ = build_battery(ENTRIES, AUTOMATIONS)
    for baseline, candidate in zip(ENTRIES, candidates):
        config_entry = ConfigEntry(
            **deepcopy(baseline), domain='template', source='user', version=2, minor_version=1,
            unique_id=None, discovery_keys=MappingProxyType({}), subentries_data=None,
        )
        await rig.hass.config_entries.async_add(config_entry)
        flow = await rig.hass.config_entries.options.async_init(config_entry.entry_id)
        assert flow['type'] == 'form'
        assert flow['step_id'] == 'sensor'
        payload = {key: value for key, value in candidate['options'].items()
                   if key not in ('name', 'template_type')}
        # Validate the exact form submission as the frontend does, before saving.
        payload = flow['data_schema'](json.loads(json.dumps(payload)))
        result = await rig.hass.config_entries.options.async_configure(flow['flow_id'], payload)
        assert result['type'] == 'create_entry'
        await rig.settle()
        saved = json.loads(json.dumps(dict(config_entry.options)))
        assert saved == candidate['options']
        assert 'availability' not in saved
        assert 'availability' in saved['additional_options']
