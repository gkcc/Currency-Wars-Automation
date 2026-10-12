"""Focused six-D offline replay checks; no real controller or extra OCR run."""
import copy
import json
from pathlib import Path
import tempfile
from unittest import TestCase

import replay_currency_wars_refresh as replay


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / 'handoff' / '2026-10-07' / 'refresh-sequence'


class RefreshReplayTests(TestCase):
    @classmethod
    def setUpClass(cls):
        replay.configure(ROOT)
        cls.manifest, cls.payloads, cls.digest = replay.load_evidence(EVIDENCE)

    def test_six_d_current_worker_has_one_budget_request_and_twelve_actual_fixture_spent(self):
        report = replay.replay_protocol(self.manifest)
        self.assertEqual((report['normal_root_requests'], report['exception_root_requests']), (1, 0))
        self.assertEqual(report['spent'], {'purchase': 0, 'refresh': 12, 'experience': 0})
        self.assertEqual(report['paid_refreshes'], 6)
        self.assertFalse(report['pending'])
        self.assertTrue(report['economy_completed'])
        self.assertEqual(report['next_phase'], 'lineup_equipment')
        self.assertFalse(report['battle_ready'])
        self.assertEqual(len(report['effects']), 6)
        self.assertEqual([event['observed_spent'] for event in report['effects']], [2] * 6)
        self.assertTrue(all(event['input_resent'] is False for event in report['effects']))
        self.assertEqual([row['historical_completed'] for row in report['correlations']],
                         [row['receipt']['completed'] for row in self.manifest['receipts']])
        self.assertTrue(all(row['origin'] == 'inert_protocol_adapter' for row in report['correlations']))
        self.assertTrue(all(row['generated_frame_is_historical_png'] is False for row in report['correlations']))
        self.assertIsNone(report['timing']['game_animation_wait'])
        self.assertIsNone(report['timing']['supervisor_wait'])
        self.assertAlmostEqual(report['timing']['active_total'], sum(report['timing']['exclusive_totals'].values()))

    def test_archival_wait_is_never_rewritten_as_a_current_protocol_receipt(self):
        before = copy.deepcopy(self.manifest)
        audited = replay.receipt_audit(self.manifest)
        self.assertEqual(self.manifest, before)
        self.assertEqual(len(audited), 6)
        self.assertTrue(all(row['exact_current_protocol_delivery']['unknown_input'] for row in audited))
        self.assertTrue(all(row['exact_current_protocol_delivery']['state'] == 'unknown' for row in audited))
        for row in audited:
            self.assertEqual(row['original_receipt']['completed'][1]['args'], [.4])
            self.assertEqual(row['hypothetical_current_request_actions'][1]['args'], [.7])
            self.assertFalse(row['live_authorization'])

    def test_original_wait_mismatch_stops_first_transaction_without_erasing_observed_charge(self):
        report = replay.replay_protocol(self.manifest, original_completed=True)
        self.assertEqual(report['simulated_input_count'], 1)
        self.assertEqual(report['observed_coin_change'], -2)
        self.assertEqual(report['observed_coin_spent'], 2)
        self.assertEqual(report['spent']['refresh'], 0)
        self.assertTrue(report['pending'])
        self.assertEqual((report['normal_root_requests'], report['exception_root_requests']), (1, 1))
        self.assertTrue(report['no_replay_on_next_advance'])
        self.assertFalse(report['economy_completed'])
        self.assertEqual(report['next_phase'], 'economy')
        self.assertEqual(report['correlations'][0]['historical_completed'],
                         report['correlations'][0]['generated_completed'])
        self.assertEqual(report['correlations'][0]['generated_completed'][1]['args'], [.4])
        self.assertEqual(report['correlations'][0]['requested_actions'][1]['args'], [.7])

    def test_cached_after_and_receipt_cannot_approve_mismatched_original_completed(self):
        report = replay.replay_protocol(self.manifest, original_completed=True)
        ack = report['cached_acknowledgement']
        self.assertTrue(ack['attempted'])
        self.assertEqual(ack['entrypoint'], 'Worker.accept_economy_plan')
        self.assertTrue(ack['cached_after_png_present'])
        self.assertTrue(ack['cached_receipt_present'])
        self.assertEqual(ack['original_completed'][1]['args'], [.4])
        self.assertEqual(ack['requested_actions'][1]['args'], [.7])
        self.assertTrue(ack['rejected'])
        self.assertIn('原completed', ack['reason'])
        self.assertEqual(ack['observed_coin_change'], -2)
        self.assertEqual(ack['formally_reconciled_spent']['refresh'], 0)
        self.assertEqual(ack['pending_request_before'], ack['pending_request_after'])
        self.assertTrue(ack['in_memory_ledger_unchanged'])
        self.assertTrue(ack['durable_ledger_unchanged'])
        self.assertTrue(ack['no_new_publication'])

    def test_tampered_png_refuses_before_any_replay_or_source_field_reuse(self):
        with tempfile.TemporaryDirectory(prefix='currency-refresh-evidence-test-') as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
            (root / self.manifest['frames'][0]['file']).write_bytes(self.payloads[0] + b'changed')
            with self.assertRaisesRegex(ValueError, 'PNG hash mismatch'):
                replay.load_evidence(root)
