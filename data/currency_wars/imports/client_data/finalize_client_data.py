"""Audit and freeze this public-client-data import without accessing the game."""
import collections
import datetime
import hashlib
import json
import math
from pathlib import Path

from download_client_data import ROOT, atomic_json, blob_sha
from normalize_client_data import KINDS


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def walk(value, path=''):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from walk(item, path + '.' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, path + '[' + str(index) + ']')


def reference_hashes(value, path=''):
    if isinstance(value, dict):
        if 'Hash' in value:
            yield path, str(value['Hash'])
        for key, item in value.items():
            yield from reference_hashes(item, path + '.' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from reference_hashes(item, path + '[' + str(index) + ']')


def semantic(value):
    """Compare facts without revision URLs, generation dates or download coverage."""
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items()
                if key not in {'source', 'cost_source', 'sources', 'parameter_source', 'config_reference',
                               'version', 'availability', 'release_pool_status', 'generated_at'}}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def main():
    main_manifest = read(ROOT / 'manifest.json')
    config_manifest = read(ROOT / 'config_manifest.json')
    current = read(ROOT / 'normalized_current.json')
    baseline = read(ROOT / 'normalized_baseline_4_4.json')
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    failures = []
    files_by_path = {row['relative_path']: row for row in main_manifest['files']}
    for row in config_manifest['files']:
        previous = files_by_path.get(row['relative_path'])
        if previous and previous['sha256'] != row['sha256']:
            failures.append('Conflicting source files: ' + row['relative_path'])
        files_by_path[row['relative_path']] = row
    main_manifest['files'] = sorted(files_by_path.values(), key=lambda row: row['relative_path'])
    main_manifest['download_errors'] = main_manifest.get('download_errors', []) + config_manifest['errors']
    for row in main_manifest['files']:
        raw = (ROOT / row['relative_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256'] or blob_sha(raw) != row['git_blob_sha1'] or len(raw) != row['bytes']:
            failures.append('Source checksum mismatch: ' + row['relative_path'])
    collection = {}
    for label in ('release_4_6', 'release_4_4'):
        inventory = read(ROOT / label / 'excel_tree_inventory.json')
        expected = {row['path']: row['sha'] for row in inventory['tree']
                    if row['type'] == 'blob' and row['path'].startswith('GridFight')}
        missing = [name for name, sha in expected.items()
                   if files_by_path.get(label + '/ExcelOutput/' + name, {}).get('git_blob_sha1') != sha]
        collection[label] = {'scope': 'Every GridFight-prefixed ExcelOutput blob in the complete pinned repository tree',
                             'source_tree_sha': inventory['tree_sha'], 'inventory_truncated': inventory['truncated'],
                             'expected_table_count': len(expected), 'downloaded_table_count': len(expected) - len(missing),
                             'missing_tables': missing, 'complete': not missing and not inventory['truncated']}
        if missing or inventory['truncated']:
            failures.append('Incomplete GridFight table inventory: ' + label)
    missing_hashes = set(current['unresolved_text_hashes'])
    contexts = collections.defaultdict(list)
    for path in (ROOT / 'release_4_6/ExcelOutput').glob('*.json'):
        if not path.name.startswith('GridFight') and path.stem not in {'MonsterSkillConfig', 'AvatarConfig', 'AvatarConfigLD'}:
            continue
        table = read(path)
        if not isinstance(table, list):
            continue
        for row in table:
            key = {field: row[field] for field in ('ID', 'RoleID', 'TraitID', 'SkillID', 'Level', 'Lv', 'Star') if field in row}
            for field, hash_value in reference_hashes(row):
                if hash_value in missing_hashes:
                    contexts[hash_value].append({'table': path.stem, 'field': field.lstrip('.'), 'row_key': key,
                                                 'hide_in_ui': row.get('HideInUI'), 'client_path': 'ExcelOutput/' + path.name,
                                                 'file_sha256': files_by_path['release_4_6/ExcelOutput/' + path.name]['sha256']})
    normalized_missing = collections.defaultdict(list)
    effects = []
    for path, value in walk(current):
        if 'text_hash' in value and 'text_status' in value:
            effects.append((path, value))
            if value['text_hash'] in missing_hashes:
                normalized_missing[value['text_hash']].append(path)
        if value.get('name_text_hash') in missing_hashes:
            normalized_missing[value['name_text_hash']].append(path + '.name')
    missing_report = []
    for hash_value in sorted(missing_hashes):
        refs = contexts[hash_value]
        tables = {ref['table'] for ref in refs}
        if 'MonsterSkillConfig' in tables:
            category = 'enemy_skill_text_not_in_published_CHS'
            impact = 'Unit names and raw skill IDs/parameters are retained. Some skill names/descriptions are unavailable; HideInUI is recorded per row, not assumed for every missing text.'
        elif 'GridFightTraitMazebuff' in tables:
            category = 'trait_buff_description_not_in_published_CHS'
            impact = 'The separate TraitLayer.PropertyDesc and raw binding/parameters are retained; they do not prove the missing buff description or executable formula.'
        elif 'GridFightItems' in tables:
            category = 'non_equipment_item_name_not_in_published_CHS'
            impact = 'Item 350108 is outside GridFightEquipment; no core equipment title is missing.'
        else:
            category = 'other_text_not_in_published_CHS'
            impact = 'Keep the exact hash and source references; do not supply guessed text.'
        missing_report.append({'text_hash': hash_value, 'category': category, 'impact': impact,
                               'normalized_paths': sorted(set(normalized_missing[hash_value])), 'source_references': refs})
    text_report = {'schema': 'currency-wars-client-text-audit/v1', 'revision': current['sources'][0]['revision'],
                   'version': current['version'], 'unique_missing_hashes': len(missing_report),
                   'categories': dict(collections.Counter(row['category'] for row in missing_report)), 'items': missing_report}
    atomic_json(ROOT / 'unresolved_text_report.json', text_report)
    missing_parameters = [{'path': path, 'text_hash': value['text_hash'], 'placeholders': value['unresolved_placeholders'],
                           'text_status': value['text_status']}
                          for path, value in effects if value['unresolved_placeholders']]
    # Unknown ModifySkillType templates are deliberately deferred, never bound by ID alone.
    unsafe_parameters = [row for row in missing_parameters if not row['path'].startswith('.modifier_description_catalog[')]
    if unsafe_parameters:
        failures.append('Unresolved parameters in typed core effects')
    for modifier in current['modifier_description_catalog']:
        if modifier['parameter_source']:
            expected_table = {'BESkill': 'GridFightBackBESkillConfig', 'ServantSkill': 'GridFightServantSkill'}[modifier['skill_type']]
            if modifier['parameter_source']['client_path'] != 'ExcelOutput/' + expected_table + '.json':
                failures.append('Wrong typed modifier source: ' + str(modifier['skill_id']))
            if modifier['parameter_source']['row_key'].get('SkillID') != modifier['skill_id']:
                failures.append('Wrong modifier SkillID binding')
            if modifier['effect_details']['unresolved_placeholders']:
                failures.append('Unresolved typed modifier parameters')
        elif modifier['effect_details']['parameters']:
            failures.append('Untyped modifier was assigned numeric parameters')
    for character in current['characters']:
        for star in character['star_effects']:
            for skill in star['front_skills']:
                if skill.get('modifier_descriptions'):
                    failures.append('A BESkill modifier is attached to a front skill')
            for skill in star['front_skills'] + star['back_skills']:
                if skill.get('unresolved'):
                    failures.append('Missing character star skill: ' + str(skill['id']))
    for kind in KINDS:
        rows = current[kind]
        if len({row['id'] for row in rows}) != len(rows):
            failures.append('Duplicate stable IDs: ' + kind)
        if any(not row['name'] or row['name'].startswith('未解析') for row in rows):
            failures.append('Missing core title: ' + kind)
        if any(row['version'] != '4.6.0' or row['source']['revision'] != current['sources'][0]['revision'] for row in rows):
            failures.append('Mixed release version in core records')
    characters = {row['id']: row for row in current['characters']}
    if characters['1213']['cost'] != 2:
        failures.append('Dan Heng Imbibitor Lunae actual shop price must be 2')
    if [characters[key]['cost'] for key in ('15061', '15062', '15063')] != [3, 4, 5]:
        failures.append('Silver Wolf LV.999 cost variants lost')
    natasha = characters['1105']['star_effects'][0]
    natasha_front = next(row for row in natasha['front_skills'] if row['id'] == 11050301)
    natasha_back = next(row for row in natasha['back_skills'] if row['id'] == 11050301)
    if '6%生命上限+120' not in natasha_front['effects'] or natasha_front.get('modifier_descriptions'):
        failures.append('Natasha front/back overlap regression')
    natasha_modifier = natasha_back['modifier_descriptions'][0]
    if natasha_modifier['effect_details']['parameters'] != [15, .12, 240, 1, 3, .2, 2, 5, .06, 120] or '12%生命上限+240' not in natasha_modifier['effect_details']['text']:
        failures.append('Natasha backend modifier parameter shift regression')
    mydei = characters['1404']['star_effects'][0]
    mydei_back = next(row for row in mydei['back_skills'] if row['id'] == 14041101)
    if '回复等同于生命上限20%' not in mydei_back['modifier_descriptions'][0]['effect_details']['text']:
        failures.append('Mydei backend heal expansion regression')
    total_stars = sum(len(row['star_effects']) for row in current['characters'])
    if total_stars != 266:
        failures.append('Character star row loss')
    random_camps = sum(row['pool_status']['random_generation_enabled'] for row in current['opponents'])
    if random_camps != 20:
        failures.append('Unexpected enabled camp count')
    recipes = sum(len(row['recipe_variants']) for row in current['equipment'])
    if recipes != 57:
        failures.append('Equipment craft row loss')
    empty_core = {kind: [{'id': row['id'], 'name': row['name'], 'status': row.get('effects_status', 'requires_review')}
                         for row in current[kind] if not str(row.get('effects', '')).strip()] for kind in KINDS}
    if any(empty_core[kind] for kind in KINDS if kind != 'opponents'):
        failures.append('Unexpected empty core effects')
    finite = True
    def finite_walk(value):
        nonlocal finite
        if isinstance(value, float) and not math.isfinite(value):
            finite = False
        elif isinstance(value, dict):
            for item in value.values(): finite_walk(item)
        elif isinstance(value, list):
            for item in value: finite_walk(item)
    finite_walk(current)
    if not finite:
        failures.append('Non-finite JSON numeric value')
    raw_diff = []
    old_files = {row['client_path']: row for row in main_manifest['files'] if row['relative_path'].startswith('release_4_4/ExcelOutput/GridFight')}
    new_files = {row['client_path']: row for row in main_manifest['files'] if row['relative_path'].startswith('release_4_6/ExcelOutput/GridFight')}
    for path in sorted(set(old_files) | set(new_files)):
        old, new = old_files.get(path), new_files.get(path)
        if not old or not new or old['sha256'] != new['sha256']:
            raw_diff.append({'client_path': path, 'old_sha256': old['sha256'] if old else None,
                             'new_sha256': new['sha256'] if new else None, 'old_count': old['record_count'] if old else None,
                             'new_count': new['record_count'] if new else None})
    changes = {}
    for kind in KINDS:
        old = {row['id']: row for row in baseline[kind]}; new = {row['id']: row for row in current[kind]}
        updated, raw_only = [], []
        for key in sorted(old.keys() & new.keys()):
            a, b = semantic(old[key]), semantic(new[key])
            fields = [field for field in sorted(set(a) | set(b)) if a.get(field) != b.get(field)]
            if fields:
                item = {'id': key, 'name': new[key]['name'], 'changed_fields': fields,
                        'cost_change': {'old': old[key].get('cost'), 'current': new[key].get('cost')} if 'cost' in fields else None}
                if fields == ['raw']:
                    old_raw, new_raw = a['raw'], b['raw']
                    item['raw_field_changes'] = [{'field': field, 'old': old_raw.get(field), 'current': new_raw.get(field),
                                                  'old_field_present': field in old_raw, 'current_field_present': field in new_raw}
                                                 for field in sorted(set(old_raw) | set(new_raw)) if old_raw.get(field) != new_raw.get(field) or (field in old_raw) != (field in new_raw)]
                    raw_only.append(item)
                else:
                    updated.append(item)
        changes[kind] = {'baseline_count': len(old), 'current_count': len(new),
                         'added': [{'id': key, 'name': new[key]['name']} for key in sorted(new.keys() - old.keys())],
                         'removed': [{'id': key, 'name': old[key]['name']} for key in sorted(old.keys() - new.keys())],
                         'changed': updated, 'raw_schema_or_raw_only_changes': raw_only}
    diff = {'schema': 'currency-wars-release-diff/v1', 'baseline': baseline['sources'][0], 'current': current['sources'][0],
            'comparison_policy': 'Exact stable IDs and semantic field equality. No fuzzy title join. Source URLs and local config download coverage do not count as game changes.',
            'changed_gridfight_raw_tables': raw_diff, 'categories': changes}
    atomic_json(ROOT / 'release_diff.json', diff)
    checks = {'all_downloaded_git_blobs_and_sha256_verified': not any('checksum' in row or 'Conflicting source' in row for row in failures),
              'all_153_gridfight_tables_collected_each_release': all(row['complete'] for row in collection.values()),
              'all_core_names_present': not any('core title' in row for row in failures),
              'stable_kind_ids_unique': not any('Duplicate' in row for row in failures),
              'dan_heng_imbibitor_lunae_shop_cost': characters['1213']['cost'],
              'silver_wolf_lv999_costs': [characters[key]['cost'] for key in ('15061', '15062', '15063')],
              'character_star_rows': total_stars, 'recipe_variants': recipes, 'enabled_random_camps': random_camps,
              'enemy_unit_count': len(current['enemy_units']), 'typed_modifier_parameter_join_count': sum(bool(row['parameter_source']) for row in current['modifier_description_catalog']),
              'untyped_modifier_deferred_count': sum(not row['parameter_source'] for row in current['modifier_description_catalog']),
              'unsafe_unresolved_parameter_count': len(unsafe_parameters), 'json_numbers_finite': finite}
    audit = {'schema': 'currency-wars-client-audit/v1', 'generated_at': timestamp, 'checks_passed': not failures,
             'checks': checks, 'failures': failures, 'coverage': current['coverage'], 'table_collection': collection,
             'source_file_count': len(main_manifest['files']), 'source_bytes': sum(row['bytes'] for row in main_manifest['files']),
             'unique_missing_text_hashes': len(missing_report), 'missing_text_categories': text_report['categories'],
             'empty_primary_effects': empty_core, 'effect_text_status_counts': dict(collections.Counter(value['text_status'] for _, value in effects)),
             'deferred_parameter_templates': missing_parameters,
             'dynamic_runtime_modifier_effect_count': sum(bool(value['dynamic_modifiers']) for _, value in effects),
             'unresolved_entity_reference_count': sum(bool(value['unresolved_entity_references']) for _, value in effects),
             'property_modifier_value_field_absent_count': sum(not value['value_explicitly_present'] for row in current['characters'] for star in row['star_effects'] for value in star['general_property_modifiers']),
             'card_pool_economy_status': current['economy_rules']['card_pool'],
             'shop_odds_effective_profile': current['shop_odds']['effective_profile'],
             'config_references': {'total': config_manifest['referenced_config_count'], 'published_downloaded': len(config_manifest['files']),
                                   'not_in_pinned_public_tree': len(config_manifest['missing_config_paths']),
                                   'scope': 'JsonPath, JsonOverrideConfig and TraitEffectJson from mode tables; enemy template JsonConfig. This is not an inventory of all AI or game engine dependencies.'},
             'simulation_supported': False,
             'simulation_limitations': ['618 explicitly referenced implementation files are absent from the pinned public repository.',
                                        '155 modifier descriptions require an explicit skill type; 31 lack it and are retained as templates.',
                                        'Published numerical parameters do not supply runtime state, stacking order, targeting, AI or final damage/interest formula semantics.',
                                        'Card-pool quantity and buy/sell weight constants exist, but finite per-role copy depletion, natural-pool sell refunds and active V2 profile remain unknown.',
                                        'Missing enemy/trait text is retained as exact unresolved references. No guessed descriptions replace it.'],
             'modifier_regression_examples': {'natasha_front_1star': natasha_front['effects'],
                                              'natasha_back_1star_modifier': natasha_modifier['effect_details'],
                                              'mydei_back_1star_modifier': mydei_back['modifier_descriptions'][0]['effect_details']}}
    atomic_json(ROOT / 'audit_report.json', audit)
    current['data_quality'] = {'static_table_collection_complete': all(row['complete'] for row in collection.values()),
                               'core_entity_names_complete': checks['all_core_names_present'], 'checks_passed': not failures,
                               'battle_simulation_supported': False, 'runtime_mechanics_computable': False,
                               'missing_text_hashes': len(missing_report), 'untyped_modifiers_deferred': checks['untyped_modifier_deferred_count'],
                               'audit_report': 'audit_report.json', 'unresolved_text_report': 'unresolved_text_report.json',
                               'config_manifest': 'config_manifest.json',
                               'source_note': 'Public extracted release-data repository, not a publisher-owned repository. OSPRODWin commit evidence pins the release; individual runtime panels were not inspected by this offline worker.'}
    atomic_json(ROOT / 'normalized_current.json', current)
    main_manifest.update({'generated_at': timestamp, 'table_collection': collection, 'config_inventory': {key: value for key, value in config_manifest.items() if key != 'files'},
        'normalized_coverage': current['coverage'], 'checks_passed': not failures,
        'source_kind': 'public_extracted_production_client_data', 'publisher_owned_repository': False,
        'field_mapping': {'characters': 'GridFightRoleBasicInfo.ID is the stable role variant ID; AvatarConfig + AvatarConfigLD AvatarName hashes -> CHS.',
                          'cost': 'GridFightRoleBasicInfo.Rarity -> GridFightShopPrice.Rarity -> BuyGoldStar1. Rarity is not the generic avatar star rarity.',
                          'star_effects': 'GridFightRoleStar.(ID,Star) -> FrontShowSkillIDList and BackShowSkillIDList -> their own skill tables and ParamList. Raw stats and modifiers remain separate.',
                          'modifier_descriptions': 'GridFightSkillDescMod ModifySkillType=BESkill -> GridFightBackBESkillConfig; ServantSkill -> GridFightServantSkill with every level. A missing type remains an unbound template.',
                          'bonds': 'TraitBasicInfo.ID -> TraitLayer.TraitID -> MazebuffID, plus exact TraitEffectList and remark joins.',
                          'equipment': 'Equipment.ID -> Items.ID; EquipDesc + ParamList. CraftConfig.CraftEquipID -> CostEquipList recipes. GeneralPropertyList retained without inferred final formulas.',
                          'investments': 'PortalBuff / Augment name and description hashes + EffectParamList; SeasonPortal / SeasonAugment registration and ModuleBan rows retained.',
                          'opponents': 'GridFightCamp.ID camp configuration; MonsterList exact unit membership; IfRandomEnabled is explicit random-generation status.',
                          'enemy_units': 'GridFightMonster.MonsterID -> MonsterConfig -> MonsterTemplateConfig and typed MonsterSkillConfig rows. Base stats, modifiers and elite scaling are stored separately.',
                          'enemy_affixes': 'AffixConfig.ID; AffixDesc + EffectParamList, RuleParamList, exact JsonPath.',
                          'economy': 'Published shop buy/sell tables, rarity weights, levels/XP/population and constants remain raw; no probability denominator or interest formula is assumed.'},
        'rebuild': {'runtime': 'Python 3, standard library only; network required only for downloads.',
                    'commands': ['python -B -X utf8 download_client_data.py', 'python -B -X utf8 download_client_configs.py',
                                 'python -B -X utf8 normalize_client_data.py', 'python -B -X utf8 finalize_client_data.py'],
                    'inventory_evidence': 'release_4_6/excel_tree_inventory.json and release_4_4/excel_tree_inventory.json are pinned non-truncated Git API tree snapshots.'}})
    artifacts = ('normalized_current.json', 'normalized_baseline_4_4.json', 'release_diff.json', 'unresolved_text_report.json', 'audit_report.json', 'README.md',
                 'download_client_data.py', 'download_client_configs.py', 'normalize_client_data.py', 'finalize_client_data.py')
    main_manifest['derived_artifacts'] = [{'relative_path': name, 'sha256': hashlib.sha256((ROOT / name).read_bytes()).hexdigest(),
                                          'bytes': (ROOT / name).stat().st_size} for name in artifacts]
    atomic_json(ROOT / 'manifest.json', main_manifest)
    print(json.dumps({'checks_passed': not failures, 'failures': failures, 'source_file_count': len(main_manifest['files']),
                      'current_sha256': main_manifest['derived_artifacts'][0]['sha256'], 'missing_text_categories': text_report['categories'],
                      'typed_modifier_bindings': checks['typed_modifier_parameter_join_count'],
                      'deferred_modifiers': checks['untyped_modifier_deferred_count'],
                      'changed_gridfight_tables': len(raw_diff)}, ensure_ascii=False), flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
