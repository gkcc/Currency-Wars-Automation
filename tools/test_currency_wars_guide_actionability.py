"""Current startup-guide crops and declared single-owner consumer protocols.

The six unedited PNGs are native cropped calibration evidence. Complete images
used below are DECLARED canvases: their stage/shop/battle glyphs, OCR rows,
successor pages and inert transport are protocol premises, never original game
frames or proof that the historical request became authorized.
"""
from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
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
EVIDENCE_DIR = ROOT / 'handoff/2026-10-08/ROOT_PR28_STARTUP_GUIDE'
EVIDENCE = []


def native_sources():
    diagnosis = json.loads((EVIDENCE_DIR / 'diagnosis.json').read_text(encoding='utf-8-sig'))
    crops = {}
    for item in diagnosis['crops']:
        path = ROOT / item['file']
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise ValueError('native crop bytes differ from the published source map')
        with Image.open(io.BytesIO(payload)) as image:
            x, y, right, bottom = item['source_roi']
            if image.format != 'PNG' or image.size != (right-x, bottom-y):
                raise ValueError('native crop dimensions differ from the published ROI')
            crops[path.stem] = np.array(image.convert('RGB'))
    return diagnosis, crops


def declaration_rows(side='source'):
    diagnosis, unused = native_sources()
    key = 'original_rows' if side == 'source' else 'fresh_rows'
    return [copy.deepcopy(row) for check in diagnosis['label_checks'] for row in check[key]]


def local_evidence(navigation, header, *, supervising=False):
    """The same two production crop consumers, without invented full frames."""
    nav, text = {}, {}
    nav_ok = guards._guide_navigation_pair(navigation, nav, supervising=supervising)
    text_ok = guards._preparation_header_crops_equal(header, text)
    return dict(allowed=bool(nav_ok and text_ok), navigation=nav, header=text)


def _surrogate(draw, row):
    """Declared strokes only: no native OCR or CJK-render accuracy claim."""
    x, y, right, bottom = row['box']
    draw.rectangle([x-3, y, right+3, bottom+3], fill=(45, 45, 45))
    top = max(y + 7, 67) if row['text'] == '1-1' else y + 7
    for index, unused in enumerate(row['text']):
        left = x + 5 + index * 14
        if left + 8 >= right:
            break
        draw.rectangle([left, top, left+2, bottom-6], fill=(242, 242, 242))
        draw.rectangle([left, top, left+8, top+2], fill=(242, 242, 242))
        draw.rectangle([left, bottom-8, left+8, bottom-6], fill=(242, 242, 242))


def canvas(side='source', *, change=None, novel_icon=False):
    """Embed actual crops unchanged; all uncaptured regions are declared."""
    unused, crops = native_sources()
    rows = declaration_rows(side)
    image = Image.new('RGB', (1920, 1080), (45, 45, 45))
    draw = ImageDraw.Draw(image)
    for row in rows:
        if row['text'] in ('1-1', '出战', '商店'):
            _surrogate(draw, row)
    for name, offset in (('navigation', (1570, 38)), ('header', (410, 20)),
                         ('front-caption', (895, 285))):
        with Image.fromarray(crops[side + '-' + name]) as crop:
            image.paste(crop, offset)
    if novel_icon:
        # A declared different appearance is a supervisor-protocol fixture,
        # never an additional native/generalization positive.
        draw = ImageDraw.Draw(image)
        draw.rectangle([1666, 46, 1707, 86], fill=(65, 65, 65))
        for x in (1675, 1694):
            draw.rectangle([x, 52, x+3, 80], fill=(244, 244, 244))
        for y in (60, 71):
            draw.rectangle([1670, y, 1701, y+3], fill=(244, 244, 244))
    page, stage = 'preparation', '1-1'
    if change == 'background':
        # Large unrelated pixel change is explicitly synthetic.
        ImageDraw.Draw(image).rectangle([100, 400, 1200, 650], fill=(150, 40, 90))
    elif change == 'front_caption':
        rows[-1]['text'] = '装饰前台区城'
        rows[-1]['confidence'] = .83
        ImageDraw.Draw(image).rectangle([895, 285, 1030, 330], fill=(80, 65, 60))
    elif change == 'page':
        page = 'environment'
    elif change == 'stage':
        stage = '1-2'
        rows[1]['text'] = stage
    elif change == 'overlay':
        rows.append(dict(text='是否确认', confidence=.99, box=[700, 300, 850, 340]))
    elif change == 'header_text':
        rows[0]['text'] = '战斗阶段'
    elif change == 'anchor_duplicate':
        rows.append(copy.deepcopy(rows[0]))
    elif change in ('icon_cover', 'icon_move', 'icon_duplicate', 'header_blank'):
        pixels = np.array(image)
        if change == 'header_blank':
            pixels[31:62, 428:515] = (70, 70, 70)
        else:
            icon = pixels[46:87, 1666:1708].copy()
            if change in ('icon_cover', 'icon_move'):
                pixels[46:87, 1666:1708] = (65, 65, 65)
            if change == 'icon_move':
                pixels[46:87, 1673:1715] = icon
            elif change == 'icon_duplicate':
                pixels[46:87, 1581:1623] = icon
        image.close()
        image = Image.fromarray(pixels)
    output = io.BytesIO()
    image.save(output, format='PNG')
    payload = output.getvalue()
    observed = dict(page=page, rows=rows, fields=dict(stage=stage, coins=6, level=3, xp='0/4', deployed='0/3'),
        semantic={}, elapsed_ms=0., snapshot_id=hashlib.sha256(payload).hexdigest(), fingerprint=perception.fingerprint(image),
        read_contract=dict(version=runner.READ_CONTRACT_VERSION, effective_scope='full', unread=[]))
    image.close()
    return dict(payload=payload, observation=observed)


def action_for(request, *, checkpoint_id=None):
    proof = dict(control_id=guards.PREPARATION_GUIDE_CONTROL,
                 snapshot_id=request['snapshot_id'], bounds=guards.PREPARATION_GUIDE_BOUNDS.copy())
    if checkpoint_id is not None:
        proof.update(source='supervising_agent',
            **{key: request[key] for key in ('request_id', 'match_id', 'resume_epoch', 'deadline_at')},
            **{key: request['observation'][key] for key in ('capture_request_id', 'frame_id', 'page')},
            stage='1-1', checkpoint_id=checkpoint_id, target_unique=True, target_exposed=True,
            expected_successor='创业指南', findings='Declared current supervisor icon review; native classifier results remain separate')
    return dict(type='click_point', args=guards.PREPARATION_GUIDE_POINT.copy(), expected_page='preparation',
        guard_texts=['备战阶段', '出战', '商店'], target_evidence=proof,
        reason='Declared one-guide-navigation protocol', expected_change='创业指南')


@contextlib.contextmanager
def visual_pair(change=None, *, supervised=False):
    import tempfile
    old, fresh = canvas(), canvas('current', change=change)
    with tempfile.TemporaryDirectory(prefix='cw-guide-local-protocol-') as temporary:
        directory = Path(temporary)
        original_path, current_path = directory / 'original.png', directory / 'current.png'
        original_path.write_bytes(old['payload'])
        current_path.write_bytes(fresh['payload'])
        for value, capture in ((old['observation'], 'declared-old'), (fresh['observation'], 'declared-current')):
            value.update(capture_request_id=capture, frame_id=capture + '-frame', captured_at=runner.now())
        request = dict(request_id='declared-request', kind='preparation_strategy',
            snapshot_id=old['observation']['snapshot_id'], observation=old['observation'],
            original_png=str(original_path), match_id='match', resume_epoch='old-epoch',
            deadline_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
            preparation_checklist=dict(phase='startup_guide'))
        action = action_for(request, checkpoint_id='a'*32 if supervised else None)
        yield action, request, fresh['observation'], current_path


def successor_frame(*, title=False):
    """An explicit current-title result with no invented guide classification."""
    image = Image.new('RGB', (1920, 1080), (55, 65, 80) if title else (25, 35, 50))
    rows = [dict(text='创业指南', confidence=.99, box=[760, 120, 1020, 160])] if title else []
    if title:
        _surrogate(ImageDraw.Draw(image), rows[0])
    output = io.BytesIO()
    image.save(output, format='PNG')
    payload = output.getvalue()
    observed = dict(page='unknown', rows=rows, fields=dict(stage='1-1'), semantic={}, elapsed_ms=0.,
        snapshot_id=hashlib.sha256(payload).hexdigest(), fingerprint=perception.fingerprint(image),
        read_contract=dict(version=runner.READ_CONTRACT_VERSION, effective_scope='full', unread=[]))
    image.close()
    return dict(payload=payload, observation=observed)


@contextlib.contextmanager
def worker_fixture(*, manual=False, change=None, successor='title', novel_icon=False):
    """Existing single Entry transport, Worker and ManualPhase; no processes."""
    fixture = compatibility.RuntimeCompatibilityTests()
    frames = dict(original=canvas(novel_icon=novel_icon),
        current=canvas('current', change=change, novel_icon=novel_icon),
        title=successor_frame(title=True), unknown=successor_frame())
    by_digest = {item['observation']['snapshot_id']: item for item in frames.values()}
    with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
        control.published, control.action_index, control.post_reads = [], 0, 0
        control.result_mutator = None
        control.successor_mode = successor
        control.current_frame, control.foreground = 'original', True
        control.status = lambda: dict(ready=True, paused=False, input_halted=False,
            game_foreground=control.foreground, broker_pid=123, broker_creation_time='456', pause_id=None)
        control.validate_actions = lambda actions: [dict(type=action['type'],
            args=[float(value) for value in action.get('args', [])]) for action in actions]
        def read(path, force=False, *, scope='full', **unused):
            observed = copy.deepcopy(by_digest[hashlib.sha256(Path(path).read_bytes()).hexdigest()]['observation'])
            return observed
        reader.read = read
        def inert(value):
            actions = copy.deepcopy(value.get('actions', []))
            mutations = [action for action in actions if action['type'] not in ('observe', 'wait')]
            if mutations:
                if mutations != [dict(type='click', args=[1687.0, 65.0])]:
                    raise AssertionError('guide fixture received a different input')
                control.action_index += 1
                control.current_frame = 'title' if control.successor_mode == 'title' else 'unknown'
            elif control.action_index:
                control.post_reads += 1
                if control.successor_mode == 'delayed':
                    control.current_frame = 'title'
            frame = frames[control.current_frame]
            data, snapshot = frame['payload'], frame['observation']['snapshot_id']
            frame_id = uuid.uuid4().hex
            directory = runtime / 'frames' / (hashlib.sha256(value['id'].encode()).hexdigest() + '-' + frame_id)
            directory.mkdir(parents=True)
            for name in ('original.png', 'preview.png'):
                (directory / name).write_bytes(data)
            control.published.append(copy.deepcopy(value))
            result = dict(id=value['id'], ok=True, completed=actions, input_attempted=bool(mutations),
                attempted_actions=mutations, offline_fixture_receipt=True,
                observation=dict(frame_protocol=1, request_id=value['id'], frame_id=frame_id,
                    captured_at=runner.now(), snapshot=str(directory / 'preview.png'),
                    original=str(directory / 'original.png'), snapshot_sha256=snapshot,
                    original_sha256=snapshot, snapshot_size=[1920, 1080], original_size=[1920, 1080]))
            if control.result_mutator is not None:
                control.result_mutator(value, result)
            control.write_json(runtime / 'result.json', result)
        control.publish_request = inert
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
        worker.ask(observed, 'preparation_strategy', 'Declared current empty reward review')
        request = worker.state['decision_request']
        worker.review_preparation(dict(proof=dict(source='observed_screen', snapshot_id=request['snapshot_id'],
            evidence_file=request['evidence_file'], resume_epoch=worker.epoch()),
            value=dict(reviewer='supervising_agent', phase='rewards', stage='1-1', completed=True,
                all_claimed=True, rescanned_after_claim=True, findings='Declared complete empty field; no real pickup claim')))
        checkpoint = None
        if manual:
            rewards_checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            runner.finish_manual_phase(runtime, owner, control, rewards_checkpoint['checkpoint_id'], [],
                dict(reviewer='supervising_agent', phase='rewards', stage='1-1', completed=True,
                    all_claimed=True, rescanned_after_claim=True,
                    findings='Declared current full-field review; original ManualPhase order is preserved'), reader=reader)
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'startup_guide', reader=reader)
        else:
            (runtime / 'runner-manual.json').unlink()
        observed = worker.observe()
        worker.state['decision_request'] = None
        worker.ask(observed, 'preparation_strategy', 'Declared one current guide navigation')
        request = worker.state['decision_request']
        control.current_frame = 'current'
        reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
        reply['actions'] = [action_for(request, checkpoint_id=checkpoint['checkpoint_id'] if checkpoint else None)]
        yield SimpleNamespace(worker=worker, control=control, reader=reader, runtime=runtime, records=records,
            owner=owner, request=request, reply=reply, checkpoint=checkpoint, frames=frames)


def manual_job(bundle, *, reply=None, step_id=None):
    kwargs = dict(manual_id='manual-one', step_id=step_id or uuid.uuid4().hex, operation='reviewed_plan',
        checkpoint_id=bundle.checkpoint['checkpoint_id'], reply=reply or bundle.reply, wait_seconds=0)
    queued = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
    if queued['status'] == 'queued':
        steps.process(bundle.worker)
    return steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs), kwargs


class GuideCropTests(unittest.TestCase):
    def test_native_crops_bind_original_sources_and_calibrate_local_glyph_and_icon(self):
        diagnosis, crops = native_sources()
        navigation = [crops[side+'-navigation'] for side in ('source', 'current')]
        header = [crops[side+'-header'][11:42, 18:105] for side in ('source', 'current')]
        evidence = local_evidence(navigation, header)
        self.assertTrue(evidence['allowed'], evidence)
        self.assertEqual(len(crops), 6)
        self.assertEqual(len({item['source_png_sha256'] for item in diagnosis['crops']}), 2)
        self.assertEqual([check['original_rows'][0]['text'] for check in diagnosis['label_checks']][-1], '昌前台区域')
        EVIDENCE.append(dict(contract='native_cropped_calibration', evidence=evidence,
            native_crop_count=6, original_full_frames_available=False,
            historical_actual_observation_reconstructed=False, historical_qualification=False,
            real_input=False, independent_generalization_positive=False))

    def test_native_derived_white_blank_cover_move_and_duplicate_remain_rejected(self):
        unused, crops = native_sources()
        navigation = [crops[side+'-navigation'] for side in ('source', 'current')]
        header = [crops[side+'-header'][11:42, 18:105] for side in ('source', 'current')]
        for change in ('white_header', 'blank_header', 'cover_icon', 'move_icon', 'duplicate_icon'):
            with self.subTest(change=change):
                nav, text = [image.copy() for image in navigation], [image.copy() for image in header]
                if change == 'white_header':
                    old = text[1]
                    mask = (np.max(old, axis=2) >= 100) & (np.max(old, axis=2) <= 190)
                    old[mask] = (245, 245, 245)
                elif change == 'blank_header':
                    text[1][:] = (70, 70, 70)
                else:
                    icon = nav[1][8:49, 96:138].copy()
                    if change in ('cover_icon', 'move_icon'):
                        nav[1][8:49, 96:138] = (65, 65, 65)
                    if change == 'move_icon':
                        nav[1][8:49, 103:145] = icon
                    elif change == 'duplicate_icon':
                        nav[1][8:49, 11:53] = icon
                result = local_evidence(nav, text)
                self.assertFalse(result['allowed'], result)


class GuideVisualProtocolTests(unittest.TestCase):
    def test_current_guide_allows_native_crops_and_ignores_irrelevant_background_front_caption(self):
        for change in (None, 'background', 'front_caption'):
            with self.subTest(change=change), visual_pair(change) as values:
                diagnostic = {}
                self.assertTrue(guards.stable_preparation_icon_target(*values, diagnostic), diagnostic)

    def test_changed_relevant_pixels_semantics_scope_and_sources_refuse(self):
        for change in ('page', 'stage', 'overlay', 'header_text', 'anchor_duplicate', 'icon_cover',
                       'icon_move', 'icon_duplicate', 'header_blank'):
            with self.subTest(change=change), visual_pair(change) as values:
                self.assertFalse(guards.stable_preparation_icon_target(*values))
        for change in ('wrong_kind', 'source_bytes', 'snapshot', 'control', 'point', 'bounds'):
            with self.subTest(change=change), visual_pair() as values:
                action, request, actual, path = values
                if change == 'wrong_kind':
                    request['kind'] = 'shop_strategy'
                elif change == 'source_bytes':
                    Path(request['original_png']).write_bytes(b'declared-corrupt-source')
                elif change == 'snapshot':
                    action['target_evidence']['snapshot_id'] = '0'*64
                elif change == 'control':
                    action['target_evidence']['control_id'] = 'arbitrary-control'
                elif change == 'point':
                    action['args'] = [1690, 65]
                elif change == 'bounds':
                    action['target_evidence']['bounds'] = [1, 2, 30, 40]
                self.assertFalse(guards.stable_preparation_icon_target(action, request, actual, path))


class GuideConsumerTests(unittest.TestCase):
    def test_native_and_separate_supervisor_unknown_shape_each_send_once_and_verify_current_title(self):
        for supervised in (False, True):
            with self.subTest(supervised=supervised), worker_fixture(manual=supervised,
                    novel_icon=supervised, successor='delayed' if supervised else 'title') as bundle:
                worker, control = bundle.worker, bundle.control
                if supervised:
                    with Image.open(io.BytesIO(bundle.frames['original']['payload'])) as opened:
                        original_pixels = np.array(opened.convert('RGB'))
                    self.assertIsNone(guards._guide_icon_location(original_pixels))
                    returned, kwargs = manual_job(bundle)
                    self.assertEqual(returned['status'], 'returned', returned)
                    self.assertFalse(returned['pending'], returned)
                    result = returned['result']['startup_navigation']
                    count = len(control.published)
                    repeated = steps.submit(bundle.runtime, bundle.owner, control, **kwargs)
                    self.assertEqual(repeated['status'], 'returned')
                    self.assertFalse(steps.process(worker))
                    self.assertEqual(len(control.published), count)
                else:
                    worker.execute_plan(bundle.reply)
                    result = worker.startup_navigation_result
                self.assertEqual(control.action_index, 1)
                self.assertEqual(result['outcome'], 'expected_title_observed')
                self.assertFalse(result['pending'])
                self.assertFalse(result['automatic_phase_completion'])
                self.assertEqual(result['verification_reads'], 1 if supervised else 0)
                self.assertEqual(result['source'], 'supervising_agent' if supervised else 'native_visual_guard')
                self.assertEqual(worker.last_observation['page'], 'unknown')
                self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
                self.assertNotIn('startup_guide', worker.preparation_reviews)
                self.assertFalse((bundle.runtime / 'runner-battle-approval.json').exists())
                record = entry.read_json(Path(result['record_file']))
                self.assertEqual(record['input_request_id'], result['input_request_id'])
                receipt = runner.await_existing_receipt(bundle.runtime, control, result['input_request_id'], 0)
                self.assertEqual(receipt['result']['completed'], [dict(type='click', args=[1687.0, 65.0]),
                                                                 dict(type='wait', args=[.7])])
                EVIDENCE.append(dict(contract='declared_current_navigation_consumer',
                    source=result['source'], native_shape_unknown=supervised, native_facts_overwritten=False,
                    input_count=control.action_index, verification_reads=result['verification_reads'],
                    outcome=result['outcome'], actual_page=worker.last_observation['page'],
                    automatic_phase_completion=False, real_navigation=False))

    def test_manual_proof_domains_deadline_and_existing_pending_input_refuse_without_publication(self):
        with worker_fixture(manual=True) as bundle:
            before = len(bundle.control.published)
            mutations = dict(source='native_reader', request_id='different-q', snapshot_id='0'*64,
                capture_request_id='different-capture', frame_id='different-frame', page='shop',
                match_id='different-match', stage='1-2', resume_epoch='different-epoch', checkpoint_id='0'*32,
                deadline_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
                target_unique=False, target_exposed=False, expected_successor='任意页面',
                bounds=[10, 20, 30, 40], control_id='arbitrary-target')
            for key, value in mutations.items():
                with self.subTest(field=key):
                    reply = copy.deepcopy(bundle.reply)
                    reply['actions'][0]['target_evidence'][key] = value
                    try:
                        refused, unused = manual_job(bundle, reply=reply)
                    except ValueError:
                        pass
                    else:
                        self.assertEqual(refused['status'], 'refused', refused)
                    self.assertEqual(bundle.control.action_index, 0)
                    self.assertEqual(len(bundle.control.published), before)
            old = bundle.request['deadline_at']
            bundle.request['deadline_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            reply = copy.deepcopy(bundle.reply)
            reply['actions'][0]['target_evidence']['deadline_at'] = bundle.request['deadline_at']
            state = entry.read_json(bundle.runtime / 'runner-state.json')
            state['decision_request'] = copy.deepcopy(bundle.request)
            bundle.control.write_json(bundle.runtime / 'runner-state.json', state)
            with self.assertRaises(ValueError):
                manual_job(bundle, reply=reply)
            self.assertEqual(len(bundle.control.published), before)
            bundle.request['deadline_at'] = old
        with worker_fixture(manual=True) as bundle:
            # Mutate only an inert existing observe receipt into an explicit
            # unknown-input fixture; no actual unresolved game action exists.
            old = bundle.control.published[0]['id']
            path = bundle.runtime / 'request-ledger' / (hashlib.sha256(old.encode()).hexdigest()+'.json')
            receipt = entry.read_json(path)
            receipt['result'].update(ok=False, input_attempted=True,
                attempted_actions=[dict(type='click', args=[10, 10])], completed=[])
            bundle.control.write_json(path, receipt)
            raw, before = path.read_bytes(), len(bundle.control.published)
            refused, unused = manual_job(bundle)
            self.assertEqual(refused['status'], 'refused', refused)
            self.assertEqual(bundle.control.action_index, 0)
            self.assertEqual(len(bundle.control.published), before)
            self.assertEqual(path.read_bytes(), raw)

    def test_unknown_successor_exhausts_two_readonly_checks_and_same_intent_never_replays(self):
        with worker_fixture(manual=True, successor='unknown') as bundle:
            returned, kwargs = manual_job(bundle)
            self.assertEqual(returned['status'], 'returned', returned)
            self.assertTrue(returned['pending'], returned)
            result = returned['result']['startup_navigation']
            self.assertEqual((result['status'], result['outcome'], result['verification_reads']), ('pending', 'unknown', 2))
            self.assertEqual(bundle.control.action_index, 1)
            self.assertEqual(bundle.control.post_reads, 2)
            self.assertEqual(bundle.worker.preparation_checklist(bundle.worker.last_observation)['phase'], 'startup_guide')
            raw = Path(result['record_file']).read_bytes()
            publications = len(bundle.control.published)
            repeated = steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
            self.assertTrue(repeated['pending'])
            self.assertFalse(steps.process(bundle.worker))
            # A newly chosen mailbox ID is still the same source action.
            new_kwargs = dict(kwargs, step_id=uuid.uuid4().hex)
            with self.assertRaises(ValueError):
                steps.submit(bundle.runtime, bundle.owner, bundle.control, **new_kwargs)
            self.assertEqual(len(bundle.control.published), publications)
            self.assertEqual(Path(result['record_file']).read_bytes(), raw)
            EVIDENCE.append(dict(contract='declared_unknown_successor', input_count=1,
                receipt_completed=True, outcome='unknown', verification_reads=2,
                same_intent_replayed=False, automatic_phase_completion=False, real_game_effect=False))
            # An independently captured identical current PNG is a fresh
            # source, not a requirement that animation alter the hash. ROOT
            # explicitly submits a new current intent through the same
            # original mailbox/checkpoint; the old record stays unknown.
            bundle.control.current_frame = 'original'
            inspect = dict(manual_id='manual-one', step_id=uuid.uuid4().hex, operation='inspect',
                           checkpoint_id=None, reply=None, wait_seconds=0)
            steps.submit(bundle.runtime, bundle.owner, bundle.control, **inspect)
            self.assertTrue(steps.process(bundle.worker))
            fresh = bundle.worker.state['decision_request']
            self.assertEqual(fresh['snapshot_id'], bundle.request['snapshot_id'])
            self.assertNotEqual(fresh['observation']['capture_request_id'], bundle.request['observation']['capture_request_id'])
            self.assertNotEqual(fresh['observation']['frame_id'], bundle.request['observation']['frame_id'])
            reply = {key: fresh[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
            reply['actions'] = [action_for(fresh, checkpoint_id=bundle.checkpoint['checkpoint_id'])]
            bundle.control.successor_mode = 'title'
            next_result, unused = manual_job(bundle, reply=reply)
            self.assertEqual(next_result['status'], 'returned', next_result)
            self.assertEqual(next_result['result']['startup_navigation']['outcome'], 'expected_title_observed')
            self.assertEqual(bundle.control.action_index, 2)
            self.assertEqual(Path(result['record_file']).read_bytes(), raw)
            next_record = entry.read_json(Path(next_result['result']['startup_navigation']['record_file']))
            self.assertEqual(next_record['prior_unknown_navigation_request_id'], bundle.request['request_id'])
            EVIDENCE.append(dict(contract='declared_new_current_manual_intent_after_known_delivery',
                identical_source_png_sha256=True, new_capture_and_frame_ids=True,
                previous_record_unchanged=True, previous_outcome='unknown',
                original_input_count=1, new_explicit_input_count=1, automatic_retry=False,
                actual_new_outcome='expected_title_observed', real_game_effect=False))
        for failure in ('unknown_delivery', 'epoch_after_input'):
            with self.subTest(failure=failure), worker_fixture(manual=True, successor='title') as bundle:
                def unknown_delivery(request, result):
                    if any(a['type'] == 'click' for a in request.get('actions', [])):
                        if failure == 'unknown_delivery':
                            result.update(ok=False, completed=[], input_attempted=True)
                        else:
                            bundle.control.write_json(bundle.runtime / 'runner-resume-epoch.json', dict(id='post-input-epoch'))
                bundle.control.result_mutator = unknown_delivery
                refused, kwargs = manual_job(bundle)
                self.assertEqual(refused['status'], 'refused', refused)
                self.assertTrue(refused['pending'])
                self.assertEqual(bundle.control.action_index, 1)
                self.assertEqual(bundle.control.post_reads, 0)
                original = copy.deepcopy(bundle.control.published)
                steps.submit(bundle.runtime, bundle.owner, bundle.control, **kwargs)
                self.assertFalse(steps.process(bundle.worker))
                self.assertEqual(bundle.control.published, original)

    def test_current_stage_source_foreground_epoch_and_publish_time_change_refuse(self):
        for case in ('epoch', 'deadline', 'match', 'foreground', 'source_receipt', 'publication_epoch',
                     'undelegated_supervisor'):
            with self.subTest(case=case), worker_fixture() as bundle:
                worker, control = bundle.worker, bundle.control
                if case == 'epoch':
                    control.write_json(bundle.runtime / 'runner-resume-epoch.json', dict(id='new-epoch'))
                elif case == 'deadline':
                    bundle.request['deadline_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
                elif case == 'match':
                    worker.active_match_id = 'different-match'
                elif case == 'foreground':
                    control.foreground = False
                elif case == 'source_receipt':
                    rid = bundle.request['observation']['capture_request_id']
                    path = bundle.runtime / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest()+'.json')
                    value = entry.read_json(path)
                    value['request']['chat_id'] = 'foreign-owner'
                    control.write_json(path, value)
                elif case == 'undelegated_supervisor':
                    bundle.reply['actions'] = [action_for(bundle.request, checkpoint_id='a'*32)]
                elif case == 'publication_epoch':
                    original_command = worker.command
                    def changed_epoch(*args, **kwargs):
                        control.write_json(bundle.runtime / 'runner-resume-epoch.json', dict(id='new-epoch'))
                        return original_command(*args, **kwargs)
                    worker.command = changed_epoch
                with self.assertRaises((ValueError, RuntimeError)):
                    worker.execute_plan(bundle.reply)
                self.assertEqual(control.action_index, 0)


if __name__ == '__main__':
    unittest.main()
