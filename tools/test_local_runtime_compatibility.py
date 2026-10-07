"""Regression checks for local startup compatibility; no game or GUI input."""
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import threading
import sys
import tempfile
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor

import currency_wars_artifacts as artifacts
import currency_wars_broker_entry as entry
import currency_wars_perception as perception
import currency_wars_runner as runner
import currency_wars_source_guard as guard


class RuntimeCompatibilityTests(unittest.TestCase):
    def gui_launcher(self):
        path=Path(__file__).resolve().parent.parent/'gui'/'launch.py'
        spec=importlib.util.spec_from_file_location('currency_wars_gui_launch_test',path)
        module=importlib.util.module_from_spec(spec)
        with patch.object(sys,'path',[str(path.parent),*sys.path]):
            spec.loader.exec_module(module)
        return module

    def test_gui_runtime_registration_publishes_after_both_identity_registrations(self):
        launch=self.gui_launcher()
        with tempfile.TemporaryDirectory(prefix='currency-wars-gui-launch-') as temporary:
            root=Path(temporary)/'selected-runtime-root';root.mkdir()
            runtime=root/'owned-gui';runtime.mkdir()
            location={'schema':1,'source':'standalone','runtime_root':str(root),'installation_id':None}
            marker={'pid':os.getpid(),'process_identity':'windows:41','run_id':'a'*32}
            events=[]
            lease=SimpleNamespace(children=lambda children,complete:events.append(('lease',children,complete)))
            real_write=launch.input_bridge.write_object
            def identity(pid):return ('active','windows:41' if pid==os.getpid() else 'windows:42')
            def protect(path,children,*,root,complete):
                self.assertEqual(path,runtime);self.assertEqual(root,runtime.parent)
                events.append(('marker',children,complete))
            def read(path,*,root):
                self.assertEqual(path,runtime);self.assertEqual(root,runtime.parent)
                self.assertEqual([event[0] for event in events],['lease','marker'])
                self.assertFalse((runtime/'runtime-location.json').exists())
                return marker
            def write(path,value):
                self.assertEqual([event[0] for event in events],['lease','marker'])
                real_write(path,value)
            records={}
            with patch.object(launch.artifacts,'process_identity',side_effect=identity), \
                 patch.object(launch.artifacts,'protect_children',side_effect=protect), \
                 patch.object(launch.artifacts,'read_marker',side_effect=read), \
                 patch.object(launch.input_bridge,'write_object',side_effect=write):
                result=launch.register_runtime_location(runtime,location,'chat',123456,lease,records)
            self.assertEqual(result,'windows:42')
            record=json.loads((runtime/'runtime-location.json').read_text())
            self.assertEqual(record,{'schema':1,'owner':'currency-wars-gui-runtime','run_id':'a'*32,'chat_id':'chat',
                                    'runtime_location':location,'launcher_pid':os.getpid(),'launcher_creation_id':'41',
                                    'gui_pid':123456,'gui_creation_id':'42'})
            self.assertEqual(events[0][1],events[1][1])
            self.assertFalse(events[0][2]);self.assertFalse(events[1][2])

    def test_gui_runtime_registration_rejects_unknown_or_reused_creation_before_publish(self):
        launch=self.gui_launcher()
        with tempfile.TemporaryDirectory(prefix='currency-wars-gui-launch-') as temporary:
            runtime=Path(temporary)
            marker={'pid':os.getpid(),'process_identity':'windows:41','run_id':'a'*32}
            location={'schema':1,'source':'standalone','runtime_root':str(runtime.parent),'installation_id':None}
            cases=[ [('unknown',None)],
                    [('active','windows:42'),('active','windows:41'),('active','windows:43')],
                    [('active','windows:42'),('active','windows:99')] ]
            for identities in cases:
                with self.subTest(identities=identities), \
                     patch.object(launch.artifacts,'process_identity',side_effect=identities), \
                     patch.object(launch.artifacts,'protect_children') as protect, \
                     patch.object(launch.artifacts,'read_marker',return_value=marker), \
                     patch.object(launch.input_bridge,'write_object') as publish:
                    with self.assertRaises(RuntimeError):
                        launch.register_runtime_location(runtime,location,'chat',123456,SimpleNamespace(children=lambda *a,**k:None),{})
                    publish.assert_not_called()
                    if len(identities)==1:protect.assert_not_called()

    @contextlib.contextmanager
    def manual_bridge_fixture(self):
        with tempfile.TemporaryDirectory(prefix='currency-wars-manual-bridge-test-') as temporary:
            outer = Path(temporary)
            runtime, records = outer / 'runtime', outer / 'debug' / 'records'
            runtime.mkdir()
            records.mkdir(parents=True)
            owner = {'run_id': 'owned-run', 'chat_id': 'owned-chat'}
            from PIL import Image
            before_png, after_png = io.BytesIO(), io.BytesIO()
            Image.new('RGB', (1920, 1080), 'black').save(before_png, format='PNG')
            Image.new('RGB', (1920, 1080), 'white').save(after_png, format='PNG')
            class Control:
                ROOT = str(runtime)
                OWNER = {'chat_id': 'owned-chat', 'run_token': 'owned-token'}
                frame = before_png.getvalue()
                published = []
                pauses = []
                def status(self):
                    return {'ready': True, 'paused': False, 'input_halted': False, 'game_foreground': True,
                            'broker_pid': 123, 'broker_creation_time': '456', 'pause_id': None}
                def pause(self, reason):
                    self.pauses.append(reason)
                    return {'ok': True, 'paused': True, 'reason': reason}
                def submission_lock(self):
                    return contextlib.nullcontext()
                def validate_actions(self, value):
                    return value
                def write_json(self, path, value):
                    Path(path).write_text(json.dumps(value), encoding='utf8')
                def publish_request(self, value):
                    self.published.append(value)
                    if value.get('actions') and value['actions'][0]['type'] == 'click':
                        self.frame = after_png.getvalue()
                    frame_id = uuid.uuid4().hex
                    directory = runtime / 'frames' / (hashlib.sha256(value['id'].encode()).hexdigest() + '-' + frame_id)
                    directory.mkdir(parents=True)
                    for filename in ('original.png', 'preview.png'):
                        (directory / filename).write_bytes(self.frame)
                    digest = hashlib.sha256(self.frame).hexdigest()
                    self.write_json(runtime / 'result.json', {'id': value['id'], 'ok': True,
                        'completed': value.get('actions', []),
                        'input_attempted': bool(value.get('handoff') or any(action['type'] not in ('observe', 'wait')
                                                for action in value.get('actions', []))),
                        'attempted_actions': [action for action in value.get('actions', []) if action['type'] not in ('observe', 'wait')],
                        'observation': {
                            'frame_protocol': 1, 'request_id': value['id'], 'frame_id': frame_id,
                            'captured_at': runner.now(), 'snapshot': str(directory / 'preview.png'),
                            'original': str(directory / 'original.png'), 'snapshot_sha256': digest,
                            'original_sha256': digest, 'snapshot_size': [1920, 1080], 'original_size': [1920, 1080]}})
            control = Control()
            reader = SimpleNamespace(frames={})
            def read_frame(path, force=False, *, scope='full'):
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                return json.loads(json.dumps({'snapshot_id': digest, 'page': 'preparation',
                    'fields': {'stage': '2-3', 'deployed': '1/1', 'coins': 52, 'level': 7, 'xp': '38/52'},
                    'rows': [], 'semantic': {}, 'elapsed_ms': 0., **reader.frames.get(digest, {})}))
            reader.read = read_frame
            control.write_json(records / 'owner.json', owner)
            control.write_json(runtime / 'runner-state.json', {**owner, 'match_id': 'match', 'preparation_stage': '2-3',
                'journal_file': str(records / 'journal.jsonl')})
            control.write_json(runtime / 'runner-manual.json', {'manual_id': 'manual-one', 'reason': 'manual'})
            control.write_json(runtime / 'runner-resume-epoch.json', {'id': 'old-epoch'})
            with patch.object(runner, 'PROJECT', outer):
                yield runtime, records, owner, control, reader

    def complete_manual_reward(self, runtime, owner, control, reader):
        checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
        entry.request(control, 'actions', ['click:1:2'], 'manual-click', False)
        review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent',
                  'findings': 'Actual before/after reward review', 'all_claimed': True, 'rescanned_after_claim': True}
        return runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'], ['manual-click'], review, reader=reader)

    def frame_worker(self, runtime, records, owner, control, reader):
        worker = object.__new__(runner.Worker)
        worker.run, worker.records, worker.owner, worker.c, worker.perception = runtime, records, owner, control, reader
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.deadline = runner.time.monotonic() + 60
        worker.frame_path, worker.frame_result, worker.last_observation = None, None, None
        worker.evidence_count, worker.strategy_reads = 0, {}
        worker.worker_request_ids = set()
        worker.state = {'statistics': {'local_inputs': 0, 'local_observations': 0, 'ocr_ms': 0., 'broker_ms': 0.},
                        'decision_request': None}
        worker.log = lambda event: None
        return worker

    def test_worker_reads_its_receipt_and_releases_only_consumed_previous_frame(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            worker = self.frame_worker(runtime, records, owner, control, reader)
            first = entry.request(control, 'actions', ['observe'], 'first', False)
            first_path = entry.observation_frame(runtime, first)
            second = entry.request(control, 'actions', ['click:1:2'], 'second', False)
            (runtime / 'game-preview.png').write_bytes(b'broken obsolete shared alias')
            observed = worker.read_frame(first)
            self.assertEqual(observed['snapshot_id'], first['observation']['snapshot_sha256'])
            self.assertEqual(observed['capture_request_id'], 'first')
            self.assertNotEqual(observed['snapshot_id'], second['observation']['snapshot_sha256'])
            worker.read_frame(second)
            self.assertFalse(first_path.exists())
            self.assertTrue(worker.frame_path.exists())
            count = len(control.published)
            self.assertEqual(entry.request(control, 'actions', ['observe'], 'first', False), first)
            self.assertEqual(len(control.published), count)
            with self.assertRaises(entry.ObservationUnavailable):
                entry.observation_frame(runtime, first)

    def test_worker_capture_failure_reobserves_without_resending_completed_input(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            worker = self.frame_worker(runtime, records, owner, control, reader)
            (runtime / 'runner-manual.json').unlink()
            initial = entry.request(control, 'actions', ['observe'], 'initial', False)
            worker.read_frame(initial)
            worker.last_observation['rows'] = [
                {'text': '备战阶段', 'confidence': .99, 'box': [420, 25, 530, 60]},
                {'text': '出战', 'confidence': .99, 'box': [1760, 720, 1860, 770]},
            ]
            actual_request = entry.request
            calls = []

            def request(controller, kind, tokens, rid, handoff, **kwargs):
                calls.append(tokens)
                result = actual_request(controller, kind, tokens, rid, handoff, **kwargs)
                if tokens[0].startswith('click:'):
                    # The input really returned once; only its PNG became unusable.
                    Path(result['observation']['snapshot']).write_bytes(b'truncated')
                return result

            with patch.object(entry, 'request', side_effect=request):
                after = worker.command(['click:1:2'], 'fixture input', 'preparation', 'unknown effect')
            self.assertEqual(calls, [['click:1:2'], ['observe']])
            self.assertEqual(worker.state['statistics']['local_inputs'], 1)
            self.assertEqual(after['snapshot_id'], hashlib.sha256(control.frame).hexdigest())
            self.assertEqual(after['page'], 'preparation')

    def test_worker_bad_observations_are_bounded_and_do_not_adopt_old_frame(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            worker = self.frame_worker(runtime, records, owner, control, reader)
            result = entry.request(control, 'actions', ['observe'], 'initial', False)
            old = worker.read_frame(result)
            actual_request = entry.request
            calls = []

            def request(controller, kind, tokens, rid, handoff, **kwargs):
                calls.append(tokens)
                fresh = actual_request(controller, kind, tokens, rid, handoff, **kwargs)
                Path(fresh['observation']['snapshot']).write_bytes(b'truncated')
                return fresh

            with patch.object(entry, 'request', side_effect=request), self.assertRaises(entry.ObservationUnavailable):
                worker.observe()
            self.assertEqual(calls, [['observe'], ['observe']])
            self.assertIs(worker.last_observation, old)

    def resumed_manual_worker(self, runtime, records, owner, control, reader, item):
        result = runner.explicit_resume(runtime, owner, control, 'new-epoch',
                                        expected_guard=runner.resume_guard_snapshot(runtime, owner, control))
        self.assertTrue(result['ok'])
        worker = object.__new__(runner.Worker)
        worker.run, worker.records, worker.owner, worker.c, worker.perception = runtime, records, owner, control, reader
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.preparation_scope, worker.preparation_reviews, worker.context = None, {}, {}
        worker.node_progress, worker.live_mode = 0, None
        worker.events = []
        worker.log = worker.events.append
        worker.publish = lambda **updates: worker.state.update(updates)
        worker.history, worker.state = {}, {}
        self.current_manual_frame(worker, control, reader)
        return worker

    def current_manual_frame(self, worker, control, reader):
        rid = uuid.uuid4().hex
        receipt = entry.request(control, 'actions', ['observe'], rid, False)
        frame = entry.observation_frame(worker.run, receipt)
        path = worker.records / ('current-' + rid + '.png')
        path.write_bytes(frame.read_bytes())
        fresh = {**reader.read(path), 'evidence_file': str(path), 'observed_at': runner.now(),
            'capture_request_id': rid, 'frame_id': receipt['observation']['frame_id'],
            'captured_at': receipt['observation']['captured_at'], 'preparation_stage': '2-3',
            'match_id': 'match', 'resume_epoch': worker.epoch()}
        worker.last_observation = fresh
        worker.history[fresh['snapshot_id']] = fresh
        worker.state['decision_request'] = {'request_id': uuid.uuid4().hex, 'snapshot_id': fresh['snapshot_id'],
            'evidence_file': fresh['evidence_file'], 'original_png': str(path), 'observation': fresh,
            'resume_epoch': worker.epoch()}
        return fresh

    def current_manual_review(self, worker, value):
        fresh = worker.last_observation
        worker.review_preparation({'value': value, 'proof': {'source': 'observed_screen',
            'snapshot_id': fresh['snapshot_id'], 'evidence_file': fresh['evidence_file'], 'resume_epoch': worker.epoch()}})

    def manual_image(self, control, reader, color, **observation):
        from PIL import Image
        payload = io.BytesIO()
        Image.new('RGB', (1920, 1080), color).save(payload, format='PNG')
        control.frame = payload.getvalue()
        reader.frames[hashlib.sha256(control.frame).hexdigest()] = observation

    def test_manual_bridge_partial_completion_survives_resume_without_blanket_completion_or_resend(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            self.assertNotEqual(item['before']['snapshot_id'], item['after']['snapshot_id'])
            self.assertTrue(Path(item['before']['evidence_file']).exists())
            self.assertTrue(Path(item['after']['evidence_file']).exists())
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            published = len(control.published)
            self.assertFalse(worker.consume_manual_results())
            self.assertFalse(getattr(worker, 'manual_results_rejected', set()))
            self.current_manual_review(worker, item['review'])
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertEqual(set(worker.preparation_reviews), {'rewards'})
            self.assertEqual(worker.preparation_reviews['rewards']['proof']['resume_epoch'], 'new-epoch')
            self.assertEqual(item['binding']['old_epoch'], 'old-epoch')
            self.assertEqual(len(control.published), published)
            event, receipt = runner.verified_resume_event(runtime, owner, control,
                entry.read_json(runtime / 'runner-resume-epoch.json'))
            self.assertEqual(event['old_epoch'], 'old-epoch')
            self.assertEqual(receipt['result']['id'], 'new-epoch')
            self.assertFalse((runtime / 'runner-battle-approval.json').exists())
            self.assertFalse(worker.consume_manual_results())

    def test_progression_knowledge_loads_conditions_but_never_cached_progress(self):
        with artifacts.scratch_directory('currency-wars-goal-cache-test') as runtime:
            source = runtime / 'knowledge.json'
            source.write_text(json.dumps({'schema': 'currency-wars-observed-knowledge/v1',
                'progression': {'completed_goal_preferences': ['巡海游侠'], 'task_conditions': [
                    {'id': 'upgrade', 'condition': '累计使用2次特权赋予卡升级进阶装备', 'condition_met': True,
                     'reward_claimed': True, 'progress': '2/2', 'proof': {'snapshot_id': 'old'}}]}}), encoding='utf8')
            knowledge = runner.coaching.load_knowledge(source)
            self.assertEqual(knowledge['progression']['task_conditions'], [
                {'id': 'upgrade', 'condition': '累计使用2次特权赋予卡升级进阶装备'}])
            proposal = runner.progression_plan(knowledge, {'snapshot_id': 'current', 'semantic': {}})
            self.assertEqual(proposal['tasks'][0]['state'], 'unknown')
            self.assertEqual(proposal['action_opportunities'], [])

    def test_manual_bridge_rejects_changed_match_stage_new_takeover_and_stale_fresh_frame(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            worker.preparation_checklist(worker.last_observation)
            fresh = worker.history[worker.last_observation['snapshot_id']]
            for field, value in [('match_id', 'other-match'), ('stage', '2-4'), ('old_epoch', 'wrong-epoch')]:
                changed = json.loads(json.dumps(item))
                changed['binding'][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    worker.verified_manual_source(changed, fresh)
            changed = json.loads(json.dumps(item))
            changed['after']['observed_at'] = '2000-01-01T00:00:00+00:00'
            with self.assertRaises(ValueError):
                worker.verified_manual_source(changed, fresh)
            with self.assertRaises(ValueError):
                worker.verified_manual_source(item, {**fresh, 'fields': {'stage': '2-4'}})
            control.write_json(runtime / 'runner-manual.json', {'manual_id': 'new-takeover', 'reason': 'manual'})
            with self.assertRaises(ValueError):
                worker.verified_manual_source(item, fresh)
            self.assertEqual(worker.preparation_reviews, {})

    def test_manual_trace_does_not_survive_a_later_unrecorded_sale(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            entry.request(control, 'actions', ['key:46'], 'unrecorded-sale', False)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            count = len(control.published)
            self.assertFalse(worker.consume_manual_results())
            self.assertEqual(worker.preparation_reviews, {})
            self.assertFalse(worker.preparation_checklist(worker.last_observation)['battle_ready'])
            self.assertEqual(len(control.published), count)

    def test_unknown_manual_phase_keeps_prior_verified_phase_without_new_takeover(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            # This second trace lacks a readable native guide title. It must
            # remain pending while the accepted reward review survives.
            incomplete = json.loads(json.dumps(item))
            incomplete['checkpoint_id'], incomplete['phase'] = 'unknown-guide', 'startup_guide'
            incomplete['review'].update(phase='startup_guide', entry_index=2, rewards_claimed=True, goals=[])
            control.write_json(runtime / 'manual-results' / 'unknown-guide.json', incomplete)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            self.current_manual_review(worker, item['review'])
            count = len(control.published)
            self.assertFalse(worker.consume_manual_results())
            status = worker.preparation_checklist(worker.last_observation)
            self.assertEqual(status['phase'], 'startup_guide')
            self.assertEqual(set(worker.preparation_reviews), {'rewards'})
            self.assertFalse(status['battle_ready'])
            self.assertIsNone(runner.manual_state(runtime))
            self.assertEqual(len(control.published), count)

    def test_manual_battle_acceptance_uses_current_population_instead_of_historical_full_team(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            historical_read = reader.read
            reader.read = lambda path: {**historical_read(path), 'fields': {'stage': '2-3', 'deployed': '4/4'}}
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            fresh = worker.history[worker.last_observation['snapshot_id']]
            fresh['fields'] = {'stage': '2-3', 'deployed': '3/4'}
            worker.preparation_scope = ('match', '2-3', 'new-epoch')
            worker.preparation_reviews = {phase: {'completed': True} for phase in runner.coaching.PHASES[:-1]}
            item['phase'] = 'battle_acceptance'
            proof = {'source': 'observed_screen', 'snapshot_id': fresh['snapshot_id'],
                     'evidence_file': fresh['evidence_file'], 'resume_epoch': 'new-epoch'}
            with self.assertRaises(ValueError):
                worker.review_preparation({'proof': proof, 'value': {**item['review'], 'phase': 'battle_acceptance'}}, manual_record=item)
            self.assertFalse(worker.preparation_checklist(fresh)['battle_ready'])
            worker.preparation_reviews = {phase: {'completed': True} for phase in runner.coaching.PHASES[:4]}
            worker.knowledge = {'roles': {}}
            units = [{'name': 'unit-' + str(slot), 'location': 'board', 'row': 'front', 'slot': slot, 'position': '前台'}
                     for slot in range(1, 5)]
            fresh['semantic']['team'] = {'checked': True, 'units': units[:3]}
            item['phase'] = 'lineup_equipment'
            lineup = {**item['review'], 'phase': 'lineup_equipment', 'team': {'checked': True, 'units': units},
                      'gear_checked': True, 'investments_checked': True, 'investments': []}
            with self.assertRaises(ValueError):
                worker.review_preparation({'proof': proof, 'value': lineup}, manual_record=item)
            self.assertEqual(worker.preparation_checklist(fresh)['phase'], 'lineup_equipment')

    def test_old_completed_reviews_do_not_allow_current_underfilled_battle_input(self):
        worker = object.__new__(runner.Worker)
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.epoch = lambda: 'epoch'
        worker.preparation_scope = ('match', '2-3', 'epoch')
        worker.preparation_reviews = {phase: {'completed': True} for phase in runner.coaching.PHASES}
        actual = {'page': 'preparation', 'fields': {'stage': '2-3', 'deployed': '3/4'}, 'rows': [
            {'text': '备战阶段', 'box': [410, 40, 500, 75], 'confidence': .99},
            {'text': '出战', 'box': [1750, 725, 1850, 770], 'confidence': .99}]}
        with self.assertRaises(ValueError):
            worker.guard_preparation_action({'type': 'click_text', 'text': '出战', 'exact': True}, actual)
        # This isolates the population arithmetic gate, not full lineup proof
        # or a supported 12-slot layout. Player level remains a different field.
        actual['fields']['level'] = '9'
        for count in ('7/12', '13/12', '12/13', '18/8', None):
            actual['fields']['deployed'] = count
            with self.subTest(count=count), self.assertRaises(ValueError):
                worker.guard_preparation_action({'type': 'click_text', 'text': '出战', 'exact': True}, actual)
        actual['fields']['deployed'] = '12/12'
        worker.guard_preparation_action({'type': 'click_text', 'text': '出战', 'exact': True}, actual)
        self.assertEqual(actual['fields']['level'], '9')

    def test_two_manual_checkpoint_writers_publish_only_one_pending_phase(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            first_capture, second_capture, release = threading.Event(), threading.Event(), threading.Event()
            read = reader.read
            reads = []
            def gated_read(path):
                reads.append(path)
                if len(reads) == 1:
                    first_capture.set()
                    if not release.wait(2):
                        raise RuntimeError('test capture gate timeout')
                else:
                    second_capture.set()
                return read(path)
            reader.read = gated_read
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(runner.begin_manual_phase, runtime, owner, control, 'manual-one', 'rewards', reader=reader)
                self.assertTrue(first_capture.wait(1))
                second = pool.submit(runner.begin_manual_phase, runtime, owner, control, 'manual-one', 'rewards', reader=reader)
                try:
                    self.assertFalse(second_capture.wait(.1))
                finally:
                    release.set()
                first.result(timeout=2)
                with self.assertRaises(ValueError):
                    second.result(timeout=2)
            phases = [entry.read_json(path) for path in (runtime / 'manual-results').glob('*.json')]
            self.assertEqual(len(phases), 1)
            self.assertEqual(phases[0]['status'], 'pending')
            self.assertEqual(len(control.published), 1)

    def test_finish_manual_phase_does_not_overwrite_changed_checkpoint_status(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            path = runtime / 'manual-results' / (checkpoint['checkpoint_id'] + '.json')
            capture = runner._manual_capture
            def replace_checkpoint(*args):
                result = capture(*args)
                changed = entry.read_json(path)
                changed['status'] = 'changed-by-another-writer'
                control.write_json(path, changed)
                return result
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent', 'findings': 'review'}
            with patch.object(runner, '_manual_capture', side_effect=replace_checkpoint), self.assertRaises(ValueError):
                runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'], [], review, reader=reader)
            self.assertEqual(entry.read_json(path)['status'], 'changed-by-another-writer')

    def test_manual_bridge_incomplete_or_omitted_receipt_stays_pending(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            entry.request(control, 'actions', ['click:1:2', 'click:3:4'], 'partial-click', False)
            path = runtime / 'request-ledger' / (hashlib.sha256(b'partial-click').hexdigest() + '.json')
            receipt = entry.read_json(path)
            receipt['result'].update(ok=False, completed=receipt['request']['actions'][:1])
            control.write_json(path, receipt)
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent', 'findings': 'review'}
            count = len(control.published)
            with self.assertRaises(ValueError):
                runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'], [], review, reader=reader)
            runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'], ['partial-click'], review, reader=reader)
            saved = entry.read_json(runtime / 'manual-results' / (checkpoint['checkpoint_id'] + '.json'))
            self.assertEqual(saved['status'], 'pending')
            self.assertEqual(saved['outcome'], 'unknown')
            self.assertTrue(saved['receipt_states'][0]['unknown_input'])
            self.assertEqual(saved['input_receipts'][0], runner.redact(receipt))
            self.assertIn('after', saved)
            self.assertEqual(len(control.published), count + 1)  # Only the new observe.

    def test_takeover_receipt_reconciliation_waits_exact_id_and_never_republishes(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            entry.request(control, 'actions', ['click:1:2'], 'ongoing', False)
            target = runtime / 'request-ledger' / (hashlib.sha256(b'ongoing').hexdigest() + '.json')
            receipt = entry.read_json(target)
            result = receipt['result']
            receipt['result'] = None
            control.write_json(target, receipt)
            count = len(control.published)
            control.write_json(runtime / 'result.json', {'id': 'unrelated', 'ok': True})
            with self.assertRaises(TimeoutError):
                runner.await_existing_receipt(runtime, control, 'ongoing', 0)
            def settle(_):
                control.write_json(runtime / 'result.json', result)
            with patch.object(runner.time, 'sleep', side_effect=settle):
                reconciled = runner.await_existing_receipt(runtime, control, 'ongoing', .2)
            self.assertEqual(reconciled['result']['id'], 'ongoing')
            self.assertEqual(len(control.published), count)

    def replace_manual_receipt(self, runtime, control, rid, *, remove=(), **updates):
        path = runtime / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
        receipt = entry.read_json(path)
        receipt['result'].update(updates)
        for key in remove:
            receipt['result'].pop(key, None)
        control.write_json(path, receipt)
        control.write_json(runtime / 'result.json', receipt['result'])
        return receipt

    def test_manual_receipt_evidence_distinguishes_no_input_completed_partial_and_unknown(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            entry.request(control, 'actions', ['click:1:2', 'click:3:4'], 'delivery', False)
            raw = runner.await_existing_receipt(runtime, control, 'delivery', 0)
            actions = raw['request']['actions']
            cases = [
                ({'ok': False, 'completed': [], 'input_attempted': False, 'attempted_actions': []}, 'zero_input', False),
                ({'ok': True, 'completed': actions, 'input_attempted': True, 'attempted_actions': actions}, 'completed', False),
                ({'ok': False, 'completed': actions[:1], 'input_attempted': True, 'attempted_actions': actions[:1]}, 'partial', False),
                ({'ok': False, 'completed': [], 'input_attempted': True, 'attempted_actions': actions[:1]}, 'unknown', True),
                ({'ok': False, 'completed': actions[:1], 'input_attempted': True, 'attempted_actions': actions}, 'unknown', True),
                ({'ok': False, 'completed': [], 'input_attempted': None, 'attempted_actions': None}, 'unknown', True),
            ]
            for result, expected, unknown in cases:
                with self.subTest(expected=expected, result=result):
                    state = runner.manual_receipt_state({**raw, 'result': {'id': 'delivery', **result}})
                    self.assertEqual((state['state'], state['unknown_input']), (expected, unknown))
            wait = {**raw, 'request': {**raw['request'], 'actions': [{'type': 'wait', 'args': [1]}], 'handoff': True},
                    'result': {'id': 'delivery', 'ok': False, 'completed': [], 'input_attempted': True, 'attempted_actions': []}}
            self.assertTrue(runner.manual_receipt_state(wait)['unknown_input'])

    def test_manual_completed_input_without_frame_and_control_resume_keep_original_receipts(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            entry.request(control, 'resume', [], 'phase-handoff', True, expected_pause_id=None)
            entry.request(control, 'actions', ['click:1:2'], 'complete-no-frame', False)
            completed = self.replace_manual_receipt(runtime, control, 'complete-no-frame', remove=('observation',),
                                                    observation_error='capture failed')
            entry.request(control, 'actions', ['click:3:4'], 'zero-input-refusal', False)
            zero = self.replace_manual_receipt(runtime, control, 'zero-input-refusal', remove=('observation',),
                ok=False, completed=[], input_attempted=False, attempted_actions=[], error='guard refused before dispatch')
            # Failed passive observations are reconciled without requiring the
            # supervisor to list them as business input or publish them again.
            entry.request(control, 'actions', ['observe'], 'passive-failure', False)
            self.replace_manual_receipt(runtime, control, 'passive-failure', remove=('observation',), ok=False)
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent',
                      'findings': 'Current reward area reviewed', 'all_claimed': True, 'rescanned_after_claim': True}
            item = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'],
                ['phase-handoff', 'complete-no-frame', 'zero-input-refusal'], review, reader=reader)
            self.assertEqual((item['status'], item['outcome']), ('completed', 'success'))
            saved = {receipt['id']: receipt for receipt in item['input_receipts']}
            self.assertEqual(saved['complete-no-frame'], runner.redact(completed))
            self.assertEqual(saved['zero-input-refusal'], runner.redact(zero))
            self.assertTrue(item['image_changed'])
            self.assertNotIn('verified_change', json.dumps(item))
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            count = len(control.published)
            self.assertFalse(worker.consume_manual_results())
            self.current_manual_review(worker, review)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertEqual(len(control.published), count)

    def test_manual_unknown_input_does_not_poison_a_new_current_review(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            entry.request(control, 'actions', ['click:1:2'], 'sent-before-focus-loss', False)
            actual = self.replace_manual_receipt(runtime, control, 'sent-before-focus-loss', ok=False,
                completed=[], input_attempted=True, error='focus failed after dispatch')
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent',
                      'findings': 'Claimed success must not erase unknown input', 'all_claimed': True,
                      'rescanned_after_claim': True}
            item = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'],
                                             ['sent-before-focus-loss'], review, reader=reader)
            self.assertEqual((item['status'], item['outcome']), ('pending', 'unknown'))
            self.assertEqual(item['input_receipts'], [runner.redact(actual)])
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            count = len(control.published)
            self.assertFalse(worker.consume_manual_results())
            self.current_manual_review(worker, {**review, 'findings': 'Independent current request: reward area now clear'})
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertFalse(worker.consume_manual_results())
            self.assertEqual(len(control.published), count)
            self.assertEqual(entry.read_json(runtime / 'manual-results' / (item['checkpoint_id'] + '.json'))['outcome'], 'unknown')
            self.assertEqual(sum(request['id'] == 'sent-before-focus-loss' for request in control.published), 1)

    def test_manual_after_frame_failure_saves_receipts_and_retry_only_observes(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            entry.request(control, 'actions', ['click:1:2'], 'one-completed-input', False)
            raw = runner.await_existing_receipt(runtime, control, 'one-completed-input', 0)
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent',
                      'findings': 'After observation is still required', 'all_claimed': True, 'rescanned_after_claim': True}
            original_request = entry.request
            def bad_after(*args, **kwargs):
                result = original_request(*args, **kwargs)
                Path(result['observation']['snapshot']).write_bytes(b'truncated')
                return result
            with patch.object(entry, 'request', side_effect=bad_after):
                pending = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'],
                                                    ['one-completed-input'], review, reader=reader)
            self.assertEqual((pending['status'], pending['outcome']), ('pending', 'unknown'))
            self.assertEqual(pending['input_receipts'], [runner.redact(raw)])
            self.assertNotIn('after', pending)
            self.assertTrue(pending['observation_error'])
            count = len(control.published)
            finished = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'],
                                                 ['one-completed-input'], review, reader=reader)
            self.assertEqual((finished['status'], finished['outcome']), ('completed', 'success'))
            self.assertEqual(len(control.published), count + 1)
            self.assertEqual(control.published[-1]['actions'], [{'type': 'observe', 'args': []}])
            self.assertEqual(sum(request['id'] == 'one-completed-input' for request in control.published), 1)

    def test_manual_known_partial_preserves_prefix_and_only_current_review_continues(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'rewards', reader=reader)
            entry.request(control, 'actions', ['click:1:2', 'click:3:4'], 'known-prefix', False)
            original = runner.await_existing_receipt(runtime, control, 'known-prefix', 0)
            prefix = original['request']['actions'][:1]
            self.replace_manual_receipt(runtime, control, 'known-prefix', ok=False, completed=prefix,
                                        attempted_actions=prefix, failing_action_index=1)
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': False, 'outcome': 'partial',
                      'reviewer': 'supervising_agent', 'findings': 'Only the first reward input completed'}
            item = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'], ['known-prefix'], review, reader=reader)
            self.assertEqual((item['status'], item['outcome']), ('pending', 'partial'))
            self.assertFalse(item['receipt_states'][0]['unknown_input'])
            self.assertEqual(item['input_receipts'][0]['result']['completed'], prefix)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            count = len(control.published)
            self.current_manual_review(worker, {**review, 'completed': True, 'outcome': 'success',
                'all_claimed': True, 'rescanned_after_claim': True, 'findings': 'Current remaining rewards independently reviewed'})
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'startup_guide')
            self.assertEqual(len(control.published), count)

    def test_manual_no_effect_coin_check_does_not_exempt_a_later_drag_from_fence(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            reward = self.complete_manual_reward(runtime, owner, control, reader)
            checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', 'startup_guide', reader=reader)
            entry.request(control, 'actions', ['drag:1:2:3:4'], 'drag-with-same-coins', False)
            review = {'phase': 'startup_guide', 'stage': '2-3', 'completed': False, 'outcome': 'no_effect',
                'reviewer': 'supervising_agent', 'findings': 'Only the coin field was compared', 'effect_fields': ['coins']}
            item = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'],
                                             ['drag-with-same-coins'], review, reader=reader)
            self.assertEqual((item['status'], item['outcome']), ('pending', 'no_effect'))
            self.assertEqual(item['effect_scope'], 'listed_dynamic_fields_only')
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            worker.preparation_checklist(worker.last_observation)
            with self.assertRaisesRegex(ValueError, '未覆盖的输入变化'):
                worker.verify_manual_mutation_fence(reward)

    def test_manual_missing_hud_defers_once_per_request_and_new_frame_can_revalidate(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            worker.last_observation['fields']['stage'] = None
            self.assertFalse(worker.consume_manual_results())
            self.assertFalse(getattr(worker, 'manual_results_rejected', set()))
            events = len(worker.events)
            self.assertFalse(worker.consume_manual_results())
            self.assertEqual(len(worker.events), events)
            fresh = self.current_manual_frame(worker, control, reader)
            worker.preparation_checklist(fresh)
            source = worker.verified_manual_source(item, fresh)
            self.assertEqual(source['preparation_stage'], '2-3')
            self.current_manual_review(worker, item['review'])
            self.assertEqual(worker.preparation_checklist(fresh)['phase'], 'startup_guide')

    def test_manual_inventory_reconciliation_requires_all_nineteen_unique_native_slots(self):
        from currency_wars_state_reader import native_slots
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            slots = [{**slot, 'status': 'empty', 'name': None, 'star': None} for slot in native_slots()]
            self.manual_image(control, reader, 'gray', semantic={'team': {'slots': slots}})
            digest = hashlib.sha256(control.frame).hexdigest()
            reader.frames[digest]['semantic']['team']['snapshot_id'] = digest
            reader.frames[digest]['semantic']['team']['capacity'] = {
                'snapshot_id': digest, 'overflow_checked': True, 'overflow_count': 0}
            for phase in runner.coaching.PHASES[:3]:
                checkpoint = runner.begin_manual_phase(runtime, owner, control, 'manual-one', phase, reader=reader)
                review = {'phase': phase, 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent',
                          'findings': 'Fixture phase; current proof still required'}
                item = runner.finish_manual_phase(runtime, owner, control, checkpoint['checkpoint_id'], [], review, reader=reader)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            worker.preparation_checklist(worker.last_observation)
            # Prior two phases model already accepted current supervising
            # reviews; this check isolates the real inventory reconciliation.
            proof = {'source': 'observed_screen', 'resume_epoch': worker.epoch(),
                     'snapshot_id': worker.last_observation['snapshot_id'], 'evidence_file': worker.last_observation['evidence_file']}
            worker.preparation_reviews = {phase: {'completed': True, 'proof': proof} for phase in runner.coaching.PHASES[:2]}
            worker.last_observation['semantic']['team']['slots'] = slots[:1]
            count = len(control.published)
            self.assertFalse(worker.consume_manual_results())
            self.assertFalse(getattr(worker, 'manual_results_rejected', set()))
            self.current_manual_frame(worker, control, reader)
            worker.last_observation['semantic']['team']['capacity']['overflow_checked'] = False
            self.assertFalse(worker.consume_manual_results())
            self.assertFalse(getattr(worker, 'manual_results_rejected', set()))
            self.current_manual_frame(worker, control, reader)
            worker.last_observation['semantic']['team']['capacity']['overflow_count'] = 1
            self.assertFalse(worker.consume_manual_results())
            self.assertFalse(getattr(worker, 'manual_results_rejected', set()))
            self.current_manual_frame(worker, control, reader)
            self.assertTrue(worker.consume_manual_results())
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'economy')
            self.assertEqual(len(control.published), count + 3)
            durable = entry.read_json(records / ('manual-' + item['checkpoint_id'] + '.json'))
            self.assertEqual(durable['reconciliation']['fresh_receipt']['id'], worker.last_observation['capture_request_id'])
            self.assertTrue(Path(durable['reconciliation']['fresh_original_png']).exists())
            self.assertEqual(durable['reconciliation']['resume_event']['new_epoch'], 'new-epoch')

    def test_manual_resume_event_rejects_changed_receipt_guard_and_new_pause_wins(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            epoch = entry.read_json(runtime / 'runner-resume-epoch.json')
            target = runtime / 'resume-events' / (hashlib.sha256(b'new-epoch').hexdigest() + '.json')
            original = entry.read_json(target)
            for field, value in [('broker_pause_id', 'wrong-pause'), ('broker_creation_id', '999')]:
                changed = json.loads(json.dumps(original))
                changed['guard'][field] = value
                control.write_json(target, changed)
                with self.subTest(field=field), self.assertRaises(ValueError):
                    runner.verified_resume_event(runtime, owner, control, epoch)
            control.write_json(target, original)
            self.replace_manual_receipt(runtime, control, 'new-epoch', observation_error='receipt changed later')
            with self.assertRaises(ValueError):
                runner.verified_resume_event(runtime, owner, control, epoch)
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            expected = runner.resume_guard_snapshot(runtime, owner, control)
            request = entry.request
            def new_pause(*args, **kwargs):
                result = request(*args, **kwargs)
                control.write_json(runtime / 'runner-manual.json', {'manual_id': 'new-takeover', 'reason': 'new pause'})
                return result
            with patch.object(entry, 'request', side_effect=new_pause), self.assertRaisesRegex(RuntimeError, '新的手动接管优先'):
                runner.explicit_resume(runtime, owner, control, 'late-resume', expected_guard=expected)
            self.assertEqual(entry.read_json(runtime / 'runner-resume-epoch.json')['id'], 'old-epoch')
            self.assertEqual(runner.manual_state(runtime)['manual_id'], 'new-takeover')
            self.assertEqual(len(control.published), 1)
            self.assertEqual(len(control.pauses), 1)

    def test_manual_current_guide_review_requires_reward_rescan_and_preserves_guide(self):
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            item = self.complete_manual_reward(runtime, owner, control, reader)
            worker = self.resumed_manual_worker(runtime, records, owner, control, reader, item)
            self.current_manual_review(worker, item['review'])
            self.manual_image(control, reader, 'blue', page='unknown',
                rows=[{'text': '创业指南', 'confidence': .99, 'box': [1, 1, 100, 40]}])
            self.current_manual_frame(worker, control, reader)
            self.current_manual_review(worker, {'phase': 'startup_guide', 'stage': '2-3', 'completed': True,
                'reviewer': 'supervising_agent', 'findings': 'Current chapter and claimed rewards read',
                'entry_index': 2, 'rewards_claimed': True, 'goals': []})
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertIn('startup_guide', worker.preparation_reviews)
            self.manual_image(control, reader, 'green', page='preparation')
            self.current_manual_frame(worker, control, reader)
            with self.assertRaises(ValueError):
                self.current_manual_review(worker, {**item['review'], 'all_claimed': False})
            self.current_manual_review(worker, item['review'])
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'inventory_cleanup')

    def test_boss_result_only_advances_once_and_does_not_confirm_a_match(self):
        worker = object.__new__(runner.Worker)
        worker.node_guard = lambda observed: True
        worker.wait_page, worker.wait_started, worker.active_match_id = None, 0, 'match'
        worker.panel_index, worker.panel_state = len(runner.PANELS), 'enter'
        worker.node_result_attempted, worker.state = set(), {'statistics': {'matches_confirmed': 0}}
        clicked, asked = [], []
        observed = {'page': 'boss_result', 'fields': {'stage': '3-7'}, 'rows': []}
        worker.click_text = lambda frame, label, reason, **kwargs: clicked.append(label) or observed
        worker.ask = lambda *args: asked.append(args[1])
        worker.tick(observed)
        worker.tick(observed)
        worker.tick({**observed, 'fields': {'stage': '2-7'}})
        self.assertEqual(clicked, ['前往结算'])
        self.assertTrue(all(kind == 'boss_result_unverified' for kind in asked))
        self.assertEqual(worker.state['statistics']['matches_confirmed'], 0)

    def test_live_guide_level_search_requires_mode_and_live_read_proof(self):
        guide = {'applied': True, 'body_read': True, 'mode_label': '超频博弈适用',
                 'body_lines': ['中期：7级搜牌，那刻夏3星后直接9本。']}
        observed = {'fields': {'level': 7, 'mode': '超频博弈'}}
        decision = runner.coaching.guide_phase(guide, observed, {'snapshot_id': 'live-guide'})
        self.assertTrue(decision['reroll_allowed'])
        self.assertEqual(decision['phase'], 'transition')
        self.assertIn('7级搜牌', decision['basis'])
        self.assertFalse(runner.coaching.guide_phase(guide, observed)['reroll_allowed'])
        self.assertFalse(runner.coaching.guide_phase(guide, {'fields': {'level': 8, 'mode': '超频博弈'}},
                                                   {'snapshot_id': 'live-guide'})['reroll_allowed'])
        self.assertFalse(runner.coaching.guide_phase(guide, {'fields': {'level': 7, 'mode': '标准博弈'}},
                                                   {'snapshot_id': 'live-guide'})['reroll_allowed'])

    def test_preparation_reviews_survive_navigation_but_inventory_invalidates_downstream(self):
        worker = object.__new__(runner.Worker)
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.epoch = lambda: 'epoch'
        worker.preparation_scope = ('match', '2-3', 'epoch')
        worker.preparation_reviews = {phase: {'completed': True, 'proof': {'snapshot_id': phase}}
                                      for phase in runner.coaching.PHASES}
        worker.strategy_reads, worker.context = {}, {}
        worker.log = lambda event: None
        self.assertTrue(worker.preparation_checklist({'page': 'guide', 'fields': {}})['economy_allowed'])
        worker.invalidate_preparation('inventory')
        status = worker.preparation_checklist({'page': 'shop', 'fields': {'stage': '2-3'}})
        self.assertEqual(status['phase'], 'inventory_cleanup')
        self.assertFalse(status['economy_allowed'])
        self.assertEqual(worker.preparation_reviews['rewards']['proof']['snapshot_id'], 'rewards')
        self.assertEqual(worker.preparation_reviews['startup_guide']['proof']['snapshot_id'], 'startup_guide')
        worker.epoch = lambda: 'new-epoch'
        self.assertEqual(worker.preparation_checklist({'fields': {'stage': '2-3'}})['phase'], 'rewards')

    def test_unclaimed_rewards_block_spending_and_selling(self):
        worker = object.__new__(runner.Worker)
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.epoch = lambda: 'epoch'
        worker.preparation_scope, worker.preparation_reviews = None, {}
        actual = {'page': 'preparation', 'fields': {'stage': '2-3'}, 'rows': []}
        for action in ({'type': 'buy_shop'}, {'type': 'buy_xp'}, {'type': 'key', 'args': [68]},
                       {'type': 'click_text', 'text': '出售'}, {'type': 'drag', 'args': [1, 1, 2, 2]},
                       {'type': 'drag', 'args': [1, 1, 2, 2], 'text': '攻略'},
                       {'type': 'buy_shop', 'text': '攻略'}, {'type': 'key', 'args': [46], 'text': '攻略'}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                worker.guard_preparation_action(action, actual)

    @contextlib.contextmanager
    def reward_capacity_fixture(self):
        # Reuse the existing real Entry/Worker receipt fixture. Images and
        # semantic values below are contract fixtures, not live vision evidence.
        with self.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            (runtime / 'runner-manual.json').unlink()
            worker = self.frame_worker(runtime, records, owner, control, reader)
            worker.preparation_scope, worker.preparation_reviews = None, {}
            worker.context, worker.history = {'reward_capacity': None}, {}
            worker.state['statistics']['decisions'] = 0
            worker.publish = lambda **updates: worker.state.update(updates)
            status = control.status
            control.status = lambda: {**status(), 'broker_pid': 123, 'broker_creation_time': '456', 'pause_id': None}
            control.pause = lambda reason: {'ok': True, 'paused': True, 'reason': reason}
            events = []
            worker.log = events.append
            control.sold = False
            publish = control.publish_request
            def publish_sale(value):
                if value.get('actions') and value['actions'][0]['type'] == 'drag':
                    from PIL import Image
                    image = io.BytesIO()
                    Image.new('RGB', (1920, 1080), 'white').save(image, format='PNG')
                    control.frame, control.sold = image.getvalue(), True
                publish(value)
            control.publish_request = publish_sale
            def read(path):
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                slots = [{**slot, 'snapshot_id': digest,
                    'status': 'empty' if control.sold and slot['slot'] == 1 else 'occupied',
                    'name': '可售角色', 'star': 1} for slot in runner.native_slots() if slot['location'] == 'bench']
                return {'snapshot_id': digest, 'page': 'preparation', 'fingerprint': '00',
                    'fields': {'stage': '2-3', 'deployed': '8/8'}, 'elapsed_ms': 0.,
                    'rows': [{'text': '备战阶段', 'confidence': .99, 'box': [420, 25, 530, 60]},
                             {'text': '出战', 'confidence': .99, 'box': [1760, 720, 1860, 770]}],
                    'semantic': {'team': {'slots': slots, 'checked': False, 'fully_read': False},
                        'coins': {'value': 51 if control.sold else 50, 'bounds': runner.GOLD_HUD,
                                  'currency_icon_gold_fraction': .5, 'confidence': .99}}}
            reader.read = read
            def ask(observed, kind, reason, choices=None):
                rid = uuid.uuid4().hex
                path = records / (rid + '-original.png')
                path.write_bytes(worker.frame_path.read_bytes())
                request = {'request_id': rid, 'snapshot_id': observed['snapshot_id'], 'kind': kind,
                    'observation': copy.deepcopy(observed), 'match_id': 'match', 'resume_epoch': worker.epoch(),
                    'original_png': str(path), 'evidence_file': str(path), 'created_at': runner.now(),
                    'deadline_at': (runner.datetime.now(runner.timezone.utc) + runner.timedelta(minutes=5)).isoformat()}
                worker.history[observed['snapshot_id']] = {**copy.deepcopy(observed),
                    'evidence_file': str(path), 'observed_at': runner.now(), 'match_id': 'match',
                    'resume_epoch': worker.epoch(), 'preparation_stage': '2-3'}
                worker.state['decision_request'] = request
                return request
            worker.ask = ask
            observed = worker.observe()
            request = ask(observed, 'preparation_strategy', 'fixture')
            inventory = {'bench_capacity': 9, 'slots': [
                {'slot': i, 'status': 'occupied', 'name': '可售角色', 'star': 1} for i in range(1, 10)],
                'overflow_checked': True, 'overflow_count': 0, 'overflow_bounds': [200, 800, 375, 1030]}
            value = {'reviewer': 'supervising_agent', 'stage': '2-3', 'findings': '逐席及溢出检查',
                'inventory': inventory, 'coins': 50,
                'blocked_reward': {'pending': True, 'blocked_by_capacity': True, 'kind': 'unit_reward',
                                   'findings': '角色奖励显示容量阻塞', 'bounds': [1550, 300, 1700, 450]},
                'sale': {'slot': 1, 'name': '可售角色', 'star': 1, 'sale_value': 1,
                         'not_required': True, 'reason': '已核攻略、升星与强制同场需求均不用该张',
                         'control_verified': True, 'control_text': '出售', 'control_bounds': [20, 800, 190, 950]}}
            proof = {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
                     'resume_epoch': worker.epoch(), 'evidence_file': request['evidence_file']}
            action = {'type': 'drag', 'purpose': 'reward_capacity', 'args': [439, 912, 100, 875],
                'expected_page': 'preparation', 'reason': '只为当前被阻塞的角色奖励腾一席', 'guard_texts': ['备战阶段'],
                'target_evidence': {'snapshot_id': request['snapshot_id'], 'control_id': 'reward_capacity_sale',
                                    'bounds': [20, 800, 497, 981]},
                'capacity_review': {'proof': proof, 'value': value}}
            reply = {'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                     'resume_epoch': worker.epoch(), 'actions': [action]}
            yield worker, control, reply, events

    def capacity_post_review(self, worker, reply):
        value = copy.deepcopy(reply['actions'][0]['capacity_review']['value'])
        value['inventory']['slots'][0]['status'] = 'empty'
        value['coins'] = 51
        value['input_request_id'] = worker.pending_reward_capacity()['input_request_id']
        request = worker.state['decision_request']
        return {'proof': {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
                         'resume_epoch': worker.epoch(), 'evidence_file': request['evidence_file']}, 'value': value}

    def test_reward_capacity_single_sale_requires_receipt_and_new_capacity_then_returns_to_rewards(self):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            worker.execute_plan(reply)
            pending = worker.pending_reward_capacity()
            self.assertIsNotNone(pending['input_request_id'])
            self.assertEqual(sum(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published), 1)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            for action in ({'type': 'buy_shop'}, {'type': 'key', 'args': [68]}, reply['actions'][0]):
                with self.assertRaises(ValueError):
                    worker.guard_preparation_action(action, worker.last_observation)
            record = self.capacity_post_review(worker, reply)
            request = worker.state['decision_request']
            worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': worker.epoch(), 'context_update': {'reward_capacity': record},
                'actions': [{'type': 'finish_preparation_review', 'reason': '新帧核钱、腾出的单席和独立溢出'}]})
            self.assertIsNone(worker.pending_reward_capacity())
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertFalse(worker.last_observation['semantic']['team']['fully_read'])
            self.assertFalse(worker.preparation_checklist(worker.last_observation)['economy_allowed'])
            self.assertEqual(sum(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published), 1)

    def test_reward_capacity_rejects_unknown_overflow_nonfull_bench_or_unverified_sale(self):
        mutations = [lambda v: v['inventory'].update(overflow_checked=False),
                     lambda v: v['inventory'].update(overflow_count=1),
                     lambda v: v['inventory']['slots'][0].update(status='empty'),
                     lambda v: v['blocked_reward'].update(blocked_by_capacity=False),
                     lambda v: v['sale'].update(control_verified=False),
                     lambda v: v['sale'].update(name='另一个角色')]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.reward_capacity_fixture() as (worker, control, reply, events):
                mutate(reply['actions'][0]['capacity_review']['value'])
                with self.assertRaises(ValueError):
                    worker.execute_plan(reply)
                self.assertFalse(any(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published))

    def test_reward_capacity_rejects_stale_identity_roi_change_and_economy_disguise(self):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            action = reply['actions'][0]
            for changed in ({'type': 'buy_shop'}, {'type': 'key', 'args': [68]},
                            {'purpose': 'ordinary_cleanup'}):
                with self.assertRaises(ValueError):
                    worker.guard_preparation_action({**action, **changed}, worker.last_observation)
            action['capacity_review']['proof']['resume_epoch'] = 'old'
            with self.assertRaises(ValueError):
                worker.execute_plan(reply)
            action['capacity_review']['proof']['resume_epoch'] = worker.epoch()
            from PIL import Image
            image = Image.open(io.BytesIO(control.frame)).convert('RGB')
            image.putpixel((440, 913), (255, 0, 0))
            changed = io.BytesIO()
            image.save(changed, format='PNG')
            control.frame = changed.getvalue()
            with self.assertRaisesRegex(ValueError, '相关库存'):
                worker.execute_plan(reply)
            self.assertFalse(any(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published))

    def test_reward_capacity_after_input_bad_frame_only_reobserves_never_repeats_sale(self):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            actual_request = entry.request
            def request(controller, kind, tokens, rid, handoff, **kwargs):
                result = actual_request(controller, kind, tokens, rid, handoff, **kwargs)
                if tokens[0].startswith('drag:'):
                    Path(result['observation']['snapshot']).write_bytes(b'truncated')
                return result
            with patch.object(entry, 'request', side_effect=request):
                worker.execute_plan(reply)
            worker.review_reward_capacity(self.capacity_post_review(worker, reply))
            self.assertIsNone(worker.pending_reward_capacity())
            self.assertEqual(sum(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published), 1)

    def test_reward_capacity_missing_receipt_or_wrong_difference_stays_pending_without_input(self):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            worker.execute_plan(reply)
            record = self.capacity_post_review(worker, reply)
            count = len(control.published)
            record['value']['coins'] = 50
            with self.assertRaises(ValueError):
                worker.review_reward_capacity(record)
            record['value']['coins'] = 51
            pending = worker.pending_reward_capacity()
            path = worker.run / 'request-ledger' / (hashlib.sha256(pending['input_request_id'].encode()).hexdigest() + '.json')
            receipt = entry.read_json(path)
            receipt['result']['input_attempted'] = False
            control.write_json(path, receipt)
            with self.assertRaisesRegex(ValueError, '未知'):
                worker.review_reward_capacity(record)
            receipt['result']['completed'] = []
            receipt['result']['input_attempted'] = True
            control.write_json(path, receipt)
            with self.assertRaisesRegex(ValueError, '未知'):
                worker.review_reward_capacity(record)
            self.assertIsNotNone(worker.pending_reward_capacity())
            self.assertEqual(len(control.published), count)

    def test_reward_capacity_new_epoch_or_later_mutation_does_not_copy_completion(self):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            worker.execute_plan(reply)
            record = self.capacity_post_review(worker, reply)
            entry.request(control, 'actions', ['key:27'], 'later-unrecorded-input', False)
            with self.assertRaisesRegex(ValueError, '另有输入'):
                worker.review_reward_capacity(record)
            worker.epoch = lambda: 'new-epoch'
            with self.assertRaises(ValueError):
                worker.review_reward_capacity(record)
            self.assertIsNotNone(worker.pending_reward_capacity())

    @unittest.skipUnless(hasattr(runner, 'verified_resume_event'), 'B003 exact resume-event dependency not installed')
    def test_reward_capacity_exact_b003_resume_reconciles_original_sale_in_new_epoch(self):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            worker.execute_plan(reply)
            control.write_json(worker.run / 'runner-manual.json', {'manual_id': 'manual-one', 'reason': 'handoff'})
            resumed = runner.explicit_resume(worker.run, worker.owner, control, 'new-epoch',
                expected_guard=runner.resume_guard_snapshot(worker.run, worker.owner, control))
            self.assertTrue(resumed['ok'])
            worker.ask(worker.observe(), 'preparation_strategy', '当前epoch补验容量')
            record = self.capacity_post_review(worker, reply)
            record['value']['resume_event_id'] = 'new-epoch'
            worker.review_reward_capacity(record)
            self.assertIsNone(worker.pending_reward_capacity())
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertEqual(sum(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published), 1)

    @unittest.skipUnless(hasattr(runner, 'verified_resume_event'), 'B003 exact manual-result dependency not installed')
    def test_reward_capacity_b003_manual_result_supersedes_unknown_sale_without_claiming_success(self):
        self.check_capacity_manual_resolution(extra_handoff=False)

    @unittest.skipUnless(hasattr(runner, 'verified_resume_event'), 'B003 exact manual-result dependency not installed')
    def test_reward_capacity_b003_manual_result_after_multiple_handoffs_closes_unknown_pending(self):
        self.check_capacity_manual_resolution(extra_handoff=True)

    def check_capacity_manual_resolution(self, *, extra_handoff):
        with self.reward_capacity_fixture() as (worker, control, reply, events):
            worker.execute_plan(reply)
            pending = worker.pending_reward_capacity()
            path = worker.run / 'request-ledger' / (hashlib.sha256(pending['input_request_id'].encode()).hexdigest() + '.json')
            receipt = entry.read_json(path)
            receipt['result'].update(ok=False, completed=[], input_attempted=True)
            control.write_json(path, receipt)
            if extra_handoff:
                control.write_json(worker.run / 'runner-manual.json', {'manual_id': 'intermediate-manual', 'reason': 'first handoff'})
                first = runner.explicit_resume(worker.run, worker.owner, control, 'intermediate-epoch',
                    expected_guard=runner.resume_guard_snapshot(worker.run, worker.owner, control))
                self.assertTrue(first['ok'])
            control.write_json(worker.run / 'runner-manual.json', {'manual_id': 'manual-one', 'reason': 'manual repair'})
            checkpoint = runner.begin_manual_phase(worker.run, worker.owner, control, 'manual-one', 'rewards', reader=worker.perception)
            entry.request(control, 'actions', ['click:1:2'], 'manual-repair', False)
            review = {'phase': 'rewards', 'stage': '2-3', 'completed': True, 'reviewer': 'supervising_agent',
                      'findings': '实际整理库存并复核当前奖励', 'all_claimed': True, 'rescanned_after_claim': True}
            runner.finish_manual_phase(worker.run, worker.owner, control, checkpoint['checkpoint_id'],
                                       ['manual-repair'], review, reader=worker.perception)
            resumed = runner.explicit_resume(worker.run, worker.owner, control, 'new-epoch',
                expected_guard=runner.resume_guard_snapshot(worker.run, worker.owner, control))
            self.assertTrue(resumed['ok'])
            worker.ask(worker.observe(), 'preparation_strategy', '以当前实读替代旧未知腾位')
            record = self.capacity_post_review(worker, reply)
            record['value'].update(resume_event_id='new-epoch', resolution='manual_reconciled',
                                   manual_checkpoint_id=checkpoint['checkpoint_id'])
            worker.review_reward_capacity(record)
            self.assertIsNone(worker.pending_reward_capacity())
            final = entry.read_json(worker.run / 'reward-capacity.json')
            self.assertEqual(final['status'], 'superseded')
            self.assertEqual(final['sale_outcome'], 'unknown')
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
            self.assertEqual(sum(req.get('actions', [{}])[0].get('type') == 'drag' for req in control.published), 1)

    def test_reward_clear_requires_current_full_sweep_not_absent_templates_or_old_review(self):
        observed = {'page': 'preparation', 'snapshot_id': 'fresh', 'semantic': {}}
        record = {'proof': {'source': 'observed_screen', 'snapshot_id': 'fresh'},
                  'value': {'reviewer': 'supervising_agent', 'all_claimed': True, 'rescanned_after_claim': True}}
        self.assertEqual(runner.coaching.reward_status(observed), 'unknown')
        self.assertEqual(runner.coaching.reward_status(observed, record), 'clear')
        self.assertEqual(runner.coaching.reward_status({**observed, 'snapshot_id': 'new'}, record), 'unknown')
        self.assertEqual(runner.coaching.reward_status({**observed, 'page': 'supply'}, record), 'pending')

    def test_selected_investment_requires_both_units_and_board_limits(self):
        investment = [{'name': '飞光·传剑', 'effect': '两人同时在场，每进入新节点比例+4%'}]
        team = {'checked': True, 'units': [{'name': '景元', 'location': 'board', 'row': 'front', 'slot': 1},
                                           {'name': '彦卿', 'location': 'bench', 'row': 'back', 'slot': 1}]}
        decision = runner.coaching.lineup_requirements(investment, team, {})
        self.assertTrue(decision['needs_lineup_plan'])
        team['units'][1]['location'] = 'board'
        self.assertFalse(runner.coaching.lineup_requirements(investment, team, {})['needs_lineup_plan'])
        team['units'][0]['slot'] = 5
        self.assertFalse(runner.coaching.lineup_requirements(investment, team, {})['verified'])
        # Cached selection belongs to an earlier match, so it cannot activate this requirement.
        self.assertFalse(runner.coaching.lineup_requirements([], team,
            {'investments': {'飞光·传剑': {'chosen_this_match': True}}})['needs_lineup_plan'])

    def test_unknown_or_unsupported_unit_position_never_certifies_lineup(self):
        team = {'checked': True, 'units': [{'name': 'unknown-unit', 'location': 'board', 'row': 'front', 'slot': 1}]}
        for position in (None, 'unsupported'):
            team['units'][0]['position'] = position
            result = runner.coaching.lineup_requirements([], team, {'roles': {}})
            self.assertFalse(result['verified'])
            self.assertEqual(result['unknown_positions'], ['unknown-unit'])
        team['units'][0]['position'] = '前后台'
        self.assertTrue(runner.coaching.lineup_requirements([], team, {'roles': {}})['verified'])

    def test_successful_progress_renews_idle_budget_without_extending_hard_deadline(self):
        worker = object.__new__(runner.Worker)
        worker.panel_index, worker.panel_state = 4, 'enter'
        worker.node_key, worker.node_started = ('shop', '2-4'), 100.0
        worker.node_attempts, worker.node_consecutive, worker.node_last_page = 6, 6, 'shop'
        worker.node_progress, worker.node_progress_seen = 1, 0
        worker.deadline = 200.0
        halted = []
        worker.pause_internal = halted.append
        with patch.object(runner.time, 'monotonic', return_value=110.0):
            self.assertTrue(worker.node_guard({'page': 'shop', 'fields': {'stage': '2-4'}}))
        self.assertEqual(worker.node_attempts, 1)
        with patch.object(runner.time, 'monotonic', return_value=169.0):
            self.assertTrue(worker.node_guard({'page': 'shop', 'fields': {'stage': '2-4'}}))
        with patch.object(runner.time, 'monotonic', return_value=170.0):
            self.assertFalse(worker.node_guard({'page': 'shop', 'fields': {'stage': '2-4'}}))
        self.assertEqual(len(halted), 1)
        self.assertEqual(worker.deadline, 200.0)

    def test_supervised_reviews_require_current_request_and_guide_second_entry(self):
        worker = object.__new__(runner.Worker)
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.epoch = lambda: 'epoch'
        worker.last_observation = {'page': 'preparation', 'fields': {'stage': '2-3'}}
        worker.preparation_scope, worker.preparation_reviews, worker.context = None, {}, {'preparation_review': None}
        worker.node_progress, worker.live_mode = 0, None
        worker.log = lambda event: None
        worker.state = {'decision_request': {'snapshot_id': 'reward-frame'}}
        worker.history = {'reward-frame': {'snapshot_id': 'reward-frame', 'match_id': 'match', 'resume_epoch': 'epoch', 'observed_at': runner.now(),
            'evidence_file': 'reward-evidence', 'page': 'preparation', 'preparation_stage': '2-3', 'rows': []}}
        def record(snapshot, evidence, **value):
            return {'value': {'reviewer': 'supervising_agent', 'completed': True, 'stage': '2-3',
                              'findings': '当前原帧实读结果', **value},
                    'proof': {'source': 'observed_screen', 'snapshot_id': snapshot,
                              'evidence_file': evidence, 'resume_epoch': 'epoch'}}
        reward = record('reward-frame', 'reward-evidence', phase='rewards', all_claimed=True, rescanned_after_claim=True)
        worker.review_preparation(reward)
        worker.history['guide-frame'] = {'snapshot_id': 'guide-frame', 'match_id': 'match', 'resume_epoch': 'epoch', 'observed_at': runner.now(),
            'evidence_file': 'guide-evidence', 'page': 'unknown', 'preparation_stage': '2-3',
            'rows': [{'text': '创业指南', 'confidence': .99, 'box': [0, 0, 100, 40]}]}
        worker.state['decision_request']['snapshot_id'] = 'guide-frame'
        guide = record('guide-frame', 'guide-evidence', phase='startup_guide', entry_index=3, rewards_claimed=True, goals=[
            {'id': 'upgrade', 'condition': '累计使用2次特权赋予卡升级进阶装备', 'condition_met': False,
             'reward_claimed': False, 'progress': '1/2'}, '文字任务待读进度',
            {'id': 'bad-proof', 'condition': '复制角色', 'condition_met': False, 'proof': []}])
        with self.assertRaises(ValueError):
            worker.review_preparation(guide)
        guide['value']['entry_index'] = 2
        worker.history['guide-frame']['rows'][0]['confidence'] = .89
        with self.assertRaises(ValueError):
            worker.review_preparation(guide)
        worker.history['guide-frame']['rows'][0]['confidence'] = .99
        worker.history['guide-frame']['rows'].append({'text': '创业指南', 'confidence': .99, 'box': [100, 0, 200, 40]})
        with self.assertRaises(ValueError):
            worker.review_preparation(guide)
        worker.history['guide-frame']['rows'].pop()
        worker.review_preparation(guide)
        self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'rewards')
        self.assertNotIn('rewards', worker.preparation_reviews)
        context, scope = worker.reviewed_task_context({'snapshot_id': 'shop-frame', 'fields': {'stage': '2-3'}})
        self.assertEqual(context['source_snapshot_id'], 'guide-frame')
        proposal = runner.progression_plan({}, {'snapshot_id': 'shop-frame'},
                                          reviewed_task_context=context, observation_scope=scope)
        self.assertEqual(proposal['tasks'][0]['state'], 'pending')
        self.assertFalse(proposal['tasks'][0]['live_progress_verified'])
        self.assertEqual(proposal['tasks'][0]['progress_proof']['snapshot_id'], 'guide-frame')
        self.assertEqual([task['state'] for task in proposal['tasks'][1:]], ['unknown', 'unknown'])
        self.assertTrue(proposal['action_opportunities'])
        self.assertIsNone(worker.reviewed_task_context({'fields': {'stage': '2-4'}})[0])
        with self.assertRaises(ValueError):
            worker.review_preparation(reward)

    def test_cached_guide_requires_current_application_binding_without_rereading_body(self):
        guide = {'title': '塔夏', 'level_plan': '7级搜牌；那刻夏3星后直接9', 'mode_label': '超频博弈'}
        observed = {'fields': {'level': 7, 'mode': '超频博弈'}}
        binding = {'origin': 'cached_guide_with_live_application', 'cache_sha256': 'historical-sha',
                   'application_proof': {'snapshot_id': 'current-application'}, 'game_version_verified': False}
        decision = runner.coaching.guide_phase(guide, observed, binding)
        self.assertTrue(decision['reroll_allowed'])
        self.assertEqual(decision['origin'], 'cached_guide_with_live_application')
        self.assertFalse(decision['proof']['game_version_verified'])
        self.assertFalse(runner.coaching.guide_phase(guide, observed)['reroll_allowed'])

    def test_runner_normalizes_real_perception_level_string_for_live_and_cached_phase(self):
        worker = object.__new__(runner.Worker)
        worker.active_match_id, worker.last_preparation_stage = 'match', '2-3'
        worker.epoch = lambda: 'epoch'
        worker.preparation_scope = ('match', '2-3', 'epoch')
        worker.preparation_reviews = {phase: {'completed': True} for phase in runner.coaching.PHASES[:3]}
        worker.inspection_attempted, worker.context = set(), {}
        worker.live_mode = {'value': '超频博弈', 'match_id': 'match'}
        worker.cached_guide_binding = None
        guide = {'applied': True, 'body_read': True, 'mode_label': '超频博弈适用', 'body_lines': ['中期：7级搜牌']}
        worker.strategy_reads = {'guide': {'value': guide, 'match_id': 'match', 'resume_epoch': 'epoch',
                                          'snapshot_id': 'guide', 'observed_at': runner.now()}}
        worker.knowledge = {'roles': {}, 'source': 'cache', 'sha256': 'sha',
                            'guide': {'title': '塔夏', 'level_plan': '7级搜牌'}}
        worker.strategy_reads['team'] = {'value': {'checked': True, 'units': [
            {'name': 'known-unit', 'location': 'board', 'row': 'front', 'slot': 1, 'position': '前台'}]},
            'match_id': 'match', 'resume_epoch': 'epoch', 'snapshot_id': 'shop', 'observed_at': runner.now()}
        observed = {'page': 'shop', 'snapshot_id': 'shop', 'fields': {'stage': '2-3', 'level': '7'}, 'rows': [],
                    'semantic': {'team': {'checked': False, 'units': []}}}
        decision = worker.preparation_policy(observed)
        self.assertTrue(decision['reroll_allowed'])
        self.assertTrue(decision['lineup_requirements']['verified'])
        self.assertEqual(observed['fields']['level'], '7')
        worker.strategy_reads = {}
        worker.cached_guide_binding = {'match_id': 'match', 'resume_epoch': 'epoch', 'proof': {'snapshot_id': 'application'},
                                       'value': {'mode': '超频博弈'}}
        decision = worker.preparation_policy(observed)
        self.assertTrue(decision['reroll_allowed'])
        self.assertEqual(decision['strategy_phase']['origin'], 'cached_guide_with_live_application')

    def test_inventory_mutations_cannot_reuse_coordinates_within_a_plan(self):
        request = {'request_id': 'request', 'snapshot_id': 'snapshot', 'deadline_at':
                   (runner.datetime.now(runner.timezone.utc) + runner.timedelta(minutes=1)).isoformat()}
        action = {'type': 'drag', 'args': [100, 100, 200, 200], 'expected_page': 'preparation',
                  'guard_texts': ['备战阶段'], 'reason': '实读装备与角色',
                  'target_evidence': {'snapshot_id': 'snapshot', 'bounds': [0, 0, 300, 300]}}
        reply = {'request_id': 'request', 'snapshot_id': 'snapshot', 'resume_epoch': 'epoch', 'actions': [action, action]}
        with self.assertRaises(ValueError):
            runner.validate_plan(reply, request, 'epoch')
        for kind, args in (('drag', [100, 100, 200, 200]), ('buy_shop', []), ('key', [46])):
            disguised = {**action, 'type': kind, 'args': args, 'text': '攻略'}
            self.assertTrue(runner.coaching.inventory_mutation(disguised))
            self.assertEqual(runner.coaching.action_effect(disguised), 'inventory')
            reply['actions'] = [disguised, disguised]
            with self.subTest(disguised=kind), self.assertRaises(ValueError):
                runner.validate_plan(reply, request, 'epoch')
        reply['actions'] = [action]
        self.assertEqual(runner.validate_plan(reply, request, 'epoch'), reply)

    def test_unchanged_frame_cannot_certify_fabricated_settlement_details(self):
        with artifacts.scratch_directory('currency-wars-settlement-proof-check') as runtime:
            facts = {'snapshot_id': 'snapshot', 'mode': '超频博弈', 'outcome': '对局胜利',
                     'rating': 'SSS', 'hp': 100, 'material_reward': 10, 'promotion_points': 20}
            observed = {'snapshot_id': 'snapshot', 'page': 'settlement', 'fingerprint': '00',
                        'fields': {}, 'rows': [], 'semantic': {'settlement': facts}}
            worker = object.__new__(runner.Worker)
            worker.run, worker.epoch = runtime, lambda: 'epoch'
            worker.observe = lambda: observed
            worker.last_observation, worker.context = observed, {}
            request = {'request_id': 'request', 'snapshot_id': 'snapshot', 'resume_epoch': 'epoch',
                       'kind': 'settlement_verify', 'observation': observed, 'evidence_file': 'evidence',
                       'deadline_at': (runner.datetime.now(runner.timezone.utc) + runner.timedelta(minutes=1)).isoformat()}
            worker.state = {'decision_request': request}
            result = {'mode': '超频博弈', 'outcome': '对局胜利', 'rating': 'SSS', 'hp': 100,
                      'materials': 10, 'promotion_points': 20, 'evidence': 'evidence'}
            for key, changed in (('hp', 99), ('rating', 'SS'), ('materials', 11), ('promotion_points', 21)):
                reply = {'request_id': 'request', 'snapshot_id': 'snapshot', 'resume_epoch': 'epoch',
                         'actions': [{'type': 'confirm_match_result', 'reason': '核实结算', 'result': {**result, key: changed}}]}
                with self.subTest(field=key), self.assertRaises(ValueError):
                    worker.execute_plan(reply)

    def test_update_notice_is_not_a_panel_mentioned_in_its_body(self):
        rows = [
            {'text': '货币战争·零和博奔赛季扩充说明V4.4', 'confidence': .95, 'box': [452, 251, 1048, 291]},
            {'text': '详情', 'confidence': .95, 'box': [1300, 260, 1349, 287]},
            {'text': '扩充内容概览', 'confidence': .95, 'box': [456, 737, 613, 765]},
            {'text': '预期收益与羁绊链路也新增了部分内容，竞争对手阵营更加丰富',
             'confidence': .95, 'box': [456, 545, 1440, 584]},
        ]
        self.assertEqual(perception.classify(rows), 'update_notice')
        for index in (0, 1, 2):
            with self.subTest(missing_modal_marker=index):
                self.assertNotEqual(perception.classify(rows[:index] + rows[index+1:]), 'update_notice')
        moved = [dict(row, box=[0, 0, 100, 40]) for row in rows]
        self.assertNotEqual(perception.classify(moved), 'update_notice')

    def test_recognized_update_notice_only_closes_and_rereads(self):
        worker = object.__new__(runner.Worker)
        worker.wait_page, worker.wait_started = None, None
        worker.node_guard = lambda observed: True
        commands = []
        worker.command = lambda *args: commands.append(args)
        worker.tick({'page': 'update_notice', 'fields': {}})
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], ['key:27', 'wait:0.7'])
        self.assertEqual(commands[0][2], 'update_notice')

    def test_manual_wait_does_not_spend_node_budget_or_extend_hard_deadline(self):
        worker = object.__new__(runner.Worker)
        worker.panel_index, worker.panel_state = 0, 'enter'
        worker.node_key, worker.node_started = ('panel', 0, 'enter'), 100.0
        worker.node_attempts, worker.node_consecutive, worker.node_last_page = 1, 1, 'lobby'
        worker.wait_started, worker.deadline = 100.0, 7200.0
        halted = []
        worker.pause_internal = halted.append
        with patch.object(runner.time, 'monotonic', return_value=520.0):
            worker.account_manual_wait(120.0)
            self.assertTrue(worker.node_guard({'page': 'lobby', 'fields': {}}))
        self.assertEqual(worker.wait_started, 500.0)
        self.assertEqual(worker.deadline, 7200.0)
        self.assertEqual(worker.node_attempts, 2)
        with patch.object(runner.time, 'monotonic', return_value=681.0):
            self.assertFalse(worker.node_guard({'page': 'lobby', 'fields': {}}))
        self.assertEqual(len(halted), 1)
        worker.node_attempts = 70
        with patch.object(runner.time, 'monotonic', return_value=682.0):
            worker.account_manual_wait(681.0)
            self.assertFalse(worker.node_guard({'page': 'lobby', 'fields': {}}))
        self.assertEqual(worker.node_attempts, 70)

    def test_new_ipc_directory_grants_its_real_user_inheritable_access(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('currency-wars-runner-acl-test', root=outer) as runtime:
                marker = artifacts.read_marker(runtime, root=outer)
                with self.assertRaises(artifacts.ArtifactError):
                    artifacts.prepare_elevated_ipc_access(runtime, root=outer, expected_run_id='0'*32)
                access = artifacts.prepare_elevated_ipc_access(runtime, root=outer,
                                                               expected_run_id=marker['run_id'])
                self.assertEqual(artifacts.read_marker(runtime, root=outer), marker)
                child = runtime / 'inherited-file.json'
                child.write_text('{"real":"readable"}', encoding='utf8')
                saved_acl = runtime / 'inherited-file.acl'
                checked = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/icacls.exe'),
                                          str(child), '/save', str(saved_acl)],
                                         capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(checked.returncode, 0, checked.stderr.decode('utf8', errors='replace'))
                self.assertRegex(saved_acl.read_text(encoding='utf-16-le'),
                                 r'\(A;[^;]*ID[^;]*;(?:FA|0x1f01ff);;;'+re.escape(access['user_sid'])+r'\)')
                self.assertEqual(json.loads(child.read_text(encoding='utf8')), {'real': 'readable'})
                artifacts.protect_children(runtime, [], root=outer, complete=False)
                with self.assertRaises(artifacts.ArtifactError):
                    artifacts.prepare_elevated_ipc_access(runtime, root=outer, expected_run_id=marker['run_id'])
                artifacts.protect_children(runtime, [], root=outer, complete=True)

    def test_windows_launcher_failures_preserve_utf8_and_ansi_diagnostics(self):
        message = 'Start-Process : 由于用户取消了操作，启动失败。'
        self.assertEqual(runner.decode_launcher_output(message.encode('utf-8-sig')), message)
        self.assertEqual(runner.decode_launcher_output(message.encode('mbcs')), message)
        self.assertEqual(runner.decode_launcher_output(b'{"Id":123}'), '{"Id":123}')
        self.assertIsInstance(runner.decode_launcher_output(b'\xff'), str)

    def test_authenticated_run_does_not_depend_on_elevated_temp_directory(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('elevation-temp-test', root=outer) as runtime:
                marker = artifacts.read_marker(runtime, root=outer)
                owner = {'owner': 'currency-wars-control', 'chat_id': 'inert-elevation-check',
                         'run_token': 'fixture-only', 'artifact_run_id': marker['run_id'],
                         'artifact_chat_id': marker.get('session_hint', {}).get('id')}
                (runtime / 'owner.json').write_text(json.dumps(owner), encoding='utf8')
                with patch('tempfile.tempdir', str(outer / 'different-elevated-temp')):
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.read_marker(runtime)
                    control = SimpleNamespace()
                    found, actual_owner = entry.authenticate(runtime, 'inert-elevation-check', 'fixture-only', control)
                    self.assertEqual(found, runtime)
                    self.assertEqual(actual_owner, owner)
                    self.assertEqual(control.ROOT, str(runtime))
                    for chat, token in [('other-chat', 'fixture-only'), ('inert-elevation-check', 'wrong')]:
                        with self.assertRaises(ValueError):
                            entry.authenticate(runtime, chat, token, SimpleNamespace())

    def test_installed_child_registration_and_verified_exit_cleanup(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('owned-child-test', root=outer) as runtime:
                child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-B', '-c', 'import time; time.sleep(20)'],
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    state, identity = artifacts.process_identity(child.pid)
                    self.assertEqual(state, 'active')
                    artifacts.protect_children(runtime, [{'pid': child.pid, 'process_identity': identity}],
                                               root=outer, complete=False)
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.protect_children(runtime, [], root=outer, complete=True)
                    self.assertTrue(runtime.exists())
                finally:
                    child.terminate()
                    child.wait(timeout=5)
                    artifacts.protect_children(runtime, [], root=outer, complete=True)
            self.assertFalse(runtime.exists())
        self.assertFalse(outer.exists())

    def test_external_marker_change_is_not_adopted_by_registration_or_close(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('owned-marker-test', root=outer) as runtime:
                marker = runtime / artifacts.MARKER
                original = marker.read_bytes()
                value = json.loads(original)
                value['created_at'] += 1
                marker.write_text(json.dumps(value), encoding='utf8')
                try:
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.protect_children(runtime, [], root=outer, complete=True)
                    owned = artifacts._installed_leases[os.path.normcase(str(runtime))]
                    with self.assertRaises(artifacts.ArtifactError):
                        owned.close()
                    self.assertTrue(runtime.exists())
                finally:
                    marker.write_bytes(original)

    def test_incomplete_and_unknown_children_keep_owned_directory(self):
        with artifacts.scratch_directory('currency-wars-compatibility-test') as outer:
            with artifacts.scratch_directory('owned-incomplete-test', root=outer) as runtime:
                owned = artifacts._installed_leases[os.path.normcase(str(runtime))]
                artifacts.protect_children(runtime, [], root=outer, complete=False)
                with self.assertRaises(artifacts.ArtifactError):
                    owned.close()
                native = artifacts.installed
                current = artifacts.process_identity(os.getpid())[1]
                with patch.object(native, 'process_identity', return_value=('unknown', None)):
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.protect_children(runtime, [{'pid': os.getpid(), 'process_identity': current}],
                                                   root=outer, complete=True)
                artifacts.protect_children(runtime, [], root=outer, complete=True)

    def test_venv_redirector_real_child_is_registered_and_wrong_identity_rejected(self):
        command = 'import json,os,time; print(json.dumps({"pid":os.getpid()}),flush=True); time.sleep(20)'
        child = subprocess.Popen([sys.executable, '-B', '-c', command], stdout=subprocess.PIPE,
                                 text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        actual_pid = None
        try:
            actual_pid = json.loads(child.stdout.readline())['pid']
            child_identity = artifacts.process_identity(child.pid)[1]
            actual_identity = artifacts.process_identity(actual_pid)[1]
            self.assertIsNotNone(actual_identity)
            def probe(pid, creation):
                expected = 'windows:' + creation
                return {'state': 'running' if artifacts.process_identity(pid) == ('active', expected) else 'reused'}
            control = SimpleNamespace(process_probe=probe)
            value = {'launch_id': 'this-launch', 'chat_id': 'this-chat', 'runner_pid': actual_pid,
                     'runner_creation_id': actual_identity.split(':', 1)[1]}
            self.assertEqual(runner.registered_worker_identity(value, 'this-launch', 'this-chat',
                             child.pid, child_identity, control), (actual_pid, actual_identity))
            for field, bad in [('launch_id', 'other-launch'), ('chat_id', 'other-chat'),
                               ('runner_creation_id', '1')]:
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    runner.registered_worker_identity({**value, field: bad}, 'this-launch', 'this-chat',
                                                      child.pid, child_identity, control)
            with self.assertRaises(RuntimeError):
                runner.registered_worker_identity(value, 'this-launch', 'this-chat', child.pid,
                                                  'windows:1', control)
        finally:
            if actual_pid is not None and artifacts.process_identity(actual_pid) == ('active', actual_identity):
                runner.psutil.Process(actual_pid).terminate()
            if child.poll() is None:
                child.wait(timeout=5)
            child.stdout.close()

    def test_transfer_keeps_original_redirector_protected(self):
        value = dict(kind='runner', pid=22, process_identity='windows:222')
        lease = object.__new__(guard.Activity)
        lease.project = Path('D:/inert-project')
        lease.path = Path('D:/inert-project/activity/start.json')
        wrapper = {'pid': 11, 'process_identity': 'windows:111'}
        lease.value = {'children': [wrapper], 'children_incomplete': True}
        lease.write = lambda: None
        with patch.object(guard, 'mutation_lock', return_value=contextlib.nullcontext()), \
                patch.object(Path, 'iterdir', return_value=[Path('inert-worker.json')]), \
                patch.object(guard, 'read_object', return_value=value), \
                patch.object(guard, 'identity_state', return_value='active'):
            lease.transfer_to_registered_child(22, 'windows:222', 'runner')
            self.assertEqual(lease.value['children'], [wrapper])
            self.assertFalse(lease.value['children_incomplete'])
            with self.assertRaises(RuntimeError):
                lease.transfer_to_registered_child(22, 'windows:wrong', 'runner')

    def test_secure_desktop_null_foreground_never_allows_input(self):
        source = runner.PROJECT / 'tools/currency_wars_control.py'
        tree = ast.parse(source.read_text(encoding='utf8'))
        function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'status')
        for foreground in (None, 0):
            namespace = {'read_optional': lambda _: None, 'Path': Path, 'ROOT': 'D:/inert-nonexistent',
                         'u': SimpleNamespace(GetForegroundWindow=lambda: foreground),
                         'win': lambda: (123, 456, [0, 0, 1920, 1080]),
                         'BINDING': {'hwnd': 123, 'creation_id': '789'}, 'OWNER': {'chat_id': 'inert'},
                         'PROTOCOL_VERSION': 2, 'gamepad_activity': lambda: {}}
            exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
            result = namespace['status']()
            self.assertFalse(result['ready'])
            self.assertFalse(result['game_foreground'])
            self.assertIsNone(result['broker_pid'])
            self.assertEqual(result['foreground'], 0)


class RuntimeRootTests(unittest.TestCase):
    """No-input root routing; native SID/ACL/TaskScheduler remain Windows gates."""
    def artifact_providers(self):
        """Use the actual installed provider if selected, plus real fallback code."""
        selected = artifacts
        if hasattr(selected, 'installed'):
            yield 'installed', selected
            spec = importlib.util.spec_from_file_location(
                '_currency_wars_standalone_root_test', Path(selected.__file__))
            standalone = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, {'CW_ARTIFACTS_STANDALONE': '1'}):
                spec.loader.exec_module(standalone)
            self.assertFalse(hasattr(standalone, 'installed'))
            yield 'standalone', standalone
        else:
            yield 'standalone', selected

    @contextlib.contextmanager
    def artifact_provider(self, provider):
        # Installed create/protect/close functions use their module globals,
        # not the public aliases exported by currency_wars_artifacts.
        identity = Mock(return_value=('unknown', None))
        with contextlib.ExitStack() as stack:
            for consumer in (sys.modules[__name__], runner, entry, runner.input_bridge):
                stack.enter_context(patch.object(consumer, 'artifacts', provider))
            native = getattr(provider, 'installed', provider)
            stack.enter_context(patch.object(provider, 'process_identity', identity))
            if native is not provider:
                stack.enter_context(patch.object(native, 'process_identity', identity))
            creator = native.create_owned_directory if native is not provider else provider.scratch_directory
            creator = getattr(creator, '__wrapped__', creator)
            self.assertIs(creator.__globals__['process_identity'], identity)
            self.assertIs(native.protect_children.__globals__['process_identity'], identity)
            yield identity

    def test_runner_load_reuses_authenticated_root_for_normal_and_emergency_channels(self):
        for name, provider in self.artifact_providers():
            with self.subTest(provider=name), self.artifact_provider(provider) as identity:
                with tempfile.TemporaryDirectory(prefix='currency-wars-load-root-') as temporary:
                    selected_root = Path(temporary) / 'selected-root'
                    other_root = Path(temporary) / 'unrelated-default'
                    identity.return_value = ('active', 'windows:41')
                    with artifacts.scratch_directory('runner-load-test', root=selected_root) as runtime:
                        marker = artifacts.read_marker(runtime, root=selected_root)
                        self.assertEqual(marker['process_identity'], 'windows:41')
                        common = {'chat_id': 'load-root-fixture', 'run_token': 'fixture-token',
                                  'artifact_chat_id': marker.get('session_hint', {}).get('id')}
                        broker_owner = {**common, 'owner': 'currency-wars-control',
                                        'artifact_run_id': marker['run_id']}
                        runner_owner = {**common, 'owner': 'currency-wars-runner',
                                        'run_id': marker['run_id'], 'runner_pid': marker['pid'],
                                        'runner_creation_id': '41'}
                        for name, value in [('owner.json', broker_owner), ('runner-owner.json', runner_owner),
                                            ('binding.json', {'fixture': True})]:
                            (runtime / name).write_text(json.dumps(value), encoding='utf8')
                        with patch.object(artifacts, 'default_root', return_value=other_root) as default, \
                                patch.object(entry, 'backend', side_effect=lambda: SimpleNamespace()):
                            for emergency in (False, True):
                                with self.subTest(emergency=emergency):
                                    loaded, owner, binding, control = runner.load(
                                        runtime, common['chat_id'], common['run_token'], emergency=emergency)
                                    self.assertEqual(loaded, runtime)
                                    self.assertEqual(owner, runner_owner)
                                    self.assertEqual(control.ROOT, str(runtime))
                                    self.assertEqual(binding, None if emergency else {'fixture': True})
                                    with self.assertRaises(ValueError):
                                        runner.load(runtime, common['chat_id'], 'wrong-token', emergency=emergency)
                            default.assert_not_called()

                identity.assert_any_call(os.getpid())

    @contextlib.contextmanager
    def roots(self, installed=True):
        with tempfile.TemporaryDirectory(prefix='currency-wars-root-test-') as temporary:
            outer = Path(temporary)
            c_root, d_root, installation = (outer / name for name in ('C-temp-root', 'D-fixed-root', 'installed'))
            if installed:
                installation.mkdir()
            with patch.object(runner.input_bridge, 'INSTALL_ROOT', installation), \
                    patch.object(artifacts, 'default_root', return_value=c_root) as default:
                yield outer, c_root, d_root, default

    def test_installed_root_overrides_default_and_revalidates_in_child(self):
        with self.roots() as (_, c_root, d_root, default):
            config = {'runtime_root': str(d_root), 'installation_id': 'a' * 32}
            with patch.object(runner.input_bridge, 'configuration', return_value=config) as verify:
                before_env, before_cache = dict(os.environ), tempfile.tempdir
                selected = runner.input_bridge.runtime_location(entry.PINNED)
                self.assertEqual(selected, {'schema': 1, 'source': 'installed_bridge',
                    'runtime_root': str(d_root), 'installation_id': 'a' * 32})
                self.assertNotEqual(Path(selected['runtime_root']), c_root)
                self.assertEqual(runner.input_bridge.runtime_location(entry.PINNED, inherited=selected), selected)
                self.assertEqual(verify.call_count, 2)
                verify.assert_called_with(entry.PINNED)
                default.assert_not_called()
                self.assertEqual(dict(os.environ), before_env)
                self.assertEqual(tempfile.tempdir, before_cache)

    def test_absent_installation_preserves_parent_standalone_root(self):
        with self.roots(installed=False) as (_, c_root, d_root, default):
            with patch.object(runner.input_bridge, 'configuration') as verify:
                selected = runner.input_bridge.runtime_location(entry.PINNED)
                self.assertEqual(selected, {'schema': 1, 'source': 'standalone',
                    'runtime_root': str(c_root), 'installation_id': None})
                default.return_value = d_root  # Another process may inherit different TEMP.
                self.assertEqual(runner.input_bridge.runtime_location(entry.PINNED, inherited=selected), selected)
                self.assertEqual(default.call_count, 1)
                verify.assert_not_called()

    def test_invalid_or_unreadable_installation_never_falls_back_or_creates_worker_run(self):
        with self.roots() as (_, _, _, default):
            with patch.object(runner.input_bridge, 'configuration', side_effect=runner.input_bridge.BridgeError('invalid installed config')), \
                    patch.object(artifacts, 'scratch_directory') as create, \
                    patch.object(entry, 'backend') as backend:
                with self.assertRaises(runner.input_bridge.BridgeError):
                    runner._worker_cli(SimpleNamespace())
                create.assert_not_called()
                backend.assert_not_called()
                default.assert_not_called()
            with patch.object(Path, 'lstat', side_effect=PermissionError('inaccessible installation')), \
                    patch.object(runner.input_bridge, 'configuration') as verify:
                with self.assertRaises(runner.input_bridge.BridgeError):
                    runner.input_bridge.runtime_location(entry.PINNED)
                default.assert_not_called()
                verify.assert_not_called()

    def test_configuration_uses_unchanged_fixed_task_root_contract(self):
        import currency_wars_bridge_task as task
        config = {'schema': 1, 'installation_id': 'a' * 32, 'user_sid': 'S-1-5-21-1-2-3-1001',
            'runtime_root': task.RUNTIME_ROOT, 'inbox': task.INBOX, 'game_path': r'D:\Game\StarRail.exe',
            'game_sha256': 'A' * 64, 'broker_sha256': entry.PINNED, 'driver_sha256': 'B' * 64,
            'task_name': runner.input_bridge.task_name('S-1-5-21-1-2-3-1001')}
        self.assertIs(task.validate_config(config), config)
        with self.roots() as (_, _, _, default):
            for key, invalid in [('runtime_root', r'C:\Temp\codex-agent-workflow'),
                                 ('inbox', r'D:\Other\inbox'), ('broker_sha256', 'F' * 64)]:
                with self.subTest(field=key), \
                        patch.object(runner.input_bridge, 'InstallationAccess') as access, \
                        patch.object(runner.input_bridge, 'verify_installation') as verify, \
                        patch.object(runner.input_bridge, 'read_object', return_value={**config, key: invalid}):
                    with self.assertRaises(runner.input_bridge.BridgeError):
                        runner.input_bridge.runtime_location(entry.PINNED)
                    verify.assert_called_once_with(access.return_value)
                    access.return_value.close.assert_called_once()
            default.assert_not_called()

    def test_changed_disappeared_and_malformed_launch_binding_are_rejected(self):
        with self.roots() as (_, _, d_root, default):
            location = {'schema': 1, 'source': 'installed_bridge', 'runtime_root': str(d_root), 'installation_id': 'a' * 32}
            with patch.object(runner.input_bridge, 'configuration', return_value={
                    'runtime_root': str(d_root), 'installation_id': 'b' * 32}):
                with self.assertRaises(runner.input_bridge.BridgeError):
                    runner.input_bridge.runtime_location(entry.PINNED, inherited=location)
            runner.input_bridge.INSTALL_ROOT.rmdir()
            with self.assertRaises(runner.input_bridge.BridgeError):
                runner.input_bridge.runtime_location(entry.PINNED, inherited=location)
            for invalid in ({**location, 'schema': True}, {**location, 'source': 'standalone'},
                            {**location, 'installation_id': 123}, {'runtime_root': str(d_root)}):
                with self.subTest(binding=invalid), self.assertRaises(runner.input_bridge.BridgeError):
                    runner.input_bridge.runtime_location(entry.PINNED, inherited=invalid)
            default.assert_not_called()

    def test_public_start_passes_verified_root_and_invalid_config_never_launches(self):
        with self.roots() as (outer, _, d_root, _):
            control = SimpleNamespace(C=SimpleNamespace(set_last_error=lambda _: None, get_last_error=lambda: 0),
                                      k=Mock(CreateMutexW=Mock(return_value=1)))
            args = SimpleNamespace(chat_id='root-fixture', max_seconds=60, max_matches=1, continue_matches=False)
            config = {'runtime_root': str(d_root), 'installation_id': 'a' * 32}
            with patch.object(entry, 'backend', return_value=control), patch.object(runner, 'CURRENT', outer / 'absent.json'), \
                    patch.object(runner.input_bridge, 'configuration', return_value=config) as verify, \
                    patch.object(subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
                    patch.object(subprocess, 'Popen', side_effect=RuntimeError('fixture stops before process launch')) as launch:
                with self.assertRaisesRegex(RuntimeError, 'fixture stops'):
                    runner._start_cli(args, Mock())
                command = launch.call_args.args[0]
                location = json.loads(command[command.index('--runtime-location-json') + 1])
                self.assertEqual(location['runtime_root'], str(d_root))
                self.assertEqual(location['installation_id'], config['installation_id'])
                self.assertEqual(location['source'], 'installed_bridge')
                launch.reset_mock()
                # The GUI's verified launch selection is a parent contract too.
                args.runtime_location_json = json.dumps(location)
                verify.return_value = {**config, 'installation_id': 'b' * 32}
                with self.assertRaises(runner.input_bridge.BridgeError):
                    runner._start_cli(args, Mock())
                launch.assert_not_called()
                verify.side_effect = runner.input_bridge.BridgeError('invalid installed config')
                with self.assertRaises(runner.input_bridge.BridgeError):
                    runner._start_cli(args, Mock())
                launch.assert_not_called()
                self.assertEqual(control.k.ReleaseMutex.call_count, 3)

    def test_unknown_existing_worker_prevents_root_selection_and_second_launch(self):
        with self.roots() as (outer, _, _, _):
            current = outer / 'current.json'
            current.write_text(json.dumps({'chat_id': 'root-fixture', 'runner_pid': 42, 'runner_creation_id': '2'}))
            control = SimpleNamespace(C=SimpleNamespace(set_last_error=lambda _: None, get_last_error=lambda: 0),
                k=Mock(CreateMutexW=Mock(return_value=1)), process_probe=lambda *args: {'state': 'unknown'})
            args = SimpleNamespace(chat_id='root-fixture', max_seconds=60, max_matches=1, continue_matches=False)
            with patch.object(entry, 'backend', return_value=control), patch.object(runner, 'CURRENT', current), \
                    patch.object(runner.input_bridge, 'runtime_location') as select, patch.object(subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, '退出未知'):
                    runner._start_cli(args, Mock())
                select.assert_not_called()
                launch.assert_not_called()

    def test_worker_marker_child_registration_and_cleanup_keep_selected_root(self):
        for name, provider in self.artifact_providers():
            with self.subTest(provider=name), self.artifact_provider(provider) as identity:
                with self.roots() as (outer, c_root, d_root, default):
                    selected = {'schema': 1, 'source': 'installed_bridge', 'runtime_root': str(d_root), 'installation_id': 'a' * 32}
                    args = SimpleNamespace(chat_id='root-fixture', runtime_location_json=json.dumps(selected),
                                           max_seconds=60, max_matches=1, continue_matches=False)
                    seen, child_state, case = [], ['active'], self
                    register, shutdown = runner.Worker.register, runner.Worker.shutdown
                    def process_identity(pid):
                        return ('active', 'windows:1') if pid == os.getpid() else (child_state[0], 'windows:2')
                    control = SimpleNamespace(process_probe=lambda *args: {'state': 'absent'},
                        write_json=lambda path, value: Path(path).write_text(json.dumps(value), encoding='utf8'))
                    class InertWorker:
                        def __init__(self, args, run, control, marker):
                            self.args, self.run, self.c, self.children, self.token = args, run, control, [], 'fixture-token'
                            self.owner = {'run_id': marker['run_id'], 'runner_pid': os.getpid(),
                                          'runner_creation_id': '1', 'launch_id': 'fixture-launch'}
                            self.state = {'state_sequence': 0, 'control_mode': 'completed'}
                            self.broker_launcher, self.bridge_launch = None, None
                            seen.append(run)
                            case.assertEqual(marker['process_identity'], 'windows:1')
                            self.assert_root = marker['root'] == str(d_root) and args.runtime_location == selected
                        def run_loop(self):
                            if not self.assert_root:
                                raise AssertionError('selected root did not reach marker and worker')
                            register(self, 424242, '2')
                            marker = artifacts.read_marker(self.run, root=d_root)
                            if marker['root'] != str(d_root) or not marker['children_incomplete']:
                                raise AssertionError('child registration did not use selected root')
                            child_state[0] = 'unknown'
                            with case.assertRaises(artifacts.ArtifactError):
                                artifacts.protect_children(self.run, [], root=d_root, complete=True)
                            case.assertTrue(self.run.exists())
                        def shutdown(self):
                            child_state[0] = 'dead'
                            shutdown(self)
                        def publish(self, **changes):
                            self.state.update(changes)
                        def log(self, _):
                            pass
                        def finish_profile(self):
                            pass
                    identity.side_effect = process_identity
                    with patch.object(runner.input_bridge, 'configuration', return_value={
                            'runtime_root': str(d_root), 'installation_id': 'a' * 32}), \
                            patch.object(entry, 'backend', return_value=control), patch.object(runner, 'Worker', InertWorker), \
                            patch.object(runner, 'CURRENT', outer / 'current.json'):
                        runner._worker_cli(args)
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(seen[0].parent, d_root)
                    self.assertFalse(seen[0].exists())
                    self.assertFalse(c_root.exists())
                    final = json.loads((outer / 'current.json').read_text())
                    self.assertEqual(final['control_mode'], 'completed')
                    self.assertTrue(final['cleanup']['removed'])
                    default.assert_not_called()

                identity.assert_any_call(os.getpid())
                identity.assert_any_call(424242)


if __name__ == '__main__':
    unittest.main(verbosity=2)
