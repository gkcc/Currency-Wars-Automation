"""Stable offline assignment regressions; all scores and sources are synthetic."""
import copy
import itertools
import random
import unittest

from currency_wars_bond_model import BondModelError, COUNT_SEMANTICS, SearchLimits, solve_deployment


def unit(role, front=0, back=0, bench=0, positions=('front', 'back'), uid=None, **extra):
    return {'uid': uid or role, 'role_id': role, 'positions': list(positions),
            'scores': {'front': front, 'back': back, 'bench': bench}, **extra}


def bond(identity, members, thresholds, positions=('front', 'back')):
    return {'id': identity, 'source': 'synthetic-counting-fixture/v1',
            'utility_source': 'synthetic-heuristic-fixture/v1; not DPS',
            'count_semantics': COUNT_SEMANTICS, 'semantics_verified': True,
            'members': [{'role_id': role, 'positions': list(positions)} for role in members],
            'thresholds': [{'count': count, 'utility': utility} for count, utility in thresholds]}


def score(item, zone):
    return item['scores'][zone]


def run(units, bonds=(), population=9, front_capacity=4, back_capacity=6, bench_capacity=9, **kwargs):
    return solve_deployment(units, score, population=population, front_capacity=front_capacity,
                            back_capacity=back_capacity, bench_capacity=bench_capacity,
                            bonds=bonds, **kwargs)


def exhaustive_score(units, bonds, population, front_capacity, back_capacity, bench_capacity):
    """Small independent oracle enumerating physical choices and full placements."""
    groups = {}
    for item in units:
        groups.setdefault(item['role_id'], [None]).extend((item, zone) for zone in item['positions'])
    best = None
    for choices in itertools.product(*groups.values()):
        selected = [entry for entry in choices if entry is not None]
        if (len(selected) > population or len(units) - len(selected) > bench_capacity
                or sum(zone == 'front' for _, zone in selected) > front_capacity
                or sum(zone == 'back' for _, zone in selected) > back_capacity):
            continue
        placed = {item['uid']: zone for item, zone in selected}
        value = sum(score(item, placed.get(item['uid'], 'bench')) for item in units)
        for definition in bonds:
            membership = {member['role_id']: member['positions'] for member in definition['members']}
            count = sum(zone in membership.get(item['role_id'], ()) for item, zone in selected)
            active = [tier for tier in definition['thresholds'] if tier['count'] <= count]
            value += max(active, key=lambda tier: tier['count'])['utility'] if active else 0
        best = value if best is None else max(best, value)
    return best


def performance_case(role_count):
    """7--12 roles, population 9, five overlapping synthetic bond definitions."""
    units = [unit(f'role-{index:02}', front=100 + index * 3, back=90 + index * 5,
                  bench=5 + index % 3,
                  positions=('front',) if index % 5 == 0 else ('back',) if index % 5 == 1
                  else ('front', 'back')) for index in range(role_count)]
    ids = [item['role_id'] for item in units]
    bonds = [bond('even', ids[::2], [(2, 50), (4, 140), (6, 210)]),
             bond('nonthird', [role for index, role in enumerate(ids) if index % 3],
                  [(2, 25), (4, 110), (6, 170)]),
             bond('nonfourth-front', [role for index, role in enumerate(ids) if index % 4],
                  [(2, 70), (3, 130)], positions=('front',)),
             bond('odd-back', ids[1::2], [(2, 40), (4, 120)], positions=('back',)),
             bond('all', ids, [(3, 5), (5, 25), (7, 50), (9, 80)])]
    return units, bonds


class BondDeploymentTests(unittest.TestCase):
    def test_threshold_combination_beats_independent_assignment(self):
        units = [unit('a', 10, positions=('front',)), unit('b', 9, positions=('front',)),
                 unit('c', 8, positions=('front',)), unit('d', 7, positions=('front',))]
        independent = run(units, population=2, front_capacity=2)
        result = run(units, [bond('pair', ['c', 'd'], [(2, 10)])], population=2, front_capacity=2)
        self.assertEqual(independent['best']['estimated_lineup_score'], 19)
        self.assertEqual(result['best']['estimated_lineup_score'], 25)
        self.assertEqual({item['role_id'] for item in result['best']['units']
                          if item['location']['zone'] != 'bench'}, {'c', 'd'})
        self.assertTrue(result['exact'])

    def test_highest_attained_threshold_replaces_lower_even_when_nonmonotonic(self):
        units = [unit(role) for role in ('a', 'b', 'c', 'd')]
        result = run(units, [bond('tiers', ['a', 'b', 'c', 'd'], [(1, 100), (2, 20), (3, 30)])],
                     population=4, bench_capacity=0)
        details = result['best']['bonds'][0]
        self.assertEqual((details['count'], details['active_threshold'], details['heuristic_utility']),
                         (4, 3, 30))
        self.assertEqual(result['best']['bond_utility'], 30)
        inactive = run(units[:1], [bond('inactive', ['a', 'b'], [(2, 50)])], bench_capacity=0)
        self.assertIsNone(inactive['best']['bonds'][0]['active_threshold'])
        self.assertEqual(inactive['best']['bond_utility'], 0)

    def test_physical_copies_count_once_keep_uid_lineage_and_real_bench_space(self):
        units = [unit('a', 10, 10, uid='a-star1', star=1),
                 unit('a', 20, 20, uid='merged-a', star=2, member_ids=['a1', 'a2', 'a3']),
                 unit('b', 8, 8, uid='b-star1', star=1)]
        original = copy.deepcopy(units)
        definitions = [bond('copies', ['a', 'b'], [(2, 5), (3, 1000)])]
        result = run(units, definitions, population=3, bench_capacity=1)
        self.assertEqual(result['best']['bonds'][0]['count'], 2)
        self.assertEqual(result['best']['bond_utility'], 5)
        self.assertEqual(len([item for item in result['best']['units']
                              if item['location']['zone'] == 'bench']), 1)
        placed = {item['uid']: item for item in result['best']['units']}
        self.assertEqual(placed['merged-a']['member_ids'], ['a1', 'a2', 'a3'])
        self.assertEqual(placed['merged-a']['star'], 2)
        self.assertNotEqual(placed['merged-a']['location']['zone'], 'bench')
        self.assertEqual(set(placed), {item['uid'] for item in units})
        self.assertEqual(units, original)
        impossible = run(units, definitions, population=3, bench_capacity=0)
        self.assertEqual(impossible['status'], 'infeasible')
        self.assertTrue(impossible['exact'])
        self.assertIsNone(impossible['best'])

    def test_zone_eligibility_and_capacities_are_hard_constraints(self):
        units = [unit('a', 3, 100, positions=('front',)),
                 unit('b', 100, 4, positions=('back',)), unit('c', 9, 8)]
        definition = bond('back-only', ['a', 'b', 'c'], [(2, 100)], positions=('back',))
        result = run(units, [definition], population=2, front_capacity=1,
                     back_capacity=1, bench_capacity=1)
        self.assertEqual(result['best']['bond_utility'], 0)
        layout = result['best']['units']
        self.assertEqual(sum(item['location']['zone'] == 'front' for item in layout), 1)
        self.assertEqual(sum(item['location']['zone'] == 'back' for item in layout), 1)
        self.assertEqual(len({(item['location']['zone'], item['location']['slot']) for item in layout}), 3)
        self.assertTrue(all(item['location']['zone'] == 'bench' or
                            item['location']['zone'] in item['positions'] for item in layout))
        impossible = run(units, [definition], population=2, front_capacity=0,
                         back_capacity=1, bench_capacity=1)
        self.assertEqual(impossible['status'], 'infeasible')

    def test_no_bonds_and_shared_bonds_match_exhaustive_assignments(self):
        rng = random.Random(206)
        for case in range(24):
            units = [unit(str(index), rng.randrange(-5, 20), rng.randrange(-5, 20),
                          rng.randrange(-2, 5), positions=rng.choice(
                              [('front',), ('back',), ('front', 'back')])) for index in range(6)]
            units.append(unit('1', 13, 10, 2, uid='copy-1'))
            definitions = [] if case % 2 else [
                bond('overlap-a', ['0', '1', '2', '3'], [(2, 7), (3, 4), (4, 18)]),
                bond('overlap-b', ['1', '3', '4', '5'], [(2, 11), (3, 15)], positions=('back',))]
            capacities = dict(population=4, front_capacity=case % 4, back_capacity=3, bench_capacity=3)
            expected = exhaustive_score(units, definitions, **capacities)
            result = run(units, definitions, **capacities)
            with self.subTest(case=case):
                self.assertTrue(result['exact'])
                self.assertEqual(result['best']['estimated_lineup_score'] if result['best'] else None, expected)

    def test_each_search_limit_reports_unproven_legal_incumbent(self):
        units, definitions = performance_case(12)
        expected = run(units, definitions, limits=SearchLimits(time_limit_ms=10_000))
        self.assertEqual(expected['status'], 'optimal')
        for field, limits in [('max_states', SearchLimits(max_states=1, time_limit_ms=10_000)),
                              ('max_transitions', SearchLimits(max_transitions=1, time_limit_ms=10_000)),
                              ('time_limit_ms', SearchLimits(time_limit_ms=0))]:
            with self.subTest(limit=field):
                result = run(units, definitions, bench_capacity=3, limits=limits)
                self.assertEqual(result['status'], 'bounded')
                self.assertTrue(result['approximate'])
                self.assertFalse(result['exact'])
                self.assertTrue(result['feasible'])
                self.assertEqual(result['search']['limit_reached'], field)
                self.assertEqual(result['optimality'], 'unproven_search_limit')
                self.assertLessEqual(result['best']['estimated_lineup_score'], expected['best']['estimated_lineup_score'])
                self.assertEqual(sum(item['location']['zone'] == 'bench' for item in result['best']['units']), 3)

    def test_unknown_or_unsupported_definitions_are_never_treated_as_zero(self):
        valid = bond('example', ['a', 'b'], [(2, 10)])
        invalid = [None, [{}]]
        for key in valid:
            missing = copy.deepcopy(valid)
            del missing[key]
            invalid.append([missing])
        for patch in [{'semantics_verified': False}, {'thresholds': []}, {'members': []},
                      {'count_semantics': 'stars_plus_emblems'}, {'future_condition': True},
                      {'thresholds': [{'count': 2, 'utility': None}]},
                      {'members': valid['members'] + [valid['members'][0]]}]:
            invalid.append([{**valid, **patch}])
        for definitions in invalid:
            with self.subTest(definitions=definitions), self.assertRaises(BondModelError):
                run([unit('a')], definitions)

    def test_invalid_identity_capacity_and_nonfinite_scores_fail_closed(self):
        for bad in (float('nan'), float('inf'), -float('inf'), None, True, 10**1000):
            with self.subTest(value_type=type(bad).__name__), self.assertRaises(BondModelError):
                run([unit('a', front=bad)])
            with self.assertRaises(BondModelError):
                run([unit('a')], [bond('bad-utility', ['a'], [(1, bad)])])
        with self.assertRaises(BondModelError):
            run([unit('a'), unit('b', uid='a')])
        with self.assertRaises(BondModelError):
            run([unit('a', name='same'), unit('b', name='same')])
        for capacity in (-1, True, 1.5):
            with self.assertRaises(BondModelError):
                run([unit('a')], bench_capacity=capacity)
        with self.assertRaises(BondModelError):
            run([unit('a', bench=1e308), unit('b', bench=1e308)])
        missing_score = unit('a')
        del missing_score['scores']['front']
        with self.assertRaisesRegex(BondModelError, 'unavailable'):
            run([missing_score])


if __name__ == '__main__':
    unittest.main()
