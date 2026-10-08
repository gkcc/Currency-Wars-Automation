"""Manual-step evidence failures with inert transport and no game/image input."""
import contextlib
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

import currency_wars_broker_entry as entry_module
import currency_wars_manual_steps as steps_module
import currency_wars_runner as runner_module
import test_currency_wars_submission_queue as queue_fixture


@contextlib.contextmanager
def manual_fixture():
    with tempfile.TemporaryDirectory(prefix='currency-wars-manual-evidence-') as temporary:
        project = Path(temporary)
        run, records = project / 'runtime', project / 'debug' / 'records'
        run.mkdir()
        records.mkdir(parents=True)
        control = queue_fixture.transport(run)
        control.status = lambda: dict(ready=True, paused=False, input_halted=False, game_foreground=True)
        owner = dict(run_id='fixture-run', chat_id='fixture-chat')
        binding = dict(run_id=owner['run_id'], match_id='fixture-match', stage='2-3',
                       manual_id='manual-one', old_epoch='fixture-epoch')
        control.write_json(records / 'owner.json', owner)
        control.write_json(run / 'runner-state.json', dict(owner, match_id=binding['match_id'],
            preparation_stage=binding['stage'], journal_file=str(records / 'journal.jsonl')))
        control.write_json(run / 'runner-manual.json', dict(manual_id='manual-one', reason='offline fixture'))
        control.write_json(run / 'runner-resume-epoch.json', dict(id='fixture-epoch'))
        checkpoint_id, step_id = 'b' * 32, 'a' * 32
        (run / 'manual-results').mkdir()
        control.write_json(run / 'manual-results' / (checkpoint_id + '.json'),
                           dict(binding=binding, status='pending', phase='rewards'))
        worker = SimpleNamespace(run=run, records=records, owner=owner, c=control,
            active_match_id=binding['match_id'], state={'decision_request': None}, last_observation=None,
            manual_step_context=None, observed_count=0, input_count=0, notifications=[], fail_after_input=False,
            after_input=lambda: None, profile_span=lambda *args, **kwargs: contextlib.nullcontext())
        worker.economy_receipt_watermark = MethodType(runner_module.Worker.economy_receipt_watermark, worker)

        def observe(**unused):
            worker.observed_count += 1
            entry_module.request(control, 'actions', ['observe'], 'step-observation', False)
            worker.last_observation = dict(page='reward_overlay', fields={'stage': '2-3'},
                                           capture_request_id='step-observation')
            return worker.last_observation

        def command(tokens, *unused, **kwargs):
            worker.input_count += 1
            entry_module.request(control, 'actions', tokens, 'step-input', False)
            worker.after_input()
            if worker.fail_after_input:
                raise RuntimeError('original action failure')
            return worker.last_observation

        def ask(observed, kind, reason, **unused):
            worker.state['decision_request'] = dict(request_id='fixture-decision', kind=kind)

        worker.observe, worker.command, worker.ask = observe, command, ask
        worker.publish = lambda **values: worker.notifications.append(values)
        worker.epoch = lambda: binding['old_epoch']
        with patch.object(runner_module, 'PROJECT', project):
            queued = steps_module.submit(run, owner, control, manual_id='manual-one', step_id=step_id,
                operation='recover_overlay', checkpoint_id=checkpoint_id, wait_seconds=0)
            if queued['status'] != 'queued':
                raise AssertionError('Fixture was not queued')
            yield SimpleNamespace(worker=worker, control=control, run=run, records=records,
                                  owner=owner, step_id=step_id, checkpoint_id=checkpoint_id)


def result_path(bundle):
    return bundle.run / 'manual-steps' / (bundle.step_id + '.json')


def original_step(bundle):
    return steps_module.submit(bundle.run, bundle.owner, bundle.control,
        manual_id='manual-one', step_id=bundle.step_id, operation='recover_overlay',
        checkpoint_id=bundle.checkpoint_id, wait_seconds=0)


class ManualStepEvidenceTests(unittest.TestCase):
    def test_before_after_or_disappearing_watermark_stays_unknown_without_replaying_step(self):
        for fault in ('before', 'after', 'missing_prior'):
            with self.subTest(fault=fault), manual_fixture() as bundle:
                worker = bundle.worker
                actual_watermark = worker.economy_receipt_watermark
                calls = []
                if fault == 'missing_prior':
                    entry_module.request(bundle.control, 'actions', ['observe'], 'prior-observe', False)
                def watermark():
                    calls.append(True)
                    if fault == 'before' and len(calls) == 1:
                        raise ValueError('before watermark unavailable')
                    if fault == 'after' and len(calls) == 2:
                        raise OSError('after watermark unavailable')
                    values = actual_watermark()
                    return [value for value in values if value != 'prior-observe'] \
                        if fault == 'missing_prior' and len(calls) == 2 else values
                worker.economy_receipt_watermark = watermark
                worker.fail_after_input = fault == 'after'
                self.assertTrue(steps_module.process(worker))
                item = entry_module.read_json(result_path(bundle))
                self.assertEqual(item['status'], 'refused')
                self.assertFalse(item['result']['receipt_watermark_verified'])
                self.assertFalse(item['result']['receipt_delivery_verified'])
                self.assertIsNone(item['result']['input_receipt_ids'])
                self.assertIsNone(item['result']['all_rewards_cleared'])
                self.assertFalse(item['result']['automatic_phase_completion'])
                self.assertTrue(steps_module.summary(item)['pending'])
                self.assertIsNone(worker.manual_step_context)
                self.assertEqual(worker.input_count, 0 if fault == 'before' else 1)
                self.assertEqual(worker.observed_count, 0 if fault == 'before' else 1)
                if fault == 'after':
                    self.assertEqual(item['error'], 'original action failure')
                    self.assertEqual(item['result']['evidence_errors'],
                                     [dict(stage='after_watermark', error_type='OSError')])
                archive = entry_module.read_json(bundle.records / ('manual-step-' + bundle.step_id + '.json'))
                self.assertEqual(archive, runner_module.redact(item))
                publications = len(bundle.control.published)
                self.assertTrue(original_step(bundle)['pending'])
                self.assertFalse(steps_module.process(worker))
                self.assertEqual(len(bundle.control.published), publications)

    def test_unreadable_pending_and_unknown_receipts_preserve_known_subset_and_original_ids(self):
        for fault in ('identity', 'pending', 'unknown'):
            with self.subTest(fault=fault), manual_fixture() as bundle:
                path = queue_fixture.receipt_path(bundle.run, 'step-input')
                def damage():
                    receipt = entry_module.read_json(path)
                    if fault == 'identity':
                        receipt['request']['chat_id'] = 'foreign-fixture'
                    elif fault == 'pending':
                        receipt['result'] = None
                        bundle.control.write_json(bundle.run / 'result.json', dict(id='other-notification'))
                    else:
                        receipt['result'].update(ok=False, completed=[])
                    bundle.control.write_json(path, receipt)
                bundle.worker.after_input = damage
                self.assertTrue(steps_module.process(bundle.worker))
                item = entry_module.read_json(result_path(bundle))
                states = {state['request_id']: state for state in item['result']['receipt_states']}
                self.assertEqual(states['step-observation']['state'], 'zero_input')
                self.assertFalse(states['step-observation']['unknown_input'])
                self.assertEqual(states['step-input']['state'], 'pending' if fault == 'pending' else 'unknown')
                self.assertTrue(states['step-input']['unknown_input'])
                self.assertTrue(item['result']['receipt_watermark_verified'])
                self.assertFalse(item['result']['receipt_delivery_verified'])
                self.assertEqual(item['result']['input_receipt_ids'], ['step-input'])
                self.assertTrue(steps_module.summary(item)['pending'])
                self.assertFalse(item['result']['automatic_phase_completion'])
                original = path.read_bytes()
                publications = len(bundle.control.published)
                self.assertTrue(original_step(bundle)['pending'])
                self.assertFalse(steps_module.process(bundle.worker))
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(len(bundle.control.published), publications)

    def test_finalization_failures_preserve_primary_error_and_never_make_running_job_replayable(self):
        for fault in ('archive', 'notification', 'both_writes'):
            with self.subTest(fault=fault), manual_fixture() as bundle:
                bundle.worker.fail_after_input = True
                write = bundle.control.write_json
                archive = bundle.records / ('manual-step-' + bundle.step_id + '.json')
                def fail_write(path, value):
                    terminal = value.get('status') in ('returned', 'refused')
                    if terminal and (fault == 'both_writes' or fault == 'archive' and path == archive):
                        raise OSError('fixture archive unavailable')
                    return write(path, value)
                bundle.control.write_json = fail_write
                if fault == 'notification':
                    def notify(**unused):
                        raise ValueError('fixture notification unavailable')
                    bundle.worker.publish = notify
                if fault == 'both_writes':
                    with self.assertRaisesRegex(RuntimeError, 'original action failure; manual-step result could not be saved') as failed:
                        steps_module.process(bundle.worker)
                    self.assertEqual(str(failed.exception.__cause__), 'original action failure')
                    self.assertEqual(entry_module.read_json(result_path(bundle))['status'], 'running')
                else:
                    self.assertTrue(steps_module.process(bundle.worker))
                    item = entry_module.read_json(result_path(bundle))
                    self.assertEqual(item['error'], 'original action failure')
                    self.assertEqual(item['status'], 'refused')
                    self.assertTrue(item['result']['receipt_watermark_verified'])
                    self.assertEqual(item['result']['input_receipt_ids'], ['step-input'])
                    self.assertTrue(item['finalization_errors'])
                    self.assertTrue(steps_module.summary(item)['pending'])
                self.assertIsNone(bundle.worker.manual_step_context)
                self.assertEqual(bundle.worker.input_count, 1)
                publications = len(bundle.control.published)
                self.assertTrue(original_step(bundle)['pending'])
                self.assertFalse(steps_module.process(bundle.worker))
                self.assertEqual(len(bundle.control.published), publications)


if __name__ == '__main__':
    unittest.main()
