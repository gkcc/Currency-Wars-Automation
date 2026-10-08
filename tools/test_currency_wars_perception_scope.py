"""Explicit read-scope contracts on generated PNGs and inert Entry fixtures.

The OCR rows below are declared test data. They check routing, cache ownership
and the same-request full reread, not recognition accuracy or game latency.
No controller, fresh game capture, physical input or historical receipt is used.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

from PIL import Image

import currency_wars_perception as perception
import currency_wars_runner as runner
import test_currency_wars_economy as economy_fixture
from replay_currency_wars_rewards import mutation_publications, protocol_fixture
from test_currency_wars_economy import values


@contextlib.contextmanager
def declared_reader(page='preparation'):
    """Real Perception routing with declared OCR and side-reader results."""
    with tempfile.TemporaryDirectory(prefix='currency-wars-read-scope-') as temporary:
        path = Path(temporary) / 'declared-frame.png'
        image = Image.new('RGB', (1920, 1080), 'black')
        image.paste((225, 180, 25), (1572, 891, 1626, 949))
        image.save(path)
        native_rows = [('备战阶段', [430, 33, 513, 60]), ('3-6', [441, 65, 502, 98]),
            ('出战', [1784, 730, 1852, 770]), ('商店', [1596, 966, 1650, 998]),
            ('8/8', [900, 218, 1005, 264]), ('100', [1430, 66, 1482, 94]),
            ('购买经验', [241, 839, 340, 877]), ('Lv.8', [252, 890, 325, 925]),
            ('65', [1630, 903, 1678, 940])]
        if page == 'shop':
            native_rows = [(text if text != '商店' else '收起', box) for text, box in native_rows]
            native_rows.append(('刷新', [1592, 472, 1646, 506]))
        elif page == 'investment_summary':
            native_rows = [('投资环境', [120, 200, 320, 250]), ('投资策略', [850, 200, 1100, 250])]
        elif page == 'supply':
            native_rows = [('补给阶段', [860, 142, 1050, 173]), ('确认', [1650, 960, 1770, 1000])]
        raw = [([[left / 1.5, top / 1.5], [right / 1.5, top / 1.5],
                 [right / 1.5, bottom / 1.5], [left / 1.5, bottom / 1.5]], text, .99)
               for text, (left, top, right, bottom) in native_rows]

        def engine(array, **kwargs):
            return (copy.deepcopy(raw), None) if array.shape[:2] == (720, 1280) else (None, None)

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        state = {'snapshot_id': digest, 'status': 'unknown',
            'team': {'snapshot_id': digest, 'status': 'unknown', 'fully_read': False,
                     'units': [], 'unknown_slots': ['declared-fixture-unread']},
            'inventory': {'snapshot_id': digest, 'status': 'unknown', 'fully_read': False,
                          'checked': False, 'items': [], 'unknown_slots': ['declared-fixture-unread']}}
        reader = perception.Perception()
        reader.engine = engine
        reader.state_reader = SimpleNamespace(read=Mock(side_effect=lambda *args, **kwargs: copy.deepcopy(state)))
        reader.shop_reader = SimpleNamespace(read=Mock(return_value={'status': 'ok', 'ok': True,
            'input': {'sha256': digest}, 'slots': [], 'source': 'declared_scope_fixture'}))
        yield reader, path


def record_fixture_reads(worker):
    """Declare scope output in a semantic fixture; record real Worker calls."""
    original_read = worker.perception.read
    original_frame = worker.read_frame
    reads, attempts = [], []

    def read(path, force=False, *, scope='full', reuse_primary=False):
        observed = copy.deepcopy(original_read(path, force, scope=scope))
        effective = scope if observed['page'] in ('preparation', 'shop') else 'full'
        observed['read_contract'] = {'version': 1, 'requested_scope': scope, 'effective_scope': effective}
        observed['read_timing'] = {'cache_hit': False, 'source': 'declared_scope_fixture'}
        if effective != 'full':
            omitted = {'status': 'not_read', 'reason': 'explicit inert fixture scope',
                'read_scope': effective, 'snapshot_id': observed['snapshot_id'],
                'checked': False, 'fully_read': False}
            observed['semantic']['team'] = {**omitted, 'units': []}
            observed['semantic']['inventory'] = {**omitted, 'items': []}
            observed['state_read'] = copy.deepcopy(omitted)
            if effective == 'rewards':
                observed['fields'].update(level=None, deployed=None)
                observed['semantic']['player_hud'] = copy.deepcopy(omitted)
        reads.append({'path': str(path), 'force': force, 'scope': scope, 'reuse_primary': reuse_primary,
                      'snapshot_id': observed['snapshot_id']})
        return observed

    def frame(result, *, scope='full', force=False, reuse_primary=False):
        attempts.append({'request_id': result['id'], 'scope': scope, 'force': force,
                         'reuse_primary': reuse_primary})
        return original_frame(result, scope=scope, force=force, reuse_primary=reuse_primary)

    worker.perception.read, worker.read_frame = read, frame
    return reads, attempts


@contextlib.contextmanager
def missing_first_mutation_frame(enabled):
    """Corrupt only a completed fixture input's PNG; never repeat that input."""
    original = runner.entry.request
    missing = []

    def request(controller, kind, tokens, rid, handoff, **kwargs):
        result = original(controller, kind, tokens, rid, handoff, **kwargs)
        if (enabled and not missing and kind == 'actions'
                and any(token.split(':')[0] not in ('observe', 'wait') for token in tokens)):
            Path(result['observation']['snapshot']).write_bytes(b'explicit truncated fixture PNG')
            missing.append(rid)
        return result

    with patch.object(runner.entry, 'request', side_effect=request):
        yield missing


class PerceptionScopeTests(unittest.TestCase):
    def assert_contract(self, observed, requested, effective=None):
        self.assertEqual({key: observed['read_contract'][key]
            for key in ('version', 'requested_scope', 'effective_scope')}, {'version': 2,
            'requested_scope': requested, 'effective_scope': effective or requested})

    def assert_not_read(self, item, observed, scope):
        self.assertEqual(item['status'], 'not_read')
        self.assertEqual(item['read_scope'], scope)
        self.assertEqual(item['snapshot_id'], observed['snapshot_id'])
        self.assertTrue(item['reason'])
        self.assertIs(item['checked'], False)
        self.assertIs(item['fully_read'], False)

    def test_default_full_and_narrow_scopes_distinguish_unread_from_unknown(self):
        for page in ('preparation', 'shop'):
            with self.subTest(page=page), declared_reader(page) as (reader, path):
                full = reader.read(path)
                self.assert_contract(full, 'full')
                self.assertEqual(full['page'], page)
                self.assertEqual(reader.state_reader.read.call_count, 1)
                self.assertFalse(full['semantic']['team']['checked'])
                self.assertEqual(full['semantic']['team']['status'], 'unknown')
                for scope in ('rewards', 'economy'):
                    with self.subTest(scope=scope):
                        shop_count = reader.shop_reader.read.call_count
                        observed = reader.read(path, scope=scope)
                        self.assert_contract(observed, scope)
                        self.assertEqual(observed['fields']['stage'], '3-6')
                        self.assertEqual(observed['semantic']['coins']['value'], 65)
                        self.assertEqual(reader.state_reader.read.call_count, 1)
                        for item in (observed['state_read'], observed['semantic']['team'], observed['semantic']['inventory']):
                            self.assert_not_read(item, observed, scope)
                        if scope == 'rewards':
                            self.assertIsNone(observed['fields']['level'])
                            self.assertIsNone(observed['fields']['deployed'])
                            self.assertEqual(observed['semantic']['player_hud']['status'], 'not_read')
                            self.assertEqual(reader.shop_reader.read.call_count, shop_count)
                        else:
                            self.assertEqual(observed['fields']['level'], full['fields']['level'])
                            self.assertEqual(observed['fields']['deployed'], full['fields']['deployed'])
                            if page == 'shop':
                                self.assertEqual(reader.shop_reader.read.call_count, shop_count + 1)
                reread = reader.read(path, force=True)
                self.assert_contract(reread, 'full')
                self.assertEqual(reader.state_reader.read.call_count, 2)
                self.assertEqual(reread['semantic']['team']['status'], 'unknown')

    def test_same_png_scope_cache_and_returned_nested_objects_are_isolated(self):
        with declared_reader() as (reader, path):
            narrow = reader.read(path, scope='rewards')
            self.assertFalse(narrow['read_timing']['cache_hit'])
            timing_before = copy.deepcopy(narrow['read_timing'])
            cached = reader.read(path, scope='rewards')
            self.assertTrue(cached['read_timing']['cache_hit'])
            self.assertEqual(narrow['read_timing'], timing_before)
            narrow['frame_id'] = 'caller-only-transport-identity'
            narrow['read_contract']['requested_scope'] = 'caller-corruption'
            narrow['semantic']['team']['units'].append({'name': 'caller-corruption'})
            narrow['rows'].append({'text': 'caller-corruption'})
            narrow['read_timing']['caller_corruption'] = True
            cached = reader.read(path, scope='rewards')
            self.assert_contract(cached, 'rewards')
            self.assertNotIn('frame_id', cached)
            self.assertEqual(cached['semantic']['team']['units'], [])
            self.assertFalse(any(row['text'] == 'caller-corruption' for row in cached['rows']))
            self.assertNotIn('caller_corruption', cached['read_timing'])
            full = reader.read(path)
            self.assert_contract(full, 'full')
            self.assertEqual(reader.state_reader.read.call_count, 1)
            self.assertEqual(full['semantic']['team']['status'], 'unknown')
            full['semantic']['team']['units'].append({'name': 'caller-corruption'})
            full_again = reader.read(path)
            self.assertEqual(full_again['semantic']['team']['units'], [])
            other_scope = reader.read(path, scope='economy')
            self.assert_contract(other_scope, 'economy')
            self.assertEqual(other_scope['snapshot_id'], full_again['snapshot_id'])
            self.assertEqual(other_scope['semantic']['team']['status'], 'not_read')
            with patch.object(perception, 'READ_CONTRACT_VERSION', 3):
                revised = reader.read(path, scope='economy')
                self.assertEqual(revised['read_contract']['version'], 3)
                self.assertFalse(revised['read_timing']['cache_hit'])

    def test_narrow_request_on_other_pages_keeps_full_effective_contract(self):
        for page in ('investment_summary', 'supply'):
            with self.subTest(page=page), declared_reader(page) as (reader, path):
                for scope in ('rewards', 'full', 'economy', 'rewards'):
                    observed = reader.read(path, scope=scope)
                    self.assertEqual(observed['page'], page)
                    self.assert_contract(observed, scope, 'full')
                    self.assertNotEqual((observed.get('state_read') or {}).get('status'), 'not_read')
                if page == 'investment_summary':
                    self.assertTrue(reader.state_reader.read.called)
                    self.assertEqual(observed['semantic']['team']['status'], 'unknown')
                with self.assertRaises(ValueError):
                    reader.read(path, scope='rewards_typo')


class WorkerReadScopeTests(unittest.TestCase):
    def test_root_request_upgrades_the_same_current_png_and_refuses_missing_or_old_identity(self):
        with protocol_fixture() as bundle:
            worker = bundle.worker
            reads, attempts = record_fixture_reads(worker)
            previous_full = worker.observe()
            previous_team = copy.deepcopy(worker.strategy_reads['team'])
            before = worker.observe(scope='rewards')
            self.assertEqual(before['snapshot_id'], previous_full['snapshot_id'])
            self.assertEqual(worker.strategy_reads['team'], previous_team)
            self.assertNotIn('team', worker.preparation_inputs(before)[1])
            count = len(bundle.control.published)
            worker.ask(before, 'preparation_strategy', 'explicit scope fixture full-field review')
            request = worker.state['decision_request']
            full = request['observation']
            self.assertEqual(full['read_contract']['effective_scope'], 'full')
            self.assertEqual(reads[-1]['scope'], 'full')
            self.assertFalse(reads[-1]['force'])
            self.assertTrue(reads[-1]['reuse_primary'])
            self.assertEqual(len(bundle.control.published), count)
            for key in ('snapshot_id', 'capture_request_id', 'frame_id', 'captured_at'):
                self.assertEqual(full[key], before[key])
            self.assertFalse(full['semantic']['team']['checked'])
            self.assertEqual(worker.preparation_reviews, {})
            self.assertEqual(before['semantic']['team']['status'], 'not_read')
            self.assertEqual(hashlib.sha256(Path(request['original_png']).read_bytes()).hexdigest(), before['snapshot_id'])
        for fault in ('missing_result', 'missing_png', 'wrong_request', 'same_pixels_new_frame'):
            with self.subTest(fault=fault), protocol_fixture() as bundle:
                worker = bundle.worker
                record_fixture_reads(worker)
                before = worker.observe(scope='rewards')
                if fault == 'missing_result':
                    worker.frame_result = None
                elif fault == 'missing_png':
                    worker.frame_path.unlink()
                elif fault == 'wrong_request':
                    before = copy.deepcopy(before)
                    before['capture_request_id'] = 'obsolete-request'
                else:
                    newer = worker.observe(scope='rewards')
                    self.assertEqual(newer['snapshot_id'], before['snapshot_id'])
                    self.assertNotEqual(newer['frame_id'], before['frame_id'])
                count = len(bundle.control.published)
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    worker.ask(before, 'preparation_strategy', 'must not silently attach another frame')
                self.assertEqual(len(bundle.control.published), count)
                self.assertIsNone(worker.state['decision_request'])

    def test_free_d_and_f_keep_economy_scope_for_result_and_read_only_frame_recovery(self):
        fixture = economy_fixture.EconomyTests()
        for action, missing_frame in ((action, missing) for action in ('D', 'F') for missing in (False, True)):
            with self.subTest(action=action, missing_frame=missing_frame):
                initial = values(free_refreshes=1) if action == 'D' else values(xp=[48, 52])
                after = values() if action == 'D' else values(coins=62, level=8, xp=[0, 72])
                with fixture.worker([after], initial=initial) as (worker, record, control, frames):
                    reads, attempts = record_fixture_reads(worker)
                    record['value']['budget']['experience'] = 0 if action == 'D' else 4
                    worker.accept_economy_plan(record)
                    with missing_first_mutation_frame(missing_frame) as missing:
                        worker.advance_economy(worker.last_observation)
                    mutations = [request for request in control.published
                        if any(item['type'] == 'key' for item in request.get('actions', []))]
                    self.assertEqual(len(mutations), 1)
                    self.assertEqual([item['args'] for item in mutations[0]['actions'] if item['type'] == 'key'],
                                     [[68. if action == 'D' else 70.]])
                    after_index = next(index for index, attempt in enumerate(attempts)
                                       if attempt['request_id'] == mutations[0]['id'])
                    self.assertEqual(attempts[after_index]['scope'], 'economy')
                    if missing_frame:
                        self.assertEqual(missing, [mutations[0]['id']])
                        self.assertEqual(attempts[after_index + 1]['scope'], 'economy')
                        self.assertNotEqual(attempts[after_index + 1]['request_id'], mutations[0]['id'])
                    ledger = worker.economy_ledger('2-3')
                    self.assertIsNone(ledger['pending'])
                    self.assertEqual(ledger['spent']['experience'], 0 if action == 'D' else 4)
                    self.assertEqual(ledger['spent']['refresh'], 0)
                    self.assertTrue(any(read['scope'] == 'economy' for read in reads))
        with self.subTest(path='public_purchase_entry'), fixture.worker() as (worker, record, control, frames):
            reads, attempts = record_fixture_reads(worker)
            worker.accept_economy_plan(record)
            observed = worker.observe(scope='economy')
            count = len(control.published)
            action = {'type': 'buy_shop', 'slot': 1, 'name': 'unreviewed-fixture-target', 'cost': 5,
                      'expected_page': 'shop', 'reason': 'full read must precede the existing purchase guard'}
            with self.assertRaises(ValueError):
                worker.execute_economic_action(action, observed, worker.state['decision_request'])
            # Full acquisition happened before the existing budget/target
            # guard refused this deliberately unreviewed buy; no buy is claimed.
            self.assertEqual(reads[-1]['scope'], 'full')
            self.assertFalse(reads[-1]['force'])
            self.assertTrue(reads[-1]['reuse_primary'])
            self.assertEqual(attempts[-1]['request_id'], observed['capture_request_id'])
            self.assertEqual(worker.last_observation['frame_id'], observed['frame_id'])
            self.assertEqual(worker.last_observation['snapshot_id'], observed['snapshot_id'])
            self.assertEqual(len(control.published), count)
            self.assertEqual(mutation_publications(control), 0)

    def test_economy_scope_keeps_live_guide_phase_and_honors_no_reroll_rule(self):
        with economy_fixture.EconomyTests().worker() as (worker, record, control, frames):
            record_fixture_reads(worker)
            record['value']['targets'] = [{'name': '飞霄', 'copies': 1, 'critical': False,
                                           'reason': 'declared current target fixture'}]
            record['value']['budget'] = {'purchase': 5, 'refresh': 2, 'experience': 0}
            record['value']['paid_search'].update(max_refreshes=1, purchase_reserve=5, targets=['飞霄'])
            worker.accept_economy_plan(record)
            guide = {'applied': True, 'body_read': True, 'mode_label': '标准博弈适用',
                     'body_lines': ['中期：7级搜牌'], 'operating_rules': {'no_reroll_phases': []}}
            worker.strategy_reads['guide'] = {'value': guide, 'match_id': worker.active_match_id,
                'resume_epoch': worker.epoch(), 'snapshot_id': 'declared-current-guide-fixture',
                'observed_at': runner.now()}
            observed = worker.observe(scope='economy')
            allowed = worker.economic_policy(observed)
            self.assertTrue(allowed['available'])
            self.assertEqual(allowed['guide_permission'], {'allowed': True, 'phase': 'transition'})
            self.assertEqual(allowed['actions'][0]['args'], [68])
            guide['operating_rules']['no_reroll_phases'] = ['transition']
            blocked = worker.economic_policy(observed)
            self.assertTrue(blocked['available'])
            self.assertEqual(blocked['guide_permission'], {'allowed': False, 'phase': 'transition'})
            self.assertEqual(blocked['actions'], [])
            self.assertEqual(mutation_publications(control), 0)

    def test_reward_results_keep_narrow_scope_until_root_full_field_review(self):
        for missing_frame in (False, True):
            with self.subTest(missing_frame=missing_frame), protocol_fixture() as bundle:
                worker, control = bundle.worker, bundle.control
                reads, attempts = record_fixture_reads(worker)
                before = worker.observe(scope='rewards')
                with missing_first_mutation_frame(missing_frame) as missing:
                    worker.tick(before)
                mutations = [request for request in control.published
                    if any(item['type'] == 'click' for item in request.get('actions', []))]
                self.assertEqual(mutation_publications(control), 2)
                for request in mutations:
                    after_index = next(index for index, attempt in enumerate(attempts)
                                       if attempt['request_id'] == request['id'])
                    self.assertEqual(attempts[after_index]['scope'], 'rewards')
                if missing_frame:
                    self.assertEqual(missing, [mutations[0]['id']])
                    first_index = next(index for index, attempt in enumerate(attempts)
                                       if attempt['request_id'] == missing[0])
                    self.assertEqual(attempts[first_index + 1]['scope'], 'rewards')
                    self.assertNotEqual(attempts[first_index + 1]['request_id'], missing[0])
                request = worker.state['decision_request']
                self.assertEqual(request['kind'], 'preparation_strategy')
                self.assertEqual(request['observation']['read_contract']['effective_scope'], 'full')
                self.assertEqual(worker.state['statistics']['decisions'], 1)
                self.assertEqual(worker.preparation_reviews, {})
                self.assertFalse(worker.preparation_checklist(worker.last_observation)['battle_ready'])
                self.assertEqual(reads[-1]['scope'], 'full')
                self.assertFalse(reads[-1]['force'])
                self.assertTrue(reads[-1]['reuse_primary'])


if __name__ == '__main__':
    unittest.main()
