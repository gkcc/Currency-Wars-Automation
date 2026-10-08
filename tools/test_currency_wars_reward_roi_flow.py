"""Actual Worker/manual mailbox/Entry with inert transport, not a game replay.

Retained native reward scan pixels are pasted into the public q01 protocol
canvas. Post-click removals, HUD rows and unknown-reader results are explicitly
declared fixtures. They do not restore original full-frame identity or prove a
real pickup. No production visual guard or effect helper is mocked.
"""
import copy
import hashlib
import io
from pathlib import Path
import unittest
import uuid

from PIL import Image

import currency_wars_runner as runner
import currency_wars_rewards as rewards
import currency_wars_manual_steps as steps
import currency_wars_manual_stage as manual_stage
from currency_wars_perception import fingerprint
import replay_currency_wars_rewards as fixture
from test_currency_wars_native_reward_detector import load_native_scans


EVIDENCE = []
DIRECTORY = Path(__file__).resolve().parents[1] / 'handoff/2026-10-08/ROOT_NATIVE_REWARDS'


def install_frames(bundle, kind, *, native_unknown=True):
    diagnosis, samples = load_native_scans(DIRECTORY)
    unused, source = next(item for item in samples if item[0]['label'] == 'fresh_worker')
    prefix = 'blue' if kind == 'blue_orb' else 'gray'
    target = dict(kind=kind, bounds=diagnosis['manual_annotations'][prefix + '_bounds'],
                  center=diagnosis['manual_annotations'][prefix + '_center'])
    base = fixture._protocol_frame(())
    with Image.open(io.BytesIO(base['payload'])) as image:
        canvas = image.convert('RGB')
    with Image.fromarray(source[230:500, 1320:1660]) as scan:
        canvas.paste(scan, (1320, 230))
    after = canvas.copy()
    x, y, right, bottom = target['bounds']
    # These source-background patches were explicitly chosen for a SYNTHETIC
    # removal fixture. There is no retained actual post-click image here.
    px, py = (1400, 410) if kind == 'blue_orb' else (1580, 340)
    with canvas.crop((px, py, px+right-x, py+bottom-y)) as background:
        after.paste(background, (x, y))
    def frame(image):
        data = io.BytesIO()
        image.save(data, format='PNG')
        payload = data.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        observed = copy.deepcopy(base['observation'])
        observed.update(snapshot_id=digest, fingerprint=fingerprint(image))
        if native_unknown:
            # Force ONLY the automatic reader boundary unknown. Native pixels
            # and production target/postcondition guards remain unchanged.
            scan = observed['semantic']['rewards']
            scan.update(snapshot_id=digest, targets=[], uncertain=True, input_allowed=False,
                        image_rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                        reason='declared_protocol_reader_unknown')
        else:
            observed['semantic']['rewards'] = rewards.detect(image, 'preparation', observed['rows'], digest)
        return dict(payload=payload, observation=observed)
    try:
        before, removed = frame(canvas), frame(after)
        if not native_unknown:
            if kind != 'gray_orb':
                raise ValueError('native batch fixture removes top gray then lower blue')
            with after.copy() as empty:
                bx, by, br, bb = diagnosis['manual_annotations']['blue_bounds']
                with canvas.crop((1400, 410, 1400+br-bx, 410+bb-by)) as background:
                    empty.paste(background, (bx, by))
                bundle.frames['empty'] = frame(empty)
    finally:
        canvas.close()
        after.close()
    bundle.frames.update(two=before, one_after=removed, one_fresh=removed)
    def read(path, force=False, *, scope='full', **unused):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        item = next(value for value in bundle.frames.values() if value['observation']['snapshot_id'] == digest)
        return copy.deepcopy(item['observation'])
    bundle.reader.read = read
    return target


def prepare(bundle, kind='blue_orb', *, native_unknown=True):
    target = install_frames(bundle, kind, native_unknown=native_unknown)
    worker, control = bundle.worker, bundle.control
    control.write_json(bundle.runtime / 'runner-manual.json',
                       dict(manual_id='manual-one', reason='declared offline ROI takeover'))
    state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
    state.update(preparation_stage='3-6', match_id=worker.active_match_id)
    control.write_json(bundle.runtime / 'runner-state.json', state)
    checkpoint = runner.begin_manual_phase(bundle.runtime, bundle.owner, control,
                                           'manual-one', 'rewards', reader=bundle.reader)
    observed = worker.observe()
    worker.ask(observed, 'preparation_strategy', '声明协议：单ROI主管补证，原生识别仍未知')
    request = worker.state['decision_request']
    # The old inert fixture's publish records in memory only. Persist this
    # actual Worker-created request for the real manual mailbox CLI reader.
    state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
    state['decision_request'] = copy.deepcopy(request)
    control.write_json(bundle.runtime / 'runner-state.json', state)
    evidence = dict(source='supervising_agent',
        **{key: request[key] for key in ('request_id', 'snapshot_id', 'match_id', 'resume_epoch', 'deadline_at')},
        **{key: request['observation'][key] for key in ('capture_request_id', 'frame_id', 'page')},
        stage='3-6', checkpoint_id=checkpoint['checkpoint_id'], **target,
        findings='原生裁片合成的协议单ROI，不是原整帧历史资格或实际输入结果')
    step_id = uuid.uuid4().hex
    kwargs = dict(manual_id='manual-one', step_id=step_id, operation='collect_rewards',
                  checkpoint_id=checkpoint['checkpoint_id'], reply={'reward_roi': evidence}, wait_seconds=0)
    return kwargs, request


def submit(bundle, kwargs):
    return steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)


def post_input_reads(bundle):
    published = bundle.control.published
    first = next(index for index, value in enumerate(published)
                 if any(action['type'] == 'click' for action in value.get('actions', [])))
    return sum(all(action['type'] in ('observe', 'wait') for action in value.get('actions', []))
               for value in published[first+1:])


class RewardRoiFlowTests(unittest.TestCase):
    metrics = EVIDENCE

    def test_unannotated_native_loop_reads_two_then_one_then_zero_and_keeps_final_root_review(self):
        with fixture.protocol_fixture() as bundle:
            kwargs, unused = prepare(bundle, 'gray_orb', native_unknown=False)
            self.assertEqual([len(bundle.frames[key]['observation']['semantic']['rewards']['targets'])
                              for key in ('two', 'one_after', 'empty')], [2, 1, 0])
            self.assertTrue(all(not bundle.frames[key]['observation']['semantic']['rewards']['uncertain']
                                for key in ('two', 'one_after', 'empty')))
            kwargs['reply'] = None
            submit(bundle, kwargs)
            steps.process(bundle.worker)
            result = submit(bundle, kwargs)
            self.assertEqual(bundle.control.action_index, 2, result)
            self.assertFalse(result['pending'], result)
            entries = [runner.entry.read_json(path) for path in bundle.records.glob('reward-step-*.json')]
            self.assertEqual(len(entries), 2)
            self.assertEqual({value['kind'] for value in entries}, {'gray_orb', 'blue_orb'})
            self.assertTrue(all(value['status'] == 'verified' and value['source'] == 'native_reader'
                                and value['verification_reads'] == 1 for value in entries), entries)
            self.assertTrue(all(value['all_rewards_cleared'] is None for value in entries))
            self.assertEqual(bundle.worker.state['decision_request']['kind'], 'preparation_strategy')
            self.assertEqual(bundle.worker.preparation_checklist(bundle.worker.last_observation)['phase'], 'rewards')
            count = len(bundle.control.published)
            self.assertFalse(steps.process(bundle.worker))
            submit(bundle, kwargs)
            self.assertEqual(len(bundle.control.published), count)
            EVIDENCE.append(dict(contract='native_detector_on_declared_pixel_successors',
                input_requests=2, source='native_reader', target_counts=[2, 1, 0],
                verification_reads=[value['verification_reads'] for value in entries],
                statuses=[value['status'] for value in entries], all_rewards_cleared=None,
                endpoint='preparation_strategy/rewards', real_game_input=False))

    def test_blue_and_gray_same_worker_single_pickup_preserves_unknown_and_never_clears_phase(self):
        for kind in ('blue_orb', 'gray_orb'):
            with self.subTest(kind=kind), fixture.protocol_fixture() as bundle:
                kwargs, request = prepare(bundle, kind)
                before = copy.deepcopy(request['observation']['semantic']['rewards'])
                save, retained = bundle.worker.save_reward_step, []
                def retain_effect(value):
                    save(value)
                    if not retained and value.get('status') == 'pending' and value.get('receipt'):
                        retained.extend(manual_stage.business_pending(bundle.runtime, bundle.owner, bundle.control,
                            bundle.worker.active_match_id, bundle.records))
                bundle.worker.save_reward_step = retain_effect
                self.assertEqual(submit(bundle, kwargs)['status'], 'queued')
                self.assertTrue(steps.process(bundle.worker))
                result = submit(bundle, kwargs)
                pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
                self.assertEqual(pending['status'], 'verified', pending.get('reason'))
                self.assertEqual(bundle.control.action_index, 1)
                self.assertEqual(pending['source'], 'supervising_agent')
                self.assertEqual(pending['manual_step_id'], kwargs['step_id'])
                self.assertEqual(pending['verification_reads'], 1)
                self.assertEqual(post_input_reads(bundle), 1)
                self.assertEqual(len(pending['verification_frames']), 2)
                self.assertNotEqual(*[f['frame_id'] for f in pending['verification_frames']])
                self.assertFalse(result['pending'])
                self.assertEqual(len(retained), 1)
                self.assertEqual(manual_stage.pending_remaining(retained, run=bundle.runtime, owner=bundle.owner,
                    control=bundle.control, records=bundle.records), [])
                self.assertEqual(request['observation']['semantic']['rewards'], before)
                self.assertEqual(pending['before']['observation']['semantic']['rewards']['targets'], [])
                self.assertTrue(pending['before']['observation']['semantic']['rewards']['uncertain'])
                self.assertIsNone(result['result']['all_rewards_cleared'])
                self.assertEqual(bundle.worker.preparation_checklist(bundle.worker.last_observation)['phase'], 'rewards')
                self.assertIsNotNone(runner.manual_state(bundle.runtime))
                count = len(bundle.control.published)
                self.assertFalse(steps.process(bundle.worker))
                self.assertEqual(submit(bundle, kwargs)['status'], result['status'])
                self.assertEqual(len(bundle.control.published), count)
                EVIDENCE.append(dict(kind=kind, contract='native_crop_composite_declared_protocol',
                    input_requests=1, post_input_read_requests=1, outcome=pending['outcome'],
                    native_unknown_retained=True, all_rewards_cleared=None, real_game_input=False))

    def test_no_effect_and_missing_original_frame_share_two_read_budget_without_resending(self):
        for missing in (False, True):
            with self.subTest(missing_original_frame=missing), fixture.protocol_fixture('zero_effect') as bundle:
                kwargs, unused = prepare(bundle)
                if missing:
                    publish = bundle.control.publish_request
                    def drop_frame(value):
                        publish(value)
                        if any(a['type'] == 'click' for a in value.get('actions', [])):
                            result = runner.entry.read_json(bundle.runtime / 'result.json')
                            result.pop('observation')
                            bundle.control.write_json(bundle.runtime / 'result.json', result)
                    bundle.control.publish_request = drop_frame
                submit(bundle, kwargs)
                steps.process(bundle.worker)
                result = submit(bundle, kwargs)
                pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
                self.assertEqual(bundle.control.action_index, 1)
                self.assertEqual(pending['status'], 'pending')
                self.assertEqual(pending['verification_reads'], 2)
                self.assertEqual(post_input_reads(bundle), 2)
                self.assertTrue(result['pending'])
                self.assertTrue(result['result']['receipt_delivery_verified'])
                self.assertTrue(result['result']['reward_step']['pending'])
                self.assertIsNone(result['result']['all_rewards_cleared'])
                count = len(bundle.control.published)
                self.assertFalse(steps.process(bundle.worker))
                submit(bundle, kwargs)
                with self.assertRaises(ValueError):
                    submit(bundle, {**kwargs, 'step_id': uuid.uuid4().hex})
                # Even a distinct freshly requested annotation cannot start a
                # second input while this original business effect is pending.
                steps._ask_current(bundle.worker)
                current = bundle.worker.state['decision_request']
                state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
                state['decision_request'] = copy.deepcopy(current)
                bundle.control.write_json(bundle.runtime / 'runner-state.json', state)
                evidence = {**kwargs['reply']['reward_roi'],
                    **{key: current[key] for key in ('request_id', 'snapshot_id', 'match_id', 'resume_epoch', 'deadline_at')},
                    **{key: current['observation'][key] for key in ('capture_request_id', 'frame_id', 'page')}}
                next_job = {**kwargs, 'step_id': uuid.uuid4().hex, 'reply': {'reward_roi': evidence}}
                submit(bundle, next_job)
                steps.process(bundle.worker)
                self.assertEqual(submit(bundle, next_job)['status'], 'refused')
                self.assertEqual(len(bundle.control.published), count)
                EVIDENCE.append(dict(contract='declared_no_effect', missing_original_frame=missing,
                    input_requests=1, post_input_read_requests=2, pending=True, real_game_input=False))

    def test_moved_target_or_flat_cover_remains_pending_after_bounded_reads(self):
        for mutation in ('move', 'flat_cover'):
            with self.subTest(mutation=mutation), fixture.protocol_fixture() as bundle:
                kwargs, unused = prepare(bundle)
                target = kwargs['reply']['reward_roi']
                x, y, right, bottom = target['bounds']
                with Image.open(io.BytesIO(bundle.frames['one_after']['payload'])) as opened:
                    changed = opened.convert('RGB')
                if mutation == 'move':
                    with Image.open(io.BytesIO(bundle.frames['two']['payload'])) as original:
                        with original.crop((x, y, right, bottom)) as orb:
                            changed.paste(orb, (x+6, y))
                else:
                    changed.paste((25, 35, 55), (x, y, right, bottom))
                data = io.BytesIO()
                changed.save(data, format='PNG')
                payload = data.getvalue()
                digest = hashlib.sha256(payload).hexdigest()
                observed = copy.deepcopy(bundle.frames['one_after']['observation'])
                observed.update(snapshot_id=digest, fingerprint=fingerprint(changed))
                observed['semantic']['rewards'].update(snapshot_id=digest,
                    image_rgb_sha256=hashlib.sha256(changed.tobytes()).hexdigest())
                changed.close()
                bundle.frames['one_after'] = bundle.frames['one_fresh'] = dict(payload=payload, observation=observed)
                submit(bundle, kwargs)
                steps.process(bundle.worker)
                result = submit(bundle, kwargs)
                pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
                self.assertEqual(bundle.control.action_index, 1)
                self.assertEqual(pending['status'], 'pending')
                self.assertEqual(pending['verification_reads'], 2)
                self.assertEqual(post_input_reads(bundle), 2)
                self.assertTrue(result['pending'])
                self.assertIsNone(pending['all_rewards_cleared'])
                count = len(bundle.control.published)
                self.assertFalse(steps.process(bundle.worker))
                submit(bundle, kwargs)
                self.assertEqual(len(bundle.control.published), count)
                EVIDENCE.append(dict(contract='declared_post_input_mutation', mutation=mutation,
                    input_requests=1, post_input_read_requests=2, status=pending['status'],
                    all_rewards_cleared=None, real_game_input=False))

    def test_unknown_delivery_keeps_original_id_and_blocks_next_job_before_any_capture(self):
        with fixture.protocol_fixture('grouped_completed') as bundle:
            kwargs, unused = prepare(bundle)
            submit(bundle, kwargs)
            steps.process(bundle.worker)
            result = submit(bundle, kwargs)
            self.assertEqual(bundle.control.action_index, 1)
            self.assertEqual(post_input_reads(bundle), 0)
            self.assertTrue(result['pending'])
            pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
            self.assertEqual(pending['verification_reads'], 0)
            original = runner.await_existing_receipt(bundle.runtime, bundle.control, pending['request_id'], 0)
            self.assertTrue(runner.manual_receipt_state(original)['unknown_input'])
            count = len(bundle.control.published)
            unannotated = {**kwargs, 'step_id': uuid.uuid4().hex, 'reply': None}
            submit(bundle, unannotated)
            steps.process(bundle.worker)
            stopped = submit(bundle, unannotated)
            self.assertTrue(stopped['pending'])
            self.assertIn(pending['request_id'], stopped['result']['prior_unknown_receipt_ids'])
            self.assertEqual(len(bundle.control.published), count)
            self.assertEqual(runner.await_existing_receipt(bundle.runtime, bundle.control, pending['request_id'], 0), original)

    def test_source_epoch_deadline_and_stage_mutations_at_publication_refuse_zero_input(self):
        for fault in ('source', 'epoch', 'deadline', 'stage'):
            with self.subTest(fault=fault), fixture.protocol_fixture() as bundle:
                kwargs, request = prepare(bundle)
                submit(bundle, kwargs)
                write, hit = bundle.control.write_json, []
                def mutate_at_ledger(path, value):
                    write(path, value)
                    record = value.get('request') or {}
                    if (not hit and Path(path).parent.name == 'request-ledger' and value.get('result') is None
                            and any(a['type'] == 'click' for a in record.get('actions', []))):
                        hit.append(True)
                        if fault == 'source':
                            Path(request['original_png']).write_bytes(b'fixture source replaced before publisher')
                        elif fault == 'epoch':
                            write(bundle.runtime / 'runner-resume-epoch.json', {'id': 'changed-epoch'})
                        elif fault == 'deadline':
                            bundle.worker.deadline = 0
                        else:
                            bundle.worker.last_observation['fields']['stage'] = '3-7'
                bundle.control.write_json = mutate_at_ledger
                steps.process(bundle.worker)
                pending = runner.entry.read_json(bundle.runtime / 'reward-step.json')
                self.assertTrue(hit, 'must reach the real Entry ledger before testing the publication fence')
                self.assertEqual(bundle.control.action_index, 0)
                self.assertEqual(pending['status'], 'refused')
                self.assertFalse(pending['publication_attempted'])


if __name__ == '__main__':
    unittest.main()
