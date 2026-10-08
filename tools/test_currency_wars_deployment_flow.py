"""Declared deployment protocols through actual Worker + Entry, no game input.

The PNGs below are generated inert fixtures. Their slot/HUD readings are
explicit declarations, not template-recognition or real deployment evidence.
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from PIL import Image, ImageDraw

import currency_wars_runner as runner
import currency_wars_manual_steps as manual_steps
import currency_wars_manual_stage as manual_stage
import test_currency_wars_deployment_reading as native
import test_local_runtime_compatibility as compatibility
import replay_currency_wars_preparation as preparation


EVIDENCE = {}


def frame(label, *, moved=False, capacity=8, occupied=None, page='preparation', shift=0, anchor_shift=0):
    observed = native.observation(moved=moved, capacity=capacity, occupied=occupied)
    observed['rows'].extend([
        dict(text='备战阶段', raw_text='备战阶段', confidence=.99, box=[420, 25, 530, 55]),
        dict(text='出战', raw_text='出战', confidence=.99, box=[1780, 735, 1850, 770])])
    image = Image.new('RGB', (1920, 1080), (30, 40, 60))
    draw = ImageDraw.Draw(image)
    plan = runner.deployment.spec(native.action(), native.knowledge())
    for index, bounds in enumerate([plan['source']['bounds'], plan['target']['bounds'],
                                    [900, 285, 1050, 325], [900, 555, 1050, 598]]):
        x, y, right, bottom = bounds
        if index == 0:
            x, right = x + shift, right + shift
        elif index > 1:
            x, right = x + anchor_shift, right + anchor_shift
        for inset in (4, 10, 16):
            draw.rectangle((x + inset, y + inset, right - inset, bottom - inset), outline='white', width=2)
        draw.line((x + 4, y + 4, right - 4, bottom - 4), fill='yellow', width=3)
    draw.text((40, 1030), 'DECLARED OFFLINE PROTOCOL ' + label, fill='white')
    out = io.BytesIO()
    image.save(out, format='PNG')
    payload, digest = out.getvalue(), hashlib.sha256(out.getvalue()).hexdigest()
    observed['snapshot_id'] = observed['state_read']['snapshot_id'] = digest
    observed['state_read']['input']['sha256'] = digest
    observed['state_read']['team']['snapshot_id'] = digest
    for slot in observed['state_read']['team']['slots']:
        slot['snapshot_id'] = digest
    observed.update(page=page, elapsed_ms=0., fingerprint=[0] * 64,
        semantic={'team': copy.deepcopy(observed['state_read']['team']),
            'rewards': {'snapshot_id': digest, 'targets': [], 'uncertain': [], 'interaction_required': False}})
    return dict(payload=payload, observation=observed)


@contextlib.contextmanager
def fixture(sequence=('before', 'ready', 'after')):
    choices = dict(before=frame('before'), ready=frame('ready'), after=frame('after', moved=True),
        oldcap=frame('oldcap', capacity=7), unknown=frame('unknown'),
        page=frame('page', page='shop'), shift=frame('shift', shift=20),
        anchor=frame('anchor', moved=True, anchor_shift=20))
    helper = compatibility.RuntimeCompatibilityTests()
    with helper.manual_bridge_fixture() as (runtime, records, owner, control, reader):
        (runtime / 'runner-manual.json').unlink()
        control.write_json(runtime / 'runner-owner.json', owner)
        control.published, control.mutations, control.schedule = [], [], list(sequence)
        control.last_label = control.schedule[0]
        control.before_publish = None
        # Execute the existing pure normalizer/geometry functions only. Do not
        # import the Win32 module or mirror its drag arity in a permissive fake.
        tree = ast.parse(Path(__file__).with_name('currency_wars_control.py').read_bytes())
        pure = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name in ('point', 'validate_actions')]
        namespace = {'math': math}
        exec(compile(ast.Module(body=pure, type_ignores=[]), '<production action normalization>', 'exec'), namespace)
        control.validate_actions = namespace['validate_actions']
        by_sha = {item['observation']['snapshot_id']: item['observation'] for item in choices.values()}
        reader.calls = []
        def read(path, force=False, *, scope='full', reuse_primary=False, deployment_slots=None):
            value = copy.deepcopy(by_sha[hashlib.sha256(Path(path).read_bytes()).hexdigest()])
            reader.calls.append(dict(scope=scope, selected=deployment_slots, snapshot_id=value['snapshot_id']))
            return value
        reader.read = read
        def inert(value):
            if control.before_publish:
                control.before_publish(value)
            actions = value.get('actions', [])
            physical = [a for a in actions if a['type'] not in ('observe', 'wait')]
            if any(a['type'] not in ('observe', 'wait', 'drag') for a in actions):
                raise AssertionError('deployment fixture received an unrelated input')
            control.mutations.extend(copy.deepcopy(physical))
            control.last_label = control.schedule.pop(0) if control.schedule else control.last_label
            item = choices[control.last_label]
            frame_id = uuid.uuid4().hex
            directory = runtime / 'frames' / (hashlib.sha256(value['id'].encode()).hexdigest() + '-' + frame_id)
            directory.mkdir(parents=True)
            for name in ('original.png', 'preview.png'):
                (directory / name).write_bytes(item['payload'])
            digest = item['observation']['snapshot_id']
            completed = copy.deepcopy(actions)
            if getattr(control, 'wrong_wait', False) and physical:
                completed[-1]['args'] = [3.]
            result = dict(id=value['id'], ok=True, completed=completed,
                input_attempted=bool(physical), attempted_actions=copy.deepcopy(physical),
                observation=dict(frame_protocol=1, request_id=value['id'], frame_id=frame_id,
                    captured_at=runner.now(), snapshot=str(directory / 'preview.png'),
                    original=str(directory / 'original.png'), snapshot_sha256=digest, original_sha256=digest,
                    snapshot_size=[1920, 1080], original_size=[1920, 1080]))
            control.published.append(copy.deepcopy(value))
            control.write_json(runtime / 'result.json', result)
        control.publish_request = inert
        worker = helper.frame_worker(runtime, records, owner, control, reader)
        preparation.runner = runner
        preparation.ready_fixture(worker, Path(__file__).resolve().parents[1])
        worker.knowledge = native.knowledge()
        worker.state['control_mode'] = 'auto'
        first = worker.observe()
        worker.preparation_scope = ('match', '3-3', worker.epoch())
        worker.preparation_reviews = {key: dict(completed=True) for key in runner.coaching.PHASES[:4]}
        worker.ask(first, 'preparation_strategy', '声明协议的指定角色部署计划')
        yield SimpleNamespace(worker=worker, control=control, runtime=runtime, records=records,
            owner=owner, reader=reader, choices=choices)


def reply(worker, action=None):
    request = worker.state['decision_request']
    action = {**native.action(), 'reason': '指定风堇前台四；只核部署效果'} if action is None else action
    return dict(request_id=request['request_id'], snapshot_id=request['snapshot_id'],
                resume_epoch=worker.epoch(), actions=[action])


class DeploymentFlowTests(unittest.TestCase):
    def test_capacity_wait_then_one_drag_and_native_result_returns_once_without_completing_gear(self):
        with fixture(('oldcap', 'oldcap', 'ready', 'unknown', 'after')) as bundle:
            worker, control = bundle.worker, bundle.control
            before_decisions = worker.state['statistics']['decisions']
            result = worker.execute_plan(reply(worker))
            self.assertEqual(result['status'], 'verified', worker.state['decision_request']['reason'])
            self.assertFalse(result['effect_pending'])
            self.assertFalse(result['equipment_verified'])
            self.assertFalse(result['battle_ready'])
            self.assertEqual(len(control.mutations), 1)
            self.assertEqual(control.mutations[0]['type'], 'drag')
            self.assertEqual(worker.state['statistics']['decisions'] - before_decisions, 1)
            self.assertEqual(worker.preparation_checklist(worker.last_observation)['phase'], 'lineup_equipment')
            self.assertEqual(set(worker.preparation_reviews), set(runner.coaching.PHASES[:4]))
            self.assertFalse(worker.last_observation['state_read']['team']['checked'])
            self.assertIsNone(worker.last_observation['fields'].get('level'))
            EVIDENCE['delayed_capacity_and_result'] = dict(kind='declared_protocol',
                simulated_drags=1, new_root_requests=1, real_game_inputs=0,
                final_population=worker.last_observation['fields']['deployed'],
                remaining_phase='lineup_equipment', equipment_verified=False, battle_ready=False,
                read_scopes=[v['scope'] for v in bundle.reader.calls])

    def test_completed_without_effect_remains_pending_and_check_only_observes_original_step(self):
        with fixture(('before', 'ready', 'unknown')) as bundle:
            worker, control = bundle.worker, bundle.control
            result = worker.execute_plan(reply(worker))
            self.assertTrue(result['effect_pending'])
            original = runner.entry.read_json(bundle.runtime / 'deployment-step.json')
            self.assertEqual(runner.manual_receipt_state(runner.await_existing_receipt(
                bundle.runtime, control, original['request_id'], 0))['state'], 'completed')
            published = len(control.mutations)
            for action in (dict(type='drag', args=[1, 2, 3, 4], expected_page='preparation'),
                           dict(type='key', args=[32], expected_page='preparation')):
                with self.assertRaises(ValueError):
                    worker.command(['drag:1:2:3:4'], '不能越过原部署待验', 'preparation', action=action)
            worker.execute_plan(reply(worker))
            self.assertEqual(len(control.mutations), published)
            control.schedule = ['after']
            checked = worker.execute_plan(reply(worker, dict(type='check_deployment', step_id=original['step_id'],
                expected_page='preparation', reason='只补原收据后的当前部署结果')))
            self.assertFalse(checked['effect_pending'], worker.state['decision_request']['reason'])
            self.assertEqual(checked['request_id'], original['request_id'])
            self.assertEqual(len(control.mutations), 1)
            EVIDENCE['completed_but_unverified'] = dict(kind='declared_protocol',
                completed_initially_pending=True, simulated_drags=1, input_resent=False,
                original_step_resolved_by='pure_observe_and_native_slot_protocol', equipment_verified=False)

    def test_page_or_local_layout_change_discards_unpublished_coordinates(self):
        for label in ('page', 'shift'):
            with self.subTest(label=label), fixture(('before', label)) as bundle:
                result = bundle.worker.execute_plan(reply(bundle.worker))
                self.assertIsNone(result)
                self.assertEqual(bundle.control.mutations, [])
                self.assertFalse((bundle.runtime / 'deployment-step.json').exists())
                self.assertIn('未闭合', bundle.worker.state['decision_request']['reason'])

    def test_cached_type_economy_reward_or_epoch_conflict_cannot_publish(self):
        for fault in ('role', 'economy', 'reward', 'epoch'):
            with self.subTest(fault=fault), fixture() as bundle:
                worker = bundle.worker
                plan = reply(worker)
                if fault == 'role':
                    plan['actions'][0]['position'] = '后台'
                elif fault == 'economy':
                    worker.economy_ledgers = {('match', '3-3'): {'pending': {'request_id': 'old-economic'}}}
                elif fault == 'reward':
                    worker.preparation_reviews.pop('rewards')
                else:
                    original = worker.guard_command_intent
                    def race(*args, **kwargs):
                        bundle.control.write_json(bundle.runtime / 'runner-resume-epoch.json', {'id': 'raced'})
                        return original(*args, **kwargs)
                    worker.guard_command_intent = race
                worker.execute_plan(plan)
                self.assertEqual(bundle.control.mutations, [])
                self.assertFalse((bundle.runtime / 'deployment-step.json').exists())

    def test_wrong_wait_or_post_page_change_retains_original_pending(self):
        for fault in ('wait', 'page', 'anchor'):
            with self.subTest(fault=fault), fixture(('before', 'ready', 'after' if fault == 'wait' else fault)) as bundle:
                bundle.control.wrong_wait = fault == 'wait'
                result = bundle.worker.execute_plan(reply(bundle.worker))
                self.assertTrue(result['effect_pending'])
                self.assertEqual(len(bundle.control.mutations), 1)
                self.assertEqual(result['status'], 'unverified')

    def test_same_worker_manual_step_keeps_one_return_and_exposes_effect_pending(self):
        with fixture(('before', 'ready', 'unknown')) as bundle:
            worker, control = bundle.worker, bundle.control
            control.write_json(bundle.runtime / 'runner-manual.json', {'manual_id': 'manual-one', 'reason': 'declared'})
            state = runner.entry.read_json(bundle.runtime / 'runner-state.json')
            state.update(preparation_stage='3-3', match_id='match')
            control.write_json(bundle.runtime / 'runner-state.json', state)
            binding, unused = runner._manual_binding(bundle.runtime, bundle.owner, control, 'manual-one')
            directory = bundle.runtime / 'manual-results'
            directory.mkdir(exist_ok=True)
            for phase in runner.coaching.PHASES[:4]:
                # Declared already-closed starting phases, not an execution of
                # the frozen reward/economy/manual review test suites.
                control.write_json(directory / (phase + '.json'),
                    dict(binding=binding, phase=phase, status='completed'))
            checkpoint = runner.begin_manual_phase(bundle.runtime, bundle.owner, control, 'manual-one',
                'lineup_equipment', reader=bundle.reader)
            # Checkpoint is pure observe; keep a stable ready source before the drag.
            control.schedule = ['ready', 'unknown']
            step_id = uuid.uuid4().hex
            before_decisions = worker.state['statistics']['decisions']
            manual_steps.submit(bundle.runtime, bundle.owner, control, manual_id='manual-one',
                step_id=step_id, operation='reviewed_plan', checkpoint_id=checkpoint['checkpoint_id'],
                reply=reply(worker), wait_seconds=0)
            self.assertTrue(manual_steps.process(worker))
            item = runner.entry.read_json(bundle.runtime / 'manual-steps' / (step_id + '.json'))
            self.assertEqual(item['status'], 'returned', item.get('error'))
            self.assertTrue(item['result']['receipt_delivery_verified'])
            self.assertTrue(item['result']['deployment_effect_pending'])
            self.assertTrue(manual_steps.summary(item)['pending'])
            self.assertEqual(worker.state['statistics']['decisions'] - before_decisions, 1)
            self.assertEqual(len(control.mutations), 1)
            count = len(control.published)
            self.assertFalse(manual_steps.process(worker))
            self.assertEqual(len(control.published), count)
            self.assertIsNotNone(runner.manual_state(bundle.runtime))
            EVIDENCE['manual_completed_effect_pending'] = dict(kind='declared_protocol',
                same_worker=True, input_receipts=1, new_root_requests=1,
                receipt_delivery_verified=True, effect_pending=True, epoch_unchanged=True,
                input_resent=False, real_game_inputs=0)

    def test_late_receipt_is_folded_before_observe_and_changed_receipt_is_not_laundered(self):
        with fixture(('before', 'ready', 'unknown')) as bundle:
            worker, control = bundle.worker, bundle.control
            worker.execute_plan(reply(worker))
            step = runner.entry.read_json(bundle.runtime / 'deployment-step.json')
            path = bundle.runtime / 'request-ledger' / (hashlib.sha256(step['request_id'].encode()).hexdigest() + '.json')
            original = runner.entry.read_json(path)
            control.write_json(path, {**original, 'result': None})
            control.write_json(bundle.runtime / 'result.json', original['result'])
            control.schedule = ['after']
            def check_folded(value):
                self.assertEqual(runner.entry.read_json(path), original)
            control.before_publish = check_folded
            checked = worker.execute_plan(reply(worker, dict(type='check_deployment', step_id=step['step_id'],
                expected_page='preparation', reason='对账原晚到回执后纯观察')))
            self.assertFalse(checked['effect_pending'], worker.state['decision_request']['reason'])
            self.assertEqual(len(control.mutations), 1)
        with fixture(('before', 'ready', 'unknown')) as bundle:
            worker, control = bundle.worker, bundle.control
            worker.execute_plan(reply(worker))
            step = runner.entry.read_json(bundle.runtime / 'deployment-step.json')
            path = bundle.runtime / 'request-ledger' / (hashlib.sha256(step['request_id'].encode()).hexdigest() + '.json')
            changed = runner.entry.read_json(path)
            changed['result']['unexpected_replacement'] = True
            control.write_json(path, changed)
            count = len(control.published)
            for unused in range(2):
                worker.execute_plan(reply(worker, dict(type='check_deployment', step_id=step['step_id'],
                    expected_page='preparation', reason='拒绝改写原终态')))
                self.assertEqual(runner.entry.read_json(bundle.runtime / 'deployment-step.json')['receipt'], step['receipt'])
                self.assertEqual(len(control.published), count)

    def test_failed_intent_write_is_a_refusal_and_corrupt_before_frame_never_closes(self):
        with fixture() as bundle:
            write = bundle.control.write_json
            failed = False
            def broken_pointer(path, value):
                nonlocal failed
                if Path(path).name == 'deployment-step.json' and not failed:
                    failed = True
                    raise OSError('declared one-time metadata write failure')
                return write(path, value)
            bundle.control.write_json = broken_pointer
            result = bundle.worker.execute_plan(reply(bundle.worker))
            self.assertIsNone(result)
            self.assertEqual(bundle.control.mutations, [])
            self.assertEqual(list(bundle.records.glob('deployment-step-*.json')), [])
            refusals = list(bundle.records.glob('deployment-refusal-*.json'))
            self.assertEqual(len(refusals), 1)
            self.assertFalse(runner.entry.read_json(refusals[0])['publication_attempted'])
        with fixture(('before', 'ready', 'unknown')) as bundle:
            worker, control = bundle.worker, bundle.control
            worker.execute_plan(reply(worker))
            step = runner.entry.read_json(bundle.runtime / 'deployment-step.json')
            Path(step['before_png']).write_bytes(b'declared corruption')
            control.schedule = ['after']
            result = worker.execute_plan(reply(worker, dict(type='check_deployment', step_id=step['step_id'],
                expected_page='preparation', reason='坏原帧不能由后读覆盖')))
            self.assertTrue(result['effect_pending'])
            self.assertIn('未闭合', worker.state['decision_request']['reason'])
            self.assertEqual(len(control.mutations), 1)


if __name__ == '__main__':
    unittest.main()
