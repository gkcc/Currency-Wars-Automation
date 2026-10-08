"""Six offline environment-card protocol checks, using generated pixels only.

Rows are declared reader premises, not native OCR measurements or retained
game frames. The white confirmation is a positive guard fixture; it does not
claim that the unresolved native gray/disabled confirmation is supported.
Real Worker and Entry consume immutable PNG receipts through an inert publisher
with no device, broker process, or game input API.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw

import currency_wars_perception as perception
import currency_wars_runner as runner
import test_local_runtime_compatibility as compatibility


def card_rows():
    labels = [('投资环境', [890, 80, 1030, 112]),
              ('确认', [1054, 968, 1110, 1000])]
    titles = ('夜之半神概念股', '第二环境', '第三环境')
    for index, box in enumerate(perception.OPTION_LAYOUTS['environment']):
        x, unused_y, right, unused_bottom = box
        center = (x + right) // 2
        labels.extend([
            (titles[index], [x + 85, 370, right - 70, 402]),
            ('正文甲' + str(index + 1), [x + 70, 434, right - 60, 466]),
            ('正文乙' + str(index + 1), [x + 70, 478, right - 60, 510]),
            ('正文丙' + str(index + 1), [x + 70, 522, right - 60, 554]),
            ('角色', [center - 20, 598, center + 22, 626]),
            ('装备', [center - 17, 744, center + 20, 766]),
        ])
    return [{'text': label, 'box': box, 'confidence': .99} for label, box in labels]


def render_cards(rows, *, gray_confirmation=False, damage=None):
    """Non-CJK surrogate strokes make localized pixel damage testable."""
    image = Image.new('RGB', (1920, 1080), (50, 50, 50))
    draw = ImageDraw.Draw(image)
    for bounds in perception.OPTION_LAYOUTS['environment']:
        x, y, right, bottom = bounds
        draw.rectangle(bounds, fill=(80, 80, 80), outline=(240, 240, 240), width=3)
        # A small, nonconstant white pattern in the existing book-badge ROI.
        draw.rectangle([right - 42, y + 24, right - 25, y + 37], outline=(240, 240, 240), width=2)
        draw.line([right - 34, y + 24, right - 34, y + 37], fill=(240, 240, 240), width=2)
    for row in rows:
        x, y, right, bottom = row['box']
        color = (120, 120, 120) if gray_confirmation and row['text'] == '确认' else (240, 240, 240)
        for index, character in enumerate(row['text']):
            left = x + 3 + index * 16
            if left + 10 >= right:
                break
            draw.rectangle([left, y + 4, left + 2, bottom - 4], fill=color)
            draw.rectangle([left, y + 4, left + 10, y + 6], fill=color)
            if ord(character) % 2:
                middle = (y + bottom) // 2
                draw.rectangle([left, middle, left + 9, middle + 2], fill=color)
            else:
                draw.rectangle([left + 8, y + 4, left + 10, bottom - 4], fill=color)
    x, y, right, bottom = perception.OPTION_LAYOUTS['environment'][0]
    if damage == 'book':
        draw.rectangle([right - 45, y + 21, right - 21, y + 41], fill=(80, 80, 80))
    elif damage == 'border':
        draw.rectangle([x, y, right, bottom], outline=(240, 240, 240), width=10)
    elif damage == 'background':
        draw.rectangle([x + 100, y + 75, x + 160, y + 85], fill=(100, 100, 100))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return image, output.getvalue()


class CardConfirmationTests(unittest.TestCase):
    @contextlib.contextmanager
    def worker(self, *, change=None, gray_confirmation=False):
        original_rows = card_rows()
        current_rows = copy.deepcopy(original_rows)
        # First card: header/confirm, title, three body lines, role, equipment.
        if change in ('missing_role', 'missing_equipment'):
            current_rows.pop(6 if change == 'missing_role' else 7)
        elif change == 'shifted_structure':
            current_rows[7]['box'] = [value + 90 if index % 2 == 0 else value
                                      for index, value in enumerate(current_rows[7]['box'])]
        elif change == 'duplicate_structure':
            current_rows.append(copy.deepcopy(current_rows[7]))
        elif change == 'low_structure':
            current_rows[7]['confidence'] = .89
        elif change == 'overlay':
            current_rows.append({'text': '未知遮挡', 'box': [310, 680, 410, 712], 'confidence': .99})
        elif change == 'title':
            current_rows[2]['text'] = '不同环境'
        elif change == 'effect':
            current_rows[3]['text'] = '正文已改变'
        original, original_png = render_cards(original_rows, gray_confirmation=gray_confirmation)
        current, current_png = render_cards(current_rows, gray_confirmation=gray_confirmation, damage=change)
        original_sha = hashlib.sha256(original_png).hexdigest()
        current_sha = hashlib.sha256(current_png).hexdigest()

        def declaration(image, rows, page):
            options = perception.option_facts(rows, 'environment')
            if change == 'omitted_effect':
                # Both semantic declarations omit a real visible body line;
                # equality alone must not replace complete-card coverage.
                options[0]['effect_lines'].pop(0)
            return {'page': page, 'rows': rows, 'fields': {},
                    'semantic': {'options': options},
                    'fingerprint': perception.fingerprint(image),
                    'read_contract': {'version': runner.READ_CONTRACT_VERSION,
                                      'effective_scope': 'full', 'unread': []}}

        fixture = compatibility.RuntimeCompatibilityTests()
        with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            (runtime / 'runner-manual.json').unlink()
            control.frame = original_png
            control.validate_actions = lambda actions: [
                {'type': action['type'], 'args': [float(value) for value in action.get('args', [])]}
                for action in actions]
            reader.frames[original_sha] = declaration(original, original_rows, 'environment')
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
            worker.ask(worker.last_observation, 'environment_strategy', '离线协议：只选择当前已核的一张环境卡')
            request = worker.state['decision_request']
            reader.frames[current_sha] = declaration(current, current_rows,
                'investment' if change == 'page' else 'environment')
            control.frame = current_png
            selected = copy.deepcopy(request['observation']['semantic']['options'][0])
            action = {'type': 'click_text', 'text': selected['title'], 'exact': True,
                      'expected_page': 'environment', 'reason': '离线协议：执行主管选定的一次环境选择',
                      'target_evidence': {**selected, 'text': selected['title'],
                                          'snapshot_id': request['snapshot_id']}}
            reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
            reply['actions'] = [action]
            yield worker, control, reply, request, original, current

    def inputs(self, control):
        return [item for item in control.published
                if any(action['type'] not in ('observe', 'wait') for action in item.get('actions', []))]

    def refuse(self, worker, control, reply):
        with self.assertRaises((ValueError, RuntimeError)):
            worker.execute_plan(reply)
        self.assertEqual(self.inputs(control), [])
        self.assertEqual(worker.state['statistics']['local_inputs'], 0)

    def test_white_confirmation_in_shared_roi_and_accounted_structure_publish_one_selection(self):
        with self.worker(change='background') as (worker, control, reply, request, original, current):
            self.assertEqual(request['observation']['rows'][1]['box'], [1054, 968, 1110, 1000])
            options = copy.deepcopy(request['observation']['semantic']['options'])
            self.assertEqual(len(options), 3)
            for option in options:
                self.assertEqual(len(option['effect_lines']), 4)
                self.assertIn('角色', option['effect_lines'])
                self.assertNotIn('装备', option['effect_lines'])
            source_bytes = Path(request['original_png']).read_bytes()
            bounds = options[0]['bounds']
            self.assertNotEqual(original.crop(bounds).tobytes(), current.crop(bounds).tobytes())
            worker.execute_plan(reply)
            published = self.inputs(control)
            self.assertEqual(len(published), 1)
            box = request['observation']['rows'][2]['box']
            self.assertEqual(published[0]['actions'], [
                {'type': 'click', 'args': [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]},
                {'type': 'wait', 'args': [.7]}])
            self.assertEqual(worker.state['statistics']['local_inputs'], 1)
            self.assertIsNone(worker.state['decision_request'])
            self.assertEqual(Path(request['original_png']).read_bytes(), source_bytes)
            self.assertEqual(options, request['observation']['semantic']['options'])
            outcomes = [event for event in worker.log_events if event['event'] == 'actual_result']
            self.assertEqual(len(outcomes), 1)
            self.assertIs(outcomes[0]['outcome_confirmed'], False)

    def test_gray_confirmation_with_zero_white_mask_rejects_even_identical_cards(self):
        with self.worker(gray_confirmation=True) as (worker, control, reply, request, original, current):
            self.assertTrue(np.array_equal(np.array(original), np.array(current)))
            for image in (original, current):
                hsv = cv2.cvtColor(np.array(image)[966:1002, 1052:1112], cv2.COLOR_RGB2HSV)
                white = (hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 190)
                self.assertEqual(np.count_nonzero(white), 0)
            # Exercise the actual local diagnostic without claiming these
            # generated PNGs or rows are retained game evidence.
            from replay_currency_wars_card_confirmation import replay_pair
            directory = worker.records / 'confirmation-diagnostic-fixture'
            directory.mkdir()
            independent = copy.deepcopy(request['observation'])
            independent.pop('capture_request_id', None)
            independent.pop('frame_id', None)
            paths = {name: directory / (name + '.json') for name in ('request', 'reply', 'independent', 'spec')}
            for name, value in (('request', request), ('reply', reply), ('independent', independent)):
                paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
            spec = {'schema': 1, 'request_json': str(paths['request']), 'reply_json': str(paths['reply']),
                    'original_png': request['original_png'], 'current_png': request['original_png'],
                    'independent_observation_json': str(paths['independent']),
                    'manual_annotation': {'confirm_disabled': True}}
            paths['spec'].write_text(json.dumps(spec), encoding='utf8')
            before = paths['independent'].read_bytes()
            report = replay_pair(paths['spec'], directory / 'crops')
            self.assertFalse(report['historical_qualification_available'])
            self.assertFalse(report['historical_byte_bound_visual_eligibility'])
            self.assertFalse(report['independent_visual_eligibility'])
            self.assertEqual(report['native_disabled_state'], 'unknown')
            self.assertFalse(report['manual_annotation']['is_native_state'])
            self.assertEqual(paths['independent'].read_bytes(), before)
            self.assertEqual(len(report['local_calibration']['artifacts']), 4)
            for artifact in report['local_calibration']['artifacts']:
                payload = (directory / 'crops' / artifact['artifact_name']).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), artifact['crop_png_sha256'])
                self.assertEqual(artifact['source_png_sha256'], request['snapshot_id'])
            # Byte mismatch refuses before creating a calibration directory.
            independent['snapshot_id'] = 'f' * 64
            paths['independent'].write_text(json.dumps(independent), encoding='utf8')
            with self.assertRaises(ValueError):
                replay_pair(paths['spec'], directory / 'must-not-exist')
            self.assertFalse((directory / 'must-not-exist').exists())
            self.refuse(worker, control, reply)

    def test_reported_structure_missing_shifted_duplicate_unknown_or_incomplete_is_rejected(self):
        for change in ('missing_role', 'missing_equipment', 'shifted_structure',
                       'duplicate_structure', 'low_structure', 'overlay', 'omitted_effect'):
            with self.subTest(change=change), self.worker(change=change) as data:
                worker, control, reply, request, original, current = data
                self.refuse(worker, control, reply)
                self.assertIs(worker.state['decision_request'], request)

    def test_changed_title_effect_book_border_or_page_is_rejected(self):
        for change in ('title', 'effect', 'book', 'border', 'page'):
            with self.subTest(change=change), self.worker(change=change) as data:
                worker, control, reply, request, original, current = data
                self.refuse(worker, control, reply)

    def test_epoch_deadline_multiple_actions_and_changed_source_reject_before_input(self):
        for change in ('epoch', 'deadline', 'select_and_confirm', 'source_sha'):
            with self.subTest(change=change), self.worker() as data:
                worker, control, reply, request, original, current = data
                if change == 'epoch':
                    control.write_json(worker.run / 'runner-resume-epoch.json', {'id': 'new-epoch'})
                elif change == 'deadline':
                    request['deadline_at'] = '2000-01-01T00:00:00+00:00'
                elif change == 'select_and_confirm':
                    reply['actions'].append({'type': 'click_text', 'text': '确认', 'exact': True,
                                            'expected_page': 'environment', 'reason': '错误协议：沿用旧帧确认'})
                else:
                    source = Path(request['original_png'])
                    source.write_bytes(source.read_bytes() + b'changed-source-bytes')
                self.refuse(worker, control, reply)

    def test_publication_fence_rejects_late_pending_and_replaced_request(self):
        for change in ('pending', 'request'):
            with self.subTest(change=change), self.worker() as data:
                worker, control, reply, request, original, current = data
                original_write = control.write_json
                injected = []

                def write(path, value):
                    original_write(path, value)
                    intent = value.get('request', {})
                    if (Path(path).parent.name == 'request-ledger'
                            and any(action['type'] == 'click' for action in intent.get('actions', []))):
                        self.assertFalse(injected)
                        injected.append(change)
                        if change == 'pending':
                            original_write(worker.run / 'reward-step.json', {
                                'step_id': 'unresolved-original-step', 'request_id': 'unresolved-original-input',
                                'status': 'pending', 'match_id': 'match', 'before': {'stage': '2-3'},
                                'publication_attempted': True})
                        else:
                            worker.state['decision_request'] = {**request, 'request_id': 'superseding-request'}

                control.write_json = write
                self.refuse(worker, control, reply)
                self.assertEqual(injected, [change])
                refused = [event for event in worker.log_events if event['event'] == 'actual_result']
                self.assertEqual(len(refused), 1)
                self.assertIs(refused[0]['request_published'], False)
                for path in (worker.run / 'request-ledger').glob('*.json'):
                    self.assertFalse(any(action['type'] == 'click'
                        for action in runner.optional(path).get('request', {}).get('actions', [])))
                if change == 'pending':
                    self.assertEqual(runner.optional(worker.run / 'reward-step.json')['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
