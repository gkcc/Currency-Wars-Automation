"""Ten bounded checkpoint/consumer checks with inert Entry receipts.

All page images and OCR below are synthetic protocol fixtures. No game,
desktop controller, native OCR or claim about visual coverage is involved.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from PIL import Image, ImageDraw

import currency_wars_runner as runner
import test_currency_wars_business as business_fixture
import test_currency_wars_economy as economics


PANELS = tuple(panel for panel, unused in runner.PANELS)


class InspectionCheckpointTests(TestCase):
    def page(self, worker, control, page, *, observe=True):
        """Change only the inert fixture's image and factual reader output."""
        image = Image.new('RGB', (1920, 1080), (20, 20, 20))
        ImageDraw.Draw(image).text((30, 30), 'inspection fixture: ' + page, fill='white')
        output = io.BytesIO()
        image.save(output, format='PNG')
        payload = output.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        labels = ['货币战争', '标准博弈'] + [label for unused, label in runner.PANELS]
        if page == 'settlement':
            labels.append('对局胜利')
        value = {'snapshot_id': digest, 'page': page,
            'fields': {'stage': None, 'deployed': None, 'level': None},
            'rows': [{'text': text, 'confidence': .99, 'box': [100, 100 + index * 45, 350, 135 + index * 45]}
                     for index, text in enumerate(labels)],
            'semantic': {}, 'elapsed_ms': 0., 'fingerprint': '0' * 64,
            'read_contract': {'version': runner.READ_CONTRACT_VERSION, 'requested_scope': 'full',
                              'effective_scope': 'full', 'unread': []}}
        control.inspection_frames[digest] = value
        control.frame = payload
        if observe:
            worker.observe()
        return value

    @contextlib.contextmanager
    def fixture(self, *, settled=False):
        with economics.EconomyTests().worker() as (worker, unused, control, frames):
            business_fixture.BusinessTests().bind(worker, control)
            worker.args = SimpleNamespace(max_matches=20)
            worker.state['statistics']['matches_confirmed'] = 0
            worker.wait_page, worker.wait_started = None, None
            worker.node_key, worker.node_attempts, worker.node_started = None, 0, None
            worker.node_last_page, worker.node_consecutive = None, 0
            worker.node_progress, worker.node_progress_seen = 0, 0
            worker.shop_stages, worker.reroll_attempted = set(), set()
            worker.free_lineup_attempted, worker.node_result_attempted, worker.loot_pickup_attempted = set(), set(), set()
            control.inspection_frames, control.next_pages, control.inspection_inputs = {}, [], []
            worker.perception.read = lambda path, **unused: copy.deepcopy(
                control.inspection_frames[hashlib.sha256(Path(path).read_bytes()).hexdigest()])
            publish = control.publish_request

            def inert_publication(request):
                physical = [action for action in request.get('actions', [])
                            if action['type'] not in ('observe', 'wait')]
                payload = control.frame
                if physical:
                    control.inspection_inputs.append(copy.deepcopy(request))
                    if control.next_pages:
                        self.page(worker, control, control.next_pages.pop(0), observe=False)
                        payload = control.frame
                publish(request)
                # The underlying Entry fixture turns clicks into a white PNG.
                # Supply our declared synthetic page before Entry consumes the
                # result; request/actions, frame identity and receipt stay real.
                control.frame = payload
                result_path = worker.run / 'result.json'
                result = runner.entry.read_json(result_path)
                frame = result['observation']
                for field in ('snapshot', 'original'):
                    Path(frame[field]).write_bytes(payload)
                frame['snapshot_sha256'] = frame['original_sha256'] = hashlib.sha256(payload).hexdigest()
                control.write_json(result_path, result)

            control.publish_request = inert_publication
            self.page(worker, control, 'lobby')
            if settled:
                self.confirm_settlement(worker, control)
            yield worker, control

    def request(self, worker, control, page, kind):
        self.page(worker, control, page)
        worker.state['decision_request'] = None
        worker.ask(worker.last_observation, kind, '合成页面协议：保留当前真实惰性收据与不可变原图')
        return worker.state['decision_request']

    def finish_reply(self, worker, control, panel):
        worker.panel_index, worker.panel_state = PANELS.index(panel), 'inspect'
        request = self.request(worker, control, panel, 'post_match_' + panel)
        coverage = {'tabs': ['常驻优势', '赛季优势'] if panel == 'advantages' else [dict(runner.PANELS)[panel]],
                    'all_tabs_reviewed': True, 'scroll_complete': True,
                    'claimable_remaining': False, 'pending': False}
        if panel == 'advantages':
            coverage['allocation_reviewed'] = True
        result = {'evidence': request['evidence_file'], 'inspection_contract': runner.INSPECTION_CONTRACT,
                  'coverage': coverage, 'findings': '合成协议核查；不宣称真实游戏面板已读或奖励已领取'}
        return {'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': worker.epoch(), 'actions': [{'type': 'finish_inspection', 'panel': panel,
                    'reason': '只持久化该合成面板核查输出，不发送游戏输入', 'result': result}]}

    def finish_all(self, worker, control):
        for panel in PANELS:
            worker.execute_plan(self.finish_reply(worker, control, panel))
        self.assertEqual(set(worker.inspections), set(PANELS))
        return copy.deepcopy(worker.business['inspections']['panels'])

    def confirm_settlement(self, worker, control):
        request = self.request(worker, control, 'settlement', 'settlement_verify')
        reply = {'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                 'resume_epoch': worker.epoch(), 'actions': [{'type': 'confirm_match_result',
                     'reason': '合成整局结算协议，不计作真实游戏成绩',
                     'result': {'mode': '标准博弈', 'outcome': '对局胜利', 'evidence': request['evidence_file']}}]}
        worker.execute_plan(reply)

    def successor(self, old, control, page='lobby'):
        old.inspections['not-persisted'] = {'memory_only': True}
        old.panel_index, old.panel_state = len(PANELS), 'return'
        # A historical unresolved request forbids a further old-run observe.
        # Only choose the new lease's synthetic frame; successor captures it.
        self.page(old, control, page, observe=False)
        contract = runner.prepare_business_start(old.state, control)
        fresh = business_fixture.BusinessTests().successor(old, control, contract)
        # BusinessTests.successor uses copy.copy. These assertions specifically
        # prove production initialize_business discarded that shallow state.
        self.assertEqual(fresh.inspections, {})
        self.assertEqual((fresh.panel_index, fresh.panel_state), (0, 'enter'))
        self.assertEqual(fresh.state['decision_request']['kind'], 'business_resume')
        self.assertTrue(fresh.business_needs_review)
        self.assertEqual(fresh.preparation_reviews, {})
        self.assertIsNone(fresh.economy_binding)
        # The older helper predates this statistic, while a real fresh Worker
        # always starts its own bounded lease counter at zero.
        fresh.state['statistics']['matches_confirmed'] = 0
        return fresh

    def review(self, worker, *, changed=(), unknown=(), disposition=None):
        request = worker.state['decision_request']
        result = business_fixture.BusinessTests().review(worker)
        if disposition is None:
            disposition = ('new_match' if request['observation']['page'] == 'lobby'
                           and worker.business['status'] != 'completed' else 'same_match')
        result['value'].update(disposition=disposition,
            current_state={'page': request['observation']['page'], 'visible_labels': ['货币战争']})
        offered = request['inspection_checkpoint']
        result['value']['inspection_resume'] = {'contract': runner.INSPECTION_CONTRACT,
            'checkpoint_revision': offered['revision'], 'settlement_sha256': offered['settlement_sha256'],
            'settlement_change': 'unchanged',
            'basis': {'source': 'supervising_agent_continuity', 'through_snapshot_id': request['snapshot_id'],
                      'unobserved_interval': False, 'details': '合成协议中持续核对原收据和当前面板，无未观察时段；不是原生红点读取'},
            'reuse': {panel: offered['panels'][panel]['record_sha256']
                      for panel in PANELS if panel not in set(changed) | set(unknown)},
            'changed': list(changed), 'unknown': list(unknown)}
        return result

    def submit(self, worker, review):
        business_fixture.BusinessTests().submit(worker, review)

    def test_four_durable_sources_resume_once_to_new_match_without_panel_inputs(self):
        with self.fixture(settled=True) as (worker, control):
            originals = self.finish_all(worker, control)
            for panel, item in originals.items():
                source = item['source']
                self.assertEqual(hashlib.sha256(Path(source['original_png']).read_bytes()).hexdigest(), source['snapshot_id'])
                self.assertTrue(Path(source['receipt_file']).is_file())
                self.assertTrue(Path(item['record_file']).is_file())
                self.assertEqual(source['origin_run_id'], worker.owner['run_id'])
                self.assertEqual(item['result']['evidence'], source['evidence_file'])
            runtime_only = worker.run / 'old-runtime-marker'
            runtime_only.write_text('must disappear', encoding='utf8')
            fresh = self.successor(worker, control)
            self.assertFalse(runtime_only.exists())
            self.assertTrue(all(item['eligible'] for item in fresh.inspection_candidates()['panels'].values()))
            self.submit(fresh, self.review(fresh))
            self.assertEqual(set(fresh.inspections), set(PANELS))
            self.assertEqual(fresh.preparation_reviews, {})
            self.assertIsNone(fresh.economy_binding)
            self.assertFalse((fresh.run / 'runner-battle-approval.json').exists())
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.state['decision_request']['kind'], 'new_match')
            self.assertEqual(control.inspection_inputs, [])
            self.assertEqual(fresh.business['inspections']['panels'], originals)

    def test_one_changed_income_returns_from_advantages_then_enters_only_income(self):
        with self.fixture(settled=True) as (worker, control):
            self.finish_all(worker, control)
            fresh = self.successor(worker, control, 'advantages')
            self.submit(fresh, self.review(fresh, changed=['income']))
            self.assertEqual(set(fresh.inspections), set(PANELS) - {'income'})
            control.next_pages[:] = ['lobby', 'income']
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.last_observation['page'], 'lobby')
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.last_observation['page'], 'income')
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.state['decision_request']['kind'], 'post_match_income')
            self.assertEqual(len(control.inspection_inputs), 2)
            self.assertEqual(control.inspection_inputs[0]['actions'][0], {'type': 'key', 'args': [27.]})
            self.assertEqual(control.inspection_inputs[1]['actions'][0]['type'], 'click')

    def test_current_unreviewed_income_requests_review_without_reopening(self):
        with self.fixture(settled=True) as (worker, control):
            self.finish_all(worker, control)
            fresh = self.successor(worker, control, 'income')
            self.submit(fresh, self.review(fresh, unknown=['income']))
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.state['decision_request']['kind'], 'post_match_income')
            self.assertEqual(control.inspection_inputs, [])
            self.assertNotIn('income', fresh.business['inspections']['panels'])

    def test_finish_is_single_action_and_missing_coverage_never_becomes_eligible(self):
        for variant in ('extra_action', 'missing_original', 'legacy', 'tabs', 'pending', 'allocation',
                        'late_pending', 'late_input'):
            with self.subTest(variant=variant), self.fixture() as (worker, control):
                panel = 'advantages' if variant == 'allocation' else 'bonds'
                reply = self.finish_reply(worker, control, panel)
                result = reply['actions'][0]['result']
                if variant == 'extra_action':
                    reply['actions'].append({'type': 'key', 'args': [27], 'expected_page': panel,
                                            'guard_texts': ['货币战争'], 'reason': '不得与完成记录混批'})
                elif variant == 'missing_original':
                    Path(worker.state['decision_request']['original_png']).unlink()
                elif variant == 'legacy':
                    result.pop('inspection_contract')
                    result.pop('coverage')
                elif variant == 'tabs':
                    result['coverage']['tabs'] = []
                elif variant == 'pending':
                    result['coverage']['pending'] = True
                elif variant == 'allocation':
                    result['coverage'].pop('allocation_reviewed')
                if variant.startswith('late_'):
                    verify_source = worker.inspection_source

                    def verify_then_deliver(panel, stored):
                        verify_source(panel, stored)
                        # Same-owner read-only requests are supported even
                        # when no manual/epoch change has occurred.
                        rid = 'late-inspection-observe'
                        runner.entry.request(control, 'actions', ['observe'], rid, False)
                        path = worker.run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
                        item = runner.entry.read_json(path)
                        if variant == 'late_pending':
                            item['result'] = None
                            # Neither delivery store has a terminal result.
                            # Leaving result.json would correctly reconcile
                            # the ledger and would not model pending at all.
                            latest = worker.run / 'result.json'
                            self.assertEqual(runner.entry.read_json(latest)['id'], rid)
                            latest.unlink()
                        else:
                            item['result'].update(input_attempted=True, attempted_actions=[{'type': 'key', 'args': [27.]}])
                        control.write_json(path, item)

                    before = worker.business_path.read_bytes()
                    with patch.object(worker, 'inspection_source', side_effect=verify_then_deliver):
                        with self.assertRaises((ValueError, RuntimeError)):
                            worker.execute_plan(reply)
                    self.assertEqual(worker.business_path.read_bytes(), before)
                    self.assertNotIn(panel, worker.inspections)
                    self.assertEqual(control.inspection_inputs, [])
                    continue
                if variant != 'legacy':
                    with self.assertRaises((ValueError, OSError)):
                        worker.execute_plan(reply)
                else:
                    worker.execute_plan(reply)
                    self.assertEqual(worker.inspections[panel], result)
                self.assertFalse(worker.inspection_candidates()['panels'][panel]['eligible'])
                self.assertEqual(control.inspection_inputs, [])

    def test_missing_png_corrupt_record_and_foreign_lease_cannot_resume(self):
        for variant in ('png', 'record', 'origin', 'checkpoint_contract', 'checkpoint_shape', 'checkpoint_revision'):
            with self.subTest(variant=variant), self.fixture(settled=True) as (worker, control):
                self.finish_all(worker, control)
                source = worker.business['inspections']['panels']['bonds']
                if variant == 'png':
                    Path(source['source']['original_png']).unlink()
                elif variant == 'record':
                    Path(source['record_file']).write_bytes(b'{}')
                elif variant == 'origin':
                    source['source']['origin_run_id'] = 'unrelated-owner-run'
                    worker.save_business()
                else:
                    if variant == 'checkpoint_contract':
                        worker.business['inspections']['contract'] = 'unsupported-contract'
                    elif variant == 'checkpoint_shape':
                        worker.business['inspections'] = ['unsupported-shape']
                    else:
                        worker.business['inspections']['revision'] = 'unknown-revision'
                    worker.save_business()
                fresh = self.successor(worker, control)
                self.assertFalse(fresh.state['decision_request']['inspection_checkpoint']['panels']['bonds']['eligible'])
                with self.assertRaises(ValueError):
                    self.submit(fresh, self.review(fresh))
                self.assertTrue(fresh.business_needs_review)
                self.assertEqual(fresh.inspections, {})
                self.assertEqual(control.inspection_inputs, [])
                if variant.startswith('checkpoint_'):
                    self.submit(fresh, self.review(fresh, unknown=PANELS))
                    self.assertEqual(fresh.business['inspections']['panels'], {})
                    fresh.execute_plan(self.finish_reply(fresh, control, 'bonds'))
                    self.assertTrue(fresh.inspection_candidates()['panels']['bonds']['eligible'])

    def test_pending_receipt_watermark_and_unknown_declarations_do_not_authorize_reuse(self):
        for variant in ('pending', 'watermark', 'settlement_unknown', 'interval', 'overlap', 'omitted',
                        'basis_null', 'basis_flag', 'all_unknown_gap'):
            with self.subTest(variant=variant), self.fixture(settled=True) as (worker, control):
                self.finish_all(worker, control)
                if variant == 'pending':
                    runner.entry.request(control, 'actions', ['observe'], 'unresolved-original', False)
                    path = worker.run / 'request-ledger' / (hashlib.sha256(b'unresolved-original').hexdigest() + '.json')
                    item = runner.entry.read_json(path)
                    item['result'] = None
                    control.write_json(path, item)
                fresh = self.successor(worker, control)
                review = self.review(fresh)
                value = review['value']['inspection_resume']
                if variant == 'watermark':
                    # No physical request bypasses the runner-owned guard.
                    # A newly delivered observe with contradictory attempted
                    # input is an explicit negative protocol declaration.
                    runner.entry.request(control, 'actions', ['observe'], 'intervening-observe', False)
                    path = fresh.run / 'request-ledger' / (hashlib.sha256(b'intervening-observe').hexdigest() + '.json')
                    item = runner.entry.read_json(path)
                    item['result'].update(input_attempted=True, attempted_actions=[{'type': 'key', 'args': [27.]}])
                    control.write_json(path, item)
                elif variant == 'settlement_unknown':
                    value['settlement_change'] = 'unknown'
                elif variant == 'interval':
                    value['basis']['unobserved_interval'] = True
                elif variant == 'overlap':
                    value['changed'] = ['income']
                elif variant == 'omitted':
                    review['value'].pop('inspection_resume')
                elif variant == 'basis_null':
                    value['basis'] = None
                elif variant == 'basis_flag':
                    value['basis']['unobserved_interval'] = 'unknown'
                elif variant == 'all_unknown_gap':
                    value.update(reuse={}, unknown=list(PANELS), settlement_change='unknown')
                    value['basis']['unobserved_interval'] = True
                if variant in ('omitted', 'all_unknown_gap'):
                    self.submit(fresh, review)
                    self.assertFalse(fresh.business_needs_review)
                    self.assertEqual(fresh.business['inspections']['panels'], {})
                    self.assertEqual(fresh.business['settlement'], worker.business['settlement'])
                else:
                    with self.assertRaises((ValueError, RuntimeError)):
                        self.submit(fresh, review)
                    self.assertTrue(fresh.business_needs_review)
                self.assertEqual(fresh.inspections, {})
                self.assertEqual(control.inspection_inputs, [])
                if variant == 'pending':
                    prior_unknown = copy.deepcopy(fresh.business_unknown)
                    self.assertTrue(prior_unknown)
                    # Historical uncertainty forbids reuse, but must not block
                    # recovery through a new, current-source panel inspection.
                    self.submit(fresh, self.review(fresh, unknown=PANELS))
                    fresh.execute_plan(self.finish_reply(fresh, control, 'bonds'))
                    self.assertIn('bonds', fresh.inspections)
                    self.assertEqual(fresh.business_unknown, prior_unknown)
                    self.assertEqual(fresh.business['inspections']['panels']['bonds']['source']['origin_run_id'],
                                     fresh.owner['run_id'])
                    self.assertFalse(fresh.inspection_candidates()['panels']['bonds']['eligible'])
                    self.assertEqual(control.inspection_inputs, [])

    def test_stop_epoch_and_cas_changes_refuse_before_enabling_memory(self):
        for variant in ('stop', 'epoch', 'cas', 'late_stop', 'late_deadline', 'late_pending'):
            with self.subTest(variant=variant), self.fixture(settled=True) as (worker, control):
                self.finish_all(worker, control)
                fresh = self.successor(worker, control)
                review = self.review(fresh)
                if variant == 'stop':
                    (fresh.run / 'runner-stop').write_text('stop', encoding='utf8')
                elif variant == 'epoch':
                    fresh.epoch = lambda: 'newer-epoch'
                elif variant == 'cas':
                    current = runner.entry.read_json(fresh.business_path)
                    current['revision'] += 1
                    control.write_json(fresh.business_path, current)
                else:
                    write = control.write_json

                    def late_change(path, value):
                        write(path, value)
                        if Path(path).name.endswith('-business-resume.json'):
                            if variant == 'late_stop':
                                (fresh.run / 'runner-stop').touch()
                            elif variant == 'late_deadline':
                                fresh.state['decision_request']['deadline_at'] = '2000-01-01T00:00:00+00:00'
                            else:
                                fresh.business_unknown.append({'origin_run_id': fresh.owner['run_id'],
                                                               'request_id': 'inert-late-unknown'})

                    control.write_json = late_change
                before = fresh.business_path.read_bytes()
                with self.assertRaises((ValueError, RuntimeError)):
                    self.submit(fresh, review)
                self.assertEqual(fresh.business_path.read_bytes(), before)
                self.assertTrue(fresh.business_needs_review)
                self.assertEqual(fresh.inspections, {})
                self.assertEqual(control.inspection_inputs, [])

    def test_new_settlement_commits_clear_atomically_and_write_failure_keeps_old_counts(self):
        for fail in (False, True):
            with self.subTest(write_failure=fail), self.fixture() as (worker, control):
                self.finish_all(worker, control)
                before = worker.business_path.read_bytes()
                writes = []
                original_write = control.write_json

                def write(path, value):
                    if Path(path) == worker.business_path:
                        writes.append(copy.deepcopy(value))
                        self.assertEqual(value['status'], 'completed')
                        self.assertEqual(value['inspections']['panels'], {})
                        self.assertTrue(value['settlement']['snapshot_id'])
                        if fail:
                            raise OSError('injected checkpoint write failure')
                    return original_write(path, value)

                with patch.object(control, 'write_json', side_effect=write):
                    if fail:
                        with self.assertRaisesRegex(OSError, 'checkpoint write failure'):
                            self.confirm_settlement(worker, control)
                    else:
                        self.confirm_settlement(worker, control)
                self.assertEqual(len(writes), 1)
                if fail:
                    self.assertEqual(worker.business_path.read_bytes(), before)
                    self.assertEqual(worker.state['statistics']['matches_confirmed'], 0)
                    self.assertFalse(worker.match_result_confirmed)
                    self.assertNotIn(worker.active_match_id, worker.consumed_match_results)
                else:
                    actual = runner.entry.read_json(worker.business_path)
                    self.assertEqual(actual['status'], 'completed')
                    self.assertEqual(actual['inspections']['panels'], {})
                    self.assertEqual(worker.state['statistics']['matches_confirmed'], 1)
                    self.assertEqual(worker.inspections, {})

    def test_lobby_new_match_intent_preserves_unresolved_then_setup_starts_empty_business(self):
        with self.fixture() as (worker, control):
            self.finish_all(worker, control)
            fresh = self.successor(worker, control)
            old_match, old_path = fresh.active_match_id, fresh.business_path
            self.submit(fresh, self.review(fresh, disposition='new_match'))
            self.assertEqual(fresh.active_match_id, old_match)
            self.assertEqual(runner.entry.read_json(old_path)['status'], 'unresolved')
            self.assertEqual(set(fresh.inspections), set(PANELS))
            self.assertEqual(fresh.state['statistics'].get('matches_confirmed', 0), 0)
            fresh.tick(fresh.last_observation)
            request = fresh.state['decision_request']
            self.assertEqual(request['kind'], 'new_match')
            control.next_pages[:] = ['investment']
            fresh.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': fresh.epoch(), 'actions': [{'type': 'key', 'args': [13],
                    'expected_page': 'lobby', 'guard_texts': ['标准博弈'], 'reason': '合成实际设置页才轮换业务身份'}]})
            self.assertNotEqual(fresh.active_match_id, old_match)
            self.assertEqual(fresh.business['status'], 'active')
            self.assertEqual(fresh.business['inspections']['panels'], {})
            self.assertEqual(fresh.inspections, {})
            self.assertEqual(runner.entry.read_json(old_path)['status'], 'unresolved')
            self.assertEqual(len(control.inspection_inputs), 1)

    def test_unknown_current_page_still_uses_existing_exception_request(self):
        with self.fixture(settled=True) as (worker, control):
            self.finish_all(worker, control)
            fresh = self.successor(worker, control)
            self.submit(fresh, self.review(fresh))
            self.page(fresh, control, 'unknown')
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.state['decision_request']['kind'], 'unknown_page')
            self.assertEqual(control.inspection_inputs, [])
            self.assertEqual(set(fresh.inspections), set(PANELS))
            retained = fresh.business_path.read_bytes()
            fresh.epoch = lambda: 'manual-new-epoch'
            fresh.state['decision_request'] = None
            fresh.tick(fresh.last_observation)
            self.assertEqual(fresh.inspections, {})
            self.assertEqual(fresh.state['inspection_results'], {})
            self.assertEqual(fresh.business_path.read_bytes(), retained)
            self.assertEqual(fresh.state['decision_request']['kind'], 'unknown_page')
            self.assertEqual(control.inspection_inputs, [])


if __name__ == '__main__':
    import unittest
    unittest.main()
