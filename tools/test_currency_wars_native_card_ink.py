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


class InvestmentNativeGuardTests(unittest.TestCase):
    """Original immutable investment pair, supplied separately from public source."""
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get('CW_NATIVE_INVESTMENT_EVIDENCE_DIR')
        if not directory:
            raise unittest.SkipTest('Retained investment pair was not supplied')
        directory = Path(directory)
        cls.request = json.loads((directory / 'investment-original-cb464-request.json').read_text('utf8'))
        cls.original = directory / 'cb464f61dd464f6982d57a750bb2d2df-strategy-original.png'
        cls.current = directory / 'investment-guard-ecf0-original.png'
        cls.request['original_png'] = str(cls.original.resolve())
        cls.actual = perception.Perception().read(cls.current, scope='full')
        option = cls.request['observation']['semantic']['options'][1]
        cls.action = {'type': 'click_text', 'text': option['title'], 'exact': True,
                      'expected_page': 'investment', 'target_evidence': {
                          **option, 'text': option['title'], 'snapshot_id': cls.request['snapshot_id']}}

    def test_mixed_books_and_disabled_caption_allow_only_card_selection(self):
        self.assertTrue(guards.stable_semantic_target(
            self.action, self.request, self.request['observation'], self.original))
        self.assertTrue(guards.stable_semantic_target(
            self.action, self.request, self.actual, self.current))
        action = copy.deepcopy(self.action)
        action['text'] = '确认'
        self.assertFalse(guards.stable_semantic_target(action, self.request, self.actual, self.current))

    def test_current_disabled_caption_rendering_noise_preserves_selection(self):
        directory = os.environ.get('CW_NATIVE_INVESTMENT_CURRENT_EVIDENCE_DIR')
        if not directory:
            self.skipTest('Current retained investment pair was not supplied')
        directory = Path(directory)
        request = json.loads((directory / 'investment-current-original-request.json').read_text('utf8'))
        current = directory / 'investment-current-guard-original.png'
        actual = perception.Perception().read(current, scope='full')
        option = request['observation']['semantic']['options'][1]
        action = {'type': 'click_text', 'text': option['title'], 'exact': True,
                  'expected_page': 'investment', 'target_evidence': {
                      **option, 'text': option['title'], 'snapshot_id': request['snapshot_id']}}
        self.assertTrue(guards.stable_semantic_target(action, request, actual, current))
        original = np.array(Image.open(current).convert('RGB'))
        with runner.artifacts.scratch_directory('investment-caption-regression') as directory:
            for damage in ('missing', 'appearing', 'covered', 'shifted', 'dimmed', 'enabled'):
                with self.subTest(damage=damage):
                    changed = original.copy()
                    crop = changed[964:1004,913:1010]
                    if damage in ('missing', 'covered'):
                        crop[:] = 40
                    elif damage == 'appearing':
                        crop[5:25,40:42] = 180
                    elif damage == 'shifted':
                        crop[:] = np.roll(crop, 1, axis=1)
                    elif damage == 'dimmed':
                        crop[:] = np.rint(crop * .7).astype(np.uint8)
                    else:
                        crop[np.max(crop, axis=2) >= 100] = 240
                    path = Path(directory) / (damage + '.png')
                    Image.fromarray(changed).save(path)
                    observation = copy.deepcopy(actual)
                    observation['snapshot_id'] = hashlib.sha256(path.read_bytes()).hexdigest()
                    self.assertFalse(guards.stable_semantic_target(action, request, observation, path))

    def test_changed_book_quality_caption_or_source_is_refused(self):
        pixels = np.array(Image.open(self.current).convert('RGB'))
        # Mutated PNGs have new identities and declared unchanged OCR premises;
        # this checks pixel/source refusal, not OCR accuracy on invented frames.
        with runner.artifacts.scratch_directory('investment-guard-regression') as directory:
            for damage in ('book_disappears', 'book_appears', 'book_cover', 'book_shift',
                           'quality_cover', 'caption_cover'):
                with self.subTest(damage=damage):
                    current = pixels.copy()
                    if damage == 'book_disappears':
                        current[210:252,1113:1149] = current[210:252,1613:1649]
                    elif damage == 'book_appears':
                        current[210:252,1613:1649] = current[210:252,1113:1149]
                    elif damage == 'book_cover':
                        current[222:239,1120:1139] = [172,134,67]
                    elif damage == 'book_shift':
                        current[210:252,1113:1149] = np.roll(current[210:252,1113:1149], 1, axis=1)
                    elif damage == 'quality_cover':
                        current[211:241,837:937] = [172,134,67]
                    else:
                        current[964:1004,913:1010] = 40
                    path = Path(directory) / (damage + '.png')
                    Image.fromarray(current).save(path)
                    actual = copy.deepcopy(self.actual)
                    actual['snapshot_id'] = hashlib.sha256(path.read_bytes()).hexdigest()
                    self.assertFalse(guards.stable_semantic_target(self.action, self.request, actual, path))
        request = copy.deepcopy(self.request)
        request['snapshot_id'] = '0' * 64
        self.assertFalse(guards.stable_semantic_target(self.action, request, self.actual, self.current))
        action = copy.deepcopy(self.action)
        action['target_evidence']['effect_lines'] = ['shortened effect']
        self.assertFalse(guards.stable_semantic_target(action, self.request, self.actual, self.current))


class SupplyNativeGuardTests(unittest.TestCase):
    """Two retained supply captures; all checks remain read-only."""
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get('CW_NATIVE_SUPPLY_EVIDENCE_DIR')
        if not directory:
            raise unittest.SkipTest('Retained supply pair was not supplied')
        directory = Path(directory)
        cls.paths = [directory / (name + '-strategy-original.png') for name in
            ('29ab994f217045d1b44713f1b90dc091', '3b2b0f7dfa3b468cace7a228b77214c6')]
        reader = perception.Perception()
        cls.observations = [reader.read(path,scope='full') for path in cls.paths]
        cls.request = dict(kind='supply_strategy', snapshot_id=cls.observations[0]['snapshot_id'],
            observation=cls.observations[0], original_png=str(cls.paths[0]), resume_epoch='offline-proof')
        option = cls.observations[0]['semantic']['options'][2]
        cls.action = dict(type='click_point', args=[867,567], expected_page='supply',
            target_evidence={**option, 'text':option['title'], 'snapshot_id':cls.request['snapshot_id']})

    def test_complete_supply_and_linked_title_pass_public_selection_without_confirm(self):
        from types import SimpleNamespace
        for observation in self.observations:
            self.assertEqual(len(observation['semantic']['options']),5)
            weak = [row for row in observation['rows'] if row['box']==[836,550,898,584]]
            self.assertEqual(len(weak),1)
            self.assertEqual(weak[0]['confidence'],.655)
            derived = [row for row in observation['rows'] if row.get('normalization_basis')==perception.SUPPLY_TITLE_CONTRAST_BASIS]
            self.assertEqual(len(derived),1)
            self.assertGreaterEqual(derived[0]['confidence'],.90)
            self.assertEqual(derived[0]['normalization_evidence']['primary_rows'],weak)
        self.assertTrue(guards.stable_semantic_target(self.action,self.request,self.observations[1],self.paths[1]))
        worker = SimpleNamespace(epoch=lambda:'offline-proof',frame_path=self.paths[1],log=lambda event:None)
        runner.Worker.check_target_roi(worker,self.action,self.request,self.observations[1])
        confirm = {**self.action,'type':'click_text','text':'确认','exact':True}
        self.assertFalse(guards.stable_semantic_target(confirm,self.request,self.observations[1],self.paths[1]))

    def test_unlinked_conflicting_duplicate_or_occluded_title_and_gray_caption_refuse(self):
        for damage in ('missing_link','conflict','duplicate','wrong_snapshot','wrong_primary_field'):
            with self.subTest(damage=damage):
                actual = copy.deepcopy(self.observations[1])
                derived = next(row for row in actual['rows'] if row.get('normalization_basis')==perception.SUPPLY_TITLE_CONTRAST_BASIS)
                if damage=='missing_link':
                    derived.pop('normalization_evidence')
                elif damage=='conflict':
                    next(row for row in actual['rows'] if row['box']==[836,550,898,584])['text']='Conflicting title'
                elif damage=='duplicate':
                    actual['rows'].append(copy.deepcopy(derived))
                elif damage=='wrong_snapshot':
                    derived['normalization_evidence']['snapshot_id']='0'*64
                else:
                    derived['normalization_evidence']['primary_field']=[832,546,902,588]
                self.assertFalse(guards.stable_semantic_target(self.action,self.request,actual,self.paths[1]))
        original = np.array(Image.open(self.paths[1]).convert('RGB'))
        with runner.artifacts.scratch_directory('supply-guard-regression') as directory:
            for damage in ('title_cover','leading_glyph','trailing_glyph','caption_cover','caption_move','selected_count'):
                with self.subTest(damage=damage):
                    changed = original.copy()
                    actual = copy.deepcopy(self.observations[1])
                    if damage=='title_cover':
                        changed[552:580,835:897]=120
                    elif damage=='leading_glyph':
                        changed[554:578,833:838]=255
                    elif damage=='trailing_glyph':
                        changed[554:578,910:926]=255
                    elif damage=='caption_cover':
                        changed[966:1000,1645:1739]=40
                    elif damage=='caption_move':
                        changed[966:1000,1645:1739]=np.roll(changed[966:1000,1645:1739],1,axis=1)
                    else:
                        next(row for row in actual['rows'] if row['text']=='已选择0/1')['text']='已选择1/1'
                    path = Path(directory)/(damage+'.png')
                    Image.fromarray(changed).save(path)
                    actual['snapshot_id']=hashlib.sha256(path.read_bytes()).hexdigest()
                    for row in actual['rows']:
                        if row.get('normalization_basis')==perception.SUPPLY_TITLE_CONTRAST_BASIS:
                            row['normalization_evidence']['snapshot_id']=actual['snapshot_id']
                    self.assertFalse(guards.stable_semantic_target(self.action,self.request,actual,path))


if __name__ == '__main__':
    unittest.main()
