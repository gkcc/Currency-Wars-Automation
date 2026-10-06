"""B007 lifecycle/receipt contracts through the existing real Worker fixtures."""
import copy
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import currency_wars_runner as runner
import test_currency_wars_economy as economics


class BusinessTests(TestCase):
    def bind(self, worker, control):
        worker.owner.update(runner_pid=41, runner_creation_id='410', launch_id='old-launch',
                            run_token=control.OWNER['run_token'])
        worker.state.update(**runner.redact(worker.owner), run_dir=str(worker.run),
                            journal_file=str(worker.records / 'journal.jsonl'), control_mode='auto',
                            match_id=worker.active_match_id)
        worker.context['business_resume'] = None
        worker.consumed_match_results = set()
        worker.match_result_confirmed = False
        control.process_probe = lambda pid, creation=None: {'pid': pid, 'state': 'absent'}
        control.write_json(worker.run / 'runner-owner.json', worker.owner)
        control.write_json(worker.records / 'owner.json', runner.redact(worker.owner))
        control.write_json(worker.run / 'broker-process.json', {**control.OWNER, 'pid': 123, 'creation_id': '456'})
        worker.initialize_business()
        worker.save_business()
        return worker

    def successor(self, old, control, contract):
        # The real Entry fixture's publisher closes over this runtime path;
        # erase its contents to model disposal. Original run identity remains
        # distinct and cannot authenticate a new-run receipt at the same path.
        shutil.rmtree(old.run)
        old.run.mkdir()
        records = old.records.parent / 'second-lease'
        records.mkdir()
        fresh = copy.copy(old)
        fresh.owner = {**old.owner, 'run_id': 'new-run', 'runner_pid': 42,
                       'runner_creation_id': '420', 'launch_id': 'new-launch', 'run_token': 'new-token',
                       'chat_id': contract['target_chat_id']}
        fresh.records = records
        fresh.state = {**runner.redact(fresh.owner), 'run_dir': str(fresh.run), 'control_mode': 'starting',
            'statistics': {'local_inputs': 0, 'local_observations': 0, 'ocr_ms': 0., 'broker_ms': 0., 'decisions': 0},
            'decision_request': None}
        fresh.context = {key: None for key in old.context}
        fresh.epoch = lambda: 'new-epoch'
        fresh.preparation_reviews, fresh.history, fresh.strategy_reads = {}, {}, {}
        fresh.economy_ledgers, fresh.economy_binding, fresh.economy_read_cache = {}, None, None
        fresh.frame_path, fresh.frame_result, fresh.last_observation = None, None, None
        fresh.preparation_scope, fresh.live_mode, fresh.cached_guide_binding = None, None, None
        fresh.consumed_match_results = set()
        fresh.publish = lambda **changes: fresh.state.update(changes)
        control.OWNER = {**control.OWNER, 'run_token': 'new-token', 'chat_id': fresh.owner['chat_id']}
        control.write_json(records / 'owner.json', runner.redact(fresh.owner))
        control.write_json(fresh.run / 'runner-owner.json', fresh.owner)
        control.write_json(fresh.run / 'runner-resume-epoch.json', {'id': 'new-epoch'})
        fresh.initialize_business(contract)
        fresh.observe()
        fresh.tick(fresh.last_observation)
        return fresh

    def review(self, worker, state=None):
        request = worker.state['decision_request']
        return {'proof': {'source': 'observed_screen', 'snapshot_id': request['snapshot_id'],
            'capture_request_id': request['observation']['capture_request_id'],
            'evidence_file': request['evidence_file'], 'resume_epoch': worker.epoch()},
            'value': {'reviewer': 'supervising_agent', 'match_id': worker.active_match_id,
                'previous_run_id': worker.business_previous_run, 'disposition': 'same_match',
                'previous_chat_id': worker.business_previous_chat, 'current_chat_id': worker.owner['chat_id'],
                'unresolved_requests': sorted(item['origin_run_id'] + ':' + item['request_id']
                                             for item in worker.business_unknown),
                'prior_outcomes_remain_unknown': True, 'remaining_policy': 'fresh_reviews_and_current_balance',
                'continuity_basis': '主管持续跟踪本局任务与前次实操，逐项读取当前追击阵容及所选策略后确认同一局。',
                'current_state': {'page': 'shop', 'stage': '2-3', 'coins': 66, 'level': 7, 'xp': [40, 52],
                    'deployed': '7/7', 'roster': ['飞霄'], 'selected_strategy': '追击', **(state or {})}}}

    def submit(self, worker, record):
        request = worker.state['decision_request']
        worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
            'resume_epoch': worker.epoch(), 'context_update': {'business_resume': record},
            'actions': [{'type': 'finish_preparation_review', 'reason': '仅核业务连续性与未定请求，随后逐阶段重验'}]})

    def test_exact_old_worker_and_broker_are_both_required_without_killing_reused_pid(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            for target in (41, 123):
                for state in ('running', 'unknown'):
                    control.process_probe = lambda pid, creation=None: {'pid': pid, 'state': state if pid == target else 'absent'}
                    with self.subTest(target=target, state=state), self.assertRaisesRegex(RuntimeError, '禁止新租期'):
                        runner.prepare_business_start(worker.state, control)
            control.process_probe = lambda pid, creation=None: {'pid': pid, 'state': 'reused', 'creation_id': 999}
            contract = runner.prepare_business_start(worker.state, control)
            original = runner.entry.read_json(Path(contract['checkpoint']))
            self.assertEqual(original['leases'][-1]['exit_evidence']['broker']['state'], 'reused')
            self.assertEqual(original['leases'][-1]['exit_evidence']['broker']['expected_creation_id'], '456')
            self.assertEqual(control.action_index, 0)
            conflict = {**worker.state, 'broker_identity': {'pid': 999, 'creation_id': '9990'}}
            with self.assertRaisesRegex(ValueError, '创建身份冲突'):
                runner.old_lease_exit(conflict, control)
            shutil.rmtree(worker.run)
            prior = copy.deepcopy(worker.state)
            prior['broker_identity'] = {'pid': 123, 'creation_id': '456'}
            prior['exit_evidence'] = {'broker': {'state': 'not_launched', 'run_id': worker.owner['run_id'],
                'worker_pid': 41, 'worker_creation_id': '410', 'launch_id': 'old-launch',
                'identity_observed': False, 'launch_attempted': False}}
            for broker_state in ('running', 'unknown'):
                probes = []
                def actual_probe(pid, creation=None):
                    probes.append((pid, creation))
                    return {'pid': pid, 'state': broker_state if pid == 123 else 'absent'}
                control.process_probe = actual_probe
                with self.subTest(retained=broker_state), self.assertRaisesRegex(RuntimeError, '旧broker'):
                    runner.old_lease_exit(prior, control)
                self.assertEqual(probes, [(41, '410'), (123, '456')])
            value = runner.entry.read_json(worker.business_path)
            value['leases'][-1].pop('broker_identity')
            control.write_json(worker.business_path, value)
            with self.assertRaisesRegex(RuntimeError, '旧broker'):
                runner.prepare_business_start(prior, control)
            self.assertEqual(probes[-2:], [(41, '410'), (123, '456')])
            control.process_probe = lambda pid, creation=None: {'pid': pid, 'state': 'absent'}
            resumed = runner.prepare_business_start(prior, control)
            committed = runner.entry.read_json(Path(resumed['checkpoint']))
            self.assertEqual(committed['leases'][-1]['broker_identity'], {'pid': 123, 'creation_id': '456'})
            conflict = copy.deepcopy(prior)
            conflict['broker_identity'] = {'pid': 999, 'creation_id': '9990'}
            with self.assertRaisesRegex(ValueError, 'CURRENT与业务'):
                runner.prepare_business_start(conflict, control)

    def test_complete_original_receipt_is_redacted_immutable_and_archived_before_launch(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            receipt = runner.entry.request(control, 'actions', ['key:27'], 'external-original', False)
            discovered = worker.state
            current = runner.PROJECT / 'CURRENT.json'
            control.write_json(current, discovered)
            control.C = SimpleNamespace(set_last_error=lambda value: None, get_last_error=lambda: 0)
            control.k = SimpleNamespace(CreateMutexW=lambda *a: 1, CloseHandle=lambda *a: None, ReleaseMutex=lambda *a: None)
            lease = SimpleNamespace(children=lambda *a, **k: None)
            args = SimpleNamespace(chat_id=worker.owner['chat_id'], max_seconds=60, max_matches=1, continue_matches=False)
            location = {'schema': 1, 'source': 'standalone',
                        'runtime_root': str(worker.run.parent.resolve()), 'installation_id': None}
            class LaunchReached(Exception):
                pass
            def launch(command, **unused):
                self.assertIn('--business-resume-json', command)
                self.assertEqual(json.loads(command[command.index('--runtime-location-json') + 1]), location)
                self.assertTrue((worker.records / 'business-archive.json').exists())
                archived = runner.entry.read_json(worker.records / 'business-receipts' /
                    (hashlib.sha256(b'external-original').hexdigest() + '.json'))
                self.assertEqual(archived['request']['actions'], [{'type': 'key', 'args': [27.]}])
                self.assertEqual(archived['result'], runner.redact(receipt))
                self.assertNotIn('run_token', json.dumps(archived))
                raise LaunchReached()
            with patch.object(runner, 'CURRENT', current), patch.object(runner.entry, 'backend', return_value=control), \
                    patch.object(runner.input_bridge, 'runtime_location', return_value=location) as select_root, \
                    patch.object(runner.subprocess, 'Popen', side_effect=launch), \
                    patch.object(runner.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
                with self.assertRaises(LaunchReached):
                    runner._start_cli(args, lease)
                select_root.assert_called_once_with(runner.entry.PINNED, inherited=None)
            item = runner.await_existing_receipt(worker.run, control, 'external-original', 0)
            changed = copy.deepcopy(item)
            changed['result']['ok'] = False
            with self.assertRaisesRegex(ValueError, '终态收据不可改写'):
                runner.archive_business_receipt(worker.records, worker.owner, changed, control)

    def test_published_f_missing_frame_survives_new_lease_without_replay_or_budget_reset(self):
        with economics.EconomyTests().worker([economics.values(coins=62, xp=[44, 52])]) as (worker, record, control, frames):
            self.bind(worker, control)
            ledger = worker.economy_ledger('2-3')
            ledger['spent']['experience'] = 4
            worker.accept_economy_plan(record)
            with patch.object(worker, 'read_frame', side_effect=runner.entry.ObservationUnavailable('missing post frame')):
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    worker.advance_economy(worker.last_observation)
            original_id = ledger['pending']['request_id']
            contract = runner.prepare_business_start(worker.state, control)
            original_path = worker.records / 'business-receipts' / (hashlib.sha256(original_id.encode()).hexdigest() + '.json')
            original_bytes = original_path.read_bytes()
            fresh = self.successor(worker, control, contract)
            with self.assertRaisesRegex(RuntimeError, '业务归属'):
                fresh.command(['key:70'], '不能重发旧F')
            bad = self.review(fresh, {'coins': 62, 'xp': [44, 52]})
            bad['value']['unresolved_requests'] = []
            with self.assertRaisesRegex(ValueError, '完整未定请求'):
                self.submit(fresh, bad)
            self.submit(fresh, self.review(fresh, {'coins': 62, 'xp': [44, 52]}))
            self.assertEqual(fresh.active_match_id, worker.active_match_id)
            self.assertEqual(fresh.preparation_reviews, {})
            inherited = fresh.economy_ledger('2-3')
            self.assertEqual(inherited['spent']['experience'], 4)
            self.assertIsNone(inherited['pending'])
            self.assertEqual(inherited['prior_run_unknown'][0]['request_id'], original_id)
            self.assertIsNone(inherited['budget'])
            fresh.ask(fresh.last_observation, 'shop_strategy', '承接后独立读数与预算')
            request = fresh.state['decision_request']
            new_plan = copy.deepcopy(record)
            new_plan['proof'].update(snapshot_id=request['snapshot_id'], evidence_file=request['evidence_file'], resume_epoch=fresh.epoch())
            new_plan['value'].update(revision=2, fields=frames[1][2])
            with self.assertRaisesRegex(ValueError, '跨租期原未定支出'):
                fresh.accept_economy_plan(new_plan)
            new_plan['value'].update(prior_run_unknown_requests=[worker.owner['run_id'] + ':' + original_id],
                prior_run_budget_policy='current_balance_after_unknown_inputs')
            fresh.accept_economy_plan(new_plan)
            self.assertFalse(inherited['requires_current_budget'])
            fresh.accept_economy_plan(new_plan)  # Same-revision fresh review is not permanently poisoned.
            self.assertEqual(inherited['spent']['experience'], 4)
            self.assertEqual(original_path.read_bytes(), original_bytes)
            self.assertEqual(control.action_index, 1)
            self.assertEqual(fresh.state['statistics']['local_inputs'], 0)

    def test_current_resume_fence_accepts_animation_but_rejects_intervening_input(self):
        for extra_input in (False, True, 'unknown_observe'):
            with self.subTest(extra_input=extra_input), economics.EconomyTests().worker() as (worker, record, control, frames):
                self.bind(worker, control)
                contract = runner.prepare_business_start(worker.state, control)
                fresh = self.successor(worker, control, contract)
                review = self.review(fresh)
                reader = fresh.perception.read
                def animation(path):
                    value = reader(path)
                    value['fingerprint'] = 'f' * 64
                    return value
                fresh.perception.read = animation
                # Actual PNG bytes also change while all economic ROI facts stay fixed.
                from PIL import Image
                import io
                png = Image.open(io.BytesIO(control.frame))
                png.putpixel((1800, 30), (255, 0, 0))
                changed = io.BytesIO()
                png.save(changed, format='PNG')
                old_digest = hashlib.sha256(control.frame).hexdigest()
                base_read = reader(fresh.frame_path)
                def changed_reader(path):
                    result = copy.deepcopy(base_read)
                    result.update(snapshot_id=hashlib.sha256(Path(path).read_bytes()).hexdigest(), fingerprint='f' * 64)
                    result['shop']['input']['sha256'] = result['snapshot_id']
                    return result
                fresh.perception.read, control.frame = changed_reader, changed.getvalue()
                if extra_input:
                    runner.entry.request(control, 'actions', ['observe'] if extra_input == 'unknown_observe' else ['key:27'], 'intervening', False)
                    if extra_input == 'unknown_observe':
                        path = fresh.run / 'request-ledger' / (hashlib.sha256(b'intervening').hexdigest() + '.json')
                        contradictory = runner.entry.read_json(path)
                        contradictory['result'].update(input_attempted=True, attempted_actions=[{'type': 'key', 'args': [70.]}])
                        control.write_json(path, contradictory)
                    with self.assertRaisesRegex(ValueError, '存在输入/未知'):
                        self.submit(fresh, review)
                    self.assertTrue(fresh.business_needs_review)
                else:
                    self.submit(fresh, review)
                    self.assertNotEqual(fresh.last_observation['snapshot_id'], old_digest)
                    self.assertFalse(fresh.business_needs_review)
                    self.assertEqual(fresh.preparation_reviews, {})

    def test_stale_business_cas_and_unknown_broker_do_not_create_a_new_lease(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            contract = runner.prepare_business_start(worker.state, control)
            with self.assertRaisesRegex(RuntimeError, 'CAS已失效'):
                worker.save_business()
            (worker.run / 'broker-process.json').unlink()
            # No PID guess from an old display 'stopped' string.
            state = copy.deepcopy(worker.state)
            state.pop('business')
            state['exit_evidence'] = {'broker': {'state': 'stopped'}}
            with self.assertRaisesRegex(ValueError, 'broker缺少PID'):
                runner.old_lease_exit(state, control)
            stale = {**contract, 'revision': contract['revision'] - 1}
            with self.assertRaisesRegex(ValueError, 'CAS'):
                worker.initialize_business(stale)

    def test_runtime_completion_does_not_close_business_and_population_cannot_be_omitted(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            for terminal in ('stopped', 'failed', 'completed'):
                worker.state['control_mode'] = terminal
                worker.save_business()
                self.assertEqual(runner.entry.read_json(worker.business_path)['status'], 'active')
                self.assertFalse(worker.state['business']['all_rewards_completed'])
                self.assertTrue(worker.state['business']['awaiting_next_lease'])
            contract = runner.prepare_business_start(worker.state, control)
            fresh = self.successor(worker, control, contract)
            for deployed in (None, '18/8', '0/0'):
                with self.subTest(deployed=deployed), self.assertRaisesRegex(ValueError, '人口独立读数'):
                    self.submit(fresh, self.review(fresh, {'deployed': deployed}))
            self.assertTrue(fresh.business_needs_review)
        # Team capacity is independent of player level. This business review
        # cannot repair native confidence or grant preparation/battle approval.
        for native, declared, accepted in ((None, '7/12', True), (None, '12/12', True),
                (None, '0/12', True), ('7/12', '7/12', True), ('6/12', '7/12', False)):
            with self.subTest(native=native, declared=declared), economics.EconomyTests().worker() as (worker, record, control, frames):
                self.bind(worker, control)
                contract = runner.prepare_business_start(worker.state, control)
                read = worker.perception.read
                def current(path):
                    value = read(path)
                    value['fields']['deployed'] = native
                    return value
                worker.perception.read = current
                fresh = self.successor(worker, control, contract)
                review = self.review(fresh, {'deployed': declared, 'level': 7})
                if accepted:
                    self.submit(fresh, review)
                    self.assertFalse(fresh.business_needs_review)
                    self.assertEqual(fresh.preparation_reviews, {})
                    self.assertIsNone(fresh.economy_binding)
                    self.assertFalse((fresh.run / 'runner-battle-approval.json').exists())
                    self.assertEqual(fresh.last_observation['fields']['deployed'], native)
                else:
                    with self.assertRaisesRegex(ValueError, '人口独立读数'):
                        self.submit(fresh, review)
                    self.assertTrue(fresh.business_needs_review)
                self.assertEqual(control.action_index, 0)

    def test_new_chat_uses_new_token_and_explicit_current_review_not_old_authority(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            old_chat, old_token = worker.owner['chat_id'], worker.owner['run_token']
            contract = runner.prepare_business_start(worker.state, control, 'new-desktop-chat')
            fresh = self.successor(worker, control, contract)
            self.assertEqual(fresh.owner['chat_id'], 'new-desktop-chat')
            self.assertNotEqual(control.OWNER['run_token'], old_token)
            bad = self.review(fresh)
            bad['value']['previous_chat_id'] = 'new-desktop-chat'
            with self.assertRaisesRegex(ValueError, '原业务身份'):
                self.submit(fresh, bad)
            self.submit(fresh, self.review(fresh))
            value = runner.entry.read_json(fresh.business_path)
            self.assertEqual([lease['chat_id'] for lease in value['leases']], [old_chat, 'new-desktop-chat'])
            self.assertEqual(value['chat_id'], old_chat)  # The origin was never rewritten.
            self.assertEqual(control.action_index, 0)

    def test_only_existing_whole_settlement_path_closes_business(self):
        for page, accepted in (('node_result', False), ('settlement', True)):
            with self.subTest(page=page), economics.EconomyTests().worker() as (worker, record, control, frames):
                self.bind(worker, control)
                worker.state['statistics']['matches_confirmed'] = 0
                base = copy.deepcopy(worker.last_observation)
                def reading(path):
                    value = copy.deepcopy(base)
                    value.update(page=page, rows=[{'text': word, 'confidence': .99, 'box': [100, 100, 300, 130]}
                                                  for word in ('标准博弈', '对局胜利')], semantic={})
                    value['snapshot_id'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    return value
                worker.perception.read = reading
                worker.state['decision_request'] = None
                worker.observe()
                worker.ask(worker.last_observation, 'settlement_verify', '仅真实整局结算可关闭')
                request = worker.state['decision_request']
                reply = {'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                    'resume_epoch': worker.epoch(), 'actions': [{'type': 'confirm_match_result', 'reason': '实读整局结算',
                        'result': {'mode': '标准博弈', 'outcome': '对局胜利', 'evidence': request['evidence_file']}}]}
                if accepted:
                    worker.execute_plan(reply)
                    self.assertEqual(runner.entry.read_json(worker.business_path)['status'], 'completed')
                    self.assertEqual(worker.state['statistics']['matches_confirmed'], 1)
                    self.assertFalse(worker.state['business']['all_rewards_completed'])
                else:
                    with self.assertRaisesRegex(ValueError, '整局结算'):
                        worker.execute_plan(reply)
                    self.assertEqual(runner.entry.read_json(worker.business_path)['status'], 'active')

    def test_battle_wait_and_lobby_new_business_intent_do_not_demand_invisible_roster(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            contract = runner.prepare_business_start(worker.state, control)
            fresh = self.successor(worker, control, contract)
            fresh.state['decision_request'] = None
            battle = {**fresh.last_observation, 'page': 'battle'}
            with patch.object(runner.time, 'sleep'):
                fresh.tick(battle)
            self.assertIsNone(fresh.state['decision_request'])
            self.assertEqual(control.action_index, 0)
            base = copy.deepcopy(fresh.last_observation)
            def lobby(path):
                value = copy.deepcopy(base)
                value.update(page='lobby', rows=[{'text': '标准博弈', 'confidence': .99, 'box': [100, 100, 300, 130]}])
                value['snapshot_id'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                return value
            fresh.perception.read = lobby
            fresh.observe()
            fresh.tick(fresh.last_observation)
            review = self.review(fresh)
            review['value'].update(disposition='new_match', current_state={'page': 'lobby', 'visible_labels': ['标准博弈']})
            old_match, old_path = fresh.active_match_id, fresh.business_path
            self.submit(fresh, review)
            self.assertEqual(fresh.active_match_id, old_match)  # No setup has happened yet.
            self.assertEqual(runner.entry.read_json(old_path)['status'], 'unresolved')
            self.assertFalse(fresh.business_needs_review)
            self.assertEqual(control.action_index, 0)

    def test_archive_failure_prevents_cleanup_and_crash_window_recovers_node_spent(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            worker.args, worker.token = SimpleNamespace(chat_id=worker.owner['chat_id']), worker.owner['run_token']
            worker.broker_launcher, worker.bridge_launch, worker.children = None, None, []
            with patch.object(runner.entry, 'stop', return_value={'exit_evidence': {'pid': 123,
                    'expected_creation_id': '456', 'state': 'absent'}}), \
                    patch.object(runner, 'archive_business_run', side_effect=OSError('archive blocked')), \
                    patch.object(runner.artifacts, 'protect_children') as cleanup:
                with self.assertRaisesRegex(OSError, 'archive blocked'):
                    worker.shutdown()
                cleanup.assert_not_called()
                self.assertTrue(worker.run.exists())
            with patch.object(runner.entry, 'stop', return_value={'exit_evidence': {'pid': 123,
                    'expected_creation_id': '456', 'state': 'absent'}}), \
                    patch.object(worker, 'publish', side_effect=OSError('final state blocked')), \
                    patch.object(runner.artifacts, 'protect_children') as cleanup:
                with self.assertRaisesRegex(OSError, 'final state blocked'):
                    worker.shutdown()
                self.assertTrue((worker.records / 'business-archive.json').exists())
                cleanup.assert_not_called()
                self.assertTrue(worker.run.exists())
            ledger = worker.economy_ledger('2-3')
            ledger['spent']['experience'] = 8
            # Simulate a crash after the node's atomic write, before the
            # manifest update; start must read the durable authoritative node.
            with patch.object(worker, 'save_business', side_effect=OSError('between writes')):
                with self.assertRaises(OSError):
                    worker.save_economy_ledger('2-3')
            contract = runner.prepare_business_start(worker.state, control)
            value = runner.entry.read_json(Path(contract['checkpoint']))
            self.assertEqual(value['economy']['2-3']['ledger']['spent']['experience'], 8)

    def test_real_new_match_setup_rotates_business_and_keeps_prior_result_unknown(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            worker.free_lineup_attempted, worker.node_result_attempted, worker.loot_pickup_attempted = set(), set(), set()
            worker.shop_stages, worker.reroll_attempted = set(), set()
            base = copy.deepcopy(worker.last_observation)
            def read(path):
                value = copy.deepcopy(base)
                value.update(page='investment' if control.action_index else 'lobby',
                    rows=[{'text': '标准博弈', 'confidence': .99, 'box': [100, 100, 300, 130]}])
                value['snapshot_id'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                return value
            worker.perception.read = read
            worker.state['decision_request'] = None
            worker.observe()
            worker.ask(worker.last_observation, 'new_match', '实际setup才建立新的整局身份')
            request, old_path, old_match = worker.state['decision_request'], worker.business_path, worker.active_match_id
            worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': worker.epoch(), 'actions': [{'type': 'key', 'args': [13],
                    'expected_page': 'lobby', 'guard_texts': ['标准博弈'], 'reason': '固定fixture进入新局设置页'}]})
            self.assertEqual(control.action_index, 1)
            self.assertNotEqual(worker.active_match_id, old_match)
            self.assertEqual(runner.entry.read_json(old_path)['status'], 'unresolved')
            self.assertEqual(runner.entry.read_json(worker.business_path)['status'], 'active')
            self.assertEqual(runner.pending_business_requests(worker.business), [])

    def test_new_pause_stays_higher_priority_than_current_business_review(self):
        with economics.EconomyTests().worker() as (worker, record, control, frames):
            self.bind(worker, control)
            fresh = self.successor(worker, control, runner.prepare_business_start(worker.state, control))
            review = self.review(fresh)
            fresh.observe()
            control.write_json(fresh.run / 'runner-manual.json', {'manual_id': 'new-takeover', 'reason': 'operator priority'})
            with self.assertRaisesRegex(ValueError, '暂停/停止/epoch优先'):
                fresh.review_business_resume(review)
            self.assertTrue(fresh.business_needs_review)
            self.assertEqual(control.action_index, 0)

    def test_stable_grade_guide_and_reward_pages_can_resume_but_cannot_relabel_a_new_match(self):
        for page in ('settlement_grade', 'guide', 'unit_gear', 'supply', 'reward_overlay', 'node_result',
                     'boss_result', 'plane_intro', 'settlement', 'advantages', 'investment_summary', 'update_notice'):
            with self.subTest(page=page), economics.EconomyTests().worker() as (worker, record, control, frames):
                self.bind(worker, control)
                fresh = self.successor(worker, control, runner.prepare_business_start(worker.state, control))
                base = copy.deepcopy(fresh.last_observation)
                def read(path):
                    value = copy.deepcopy(base)
                    value.update(page=page, rows=[{'text': '当前可见文字', 'confidence': .99, 'box': [100, 100, 300, 130]}])
                    value['snapshot_id'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    return value
                fresh.perception.read = read
                fresh.state['decision_request'] = None
                fresh.observe()
                fresh.tick(fresh.last_observation)
                self.assertEqual(fresh.state['decision_request']['kind'], 'business_resume')
                review = self.review(fresh)
                review['value'].update(disposition='new_match', current_state={'page': page, 'visible_labels': ['当前可见文字']})
                with self.assertRaisesRegex(ValueError, '另开新局须'):
                    self.submit(fresh, review)
                self.assertTrue(fresh.business_needs_review)
                review['value']['disposition'] = 'same_match'
                self.submit(fresh, review)
                self.assertFalse(fresh.business_needs_review)
                self.assertEqual(fresh.preparation_reviews, {})
                self.assertEqual(control.action_index, 0)


if __name__ == '__main__':
    import unittest
    unittest.main()
