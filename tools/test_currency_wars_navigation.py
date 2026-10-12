"""Offline navigation protocol checks; generated pixels and declared OCR only.

These are not retained game frames, native OCR accuracy, or game receipts. The
menu labels/positions are explicit supported-domain premises. Real Worker and
Entry consume immutable generated PNGs through the existing inert transport;
the publisher has no device, broker process, or input API.
"""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw

import currency_wars_perception as perception
import currency_wars_runner as runner
import currency_wars_visual_guards as guards
import replay_currency_wars_navigation as navigation_replay
import test_local_runtime_compatibility as compatibility


def rows_for(domain):
    if domain == 'opponents':
        labels = [('竞争对手', [120, 80, 240, 112]),
                  ('下一步', [1660, 900, 1760, 932])]
    else:
        anchors = {'peace_universe': ('星际和平指南', '宇宙纷争'),
                   'peace_loot': ('星际和平指南', '逐光捡金'),
                   'journey': ('旅情事记', '常驻活动')}[domain]
        labels = [(anchors[0], [120, 80, 300, 112]),
                  (anchors[1], [120, 160, 260, 192]),
                  ('货币战争', [1060, 470, 1200, 502])]
    return [{'text': text, 'box': box, 'confidence': .99} for text, box in labels]


def render(rows, phase=0, polarity='light', damage=None):
    """Deterministic surrogate glyphs, never a claimed CJK OCR render."""
    bands = ((np.arange(1920) // 32 + phase) % 2 * 65 + 100).astype(np.uint8)
    pixels = np.broadcast_to(bands[None, :, None], (1080, 1920, 3)).copy()
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    color = (240, 240, 240) if polarity == 'light' else (18, 18, 18)
    for row in rows:
        x, y, right, bottom = row['box']
        draw.rectangle([x-10, y-10, right+10, bottom+10], fill=(130, 130, 130))
        # Fixed surrogate strokes make pixel motion/occlusion meaningful while
        # declared reader rows supply the protocol's exact text and confidence.
        for index, character in enumerate(row['text']):
            left = x + 5 + index * 19
            if left + 12 >= right:
                break
            draw.rectangle([left, y+6, left+2, bottom-7], fill=color)
            draw.rectangle([left, y+6, left+12, y+8], fill=color)
            if ord(character) % 2:
                draw.rectangle([left, y+17, left+11, y+19], fill=color)
            else:
                draw.rectangle([left+10, y+7, left+12, bottom-7], fill=color)
    if damage:
        x, y, right, bottom = rows[-1]['box']
        if damage == 'occluded':
            draw.rectangle([x+3, y+4, x+16, bottom-4], fill=(130, 130, 130))
        elif damage == 'gray':
            pixels = np.array(image)
            patch = pixels[y:bottom, x:right]
            mask = (patch[:, :, 0] > 190) if polarity == 'light' else (patch[:, :, 0] < 80)
            patch[mask] = 120
            image = Image.fromarray(pixels)
        elif damage == 'center_cover':
            cx, cy = (x+right)//2, (y+bottom)//2
            draw.rectangle([cx-4, cy-4, cx+4, cy+4], fill=(150, 70, 150))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return image, output.getvalue()


class NavigationTests(unittest.TestCase):
    @contextlib.contextmanager
    def worker(self, domain='opponents', *, phase=1, polarity='light', change=None):
        fixture = compatibility.RuntimeCompatibilityTests()
        original_rows = rows_for(domain)
        if change == 'body_layout':
            # The same three strings remain stable in both frames, but a
            # centered paragraph is not the supported header/menu layout.
            for index, row in enumerate(original_rows):
                row['box'] = [700, 400+70*index, 900, 432+70*index]
        elif change == 'next_at_top':
            original_rows[-1]['box'] = [1660, 160, 1760, 192]
        elif change == 'center_cover':
            # A declared wide text box with a blank center, distinct from the
            # surrogate glyphs and their two-pixel halo. Not a native layout.
            original_rows[-1]['box'][2] = original_rows[-1]['box'][0] + 200
        current_rows = copy.deepcopy(original_rows)
        actual_page = 'opponents' if domain == 'opponents' else 'unknown'
        damage = None
        if change in ('target_move', 'anchor_move'):
            row = current_rows[-1 if change == 'target_move' else 0]
            row['box'] = [value+4 if index % 2 == 0 else value for index, value in enumerate(row['box'])]
        elif change == 'text':
            current_rows[-1]['text'] += '新'
        elif change == 'page':
            actual_page = 'shop'
        elif change == 'modal':
            current_rows.append({'text': '是否确认', 'box': [700, 340, 830, 372], 'confidence': .80})
        elif change in ('occluded', 'gray', 'center_cover'):
            damage = change
        elif change in ('duplicate_target', 'duplicate_anchor'):
            duplicate = copy.deepcopy(current_rows[-1 if change == 'duplicate_target' else 0])
            duplicate['box'] = [700, 600, 890, 632]
            current_rows.append(duplicate)
        elif change in ('low_target', 'low_anchor'):
            current_rows[-1 if change == 'low_target' else 0]['confidence'] = .89
        elif change == 'missing_anchor':
            current_rows.pop(0)
        original, original_png = render(original_rows, polarity=polarity)
        current, current_png = render(current_rows, phase, polarity, damage)
        if change == 'center_cover':
            old, fresh = np.array(original), np.array(current)
            ink = np.all(old > 190, axis=2) if polarity == 'light' else np.all(old < 80, axis=2)
            fresh_ink = np.all(fresh > 190, axis=2) if polarity == 'light' else np.all(fresh < 80, axis=2)
            self.assertTrue(np.array_equal(ink, fresh_ink))
            halo = np.logical_or.reduce([np.roll(np.roll(ink, y, axis=0), x, axis=1)
                                         for x in range(-2, 3) for y in range(-2, 3)])
            changed = np.any(old != fresh, axis=2)
            self.assertEqual(np.count_nonzero(changed), 81)
            self.assertFalse(np.any(changed & halo))
        with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            (runtime / 'runner-manual.json').unlink()
            control.frame = original_png
            control.validate_actions = lambda actions: [
                {'type': action['type'], 'args': [float(value) for value in action.get('args', [])]}
                for action in actions]

            def declaration(image, rows, page):
                return {'page': page, 'rows': rows, 'fields': {}, 'semantic': {},
                        'fingerprint': perception.fingerprint(image),
                        'read_contract': {'version': runner.READ_CONTRACT_VERSION,
                                          'effective_scope': 'full', 'unread': []}}

            original_sha = hashlib.sha256(original_png).hexdigest()
            current_sha = hashlib.sha256(current_png).hexdigest()
            reader.frames[original_sha] = declaration(original, original_rows,
                'opponents' if domain == 'opponents' else 'unknown')
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
            worker.ask(worker.last_observation,
                'opponents_strategy' if domain == 'opponents' else 'unknown_page',
                '离线协议：主管已经选定一个导航动作')
            request = worker.state['decision_request']
            # Register after ask, including identical-pixel confidence changes:
            # OCR declarations are explicit protocol premises, never native OCR.
            reader.frames[current_sha] = declaration(current, current_rows, actual_page)
            control.frame = current_png
            action = {'type': 'click_text', 'text': original_rows[-1]['text'],
                      'exact': True, 'expected_page': request['observation']['page'],
                      'reason': '离线协议：执行本次主管已选定的唯一导航'}
            substring_values = {'substring': False, 'substring_zero': 0,
                                'substring_none': None, 'substring_empty': ''}
            if change in substring_values:
                action.update(text=action['text'][:-1], exact=substring_values[change])
            elif change == 'empty_text':
                action.update(text='', exact=False, bounds=original_rows[-1]['box'])
            reply = {key: request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
            reply['actions'] = [action]
            yield worker, control, reply, request, current_sha

    def inputs(self, control):
        return [item for item in control.published
                if any(action['type'] not in ('observe', 'wait') for action in item.get('actions', []))]

    def test_single_selected_navigation_accepts_large_background_animation_in_both_polarities(self):
        for domain in ('opponents', 'peace_universe', 'peace_loot', 'journey'):
            for polarity in ('light', 'dark'):
                with self.subTest(domain=domain, polarity=polarity), self.worker(domain, polarity=polarity) as data:
                    worker, control, reply, request, current_sha = data
                    source_bytes = Path(request['original_png']).read_bytes()
                    source_frame = request['observation']['frame_id']
                    self.assertGreater(runner.hash_distance(request['observation']['fingerprint'],
                        worker.perception.frames[current_sha]['fingerprint']), .10)
                    worker.execute_plan(reply)
                    published = self.inputs(control)
                    self.assertEqual(len(published), 1)
                    box = request['observation']['rows'][-1]['box']
                    self.assertEqual(published[0]['actions'], [
                        {'type': 'click', 'args': [(box[0]+box[2])/2, (box[1]+box[3])/2]},
                        {'type': 'wait', 'args': [.7]}])
                    self.assertEqual(worker.state['statistics']['local_inputs'], 1)
                    self.assertEqual(worker.state['statistics']['local_observations'], 2)
                    self.assertNotEqual(worker.last_observation['frame_id'], source_frame)
                    self.assertEqual(Path(request['original_png']).read_bytes(), source_bytes)
                    self.assertIsNone(worker.state['decision_request'])
                    self.assertTrue(any(event['event'] == 'local_navigation_guard_matched'
                                        for event in worker.log_events))
                    outcomes = [event for event in worker.log_events if event['event'] == 'actual_result']
                    self.assertEqual(len(outcomes), 1)
                    self.assertIs(outcomes[0]['outcome_confirmed'], False)

    def test_small_global_delta_never_hides_changed_target_anchors_page_or_exposure(self):
        changes = ('target_move', 'anchor_move', 'text', 'page', 'modal', 'occluded', 'gray',
                   'duplicate_target', 'duplicate_anchor', 'low_target', 'low_anchor', 'missing_anchor',
                   'substring', 'substring_zero', 'substring_none', 'substring_empty', 'empty_text',
                   'center_cover')
        for domain in ('opponents', 'peace_loot'):
            for change in changes:
                with self.subTest(domain=domain, change=change), self.worker(domain, phase=0, change=change) as data:
                    worker, control, reply, request, current_sha = data
                    self.assertLessEqual(runner.hash_distance(request['observation']['fingerprint'],
                        worker.perception.frames[current_sha]['fingerprint']), .10)
                    with self.assertRaises((ValueError, RuntimeError)):
                        worker.execute_plan(reply)
                    self.assertEqual(self.inputs(control), [])
                    self.assertEqual(worker.state['statistics']['local_inputs'], 0)
                    self.assertIs(worker.state['decision_request'], request)

    def test_no_exemption_for_wrong_scope_unknown_target_multiple_actions_or_source_change(self):
        for case in ('wrong_kind', 'not_exact', 'wrong_bounds', 'unknown_target',
                     'multiple_actions', 'changed_source', 'reply_snapshot',
                     'body_layout', 'next_at_top'):
            domain = 'opponents' if case == 'next_at_top' else 'journey'
            layout = case if case in ('body_layout', 'next_at_top') else None
            with self.subTest(case=case), self.worker(domain, change=layout) as data:
                worker, control, reply, request, unused = data
                if case == 'wrong_kind':
                    request['kind'] = 'shop_strategy'
                elif case == 'not_exact':
                    reply['actions'][0]['exact'] = False
                elif case == 'wrong_bounds':
                    reply['actions'][0]['bounds'] = [10, 10, 100, 100]
                elif case == 'unknown_target':
                    reply['actions'][0]['text'] = '常驻活动'
                    self.assertIsNone(guards.navigation_target(reply['actions'][0], request))
                elif case == 'multiple_actions':
                    reply['actions'].append(copy.deepcopy(reply['actions'][0]))
                elif case == 'changed_source':
                    Path(request['original_png']).write_bytes(control.frame)
                elif case == 'reply_snapshot':
                    reply['snapshot_id'] = '0' * 64
                else:
                    self.assertEqual(request['observation']['rows'],
                                     worker.perception.frames[unused]['rows'])
                    self.assertEqual(perception.classify(request['observation']['rows']),
                                     request['observation']['page'])
                with self.assertRaises((ValueError, RuntimeError)):
                    worker.execute_plan(reply)
                self.assertEqual(self.inputs(control), [])
                self.assertEqual(worker.state['statistics']['local_inputs'], 0)

    def test_publication_fence_still_rejects_late_epoch_pending_foreground_and_request_changes(self):
        for change in ('epoch', 'pending', 'foreground', 'request'):
            with self.subTest(change=change), self.worker() as data:
                worker, control, reply, request, unused = data
                original_write = control.write_json
                original_status = control.status
                injected = []
                control.foreground = True
                control.status = lambda: {**original_status(), 'game_foreground': control.foreground}

                def write(path, value):
                    original_write(path, value)
                    intent = value.get('request', {})
                    if (Path(path).parent.name == 'request-ledger'
                            and any(action['type'] == 'click' for action in intent.get('actions', []))):
                        self.assertFalse(injected)
                        self.assertTrue(any(event['event'] == 'local_navigation_guard_matched'
                                            for event in worker.log_events))
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
                refused = [event for event in worker.log_events if event['event'] == 'actual_result']
                self.assertEqual(len(refused), 1)
                self.assertIs(refused[0]['request_published'], False)
                # The new unsubmitted click intent is removed by the original
                # GuardedSubmission contract; the unresolved old step persists.
                for path in (worker.run / 'request-ledger').glob('*.json'):
                    self.assertFalse(any(action['type'] == 'click'
                        for action in runner.optional(path).get('request', {}).get('actions', [])))
                if change == 'pending':
                    self.assertEqual(runner.optional(worker.run / 'reward-step.json')['status'], 'pending')

    def test_retained_pair_driver_reports_only_byte_eligibility_without_authentication_or_input(self):
        with self.worker() as (worker, control, reply, request, current_sha):
            actual = copy.deepcopy(worker.observe())
            pair = worker.records / 'local-protocol-pair'
            pair.mkdir()
            source = copy.deepcopy(request)
            source['run_token'] = 'private-protocol-token-must-not-appear'
            spec = {'schema': 1, 'request_json': 'request.json', 'reply_json': 'reply.json',
                    'current_observation_json': 'current.json',
                    'original_png': 'original.png', 'current_png': 'current.png'}
            for name, value in (('spec.json', spec), ('request.json', source),
                                ('reply.json', reply), ('current.json', actual)):
                (pair / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
            (pair / 'original.png').write_bytes(Path(request['original_png']).read_bytes())
            (pair / 'current.png').write_bytes(worker.frame_path.read_bytes())
            published_before = copy.deepcopy(control.published)
            report = navigation_replay.replay_pair(pair / 'spec.json')
            self.assertEqual(report['png_sha256'], [request['snapshot_id'], current_sha])
            for key in ('original_source_identities_present', 'original_png_digests_match',
                        'original_fingerprints_match', 'byte_bound_visual_eligibility'):
                self.assertIs(report[key], True)

            def assert_summary_only(value):
                for key in ('native_capture_authentication_replayed',
                            'current_epoch_pending_foreground_verified',
                            'input_published', 'navigation_outcome_verified'):
                    self.assertIs(value[key], False)
                self.assertEqual(set(value), {
                    'spec_sha256', 'request_json_sha256', 'reply_json_sha256', 'current_json_sha256',
                    'png_sha256', 'page', 'control_id', 'actual_fingerprint_distance',
                    'original_source_identities_present', 'original_png_digests_match',
                    'original_fingerprints_match', 'visual_eligibility', 'byte_bound_visual_eligibility',
                    'native_capture_authentication_replayed', 'current_epoch_pending_foreground_verified',
                    'input_published', 'navigation_outcome_verified'})
                encoded = json.dumps(value, ensure_ascii=False)
                for sensitive in (source['run_token'], str(pair), str(worker.run),
                                  str(request['original_png']), '竞争对手', '下一步'):
                    self.assertNotIn(sensitive, encoded)
                self.assertEqual(control.published, published_before)

            assert_summary_only(report)
            missing_identity = copy.deepcopy(actual)
            missing_identity.pop('frame_id')
            current_json = pair / 'current.json'
            current_json.write_text(json.dumps(missing_identity, ensure_ascii=False), encoding='utf8')
            untouched = current_json.read_bytes()
            missing = navigation_replay.replay_pair(pair / 'spec.json')
            self.assertIs(missing['original_source_identities_present'], False)
            self.assertIs(missing['byte_bound_visual_eligibility'], True)
            self.assertEqual(current_json.read_bytes(), untouched)
            self.assertNotIn('frame_id', json.loads(current_json.read_text(encoding='utf8')))
            assert_summary_only(missing)

            # Restore the supplied identity, then replace only current PNG
            # bytes. A valid-size PNG cannot borrow the old observation SHA.
            current_json.write_text(json.dumps(actual, ensure_ascii=False), encoding='utf8')
            (pair / 'current.png').write_bytes((pair / 'original.png').read_bytes())
            replaced = navigation_replay.replay_pair(pair / 'spec.json')
            self.assertIs(replaced['original_source_identities_present'], True)
            self.assertIs(replaced['original_png_digests_match'], False)
            self.assertIs(replaced['byte_bound_visual_eligibility'], False)
            assert_summary_only(replaced)
            self.assertEqual(self.inputs(control), [])


if __name__ == '__main__':
    unittest.main()
