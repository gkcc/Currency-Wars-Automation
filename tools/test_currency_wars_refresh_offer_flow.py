"""Typed refresh consumer contracts on generated, inert Worker/Entry fixtures.

No retained gameplay sequence or native OCR confidence is supplied here.
``consume_offer`` is an explicit protocol boundary: its declared outputs and
rejections exercise the economic consumer. Real widget geometry, OCR and source
validation belong to the separate refresh reader tests.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from PIL import ImageDraw

import currency_wars_economy as economy
import currency_wars_refresh_offer as refresh_reader
import currency_wars_runner as runner
import test_currency_wars_economy as contracts


def free(count):
    return {'mode': 'free', 'free_remaining': count, 'paid_cost': None}


def paid(cost):
    return {'mode': 'paid', 'free_remaining': None, 'paid_cost': cost}


def key_inputs(control):
    return [action['args'] for request in control.published
            for action in request.get('actions', []) if action['type'] == 'key']


class RefreshOfferFlowTests(TestCase):
    @contextlib.contextmanager
    def worker(self, offers, *, coins=None, legacy=False, hidden_player=False):
        """Use existing real transaction guards with explicitly declared pixels.

        A marker separates successive generated frames even for zero-cost or
        zero-effect steps. It is not a game widget. The inherited numeric reader
        is also a protocol fixture, not an OCR measurement.
        """
        coins = coins or [70] * len(offers)
        self.assertEqual(len(coins), len(offers))
        legacy_cost = offers[0]['paid_cost'] if legacy and offers[0] and offers[0]['mode'] == 'paid' else 2
        states = [contracts.values(coins=amount, level=8, xp=[0, 72],
                                  free_refreshes=2, refresh_cost=legacy_cost, protocol_step=index)
                  for index, amount in enumerate(coins)]
        original_numeric = contracts.numeric_frame

        def numeric_frame(state):
            image, unused, fields = original_numeric(state)
            ImageDraw.Draw(image).text((1100, 40), 'protocol frame ' + str(state['protocol_step']),
                                       fill='white')
            output = io.BytesIO()
            image.save(output, format='PNG')
            return image, output.getvalue(), fields

        with patch.object(contracts, 'numeric_frame', side_effect=numeric_frame):
            with contracts.EconomyTests().worker(states[1:], initial=states[0]) as fixture:
                worker, record, control, frames = fixture
                digests = [hashlib.sha256(frame[1]).hexdigest() for frame in frames]
                pixels = {digest: hashlib.sha256(frame[0].convert('RGB').tobytes()).hexdigest()
                          for digest, frame in zip(digests, frames)}
                by_digest = dict(zip(digests, offers))
                calls = []

                def declared_offer(digest, value):
                    return {'source': 'declared_refresh_protocol_fixture',
                            'snapshot_id': digest,
                            'mode': value['mode'] if value else 'unknown',
                            'protocol_value': copy.deepcopy(value)}

                original_read = worker.perception.read

                def read(path, force=False, *, scope='full', reuse_primary=False):
                    observed = original_read(path, force=force, scope=scope)
                    digest = observed['snapshot_id']
                    observed['semantic']['refresh_offer'] = declared_offer(digest, by_digest[digest])
                    observed['semantic']['team'] = {'checked': False, 'fully_read': False, 'units': []}
                    if hidden_player:
                        observed['fields']['level'] = None
                        observed['fields']['deployed'] = None
                    # Keep this fixture's existing full-observation contract.
                    # Real scoped/full reading is covered by reader tests.
                    return observed

                def consume(offer, image, snapshot_id, page):
                    calls.append({'offer': copy.deepcopy(offer), 'snapshot_id': snapshot_id, 'page': page})
                    if (not isinstance(offer, dict)
                            or offer.get('source') != 'declared_refresh_protocol_fixture'
                            or offer.get('snapshot_id') != snapshot_id
                            or pixels.get(snapshot_id) != hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()):
                        raise ValueError('declared refresh protocol source rejection')
                    if page != 'shop':
                        return None
                    return copy.deepcopy(offer['protocol_value'])

                with patch.object(worker.perception, 'read', side_effect=read), \
                        patch.object(refresh_reader, 'consume_offer', side_effect=consume):
                    worker.observe()
                    worker.state['decision_request'] = None
                    worker.ask(worker.last_observation, 'shop_strategy', '显式刷新协议预算入口；非真实策略记录')
                    request = worker.state['decision_request']
                    record['proof'].update(snapshot_id=request['snapshot_id'],
                                           evidence_file=request['evidence_file'], resume_epoch=worker.epoch())
                    record['value']['budget']['experience'] = 0
                    if not legacy:
                        for key in ('free_refreshes', 'refresh_cost'):
                            record['value']['fields'].pop(key)
                    if hidden_player:
                        record['value']['fields'] = {'coins': record['value']['fields']['coins']}
                    worker.strategy_reads['guide'] = {'match_id': worker.active_match_id,
                        'resume_epoch': worker.epoch(), 'observed_at': runner.now(),
                        'snapshot_id': worker.last_observation['snapshot_id'],
                        'value': {'applied': True, 'body_read': True, 'mode_label': '标准博弈',
                                  'body_lines': ['后期：8级搜牌'], 'operating_rules': {}}}
                    yield SimpleNamespace(worker=worker, record=record, control=control, frames=frames,
                                          digests=digests, calls=calls, declared_offer=declared_offer)

    @staticmethod
    def search_budget(record, amount, count):
        value = record['value']
        value['targets'] = [{'name': '飞霄', 'copies': 1, 'critical': False,
                             'reason': '协议前提：主管已明确的搜牌目标'}]
        value['budget'] = {'purchase': 5, 'refresh': amount, 'experience': 0}
        value['paid_search'].update(max_refreshes=count, targets=['飞霄'], purchase_reserve=5,
                                    reason='协议前提：本节点已核预算与搜牌停止条件')

    @staticmethod
    def resolution(fixture, pending_id):
        worker = fixture.worker
        request = worker.state['decision_request']
        result = copy.deepcopy(fixture.record)
        result['proof'].update(snapshot_id=request['snapshot_id'], evidence_file=request['evidence_file'],
                               resume_epoch=worker.epoch())
        result['value'].update(resolve_request_id=pending_id, revision=2)
        # The numeric fields are explicitly re-bound to the current generated
        # frame; a typed mode is never smuggled in as a numeric zero.
        result['value']['fields'] = {name: fixture.frames[1][2][name]
                                     for name in fixture.record['value']['fields']}
        return result

    def test_free_counts_and_changed_paid_prices_use_each_original_transaction_cost(self):
        offers = [free(2), free(1), paid(2), paid(3), paid(3)]
        with self.worker(offers, coins=[70, 70, 70, 68, 65]) as fixture:
            worker, control = fixture.worker, fixture.control
            self.search_budget(fixture.record, 5, 2)
            worker.accept_economy_plan(fixture.record)
            transactions = []
            original_apply = worker.apply_economy_result

            def apply(ledger, pending, result, **kwargs):
                transactions.append({'cost': pending['cost'], 'offer': copy.deepcopy(
                    pending['before']['values']['refresh_offer']), 'outcome': result['outcome']})
                self.assertNotIn('free_refreshes', pending['before']['values'])
                return original_apply(ledger, pending, result, **kwargs)

            with patch.object(worker, 'apply_economy_result', side_effect=apply):
                worker.advance_economy(worker.last_observation)
            self.assertEqual([item['cost'] for item in transactions], [0, 0, 2, 3])
            self.assertEqual([item['offer'] for item in transactions], offers[:-1])
            self.assertEqual([item['outcome'] for item in transactions], ['success'] * 4)
            self.assertEqual(key_inputs(control), [[68.0]] * 4)
            ledger = worker.economy_ledger('2-3')
            self.assertEqual(ledger['spent'], {'purchase': 0, 'refresh': 5, 'experience': 0})
            self.assertEqual(ledger['paid_refreshes'], 2)
            self.assertIsNone(ledger['pending'])
            current = worker.economy_observation(worker.last_observation)['values']
            self.assertEqual(economy.refresh_offer(current), paid(3))
            self.assertNotIn('free_refreshes', current)
            self.assertIsNone(current['refresh_offer']['free_remaining'])

    def test_typed_unknown_blocks_legacy_and_changed_same_frame_offer_invalidates_cache(self):
        with self.worker([None], legacy=True) as fixture:
            worker, control = fixture.worker, fixture.control
            worker.accept_economy_plan(fixture.record)
            unknown = worker.economic_policy(worker.last_observation)
            self.assertIsNone(economy.refresh_offer(unknown['observation']['values']))
            self.assertEqual(unknown['actions'], [])
            worker.advance_economy(worker.last_observation)
            self.assertEqual(key_inputs(control), [])
            # Same PNG, but newly supplied semantic evidence: neither a known
            # nor a subsequently rejected offer may hit the previous cache.
            actual = worker.last_observation
            actual['semantic']['refresh_offer'] = fixture.declared_offer(actual['snapshot_id'], free(2))
            known = worker.economic_policy(actual)
            self.assertEqual(economy.refresh_offer(known['observation']['values']), free(2))
            self.assertEqual(known['actions'][0]['args'], [68])
            before = len(fixture.calls)
            actual['semantic']['refresh_offer'] = fixture.declared_offer(actual['snapshot_id'], None)
            rejected = worker.economic_policy(actual)
            self.assertGreater(len(fixture.calls), before)
            self.assertIsNone(rejected['observation']['values']['refresh_offer'])
            self.assertEqual(rejected['actions'], [])
            self.assertEqual(key_inputs(control), [])

        with self.subTest(case='new_contract_missing_offer'), self.worker([free(2)], legacy=True) as fixture:
            worker, control = fixture.worker, fixture.control
            actual = worker.last_observation
            source = worker.history[fixture.record['proof']['snapshot_id']]
            for observed in (source, actual):
                observed['read_contract'] = {'version': 2, 'requested_scope': 'full',
                                             'effective_scope': 'full', 'unread': []}
                observed['semantic'].pop('refresh_offer', None)
            self.assertEqual(fixture.record['value']['fields']['free_refreshes']['value'], 2)
            self.assertEqual(fixture.record['value']['fields']['refresh_cost']['value'], 2)
            worker.accept_economy_plan(fixture.record)
            policy = worker.economic_policy(actual)
            self.assertIsNone(policy['observation']['values']['refresh_offer'])
            self.assertNotIn('free_refreshes', policy['observation']['values'])
            self.assertNotIn('refresh_cost', policy['observation']['values'])
            self.assertEqual(policy['actions'], [])
            published = len(control.published)
            worker.advance_economy(actual)
            self.assertEqual(len(control.published), published)
            self.assertEqual(key_inputs(control), [])

        with self.subTest(case='changed_png_before_cache_hit'), self.worker([free(2), None]) as fixture:
            worker, control = fixture.worker, fixture.control
            worker.accept_economy_plan(fixture.record)
            worker.advance_economy(worker.last_observation)
            ledger = worker.economy_ledger('2-3')
            pending = copy.deepcopy(ledger['pending'])
            self.assertIsNotNone(pending)
            actual = worker.last_observation
            actual['semantic']['refresh_offer'] = fixture.declared_offer(actual['snapshot_id'], free(1))
            known = worker.economic_policy(actual)
            self.assertTrue(known['available'])
            self.assertEqual(known['observation']['values']['refresh_offer'], free(1))
            snapshot_id = actual['snapshot_id']
            offer = copy.deepcopy(actual['semantic']['refresh_offer'])
            published = len(control.published)
            keys = key_inputs(control)
            # Keep every cache-key fact unchanged while replacing only the
            # current immutable frame bytes; a cache cannot waive this check.
            worker.frame_path.write_bytes(fixture.frames[0][1])
            refused = worker.economic_policy(actual)
            self.assertFalse(refused['available'])
            self.assertEqual(refused['actions'], [])
            worker.advance_economy(actual)
            self.assertEqual(actual['snapshot_id'], snapshot_id)
            self.assertEqual(actual['semantic']['refresh_offer'], offer)
            self.assertEqual(ledger['pending'], pending)
            self.assertEqual(ledger['spent']['refresh'], 0)
            self.assertEqual(len(control.published), published)
            self.assertEqual(key_inputs(control), keys)

    def test_free_refresh_does_not_require_level_experience_or_team(self):
        with self.worker([free(1), paid(2)], hidden_player=True) as fixture:
            worker = fixture.worker
            worker.accept_economy_plan(fixture.record)
            policy = worker.economic_policy(worker.last_observation)
            required = economy.required_fields('refresh', policy['observation']['values'])
            self.assertFalse(required & {'level', 'xp', 'xp_cost', 'xp_gain', 'team',
                                         'free_refreshes', 'refresh_cost'})
            self.assertNotIn('level', policy['observation']['values'])
            self.assertFalse(worker.last_observation['semantic']['team']['checked'])
            self.assertEqual(worker.require_economic_action(policy['actions'][0], worker.last_observation)['cost'], 0)
            worker.advance_economy(worker.last_observation)
            self.assertEqual(key_inputs(fixture.control), [[68.0]])
            self.assertIsNone(worker.economy_ledger('2-3')['pending'])
            self.assertEqual(worker.economy_ledger('2-3')['spent']['refresh'], 0)
            self.assertIsNone(worker.economy_observation(worker.last_observation)['values']['refresh_offer']['free_remaining'])

    def test_current_paid_price_over_budget_publishes_no_action(self):
        with self.worker([paid(3)], legacy=True) as fixture:
            worker, control = fixture.worker, fixture.control
            self.search_budget(fixture.record, 2, 1)
            worker.accept_economy_plan(fixture.record)
            policy = worker.economic_policy(worker.last_observation)
            self.assertEqual(economy.refresh_cost(policy['observation']['values']), 3)
            self.assertEqual(policy['actions'], [])
            published = len(control.published)
            with self.assertRaises(ValueError):
                worker.require_economic_action({'type': 'key', 'args': [68], 'expected_page': 'shop'},
                                               worker.last_observation)
            worker.advance_economy(worker.last_observation)
            self.assertEqual(len(control.published), published)
            self.assertEqual(key_inputs(control), [])
            self.assertEqual(worker.economy_ledger('2-3')['spent']['refresh'], 0)

    def test_zero_or_unknown_result_keeps_original_pending_without_resending(self):
        for after, expected in ((paid(2), 'zero'), (None, 'unknown')):
            with self.subTest(outcome=expected), self.worker([paid(2), after]) as fixture:
                worker, control = fixture.worker, fixture.control
                self.search_budget(fixture.record, 2, 1)
                worker.accept_economy_plan(fixture.record)
                worker.advance_economy(worker.last_observation)
                ledger = worker.economy_ledger('2-3')
                pending = copy.deepcopy(ledger['pending'])
                self.assertIsNotNone(pending)
                self.assertEqual(pending['cost'], 2)
                self.assertEqual(worker.state['decision_request']['kind'], 'economy_result')
                outcomes = [event['outcome'] for event in worker.log_events
                            if event.get('event') == 'economic_effect_verified']
                self.assertEqual(outcomes[-1], expected)
                published = len(control.published)
                worker.advance_economy(worker.last_observation)
                self.assertEqual(ledger['pending'], pending)
                self.assertEqual(len(control.published), published)
                self.assertEqual(key_inputs(control), [[68.0]])
                self.assertEqual(ledger['spent']['refresh'], 0)

    def test_epoch_page_and_current_png_mismatch_prevent_refresh_publication(self):
        for mismatch in ('epoch', 'page', 'png'):
            with self.subTest(mismatch=mismatch), self.worker([free(2)]) as fixture:
                worker, control = fixture.worker, fixture.control
                worker.accept_economy_plan(fixture.record)
                if mismatch == 'epoch':
                    worker.epoch = lambda: 'different-epoch'
                elif mismatch == 'page':
                    worker.last_observation['page'] = 'preparation'
                else:
                    worker.last_observation['snapshot_id'] = 'f' * 64
                published = len(control.published)
                policy = worker.economic_policy(worker.last_observation)
                self.assertEqual(policy['actions'], [])
                worker.advance_economy(worker.last_observation)
                self.assertEqual(len(control.published), published)
                self.assertEqual(key_inputs(control), [])

    def test_original_group_completed_and_missing_identity_are_separate_rejections(self):
        directory = Path(__file__).resolve().parent.parent / 'handoff' / '2026-10-07' / 'free-refresh-sequence'
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
        groups = {item['file']: item for item in manifest['action_groups']}
        for filename in ('f01-group-receipt.json', 'f02-group-receipt.json'):
            with self.subTest(group=filename), self.worker([free(2), None]) as fixture:
                worker, control = fixture.worker, fixture.control
                archived = json.loads((directory / filename).read_text(encoding='utf8'))
                self.assertTrue(archived['projection_only'])
                worker.accept_economy_plan(fixture.record)
                worker.advance_economy(worker.last_observation)
                ledger = worker.economy_ledger('2-3')
                pending = copy.deepcopy(ledger['pending'])
                path = worker.run / 'request-ledger' / (hashlib.sha256(pending['request_id'].encode()).hexdigest() + '.json')
                receipt = json.loads(path.read_text(encoding='utf8'))
                # Only the original completed list is transplanted unchanged.
                # This generated transport identity and generated post-frame
                # isolate the action mismatch; they do not restore the old
                # receipt's missing immutable capture identity.
                receipt['result']['completed'] = copy.deepcopy(archived['completed'])
                self.assertNotEqual(receipt['result']['completed'], pending['broker_actions'])
                control.write_json(path, receipt)
                actual = worker.last_observation
                actual['semantic']['refresh_offer'] = fixture.declared_offer(actual['snapshot_id'], free(1))
                published = len(control.published)
                with self.assertRaises(ValueError):
                    worker.accept_economy_plan(self.resolution(fixture, pending['request_id']))
                self.assertEqual(json.loads(path.read_text(encoding='utf8'))['result']['completed'], archived['completed'])
                self.assertEqual(ledger['pending'], pending)
                self.assertEqual(ledger['spent']['refresh'], 0)
                # Independently submit the unaugmented old projection to the
                # actual frame validator. No synthetic identity is added here.
                self.assertNotIn('observation', archived)
                self.assertEqual(groups[filename]['original_receipt_snapshot_basename'], 'game-preview.png')
                self.assertFalse(groups[filename]['original_receipt_snapshot_exists_now'])
                with self.assertRaises(runner.entry.ObservationUnavailable):
                    runner.entry.observation_frame(worker.run, archived)
                self.assertEqual(len(control.published), published)
                self.assertEqual(key_inputs(control), [[68.0]])

    def test_pending_restore_uses_archived_typed_frame_and_rejects_changed_archive(self):
        for damage in (None, 'png', 'offer_source'):
            with self.subTest(damage=damage), self.worker([free(2), None]) as fixture:
                worker, control = fixture.worker, fixture.control
                worker.accept_economy_plan(fixture.record)
                worker.advance_economy(worker.last_observation)
                ledger = worker.economy_ledger('2-3')
                if damage == 'offer_source':
                    # The protocol consumer declares a source rejection. The
                    # Worker may not replace it with same-PNG current facts.
                    ledger['pending']['after_refresh_offer']['snapshot_id'] = 'f' * 64
                pending = copy.deepcopy(ledger['pending'])
                self.assertEqual(pending['after_snapshot_id'], fixture.digests[1])
                self.assertEqual(pending['after_page'], 'shop')
                self.assertIn('after_refresh_offer', pending)
                actual = worker.last_observation
                actual['semantic']['refresh_offer'] = fixture.declared_offer(actual['snapshot_id'], free(1))
                resolution = self.resolution(fixture, pending['request_id'])
                published = len(control.published)
                if damage == 'png':
                    Path(pending['after_png']).write_bytes(fixture.frames[0][1])
                    with self.assertRaisesRegex(ValueError, '后帧身份'):
                        worker.accept_economy_plan(resolution)
                    self.assertEqual(ledger['pending'], pending)
                elif damage == 'offer_source':
                    with self.assertRaises(ValueError):
                        worker.accept_economy_plan(resolution)
                    self.assertEqual(ledger['pending'], pending)
                else:
                    worker.accept_economy_plan(resolution)
                    self.assertIsNone(ledger['pending'])
                    persisted = json.loads(worker.economy_ledger_path('2-3').read_text(encoding='utf8'))
                    self.assertIsNone(persisted['ledger']['pending'])
                    self.assertEqual(persisted['ledger']['spent']['refresh'], 0)
                self.assertEqual(ledger['paid_refreshes'], 0)
                self.assertEqual(len(control.published), published)
                self.assertEqual(key_inputs(control), [[68.0]])


if __name__ == '__main__':
    import unittest
    unittest.main()
