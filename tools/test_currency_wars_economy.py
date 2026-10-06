"""Focused economy contracts and the real Worker/Entry path; no game inputs."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from PIL import Image, ImageDraw

import currency_wars_economy as economy
import currency_wars_runner as runner
import currency_wars_shop_reader as shop_reader
import test_local_runtime_compatibility as compatibility


def values(**changes):
    return {'coins': 66, 'level': 7, 'xp': [40, 52], 'xp_cost': 4,
            'xp_gain': 4, 'free_refreshes': 0, 'refresh_cost': 2, **changes}


def plan(**changes):
    result = {'schema': economy.SCHEMA, 'reviewer': 'supervising_agent', 'revision': 1,
        'stage': '2-3', 'mode': '标准博弈', 'reason': '先核当前商店缺口与免费刷新，搜牌0后升8级',
        'targets': [], 'shop_reviewed': True, 'purchase_only_targets': True,
        'budget': {'purchase': 0, 'refresh': 0, 'experience': 12},
        'paid_search': {'max_refreshes': 0, 'purchase_reserve': 0, 'reason': '当前无付费搜牌目标',
            'targets': [], 'stop_conditions': ['budget_exhausted', 'max_refreshes', 'purchase_reserve', 'target_acquired']},
        'experience': {'target_level': 8, 'reason': '增加一个实读需要的上场位置', 'critical': False},
        'reserve': {'coins': 50, 'critical_allowance': 0, 'reason': '标准常规满血连胜储备'}}
    result.update(changes)
    return result


def shop(snapshot, card=None):
    slots = [{'slot': slot, 'status': 'empty'} for slot in range(1, 6)]
    if card:
        name, cost = card
        slots[0] = {'slot': 1, 'status': 'recognized', 'name': name, 'cost': cost,
            'bounds': [400, 300, 180, 400], 'position': [490, 500],
            'evidence': {'name': {'method': 'ocr', 'confidence': .99, 'bounds': [410, 310, 570, 345]},
                'cost': {'method': 'digit_ocr', 'confidence': .99, 'ocr_bounds': [410, 650, 440, 680],
                         'raw_ocr': [{'raw': str(cost), 'confidence': .99}]}}}
    return {'schema': 'currency-wars-shop-observation/v1', 'ok': True, 'status': 'ok',
            'input': {'sha256': snapshot, 'size': [1920, 1080], 'format': 'PNG'},
            'page': {'reliable_open_shop': True, 'geometry': {'frame_scores': [.99] * 5, 'overlays': []}}, 'slots': slots}


def numeric_frame(state):
    image = Image.new('RGB', (1920, 1080), 'black')
    draw = ImageDraw.Draw(image)
    fields = {}
    for index, name in enumerate(economy.FIELDS):
        bounds = runner.GOLD_HUD if name == 'coins' else [50 + index * 110, 900, 150 + index * 110, 950]
        raw = '/'.join(map(str, state[name])) if name == 'xp' else str(state[name])
        draw.text((bounds[0] + 3, bounds[1] + 4), raw, fill='white')
        fields[name] = {'value': state[name], 'bounds': bounds}
    output = io.BytesIO()
    image.save(output, format='PNG')
    return image, output.getvalue(), fields


class EconomyTests(TestCase):
    def test_f_is_page_specific_and_old_e_is_rejected_by_real_plan_guard(self):
        for page in ('preparation', 'shop'):
            action = {'type': 'key', 'args': [70], 'expected_page': page, 'guard_texts': ['购买经验'], 'reason': '实际F'}
            self.assertEqual(economy.economic_action(action, page), 'experience')
            self.assertEqual(runner.coaching.action_effect(action), 'economy')
            request = {'request_id': 'r', 'snapshot_id': 's', 'observation': {'page': page},
                       'deadline_at': (runner.datetime.now(runner.timezone.utc) + runner.timedelta(minutes=1)).isoformat()}
            reply = {'request_id': 'r', 'snapshot_id': 's', 'resume_epoch': 'e', 'actions': [action]}
            runner.validate_plan(reply, request, 'e')
            reply['actions'] = [{**action, 'args': [69]}]
            with self.assertRaisesRegex(ValueError, 'E69'):
                runner.validate_plan(reply, request, 'e')
        action = {'type': 'key', 'args': [70], 'expected_page': 'world_entry'}
        self.assertIsNone(economy.economic_action(action, 'world_entry'))
        self.assertEqual(runner.coaching.action_effect(action), 'navigation')

    def test_dependencies_never_put_xp_before_gaps_free_or_unresolved_paid(self):
        p, ledger = plan(), economy.new_ledger()
        observation = {'values': values(), 'unknown': []}
        self.assertEqual(economy.dependencies(p, observation, ledger, [{'slot': 1}], shop_complete=True)['phase'], 'purchase')
        for free in (None, 2):
            observation['values']['free_refreshes'] = free
            self.assertFalse(economy.dependencies(p, observation, ledger, [], shop_complete=True)['experience_allowed'])
        observation['values']['free_refreshes'] = 0
        self.assertTrue(economy.dependencies(p, observation, ledger, [], shop_complete=True)['experience_allowed'])
        p['budget']['refresh'] = 2
        p['budget']['purchase'] = 5
        p['targets'] = [{'name': '飞霄', 'copies': 1, 'reason': '明确升星缺口', 'critical': False}]
        p['paid_search'].update(max_refreshes=1, targets=['飞霄'], purchase_reserve=5)
        self.assertFalse(economy.dependencies(p, observation, ledger, [], shop_complete=True,
                         guide_permission={'phase': None, 'allowed': False})['experience_allowed'])
        self.assertEqual(economy.dependencies(p, observation, ledger, [], shop_complete=True,
                         guide_permission={'phase': 'transition', 'allowed': True})['phase'], 'paid_search')
        stopped = economy.dependencies(p, observation, ledger, [], shop_complete=True,
                    guide_permission={'phase': 'early', 'allowed': False})
        self.assertTrue(stopped['experience_allowed'])
        self.assertIn('攻略', stopped['paid_search']['reason'])
        ledger['spent']['refresh'] = 2
        self.assertTrue(economy.dependencies(p, observation, ledger, [], shop_complete=True,
            guide_permission={'phase': None, 'allowed': False})['experience_allowed'])
        observation['values']['refresh_cost'] = None
        for stopped_by in ('budget', 'count', 'target', 'guide', 'purchase_reserve'):
            stopped_ledger = economy.new_ledger()
            permission = {'phase': None, 'allowed': False}
            if stopped_by == 'budget':
                stopped_ledger['spent']['refresh'] = 2
            elif stopped_by == 'count':
                stopped_ledger['paid_refreshes'] = 1
            elif stopped_by == 'target':
                stopped_ledger['purchased']['飞霄'] = 1
            elif stopped_by == 'guide':
                permission['phase'] = 'early'
            else:
                stopped_ledger['spent']['purchase'] = 1
            with self.subTest(stopped_by=stopped_by):
                self.assertTrue(economy.dependencies(p, observation, stopped_ledger, [], shop_complete=True,
                                                    guide_permission=permission)['experience_allowed'])

    def test_budget_is_shared_reserve_is_mode_specific_and_spent_never_resets(self):
        p, ledger, observed = plan(), economy.new_ledger(), {'values': values()}
        economy.validate_budget(p, observed, ledger)
        p['budget']['purchase'] = 5
        with self.assertRaisesRegex(ValueError, '重复占用'):
            economy.validate_budget(p, observed, ledger)
        p['budget']['purchase'] = 0
        ledger['spent']['experience'], ledger['budget'], ledger['revision'] = 8, dict(p['budget']), 1
        with self.assertRaises(ValueError):
            economy.validate_budget({**p, 'budget': {'purchase': 0, 'refresh': 0, 'experience': 0}}, observed, ledger)
        p = plan(mode='超频博弈')
        with self.assertRaisesRegex(ValueError, '超频无利息'):
            economy.validate_budget(p, observed, economy.new_ledger())
        p['reserve']['coins'] = 0
        economy.validate_budget(p, observed, economy.new_ledger())
        p = plan(stage='3-7')
        p['reserve']['coins'] = 0
        with self.assertRaisesRegex(ValueError, '不能用节点字符串'):
            economy.validate_budget(p, observed, economy.new_ledger())
        p = plan()
        with self.assertRaisesRegex(ValueError, '利息储备'):
            economy.require_spending('experience', 4, p, {'values': values(coins=52)}, economy.new_ledger())
        p['reserve']['critical_allowance'] = 2
        self.assertEqual(economy.require_spending('experience', 4, p, {'values': values(coins=52)},
                                               economy.new_ledger(), critical=True), 2)
        p['reserve']['critical_allowance'], p['budget']['experience'] = 1000, 1000
        with self.assertRaisesRegex(ValueError, '可用资金'):
            economy.validate_budget(p, observed, economy.new_ledger())

    def test_numeric_read_keeps_provenance_and_low_confidence_unknown(self):
        original, unused, fields = numeric_frame(values())
        binding = economy.bind_fields(fields, original, gold_bounds=runner.GOLD_HUD)
        first = economy.observe_fields(binding, original)
        self.assertEqual(first['values'], values())
        self.assertEqual(first['evidence']['coins']['source'], 'supervisor_read_with_identical_current_roi')
        changed, unused, unused_fields = numeric_frame(values(coins=62))
        weak = economy.observe_fields(binding, changed, lambda *a, **k: ([['62', .89]], None))
        self.assertIn('coins', weak['unknown'])
        fresh = economy.observe_fields(binding, changed, lambda *a, **k: ([['62', .99]], None))
        self.assertEqual(fresh['values']['coins'], 62)
        self.assertEqual(fresh['evidence']['coins']['source'], 'current_numeric_crop_ocr')
        self.assertEqual(binding['coins']['value'], 66)

    def test_actual_effects_zero_partial_unknown_and_single_level_transition(self):
        before = {'values': values(), 'shop': [], 'shop_complete': True}
        for after, outcome in ((values(), 'zero'), (values(coins=62, xp=[42, 52]), 'partial'),
                               (values(coins=62, xp=[44, 52]), 'success')):
            self.assertEqual(economy.classify_effect('experience', before, {'values': after}, expected_cost=4)['outcome'], outcome)
        self.assertEqual(economy.classify_effect('experience', before, {'values': {}}, expected_cost=4)['outcome'], 'unknown')
        result = economy.classify_effect('experience', {'values': values(coins=58, xp=[48, 52])},
            {'values': values(coins=54, level=8, xp=[0, 72])}, expected_cost=4, target_level=8)
        self.assertTrue(result['target_reached'])
        self.assertEqual(result['outcome'], 'success')
        result = economy.classify_effect('refresh', {'values': values(free_refreshes=1), 'shop': []},
            {'values': values(), 'shop': [], 'shop_complete': True}, expected_cost=0)
        self.assertEqual(result['outcome'], 'success')
        # A paid reroll can draw identical cards; its exact actual charge counts.
        self.assertEqual(economy.classify_effect('refresh', before,
            {'values': values(coins=64), 'shop': [], 'shop_complete': True}, expected_cost=2)['outcome'], 'success')

    @contextlib.contextmanager
    def worker(self, transitions=None, initial=None):
        fixture = compatibility.RuntimeCompatibilityTests()
        with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
            (runtime / 'runner-manual.json').unlink()
            states = [initial or values()] + (transitions or [])
            rendered = [numeric_frame(state) for state in states]
            by_digest = {hashlib.sha256(payload).hexdigest(): state for state, (image, payload, fields) in zip(states, rendered)}
            crop_text = {}
            for state, (image, payload, fields) in zip(states, rendered):
                for name, item in fields.items():
                    raw = '/'.join(map(str, state[name])) if name == 'xp' else str(state[name])
                    crop_text[hashlib.sha256(image.crop(item['bounds']).tobytes()).hexdigest()] = raw
            reader.engine = lambda crop, **unused: ([[crop_text[hashlib.sha256(crop.tobytes()).hexdigest()], .99]], None)
            def read(path):
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                state = by_digest[digest]
                return {'snapshot_id': digest, 'page': 'shop', 'fields': {'stage': '2-3', 'deployed': '7/7', 'level': str(state['level'])},
                    'rows': [{'text': '刷新', 'confidence': .99, 'box': [200, 780, 260, 810]}],
                    'semantic': {'coins': {'value': state['coins'], 'bounds': runner.GOLD_HUD, 'currency_icon_gold_fraction': .8}},
                    'shop': shop(digest), 'elapsed_ms': 0., 'fingerprint': '0' * 64}
            reader.read = read
            original_publish = control.publish_request
            control.frame = rendered[0][1]
            control.action_index = 0
            control.validate_actions = lambda actions: [{'type': item['type'], 'args': [float(value) for value in item.get('args', [])]} for item in actions]
            def publish(value):
                if any(action['type'] == 'key' for action in value.get('actions', [])):
                    control.action_index += 1
                    if control.action_index < len(rendered):
                        control.frame = rendered[control.action_index][1]
                return original_publish(value)
            control.publish_request = publish
            worker = fixture.frame_worker(runtime, records, owner, control, reader)
            worker.epoch = lambda: 'old-epoch'
            worker.context = {name: None for name in ('coins', 'team', 'gear', 'xp', 'bonds', 'economy_plan', 'preparation_review')}
            worker.preparation_scope = ('match', '2-3', 'old-epoch')
            worker.preparation_reviews = {phase: {'completed': True} for phase in runner.coaching.PHASES[:3]}
            worker.live_mode = {'match_id': 'match', 'value': '标准博弈'}
            worker.knowledge = {'roles': {}, 'guide': {}, 'progression': {}}
            worker.history, worker.inspections, worker.inspection_attempted = {}, {}, set()
            worker.state['statistics']['decisions'] = 0
            worker.publish = lambda **changes: worker.state.update(changes)
            worker.log_events = []
            worker.log = worker.log_events.append
            worker.observe()
            worker.ask(worker.last_observation, 'shop_strategy', '测试当前请求')
            current = worker.state['decision_request']
            record = {'value': {**plan(), 'fields': rendered[0][2]},
                      'proof': {'source': 'observed_screen', 'snapshot_id': current['snapshot_id'],
                                'evidence_file': current['evidence_file'], 'resume_epoch': worker.epoch()}}
            resource = records / 'synthetic-shop-resources'
            resource.mkdir()
            (resource / 'names.json').write_text(json.dumps({'names': ['飞霄']}), encoding='utf8')
            with patch.object(shop_reader, 'RESOURCE_DIR', resource):
                yield worker, record, control, rendered

    def test_real_worker_plan_command_receipts_three_f_and_durable_actual_spend(self):
        with self.worker([values(coins=62, xp=[44, 52]), values(coins=58, xp=[48, 52]),
                          values(coins=54, level=8, xp=[0, 72])]) as (worker, record, control, frames):
            request = worker.state['decision_request']
            worker.execute_plan({'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
                'resume_epoch': worker.epoch(), 'context_update': {'economy_plan': record},
                'actions': [{'type': 'finish_preparation_review', 'reason': '预算与读数已核，仅登记'}]})
            worker.advance_economy(worker.last_observation)
            keys = [action['args'] for entry in control.published for action in entry.get('actions', []) if action['type'] == 'key']
            self.assertEqual(keys, [[70.], [70.], [70.]])
            ledger = worker.economy_ledger('2-3')
            self.assertEqual(ledger['spent'], {'purchase': 0, 'refresh': 0, 'experience': 12})
            self.assertIsNone(ledger['pending'])
            self.assertEqual(worker.economic_policy(worker.last_observation)['observation']['values']['level'], 8)
            self.assertEqual(json.loads(worker.economy_ledger_path('2-3').read_text())['ledger']['spent']['experience'], 12)
            worker.economy_ledgers = {}
            self.assertEqual(worker.economy_ledger('2-3')['spent']['experience'], 12)
            worker.epoch = lambda: 'new-epoch'
            self.assertFalse(worker.economic_policy(worker.last_observation)['available'])
            self.assertEqual(worker.economy_ledger('2-3')['spent']['experience'], 12)

    def test_published_zero_effect_stops_without_replay_and_reconcile_is_atomic(self):
        with self.worker([values()]) as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            self.assertTrue(worker.advance_economy(worker.last_observation))
            ledger = worker.economy_ledger('2-3')
            self.assertEqual(control.action_index, 1)
            self.assertIsNotNone(ledger['pending'])
            self.assertFalse(worker.advance_economy(worker.last_observation))
            self.assertEqual(control.action_index, 1)
            pending_id = ledger['pending']['request_id']
            request = worker.state['decision_request']
            resolution = copy.deepcopy(record)
            resolution['proof'].update(snapshot_id=request['snapshot_id'], evidence_file=request['evidence_file'])
            resolution['value'].update(resolve_request_id=pending_id, revision=2)
            resolution['value']['budget']['experience'] = 1000
            with self.assertRaises(ValueError):
                worker.accept_economy_plan(resolution)
            self.assertEqual(ledger['pending']['request_id'], pending_id)
            self.assertEqual(ledger['spent']['experience'], 0)
            resolution['value']['budget']['experience'] = 0
            worker.accept_economy_plan(resolution)
            self.assertIsNone(ledger['pending'])
            self.assertEqual(control.action_index, 1)

    def test_missing_after_frame_uses_only_observe_receipt_fence(self):
        with self.worker([values(coins=62, xp=[44, 52])]) as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            with patch.object(worker, 'read_frame', side_effect=runner.entry.ObservationUnavailable('truncated')):
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    worker.advance_economy(worker.last_observation)
            ledger = worker.economy_ledger('2-3')
            pending_id = ledger['pending']['request_id']
            self.assertTrue(ledger['pending']['receipt'])
            self.assertNotIn('after_png', ledger['pending'])
            worker.observe()
            worker.ask(worker.last_observation, 'economy_result', '只读补证')
            request = worker.state['decision_request']
            resolution = copy.deepcopy(record)
            resolution['proof'].update(snapshot_id=request['snapshot_id'], evidence_file=request['evidence_file'])
            resolution['value'].update(resolve_request_id=pending_id, fields=frames[1][2], revision=2)
            worker.accept_economy_plan(resolution)
            self.assertIsNone(ledger['pending'])
            self.assertEqual(ledger['spent']['experience'], 4)
            self.assertEqual(control.action_index, 1)

    def test_real_b003_resume_cas_repairs_pending_economy_without_replaying_f(self):
        with self.worker([values(coins=62, xp=[44, 52])]) as (worker, record, control, frames):
            # Use the production epoch reader, not the fixture's fixed epoch.
            del worker.epoch
            worker.accept_economy_plan(record)
            old_review = copy.deepcopy(record)
            with patch.object(worker, 'read_frame', side_effect=runner.entry.ObservationUnavailable('truncated')):
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    worker.advance_economy(worker.last_observation)
            ledger = worker.economy_ledger('2-3')
            pending = copy.deepcopy(ledger['pending'])
            self.assertTrue(pending['publication_attempted'])
            self.assertNotIn('after_png', pending)
            receipt_path = worker.run / 'request-ledger' / (hashlib.sha256(pending['request_id'].encode()).hexdigest() + '.json')
            original_receipt = receipt_path.read_bytes()
            pause_path = worker.run / 'manual-pause.json'
            original_status, original_pause, original_publish = control.status, control.pause, control.publish_request

            def status():
                pause = runner.optional(pause_path)
                return {**original_status(), 'paused': pause is not None,
                        'pause_id': pause['pause_id'] if pause else None}

            def pause(reason):
                result = original_pause(reason)
                control.write_json(pause_path, {**control.OWNER, 'pause_id': 'economy-pause', 'reason': reason})
                return {**result, 'pause_id': 'economy-pause'}

            def publish(value):
                if value['kind'] == 'resume':
                    self.assertEqual(value['expected_pause_id'], runner.optional(pause_path)['pause_id'])
                result = original_publish(value)
                if value['kind'] == 'resume':
                    pause_path.unlink()
                return result

            # Only the fixture's broker pause state is modeled here. Real
            # latch_manual, explicit_resume, Entry publication/receipt and the
            # B003 event validator run unchanged; no resume ledger is invented.
            worker.state['statistics']['failures'] = 0
            with patch.object(runner.entry, 'backend', return_value=control), \
                    patch.object(control, 'status', side_effect=status), \
                    patch.object(control, 'pause', side_effect=pause), \
                    patch.object(control, 'publish_request', side_effect=publish):
                worker.pause_internal('经济输入已发布，后帧暂不可用；保留原请求')
                manual = runner.manual_state(worker.run)
                guard = runner.resume_guard_snapshot(worker.run, worker.owner, control)
                self.assertEqual(guard['pending_manual_ids'], [manual['manual_id']])
                self.assertEqual(guard['broker_pause_id'], 'economy-pause')
                published = len(control.published)
                refused = runner.explicit_resume(worker.run, worker.owner, control, 'stale-economic-resume',
                    expected_guard={**guard, 'broker_pause_id': 'obsolete-pause'})
                self.assertFalse(refused['resumed'])
                self.assertEqual(refused['mismatch_field'], 'broker_pause_id')
                self.assertEqual(len(control.published), published)
                self.assertEqual(worker.epoch(), 'old-epoch')
                self.assertEqual(ledger['pending'], pending)
                self.assertTrue(pause_path.exists())

                resumed = runner.explicit_resume(worker.run, worker.owner, control, 'economic-resume', expected_guard=guard)
                self.assertTrue(resumed['resumed'])
                self.assertIsNone(runner.manual_state(worker.run))
                epoch = runner.entry.read_json(worker.run / 'runner-resume-epoch.json')
                event, resume_receipt = runner.verified_resume_event(worker.run, worker.owner, control, epoch)
                self.assertEqual((event['old_epoch'], event['new_epoch']), ('old-epoch', 'economic-resume'))
                self.assertEqual(event['guard'], guard)
                self.assertEqual(event['receipt'], runner.redact(resume_receipt))
                self.assertEqual(resume_receipt['request']['expected_pause_id'], 'economy-pause')
                self.assertFalse(worker.economic_policy(worker.last_observation)['available'])
                self.assertEqual(ledger['pending'], pending)

                worker.observe()
                worker.ask(worker.last_observation, 'economy_result', '新epoch当前请求只读对账原经济输入')
                current = worker.state['decision_request']
                self.assertEqual(current['resume_epoch'], 'economic-resume')
                self.assertNotEqual(current['snapshot_id'], old_review['proof']['snapshot_id'])
                self.assertEqual(worker.preparation_reviews, {})
                with self.assertRaises(ValueError):
                    worker.accept_economy_plan(old_review)
                self.assertEqual(ledger['pending'], pending)

                resolution = copy.deepcopy(old_review)
                resolution['proof'].update(snapshot_id=current['snapshot_id'], evidence_file=current['evidence_file'],
                                           resume_epoch=worker.epoch())
                resolution['value'].update(resolve_request_id=pending['request_id'], fields=frames[1][2], revision=2)
                resolution['value']['budget']['experience'] = 4  # Cumulative actual spend; no more F planned.
                worker.accept_economy_plan(resolution)
                self.assertIsNone(ledger['pending'])
                self.assertEqual(ledger['spent'], {'purchase': 0, 'refresh': 0, 'experience': 4})
                self.assertEqual(worker.economy_binding['scope'], ('match', '2-3', 'economic-resume'))
                self.assertEqual(worker.economy_binding['proof'], resolution['proof'])
                self.assertEqual(runner.entry.read_json(worker.economy_ledger_path('2-3'))['ledger']['spent'], ledger['spent'])
                self.assertEqual(record, old_review)
                self.assertEqual(receipt_path.read_bytes(), original_receipt)
                self.assertEqual(runner.verified_resume_event(worker.run, worker.owner, control, epoch)[1], resume_receipt)
                self.assertEqual([action['args'] for request in control.published for action in request.get('actions', [])
                                  if action['type'] == 'key'], [[70.]])
                self.assertEqual(sum(request['kind'] == 'resume' for request in control.published), 1)

    def test_known_target_slot_can_buy_before_unrelated_unknown_slots_but_not_xp(self):
        with self.worker() as (worker, record, control, frames):
            actual = worker.last_observation
            actual['shop'] = shop(actual['snapshot_id'], ('飞霄', 5))
            actual['shop']['slots'][1]['status'] = 'unknown'
            record['value'].update(targets=[{'name': '飞霄', 'copies': 1, 'reason': '当前已核缺口', 'critical': False}],
                budget={'purchase': 5, 'refresh': 0, 'experience': 8})
            # Free count need not be known until after the present gap buy.
            record['value']['fields']['free_refreshes'] = None
            worker.accept_economy_plan(record)
            policy = worker.economic_policy(actual)
            self.assertEqual(policy['actions'][0]['type'], 'buy_shop')
            self.assertFalse(policy['observation']['shop_complete'])
            worker.require_economic_action(policy['actions'][0], actual)
            with self.assertRaises(ValueError):
                worker.require_xp_cost(actual)
            worker.invalidate_preparation('inventory')
            self.assertFalse(worker.economic_policy(actual)['available'])

    def test_free_d_is_zero_spend_and_unknown_paid_price_is_not_fabricated(self):
        with self.worker([values()], initial=values(free_refreshes=1)) as (worker, record, control, frames):
            record['value']['fields']['refresh_cost'] = None
            record['value']['budget']['experience'] = 0
            worker.accept_economy_plan(record)
            worker.advance_economy(worker.last_observation)
            ledger = worker.economy_ledger('2-3')
            self.assertEqual(control.action_index, 1)
            self.assertEqual([action['args'] for entry in control.published for action in entry.get('actions', [])
                              if action['type'] == 'key'], [[68.]])
            self.assertEqual(ledger['spent']['refresh'], 0)
            self.assertEqual(ledger['paid_refreshes'], 0)
            self.assertIsNone(ledger['pending'])

    def test_native_coin_override_still_requires_icon_and_confidence(self):
        with self.worker() as (worker, record, control, frames):
            worker.last_observation['semantic']['coins'].update(value=999, currency_icon_gold_fraction=.1, confidence=.4)
            worker.accept_economy_plan(record)
            observed = worker.economy_observation(worker.last_observation)
            self.assertEqual(observed['values']['coins'], 66)
            self.assertEqual(observed['evidence']['coins']['source'], 'supervisor_read_with_identical_current_roi')
            worker.last_observation['semantic']['coins'].update(currency_icon_gold_fraction=.8, confidence=.99)
            worker.economy_read_cache = None
            self.assertIn('coins', worker.economy_observation(worker.last_observation)['unknown'])

    def test_missing_frame_cannot_cross_a_later_mutating_receipt(self):
        with self.worker([values(coins=62, xp=[44, 52])]) as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            with patch.object(worker, 'read_frame', side_effect=runner.entry.ObservationUnavailable('truncated')):
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    worker.advance_economy(worker.last_observation)
            pending = worker.economy_ledger('2-3')['pending']
            runner.entry.request(control, 'actions', ['key:27'], 'later-input', False)
            with self.assertRaisesRegex(ValueError, '其他输入'):
                worker.verify_economy_fence(pending)
            self.assertEqual(control.action_index, 2)  # Only the deliberate fixture inputs above.
            self.assertIsNotNone(worker.economy_ledger('2-3')['pending'])

    def test_zero_experience_budget_does_not_require_hidden_experience_controls(self):
        with self.worker() as (worker, record, control, frames):
            record['value']['fields'] = {name: record['value']['fields'][name] for name in ('coins', 'free_refreshes')}
            record['value']['budget']['experience'] = 0
            worker.accept_economy_plan(record)
            self.assertIn('xp_gain', worker.economic_policy(worker.last_observation)['observation']['unknown'])
            worker.review_preparation({'proof': record['proof'], 'value': {'phase': 'economy', 'stage': '2-3',
                'reviewer': 'supervising_agent', 'completed': True, 'findings': '预算0、免费0、全店无目标缺口，按实读停止'}})
            self.assertTrue(worker.preparation_reviews['economy']['completed'])
            self.assertEqual(control.action_index, 0)

    def test_failed_pure_observe_is_zero_input_but_handoff_is_not(self):
        with self.worker([values()]) as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            worker.advance_economy(worker.last_observation)
            pending = worker.economy_ledger('2-3')['pending']
            runner.entry.request(control, 'actions', ['observe'], 'failed-observe', False)
            path = worker.run / 'request-ledger' / (hashlib.sha256(b'failed-observe').hexdigest() + '.json')
            receipt = json.loads(path.read_text())
            receipt['result'].update(ok=False, completed=[], input_attempted=False, attempted_actions=[])
            control.write_json(path, receipt)
            worker.verify_economy_fence(pending)
            before_conflict = copy.deepcopy(pending)
            for ok, completed in ((False, []), (True, receipt['request']['actions'])):
                contradictory = copy.deepcopy(receipt)
                contradictory['result'].update(ok=ok, completed=completed, input_attempted=True,
                    attempted_actions=[{'type': 'key', 'args': [70]}])
                control.write_json(path, contradictory)
                with self.subTest(receipt_ok=ok, completed=completed):
                    self.assertTrue(runner.manual_receipt_state(contradictory)['unknown_input'])
                    with self.assertRaises(ValueError):
                        worker.verify_economy_fence(pending)
                    self.assertEqual(pending, before_conflict)
            receipt['request']['handoff'] = True
            control.write_json(path, receipt)
            with self.assertRaises(ValueError):
                worker.verify_economy_fence(pending)
            self.assertEqual(control.action_index, 1)


if __name__ == '__main__':
    import unittest
    unittest.main()
