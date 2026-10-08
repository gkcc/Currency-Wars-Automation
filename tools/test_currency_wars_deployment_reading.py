"""Declared native-reader protocol fixtures, not real game deployment evidence."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

import currency_wars_deployment as deployment
from currency_wars_perception import Perception
from currency_wars_state_reader import SCHEMA, StateReader, native_slots


def knowledge():
    return dict(origin='historical_observed_knowledge', sha256='a' * 64, error=None,
                roles={'风堇': {'position': '前台'}})


def action():
    return dict(type='deploy_unit', name='风堇', bench_slot=1, target={'row': 'front', 'slot': 4},
                capacity=8, position='前台', knowledge_sha256='a' * 64, expected_page='preparation')


def observation(*, moved=False, occupied=None, capacity=8, snapshot='b' * 64):
    slots = []
    for geometry in native_slots():
        selected = geometry['row'] == ('front' if moved else 'bench') and geometry['slot'] == (4 if moved else 1)
        relevant = (geometry['row'], geometry['slot']) in (('front', 4), ('bench', 1))
        slot = dict(geometry, snapshot_id=snapshot, origin='native_visual_state_reader',
                    status='occupied' if selected else 'empty' if relevant else 'unknown',
                    name='风堇' if selected else None, star=None,
                    position='前台' if selected else None, confidence=.96 if relevant else None,
                    evidence={}, reasons=[])
        if relevant:
            slot['evidence'] = dict(method='bounded_native_slot_templates', empty_score=.1 if selected else .96,
                badge_score=.96 if selected else .1, identity_score=.96 if selected else .4,
                identity_margin=.2 if selected else .03)
        if selected:
            slot['evidence']['identity'] = dict(kind='unit', name='风堇', file='declared-unit.png',
                                                source_sha256='d' * 64, score=.96)
        slots.append(slot)
    population = f'{occupied if occupied is not None else 8 if moved else 7}/{capacity}'
    return dict(page='preparation', snapshot_id=snapshot, fields={'stage': '3-3', 'deployed': population},
        rows=[dict(text='3-3', raw_text='3-3', confidence=.99, box=[430, 60, 510, 95]),
              dict(text=population, raw_text=population, confidence=.99, box=[880, 215, 1015, 275])],
        state_read=dict(schema=SCHEMA, snapshot_id=snapshot, origin='native_visual_state_reader',
            input=dict(sha256=snapshot, format='PNG', size=[1920, 1080]), resource_version='c' * 64,
            anchors={'front': .96, 'back': .95}, overlays=[],
            team=dict(snapshot_id=snapshot, slots=slots, checked=False, fully_read=False)))


class DeploymentReadingTests(unittest.TestCase):
    def setUp(self):
        self.plan = deployment.spec(action(), knowledge())

    def test_two_native_slots_and_independent_population_close_only_deployment(self):
        initial, final = observation(), observation(moved=True, snapshot='e' * 64)
        untouched = copy.deepcopy((initial, final))
        prior = deployment.before(initial, self.plan)
        result = deployment.after(final, self.plan, prior)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual((result['source']['status'], result['target']['name'], result['occupied']), ('empty', '风堇', 8))
        self.assertEqual((initial, final), untouched)
        self.assertFalse(final['state_read']['team']['checked'])
        self.assertNotIn('star', result['target'])
        self.assertNotIn('equipped', result)

    def test_cached_type_capacity_and_raw_coordinates_cannot_be_overridden(self):
        changes = ({'position': '后台'}, {'knowledge_sha256': 'f' * 64}, {'capacity': 11},
                   {'bench_slot': True}, {'args': []}, {'target': {'row': 'back', 'slot': 1}})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(deployment.DeploymentRejected):
                deployment.spec({**action(), **change}, knowledge())
        for change in ({'origin': 'supervising_agent'}, {'error': 'unreadable'}, {'sha256': None}):
            with self.subTest(change=change), self.assertRaises(deployment.DeploymentRejected):
                deployment.spec(action(), {**knowledge(), **change})

    def test_capacity_old_frame_and_completed_without_result_remain_unverified(self):
        self.assertEqual(deployment.intent(observation(occupied=7, capacity=7), self.plan)['capacity'], 7)
        with self.assertRaises(deployment.DeploymentUnreadable):
            deployment.before(observation(occupied=7, capacity=7), self.plan)
        prior = deployment.before(observation(), self.plan)
        for fresh in (observation(), observation(occupied=8), observation(moved=True, occupied=7)):
            fresh['receipt'] = {'completed': [{'type': 'drag'}]}
            with self.subTest(moved=fresh['state_read']['team']['slots'][3]['status']), self.assertRaises(deployment.DeploymentUnreadable):
                deployment.after(fresh, self.plan, prior)

    def test_page_geometry_overlay_stage_and_source_changes_reject(self):
        prior = deployment.before(observation(), self.plan)
        for fault in ('page', 'geometry', 'overlay', 'stage', 'snapshot', 'resource', 'capacity'):
            fresh = observation(moved=True, snapshot='e' * 64)
            if fault == 'page':
                fresh['page'] = 'shop'
            elif fault == 'geometry':
                fresh['state_read']['team']['slots'][3]['point'][0] += 5
            elif fault == 'overlay':
                fresh['state_read']['overlays'] = [[600, 250, 1200, 800]]
            elif fault == 'stage':
                fresh['fields']['stage'] = fresh['rows'][0]['text'] = fresh['rows'][0]['raw_text'] = '3-4'
            elif fault == 'snapshot':
                fresh['state_read']['team']['slots'][3]['snapshot_id'] = 'f' * 64
            elif fault == 'resource':
                fresh['state_read']['resource_version'] = 'f' * 64
            else:
                fresh = observation(moved=True, occupied=8, capacity=9)
            with self.subTest(fault=fault), self.assertRaises(deployment.DeploymentRejected):
                deployment.after(fresh, self.plan, prior)

    def test_population_or_name_alone_cannot_certify_correct_role_and_destination(self):
        prior = deployment.before(observation(), self.plan)
        for fault in ('name', 'position', 'identity', 'empty_conflict', 'duplicate', 'hud_conflict'):
            fresh = observation(moved=True, snapshot='e' * 64)
            slot = fresh['state_read']['team']['slots'][3]
            if fault == 'name':
                slot['name'] = '其他角色'
            elif fault == 'position':
                slot['position'] = '后台'
            elif fault == 'identity':
                slot['evidence']['identity']['source_sha256'] = None
            elif fault == 'empty_conflict':
                slot['evidence']['empty_score'] = .95
            elif fault == 'duplicate':
                fresh['state_read']['team']['slots'].append(copy.deepcopy(slot))
            else:
                fresh['fields']['deployed'] = '7/8'
            with self.subTest(fault=fault), self.assertRaises(deployment.DeploymentRejected):
                deployment.after(fresh, self.plan, prior)

    def test_missing_resources_and_unknown_target_are_not_filled_from_plan(self):
        prior = deployment.before(observation(), self.plan)
        for fault in ('resources', 'name', 'status', 'hud'):
            fresh = observation(moved=True, snapshot='e' * 64)
            slot = fresh['state_read']['team']['slots'][3]
            if fault == 'resources':
                fresh['state_read']['resource_version'] = None
            elif fault == 'name':
                slot['name'] = None
            elif fault == 'status':
                slot['status'] = 'unknown'
            else:
                fresh['rows'][1]['confidence'] = .5
            original = copy.deepcopy(fresh)
            with self.subTest(fault=fault), self.assertRaises(deployment.DeploymentUnreadable):
                deployment.after(fresh, self.plan, prior)
            self.assertEqual(fresh, original)

    def test_projection_preserves_rederivation_and_current_capture_binding(self):
        initial = observation()
        initial.update(capture_request_id='capture', frame_id='frame', captured_at='current-time')
        initial['rows'].append(dict(text='unrelated', raw_text='unrelated', confidence=.99, box=[1700, 1000, 1890, 1050]))
        projected = deployment.project(initial, self.plan)
        self.assertEqual(deployment.before(projected, self.plan), deployment.before(initial, self.plan))
        self.assertEqual(len(projected['state_read']['team']['slots']), 2)
        self.assertEqual(len(projected['rows']), 2)
        self.assertEqual([projected[k] for k in ('capture_request_id', 'frame_id', 'captured_at')],
                         ['capture', 'frame', 'current-time'])
        final = observation(moved=True, snapshot='e' * 64)
        prior = deployment.before(initial, self.plan)
        self.assertEqual(deployment.after(deployment.project(final, self.plan), self.plan, prior),
                         deployment.after(final, self.plan, prior))

    def test_native_rounded_scores_and_ambiguous_stage_keep_original_contract(self):
        initial = observation()
        empty = initial['state_read']['team']['slots'][3]
        empty['evidence'].update(badge_score=.65, identity_score=.90, identity_margin=.20)
        prior = deployment.before(initial, self.plan)
        final = observation(moved=True, snapshot='e' * 64)
        final['state_read']['team']['slots'][3]['evidence']['empty_score'] = .90
        self.assertEqual(deployment.after(final, self.plan, prior)['status'], 'verified')
        final['rows'].append({**final['rows'][0], 'text': '3-4', 'raw_text': '3-4'})
        with self.assertRaises(deployment.DeploymentUnreadable):
            deployment.after(final, self.plan, prior)


class DeploymentScopeTests(unittest.TestCase):
    """Scoped reader calls with declared matches; retained PNG is negative only."""
    selection = [{'row': 'bench', 'slot': 1}, {'row': 'front', 'slot': 4}]

    def reader(self):
        reader = StateReader()
        reader._version = 'c' * 64
        reader._load = Mock(return_value=True)
        reader._overlays = Mock(return_value=[])
        reader._inventory = Mock(side_effect=AssertionError('deployment must not read inventory'))
        active = {'source': False}

        def match(rgb, kind, name=None, scales=(1.,)):
            if kind in ('star', 'starbar'):
                raise AssertionError('deployment must not read stars')
            if kind == 'empty':
                active['source'] = name == 'bench_1'
            score = .97 if kind == 'anchor' or kind == 'empty' and not active['source'] else .1
            if kind == 'badge':
                return [(.96 if active['source'] else .1, {'name': '前台'}, None)]
            if kind == 'unit':
                identity = dict(kind='unit', name='风堇', file='declared-unit.png', source_sha256='d' * 64)
                return [(.96 if active['source'] else .4, identity, None),
                        (.55 if active['source'] else .38, {**identity, 'name': '其他角色'}, None)]
            return [(score, {'name': name}, None)]

        reader._match = Mock(side_effect=match)
        reader._slot = Mock(wraps=reader._slot)
        return reader

    @staticmethod
    def write_png(path, color=(180, 80, 180)):
        with Image.new('RGB', (1920, 1080), color) as image:
            image.save(path)

    @staticmethod
    def ocr_rows():
        rows = observation()['rows'] + [dict(text='备战阶段', confidence=.99, box=[420, 25, 530, 60]),
            dict(text='出战', confidence=.99, box=[1770, 720, 1860, 780]),
            dict(text='商店', confidence=.99, box=[1585, 960, 1660, 1005])]
        return [([[row['box'][0] / 1.5, row['box'][1] / 1.5], [row['box'][2] / 1.5, row['box'][1] / 1.5],
                  [row['box'][2] / 1.5, row['box'][3] / 1.5], [row['box'][0] / 1.5, row['box'][3] / 1.5]],
                 row['text'], row['confidence']) for row in rows]

    def test_actual_slot_producer_reads_only_two_selected_slots_without_stars(self):
        reader = self.reader()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'declared.png'
            self.write_png(path)
            state = reader.read(path, page='preparation', selection=self.selection)
        self.assertEqual(reader._slot.call_count, 2)
        self.assertTrue(all(call.kwargs == {'read_stars': False} for call in reader._slot.call_args_list))
        self.assertEqual(sum(slot['status'] == 'not_read' for slot in state['team']['slots']), 17)
        self.assertFalse(state['team']['fully_read'])
        self.assertFalse(state['team']['capacity']['bench_checked'])
        self.assertEqual(state['inventory']['status'], 'not_read')
        current = observation(snapshot=state['snapshot_id'])
        current['state_read'] = state
        self.assertEqual(deployment.before(current, deployment.spec(action(), knowledge()))['occupied'], 7)

    def test_perception_scope_selection_and_current_png_are_in_cache_key(self):
        reader = Perception()
        reader.engine = Mock(return_value=(self.ocr_rows(), None))
        reader.state_reader = self.reader()
        reader.shop_reader = Mock(read=Mock(side_effect=AssertionError('unexpected shop read')))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'declared.png'
            self.write_png(path)
            first = reader.read(path, scope='deployment', deployment_slots=self.selection)
            cached = reader.read(path, scope='deployment', deployment_slots=list(reversed(self.selection)))
            other = reader.read(path, scope='deployment', deployment_slots=[self.selection[0], {'row': 'front', 'slot': 3}])
            self.write_png(path, color=(181, 80, 180))
            fresh = reader.read(path, scope='deployment', deployment_slots=self.selection)
        self.assertTrue(cached['read_timing']['cache_hit'])
        self.assertNotEqual(first['read_contract']['deployment_slots'], other['read_contract']['deployment_slots'])
        self.assertNotEqual(first['snapshot_id'], fresh['snapshot_id'])
        self.assertEqual(reader.engine.call_count, 3)
        self.assertEqual(reader.state_reader._slot.call_count, 6)
        self.assertFalse(fresh['read_timing']['primary_ocr_reused'])
        self.assertEqual(fresh['fields']['deployed'], '7/8')
        self.assertEqual(fresh['semantic']['player_hud']['status'], 'not_read')
        self.assertEqual(fresh['read_contract']['page_ocr'], 'full_frame')

    def test_selection_does_not_accept_missing_slots_duplicates_or_geometry(self):
        reader = Perception()
        for selection in (None, [], [self.selection[0]], [self.selection[0]] * 2,
                          [self.selection[0], {'row': 'bench', 'slot': 2}],
                          [self.selection[0], {'row': 'front', 'slot': 5}],
                          [self.selection[0], {'row': 'front', 'slot': True}],
                          [self.selection[0], {'row': 'front', 'slot': 4, 'bounds': [0, 0, 1, 1]}]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                reader.read('not-opened.png', scope='deployment', deployment_slots=selection)
        with self.assertRaises(ValueError):
            reader.read('not-opened.png', scope='full', deployment_slots=self.selection)

    def test_unknown_page_keeps_existing_full_read_route_and_cannot_deploy(self):
        reader = Perception()
        reader.engine = Mock(return_value=([], None))
        reader.state_reader = self.reader()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unknown.png'
            self.write_png(path)
            current = reader.read(path, scope='deployment', deployment_slots=self.selection)
        self.assertEqual(current['page'], 'unknown')
        self.assertEqual(current['read_contract']['effective_scope'], 'full')
        reader.state_reader._slot.assert_not_called()
        with self.assertRaises(deployment.DeploymentRejected):
            deployment.before(current, deployment.spec(action(), knowledge()))

    def test_retained_png_with_explicitly_missing_resources_keeps_unknown(self):
        path = Path(__file__).resolve().parents[1] / 'handoff/2026-10-07/reward-sequence/q02.png'
        with tempfile.TemporaryDirectory() as directory:
            state = StateReader(resources=Path(directory) / 'absent').read(path, page='preparation', selection=self.selection)
        self.assertEqual(state['snapshot_id'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertIsNone(state['resource_version'])
        self.assertEqual(sum(slot['status'] == 'unknown' for slot in state['team']['slots']), 2)
        self.assertEqual(sum(slot['status'] == 'not_read' for slot in state['team']['slots']), 17)
        self.assertFalse(state['team']['fully_read'])


if __name__ == '__main__':
    unittest.main()
