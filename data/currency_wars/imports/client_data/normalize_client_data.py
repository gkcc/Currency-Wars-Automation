"""Normalize pinned release tables into ROOT's offline database envelope."""
import collections
import datetime
import hashlib
import html
import json
from pathlib import Path
import re

from download_client_data import ROOT, REPOSITORY, atomic_json

KINDS = ('characters', 'bonds', 'equipment', 'investment_environments',
         'investment_strategies', 'opponents', 'enemy_units', 'enemy_affixes')


def scalar(value):
    if isinstance(value, dict):
        for key in ('Value', 'IntValue', 'DoubleValue', 'StringValue', 'BoolValue'):
            if key in value:
                return scalar(value[key])
        if 'ArrayValue' in value:
            return [scalar(item) for item in value['ArrayValue']]
    return value


class Release:
    def __init__(self, label):
        self.label = label
        self.directory = ROOT / label
        self.release = json.loads((self.directory / 'source_revision.json').read_text(encoding='utf-8'))
        self.version, self.revision = self.release['version'], self.release['revision']
        self.tables, self.hashes, self.unresolved = {}, {}, []
        self.property_labels = {}
        self.textmap = {}
        for filename in ('TextMapCHS.json', 'TextMapMainCHS.json'):
            self.textmap.update(json.loads((self.directory / 'TextMap' / filename).read_text(encoding='utf-8')))
        for row in self.table('GridFightRolePropertyConfig'):
            self.property_labels[row['PropertyType']] = self.clean(self.text(row.get('PropertyName')))
        self.avatars = self.index('AvatarConfig', 'AvatarID')
        self.avatars.update(self.index('AvatarConfigLD', 'AvatarID'))
        self.roles = self.index('GridFightRoleBasicInfo', 'ID')
        self.traits = self.index('GridFightTraitBasicInfo', 'ID')
        self.items = self.index('GridFightItems', 'ID')
        self.role_names = {key: self.clean(self.text(self.avatars.get(row['AvatarID'], {}).get('AvatarName')) or f'未解析角色[{key}]')
                           for key, row in self.roles.items()}
        self.trait_names = {key: self.clean(self.text(row['TraitName'])) for key, row in self.traits.items()}
        self.item_names = {key: self.clean(self.text(row['ItemName'])) for key, row in self.items.items()}

    def table(self, name):
        if name not in self.tables:
            path = self.directory / 'ExcelOutput' / (name + '.json')
            self.tables[name] = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []
        return self.tables[name]

    def index(self, name, key):
        return {row[key]: row for row in self.table(name)}

    def group(self, name, key):
        result = collections.defaultdict(list)
        for row in self.table(name):
            result[row[key]].append(row)
        return result

    def text(self, ref):
        if isinstance(ref, dict) and 'Hash' in ref:
            value = self.textmap.get(str(ref['Hash']))
            if value is None:
                self.unresolved.append({'text_hash': str(ref['Hash'])})
            return value or ''
        return ref if isinstance(ref, str) else ''

    def clean(self, text):
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
        text = re.sub(r'<property\s+[^>]*type=([^\s>]+)[^>]*display=all[^>]*>',
                      lambda match: self.property_labels.get(match.group(1)) or '[属性:' + match.group(1) + ']', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\{RUBY_[^}]+\}', '', text)
        return html.unescape(text).replace('\\n', '\n').replace('{NICKNAME}', '开拓者').strip()

    def effect(self, ref, params=()):
        template = self.text(ref)
        values = [scalar(value) for value in params]
        def entity_name(match):
            attrs = match.group(1)
            kind = re.search(r'type=(\w+)', attrs)
            key = re.search(r'id=(\d+)', attrs)
            if not kind or not key:
                return match.group(0)
            mapping = {'role': self.role_names, 'trait': self.trait_names,
                       'item': self.item_names, 'equip': self.item_names,
                       'equipment': self.item_names}.get(kind.group(1), {})
            return mapping.get(int(key.group(1)), match.group(0))
        rendered = re.sub(r'<gridfightinfo\s+([^>]+)>\s*<name>\s*</gridfightinfo>', entity_name, template)
        def parameter(match):
            index = int(match.group(1)) - 1
            if index >= len(values) or not isinstance(values[index], (int, float)):
                return match.group(0)
            number = values[index]
            if re.match(r'(?:</[^>]+>)*[%％]', rendered[match.end():]):
                number *= 100
            # Keep exact finite values; never guess a runtime damage formula.
            return format(number, '.15g')
        rendered = re.sub(r'(?<![=A-Za-z0-9_])#(\d+)(?:\[(?:i|f\d*)\])?(?![A-Za-z0-9_])', parameter, rendered)
        dynamic_modifiers = re.findall(r'<gridfightmod\s+([^>]+)>', template)
        unresolved_entities = re.findall(r'<gridfightinfo\s+([^>]+)>\s*<name>\s*</gridfightinfo>', rendered)
        text_hash = str(ref['Hash']) if isinstance(ref, dict) and 'Hash' in ref else None
        unresolved_parameters = sorted(set(re.findall(r'(?<![=A-Za-z0-9_])#\d+(?:\[[^\]]+\])?(?![A-Za-z0-9_])', rendered)))
        return {'text': self.clean(rendered), 'template': template,
                'parameters': values, 'text_hash': text_hash,
                'text_status': 'resolved' if template else 'not_in_published_TextMap' if text_hash else 'field_not_present',
                'rendering_policy': 'Static parameter expansion only. A percent suffix explicitly multiplies its value by 100; display rounding and runtime modifiers are not inferred. Original parameters are retained.',
                'dynamic_modifiers': dynamic_modifiers, 'unresolved_entity_references': unresolved_entities,
                'requires_runtime_context': bool(dynamic_modifiers or unresolved_entities or unresolved_parameters),
                'unresolved_placeholders': unresolved_parameters}

    def property_values(self, rows):
        return [{'property_type': row['PropertyType'], 'name': self.property_labels.get(row['PropertyType']) or row['PropertyType'],
                 'value': scalar(row.get('Value')), 'value_explicitly_present': 'Value' in row, 'raw': row,
                 'value_semantics': 'raw released property modifier; stacking and final statistic formulas are not inferred'} for row in rows]

    def config_reference(self, relative):
        if not relative:
            return {'path': relative, 'status': 'not_configured'}
        path = self.directory / relative
        result = {'path': relative, 'status': 'public_file_downloaded' if path.is_file() else 'not_in_downloaded_public_release',
                  'source_url': f'https://raw.githubusercontent.com/{REPOSITORY}/{self.revision}/{relative}',
                  'revision': self.revision}
        if path.is_file():
            result['file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def source(self, table, row):
        relative = 'ExcelOutput/' + table + '.json'
        path = self.directory / relative
        if table not in self.hashes:
            self.hashes[table] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'repository': REPOSITORY, 'revision': self.revision, 'version': self.version,
                'client_path': relative, 'file_sha256': self.hashes[table],
                'source_url': f'https://raw.githubusercontent.com/{REPOSITORY}/{self.revision}/{relative}',
                'row_key': {key: row.get(key) for key in ('ID', 'RoleID', 'MonsterID', 'MonsterTemplateID', 'TraitID', 'Star', 'SkillID',
                                                   'ModifySkillID', 'ModifySkillType', 'Level', 'RankID', 'EquipmentID',
                                                   'CraftID', 'CraftEquipID', 'Rarity', 'PropertyType', 'ConstValueName',
                                                   'PlayerLevel', 'GridFightLevel') if key in row or (table == 'GridFightSkillDescMod' and key == 'ModifySkillType')}}

    def base(self, table, row, key, name):
        return {'id': str(key), 'name': name, 'version': self.version, 'availability': 'current',
                'source': self.source(table, row), 'raw': row}

    def skill(self, row, table):
        effect = self.effect(row.get('SkillDesc'), row.get('ParamList', []))
        result = {'id': row['SkillID'], 'name': self.clean(self.text(row.get('SkillName'))),
                'name_text_hash': str(row['SkillName']['Hash']) if isinstance(row.get('SkillName'), dict) and 'Hash' in row['SkillName'] else None,
                'name_text_status': 'resolved' if self.text(row.get('SkillName')) else 'not_in_published_TextMap' if row.get('SkillName') else 'field_not_present',
                'type': self.clean(self.text(row.get('SkillTypeDesc'))), 'level': row.get('Level'),
                'effects': effect['text'], 'effect_details': effect,
                'source': self.source(table, row), 'raw': row}
        if table == 'GridFightBackBESkillConfig':
            extras = self.group('GridFightBackSkillExtraDesc', 'SkillID')
            result['condition_effects'] = [{'effect_details': self.effect(extra.get('ConditionDesc'), extra.get('ParamList', [])),
                                          'source': self.source('GridFightBackSkillExtraDesc', extra), 'raw': extra}
                                         for extra in extras.get(row['SkillID'], [])]
        if table == 'GridFightBackBESkillConfig':
            modifications = self.group('GridFightSkillDescMod', 'ModifySkillID')
            result['modifier_descriptions'] = [{'effect_details': self.effect(mod.get('ModifySkillDesc'), row.get('ParamList', [])),
                                               'source': self.source('GridFightSkillDescMod', mod), 'raw': mod,
                                               'parameter_source': self.source(table, row),
                                               'parameter_binding': 'explicit ModifySkillType=BESkill -> GridFightBackBESkillConfig.SkillID',
                                               'calculation_status': 'requires_runtime_rank_or_effect_state'}
                                              for mod in modifications.get(row['SkillID'], []) if mod.get('ModifySkillType') == 'BESkill']
        return result

    def normalize(self):
        doc = {'schema': 'currency-wars-normalized-client/v1', 'source_id': 'client_' + self.label,
               'version': self.version, 'default_availability': 'current', 'season_id': 1,
               'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'sources': [self.release], 'release_policy': 'current means this published configuration, not unconditional runtime shop availability; all pool and module conditions are retained.',
               'future_data_excluded': ['4.6.51 beta', '4.7'], **{kind: [] for kind in KINDS}}
        prices = self.index('GridFightShopPrice', 'Rarity')
        stars = self.group('GridFightRoleStar', 'ID')
        displays = self.group('GridFightRoleSkillDisplay', 'RoleID')
        front = self.group('GridFightFrontSkill', 'SkillID')
        back = self.group('GridFightBackBESkillConfig', 'SkillID')
        ranks = self.index('GridFightBackRoleRank', 'RankID')
        rank_modifiers = self.group('GridFightRankSkillModify', 'RankID')
        role_remarks = self.group('GridFightRoleRemark', 'RoleID')
        role_switches = self.group('GridFightRoleSwitchConfig', 'RoleID')
        back_equipment = self.group('GridFightBackEquipment', 'RoleID')
        module_roles = self.group('GridFightModuleBanRole', 'RoleId')
        constants = {row['ConstValueName']: scalar(row['Value']) for row in self.table('GridFightConstCommon')}
        initial_except = set(constants.get('GridFight_InitialExceptRoleIdList', []))
        for key, row in self.roles.items():
            out = self.base('GridFightRoleBasicInfo', row, key, self.role_names[key])
            avatar = self.avatars.get(row['AvatarID'], {})
            price = prices.get(row['Rarity'], {})
            out.update({'avatar_id': row['AvatarID'], 'cost': price.get('BuyGoldStar1'),
                        'cost_source': self.source('GridFightShopPrice', price) if price else None,
                        'prices_by_star': price, 'position': {'Front': '前台', 'Back': '后台'}.get(row.get('FrontBackType'), row.get('FrontBackType')),
                        'damage_type': avatar.get('DamageType'), 'is_expert': bool(row.get('IsExpert')),
                        'bonds': [self.trait_names.get(value, f'未解析羁绊[{value}]') for value in row.get('TraitList', [])],
                        'bond_ids': row.get('TraitList', []),
                        'pool_status': {'is_in_pool': row.get('IsInPool'), 'is_in_book': row.get('IsInBook'),
                                        'is_expert': bool(row.get('IsExpert')), 'excluded_from_initial_role_selection': key in initial_except,
                                        'season_id': row.get('SeasonID'), 'season_id_list': row.get('SeasonIDList'),
                                        'module_bans': module_roles.get(key, [])},
                        'shop_source': 'expert_investment_condition' if row.get('IsExpert') else 'conditional_variant' if key in initial_except else 'season_pool; runtime investment/module restrictions still apply',
                        'switch_conditions': role_switches.get(key, []),
                        'star_effects': [], 'rank_effects': [], 'skill_display': [], 'remarks': [], 'back_equipment_effects': [], 'effects': ''})
            if avatar.get('Release') is False or out['name'].startswith('未解析'):
                out['availability'] = 'unknown'
            for display in displays.get(key, []):
                out['skill_display'].append({'position': display['FrontBackType'], 'name': self.clean(self.text(display['Name'])), 'category_tags': display['CategoryTagList']})
            out['role'] = ','.join(sorted({tag for display in displays.get(key, []) for tag in display['CategoryTagList']}))
            for star in stars.get(key, []):
                detail = {'star': star['Star'], 'front_summary': self.clean(self.text(star.get('FrontOneWordDesc'))),
                          'back_summary': self.clean(self.text(star.get('BackOneWordDesc'))),
                          'front_skills': [], 'back_skills': [], 'source': self.source('GridFightRoleStar', star), 'raw': star}
                for field, index, table, target in [('FrontShowSkillIDList', front, 'GridFightFrontSkill', 'front_skills'),
                                                      ('BackShowSkillIDList', back, 'GridFightBackBESkillConfig', 'back_skills')]:
                    for skill_id in star.get(field, []):
                        choices = index.get(skill_id, [])
                        if choices:
                            selected = min(choices, key=lambda item: item.get('Level', 1))
                            detail[target].append(self.skill(selected, table))
                        else:
                            detail[target].append({'id': skill_id, 'unresolved': True})
                for stat in ('FrontPowerBase', 'BackPowerBase', 'LuckChance', 'LuckDamage', 'ExtraHealBase', 'ExtraShieldBase', 'BackMaxSP', 'BackInitialSP', 'BackEnergyBar', 'BackInitialEnergyBar'):
                    if stat in star:
                        detail[stat] = scalar(star[stat])
                detail['general_property_modifiers'] = self.property_values(star.get('GeneralPropertyModifyList', []))
                detail['config_reference'] = self.config_reference(star.get('JsonOverrideConfig'))
                out['star_effects'].append(detail)
            for rank_id in row.get('BackendRankList', []):
                rank = ranks.get(rank_id)
                if rank:
                    effect = self.effect(rank.get('Desc'), rank.get('DescParamList', []))
                    out['rank_effects'].append({'id': rank_id, 'rank': rank['Rank'], 'name': self.clean(self.text(rank['Name'])),
                                                'effects': effect['text'], 'effect_details': effect,
                                                'skill_modifiers': rank_modifiers.get(rank_id, []),
                                                'source': self.source('GridFightBackRoleRank', rank), 'raw': rank})
            for remark in role_remarks.get(key, []):
                out['remarks'].append({'effect_details': self.effect(remark.get('RoleRemark')),
                                       'source': self.source('GridFightRoleRemark', remark), 'raw': remark})
            for equip in back_equipment.get(key, []):
                out['back_equipment_effects'].append({'equipment_id': equip['EquipmentID'], 'level': equip['Level'],
                                                    'effect_details': self.effect(equip.get('BackEquipmentDesc'), equip.get('ParamList', [])),
                                                    'owner_property_modifiers': self.property_values(equip.get('OwnerGeneralPropertyList', [])),
                                                    'all_member_property_modifiers': self.property_values(equip.get('AllMemberGeneralPropertyList', [])),
                                                    'source': self.source('GridFightBackEquipment', equip), 'raw': equip})
            if out['star_effects']:
                out['effects'] = '\n'.join(filter(None, [out['star_effects'][0]['front_summary'], out['star_effects'][0]['back_summary']]))
            if out['position'] is None and any(star['front_skills'] and star['back_skills'] for star in out['star_effects']):
                out['position'] = '前后台'
                out['position_basis'] = 'Explicit star rows contain both FrontShowSkillIDList and BackShowSkillIDList; no missing enum default is guessed.'
            doc['characters'].append(out)
        trait_layers = self.group('GridFightTraitLayer', 'TraitID')
        trait_buffs = self.group('GridFightTraitMazebuff', 'ID')
        trait_effects = self.index('GridFightTraitEffect', 'ID')
        trait_effect_layers = self.group('GridFightTraitEffectLayerPa', 'ID')
        for key, row in self.traits.items():
            out = self.base('GridFightTraitBasicInfo', row, key, self.trait_names[key])
            effect = self.effect(row['TraitBaseDesc'], row.get('BaseDescParamList', []))
            out.update({'effects': effect['text'], 'effect_details': effect, 'activation_type': row['ActivationType'],
                        'thresholds': [], 'remarks': [], 'special_effects': []})
            for layer in trait_layers.get(key, []):
                buff = next(iter(trait_buffs.get(layer.get('MazebuffID'), [])), {})
                effect = self.effect(buff.get('BuffDesc'), buff.get('ParamList', []))
                property_effect = self.effect(layer.get('PropertyDesc'), layer.get('PropertyParamList', []))
                out['thresholds'].append({'members': layer['Layer'], 'quality': layer.get('Quality'), 'effects': '\n'.join(filter(None, [effect['text'], property_effect['text']])),
                                          'effect_details': effect, 'property_effects': property_effect,
                                          'source': self.source('GridFightTraitLayer', layer), 'raw': layer, 'buff_raw': buff})
            for effect_id in row.get('TraitEffectList', []):
                trait_effect = trait_effects.get(effect_id, {})
                if not trait_effect:
                    out['special_effects'].append({'id': effect_id, 'status': 'missing_table_join'})
                    continue
                out['special_effects'].append({'id': effect_id, 'effect_type': trait_effect['TraitEffectType'],
                                              'config_reference': self.config_reference(trait_effect.get('TraitEffectJson')),
                                              'layers': [{'members': layer['Layer'],
                                                          'effect_details': self.effect(layer.get('TraitEffectDesc'), layer.get('DescParamList', [])),
                                                          'source': self.source('GridFightTraitEffectLayerPa', layer), 'raw': layer}
                                                         for layer in trait_effect_layers.get(effect_id, [])],
                                              'source': self.source('GridFightTraitEffect', trait_effect), 'raw': trait_effect})
            for remark in self.table('GridFightTraitRemark'):
                if remark['ID'] == key:
                    effect = self.effect(remark['TraitRemark'], remark.get('TraitRemarkParamList', []))
                    out['remarks'].append({'effects': effect['text'], 'effect_details': effect, 'raw': remark})
            doc['bonds'].append(out)
        recipes = self.group('GridFightCraftConfig', 'CraftEquipID')
        season_items = {row['ItemID'] for row in self.table('GridFightSeasonItem') if row['SeasonID'] == 1}
        upgrades = self.group('GridFightEquipUpgrade', 'UpgradeID')
        for row in self.table('GridFightEquipment'):
            key = row['ID']; out = self.base('GridFightEquipment', row, key, self.item_names.get(key, f'未解析装备[{key}]'))
            effect = self.effect(row.get('EquipDesc'), row.get('ParamList', []))
            properties = self.property_values(row.get('GeneralPropertyList', []))
            property_text = '\n'.join(f"{value['name']} [{value['property_type']}] {value['value']:+g}（配置值）" for value in properties if isinstance(value['value'], (int, float)))
            out.update({'effects': effect['text'], 'effect_details': effect,
                        'general_property_modifiers': properties, 'property_effects': property_text,
                        'category': row.get('EquipCategory'), 'equipment_type': row.get('EquipType'),
                        'config_reference': self.config_reference(row.get('JsonPath')),
                        'recipe': [], 'recipe_variants': [], 'upgrades_from': [],
                        'enable_conditions': {'season_item_registered': key in season_items,
                                              'dress_rule': row.get('DressRule'), 'dress_rule_params': row.get('DressRuleParamList', [])}})
            if not out['effects'] and properties:
                out['effects'] = property_text
                out['effects_basis'] = 'Released GeneralPropertyList raw modifiers, not an inferred display percentage or stacking formula.'
            for recipe in recipes.get(key, []):
                ingredients = [{'id': ingredient, 'name': self.item_names.get(ingredient, f'未解析装备[{ingredient}]'), 'amount': amount}
                               for ingredient, amount in collections.Counter(recipe['CostEquipList']).items()]
                out['recipe_variants'].append({'ingredients': ingredients, 'source': self.source('GridFightCraftConfig', recipe), 'raw': recipe})
            if out['recipe_variants']:
                out['recipe'] = out['recipe_variants'][0]['ingredients']
            out['upgrades_from'] = [{'id': upgrade['PreID'], 'name': self.item_names.get(upgrade['PreID'])} for upgrade in upgrades.get(key, [])]
            doc['equipment'].append(out)
        for kind, table, name_field, desc_field, season_table, season_key, ban_table, ban_key in [
            ('investment_environments', 'GridFightPortalBuff', 'PortalBuffTitle', 'PortalBuffDesc', 'GridFightSeasonPortal', 'PortalID', 'GridFightModuleBanPortal', 'BanPortalId'),
            ('investment_strategies', 'GridFightAugment', 'HexName', 'HexDesc', 'GridFightSeasonAugment', 'AugmentID', 'GridFightModuleBanAugment', 'BanAugmentId')]:
            season_ids = {row[season_key] for row in self.table(season_table) if row['SeasonID'] == 1}
            bans = self.group(ban_table, ban_key)
            for row in self.table(table):
                key = row['ID']; out = self.base(table, row, key, self.clean(self.text(row[name_field])))
                effect = self.effect(row[desc_field], row.get('EffectParamList', []))
                out.update({'effects': effect['text'], 'effect_details': effect, 'quality': row.get('Quality'),
                            'enable_conditions': {'season_registered': key in season_ids, 'module_bans': bans.get(key, []),
                                                  'chapter_limit': row.get('ChapterLimitList'), 'is_oc_effective': row.get('IsOCEffective'),
                                                  'is_in_book': row.get('IfInBook')}, 'config_path': row.get('JsonPath'),
                            'config_reference': self.config_reference(row.get('JsonPath')), 'remarks': []})
                remark_table, remark_key, remark_field = ('GridFightPortalRemark', 'PortalID', 'PortalRemark') if kind == 'investment_environments' else ('GridFightAugmentRemark', 'AugmentID', 'AugmentRemark')
                for remark in self.table(remark_table):
                    if remark[remark_key] == key:
                        out['remarks'].append({'effect_details': self.effect(remark.get(remark_field)),
                                               'source': self.source(remark_table, remark), 'raw': remark})
                if key not in season_ids:
                    out['availability'] = 'inactive'
                doc[kind].append(out)
        monsters = self.index('MonsterConfig', 'MonsterID')
        templates = self.index('MonsterTemplateConfig', 'MonsterTemplateID')
        monster_skills = self.group('MonsterSkillConfig', 'SkillID')
        groups = self.index('GridFightEliteGroup', 'EliteGroup')
        for row in self.table('GridFightMonster'):
            key = row['MonsterID']; monster = monsters.get(key, {}); template = templates.get(monster.get('MonsterTemplateID'), {})
            name = self.clean(self.text(monster.get('MonsterName') or template.get('MonsterName'))) or f'未解析敌人[{key}]'
            out = self.base('GridFightMonster', row, key, name)
            out.update({'template_id': monster.get('MonsterTemplateID'), 'rank': template.get('Rank'), 'tier': row.get('MonsterTier'),
                        'weaknesses': monster.get('StanceWeakList'), 'damage_resistance': monster.get('DamageTypeResistance'),
                        'stats_base': {k: scalar(template[k]) for k in ('AttackBase', 'DefenceBase', 'HPBase', 'SpeedBase', 'StanceBase') if k in template},
                        'monster_modifiers': {k: scalar(monster[k]) for k in ('AttackModifyRatio', 'DefenceModifyRatio', 'HPModifyRatio', 'SpeedModifyRatio', 'StanceModifyRatio') if k in monster},
                        'star_scaling': {str(star): groups.get(row.get('Star' + str(star) + 'EliteGroup3'), {}) for star in range(1, 5)},
                        'config_reference': self.config_reference(template.get('JsonConfig')),
                        'skills': [], 'monster_raw': monster, 'template_raw': template})
            for skill_id in monster.get('SkillList', []):
                choices = monster_skills.get(skill_id, [])
                out['skills'].extend(self.skill(skill, 'MonsterSkillConfig') for skill in choices if skill.get('Level', 1) == 1)
            out['effects'] = '\n'.join(skill.get('effects', '') for skill in out['skills'])
            doc['enemy_units'].append(out)
        for row in self.table('GridFightCamp'):
            out = self.base('GridFightCamp', row, row['ID'], self.clean(self.text(row['CampName'])))
            out.update({'pool_status': {'random_generation_enabled': bool(row.get('IfRandomEnabled')), 'initial_random_code': row.get('InitialRandomCode')},
                        'enemy_unit_ids': row['MonsterList'], 'enemy_units': [{'id': key, 'name': self.clean(self.text(monsters.get(key, {}).get('MonsterName')))} for key in row['MonsterList']],
                        'battle_areas': row.get('BattleAreaList'), 'boss_battle_area': row.get('BossBattleArea'), 'effects': '',
                        'effects_status': 'not_applicable_camp_is_unit_membership_and_battle_area_configuration'})
            doc['opponents'].append(out)
        for row in self.table('GridFightAffixConfig'):
            out = self.base('GridFightAffixConfig', row, row['ID'], self.clean(self.text(row['AffixName'])))
            effect = self.effect(row['AffixDesc'], row.get('EffectParamList', []))
            out.update({'effects': effect['text'], 'effect_details': effect, 'config_path': row.get('JsonPath'),
                        'config_reference': self.config_reference(row.get('JsonPath')), 'rule_param_list': row.get('RuleParamList')})
            doc['enemy_affixes'].append(out)
        doc['economy_rules'] = {'shop_prices': self.table('GridFightShopPrice'), 'player_levels': self.table('GridFightPlayerLevel'),
                                'level_v2': self.table('GridFightLevelV2'), 'constants': constants,
                                'constants_v2': self.table('GridFightConstValueCommonV2'),
                                'sources': [self.source(name, {}) for name in ('GridFightShopPrice', 'GridFightPlayerLevel', 'GridFightLevelV2', 'GridFightConstCommon', 'GridFightConstValueCommonV2')]}
        v2_pool_names = {'GridFight_CardPoolNumber', 'GridFight_AvatarOptionBuyOrSellWeightDelta',
                         'GridFight_MaxRarityStolenSameCardLimit', 'GridFight_StolenPoolRefundParamFromInitialPurchase',
                         'GridFight_StolenPoolRefundParamFromSell', 'GridFight_StolenPoolRefundParamFromHold'}
        doc['economy_rules']['card_pool'] = {
            'configuration_fields_present': [{'name': row['ConstValueName'], 'value': scalar(row['Value']),
                                              'source': self.source('GridFightConstValueCommonV2', row), 'raw': row}
                                             for row in self.table('GridFightConstValueCommonV2') if row['ConstValueName'] in v2_pool_names],
            'active_mode_profile': 'unknown', 'finite_per_role_copy_count': 'unknown',
            'ownership_decreases_remaining_draw_probability': 'unknown', 'selling_returns_to_natural_shop_pool': 'unknown',
            'calculation_status': 'Do not infer the per-role unit, array index mapping, weight-change direction, stolen-pool behavior or runtime V2 activation from constant names alone.'}
        doc['shop_odds'] = {'rarity_weight': self.table('GridFightRarityWeight'), 'level_v2': self.table('GridFightLevelV2'),
                            'player_level_weights': self.table('GridFightPlayerLevel'),
                            'v2_card_weight_constants': [row for row in self.table('GridFightConstValueCommonV2') if row['ConstValueName'].startswith('GridFight_CardWeight_Lv')],
                            'effective_profile': 'unknown',
                            'profile_selection_warning': 'RarityWeight, PlayerLevel and LevelV2 disagree at some levels. Current release does not imply all profiles apply to the same current mode; no effective profile is selected without runtime or implementation evidence.',
                            'raw_weight_warning': 'Raw weights are retained; no probability denominator or interest/damage formula is guessed.'}
        doc['property_definitions'] = [{'property_type': row['PropertyType'], 'name': self.property_labels[row['PropertyType']],
                                       'panel_name': self.clean(self.text(row.get('PanelPropertyName'))),
                                       'source': self.source('GridFightRolePropertyConfig', row), 'raw': row}
                                      for row in self.table('GridFightRolePropertyConfig')]
        doc['economy_rules']['expert_restrictions'] = self.table('GridFightExpertRestrict')
        doc['modifier_description_catalog'] = []
        modifier_sources = {'BESkill': 'GridFightBackBESkillConfig', 'ServantSkill': 'GridFightServantSkill'}
        for modification in self.table('GridFightSkillDescMod'):
            table = modifier_sources.get(modification.get('ModifySkillType'))
            candidates = [row for row in self.table(table) if row['SkillID'] == modification['ModifySkillID']] if table else []
            if candidates:
                for row in candidates:
                    doc['modifier_description_catalog'].append({'skill_id': modification['ModifySkillID'],
                        'skill_type': modification.get('ModifySkillType'), 'level': row.get('Level'),
                        'effect_details': self.effect(modification.get('ModifySkillDesc'), row.get('ParamList', [])),
                        'parameter_source': self.source(table, row), 'source': self.source('GridFightSkillDescMod', modification),
                        'parameter_binding_status': 'explicit_skill_type_and_id_join',
                        'calculation_status': 'static_parameter_expansion_only; runtime modifiers require current state', 'raw': modification})
            else:
                doc['modifier_description_catalog'].append({'skill_id': modification['ModifySkillID'],
                    'skill_type': modification.get('ModifySkillType'), 'effect_details': self.effect(modification.get('ModifySkillDesc')),
                    'parameter_source': None, 'source': self.source('GridFightSkillDescMod', modification),
                    'parameter_binding_status': 'skill_type_not_present_no_parameter_binding' if not table else 'typed_skill_id_not_found',
                    'calculation_status': 'unresolved_template_no_determined_numeric_effect', 'raw': modification})
        doc['coverage'] = {kind: {'count': len(doc[kind]), 'availability': dict(collections.Counter(row['availability'] for row in doc[kind]))} for kind in KINDS}
        doc['unresolved_text_hashes'] = sorted({row['text_hash'] for row in self.unresolved})
        return doc


def main():
    current = Release('release_4_6').normalize()
    atomic_json(ROOT / 'normalized_current.json', current)
    baseline = Release('release_4_4').normalize()
    baseline['default_availability'] = 'historical'
    baseline['release_policy'] = 'Historical 4.4 production release baseline; never import as current game configuration.'
    for kind in KINDS:
        for row in baseline[kind]:
            row['release_pool_status'] = row['availability']
            row['availability'] = 'historical'
        baseline['coverage'][kind]['availability'] = {'historical': len(baseline[kind])}
    atomic_json(ROOT / 'normalized_baseline_4_4.json', baseline)
    print(json.dumps({'output': str(ROOT / 'normalized_current.json'), 'coverage': current['coverage'],
                      'unresolved_text_hashes': len(current['unresolved_text_hashes'])}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
