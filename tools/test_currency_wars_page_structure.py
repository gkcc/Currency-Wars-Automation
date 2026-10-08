"""Retained native OCR rows plus separately labelled inert Worker protocols.

The original UID-bearing PNG is not available here. No OCR engine, capture,
input, broker, or GUI is initialized by these cases.
"""
import copy
import hashlib
import json
from pathlib import Path
import time
from unittest import TestCase
from unittest.mock import Mock, patch

import currency_wars_perception as perception
import currency_wars_runner as runner


EVIDENCE_PATH = 'handoff/2026-10-08/ROOT_PR19_ADVANTAGES_MISCLASSIFICATION.json'
EVIDENCE_COMMIT = '16b8b96b06ab2e6e8073ca32fa4a8268e2287069'
EVIDENCE_BLOB = '0a1f2d85ff581f618602c721420c9d3e75d9b647'


def retained():
    return json.loads((Path(__file__).resolve().parents[1] / EVIDENCE_PATH).read_text(encoding='utf-8'))


def hud_rows():
    # Explicit protocol coordinates for the existing HUD contract, not a PNG.
    return [dict(text=text, confidence=.99, box=box) for text, box in (
        ('1-3', [700, 10, 740, 35]), ('84%', [770, 10, 810, 35]),
        ('10', [1000, 10, 1040, 35]), ('总伤害', [1790, 85, 1890, 115]))]


def broken(rows, index, kind):
    rows = copy.deepcopy(rows)
    if kind == 'missing':
        rows.pop(index)
    elif kind == 'moved':
        box = rows[index]['box']
        rows[index]['box'] = [box[0], box[1] + 400, box[2], box[3] + 400]
    elif kind == 'low_confidence':
        rows[index]['confidence'] = .89
    elif kind == 'duplicate':
        rows.append(copy.deepcopy(rows[index]))
    return rows


class PageStructureTests(TestCase):
    def test_retained_ocr_record_uses_native_page_before_skill_paragraph(self):
        path = Path(__file__).resolve().parents[1] / EVIDENCE_PATH
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(), EVIDENCE_BLOB)
        evidence = retained()
        self.assertFalse(evidence['raw_snapshot_public'])
        self.assertEqual(evidence['page_reader'], 'battle')
        self.assertEqual(evidence['receipt_id'], 'b71a7bf33f6840ebb7e5fea80f2519e3')
        self.assertEqual(evidence['snapshot_sha256'], '25632a83eebdee01c30cfe1b5ee3b75fd8e3f69e213c8b1de0895e7181910575')
        rows = evidence['rows']
        before = copy.deepcopy(rows)
        self.assertTrue(perception._native_advantages_page(rows))
        self.assertIsNone(perception._native_battle_stage(rows))
        self.assertEqual(perception.classify(rows), 'advantages')
        self.assertEqual(rows, before)

    def test_each_advantages_anchor_still_needs_unique_current_geometry_and_confidence(self):
        for index in range(4):
            for kind in ('missing', 'moved', 'low_confidence', 'duplicate'):
                with self.subTest(anchor=index, variant=kind):
                    rows = broken(retained()['rows'], index, kind)
                    self.assertFalse(perception._native_advantages_page(rows))
                    self.assertEqual(perception.classify(rows), 'unknown')

    def test_skill_battle_word_alone_is_not_a_battle_surface(self):
        rows = [row for row in retained()['rows'] if '战斗中' in row['text']]
        self.assertEqual(len(rows), 1)
        self.assertEqual(perception.classify(rows), 'unknown')

    def test_battle_requires_existing_four_fixed_native_hud_anchors(self):
        rows = hud_rows()
        self.assertEqual(perception._native_battle_stage(rows), '1-3')
        self.assertEqual(perception.classify(rows), 'battle')
        for index in range(4):
            for kind in ('missing', 'moved', 'low_confidence', 'duplicate'):
                with self.subTest(anchor=index, variant=kind):
                    partial = broken(rows, index, kind)
                    partial += [row for row in retained()['rows'] if '战斗中' in row['text']]
                    self.assertIsNone(perception._native_battle_stage(partial))
                    self.assertEqual(perception.classify(partial), 'unknown')

    def test_modals_and_formal_result_keep_priority_over_background_advantages(self):
        cases = (
            ('reward_overlay', [('获得奖励', [800, 300, 1000, 350])]),
            ('update_notice', [
                ('货币战争·零和博奔赛季扩充说明V4.4', [452, 251, 1048, 291]),
                ('详情', [1300, 260, 1349, 287]), ('扩充内容概览', [456, 737, 613, 765])]),
            ('settlement_grade', [(word, [700, 300, 900, 350])
                for word in ('对局评价', '当前职级', '职级晋升', '下一步')]),
        )
        for expected, labels in cases:
            with self.subTest(page=expected):
                rows = retained()['rows'] + [dict(text=text, box=box, confidence=.99) for text, box in labels]
                self.assertEqual(perception.classify(rows), expected)


class PageRoutingTests(TestCase):
    def worker(self):
        worker = object.__new__(runner.Worker)
        worker.consume_manual_stage_bridge = lambda observed: False
        worker.pending_reward_step = lambda observed: None
        worker.node_guard = lambda observed: True
        worker.wait_page, worker.wait_started = None, 0
        worker.panel_index = next(i for i, item in enumerate(runner.PANELS) if item[0] == 'advantages')
        worker.panel_state, worker.claim_count = 'inspect', 0
        worker.business_needs_review = False
        worker.ask = Mock()
        worker.publish = Mock()
        worker.command = Mock(side_effect=AssertionError('inert routing must not issue input'))
        return worker

    def observed(self):
        rows = retained()['rows']
        return {'page': perception.classify(rows), 'rows': rows}

    def test_cross_lease_worker_requests_business_resume_from_current_advantages(self):
        worker = self.worker()
        worker.business_needs_review = True
        worker.business_path = Path('inert-checkpoint')
        worker.business_previous_run, worker.business_previous_chat = 'old-run', 'old-chat'
        worker.owner = {'chat_id': 'current-chat'}
        worker.business_unknown = [{'outcome': 'unknown'}]
        worker.business = {'status': 'active'}
        worker.business_previous_observation = {'reference': 'inert-prior-source'}
        worker.deadline = time.monotonic() + 60
        with patch.object(runner.time, 'sleep', side_effect=AssertionError('a recognized page must not idle')):
            worker.tick(self.observed())
        self.assertEqual(worker.ask.call_args.args[1], 'business_resume')
        self.assertEqual(worker.ask.call_args.kwargs['choices']['unresolved_requests'], worker.business_unknown)
        worker.command.assert_not_called()
        worker.publish.assert_not_called()

    def test_current_panel_requests_inspection_without_claiming_complete_or_clicking(self):
        worker = self.worker()
        worker.tick(self.observed())
        self.assertEqual(worker.ask.call_args.args[1], 'post_match_advantages')
        worker.command.assert_not_called()
        self.assertEqual(worker.panel_state, 'inspect')

    def test_out_of_sequence_advantages_returns_review_instead_of_inventing_panel_completion(self):
        worker = self.worker()
        worker.panel_index = 0
        worker.tick(self.observed())
        self.assertEqual(worker.ask.call_args.args[1], 'unknown_page')
        self.assertEqual(worker.panel_index, 0)
        worker.command.assert_not_called()
