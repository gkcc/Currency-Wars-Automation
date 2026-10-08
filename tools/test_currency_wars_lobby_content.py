"""Focused lobby protocol checks, not native screenshot/OCR or game success.

The five OCR declarations come from ROOT's published retained diagnostics.
All pixels and transport receipts below are synthetic. Only the production
Worker/Entry consumers are real; the reused fixture has no device/process API.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageChops, ImageDraw

import currency_wars_perception as perception
import currency_wars_runner as runner
import test_local_runtime_compatibility as compatibility


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'handoff/2026-10-08/ROOT_PR22_NATIVE_LOBBY_GUARD_FAILURE.json'
ROI = (1360, 930, 1810, 1020)
CONTENT = (1360, 930, 1810, 1019)
LABEL = '开始「货币战争」'


def retained_rows(which):
    record = json.loads(EVIDENCE.read_text(encoding='utf8'))
    return [copy.deepcopy(row) for anchor in record['anchors'].values() for row in anchor[which]]


def render(phase=0, *, border=False, damage=None):
    """Surrogate strokes and deterministic bands; never claimed native pixels."""
    bands = ((np.arange(1920) // 32 + phase) % 2 * 65 + 100).astype(np.uint8)
    image = Image.fromarray(np.broadcast_to(bands[None, :, None], (1080, 1920, 3)).copy())
    draw = ImageDraw.Draw(image)
    draw.rectangle((1360, 930, 1809, 1019), fill=(120, 120, 120))
    for x in range(1473, 1685, 19):
        draw.rectangle((x, 962, x+2, 982), fill=(240, 240, 240))
        draw.rectangle((x, 962, x+12, 964), fill=(240, 240, 240))
    if border:
        # Exact shape of the public diagnostic, not copied native PNG pixels.
        draw.line((1412, 1019, 1744, 1019), fill=(121, 120, 120))
    if damage == 'adjacent_inside_row':
        image.putpixel((1412, 1018), (121, 120, 120))
    elif damage == 'target_shift':
        image.paste(image.crop((1468, 956, 1706, 990)), (1469, 956))
    elif damage == 'center_cover':
        draw.rectangle((1583, 967, 1591, 975), fill=(150, 70, 150))
    elif damage == 'gray':
        draw.rectangle((1468, 956, 1706, 990), fill=(110, 110, 110))
    payload = io.BytesIO()
    image.save(payload, format='PNG')
    return image, payload.getvalue()


def declaration(image, rows, page='lobby'):
    return {'page': page, 'rows': rows, 'fields': {}, 'semantic': {},
            'fingerprint': perception.fingerprint(image),
            'read_contract': {'version': runner.READ_CONTRACT_VERSION, 'effective_scope': 'full', 'unread': []}}


def action():
    return {'type': 'click_text', 'text': LABEL, 'exact': True, 'expected_page': 'lobby',
            'bounds': list(ROI), 'reason': '离线协议：本次主管选定唯一大厅开始导航'}


class LobbyContentTests(unittest.TestCase):
    @contextlib.contextmanager
    def worker(self, *, phase=0, damage=None):
        fixture = compatibility.RuntimeCompatibilityTests()
        old_image, old_png = render()
        current_image, current_png = render(phase, border=True, damage=damage)
        with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            (runtime / 'runner-manual.json').unlink()
            control.frame = old_png
            control.validate_actions = lambda actions: [
                {'type': item['type'], 'args': [float(value) for value in item.get('args', [])]}
                for item in actions]
            old_sha, current_sha = (hashlib.sha256(value).hexdigest() for value in (old_png, current_png))
            reader.frames[old_sha] = declaration(old_image, retained_rows('before'))
            worker = fixture.frame_worker(runtime, records, owner, control, reader)
            worker.context = {name: None for name in
                ('coins', 'team', 'gear', 'xp', 'bonds', 'economy_plan', 'preparation_review')}
            worker.preparation_scope = ('match', '2-3', 'old-epoch')
            worker.preparation_reviews = {}
            worker.live_mode = {'match_id': 'match', 'value': '标准博弈'}
            worker.knowledge = {'roles': {}, 'guide': {}, 'progression': {}}
            worker.history, worker.inspections, worker.inspection_attempted = {}, {}, set()
            worker.state['statistics']['decisions'] = 0
            worker.publish = lambda **changes: worker.state.update(changes)
            worker.log_events = []
            worker.log = worker.log_events.append
            worker.observe()
            worker.ask(worker.last_observation, 'new_match', '离线协议：单次大厅开始请求')
            request = worker.state['decision_request']
            reader.frames[current_sha] = declaration(current_image, retained_rows('after'))
            # The inert transport switches to white after click. Declare only
            # unknown, so a synthetic receipt cannot claim new-match arrival.
            output = io.BytesIO()
            white = Image.new('RGB', (1920, 1080), 'white')
            white.save(output, format='PNG')
            reader.frames[hashlib.sha256(output.getvalue()).hexdigest()] = declaration(white, [], 'unknown')
            control.frame = current_png
            reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
            reply['actions'] = [action()]
            yield worker, control, reply, request, current_sha

    def inputs(self, control):
        return [value for value in control.published if any(
            item['type'] not in ('observe', 'wait') for item in value.get('actions', []))]

    def test_retained_boundary_shape_and_single_fresh_center_publication(self):
        evidence = json.loads(EVIDENCE.read_text(encoding='utf8'))
        old, unused = render()
        changed, unused = render(border=True)
        self.assertEqual(list(ImageChops.difference(old.crop(ROI), changed.crop(ROI)).getbbox()),
                         evidence['pixel_difference_box'])
        self.assertEqual(old.crop(CONTENT).tobytes(), changed.crop(CONTENT).tobytes())
        for phase in (0, 1):
            with self.subTest(phase=phase), self.worker(phase=phase) as data:
                worker, control, reply, request, current_sha = data
                source_bytes = Path(request['original_png']).read_bytes()
                distance = runner.hash_distance(request['observation']['fingerprint'],
                    worker.perception.frames[current_sha]['fingerprint'])
                self.assertEqual(distance > .10, phase == 1)
                worker.execute_plan(reply)
                published = self.inputs(control)
                self.assertEqual(len(published), 1)
                self.assertEqual(published[0]['actions'], [
                    {'type': 'click', 'args': [1587., 971.]}, {'type': 'wait', 'args': [.7]}])
                self.assertEqual(worker.state['statistics']['local_inputs'], 1)
                self.assertEqual(worker.state['statistics']['local_observations'], 2)
                self.assertEqual(Path(request['original_png']).read_bytes(), source_bytes)
                self.assertIsNone(worker.state['decision_request'])
                self.assertEqual(worker.last_observation['page'], 'unknown')
                self.assertEqual(worker.active_match_id, 'match')
                matches = [event for event in worker.log_events if event['event'] == 'local_navigation_guard_matched']
                self.assertEqual([event['control_id'] for event in matches], ['lobby_start'])
                outcomes = [event for event in worker.log_events if event['event'] == 'actual_result']
                self.assertEqual(len(outcomes), 1)
                self.assertIs(outcomes[0]['outcome_confirmed'], False)

    def test_changed_content_rejects_even_below_global_threshold(self):
        for damage in ('adjacent_inside_row', 'target_shift', 'center_cover', 'gray'):
            with self.subTest(damage=damage), self.worker(damage=damage) as data:
                worker, control, reply, request, current_sha = data
                self.assertLessEqual(runner.hash_distance(request['observation']['fingerprint'],
                    worker.perception.frames[current_sha]['fingerprint']), .10)
                with self.assertRaisesRegex(ValueError, '大厅开始按钮'):
                    worker.execute_plan(reply)
                self.assertEqual(self.inputs(control), [])
                self.assertEqual(worker.state['statistics']['local_inputs'], 0)
                self.assertIs(worker.state['decision_request'], request)

    def test_each_native_anchor_and_target_bounds_remain_required(self):
        with self.worker() as (worker, control, reply, request, unused):
            actual = copy.deepcopy(worker.observe())
            self.assertTrue(runner.stable_lobby_entry_navigation(reply, request, actual, worker.frame_path))
            for index in range(5):
                for change in ('missing', 'duplicate', 'confidence', 'displacement'):
                    with self.subTest(anchor=index, change=change):
                        fresh = copy.deepcopy(actual)
                        row = fresh['rows'][index]
                        if change == 'missing':
                            fresh['rows'].pop(index)
                        elif change == 'duplicate':
                            fresh['rows'].append(copy.deepcopy(row))
                        elif change == 'confidence':
                            row['confidence'] = .89
                        else:
                            row['box'][0] += 13
                            row['box'][2] += 13
                        self.assertFalse(runner.stable_lobby_entry_navigation(reply, request, fresh, worker.frame_path))
            # Both old and new text must be fully within stable content, even
            # if a declared box in the old search ROI remains unchanged.
            old, fresh = copy.deepcopy(request), copy.deepcopy(actual)
            for observation in (old['observation'], fresh):
                observation['rows'][-1]['box'] = [1468, 995, 1706, 1020]
            self.assertFalse(runner.stable_lobby_entry_navigation(reply, old, fresh, worker.frame_path))
            fresh = copy.deepcopy(actual)
            fresh['page'] = 'unknown'
            self.assertFalse(runner.stable_lobby_entry_navigation(reply, request, fresh, worker.frame_path))
            self.assertEqual(self.inputs(control), [])

    def test_original_request_scope_and_file_bytes_are_required(self):
        with self.worker() as (worker, control, reply, request, unused):
            actual = copy.deepcopy(worker.observe())
            for case in ('request_snapshot', 'reply_snapshot', 'current_snapshot', 'current_bytes',
                         'original_bytes', 'missing_original', 'wrong_size', 'wrong_format'):
                with self.subTest(case=case), tempfile.TemporaryDirectory(prefix='cw-lobby-source-') as directory:
                    source, plan, fresh = copy.deepcopy(request), copy.deepcopy(reply), copy.deepcopy(actual)
                    current_path = Path(directory) / 'current.png'
                    current_path.write_bytes(worker.frame_path.read_bytes())
                    source_path = Path(directory) / 'original.png'
                    source_path.write_bytes(Path(request['original_png']).read_bytes())
                    source['original_png'] = str(source_path)
                    if case == 'request_snapshot':
                        source['snapshot_id'] = '0' * 64
                    elif case == 'reply_snapshot':
                        plan['snapshot_id'] = '0' * 64
                    elif case == 'current_snapshot':
                        fresh['snapshot_id'] = '0' * 64
                    elif case == 'current_bytes':
                        current_path.write_bytes(source_path.read_bytes())
                    elif case == 'original_bytes':
                        source_path.write_bytes(current_path.read_bytes())
                    elif case == 'missing_original':
                        source_path.unlink()
                    else:
                        Image.new('RGB', (1920, 1079) if case == 'wrong_size' else (1920, 1080)).save(
                            current_path, format='PNG' if case == 'wrong_size' else 'BMP')
                        fresh['snapshot_id'] = hashlib.sha256(current_path.read_bytes()).hexdigest()
                    self.assertFalse(runner.stable_lobby_entry_navigation(plan, source, fresh, current_path))
            self.assertEqual(self.inputs(control), [])

    def test_consumer_rejects_ambiguous_plan_with_small_global_delta(self):
        for case in ('wrong_kind', 'substring', 'multiple', 'bounds', 'not_exact', 'page',
                     'text', 'missing_anchor', 'epoch', 'request_id'):
            with self.subTest(case=case), self.worker() as data:
                worker, control, reply, request, current_sha = data
                if case == 'wrong_kind':
                    request['kind'] = 'unknown_page'
                elif case == 'substring':
                    reply['actions'][0].update(text='开始', exact=False)
                elif case == 'multiple':
                    reply['actions'].append(copy.deepcopy(reply['actions'][0]))
                elif case == 'bounds':
                    reply['actions'][0]['bounds'][3] -= 1
                elif case == 'not_exact':
                    reply['actions'][0]['exact'] = False
                elif case == 'page':
                    worker.perception.frames[current_sha]['page'] = 'unknown'
                elif case == 'text':
                    worker.perception.frames[current_sha]['rows'][-1]['text'] += '新'
                elif case == 'missing_anchor':
                    worker.perception.frames[current_sha]['rows'].pop(0)
                elif case == 'epoch':
                    reply['resume_epoch'] = 'stale-epoch'
                else:
                    reply['request_id'] = 'stale-request'
                with self.assertRaises((ValueError, RuntimeError)):
                    worker.execute_plan(reply)
                self.assertEqual(self.inputs(control), [])
                self.assertIs(worker.state['decision_request'], request)

    def test_late_publication_fences_remain_unpublished(self):
        for change in ('epoch', 'pending', 'foreground', 'request'):
            with self.subTest(change=change), self.worker() as data:
                worker, control, reply, request, unused = data
                original_write, original_status = control.write_json, control.status
                injected = []
                control.foreground = True
                control.status = lambda: {**original_status(), 'game_foreground': control.foreground}

                def write(path, value):
                    original_write(path, value)
                    if (Path(path).parent.name == 'request-ledger'
                            and any(item['type'] == 'click' for item in value.get('request', {}).get('actions', []))):
                        self.assertFalse(injected)
                        self.assertTrue(any(event['event'] == 'local_navigation_guard_matched' for event in worker.log_events))
                        injected.append(change)
                        if change == 'epoch':
                            original_write(worker.run / 'runner-resume-epoch.json', {'id': 'new-epoch'})
                        elif change == 'pending':
                            original_write(worker.run / 'reward-step.json', {
                                'step_id': 'unresolved-original-step', 'request_id': 'unresolved-original-input',
                                'status': 'pending', 'match_id': 'match', 'before': {'stage': '2-3'},
                                'publication_attempted': True})
                        elif change == 'foreground':
                            control.foreground = False
                        else:
                            worker.state['decision_request'] = {**request, 'request_id': 'superseding-request'}

                control.write_json = write
                with self.assertRaises((ValueError, RuntimeError)):
                    worker.execute_plan(reply)
                self.assertEqual(injected, [change])
                self.assertEqual(self.inputs(control), [])
                self.assertEqual(worker.state['statistics']['local_inputs'], 0)
                outcomes = [event for event in worker.log_events if event['event'] == 'actual_result']
                self.assertEqual(len(outcomes), 1)
                self.assertIs(outcomes[0]['request_published'], False)
                if change == 'pending':
                    self.assertEqual(runner.optional(worker.run / 'reward-step.json')['status'], 'pending')

    def test_pair_replay_never_repairs_missing_provenance_or_exposes_private_fields(self):
        from replay_currency_wars_lobby_content import replay_pair
        with self.worker() as (worker, control, reply, request, current_sha):
            actual = copy.deepcopy(worker.observe())
            pair = worker.records / 'protocol-pair'
            pair.mkdir()
            source = copy.deepcopy(request)
            source['run_token'] = 'inert-private-protocol-value'
            spec = {'schema': 1, 'request_json': 'request.json', 'reply_json': 'reply.json',
                    'current_observation_json': 'current.json', 'original_png': 'original.png', 'current_png': 'current.png'}
            for name, value in (('spec.json', spec), ('request.json', source), ('reply.json', reply), ('current.json', actual)):
                (pair / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
            (pair / 'original.png').write_bytes(Path(request['original_png']).read_bytes())
            (pair / 'current.png').write_bytes(worker.frame_path.read_bytes())
            published_before = copy.deepcopy(control.published)
            for case in ('valid', 'missing_identity', 'wrong_reply_epoch', 'changed_png'):
                with self.subTest(case=case):
                    fresh, plan = copy.deepcopy(actual), copy.deepcopy(reply)
                    if case == 'missing_identity':
                        fresh.pop('frame_id')
                    if case == 'wrong_reply_epoch':
                        plan['resume_epoch'] = 'other-epoch'
                    (pair / 'current.json').write_text(json.dumps(fresh), encoding='utf8')
                    (pair / 'reply.json').write_text(json.dumps(plan), encoding='utf8')
                    (pair / 'current.png').write_bytes(Path(request['original_png']).read_bytes()
                                                      if case == 'changed_png' else worker.frame_path.read_bytes())
                    before = {path.name: path.read_bytes() for path in pair.iterdir()}
                    report = replay_pair(pair / 'spec.json')
                    self.assertEqual(report['byte_bound_visual_eligibility'], case == 'valid')
                    if case == 'valid':
                        self.assertEqual(report['png_sha256'], [request['snapshot_id'], current_sha])
                        self.assertIs(report['full_roi_pixels_equal'], False)
                        self.assertIs(report['content_roi_pixels_equal'], True)
                    self.assertEqual(before, {path.name: path.read_bytes() for path in pair.iterdir()})
                    for key in ('native_capture_authentication_replayed', 'current_epoch_pending_foreground_verified',
                                'input_published', 'navigation_outcome_verified'):
                        self.assertIs(report[key], False)
                    encoded = json.dumps(report, ensure_ascii=False)
                    for private in (source['run_token'], str(pair), str(worker.run), str(request['original_png']), LABEL):
                        self.assertNotIn(private, encoded)
                    self.assertEqual(control.published, published_before)


if __name__ == '__main__':
    unittest.main()
