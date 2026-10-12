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


class SelectedModelContracts(TestCase):
    """Production budget/first-action contracts, separate from receipt stubs."""
    def model(self):
        worker = runner.Worker.__new__(runner.Worker)
        worker.active_match_id = 'match'
        worker.live_mode = {'match_id': 'match', 'value': '标准博弈'}
        worker.epoch = lambda: 'epoch'
        actual = {'snapshot_id': 'b' * 64, 'page': 'shop', 'fields': {'stage': '2-3'},
                  'shop': shop('b' * 64, ('飞霄', 5))}
        binding = {'request_id': 'root-request', 'snapshot_id': actual['snapshot_id'],
                   'resume_epoch': 'epoch', 'match_id': 'match'}
        model = {'schema': 'currency-wars-economic-allocation/v1', 'model': 'released_prior',
            'snapshot_id': actual['snapshot_id'], 'binding': binding, 'status': 'conditional_joint_decision',
            'complete_dimensions': dict.fromkeys(('purchase', 'experience', 'refresh'), True),
            'configured_XP_gain_assumption': 4, 'database_sha256': 'd' * 64,
            'conditional_best': {'budget': {'purchase': 5, 'experience': 8, 'refresh': 0},
                'purchase_refs': ['shop.slots[0]'], 'level_assumption': 8, 'profile_assumption': 'matched'},
            'allocation': {'budget': {'purchase': 5, 'experience': 8, 'refresh': 0},
                'reserve': 0, 'refresh_count_max': 0, 'future_purchase_budget': 0},
            'conditional_raw_actions': [{'type': 'buy_xp', 'count': 2}, {'type': 'buy', 'reference': 'shop.slots[0]'}]}
        review = {'origin': 'local_advisor_recomputation', **binding, 'input_sha256': 'a' * 64,
                  'source_verified': True, 'allocation': model}
        return worker, actual, review, economy.new_ledger()

    def test_zero_budget_or_changed_targets_cannot_override_current_selected_model(self):
        worker, actual, review, ledger = self.model()
        selected = worker.model_economy_plan(review, actual, ledger)
        worker.assert_model_budget(selected, review, actual, ledger)
        for change in ({'budget': dict.fromkeys(('purchase', 'experience', 'refresh'), 0)},
                       {'targets': []}, {'reserve': {'coins': 50}}, {'experience': {'target_level': 7}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                worker.assert_model_budget({**selected, **change}, review, actual, ledger)

    def test_selected_xp_precedes_visible_purchase_and_dispatches_only_one(self):
        worker, actual, review, ledger = self.model()
        ledger['spent']['experience'] = 4
        plan_value = worker.model_economy_plan(review, actual, ledger)
        self.assertEqual(plan_value['budget']['experience'], 12)
        reading = {'values': {**values(), 'refresh_offer': {'mode': 'paid', 'free_remaining': None, 'paid_cost': 2}},
                   'snapshot_id': actual['snapshot_id'], 'unknown': ['xp_gain']}
        reading['values'].pop('xp_gain')
        chosen = worker.selected_economic_action(review, actual, reading, plan_value, ledger)
        self.assertEqual(chosen['type'], 'buy_xp')
        self.assertEqual(chosen['count'], 1)
        self.assertNotIn('xp_gain', reading['values'])
        self.assertEqual(plan_value['model_values']['xp_gain_assumption'], 4)
        ledger['pending'] = {'request_id': 'original-unknown'}
        with self.assertRaises(ValueError):
            worker.selected_economic_action(review, actual, reading, plan_value, ledger)
        self.assertEqual(ledger['pending']['request_id'], 'original-unknown')

    def test_model_xp_gain_is_separate_and_unexpected_transition_stays_unknown(self):
        before = {'values': {'coins': 66, 'level': 7, 'xp': [40, 52]}}
        after = {'values': {'coins': 62, 'level': 7, 'xp': [44, 52]}}
        model = {'xp_gain_assumption': 4, 'source': 'released_database_planning_assumption'}
        self.assertEqual(economy.classify_effect('experience', before, after, expected_cost=4,
                         model_values=model)['outcome'], 'success')
        after['values']['xp'] = [45, 52]
        self.assertEqual(economy.classify_effect('experience', before, after, expected_cost=4,
                         model_values=model)['outcome'], 'unknown')
        self.assertNotIn('xp_gain', before['values'])

    def test_typed_actor_cannot_accept_budget_coordinates_batch_or_wrong_phase(self):
        from datetime import datetime, timedelta, timezone
        request = {'request_id': 'root', 'snapshot_id': 'b' * 64, 'resume_epoch': 'epoch', 'match_id': 'match',
            'deadline_at': (datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat(), 'kind': 'shop_strategy',
            'observation': {'page': 'shop'}, 'preparation_checklist': {'phase': 'economy'}}
        action = {'type': 'advance_local_economy', 'expected_page': 'shop', 'reason': '本地联合模型一次'}
        reply = {k: request[k] for k in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')}
        reply.update(actions=[action], context_update={})
        runner.validate_plan(reply, request, 'epoch')
        for bad in ({**reply, 'actions': [{**action, 'budget': 0}]},
                    {**reply, 'actions': [{**action, 'args': [490, 500]}]},
                    {**reply, 'actions': [action, action]},
                    {**reply, 'context_update': {'economy_plan': {'budget': 0}}}):
            with self.assertRaises(ValueError):
                runner.validate_plan(bad, request, 'epoch')
        request['preparation_checklist']['phase'] = 'inventory_cleanup'
        with self.assertRaises(ValueError):
            runner.validate_plan(reply, request, 'epoch')

    def test_actor_real_entry_executes_one_xp_and_publishes_new_source(self):
        import currency_wars_refresh_offer as refresh_reader
        changes = [values(coins=62, xp=[44, 52]), values(coins=58, xp=[48, 52])]
        with EconomyTests().worker(changes, joint_complete=False) as (worker, record, control, frames):
            worker.owner['run_token'] = control.OWNER['run_token']
            control.assert_owner = lambda chat, token: self.assertEqual(
                {'chat_id': chat, 'run_token': token}, control.OWNER)
            control.write_json(worker.run / 'runner-owner.json', worker.owner)
            original_read = worker.perception.read
            states = {hashlib.sha256(f[1]).hexdigest(): s for f, s in zip(frames, [values()]+changes)}
            def native_read(path, force=False, *, scope='full'):
                observed = original_read(path, force=force, scope=scope)
                snapshot = observed['snapshot_id']
                state = states[snapshot]
                observed['semantic'].update(
                    player_hud={'snapshot_id': snapshot, 'origin': 'native_player_hud', 'actor': 'player',
                                'level': state['level'], 'xp': state['xp']},
                    experience_offer={'snapshot_id': snapshot, 'origin': 'native_experience_offer',
                                      'cost_checked': True, 'buy_cost': 4, 'buy_gain': None},
                    refresh_offer={'snapshot_id': snapshot, 'source': 'declared_protocol_fixture'},
                    team={'slots': [{'location': 'board', 'status': 'empty'} for unused in range(10)]})
                return observed
            worker.perception.read = native_read
            model_template = self.model()[2]['allocation']
            model_template['conditional_best']['budget']['purchase'] = 0
            model_template['allocation']['budget']['purchase'] = 0
            model_template['conditional_best']['purchase_refs'] = []
            model_template['conditional_raw_actions'] = [{'type': 'buy_xp', 'count': 2}]
            def model(request, observed):
                binding = {k: request[k] for k in ('request_id', 'resume_epoch', 'match_id')}
                binding['snapshot_id'] = observed['snapshot_id']
                allocation = {**copy.deepcopy(model_template), 'binding': binding, 'snapshot_id': observed['snapshot_id']}
                return {'origin': 'local_advisor_recomputation', **binding, 'source_verified': True,
                        'input_sha256': 'a' * 64, 'allocation': allocation, 'full_observation': True}
            worker.local_economic_allocation = model
            with patch.object(refresh_reader, 'consume_offer', return_value={'mode': 'paid', 'free_remaining': None, 'paid_cost': 2}), \
                    patch.object(runner.entry, 'owned_broker_identity', return_value={'pid': 123, 'creation_id': '456'}):
                worker.economy_binding = None  # First local budget after a new lease.
                inherited = worker.economy_ledger('2-3')
                inherited['requires_current_budget'] = True
                inherited['prior_run_unknown'] = [{'origin_run_id': 'prior-fixture',
                                                   'request_id': 'old-unconfirmed'}]
                worker.observe()
                worker.state['decision_request'] = None
                worker.ask(worker.last_observation, 'shop_strategy', '声明协议替身，测试真实单次Entry发布')
                request = copy.deepcopy(worker.state['decision_request'])
                reply = {k: request[k] for k in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')}
                reply.update(actions=[{'type': 'advance_local_economy', 'expected_page': 'shop', 'reason': '仅本地选择一笔'}], context_update={})
                # Passive capture changes receipt/frame IDs even with the same
                # PNG. The old authorization must produce only a new source.
                worker.observe()
                worker.execute_plan(reply)
                self.assertEqual(control.action_index, 0)
                refreshed = worker.state['decision_request']
                self.assertNotEqual(refreshed['request_id'], request['request_id'])
                self.assertFalse(refreshed['source_refresh']['input_sent'])
                self.assertTrue(worker.economy_ledger('2-3')['requires_current_budget'])
                reply = {k: refreshed[k] for k in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')}
                reply.update(actions=[{'type': 'advance_local_economy', 'expected_page': 'shop', 'reason': '新原帧只授权一笔'}], context_update={})
                worker.execute_plan(reply)
                self.assertEqual(control.action_index, 1)
                self.assertEqual(worker.economy_ledger('2-3')['spent']['experience'], 4)
                self.assertFalse(worker.economy_ledger('2-3')['requires_current_budget'])
                self.assertEqual(worker.economy_ledger('2-3')['prior_run_unknown'],
                                 [{'origin_run_id': 'prior-fixture', 'request_id': 'old-unconfirmed'}])
                self.assertEqual(worker.economy_binding['plan']['prior_run_unknown_requests'],
                                 ['prior-fixture:old-unconfirmed'])
                self.assertIsNone(worker.economy_ledger('2-3')['pending'])
                self.assertNotEqual(worker.state['decision_request']['request_id'], request['request_id'])
                self.assertNotEqual(worker.state['decision_request']['snapshot_id'], request['snapshot_id'])
                self.assertNotIn('economy', worker.preparation_reviews)
                self.assertNotIn('xp_gain', worker.economy_observation(worker.last_observation)['values'])
                # This newly sourced model changes the remaining allocation;
                # the old cumulative budget cannot silently fund another XP.
                self.assertFalse(worker.economic_policy(worker.last_observation)['available'])
                count = control.action_index
                with self.assertRaises(ValueError):
                    worker.execute_plan(reply)
                self.assertEqual(control.action_index, count)
                current = worker.state['decision_request']
                new_reply = {k: current[k] for k in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')}
                new_reply.update(actions=reply['actions'], context_update={})
                worker.economy_ledger('2-3')['pending'] = {'request_id': 'original-unresolved'}
                with self.assertRaises(ValueError):
                    worker.execute_plan(new_reply)
                self.assertEqual(worker.economy_ledger('2-3')['pending']['request_id'], 'original-unresolved')
                self.assertEqual(control.action_index, count)

    def test_economic_cache_rechecks_changed_same_png_shop_contract(self):
        with EconomyTests().worker(joint_complete=False) as (worker, record, control, frames):
            worker.accept_economy_plan(record)
            actual = worker.last_observation
            self.assertTrue(worker.economy_observation(actual)['shop_complete'])
            actual['shop']['slots'][0]['status'] = 'unknown'
            self.assertFalse(worker.economy_observation(actual)['shop_complete'])
            self.assertEqual(control.action_index, 0)

    def test_comparison_source_authenticates_navigation_and_every_intervening_receipt(self):
        import currency_wars_economic_views as views
        with EconomyTests().worker(joint_complete=False) as (worker, record, control, frames):
            original = worker.last_observation
            root_request = worker.state['decision_request']
            scope = {'stage': '2-3', 'match_id': worker.active_match_id, 'resume_epoch': worker.epoch()}
            worker.economic_roster_view = {'observation': copy.deepcopy(original),
                'png': root_request['original_png'], 'scope': scope, 'watermark': worker.economy_receipt_watermark()}
            worker.economic_navigation_receipts = {}
            runner.entry.request(control, 'actions', ['click:10:10', 'wait:0.7'], 'declared-open-shop', False)
            nav = runner.await_existing_receipt(worker.run, control, 'declared-open-shop', 0)
            worker.economic_navigation_receipts['declared-open-shop'] = {'scope': scope,
                'actions': copy.deepcopy(nav['request']['actions'])}
            # The fake broker changes any click to a white test frame. Restore
            # this declared numeric source before the independent observe; only
            # the receipt authentication layer is under test, not real OCR/UI.
            control.frame = frames[0][1]
            actual = worker.observe()
            with patch.object(views, 'compose_view', return_value={'origin': 'declared-composer-result'}) as compose:
                self.assertEqual(worker.economic_comparison_view(root_request, actual)['origin'], 'declared-composer-result')
                self.assertEqual(compose.call_args.args[-1], ['declared-open-shop'])
                runner.entry.request(control, 'actions', ['key:46'], 'uncovered-sale', False)
                actual = worker.observe()
                with self.assertRaises(ValueError):
                    worker.economic_comparison_view(root_request, actual)
                self.assertEqual(compose.call_count, 1)


class MacroContracts(TestCase):
    """Authorization/spend regressions through the real Worker and Entry path.

    Numeric/native readings and solver output are declared protocol fixtures;
    these cases make no claim about live OCR, scoring or game efficacy.
    """
    @contextlib.contextmanager
    def worker(self, *, changes=None, dimensions='xp', hidden_board=False):
        import currency_wars_refresh_offer as refresh_reader
        changes = changes if changes is not None else [values(coins=62, xp=[44, 52]), values(coins=58, xp=[48, 52])]
        initial = values(free_refreshes=2) if dimensions == 'free_refresh' else values()
        with EconomyTests().worker(changes, initial=initial, joint_complete=False) as (worker, unused, control, frames):
            worker.owner['run_token'] = control.OWNER['run_token']
            control.assert_owner = lambda chat, token: self.assertEqual({'chat_id': chat, 'run_token': token}, control.OWNER)
            control.write_json(worker.run / 'runner-owner.json', worker.owner)
            original_status = control.status
            control.status = lambda: {**original_status(), 'game': {'pid': 777, 'creation_id': '888', 'hwnd': 999}}
            original_read = worker.perception.read
            navigation_page = ['shop']
            initial_observation = copy.deepcopy(worker.last_observation)
            states = {hashlib.sha256(f[1]).hexdigest(): s for f, s in zip(frames, [initial] + changes)}
            worker.declared_macro_states = states
            def native_read(path, force=False, *, scope='full'):
                try:
                    observed = original_read(path, force=force, scope=scope)
                except KeyError:
                    # Declared harmless-animation frame, unchanged native
                    # digits. Never offered as evidence of actual OCR quality.
                    snapshot = hashlib.sha256(Path(path).read_bytes()).hexdigest()
                    self.assertIn(snapshot, states)
                    observed = {**copy.deepcopy(initial_observation), 'snapshot_id': snapshot, 'shop': shop(snapshot)}
                observed['page'] = navigation_page[0]
                observed['fields']['deployed'] = '0/7'
                observed['read_contract'] = {'effective_scope': 'full', 'version': runner.READ_CONTRACT_VERSION, 'unread': []}
                snapshot = observed['snapshot_id']
                state = states[snapshot]
                observed['semantic'].update(
                    player_hud={'snapshot_id': snapshot, 'origin': 'native_player_hud', 'actor': 'player',
                                'level': state['level'], 'xp': state['xp']},
                    experience_offer={'snapshot_id': snapshot, 'origin': 'native_experience_offer',
                                      'cost_checked': True, 'buy_cost': 4, 'buy_gain': None},
                    refresh_offer={'snapshot_id': snapshot, 'source': 'declared_protocol_fixture'},
                    team={'snapshot_id': snapshot, 'slots': [
                        {'location': location, 'row': row, 'slot': index, 'snapshot_id': snapshot,
                         'status': 'unknown' if hidden_board and navigation_page[0] == 'shop' and location == 'board' else 'empty'}
                        for location, row, count in (('board', 'front', 4), ('board', 'back', 6), ('bench', 'bench', 9))
                        for index in range(1, count + 1)]})
                observed['semantic']['coins'].update(snapshot_id=snapshot, confidence=.99)
                header = [250, 50, 325, 80] if observed['page'] == 'shop' else [410, 40, 510, 80]
                observed['rows'].extend([{'text': '备战阶段', 'confidence': .99, 'box': header},
                    {'text': '出战', 'confidence': .99, 'box': [1700, 730, 1800, 760]},
                    {'text': '收起' if observed['page'] == 'shop' else '商店', 'confidence': .99,
                     'box': [1580, 960, 1630, 980]}])
                return observed
            worker.perception.read = native_read
            original_write = control.write_json
            def navigate_write(path, value):
                # Existing broker fixture paints clicks white. For native view
                # navigation declare unchanged numeric assets and page toggle,
                # while preserving its original input ID/terminal receipt.
                if Path(path) == worker.run / 'result.json' and (value.get('completed') or [{}])[0].get('type') == 'click':
                    navigation_page[0] = 'preparation' if navigation_page[0] == 'shop' else 'shop'
                    payload = frames[control.action_index][1]
                    control.frame = payload
                    frame = value['observation']
                    for name in ('original', 'snapshot'):
                        Path(frame[name]).write_bytes(payload)
                    frame['snapshot_sha256'] = frame['original_sha256'] = hashlib.sha256(payload).hexdigest()
                original_write(path, value)
            control.write_json = navigate_write
            template = SelectedModelContracts().model()[2]['allocation']
            def model(request, observed):
                binding = {k: request[k] for k in ('request_id', 'resume_epoch', 'match_id')}
                binding['snapshot_id'] = observed['snapshot_id']
                allocation = copy.deepcopy(template)
                done = observed['semantic']['player_hud']['xp'][0] >= 48
                allocation.update(binding=binding, snapshot_id=observed['snapshot_id'])
                budget = {'purchase': 0, 'experience': 0 if done or dimensions != 'xp' else 8, 'refresh': 2 if dimensions == 'refresh' else 0}
                allocation['conditional_best'].update(budget=budget, purchase_refs=[])
                allocation['allocation'].update(budget=budget, refresh_count_max=int(dimensions in ('refresh', 'free_refresh')))
                allocation['bounds'] = {'node_max_refreshes': 3}
                allocation['conditional_raw_actions'] = [] if done or dimensions == 'none' else [
                    {'type': 'buy_xp', 'count': 2} if dimensions == 'xp' else {'type': 'refresh', 'price': 0 if dimensions == 'free_refresh' else 2}]
                return {'origin': 'local_advisor_recomputation', **binding, 'source_verified': True,
                        'input_sha256': hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest(),
                        'allocation': allocation, 'full_observation': True}
            worker.local_economic_allocation = model
            def offer(unused_offer, unused_image, snapshot, unused_page):
                free = states[snapshot]['free_refreshes']
                return {'mode': 'free', 'free_remaining': free, 'paid_cost': None} if free else {
                    'mode': 'paid', 'free_remaining': None, 'paid_cost': 2}
            with patch.object(refresh_reader, 'consume_offer', side_effect=offer), \
                    patch.object(runner.entry, 'owned_broker_identity', return_value={'pid': 123, 'creation_id': '456'}):
                worker.observe()
                worker.state['decision_request'] = None
                worker.ask(worker.last_observation, 'shop_strategy', '声明原协议替身，验证有界宏')
                self.assertEqual(worker.state['decision_request'].get('economic_macro_contract'), economy.MACRO_CONTRACT)
                yield worker, control, frames

    def reply(self, worker, **limits):
        request = worker.state['decision_request']
        caps = {'max_economic_actions': 2, 'max_total_spent': 8, 'max_paid_refreshes': 0,
                'max_navigation_clicks': 0, 'max_wall_seconds': 30, **limits}
        return {**{k: request[k] for k in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')},
            'actions': [{'type': 'advance_local_economy_macro', 'expected_page': 'shop', 'reason': '测试有界逐笔重算', 'limits': caps}],
            'context_update': {}}

    def test_two_verified_original_effects_one_parent_and_fresh_children(self):
        with self.worker() as (worker, control, frames):
            reply = self.reply(worker)
            parent = copy.deepcopy(worker.state['decision_request'])
            animation = Image.open(io.BytesIO(frames[0][1]))
            ImageDraw.Draw(animation).rectangle([5, 5, 8, 8], fill='blue')
            payload = io.BytesIO()
            animation.save(payload, format='PNG')
            animation.close()
            control.frame = payload.getvalue()
            worker.declared_macro_states[hashlib.sha256(control.frame).hexdigest()] = values()
            worker.observe()  # B: original parent plus authenticated pure observe.
            count = worker.state['statistics']['decisions']
            result = worker.execute_plan(reply)
            self.assertEqual(control.action_index, 2, result.get('error'))
            self.assertEqual(result['effects_verified'], 2)
            self.assertEqual(result['gross_spent'], 8)
            self.assertEqual(result['status'], 'consumed')
            self.assertEqual(worker.economy_ledger('2-3')['spent']['experience'], 8)
            self.assertIsNone(worker.economy_ledger('2-3')['pending'])
            self.assertEqual(worker.state['statistics']['decisions'], count + 1)
            self.assertEqual(result['parent'], parent)
            self.assertNotEqual(result['children'][0]['request']['snapshot_id'], parent['snapshot_id'])
            inputs = [p for p in result['publications'] if p['kind'] == 'experience']
            self.assertEqual(len({p['request_id'] for p in inputs}), 2)
            self.assertTrue(all(p['status'] == 'effect_verified' and p['receipt']['result']['ok'] for p in inputs))
            sources = [c for c in result['children'] if c.get('selected_action')]
            self.assertEqual(len({c['request']['request_id'] for c in sources}), 2)
            self.assertEqual(len({c['request']['observation']['capture_request_id'] for c in sources}), 2)
            self.assertEqual(len({c['request']['snapshot_id'] for c in sources}), 2)
            self.assertEqual(len({c['input_sha256'] for c in sources}), 2)
            self.assertTrue(all(Path(c['request']['original_png']).read_bytes() for c in sources))
            with self.assertRaises(ValueError):
                worker.execute_plan(reply)
            self.assertEqual(control.action_index, 2)

    def test_global_action_and_gross_caps_never_choose_cheaper_substitute(self):
        for caps, expected in (({'max_economic_actions': 1}, 1), ({'max_total_spent': 4}, 1),
                               ({'max_total_spent': 3}, 0)):
            with self.subTest(caps=caps), self.worker() as (worker, control, frames):
                result = worker.execute_plan(self.reply(worker, **caps))
                self.assertEqual(control.action_index, expected, result.get('error'))
                self.assertEqual(result['gross_spent'], 4 * expected)
                self.assertNotEqual(result['outcome'], 'economy_complete')
                self.assertNotIn('economy', worker.preparation_reviews)

    def test_initial_external_and_attempted_observe_successors_are_rejected(self):
        for mutation in ('physical', 'attempted', 'pending', 'handoff', 'fence_missing'):
            with self.subTest(mutation=mutation), self.worker() as (worker, control, frames):
                reply = self.reply(worker)
                if mutation == 'physical':
                    worker.observe()
                    rid = worker.last_observation['capture_request_id']
                    path = worker.run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
                    receipt = runner.entry.read_json(path)
                    receipt['request']['actions'] = [{'type': 'key', 'args': [46]}]
                    receipt['result'].update(completed=receipt['request']['actions'], input_attempted=True,
                                             attempted_actions=receipt['request']['actions'])
                    control.write_json(path, receipt)
                    worker.observe()
                elif mutation == 'fence_missing':
                    Path(worker.state['decision_request']['economic_macro_source_fence']['file']).unlink()
                else:
                    worker.observe()
                    rid = worker.last_observation['capture_request_id']
                    path = worker.run / 'request-ledger' / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
                    receipt = runner.entry.read_json(path)
                    if mutation == 'attempted':
                        receipt['result']['input_attempted'] = True
                    elif mutation == 'pending':
                        receipt['result'] = None
                        control.write_json(worker.run / 'result.json', {'id': 'unrelated'})
                    else:
                        receipt['request']['handoff'] = True
                    control.write_json(path, receipt)
                try:
                    result = worker.execute_plan(reply)
                except (ValueError, FileNotFoundError):
                    result = None
                self.assertEqual(control.action_index, 0, result)

    def test_unknown_first_effect_keeps_original_pending_and_no_later_input(self):
        with self.worker(changes=[values(coins=62, xp=[45, 52])]) as (worker, control, frames):
            result = worker.execute_plan(self.reply(worker))
            self.assertEqual(control.action_index, 1, result.get('error'))
            pending = worker.economy_ledger('2-3')['pending']
            self.assertEqual(pending['request_id'], result['publications'][0]['request_id'])
            self.assertEqual(worker.state['decision_request']['kind'], 'economy_result')
            self.assertEqual(result['outcome'], 'original_effect_unresolved')
            self.assertEqual(result['effects_verified'], 0)
            self.assertEqual(result['navigation_attempts'], 0)

    def test_unknown_original_delivery_stops_without_later_observe_or_input(self):
        with self.worker() as (worker, control, frames):
            original_write = control.write_json
            def write(path, value):
                if Path(path) == worker.run / 'result.json' and value.get('input_attempted'):
                    # Known physical attempt, missing terminal completion.
                    value.update(ok=False, completed=[], attempted_actions=value['attempted_actions'])
                original_write(path, value)
            control.write_json = write
            result = worker.execute_plan(self.reply(worker))
            self.assertEqual(control.action_index, 1, result.get('error'))
            self.assertEqual(result['effects_verified'], 0)
            self.assertEqual(len(result['publications']), 1)
            rid = result['publications'][0]['request_id']
            self.assertEqual(worker.economy_ledger('2-3')['pending']['request_id'], rid)
            self.assertEqual(worker.state['decision_request']['kind'], 'economy_result')
            # Last original broker publication is the unknown physical input;
            # no successor observe/navigation can bury that receipt.
            self.assertEqual(control.published[-1]['id'], rid)

    def test_stop_and_epoch_race_at_original_last_publication_boundary(self):
        for race in ('stop', 'epoch', 'deadline'):
            with self.subTest(race=race), self.worker() as (worker, control, frames):
                original_write = control.write_json
                def write(path, value):
                    original_write(path, value)
                    if value.get('schema') == economy.MACRO_CONTRACT and value.get('economic_attempts') == 1:
                        if race == 'stop':
                            (worker.run / 'runner-stop').touch()
                        elif race == 'epoch':
                            worker.epoch = lambda: 'new-epoch'
                        else:
                            worker.economy_macro_active['monotonic_deadline'] = 0
                control.write_json = write
                result = worker.execute_plan(self.reply(worker))
                self.assertEqual(control.action_index, 0, result.get('error'))
                self.assertEqual(result['effects_verified'], 0)
                self.assertEqual(result['status'], 'consumed')
                self.assertIsNone(worker.economy_ledger('2-3')['pending'])

    def test_stop_during_publication_profile_io_still_precedes_real_publisher(self):
        with self.worker() as (worker, control, frames):
            original = worker.profile_span
            @contextlib.contextmanager
            def span(name, **kwargs):
                with original(name, **kwargs) as value:
                    if name == 'publish_request':
                        (worker.run / 'runner-stop').touch()
                    yield value
            worker.profile_span = span
            result = worker.execute_plan(self.reply(worker))
            self.assertEqual(control.action_index, 0, result.get('error'))
            self.assertEqual(result['economic_attempts'], 1)
            self.assertFalse(result['publications'][0]['publication_attempted'])
            self.assertEqual(result['gross_spent'], 0)
            self.assertIsNone(worker.economy_ledger('2-3')['pending'])

    def test_paid_refresh_cap_is_independent_of_gross_cap(self):
        with self.worker(dimensions='refresh') as (worker, control, frames):
            result = worker.execute_plan(self.reply(worker, max_paid_refreshes=0))
            self.assertEqual(control.action_index, 0, result.get('error'))
            self.assertIn('付费刷新', result.get('error', ''))
            self.assertEqual(result['gross_spent'], 0)
            self.assertNotIn('economy', worker.preparation_reviews)

    def test_free_refresh_consumes_action_cap_even_without_spend(self):
        with self.worker(dimensions='free_refresh', changes=[values(free_refreshes=1)]) as (worker, control, frames):
            result = worker.execute_plan(self.reply(worker, max_economic_actions=1, max_total_spent=0))
            self.assertEqual(control.action_index, 1, result.get('error'))
            self.assertEqual(result['economic_attempts'], 1)
            self.assertEqual(result['effects_verified'], 1)
            self.assertEqual(result['paid_refresh_attempts'], 0)
            self.assertEqual(result['gross_spent'], 0)
            self.assertNotEqual(result['outcome'], 'economy_complete')

    def test_each_asset_effect_requires_new_roster_navigation(self):
        with self.worker(hidden_board=True) as (worker, control, frames):
            result = worker.execute_plan(self.reply(worker, max_navigation_clicks=6))
            self.assertEqual(control.action_index, 2, result.get('error'))
            self.assertEqual(result['navigation_attempts'], 6)
            self.assertEqual(result['outcome'], 'economy_complete', result.get('error'))
            roster_sources = {child['request']['comparison_view']['roster_provenance']['capture_request_id']
                              for child in result['children'] if child['request'].get('comparison_view')}
            self.assertEqual(len(roster_sources), 3)

    def test_navigation_cap_blocks_hidden_board_without_faking_completion(self):
        with self.worker(hidden_board=True) as (worker, control, frames):
            result = worker.execute_plan(self.reply(worker))
            self.assertEqual(control.action_index, 0, result.get('error'))
            self.assertIn('导航上限', result.get('error', ''))
            self.assertEqual(result['navigation_attempts'], 0)
            self.assertNotIn('economy', worker.preparation_reviews)

    def test_navigation_pair_can_finish_only_with_fresh_model_completion(self):
        for dimensions, caps, expected in (('none', 2, 'economy_complete'), ('xp', 1, 'stopped'), ('xp', 2, 'stopped')):
            with self.subTest(dimensions=dimensions, caps=caps), self.worker(hidden_board=True, dimensions=dimensions) as (worker, control, frames):
                count = worker.state['statistics']['decisions']
                result = worker.execute_plan(self.reply(worker, max_total_spent=0, max_navigation_clicks=caps))
                self.assertEqual(result['navigation_attempts'], caps, result.get('error'))
                self.assertEqual(result['outcome'], expected, result.get('error'))
                self.assertEqual(control.action_index, 0)
                self.assertEqual(result['gross_spent'], 0)
                self.assertEqual(worker.state['statistics']['decisions'], count + 1)
                self.assertEqual(worker.preparation_reviews.get('economy', {}).get('completed'), True if dimensions == 'none' else None)
                self.assertNotIn('battle_acceptance', worker.preparation_reviews)

    def test_solver_expiry_and_active_reentry_produce_no_physical_input(self):
        for mutation in ('expire', 'reentry'):
            with self.subTest(mutation=mutation), self.worker() as (worker, control, frames):
                reply = self.reply(worker)
                original = worker.local_economic_allocation
                def model(request, observed):
                    if mutation == 'expire':
                        worker.economy_macro_active['monotonic_deadline'] = 0
                    else:
                        with self.assertRaises(ValueError):
                            worker.execute_plan(reply)
                    return original(request, observed)
                worker.local_economic_allocation = model
                result = worker.execute_plan(reply)
                self.assertEqual(control.action_index, 0 if mutation == 'expire' else 2, result.get('error'))

    def test_new_rewards_options_capacity_or_node_stop_before_later_input(self):
        for mutation in ('rewards', 'options', 'capacity', 'node', 'manual'):
            with self.subTest(mutation=mutation), self.worker() as (worker, control, frames):
                original_read = worker.perception.read
                def read(path, force=False, *, scope='full'):
                    observed = original_read(path, force, scope=scope)
                    if control.action_index:
                        semantic, snapshot = observed['semantic'], observed['snapshot_id']
                        if mutation == 'rewards':
                            semantic['rewards'] = {'snapshot_id': snapshot, 'targets': [{'kind': 'orb'}]}
                        elif mutation == 'options':
                            semantic['options'] = [{'title': 'new-choice'}]
                        elif mutation == 'capacity':
                            semantic['team']['capacity'] = {'snapshot_id': snapshot, 'overflow_count': 1}
                        elif mutation == 'node':
                            observed['fields']['stage'] = '2-4'
                        else:
                            (worker.run / 'runner-manual.json').write_text('{}', encoding='utf-8')
                    return observed
                worker.perception.read = read
                result = worker.execute_plan(self.reply(worker))
                self.assertEqual(control.action_index, 1, result.get('error'))
                self.assertEqual(result['navigation_attempts'], 0)
                self.assertNotEqual(result['outcome'], 'economy_complete')


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
    def worker(self, transitions=None, initial=None, *, joint_complete=True):
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
            def read(path, force=False, *, scope='full'):
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
            worker.preparation_reviews = {phase: {'completed': True}
                for phase in ('rewards', 'startup_guide', 'inventory_cleanup')}
            worker.live_mode = {'match_id': 'match', 'value': '标准博弈'}
            worker.knowledge = {'roles': {}, 'guide': {}, 'progression': {}}
            worker.history, worker.inspections, worker.inspection_attempted = {}, {}, set()
            worker.state['statistics']['decisions'] = 0
            worker.publish = lambda **changes: worker.state.update(changes)
            worker.log_events = []
            worker.log = worker.log_events.append
            if joint_complete:
                # These old synthetic receipt fixtures do not exercise model
                # budget acceptance. Real selected-model matching has separate
                # focused checks below, without this compatibility stub.
                worker.assert_model_budget = lambda *unused: None
                # These numeric fixtures isolate transaction/receipt invariants.
                # The default real model remains incomplete; its gate is tested
                # separately with joint_complete=False, never claimed game evidence.
                worker.local_economic_allocation = lambda request, observed: {
                    'origin': 'local_advisor_recomputation', 'request_id': request['request_id'],
                    'snapshot_id': observed['snapshot_id'], 'match_id': worker.active_match_id,
                    'resume_epoch': worker.epoch(), 'full_observation': True, 'source_verified': True,
                    'input_sha256': 'a' * 64,
                    'allocation': {'schema': 'currency-wars-economic-allocation/v1',
                        'snapshot_id': observed['snapshot_id'], 'binding': {'request_id': request['request_id'],
                            'snapshot_id': observed['snapshot_id'], 'match_id': worker.active_match_id, 'resume_epoch': worker.epoch()},
                        'status': 'conditional_joint_decision',
                        'complete_dimensions': {'purchase': True, 'experience': True, 'refresh': True}}}
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

    def test_final_reserve_uses_current_sourced_confirmation_without_fabricating_ocr(self):
        with self.worker() as (worker, record, control, frames):
            request = worker.state['decision_request']
            proof = {'source': 'supervisor_confirmation', 'reviewer': 'supervising_agent',
                'remaining_interest_rounds': 0, 'proof': copy.deepcopy(record['proof']),
                'request_id': request['request_id'], 'match_id': worker.active_match_id,
                'stage': '2-3', 'mode': '标准博弈', 'confirmation_source': 'user_confirmation',
                'reference': 'offline-fixture:current-user-confirmation',
                'statement': '离线合同样例：当前节点已明确为最后备战；此句不是游戏规则或实机证据'}
            record['value']['reserve'].update(coins=0, remaining_interest_rounds=0, rounds_evidence=proof)
            record['value']['budget']['experience'] = 66
            before = copy.deepcopy(worker.last_observation)
            published = len(control.published)
            for change in ({'request_id': 'old-request'}, {'match_id': 'other-match'}, {'stage': '3-7'},
                           {'mode': '超频博弈'}, {'reference': ''}, {'statement': ''},
                           {'confirmation_source': 'guess'}, {'source': 'unknown'},
                           {'proof': {**record['proof'], 'resume_epoch': 'old-other-epoch'}}):
                invalid = copy.deepcopy(record)
                invalid['value']['reserve']['rounds_evidence'].update(change)
                with self.subTest(change=change), self.assertRaises(ValueError):
                    worker.accept_economy_plan(invalid)
            invalid = copy.deepcopy(record)
            invalid['value']['reserve']['remaining_interest_rounds'] = 1
            invalid['value']['reserve']['rounds_evidence']['remaining_interest_rounds'] = 1
            with self.assertRaisesRegex(ValueError, '末关确认'):
                worker.accept_economy_plan(invalid)
            worker.accept_economy_plan(record)
            self.assertEqual(worker.economy_ledger('2-3')['policy']['reserve']['rounds_evidence'], proof)
            self.assertEqual(worker.last_observation, before)
            self.assertEqual(len(control.published), published)
            self.assertEqual(worker.log_events[-1]['interest_source'], 'supervisor_confirmation')
            self.assertEqual(worker.log_events[-1]['reserve_coins'], 0)

    def test_screen_interest_proof_still_requires_current_reading_and_confidence(self):
        with self.worker() as (worker, record, control, frames):
            row = {'text': '剩余0次结息', 'confidence': .89, 'box': [100, 100, 280, 140]}
            source = worker.history[record['proof']['snapshot_id']]
            source['rows'].append(row)
            record['value']['reserve'].update(coins=0, remaining_interest_rounds=0,
                rounds_evidence={'remaining_interest_rounds': 0, 'proof': copy.deepcopy(record['proof']),
                    'reading': row['text'], 'bounds': row['box']})
            with self.assertRaisesRegex(ValueError, '屏幕结息来源'):
                worker.accept_economy_plan(record)
            row['confidence'] = .99
            worker.accept_economy_plan(record)
            self.assertEqual(worker.log_events[-1]['interest_source'], 'observed_screen')

    def test_command_refuses_observed_page_mismatch_before_entry_publication(self):
        with self.worker() as (worker, record, control, frames):
            published = len(control.published)
            for expected, action in (('preparation', None),
                                     ('shop', {'type': 'click_point', 'expected_page': 'preparation'})):
                with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, '前置页面'):
                    worker.command(['click:490:500'], '旧领奖位置可能已成为商店牌', expected, action=action)
            self.assertEqual(len(control.published), published)

    def test_click_text_refuses_old_snapshot_capture_or_stage_without_replaying(self):
        with self.worker() as (worker, record, control, frames):
            published = len(control.published)
            for changes in ({'snapshot_id': '0' * 64}, {'page': 'preparation'},
                            {'capture_request_id': 'old-observe'}, {'frame_id': 'old-frame'},
                            {'fields': {**worker.last_observation['fields'], 'stage': '3-7'}}):
                old = {**worker.last_observation, **changes}
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '旧帧/页面/节点'):
                    worker.click_text(old, '刷新', '旧文字ROI不可继续输入')
            self.assertEqual(len(control.published), published)

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
