"""New reward/manual boundaries only; retained pixels plus inert protocols.

No native game input, model service, installed candidate, or continuous real
reward sequence is exercised. Imported old test modules supply fixtures only.
"""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from PIL import Image, ImageDraw

import currency_wars_runner as runner
import currency_wars_rewards as rewards
import currency_wars_manual_steps as manual_steps
import currency_wars_source_guard as source_guard
import replay_currency_wars_rewards as reward_fixture
import test_currency_wars_economy as economy_fixture
import test_currency_wars_submission_queue as queue_fixture
import test_local_runtime_compatibility as compatibility
import test_currency_wars_manual_stage as stage_fixture


EVIDENCE = {}  # Compact measurements from this exact new selection only.


class RewardManualBoundaryTests(unittest.TestCase):
    def manual(self, bundle):
        bundle.control.write_json(bundle.runtime / 'runner-manual.json',
            {'manual_id': 'manual-one', 'reason': 'declared offline manual takeover'})
        state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
        state.update(preparation_stage='3-6', match_id=bundle.worker.active_match_id)
        bundle.control.write_json(bundle.runtime / 'runner-state.json', state)
        return runner.begin_manual_phase(bundle.runtime, bundle.owner, bundle.control,
                                         'manual-one', 'rewards', reader=bundle.reader)

    def enqueue(self, bundle, checkpoint, operation='collect_rewards', reply=None):
        rid = uuid.uuid4().hex
        result = manual_steps.submit(bundle.runtime, bundle.owner, bundle.control,
            manual_id='manual-one', step_id=rid, operation=operation,
            checkpoint_id=checkpoint['checkpoint_id'], reply=reply, wait_seconds=0)
        self.assertEqual(result['status'], 'queued')
        return rid

    def test_local_layout_allows_background_animation_and_refuses_changed_target_page_overlay_or_source(self):
        with reward_fixture.protocol_fixture() as bundle:
            worker = bundle.worker
            first = worker.observe(scope='rewards')
            original = worker.frame_path.read_bytes()
            fresh = worker.observe(scope='rewards')
            current = worker.frame_path.read_bytes()
            self.assertTrue(rewards.stable_layout(first, fresh, original, current))
            # Only an unrelated already public background rectangle changes.
            image = Image.open(io.BytesIO(current)).convert('RGB')
            ImageDraw.Draw(image).rectangle((600, 180, 750, 220), fill=(60, 40, 160))
            out = io.BytesIO()
            image.save(out, format='PNG')
            animated = out.getvalue()
            animation = copy.deepcopy(fresh)
            animation['snapshot_id'] = hashlib.sha256(animated).hexdigest()
            animation['semantic']['rewards'] = rewards.detect(image, 'preparation', animation['rows'], animation['snapshot_id'])
            self.assertTrue(rewards.stable_layout(first, animation, original, animated))
            for change in ('page', 'stage', 'overlay', 'target_shift', 'frame', 'snapshot', 'partial', 'cover'):
                other, payload = copy.deepcopy(fresh), current
                if change == 'page': other['page'] = 'shop'
                elif change == 'stage': other['fields']['stage'] = '3-7'
                elif change == 'overlay': other['rows'].append({'text': '新选择', 'confidence': .99, 'box': [700, 400, 800, 450]})
                elif change == 'target_shift': other['semantic']['rewards']['targets'][0]['center'][0] += 3
                elif change == 'frame': other['frame_id'] = first['frame_id']
                elif change == 'snapshot': other['snapshot_id'] = '0' * 64
                elif change == 'partial': other['semantic']['rewards']['area_fully_visible'] = False
                else:
                    image = Image.open(io.BytesIO(current)).convert('RGB')
                    x, y = other['semantic']['rewards']['targets'][0]['center']
                    ImageDraw.Draw(image).rectangle((x-5, y-5, x+5, y+5), fill=(200, 30, 20))
                    out = io.BytesIO(); image.save(out, format='PNG'); payload = out.getvalue()
                    other['snapshot_id'] = hashlib.sha256(payload).hexdigest()
                    other['semantic']['rewards'] = rewards.detect(image, 'preparation', other['rows'], other['snapshot_id'])
                with self.subTest(change=change):
                    self.assertFalse(rewards.stable_layout(first, other, original, payload))

    def test_delayed_shop_open_stops_old_orb_batch_and_next_tick_closes_current_shop(self):
        with reward_fixture.protocol_fixture() as bundle:
            worker, control = bundle.worker, bundle.control
            first = worker.observe(scope='rewards')
            control.current_frame, control.shop_closed = 'shop', False
            worker.advance_rewards(first)
            self.assertEqual(control.action_index, 0)
            self.assertEqual(worker.last_observation['page'], 'shop')
            EVIDENCE['delayed_shop'] = {'fixture': 'retained_pixels_with_declared_delayed_shop_protocol',
                'old_orb_publications': control.action_index, 'current_page_after_refusal': 'shop'}
            self.assertIsNone(runner.optional(bundle.runtime / 'reward-step.json'))
            bundle.frames['one_fresh'] = bundle.frames['one_after']
            worker.tick(worker.observe())
            clicks = [a['args'] for item in control.published for a in item['actions'] if a['type'] == 'click']
            self.assertEqual(clicks[0], [1623., 982.])
            self.assertEqual(len(clicks), 3)
            EVIDENCE['delayed_shop'].update(next_pass_inputs=len(clicks), first_action='current_close_shop',
                terminal_request='preparation_strategy', all_rewards_cleared=None)
            self.assertEqual(worker.state['decision_request']['kind'], 'preparation_strategy')
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertIsNone(runner.entry.read_json(bundle.runtime / 'reward-step.json')['all_rewards_cleared'])

    def test_same_worker_manual_batch_has_one_final_root_boundary_and_same_job_never_replays(self):
        with reward_fixture.protocol_fixture(start_shop=True, profiling=True) as bundle:
            bundle.frames['one_fresh'] = bundle.frames['one_after']
            checkpoint = self.manual(bundle)
            rid = self.enqueue(bundle, checkpoint)
            self.assertTrue(manual_steps.process(bundle.worker))
            item = runner.entry.read_json(bundle.runtime / 'manual-steps' / (rid + '.json'))
            self.assertEqual(item['status'], 'returned', item.get('error'))
            self.assertEqual(bundle.control.action_index, 3)
            self.assertEqual(len(item['result']['input_receipt_ids']), 3)
            self.assertTrue(all(s['state'] in ('completed', 'zero_input') for s in item['result']['receipt_states']))
            self.assertEqual(bundle.worker.state['statistics']['decisions'], 1)
            self.assertEqual(item['result']['decision_kind'], 'preparation_strategy')
            self.assertIsNone(item['result']['all_rewards_cleared'])
            self.assertEqual(bundle.worker.preparation_reviews, {})
            self.assertIsNotNone(runner.manual_state(bundle.runtime))
            self.assertEqual(bundle.worker.epoch(), 'old-epoch')
            from currency_wars_profile import read_events, summarize_events
            recorder = bundle.worker.profile
            recorder.close(complete=False)
            events, issues = read_events([recorder.path])
            report = summarize_events(events, issues)
            queue = [event for event in events if event.get('operation') == 'queue_wait']
            self.assertTrue(queue)
            self.assertEqual({event.get('timing_source') for event in queue}, {'entry_submission_lease'})
            self.assertIsNone(recorder.error)
            self.assertEqual(report['issues'], [])
            EVIDENCE['manual_batch'] = {'fixture': 'retained_pixels_with_declared_stable_reward_protocol',
                'same_worker': True, 'physical_actions': ['close_shop', 'blue_orb', 'blue_orb'],
                'input_receipts': len(item['result']['input_receipt_ids']),
                'zero_input_receipts': sum(value['state'] == 'zero_input' for value in item['result']['receipt_states']),
                'worker_root_requests': bundle.worker.state['statistics']['decisions'],
                'endpoint': item['result']['decision_kind'], 'endpoint_phase': 'rewards',
                'manual_latch_retained': True, 'epoch_changed': False,
                'all_rewards_cleared': None, 'same_id_replayed': False,
                'profile_error': recorder.error, 'profile_issues': report['issues'],
                'profile_operation_contracts': report['operation_contracts'],
                'profile_nodes': [{key: node.get(key) for key in
                    ('stage', 'total_seconds', 'complete_boundaries', 'operation_seconds', 'overlap_unknown_seconds')}
                    for node in report['nodes']],
                'resume_boundary_included': False, 'live_automation_verified': False}
            published = len(bundle.control.published)
            replay = manual_steps.submit(bundle.runtime, bundle.owner, bundle.control,
                manual_id='manual-one', step_id=rid, operation='collect_rewards',
                checkpoint_id=checkpoint['checkpoint_id'], wait_seconds=0)
            self.assertEqual(replay['status'], 'returned')
            self.assertFalse(manual_steps.process(bundle.worker))
            self.assertEqual(len(bundle.control.published), published)
            with self.assertRaisesRegex(ValueError, 'another intent'):
                manual_steps.submit(bundle.runtime, bundle.owner, bundle.control, manual_id='manual-one',
                    step_id=rid, operation='recover_overlay', checkpoint_id=checkpoint['checkpoint_id'], wait_seconds=0)

    def test_common_command_and_both_raw_clients_refuse_reward_economy_bypasses(self):
        with reward_fixture.protocol_fixture(start_shop=True) as bundle:
            worker = bundle.worker
            bundle.control.write_json(bundle.runtime / 'runner-owner.json', bundle.owner)
            worker.observe(scope='rewards')
            for tokens, action in ((['click:1567:720'], None),
                    (['click:1567:720'], {'type': 'click_point', 'args': [1567, 720], 'expected_page': 'shop'}),
                    (['click:1567:720'], {'type': 'click_text', 'text': '奖励球', 'expected_page': 'shop'}),
                    (['key:68'], {'type': 'key', 'args': [68], 'expected_page': 'shop'}),
                    (['key:70'], {'type': 'key', 'args': [70], 'expected_page': 'shop'}),
                    (['click:1567:720', 'click:1298:720'], {'type': 'click_point', 'args': [1567, 720], 'expected_page': 'shop'})):
                published = reward_fixture.mutation_publications(bundle.control)
                with self.subTest(tokens=tokens, action=action), self.assertRaises((ValueError, RuntimeError)):
                    worker.command(tokens, 'declared stale manual intent', 'shop', action=action)
                self.assertEqual(reward_fixture.mutation_publications(bundle.control), published)
            with self.assertRaisesRegex(ValueError, 'manual-step/decide'):
                runner.entry.request(bundle.control, 'actions', ['click:1567:720'], 'raw-manual', False)
            # Execute the actual public control-client function AST, without
            # importing/initializing Win32 or invoking the broker.
            tree = ast.parse(Path(__file__).with_name('currency_wars_control.py').read_bytes())
            body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'request_reply']
            scope = dict(Path=Path, ROOT=str(bundle.runtime), status=lambda: self.fail('raw input reached status'))
            exec(compile(ast.Module(body=body, type_ignores=[]), '<production raw client>', 'exec'), scope)
            with self.assertRaisesRegex(ValueError, 'manual-step or decide'):
                scope['request_reply']({'kind': 'actions', 'handoff': False,
                    'actions': [{'type': 'click', 'args': [1567, 720]}]})

    def test_manual_checkpoint_epoch_and_known_recovery_are_rechecked_at_publication(self):
        for change in ('checkpoint', 'epoch', 'pause'):
            with self.subTest(change=change), reward_fixture.protocol_fixture() as bundle:
                checkpoint = self.manual(bundle)
                rid = self.enqueue(bundle, checkpoint)
                original = bundle.worker.command
                def race(*args, **kwargs):
                    if change == 'checkpoint':
                        path = bundle.runtime / 'manual-results' / (checkpoint['checkpoint_id'] + '.json')
                        item = runner.entry.read_json(path); item['status'] = 'completed'; bundle.control.write_json(path, item)
                    elif change == 'epoch':
                        bundle.control.write_json(bundle.runtime / 'runner-resume-epoch.json', {'id': 'raced-epoch'})
                    else:
                        status = bundle.control.status
                        bundle.control.status = lambda: {**status(), 'paused': True}
                    return original(*args, **kwargs)
                bundle.worker.command = race
                manual_steps.process(bundle.worker)
                self.assertEqual(bundle.control.action_index, 0)
                self.assertIsNotNone(runner.manual_state(bundle.runtime))
                pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
                self.assertEqual(pending['outcome'], 'not_published')
        # Recovery is not a blanket manual ban: the same Worker can close a
        # currently identified known reward overlay, then stops for its frame.
        with reward_fixture.protocol_fixture() as bundle:
            checkpoint = self.manual(bundle)
            read = bundle.reader.read
            def overlay(path, *args, **kwargs):
                observed = read(path, *args, **kwargs)
                observed['page'] = 'reward_overlay' if bundle.control.action_index == 0 else 'preparation'
                observed['rows'] = [{'text': '获得物品', 'confidence': .99, 'box': [800, 300, 1100, 350]}]
                return observed
            bundle.reader.read = overlay
            rid = self.enqueue(bundle, checkpoint, 'recover_overlay')
            manual_steps.process(bundle.worker)
            item = runner.entry.read_json(bundle.runtime / 'manual-steps' / (rid + '.json'))
            self.assertEqual(item['status'], 'returned', item.get('error'))
            self.assertEqual(bundle.control.action_index, 1)
            keys = [a['args'] for p in bundle.control.published for a in p['actions'] if a['type'] == 'key']
            self.assertEqual(keys, [[27.]])

    def test_late_original_result_is_folded_before_observe_and_unknown_effect_never_retries(self):
        with reward_fixture.protocol_fixture() as bundle:
            first = bundle.worker.observe()
            rid = first['capture_request_id']
            path = queue_fixture.receipt_path(bundle.runtime, rid)
            original = runner.entry.read_json(path)
            bundle.control.write_json(path, {**original, 'result': None})
            publications = len(bundle.control.published)
            reconciled = runner._drain_manual_receipts(bundle.runtime, bundle.control)
            self.assertEqual(reconciled[0]['result'], original['result'])
            self.assertEqual(runner.entry.read_json(path), original)
            self.assertEqual(len(bundle.control.published), publications)
        with reward_fixture.protocol_fixture('unknown_selection') as bundle:
            checkpoint = self.manual(bundle)
            rid = self.enqueue(bundle, checkpoint)
            manual_steps.process(bundle.worker)
            self.assertEqual(bundle.control.action_index, 1)
            pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
            self.assertEqual(pending['outcome'], 'unknown')
            retry = self.enqueue(bundle, checkpoint)
            manual_steps.process(bundle.worker)
            self.assertEqual(bundle.control.action_index, 1)
            result = runner.entry.read_json(bundle.runtime / 'manual-steps' / (retry + '.json'))
            self.assertEqual(result['status'], 'refused')
            self.assertIn('unknown', result['error'])
            self.assertEqual(runner.entry.read_json(bundle.runtime / 'reward-step.json')['request_id'], pending['request_id'])

    def test_native_budget_path_survives_common_gate_but_removed_reward_review_stops_next_input(self):
        # One new F transaction specifically tests the newly added common gate;
        # it does not rerun the frozen three-F/six-D selections or OCR fixtures.
        with economy_fixture.EconomyTests().worker([economy_fixture.values(coins=62, level=8, xp=[0, 72])],
                initial=economy_fixture.values(xp=[48, 52])) as (worker, record, control, frames):
            record['value']['budget']['experience'] = 4
            worker.accept_economy_plan(record)
            worker.advance_economy(worker.last_observation)
            self.assertEqual(control.action_index, 1)
            ledger = worker.economy_ledger('2-3')
            self.assertEqual(ledger['spent']['experience'], 4)
            self.assertIsNone(ledger['pending'])
            EVIDENCE['one_authorized_f'] = {'fixture': 'declared_numeric_economy_protocol',
                'published_keys': [70], 'actual_spent_in_protocol': ledger['spent']['experience'],
                'pending': ledger['pending'], 'live_automation_verified': False}
        with economy_fixture.EconomyTests().worker() as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            worker.preparation_reviews.pop('rewards')
            worker.advance_economy(worker.last_observation)
            self.assertEqual(control.action_index, 0)

    def test_actual_widget_labels_shortcuts_prices_and_background_require_the_same_budget_intent(self):
        # Declared semantic-negative cases: neither these rows nor their
        # placeholder pixels are presented as native OCR evidence.
        with economy_fixture.EconomyTests().worker() as (worker, record, control, frames):
            worker.last_observation['rows'] = [
                {'text': label, 'confidence': .99, 'box': bounds}
                for label, bounds in [('购买经验', [248, 850, 346, 878]),
                    ('免费刷新', [1570, 475, 1670, 505]), ('D', [1605, 435, 1625, 455]),
                    ('2', [1640, 520, 1660, 550]), ('飞霄', [1460, 285, 1520, 325])]]
            for completed in (False, True):
                worker.preparation_reviews = ({phase: {'completed': True}
                    for phase in runner.coaching.PHASES[:3]} if completed else {})
                actions = [('购买', False, [297, 864]), ('D', True, [1615, 445]),
                           ('2', True, [1650, 535]), ('免费刷新', True, [1620, 490]),
                           ('飞霄', True, [1490, 305])]
                for label, exact, point in actions:
                    action = {'type': 'click_text', 'text': label, 'exact': exact, 'expected_page': 'shop'}
                    with self.subTest(completed=completed, label=label), self.assertRaises(ValueError):
                        worker.command(['click:' + ':'.join(map(str, point))], 'unbudgeted widget', 'shop', action=action)
                for point in ([1610, 575], [235, 990], [600, 90]):
                    action = {'type': 'click_point', 'args': point, 'expected_page': 'shop'}
                    with self.subTest(completed=completed, background=point), self.assertRaises(ValueError):
                        worker.command(['click:' + ':'.join(map(str, point))], 'unbudgeted background', 'shop', action=action)
            self.assertEqual(control.action_index, 0)

    def test_budgeted_text_to_key_and_capacity_first_publications_keep_exact_authorization(self):
        with economy_fixture.EconomyTests().worker([economy_fixture.values(coins=62, level=8, xp=[0, 72])],
                initial=economy_fixture.values(xp=[48, 52])) as (worker, record, control, frames):
            record['value']['budget']['experience'] = 4
            worker.accept_economy_plan(record)
            worker.last_observation['rows'].append({'text': '购买经验', 'confidence': .99, 'box': [248, 850, 346, 878]})
            action = {'type': 'click_text', 'text': '购买经验', 'expected_page': 'shop', 'reason': 'one authorized F'}
            worker.execute_economic_action(action, worker.last_observation, worker.state['decision_request'])
            self.assertEqual(control.action_index, 1)
            self.assertEqual(worker.economy_ledger('2-3')['spent']['experience'], 4)
        with economy_fixture.EconomyTests().worker() as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            command = worker.command
            def changed_budget(*args, **kwargs):
                worker.economy_ledger('2-3')['revision'] += 1
                return command(*args, **kwargs)
            worker.command = changed_budget
            with self.assertRaisesRegex(ValueError, '尚未发布的意图/预算'):
                worker.advance_economy(worker.last_observation)
            self.assertEqual(control.action_index, 0)
        # A new single publication test for the common guard, not the old
        # capacity replay/finish suite. Its business effect remains pending.
        with compatibility.RuntimeCompatibilityTests().reward_capacity_fixture() as (worker, control, reply, events):
            action = reply['actions'][0]
            actual = worker.last_observation
            value = worker.reward_capacity_action(action, actual, pixels=True)
            worker.begin_reward_capacity(value, action, actual)
            worker.command(['drag:' + ':'.join(map(str, action['args'])), 'wait:0.5'],
                           'current authorized capacity exception', 'preparation', action=action)
            self.assertTrue(control.sold)
            self.assertEqual(worker.pending_reward_capacity()['status'], 'unverified')
            with self.assertRaises(ValueError):
                worker.command(['drag:' + ':'.join(map(str, action['args'])), 'wait:0.5'],
                               'do not replay capacity sale', 'preparation', action=action)

    def test_stage_adoption_preserves_old_effect_pending_and_missing_hud_defers_without_takeover(self):
        factory = stage_fixture.ManualStageTests()
        with factory.fixture() as (run, records, owner, control, reader):
            factory.prepare(control, reader)
            factory.create(run, owner, control, reader)
            worker, fresh = factory.resume(run, records, owner, control, reader)
            worker.context = {}
            worker.publish = lambda **updates: worker.state.update(updates)
            self.assertTrue(worker.consume_manual_stage_bridge(fresh))
            self.assertEqual(worker.preparation_scope[1], '2-4')
            self.assertEqual(worker.preparation_reviews, {})
            fresh['rows'].append({'text': '出战', 'confidence': .99, 'box': [1784, 730, 1852, 770]})
            count = len(control.published)
            with self.assertRaisesRegex(ValueError, '原经济业务效果仍pending'):
                worker.command(['click:1490:305'], 'old effect cannot leave its node', 'preparation',
                               action={'type': 'click_point', 'args': [1490, 305], 'expected_page': 'preparation'})
            self.assertEqual(len(control.published), count)
            self.assertEqual(worker.economy_ledgers[('match', '2-3')]['pending']['request_id'], 'old-pending')
        with factory.fixture() as (run, records, owner, control, reader):
            factory.prepare(control, reader)
            factory.create(run, owner, control, reader)
            worker, fresh = factory.resume(run, records, owner, control, reader)
            worker.publish = lambda **updates: worker.state.update(updates)
            fresh['fields']['stage'], fresh['rows'] = None, []
            count = len(control.published)
            worker.tick(fresh)
            self.assertEqual(len(control.published), count)
            self.assertIsNone(runner.manual_state(run))
            self.assertEqual(worker.epoch(), 'new-epoch')
            self.assertFalse(hasattr(worker, 'manual_stage_consumed'))
            self.assertIn('只补当前观察', worker.state['reason'])

    def test_new_runtime_modules_are_in_shared_source_manifest_and_changed_bytes_refuse(self):
        files = source_guard.production_files(runner.PROJECT)
        modules = ('tools/currency_wars_manual_stage.py', 'tools/currency_wars_manual_steps.py')
        self.assertTrue(set(modules) <= set(files))
        self.assertEqual(hashlib.sha256(runner.entry.SOURCE.read_bytes()).hexdigest().upper(), runner.entry.PINNED)
        with tempfile.TemporaryDirectory(prefix='currency-wars-new-source-') as temporary:
            root = Path(temporary)
            for name in files:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((runner.PROJECT / name).read_bytes())
            expected = source_guard.source_hashes(root)
            for name in modules:
                path = root / name
                data = path.read_bytes(); path.write_bytes(data + b'\n# changed source byte\n')
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'not reviewed'):
                    source_guard.verify_source_hashes(root, expected)
                path.write_bytes(data)


if __name__ == '__main__':
    unittest.main()
