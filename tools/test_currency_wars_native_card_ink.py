"""Native-derived ROI calibration and a separately labelled mixed protocol.

Only the six supplied confirmation/caption/book ROIs contain retained native
pixels. Full protocol canvases are generated and receive new PNG identities;
their declared rows are fixture premises, never recovered historical OCR or
permission to replay ROOT's original request. No old test class is selected.
"""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
import zlib

import cv2
import numpy as np
from PIL import Image

import currency_wars_perception as perception
import currency_wars_runner as runner
import currency_wars_visual_guards as guards
from test_currency_wars_card_confirmation import render_cards
import test_local_runtime_compatibility as compatibility


CONFIRM_GLYPH = [1052, 966, 1112, 1002]


def decode_pair(item):
    shape = item['array_shape']
    x, y, right, bottom = item['bounds']
    if shape != [bottom-y, right-x, 3]:
        raise ValueError('Native RGB shape does not match its declared ROI')
    arrays = []
    for label in ('original', 'actual'):
        source = item[label]
        payload = zlib.decompress(base64.b64decode(source['pixels'], validate=True))
        if (len(payload) != int(np.prod(shape))
                or hashlib.sha256(payload).hexdigest() != source['roi_rgb_sha256']):
            raise ValueError('Native ROI bytes do not match the evidence digest')
        arrays.append(np.frombuffer(payload, np.uint8).reshape(shape).copy())
    return arrays


def roi_images(item):
    """Coordinate containers only; they are not reconstructed native frames."""
    x, y, right, bottom = item['bounds']
    images = [np.zeros((1080, 1920, 3), np.uint8) for unused in range(2)]
    for image, crop in zip(images, decode_pair(item)):
        image[y:bottom, x:right] = crop
    return images


def damage_region(image, bounds, damage):
    """Explicit mutations of supplied pixels, not additional native examples."""
    image = image.copy()
    x, y, right, bottom = bounds
    crop = image[y:bottom, x:right]
    # This mutation selects bright native strokes only to make a negative
    # example; it is not the production calibration/extraction threshold.
    support = crop.max(axis=2) >= .75 * int(crop.max())
    if damage == 'white':
        crop[support] = 255
    elif damage == 'blank':
        crop[:] = crop[0, 0].copy()
    elif damage == 'colored':
        crop[support] = [255, 0, 200]
    elif damage == 'dim_overlay':
        crop[:] = np.rint(crop.astype(np.float32) * .5).astype(np.uint8)
    elif damage == 'occlusion':
        middle = crop.shape[1] // 2
        crop[:, middle-5:middle+6] = crop[0, 0].copy()
    elif damage == 'shift':
        crop[:] = np.roll(crop.copy(), 6, axis=1)
    else:
        raise ValueError('Unknown native-derived negative mutation')
    return image


class NativeCardInkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(os.environ['CW_NATIVE_CARD_EVIDENCE_DIR'])
        cls.confirm = json.loads((directory / 'ROOT_ENVIRONMENT_CONFIRM_RGB.json').read_text(encoding='utf-8-sig'))
        captions = json.loads((directory / 'ROOT_ENVIRONMENT_CAPTIONS_RGB.json').read_text(encoding='utf-8-sig'))
        cls.rois = {item['label']: item for item in captions['rois']}
        if set(cls.rois) != {'角色', '装备', 'yellow_book_1', 'yellow_book_2', 'yellow_book_3'}:
            raise ValueError('The explicit native evidence selection is incomplete')
        for item in [cls.confirm, *cls.rois.values()]:
            decode_pair(item)
            for role in ('original', 'actual'):
                if item[role]['full_png_sha256'] != cls.confirm[role]['full_png_sha256']:
                    raise ValueError('Native ROIs do not share the declared original pair')

    def test_native_gray_confirmation_keeps_glyph_while_background_changes(self):
        images = roi_images(self.confirm)
        x, y, right, bottom = CONFIRM_GLYPH
        self.assertFalse(np.array_equal(images[0][y:bottom, x:right], images[1][y:bottom, x:right]))
        for image in images:
            hsv = cv2.cvtColor(image[y:bottom, x:right], cv2.COLOR_RGB2HSV)
            self.assertEqual(np.count_nonzero((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 190)), 0)
        before = [hashlib.sha256(image.tobytes()).hexdigest() for image in images]
        self.assertTrue(guards._environment_gray_confirmation_equal(images, CONFIRM_GLYPH))
        self.assertEqual(before, [hashlib.sha256(image.tobytes()).hexdigest() for image in images])

    def test_native_two_captions_and_three_books_keep_real_appearance(self):
        for label, item in self.rois.items():
            with self.subTest(label=label):
                images = roi_images(item)
                self.assertTrue(np.array_equal(*decode_pair(item)))
                self.assertFalse(guards._text_equal(images, item['bounds']))
                check = (guards._environment_book_equal if label.startswith('yellow_book')
                         else guards._environment_caption_equal)
                self.assertTrue(check(images, item['bounds']))
                # Identical absence is not evidence for either a word or book.
                self.assertFalse(check([np.zeros_like(image) for image in images], item['bounds']))

    def test_native_gray_mutations_do_not_preserve_old_state_or_exposure(self):
        selections = [('确认', self.confirm, CONFIRM_GLYPH, guards._environment_gray_confirmation_equal)]
        selections.extend((label, self.rois[label], self.rois[label]['bounds'], guards._environment_caption_equal)
                          for label in ('角色', '装备'))
        for label, item, bounds, check in selections:
            images = roi_images(item)
            for damage in ('white', 'blank', 'colored', 'dim_overlay', 'occlusion', 'shift'):
                with self.subTest(label=label, damage=damage):
                    self.assertFalse(check([images[0], damage_region(images[1], bounds, damage)], bounds))

    @contextlib.contextmanager
    def mixed_worker(self, change=None):
        """Real Worker/Entry, inert transport, generated surrounding pixels."""
        original_rows = copy.deepcopy(self.confirm['original_observation_rows'])
        current_rows = copy.deepcopy(self.confirm['actual_observation_rows'])
        if change in ('title', 'effect'):
            old_text = (self.confirm['original_options'][0]['title'] if change == 'title'
                        else self.confirm['original_options'][0]['effect_lines'][0])
            next(row for row in current_rows if row['text'] == old_text)['text'] = '当前文本已改变'
        elif change == 'missing_equipment':
            current_rows = [row for row in current_rows if row['text'] != '装备']
        elif change == 'overlay':
            current_rows.append({'text': '新覆盖层', 'box': [310, 680, 410, 712], 'confidence': .99})
        originals = [render_cards(original_rows)[0],
                     render_cards(current_rows, damage='border' if change == 'border' else 'background')[0]]
        images = [np.array(image) for image in originals]
        for item in [self.confirm, *self.rois.values()]:
            x, y, right, bottom = item['bounds']
            for image, crop in zip(images, decode_pair(item)):
                image[y:bottom, x:right] = crop
        if change == 'confirmation_white':
            images[1] = damage_region(images[1], CONFIRM_GLYPH, 'white')
        elif change == 'caption_blank':
            images[1] = damage_region(images[1], self.rois['角色']['bounds'], 'blank')
        elif change == 'book':
            x, y, right, bottom = self.rois['yellow_book_1']['bounds']
            images[1][y:bottom, x:right] = [18, 18, 18]
        pil_images, payloads = [], []
        for image in images:
            native_derived_canvas = Image.fromarray(image, 'RGB')
            buffer = io.BytesIO()
            native_derived_canvas.save(buffer, format='PNG')
            pil_images.append(native_derived_canvas)
            payloads.append(buffer.getvalue())
        png_hashes = [hashlib.sha256(payload).hexdigest() for payload in payloads]
        for digest in png_hashes:
            self.assertNotIn(digest, [self.confirm[role]['full_png_sha256'] for role in ('original', 'actual')])

        def declaration(image, rows, page):
            options = perception.option_facts(rows, 'environment')
            if change == 'omitted_effect':
                options[0]['effect_lines'].pop(0)
            return {'page': page, 'rows': rows, 'fields': {},
                    'semantic': {'options': options}, 'fingerprint': perception.fingerprint(image),
                    'read_contract': {'version': runner.READ_CONTRACT_VERSION,
                                      'effective_scope': 'full', 'unread': []}}

        fixture = compatibility.RuntimeCompatibilityTests()
        with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            (runtime / 'runner-manual.json').unlink()
            control.frame = payloads[0]
            control.validate_actions = lambda actions: [
                {'type': action['type'], 'args': [float(value) for value in action.get('args', [])]}
                for action in actions]
            reader.frames[png_hashes[0]] = declaration(pil_images[0], original_rows, 'environment')
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
            worker.ask(worker.last_observation, 'environment_strategy', '混合协议：只选择当前已核的一张环境卡')
            request = worker.state['decision_request']
            reader.frames[png_hashes[1]] = declaration(pil_images[1], current_rows,
                'investment' if change == 'page' else 'environment')
            control.frame = payloads[1]
            selected = copy.deepcopy(request['observation']['semantic']['options'][0])
            action = {'type': 'click_text', 'text': selected['title'], 'exact': True,
                      'expected_page': 'environment', 'reason': '混合协议：执行已选环境一次，不确认结果',
                      'target_evidence': {**selected, 'text': selected['title'],
                                          'snapshot_id': request['snapshot_id']}}
            reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
            reply['actions'] = [action]
            yield worker, control, reply, request

    def inputs(self, control):
        return [item for item in control.published
                if any(action['type'] not in ('observe', 'wait') for action in item.get('actions', []))]

    def refuse(self, worker, control, reply):
        with self.assertRaises((ValueError, RuntimeError)):
            worker.execute_plan(reply)
        self.assertEqual(self.inputs(control), [])
        self.assertEqual(worker.state['statistics']['local_inputs'], 0)

    def test_mixed_protocol_publishes_once_without_claiming_selection_outcome(self):
        with self.mixed_worker() as (worker, control, reply, request):
            self.assertEqual(request['observation']['semantic']['options'], self.confirm['original_options'])
            source = Path(request['original_png']).read_bytes()
            worker.execute_plan(reply)
            self.assertEqual(len(self.inputs(control)), 1)
            self.assertEqual(self.inputs(control)[0]['actions'], [
                {'type': 'click', 'args': [441.0, 393.0]}, {'type': 'wait', 'args': [.7]}])
            self.assertEqual(worker.state['statistics']['local_inputs'], 1)
            self.assertIsNone(worker.state['decision_request'])
            self.assertEqual(source, Path(request['original_png']).read_bytes())
            outcomes = [event for event in worker.log_events if event['event'] == 'actual_result']
            self.assertEqual(len(outcomes), 1)
            self.assertIs(outcomes[0]['outcome_confirmed'], False)

    def test_mixed_protocol_changed_semantics_appearance_and_layout_are_refused(self):
        for change in ('title', 'effect', 'book', 'border', 'page', 'confirmation_white',
                       'caption_blank', 'overlay', 'missing_equipment', 'omitted_effect'):
            with self.subTest(change=change), self.mixed_worker(change) as data:
                worker, control, reply, request = data
                self.refuse(worker, control, reply)

    def test_mixed_protocol_keeps_source_epoch_deadline_and_publication_fences(self):
        for change in ('epoch', 'deadline', 'source_sha', 'select_and_confirm', 'pending', 'request'):
            with self.subTest(change=change), self.mixed_worker() as data:
                worker, control, reply, request = data
                injected = []
                if change == 'epoch':
                    control.write_json(worker.run / 'runner-resume-epoch.json', {'id': 'new-epoch'})
                elif change == 'deadline':
                    request['deadline_at'] = '2000-01-01T00:00:00+00:00'
                elif change == 'source_sha':
                    path = Path(request['original_png'])
                    path.write_bytes(path.read_bytes() + b'changed-source-bytes')
                elif change == 'select_and_confirm':
                    reply['actions'].append({'type': 'click_text', 'text': '确认', 'exact': True,
                                            'expected_page': 'environment', 'reason': '拒绝沿旧帧选卡并确认'})
                else:
                    original_write = control.write_json

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
                if change in ('pending', 'request'):
                    self.assertEqual(injected, [change])
                if change == 'pending':
                    self.assertEqual(runner.optional(worker.run / 'reward-step.json')['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
