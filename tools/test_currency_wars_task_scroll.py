"""Focused task-list scroll consumers; no game, controller, or native OCR.

Only source-progress.png and actual-progress.png are native evidence, each a
52x35 unedited crop. Full canvases, title/chapter strokes, OCR rows, historical
effect records, and post-scroll frames below are DECLARED protocol fixtures.
The production mailbox, ManualPhase, Worker, GuardedSubmission, Entry lease,
immutable receipts, and frame readers consume those fixtures unchanged.
"""
from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import numpy as np
from PIL import Image, ImageDraw

import currency_wars_broker_entry as entry
import currency_wars_manual_steps as steps
import currency_wars_perception as perception
import currency_wars_runner as runner
import currency_wars_visual_guards as guards
import replay_currency_wars_preparation as preparation_fixture
import test_local_runtime_compatibility as compatibility


ROOT = Path(__file__).resolve().parents[1]
PARENT = 'd2477df7c5620fe606340e068642bf684445a5fb'
EVIDENCE_DIR = ROOT / 'handoff/2026-10-08/ROOT_PR31_SCROLL_GUARD'
NATIVE_SHA256 = {
    'diagnosis.json': 'a38fe6da05d2f061a146aafce5271ed4cc04d9312f7ae3c365b3631fcad7f2b6',
    'source-progress.png': '161df85362d5c3094bf93963290726123aaab077a13cc27ea025a3de10a27a8d',
    'actual-progress.png': 'd3475e5e4fd339a2a0205df120496090f70d4688f03bb94e4e12a1a8e72a3062',
}
BOUNDS = [1460, 324, 1512, 359]
POINT = [1486, 341.5]
TITLE_BOUNDS = [760, 120, 1020, 160]
CHAPTER_TEXT = '第4章'
CHAPTER_BOUNDS = [700, 205, 810, 240]
EVIDENCE = []


def native_sources():
    payloads = {}
    for name, expected in NATIVE_SHA256.items():
        payload = (EVIDENCE_DIR / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError('native scroll evidence bytes changed: ' + name)
        payloads[name] = payload
    diagnosis = json.loads(payloads['diagnosis.json'].decode('utf-8-sig'))
    if (diagnosis.get('candidate') != PARENT or diagnosis.get('target_bounds') != BOUNDS
            or diagnosis.get('point') != POINT or diagnosis.get('target_label') != '0/2'):
        raise ValueError('native scroll diagnosis is not the frozen PR31 pair')
    pixels = {}
    for side in ('source', 'actual'):
        with Image.open(io.BytesIO(payloads[side + '-progress.png'])) as image:
            if image.format != 'PNG' or image.size != (52, 35):
                raise ValueError('native scroll crop dimensions changed')
            pixels[side] = np.array(image.convert('RGB'))
    return diagnosis, pixels


def _strokes(draw, row):
    """Declared high-contrast strokes; never a native OCR accuracy premise."""
    left, top, right, bottom = row['box']
    draw.rectangle([left-2, top-2, right+2, bottom+2], fill=(45, 45, 45))
    for index, unused in enumerate(row['text']):
        x = left + 5 + index * 14
        if x + 8 >= right:
            break
        draw.rectangle([x, top+6, x+2, bottom-6], fill=(242, 242, 242))
        draw.rectangle([x, top+6, x+8, top+8], fill=(242, 242, 242))
        draw.rectangle([x, bottom-8, x+8, bottom-6], fill=(242, 242, 242))


def canvas(side='source', *, change=None, post=False):
    """Unchanged native pixels in a declared 1920x1080 protocol canvas."""
    unused, pixels = native_sources()
    image = Image.new('RGB', (1920, 1080), (45, 45, 45))
    draw = ImageDraw.Draw(image)
    if side == 'ready':
        rows = [dict(text='备战阶段', confidence=.99, box=[420, 28, 520, 64]),
                dict(text='1-1', confidence=.99, box=[530, 28, 610, 64]),
                dict(text='商店', confidence=.99, box=[60, 750, 130, 790]),
                dict(text='出战', confidence=.99, box=[1680, 950, 1800, 990])]
        for row in rows:
            _strokes(draw, row)
        page = 'preparation'
    else:
        shift = (-80 if post else 0)
        if change == 'move':
            shift = -6
        elif post and change in ('no_motion', 'text'):
            shift = 0
        elif post and change == 'reverse':
            shift = 80
        target = [BOUNDS[0], BOUNDS[1]+shift, BOUNDS[2], BOUNDS[3]+shift]
        rows = [dict(text='创业指南', confidence=.99, box=TITLE_BOUNDS.copy()),
                dict(text=CHAPTER_TEXT, confidence=.99, box=CHAPTER_BOUNDS.copy()),
                dict(text='0/2', confidence=.99, box=target)]
        for row in rows[:2]:
            _strokes(draw, row)
        with Image.fromarray(pixels['source' if side == 'source' else 'actual']) as crop:
            image.paste(crop, (target[0], target[1]))
        if change == 'cover':
            ImageDraw.Draw(image).rectangle([target[0], target[1], target[2]-1, target[3]-1],
                                             fill=(65, 65, 65))
        elif change == 'text':
            rows[-1]['text'] = '1/2'
            _strokes(ImageDraw.Draw(image), rows[-1])
        elif change == 'overlay':
            rows.append(dict(text='是否确认', confidence=.99, box=[750, 300, 910, 340]))
            _strokes(ImageDraw.Draw(image), rows[-1])
        page = 'unknown'
    output = io.BytesIO()
    image.save(output, format='PNG')
    payload = output.getvalue()
    observation = dict(page=page, rows=rows,
        fields=dict(stage='1-1', coins=6, level=3, xp='0/4', deployed='0/3'),
        semantic={}, elapsed_ms=0., snapshot_id=hashlib.sha256(payload).hexdigest(),
        fingerprint=perception.fingerprint(image),
        read_contract=dict(version=runner.READ_CONTRACT_VERSION, effective_scope='full', unread=[]))
    image.close()
    return dict(payload=payload, observation=observation)


def action_for(request, *, marked=True):
    proof = dict(snapshot_id=request['snapshot_id'], text='0/2', bounds=BOUNDS.copy())
    if marked:
        proof.update(control_id=guards.TASK_LIST_SCROLL_CONTROL, source='observed_screen',
            capture_request_id=request['observation']['capture_request_id'],
            frame_id=request['observation']['frame_id'], chapter_text=CHAPTER_TEXT,
            chapter_bounds=CHAPTER_BOUNDS.copy())
    return dict(type='scroll', args=POINT + [-120], expected_page='unknown',
        guard_texts=['创业指南', CHAPTER_TEXT, '0/2'], target_evidence=proof,
        reason='声明协议：从当前完整唯一任务文字中心滚动一次，核实同一目标的实际位移')


@contextlib.contextmanager
def worker_fixture(*, before_change=None, after_change=None, marked=True):
    """Actual one-owner consumers over inert publisher/reader boundaries."""
    fixture = compatibility.RuntimeCompatibilityTests()
    frames = dict(ready=canvas('ready'), source=canvas(),
        current=canvas('actual', change=before_change),
        after=canvas('actual', change=after_change, post=True))
    by_digest = {item['observation']['snapshot_id']: item for item in frames.values()}
    with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
        control.published, control.mutation_attempts = [], []
        control.action_index, control.post_reads, control.reader_calls = 0, 0, 0
        control.current_frame = 'ready'
        control.result_mutator, control.on_input_receipt = None, None
        control.lock_depth, control.lock_acquisitions = 0, 0
        control.input_locks, control.receipt_locks = [], []
        submission_mutex = threading.RLock()

        @contextlib.contextmanager
        def submission_lock():
            with submission_mutex:
                control.lock_depth += 1
                control.lock_acquisitions += 1
                try:
                    yield
                finally:
                    control.lock_depth -= 1

        control.submission_lock = submission_lock
        control.validate_actions = lambda actions: [dict(type=action['type'],
            args=[float(value) for value in action.get('args', [])]) for action in actions]
        write_json = control.write_json

        def write(path, value):
            write_json(path, value)
            request = value.get('request', {}) if isinstance(value, dict) else {}
            if (Path(path).parent.name == 'request-ledger' and value.get('result') is None
                    and any(action['type'] not in ('observe', 'wait') for action in request.get('actions', []))):
                control.receipt_locks.append(control.lock_depth > 0)
                if control.on_input_receipt is not None:
                    callback, control.on_input_receipt = control.on_input_receipt, None
                    callback(request)

        control.write_json = write

        def read(path, force=False, *, scope='full', **unused):
            control.reader_calls += 1
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            return copy.deepcopy(by_digest[digest]['observation'])

        reader.read = read

        def inert(value):
            if control.lock_depth <= 0:
                raise AssertionError('Entry publisher ran without its submission lease')
            actions = copy.deepcopy(value.get('actions', []))
            mutations = [action for action in actions if action['type'] not in ('observe', 'wait')]
            control.mutation_attempts.extend(mutations)
            if mutations:
                if mutations != [dict(type='scroll', args=[1486., 341.5, -120.])]:
                    raise AssertionError('task-list fixture received a different physical command')
                control.action_index += 1
                control.input_locks.append(control.lock_depth > 0)
                control.current_frame = 'after'
            elif control.action_index:
                control.post_reads += 1
            frame = frames[control.current_frame]
            payload, digest = frame['payload'], frame['observation']['snapshot_id']
            frame_id = uuid.uuid4().hex
            directory = runtime / 'frames' / (hashlib.sha256(value['id'].encode()).hexdigest() + '-' + frame_id)
            directory.mkdir(parents=True)
            for name in ('original.png', 'preview.png'):
                (directory / name).write_bytes(payload)
            control.published.append(copy.deepcopy(value))
            result = dict(id=value['id'], ok=True, completed=actions,
                input_attempted=bool(mutations), attempted_actions=mutations,
                offline_fixture_receipt=True,
                observation=dict(frame_protocol=1, request_id=value['id'], frame_id=frame_id,
                    captured_at=runner.now(), snapshot=str(directory / 'preview.png'),
                    original=str(directory / 'original.png'), snapshot_sha256=digest,
                    original_sha256=digest, snapshot_size=[1920, 1080], original_size=[1920, 1080]))
            if control.result_mutator is not None:
                control.result_mutator(value, result)
            control.write_json(runtime / 'result.json', result)

        control.publish_request = inert
        # Presence of the actual owner marker makes the original Entry reject
        # any unguarded physical request; the fixture supplies no bypass flag.
        control.write_json(runtime / 'runner-owner.json', owner)
        state = entry.read_json(runtime / 'runner-state.json')
        state['preparation_stage'] = '1-1'
        control.write_json(runtime / 'runner-state.json', state)
        worker = fixture.frame_worker(runtime, records, owner, control, reader)
        preparation_fixture.runner = runner
        preparation_fixture.ready_fixture(worker, ROOT)
        worker.last_preparation_stage = '1-1'
        worker.profile_wait_id, worker.profile_wait_kind = None, None

        def publish(**changes):
            worker.state.update(changes)
            saved = entry.read_json(runtime / 'runner-state.json')
            saved.update(match_id=worker.active_match_id, preparation_stage='1-1',
                         decision_request=copy.deepcopy(worker.state.get('decision_request')))
            control.write_json(runtime / 'runner-state.json', saved)

        worker.publish = publish
        observed = worker.observe()
        worker.ask(observed, 'preparation_strategy', '声明前置：当前奖励已全场复核')
        request = worker.state['decision_request']
        worker.review_preparation(dict(proof=dict(source='observed_screen', snapshot_id=request['snapshot_id'],
            evidence_file=request['evidence_file'], resume_epoch=worker.epoch()),
            value=dict(reviewer='supervising_agent', phase='rewards', stage='1-1', completed=True,
                all_claimed=True, rescanned_after_claim=True,
                findings='声明协议前置的空场复核；没有真实领奖或导航输入')))
        rewards = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
        runner.finish_manual_phase(runtime, owner, control, rewards['checkpoint_id'], [],
            dict(reviewer='supervising_agent', phase='rewards', stage='1-1', completed=True,
                all_claimed=True, rescanned_after_claim=True, findings='声明当前空场；沿用既有人工阶段顺序'),
            reader=reader)
        # A declared already-open page follows; no navigation input is run.
        control.current_frame = 'source'
        checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'startup_guide', reader=reader)
        observed = worker.observe()
        worker.state['decision_request'] = None
        worker.ask(observed, 'unknown_page', '声明当前创业指南任务页的一次受检滚动')
        request = worker.state['decision_request']
        reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
        reply['actions'] = [action_for(request, marked=marked)]
        control.current_frame = 'current'
        yield SimpleNamespace(worker=worker, control=control, reader=reader, runtime=runtime,
            records=records, owner=owner, request=request, reply=reply, checkpoint=checkpoint, frames=frames)


def job_kwargs(bundle, *, reply=None, step_id=None):
    return dict(manual_id='manual-one', step_id=step_id or uuid.uuid4().hex, operation='reviewed_plan',
        checkpoint_id=bundle.checkpoint['checkpoint_id'], reply=reply or bundle.reply, wait_seconds=0)


def manual_job(bundle, *, reply=None):
    kwargs = job_kwargs(bundle, reply=reply)
    queued = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
    if queued['status'] != 'queued':
        raise AssertionError('new manual step was not queued: ' + repr(queued))
    if not steps.process(bundle.worker):
        raise AssertionError('the owning Worker did not consume its manual step')
    return steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs), kwargs


def remember_case(bundle, case, returned, before):
    EVIDENCE.append(dict(case=case, consumer='existing_manual_step_worker_entry',
        fixture_scope='native_target_crop_pair_with_declared_complete_frames_and_ocr',
        status=returned['status'], input_publications=len(bundle.control.mutation_attempts),
        new_entry_publications=len(bundle.control.published)-before,
        actual_page=(bundle.worker.last_observation or {}).get('page'),
        native_page_overwritten=False, real_game_input=False))


class TaskScrollConsumerTests(unittest.TestCase):
    def test_native_pair_scroll_once_verifies_displacement_and_preserves_unknown(self):
        diagnosis, pixels = native_sources()
        delta = np.abs(pixels['source'].astype(np.int16)-pixels['actual'].astype(np.int16))
        self.assertEqual(int(np.max(delta)), diagnosis['max_abs_rgb_difference'])
        self.assertAlmostEqual(float(np.mean(delta)), diagnosis['mean_abs_rgb_difference'], places=14)
        self.assertAlmostEqual(float(np.mean(np.any(delta != 0, axis=2))),
                               diagnosis['changed_pixel_fraction'], places=14)
        with worker_fixture() as bundle:
            historic_id = uuid.uuid4().hex
            historical = dict(schema='startup-guide-navigation/v1', request_id=historic_id,
                status='pending', outcome='unknown', automatic_phase_completion=False,
                declaration='Historical unknown-effect fixture; no native historical input receipt claimed.')
            historical_paths = [bundle.runtime / 'startup-navigation.json',
                                bundle.records / ('guide-navigation-' + historic_id + '.json')]
            for path in historical_paths:
                bundle.control.write_json(path, historical)
            original_bytes = {path: path.read_bytes() for path in historical_paths}
            before = len(bundle.control.published)
            returned, kwargs = manual_job(bundle)
            self.assertEqual(returned['status'], 'returned', returned)
            self.assertFalse(returned['pending'], returned)
            result = returned['result']['task_scroll']
            self.assertEqual((result['status'], result['outcome']), ('verified', '位移已核实'))
            self.assertEqual(result['verification_reads'], 0)
            self.assertFalse(result['automatic_phase_completion'])
            self.assertEqual(bundle.control.action_index, 1)
            self.assertEqual(bundle.control.post_reads, 0)
            self.assertEqual(bundle.control.input_locks, [True])
            self.assertEqual(bundle.control.receipt_locks, [True])
            self.assertEqual(len(bundle.control.published)-before, 2)
            self.assertEqual(bundle.worker.last_observation['page'], 'unknown')
            self.assertEqual(bundle.worker.preparation_checklist(bundle.worker.last_observation)['phase'], 'startup_guide')
            self.assertNotIn('startup_guide', bundle.worker.preparation_reviews)
            self.assertFalse((bundle.runtime / 'runner-battle-approval.json').exists())
            record_path = Path(result['record_file'])
            self.assertEqual(record_path, bundle.records / ('task-scroll-' + bundle.request['request_id'] + '.json'))
            record = entry.read_json(record_path)
            self.assertEqual(record['input_request_id'], result['input_request_id'])
            receipt = runner.await_existing_receipt(bundle.runtime, bundle.control, result['input_request_id'], 0)
            physical = [action for action in receipt['result']['completed'] if action['type'] not in ('observe', 'wait')]
            self.assertEqual(physical, [dict(type='scroll', args=[1486., 341.5, -120.])])
            self.assertEqual(receipt['id'], result['input_request_id'])
            publications, raw = len(bundle.control.published), record_path.read_bytes()
            repeated = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
            self.assertEqual(repeated, returned)
            self.assertFalse(steps.process(bundle.worker))
            self.assertEqual(len(bundle.control.published), publications)
            self.assertEqual(record_path.read_bytes(), raw)
            for path, payload in original_bytes.items():
                self.assertEqual(path.read_bytes(), payload)
            remember_case(bundle, '原生低幅目标、声明位移、同一步只查询', returned, before)
            EVIDENCE.append(dict(case='原生配对与历史未知档案', native_file_sha256=NATIVE_SHA256,
                native_roi=BOUNDS, max_abs_rgb=int(np.max(delta)), mean_abs_rgb=float(np.mean(delta)),
                changed_fraction=float(np.mean(np.any(delta != 0, axis=2))),
                original_full_png_sha256=[diagnosis['source_sha256'], diagnosis['actual_sha256']],
                original_full_pngs_available=False, declared_shift_y=-80,
                historical_unknown_records_byte_unchanged=True,
                verification_reads=0, automatic_phase_completion=False,
                real_scroll_effect_verified=False, native_ocr_executed=False))

    def test_changed_position_cover_text_and_overlay_refuse_before_input(self):
        for change in ('move', 'cover', 'text', 'overlay'):
            with self.subTest(change=change), worker_fixture(before_change=change) as bundle:
                before = len(bundle.control.published)
                returned, unused = manual_job(bundle)
                self.assertEqual(returned['status'], 'refused', returned)
                self.assertEqual(bundle.control.mutation_attempts, [])
                self.assertEqual(bundle.control.action_index, 0)
                self.assertEqual(bundle.worker.last_observation['page'], 'unknown')
                remember_case(bundle, '输入前拒绝:' + change, returned, before)

    def test_wrong_source_bytes_capture_and_current_frame_refuse_through_consumers(self):
        for change in ('source_bytes', 'source_capture', 'current_frame'):
            with self.subTest(change=change), worker_fixture() as bundle:
                kwargs = job_kwargs(bundle)
                self.assertEqual(steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)['status'], 'queued')
                before = len(bundle.control.published)
                if change == 'source_bytes':
                    with Path(bundle.request['original_png']).open('ab') as stream:
                        stream.write(b'declared-wrong-source')
                elif change == 'source_capture':
                    capture_id = bundle.request['observation']['capture_request_id']
                    receipt_path = bundle.runtime / 'request-ledger' / (hashlib.sha256(capture_id.encode()).hexdigest() + '.json')
                    receipt = entry.read_json(receipt_path)
                    receipt['result']['observation']['request_id'] = 'wrong-declared-capture'
                    bundle.control.write_json(receipt_path, receipt)
                else:
                    def change_frame(unused_request, result):
                        result['observation']['frame_id'] = 'f'*32
                    bundle.control.result_mutator = change_frame
                self.assertTrue(steps.process(bundle.worker))
                returned = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertEqual(returned['status'], 'refused', returned)
                self.assertEqual(bundle.control.mutation_attempts, [])
                self.assertEqual(bundle.control.action_index, 0)
                remember_case(bundle, '来源拒绝:' + change, returned, before)

    def test_current_phase_invalidated_after_queue_and_inside_entry_lock_refuses(self):
        for point in ('after_queue', 'inside_entry_lock', 'checkpoint_inside_entry_lock'):
            with self.subTest(point=point), worker_fixture() as bundle:
                kwargs = job_kwargs(bundle)
                self.assertEqual(steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)['status'], 'queued')
                before = len(bundle.control.published)
                invalidations = []

                def invalidate(unused_request=None):
                    invalidations.append(bundle.control.lock_depth)
                    if point == 'checkpoint_inside_entry_lock':
                        path = bundle.runtime / 'manual-results' / (bundle.checkpoint['checkpoint_id'] + '.json')
                        checkpoint = entry.read_json(path)
                        checkpoint['phase'] = 'inventory_cleanup'
                        bundle.control.write_json(path, checkpoint)
                    else:
                        bundle.worker.preparation_reviews.clear()

                if point == 'after_queue':
                    invalidate()
                else:
                    bundle.control.on_input_receipt = invalidate
                self.assertTrue(steps.process(bundle.worker))
                returned = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertEqual(returned['status'], 'refused', returned)
                self.assertEqual(bundle.control.mutation_attempts, [])
                self.assertEqual(bundle.control.action_index, 0)
                self.assertEqual(len(invalidations), 1)
                if point != 'after_queue':
                    self.assertGreater(invalidations[0], 0)
                    self.assertEqual(bundle.control.receipt_locks, [True])
                self.assertEqual(bundle.worker.preparation_checklist(bundle.worker.last_observation)['phase'],
                                 'startup_guide' if point == 'checkpoint_inside_entry_lock' else 'rewards')
                remember_case(bundle, '当前阶段失效:' + point, returned, before)

    def test_request_and_manual_deadline_crossed_during_final_local_guard_refuse(self):
        for deadline in ('request', 'manual_step'):
            with self.subTest(deadline=deadline), worker_fixture() as bundle:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=5)
                if deadline == 'request':
                    bundle.request['deadline_at'] = expires_at.isoformat()
                    bundle.worker.publish()
                kwargs = job_kwargs(bundle)
                self.assertEqual(steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)['status'], 'queued')
                if deadline == 'manual_step':
                    path = bundle.runtime / 'manual-steps' / (kwargs['step_id'] + '.json')
                    item = entry.read_json(path)
                    item['expires_at'] = expires_at.isoformat()
                    bundle.control.write_json(path, item)
                before = len(bundle.control.published)
                clock = dict(delta=timedelta(0), advanced=False, guard_calls=0, lock_depth=0)
                real_monotonic = runner.time.monotonic
                original_guard = runner.stable_task_list_scroll

                class Clock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return datetime.now(tz) + clock['delta']

                def finish_local_guard(*args, **options):
                    allowed = original_guard(*args, **options)
                    clock['guard_calls'] += 1
                    # The real Entry has persisted this still-unpublished
                    # input receipt and still holds its original lease. Only
                    # elapsed wall/monotonic time is injected after local IO.
                    if allowed and bundle.control.receipt_locks and not clock['advanced']:
                        clock.update(advanced=True, lock_depth=bundle.control.lock_depth,
                            delta=expires_at-datetime.now(timezone.utc)+timedelta(seconds=1))
                    return allowed

                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(runner, 'stable_task_list_scroll', side_effect=finish_local_guard))
                    stack.enter_context(patch.object(runner.time, 'monotonic',
                        side_effect=lambda: real_monotonic()+clock['delta'].total_seconds()))
                    stack.enter_context(patch.object(runner if deadline == 'request' else steps, 'datetime', Clock))
                    self.assertTrue(steps.process(bundle.worker))
                returned = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertEqual(returned['status'], 'refused', returned)
                self.assertTrue(clock['advanced'])
                self.assertGreater(clock['lock_depth'], 0)
                self.assertEqual(bundle.control.receipt_locks, [True])
                self.assertEqual(bundle.control.mutation_attempts, [])
                self.assertEqual(bundle.control.action_index, 0)
                result = returned['result']['task_scroll']
                self.assertEqual((result['status'], result['outcome']), ('refused', '未发布'))
                remember_case(bundle, '局部复核跨期限:' + deadline, returned, before)
                EVIDENCE.append(dict(case='最后局部守卫后期限失效', deadline=deadline,
                    real_local_guard_calls=clock['guard_calls'], elapsed_clock_injected=True,
                    injected_inside_entry_lock=True, input_publications=0, real_game_input=False))

    def test_unverified_postconditions_return_pending_without_extra_reads_or_replay(self):
        for change in ('no_motion', 'reverse', 'text', 'overlay'):
            with self.subTest(change=change), worker_fixture(after_change=change) as bundle:
                before = len(bundle.control.published)
                returned, kwargs = manual_job(bundle)
                self.assertEqual(returned['status'], 'returned', returned)
                self.assertTrue(returned['pending'], returned)
                result = returned['result']['task_scroll']
                self.assertEqual((result['status'], result['outcome']), ('pending', '效果未确认'))
                self.assertEqual(result['verification_reads'], 0)
                self.assertFalse(result['automatic_phase_completion'])
                self.assertEqual(bundle.control.action_index, 1)
                self.assertEqual(bundle.control.post_reads, 0)
                self.assertEqual(len(bundle.control.published)-before, 2)
                raw = Path(result['record_file']).read_bytes()
                publications = len(bundle.control.published)
                repeated = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertEqual(repeated, returned)
                self.assertFalse(steps.process(bundle.worker))
                self.assertEqual(len(bundle.control.published), publications)
                self.assertEqual(Path(result['record_file']).read_bytes(), raw)
                with self.assertRaises(ValueError):
                    steps.submit(bundle.runtime, bundle.owner, bundle.control, **dict(kwargs, step_id=uuid.uuid4().hex))
                self.assertEqual(bundle.control.action_index, 1)
                remember_case(bundle, '动作后效果待核:' + change, returned, before)

    def test_missing_notification_counts_one_readonly_fallback_without_resending_scroll(self):
        with worker_fixture() as bundle:
            missing = []

            def remove_input_frame(request, result):
                if any(action['type'] == 'scroll' for action in request.get('actions', [])):
                    missing.append(request['id'])
                    result.pop('observation')

            bundle.control.result_mutator = remove_input_frame
            before = len(bundle.control.published)
            returned, kwargs = manual_job(bundle)
            self.assertEqual(returned['status'], 'returned', returned)
            self.assertFalse(returned['pending'], returned)
            result = returned['result']['task_scroll']
            self.assertEqual((result['status'], result['outcome']), ('verified', '位移已核实'))
            self.assertEqual(result['verification_reads'], 1)
            self.assertFalse(result['automatic_phase_completion'])
            self.assertEqual(missing, [result['input_request_id']])
            self.assertEqual(bundle.control.action_index, 1)
            self.assertEqual(bundle.control.post_reads, 1)
            self.assertEqual(len(bundle.control.published)-before, 3)
            receipt = runner.await_existing_receipt(bundle.runtime, bundle.control, result['input_request_id'], 0)
            self.assertNotIn('observation', receipt['result'])
            self.assertEqual(runner.manual_receipt_state(receipt)['state'], 'completed')
            actual = bundle.worker.last_observation
            self.assertNotEqual(actual['capture_request_id'], result['input_request_id'])
            recovery = runner.await_existing_receipt(bundle.runtime, bundle.control, actual['capture_request_id'], 0)
            self.assertEqual(recovery['request']['actions'], [dict(type='observe', args=[])])
            record_path = Path(result['record_file'])
            publications, raw = len(bundle.control.published), record_path.read_bytes()
            self.assertEqual(steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs), returned)
            self.assertFalse(steps.process(bundle.worker))
            self.assertEqual(len(bundle.control.published), publications)
            self.assertEqual(record_path.read_bytes(), raw)
            remember_case(bundle, '通知缺图仅一次计数回补', returned, before)
            EVIDENCE.append(dict(case='通知缺图回补计数', original_input_request_id=result['input_request_id'],
                original_delivery='completed', original_notification_image_missing=True,
                input_publications=1, readonly_fallbacks=1, verification_reads=1,
                same_step_queried_without_replay=True, real_game_input=False))

    def test_caller_preflight_missing_text_and_outside_point_create_no_job_or_capture(self):
        for marked in (False, True):
            for change in ('missing_text', 'point_outside'):
                with self.subTest(marked=marked, change=change), worker_fixture(marked=marked) as bundle:
                    reply = copy.deepcopy(bundle.reply)
                    action = reply['actions'][0]
                    if change == 'missing_text':
                        action['target_evidence'].pop('text')
                    else:
                        action['args'][0] = BOUNDS[2]
                    kwargs = job_kwargs(bundle, reply=reply)
                    before, reads, pauses = len(bundle.control.published), bundle.control.reader_calls, len(bundle.control.pauses)
                    with self.assertRaises(ValueError):
                        steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                    self.assertFalse((bundle.runtime / 'manual-steps' / (kwargs['step_id'] + '.json')).exists())
                    self.assertFalse(steps.process(bundle.worker))
                    self.assertEqual(len(bundle.control.published), before)
                    self.assertEqual(bundle.control.reader_calls, reads)
                    self.assertEqual(len(bundle.control.pauses), pauses)
                    self.assertEqual(bundle.control.mutation_attempts, [])
                    self.assertEqual(bundle.worker.state['decision_request']['request_id'], bundle.request['request_id'])
                    EVIDENCE.append(dict(case='调用端预检:' + change, marked=marked, job_created=False,
                        new_entry_publications=0, input_publications=0, new_reader_calls=0,
                        worker_paused=False, real_game_input=False))

    def test_critical_actions_cannot_reuse_task_scroll_marker(self):
        cases = (dict(type='key', args=[68]),
                 dict(type='click_point', args=POINT.copy()),
                 dict(type='click_text', text='出战', exact=True))
        for critical in cases:
            with self.subTest(action=critical['type']), worker_fixture() as bundle:
                kwargs = job_kwargs(bundle)
                self.assertEqual(steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)['status'], 'queued')
                mailbox = bundle.runtime / 'manual-steps' / (kwargs['step_id'] + '.json')
                item = entry.read_json(mailbox)
                item['payload']['reply']['actions'][0].update(critical)
                # A corrupted queued action still crosses the real Worker
                # consumer; no source-code scan stands in for this refusal.
                bundle.control.write_json(mailbox, item)
                before = len(bundle.control.published)
                self.assertTrue(steps.process(bundle.worker))
                returned = entry.read_json(mailbox)
                self.assertEqual(returned['status'], 'refused', returned)
                self.assertEqual(len(bundle.control.published), before)
                self.assertEqual(bundle.control.mutation_attempts, [])
                remember_case(bundle, '关键动作标记误用:' + critical['type'], returned, before)

    def test_unmarked_scroll_still_requires_byte_identical_target(self):
        with worker_fixture(marked=False) as bundle:
            before = len(bundle.control.published)
            returned, unused = manual_job(bundle)
            self.assertEqual(returned['status'], 'refused', returned)
            self.assertIn('目标ROI实际已变', returned['error'])
            self.assertEqual(bundle.control.mutation_attempts, [])
            self.assertEqual(bundle.control.action_index, 0)
            remember_case(bundle, '普通滚动保持逐字节守卫', returned, before)
