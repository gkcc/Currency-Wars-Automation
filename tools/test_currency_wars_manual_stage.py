"""New manual stage contracts; inert transport and declared native HUD rows.

These are protocol cases. They do not reproduce a real battle transition,
operate the desktop, or assert that the retained ROOT error receipt was read.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import currency_wars_manual_stage as stage_bridge
import currency_wars_runner as runner
import test_local_runtime_compatibility as compatibility


def native(stage='2-4', page='preparation'):
    title, location = (([430, 33, 513, 60], [441, 62, 502, 98]) if page == 'preparation'
                       else ([242, 56, 334, 87], [257, 92, 314, 124]))
    return {'page': page, 'fields': {'stage': stage}, 'rows': [
        {'text': text, 'confidence': .99, 'box': bounds,
         'normalization_basis': 'declared_offline_protocol_fixture'}
        for text, bounds in (('备战阶段', title), (stage, location))]}


class ManualStageTests(unittest.TestCase):
    def fixture(self):
        return compatibility.RuntimeCompatibilityTests().manual_bridge_fixture()

    def prepare(self, control, reader, *frames):
        control.published = []
        old_read = reader.read
        values = list(frames or [native()])
        def read(path, *args, **kwargs):
            value = values.pop(0) if len(values) > 1 else values[0]
            return {**old_read(path, *args, **kwargs), **copy.deepcopy(value)}
        reader.read = read

    def raw_binding(self, run, owner, control):
        return runner._manual_binding(run, owner, control, 'manual-one', resolve_stage=False)[0]

    def create(self, run, owner, control, reader):
        return stage_bridge.create(run, owner, control, 'manual-one', reader=reader)

    def resume(self, run, records, owner, control, reader):
        result = runner.explicit_resume(run, owner, control, 'new-epoch',
            expected_guard=runner.resume_guard_snapshot(run, owner, control))
        self.assertTrue(result['resumed'])
        worker = compatibility.RuntimeCompatibilityTests().frame_worker(run, records, owner, control, reader)
        worker.preparation_reviews = {'economy': {'completed': True}}
        worker.economy_ledgers = {('match', '2-3'): {'spent': {'purchase': 2}, 'pending': {'request_id': 'old-pending'}}}
        return worker, worker.observe()

    def test_two_native_observations_rebind_manual_scope_without_claiming_completion(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            old_state = (run / 'runner-state.json').read_bytes()
            base = self.raw_binding(run, owner, control)
            bridge = self.create(run, owner, control, reader)
            self.assertEqual((bridge['from_stage'], bridge['to_stage']), ('2-3', '2-4'))
            self.assertEqual(len(control.published), 2)
            self.assertTrue(all(item['actions'] == [{'type': 'observe', 'args': []}]
                                and item['handoff'] is False for item in control.published))
            self.assertEqual(len({item['frame_id'] for item in bridge['observations']}), 2)
            self.assertEqual(bridge['status'], 'scope_rebound')
            self.assertFalse(bridge['input_authorized'])
            self.assertEqual(bridge['completed_phases'], [])
            self.assertIsNone(bridge['all_rewards_cleared'])
            self.assertEqual(bridge['historical_transition_attribution'], 'unverified')
            resolved = stage_bridge.resolve_binding(run, owner, control, base)
            self.assertEqual(resolved, {**base, 'stage': '2-4'})
            self.assertEqual((run / 'runner-state.json').read_bytes(), old_state)
            self.assertEqual(runner.optional(run / 'runner-resume-epoch.json'), {'id': 'old-epoch'})
            self.assertIsNotNone(runner.manual_state(run))

    def test_delayed_shop_stage_and_anchor_changes_stop_bridge_without_input(self):
        bad_row = native()
        bad_row['rows'][1]['confidence'] = .50
        outside = native()
        outside['rows'][1]['box'] = [900, 300, 950, 340]
        moved = native()
        moved['rows'][1]['box'][0] += 4
        cases = [(native(), native(page='shop')), (native(), native('2-5')),
                 (native(), moved), (bad_row,), (outside,), (native('2-2'),)]
        for frames in cases:
            with self.subTest(frames=frames), self.fixture() as (run, records, owner, control, reader):
                self.prepare(control, reader, *frames)
                with self.assertRaises(ValueError):
                    self.create(run, owner, control, reader)
                self.assertFalse((run / stage_bridge.FILE).exists())
                self.assertTrue(all(item['actions'] == [{'type': 'observe', 'args': []}]
                                    for item in control.published))

    def test_nonterminal_blocks_capture_and_terminal_unknown_is_retained_without_authorization(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            rid = 'synthetic-unresolved-receipt'
            ledger = run / 'request-ledger'
            ledger.mkdir()
            path = ledger / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
            receipt = {'id': rid, 'request': {'id': rid, 'kind': 'actions', 'handoff': False,
                **control.OWNER, 'actions': [{'type': 'key', 'args': [68]}]}, 'result': None}
            control.write_json(path, receipt)
            original = path.read_bytes()
            with patch.object(runner, '_drain_manual_receipts', side_effect=TimeoutError('declared unresolved original')):
                with self.assertRaises(TimeoutError):
                    self.create(run, owner, control, reader)
            self.assertEqual(control.published, [])
            self.assertFalse((run / stage_bridge.FILE).exists())
            self.assertEqual(path.read_bytes(), original)
            receipt['result'] = {'id': rid, 'ok': False, 'completed': [],
                                 'input_attempted': True, 'attempted_actions': receipt['request']['actions']}
            control.write_json(path, receipt)
            original = path.read_bytes()
            bridge = self.create(run, owner, control, reader)
            self.assertEqual(bridge['blocked_ids'], [rid])
            self.assertEqual(bridge['receipts'][rid]['result'], receipt['result'])
            self.assertIn(rid, bridge['receipt_watermark'])
            self.assertFalse(bridge['input_authorized'])
            self.assertEqual(path.read_bytes(), original)
            worker, fresh = self.resume(run, records, owner, control, reader)
            verdict = stage_bridge.verify_for_resume(worker, fresh)
            self.assertEqual(verdict['blocked_ids'], [rid])
            self.assertEqual(verdict['original_blocked_ids'], [rid])
            self.assertFalse(verdict['input_authorized'])
            self.assertEqual(path.read_bytes(), original)

    def test_resume_requires_new_observe_and_preserves_spending_pending_and_reviews(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            bridge = self.create(run, owner, control, reader)
            worker, fresh = self.resume(run, records, owner, control, reader)
            original = copy.deepcopy(worker.economy_ledgers)
            original_reviews = copy.deepcopy(worker.preparation_reviews)
            published = len(control.published)
            verdict = stage_bridge.verify_for_resume(worker, fresh)
            self.assertEqual(verdict['stage'], '2-4')
            self.assertEqual(verdict['blocked_ids'], [])
            self.assertTrue(verdict['scope_only'])
            self.assertFalse(verdict['input_authorized'])
            self.assertEqual(worker.economy_ledgers, original)
            self.assertEqual(worker.preparation_reviews, original_reviews)
            self.assertEqual(len(control.published), published)
            old = bridge['observations'][-1]
            stale = {**fresh, 'capture_request_id': old['receipt_id'], 'frame_id': old['frame_id'],
                     'snapshot_id': old['snapshot_id'], 'captured_at': old['captured_at']}
            with self.assertRaisesRegex(ValueError, '新epoch同请求'):
                stage_bridge.verify_for_resume(worker, stale)

    def test_completed_manual_claim_receipts_allow_scope_only_after_shop_closes(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader, native(page='shop'), native(page='shop'),
                         native(page='shop'), native(), native())
            self.create(run, owner, control, reader)
            checkpoint = runner.begin_manual_phase(run, owner, control, 'manual-one', 'rewards', reader=reader)
            self.assertEqual(checkpoint['binding']['stage'], '2-4')
            # The fixture's publisher returns a real Entry ledger and an
            # immutable protocol successor; no physical claim is executed.
            runner.entry.request(control, 'actions', ['click:1:2'], 'protocol-manual-claim', False)
            review = {'phase': 'rewards', 'stage': '2-4', 'completed': True,
                'reviewer': 'supervising_agent', 'findings': 'Declared protocol manual review only',
                'all_claimed': True, 'rescanned_after_claim': True}
            completed = runner.finish_manual_phase(run, owner, control, checkpoint['checkpoint_id'],
                                                   ['protocol-manual-claim'], review, reader=reader)
            self.assertEqual(completed['status'], 'completed')
            worker, fresh = self.resume(run, records, owner, control, reader)
            verdict = stage_bridge.verify_for_resume(worker, fresh)
            self.assertEqual(verdict['covered_manual_receipt_ids'], ['protocol-manual-claim'])
            self.assertEqual(verdict['stage'], '2-4')
            self.assertEqual(fresh['page'], 'preparation')
            self.assertEqual(verdict['completed_phases'], [])
            self.assertIsNone(verdict['all_rewards_cleared'])
            self.assertNotIn('rewards', worker.preparation_reviews)
            self.assertFalse(verdict['input_authorized'])
            worker.context = {}
            worker.publish = lambda **updates: worker.state.update(updates)
            original_receipts = {path: path.read_bytes() for path in (run / 'request-ledger').glob('*.json')}
            self.assertTrue(worker.consume_manual_stage_bridge(fresh))
            self.assertFalse(worker.consume_manual_stage_bridge(fresh))
            source = worker.verified_manual_source(completed, {**fresh,
                'preparation_stage': '2-4', 'observed_at': runner.now()})
            self.assertEqual(source['preparation_stage'], '2-4')
            self.assertEqual(worker.preparation_reviews, {})
            self.assertEqual(worker.preparation_checklist(fresh)['phase'], 'rewards')
            self.assertTrue(all(path.read_bytes() == data for path, data in original_receipts.items()))

    def test_completed_delivery_keeps_economy_reward_and_capacity_effects_pending(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            for rid, token in (('protocol-old-refresh', 'key:68'), ('protocol-old-reward', 'click:1:2'),
                               ('protocol-old-capacity', 'drag:1:2:3:4')):
                runner.entry.request(control, 'actions', [token], rid, False)
            ledger = runner.economy.new_ledger()
            ledger['spent']['refresh'] = 5
            rid = 'protocol-old-refresh'
            original = runner.await_existing_receipt(run, control, rid, 0)
            ledger['pending'] = {'request_id': rid, 'kind': 'refresh', 'cost': 2,
                'before': {'stage': '2-3'}, 'resume_epoch': 'old-epoch', 'publication_attempted': True,
                'broker_actions': original['request']['actions']}
            economy_path = records / ('economy-' + hashlib.sha256(b'match:2-3').hexdigest()[:24] + '.json')
            control.write_json(economy_path, {'schema': runner.economy.SCHEMA, 'run_id': owner['run_id'],
                'match_id': 'match', 'stage': '2-3', 'ledger': ledger})
            reward = {'step_id': 'protocol-old-reward-step', 'request_id': 'protocol-old-reward',
                'kind': 'blue_orb', 'match_id': 'match', 'before': {'stage': '2-3'},
                'resume_epoch': 'old-epoch', 'status': 'pending', 'outcome': 'unknown',
                'publication_attempted': True}
            capacity = {'run_id': owner['run_id'], 'match_id': 'match', 'stage': '2-3',
                'resume_epoch': 'old-epoch', 'status': 'unverified', 'input_request_id': 'protocol-old-capacity'}
            control.write_json(run / 'reward-step.json', reward)
            control.write_json(records / 'reward-step-protocol-old-reward-step.json', reward)
            control.write_json(run / 'reward-capacity.json', capacity)
            originals = {path: path.read_bytes() for path in (economy_path, run / 'reward-step.json',
                records / 'reward-step-protocol-old-reward-step.json', run / 'reward-capacity.json')}
            bridge = self.create(run, owner, control, reader)
            self.assertEqual(len(bridge['business_pending']), 3)
            self.assertTrue(all(item['delivery_state'] == 'completed' for item in bridge['business_pending']))
            worker, fresh = self.resume(run, records, owner, control, reader)
            verdict = stage_bridge.verify_for_resume(worker, fresh)
            self.assertEqual(verdict['delivery_blocked_ids'], [])
            self.assertEqual(verdict['blocked_ids'], ['protocol-old-capacity', 'protocol-old-refresh', 'protocol-old-reward'])
            self.assertEqual({item['stage'] for item in verdict['business_pending']}, {'2-3'})
            self.assertEqual({item['source_kind'] for item in verdict['business_pending']},
                             {'economy', 'reward_step', 'reward_capacity'})
            economic = next(item for item in verdict['business_pending'] if item['source_kind'] == 'economy')
            self.assertEqual(economic['planned_cost_not_actual'], 2)
            self.assertTrue(economic['request_actions_match'])
            self.assertEqual(runner.entry.read_json(economy_path)['ledger']['spent']['refresh'], 5)
            self.assertTrue(all(path.read_bytes() == payload for path, payload in originals.items()))

    def test_missing_original_receipt_or_old_business_file_does_not_clear_effect(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            reward = {'step_id': 'protocol-missing-original', 'request_id': 'protocol-missing-receipt',
                'kind': 'blue_orb', 'match_id': 'match', 'before': {'stage': '2-3'},
                'resume_epoch': 'old-epoch', 'status': 'pending', 'outcome': 'unknown',
                'publication_attempted': True}
            path = run / 'reward-step.json'
            control.write_json(path, reward)
            bridge = self.create(run, owner, control, reader)
            self.assertFalse(bridge['business_pending'][0]['receipt_available'])
            self.assertEqual(bridge['blocked_ids'], ['protocol-missing-receipt'])
            path.unlink()  # Loss of an old source is not a business resolution.
            worker, fresh = self.resume(run, records, owner, control, reader)
            verdict = stage_bridge.verify_for_resume(worker, fresh)
            self.assertEqual(verdict['blocked_ids'], ['protocol-missing-receipt'])
            self.assertTrue(verdict['business_pending'][0]['retained_from_bridge'])
            self.assertTrue(verdict['business_pending'][0]['effect_pending'])
            self.assertFalse(verdict['input_authorized'])

    def test_economy_debt_requires_original_effect_log_and_exact_spending_to_clear(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            rid = 'protocol-economy-resolution'
            runner.entry.request(control, 'actions', ['key:68'], rid, False)
            original = runner.await_existing_receipt(run, control, rid, 0)
            ledger = runner.economy.new_ledger()
            ledger['spent']['refresh'] = 5
            ledger['pending'] = {'request_id': rid, 'kind': 'refresh', 'cost': 2,
                'before': {'stage': '2-3'}, 'resume_epoch': 'old-epoch', 'publication_attempted': True,
                'broker_actions': original['request']['actions']}
            path = records / ('economy-' + hashlib.sha256(b'match:2-3').hexdigest()[:24] + '.json')
            stored = {'schema': runner.economy.SCHEMA, 'run_id': owner['run_id'],
                      'match_id': 'match', 'stage': '2-3', 'ledger': ledger}
            control.write_json(path, stored)
            entries = stage_bridge.business_pending(run, owner, control, 'match', records)
            remaining = lambda: stage_bridge.pending_remaining(entries, run=run, owner=owner, control=control, records=records)
            self.assertEqual(remaining(), entries)  # completed delivery is insufficient.
            ledger['pending'] = None
            ledger['spent']['refresh'] = 7
            control.write_json(path, stored)
            self.assertEqual(remaining(), entries)  # cleared pointer alone is insufficient.
            event = {'event': 'economic_effect_verified', 'run_id': owner['run_id'],
                'request_id': rid, 'stage': '2-3', 'kind': 'refresh', 'outcome': 'success',
                'observed_spent': 2, 'actual_spent': ledger['spent'], 'input_resent': False,
                'source': 'declared_offline_protocol_effect_record'}
            (records / 'journal.jsonl').write_text(json.dumps(event) + '\n', encoding='utf8')
            self.assertEqual(remaining(), [])
            ledger['spent']['refresh'] = 8
            control.write_json(path, stored)
            self.assertEqual(remaining(), entries)
            ledger['spent']['refresh'] = 7
            control.write_json(path, stored)
            receipt_path = run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
            changed = copy.deepcopy(original)
            changed['result']['ok'] = False
            control.write_json(receipt_path, changed)
            self.assertEqual(remaining(), entries)

    def test_verified_reward_archive_cannot_hide_same_id_pending_pointer(self):
        with self.fixture() as (run, records, owner, control, reader):
            self.prepare(control, reader)
            rid = 'protocol-reward-resolution'
            runner.entry.request(control, 'actions', ['click:1:2'], rid, False)
            original = runner.await_existing_receipt(run, control, rid, 0)
            reward = {'step_id': 'protocol-step', 'request_id': rid, 'kind': 'blue_orb',
                'match_id': 'match', 'before': {'stage': '2-3'}, 'resume_epoch': 'old-epoch',
                'status': 'pending', 'outcome': 'unknown', 'publication_attempted': True}
            archive = records / 'reward-step-protocol-step.json'
            pointer = run / 'reward-step.json'
            control.write_json(archive, reward)
            control.write_json(pointer, reward)
            entries = stage_bridge.business_pending(run, owner, control, 'match', records)
            remaining = lambda: stage_bridge.pending_remaining(entries, run=run, owner=owner, control=control, records=records)
            image = records / 'protocol-reward-after.png'
            image.write_bytes(control.frame)
            verified = {**reward, 'status': 'verified', 'outcome': 'one_visible_orb_removed',
                'all_rewards_cleared': None, 'receipt': runner.redact(original['result']),
                'after_png': str(image), 'after_snapshot_id': hashlib.sha256(control.frame).hexdigest(),
                'after_capture_request_id': rid}
            control.write_json(archive, verified)
            self.assertEqual(remaining(), entries)  # same-ID pointer still pending.
            control.write_json(pointer, {**reward, 'step_id': 'different-step'})
            self.assertEqual(remaining(), entries)  # same ID cannot name a new step.
            control.write_json(pointer, verified)
            self.assertEqual(remaining(), [])
            control.write_json(pointer, {**reward, 'step_id': 'next-step', 'request_id': 'next-request'})
            self.assertEqual(remaining(), [])  # original verified archive survives a new pointer.
            archive.unlink()
            self.assertEqual(remaining(), entries)

    def test_current_hud_can_defer_but_bad_source_and_epoch_are_hard_refusals(self):
        with self.fixture() as (run, records, owner, control, reader):
            transition = {'page': 'unknown', 'fields': {'stage': None}, 'rows': []}
            self.prepare(control, reader, native(), native(), transition, native())
            self.create(run, owner, control, reader)
            worker, fresh = self.resume(run, records, owner, control, reader)
            with self.assertRaises(stage_bridge.StageObservationDeferred) as deferred:
                stage_bridge.verify_for_resume(worker, fresh)
            self.assertTrue(deferred.exception.can_reobserve)
            self.assertIsNone(runner.manual_state(run))
            with self.assertRaises(ValueError) as bad_frame:
                stage_bridge.verify_for_resume(worker, {**fresh, 'frame_id': 'not-the-original-frame'})
            self.assertNotIsInstance(bad_frame.exception, stage_bridge.StageObservationDeferred)
            epoch = runner.optional(run / 'runner-resume-epoch.json')
            control.write_json(run / 'runner-resume-epoch.json', {**epoch, 'previous_epoch': 'different'})
            with self.assertRaises(ValueError) as bad_epoch:
                stage_bridge.verify_for_resume(worker, fresh)
            self.assertNotIsInstance(bad_epoch.exception, stage_bridge.StageObservationDeferred)
            control.write_json(run / 'runner-resume-epoch.json', epoch)
            current = worker.observe()
            verdict = stage_bridge.verify_for_resume(worker, current)
            self.assertEqual(verdict['stage'], '2-4')
            self.assertEqual(verdict['completed_phases'], [])
            self.assertIsNone(runner.manual_state(run))

    def test_missing_original_png_receipt_and_changed_archive_are_refused(self):
        for mutation in ('png', 'receipt', 'archive'):
            with self.subTest(mutation=mutation), self.fixture() as (run, records, owner, control, reader):
                self.prepare(control, reader)
                base = self.raw_binding(run, owner, control)
                bridge = self.create(run, owner, control, reader)
                if mutation == 'png':
                    from pathlib import Path
                    Path(bridge['observations'][0]['evidence_file']).write_bytes(b'changed protocol source')
                elif mutation == 'receipt':
                    rid = bridge['observations'][0]['receipt_id']
                    (run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')).unlink()
                else:
                    control.write_json(run / stage_bridge.FILE, {**bridge, 'to_stage': '2-5'})
                with self.assertRaises(ValueError):
                    stage_bridge.resolve_binding(run, owner, control, base)

    def test_new_manual_epoch_and_later_input_do_not_adopt_old_scope(self):
        for mutation in ('takeover', 'epoch', 'input', 'stage'):
            with self.subTest(mutation=mutation), self.fixture() as (run, records, owner, control, reader):
                self.prepare(control, reader)
                self.create(run, owner, control, reader)
                worker, fresh = self.resume(run, records, owner, control, reader)
                if mutation == 'takeover':
                    control.write_json(run / 'runner-manual.json', {'manual_id': 'later-takeover', 'reason': 'protocol'})
                elif mutation == 'epoch':
                    epoch = runner.optional(run / 'runner-resume-epoch.json')
                    control.write_json(run / 'runner-resume-epoch.json', {**epoch, 'previous_epoch': 'wrong-old-epoch'})
                elif mutation == 'input':
                    runner.entry.request(control, 'actions', ['key:27'], 'protocol-later-input', False)
                else:
                    fresh = {**fresh, **native('2-5')}
                with self.assertRaises(ValueError):
                    stage_bridge.verify_for_resume(worker, fresh)
                self.assertEqual(worker.preparation_reviews, {'economy': {'completed': True}})


if __name__ == '__main__':
    unittest.main()
