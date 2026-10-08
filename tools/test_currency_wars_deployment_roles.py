"""Focused cached deployment constraints; no capture, input or model service."""
import copy
import unittest

from currency_wars_coaching import deployment_position, lineup_requirements


class DeploymentRolesTests(unittest.TestCase):
    def test_cached_aliases_require_one_valid_type(self):
        for position in ('前台', '后台', '前后台'):
            for role in ({'position': position}, {'deployment': position},
                         {'position': position, 'deployment': position}):
                with self.subTest(role=role):
                    self.assertEqual(deployment_position({'roles': {'角色': role}}, '角色'), position)
        invalid = (None, {}, {'roles': {}}, {'roles': {'角色': None}},
                   {'roles': {'角色': {}}}, {'roles': {'角色': {'position': None}}},
                   {'roles': {'角色': {'position': 'front'}}},
                   {'roles': {'角色': {'position': '前台', 'deployment': '后台'}}},
                   {'roles': {'角色': {'position': '前台', 'deployment': None}}})
        for knowledge in invalid:
            with self.subTest(knowledge=knowledge), self.assertRaises(ValueError):
                deployment_position(knowledge, '角色')

    def test_cached_type_cannot_be_overridden_or_hidden(self):
        for role, declared, row in (({'position': '前台'}, '后台', 'front'),
                                    ({'deployment': '前台'}, '前后台', 'back'),
                                    ({'position': '后台'}, '前台', 'front'),
                                    ({'position': '前台', 'deployment': '后台'}, '前台', 'front')):
            team = {'checked': True, 'units': [{'name': '角色', 'location': 'board',
                    'row': row, 'slot': 1, 'position': declared}]}
            before = copy.deepcopy(team)
            with self.subTest(role=role, declared=declared, row=row):
                result = lineup_requirements([], team, {'roles': {'角色': role}})
                self.assertFalse(result['verified'])
                self.assertTrue(result['position_violations'])
                self.assertEqual(team, before)
        # The cache supplies a planning constraint, never a fabricated native badge.
        team = {'checked': True, 'units': [{'name': '角色', 'location': 'board',
                'row': 'front', 'slot': 1, 'position': None}]}
        result = lineup_requirements([], team, {'roles': {'角色': {'deployment': '前台'}}})
        self.assertTrue(result['verified'])
        self.assertIsNone(team['units'][0]['position'])

    def test_missing_cache_preserves_current_explicit_type_and_unknown(self):
        for position, row, verified in (('前台', 'front', True), ('后台', 'back', True),
                                        ('前后台', 'front', True), (None, 'front', False)):
            team = {'checked': True, 'units': [{'name': '角色', 'location': 'board',
                    'row': row, 'slot': 1, 'position': position}]}
            with self.subTest(position=position):
                result = lineup_requirements([], team, {'roles': {}})
                self.assertEqual(result['verified'], verified)
                self.assertEqual(result['position_violations'], [])
                self.assertEqual(result['unknown_positions'], [] if verified else ['角色'])

    def test_front_four_back_six_and_unique_slots_remain_required(self):
        units = [{'name': f'{row}{slot}', 'location': 'board', 'row': row,
                  'slot': slot, 'position': position}
                 for row, count, position in (('front', 4, '前台'), ('back', 6, '后台'))
                 for slot in range(1, count + 1)]
        self.assertTrue(lineup_requirements([], {'checked': True, 'units': units}, {})['verified'])
        for index, slot in ((0, 5), (4, 7), (1, 1)):
            changed = copy.deepcopy(units)
            changed[index]['slot'] = slot
            with self.subTest(index=index, slot=slot):
                result = lineup_requirements([], {'checked': True, 'units': changed}, {})
                self.assertFalse(result['verified'])
                self.assertTrue(result['position_violations'])


if __name__ == '__main__':
    unittest.main()
