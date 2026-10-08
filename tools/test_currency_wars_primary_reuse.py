"""Same-PNG primary OCR evidence reuse with declared, non-model OCR output.

These tests run real Perception routing on generated PNGs. OCR and side-reader
outputs are explicit fixtures; they do not establish recognition accuracy,
game latency or the effect of any input. No controller or capture is used.
"""
from __future__ import annotations

import contextlib
import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

from PIL import Image

import currency_wars_perception as perception
from test_currency_wars_perception_scope import declared_reader


def declared_raw(text, bounds):
    left, top, right, bottom = bounds
    return ([[left / 1.5, top / 1.5], [right / 1.5, top / 1.5],
             [right / 1.5, bottom / 1.5], [left / 1.5, bottom / 1.5]], text, .99)


class RecordingEngine:
    """Expose the actual RapidOCR contract fields without loading a model."""

    def __init__(self, delegate, *, replacement=None, additions=()):
        self.delegate = delegate
        self.replacement, self.additions = replacement, list(additions)
        self.use_det = self.use_rec = True
        self.text_score = .5
        self.text_det = SimpleNamespace(postprocess_op=SimpleNamespace(box_thresh=.5, unclip_ratio=1.6))
        self.primary_calls = 0
        self.last_raw = None

    def __call__(self, array, **kwargs):
        raw, timing = self.delegate(array, **kwargs)
        if array.shape[:2] != (720, 1280):
            return raw, timing
        self.primary_calls += 1
        raw = self.replacement if self.replacement is not None else raw
        self.last_raw = copy.deepcopy(list(raw or []) + self.additions)
        return self.last_raw, timing


class PrimaryEvidenceReuseTests(unittest.TestCase):
    def assert_timing(self, observed, *, cached=False, reused=False, executed=False):
        timing = observed['read_timing']
        self.assertIs(timing['cache_hit'], cached)
        self.assertIs(timing['primary_ocr_reused'], reused)
        self.assertIs(timing['primary_ocr_executed'], executed)
        self.assertGreaterEqual(observed['elapsed_ms'], 0)

    def test_raw_evidence_and_scope_semantics_have_independent_ownership(self):
        with declared_reader('shop') as (reader, path):
            engine = RecordingEngine(reader.engine, additions=[declared_raw('李生素数', [1200, 400, 1310, 430])])
            reader.engine = engine
            with patch.object(perception, 'classify', wraps=perception.classify) as classify:
                narrow = reader.read(path, scope='rewards')
                self.assert_timing(narrow, executed=True)
                self.assertEqual(engine.primary_calls, 1)
                self.assertEqual(narrow['semantic']['team']['status'], 'not_read')
                self.assertEqual(reader.state_reader.read.call_count, 0)
                self.assertEqual(reader.shop_reader.read.call_count, 0)
                classifications = classify.call_count

                # Neither an engine-owned buffer nor a consumer's derived
                # result may mutate the original primary evidence slot.
                engine.last_raw[0] = declared_raw('corrupt-engine-buffer', [1, 1, 10, 10])
                engine.last_raw[-1][0][0][0] = -999
                narrow['rows'][0]['text'] = 'corrupt-derived-row'
                narrow['rows'].append({'text': 'corrupt-appended-row', 'box': [0, 0, 1, 1]})
                narrow['semantic']['team']['units'].append({'name': 'corrupt-derived-team'})
                narrow['capture_request_id'] = 'consumer-only-request'
                with patch.dict(perception.OBSERVED_TEXT_ALIASES,
                                {'李生素数': ('当次重新归一', 'explicit alias recomputation fixture')}):
                    full = reader.read(path, reuse_primary=True)
                self.assert_timing(full, reused=True)
                self.assertEqual(engine.primary_calls, 1)
                self.assertGreater(classify.call_count, classifications)
                self.assertEqual(full['page'], 'shop')
                self.assertEqual(full['read_contract']['effective_scope'], 'full')
                self.assertNotIn('capture_request_id', full)
                self.assertEqual(full['semantic']['team']['status'], 'unknown')
                self.assertFalse(full['semantic']['team']['checked'])
                self.assertEqual(full['semantic']['team']['units'], [])
                self.assertEqual(reader.state_reader.read.call_count, 1)
                self.assertEqual(reader.shop_reader.read.call_count, 1)
                self.assertFalse(any(row['text'].startswith('corrupt-') for row in full['rows']))
                alias = next(row for row in full['rows'] if row['raw_text'] == '李生素数')
                self.assertEqual(alias['text'], '当次重新归一')
                self.assertEqual(alias['box'], [1200, 400, 1310, 430])

                # Explicit reuse bypasses the complete-result cache and
                # reconstructs semantics even when full already exists.
                full['semantic']['team']['units'].append({'name': 'consumer-only'})
                again = reader.read(path, reuse_primary=True)
                self.assert_timing(again, reused=True)
                self.assertEqual(engine.primary_calls, 1)
                self.assertEqual(reader.state_reader.read.call_count, 2)
                self.assertEqual(reader.shop_reader.read.call_count, 2)
                self.assertEqual(again['semantic']['team']['units'], [])
                self.assertEqual(next(row for row in again['rows']
                                      if row['raw_text'] == '李生素数')['text'], '孪生素数')
                cached = reader.read(path)
                self.assert_timing(cached, cached=True)
                self.assertEqual(engine.primary_calls, 1)
                self.assertEqual(reader.state_reader.read.call_count, 2)

    def test_only_explicit_full_reuse_accepts_same_sha_and_current_ocr_contract(self):
        faults = ('force', 'sha', 'contract', 'engine', 'use_det', 'use_rec',
                  'text_score', 'box_thresh', 'unclip_ratio')
        for fault in faults:
            with self.subTest(invalidation=fault), declared_reader() as (reader, path), contextlib.ExitStack() as stack:
                engine = RecordingEngine(reader.engine)
                reader.engine = engine
                initial = reader.read(path, scope='rewards')
                self.assert_timing(initial, executed=True)
                if fault == 'sha':
                    with Image.open(path) as original:
                        altered = original.convert('RGB')
                    altered.putpixel((0, 0), (1, 2, 3))
                    altered.save(path)
                elif fault == 'contract':
                    stack.enter_context(patch.object(perception, 'OCR_CONTRACT_VERSION',
                                                     perception.OCR_CONTRACT_VERSION + 1))
                elif fault == 'engine':
                    reader.engine = RecordingEngine(engine.delegate)
                elif fault in ('use_det', 'use_rec'):
                    setattr(engine, fault, False)
                elif fault == 'text_score':
                    engine.text_score = .8
                elif fault in ('box_thresh', 'unclip_ratio'):
                    setattr(engine.text_det.postprocess_op, fault, 1.9)
                result = reader.read(path, force=fault == 'force', reuse_primary=True)
                self.assert_timing(result, executed=True)
                calls = engine.primary_calls + (reader.engine.primary_calls if reader.engine is not engine else 0)
                self.assertEqual(calls, 2)
                self.assertEqual(result['snapshot_id'] == initial['snapshot_id'], fault != 'sha')
                self.assert_timing(reader.read(path), cached=True)

        for scope in ('full', 'economy'):
            with self.subTest(ordinary_scope=scope), declared_reader() as (reader, path):
                reader.engine = RecordingEngine(reader.engine)
                reader.read(path, scope='rewards')
                result = reader.read(path, scope=scope)
                self.assert_timing(result, executed=True)
                self.assertEqual(reader.engine.primary_calls, 2)
        with declared_reader() as (reader, path):
            reader.engine = RecordingEngine(reader.engine)
            reader.read(path, scope='rewards')
            for scope in ('rewards', 'economy'):
                with self.subTest(rejected_reuse_scope=scope), self.assertRaises(ValueError):
                    reader.read(path, scope=scope, reuse_primary=True)
            self.assertEqual(reader.engine.primary_calls, 1)

    def test_unknown_and_modal_classification_keep_full_frame_evidence(self):
        # The overlay coexists with prep anchors. A central text omission must
        # not turn it into a preparation page eligible for narrow semantics.
        for page in ('unknown', 'reward_overlay'):
            with self.subTest(page=page), declared_reader() as (reader, path):
                engine = RecordingEngine(reader.engine,
                    replacement=[] if page == 'unknown' else None,
                    additions=[] if page == 'unknown' else [declared_raw('获得奖励', [840, 400, 1090, 470])])
                reader.engine = engine
                first = reader.read(path, scope='rewards')
                self.assertEqual(first['page'], page)
                self.assertEqual(first['read_contract']['effective_scope'], 'full')
                self.assertEqual(first['read_contract']['fallback_reason'], 'page_requires_full_read')
                first['page'] = 'preparation'  # Consumer mutation is not evidence.
                full = reader.read(path, reuse_primary=True)
                self.assert_timing(full, reused=True)
                self.assertEqual(engine.primary_calls, 1)
                self.assertEqual(full['page'], page)
                self.assertEqual(full['read_contract']['effective_scope'], 'full')
                self.assertEqual(full['read_contract']['unread'], [])
                self.assertEqual(reader.state_reader.read.call_count, 0)
                if page == 'reward_overlay':
                    self.assertTrue(any(row['raw_text'] == '获得奖励' for row in full['rows']))
                else:
                    self.assertEqual(full['rows'], [])


if __name__ == '__main__':
    unittest.main()
