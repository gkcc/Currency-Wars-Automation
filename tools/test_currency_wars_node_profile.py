"""New timing contracts only: declared OCR and synthetic clocks, no game input."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import currency_wars_perception as perception
from currency_wars_profile import OPERATIONS, ProfileRecorder, compare_reports, read_events, summarize_events
from test_currency_wars_perception_scope import declared_reader
from test_currency_wars_profile import event, window


class NodeProfileTests(unittest.TestCase):
    def test_node_wait_tool_observation_and_ocr_are_exclusive_not_causal(self):
        spans = [('root', 0, 6, 'supervisor_wait', None),
                 ('tool', 1, 4, 'tool_roundtrip', 'root'),
                 ('queue', 1, 2, 'queue_wait', 'tool'),
                 ('capture', 2, 3, 'capture', 'tool'),
                 ('read', 4, 5, 'perception', 'root'),
                 ('engine', 4.25, 4.75, 'ocr_engine', 'read'),
                 ('manual', 6, 9, 'manual_wait', None),
                 ('manual_tool', 7, 8, 'tool_roundtrip', 'manual'),
                 ('manual_capture', 7, 7.5, 'capture', 'manual_tool'),
                 ('local', 9, 9.5, 'decision', None)]
        events = window()
        for name, start, end, operation, parent in spans:
            events += [event('span_begin', name, start, operation=operation, parent_id=parent),
                       event('span_end', name, end)]
        report = summarize_events(events)
        measured = report['nodes'][0]['operation_seconds']
        self.assertEqual(report['issues'], [])
        self.assertEqual(sum(measured.values()), 10)
        self.assertEqual({key: value for key, value in measured.items() if value}, {
            'supervisor_wait': 2, 'manual_wait': 2, 'tool_roundtrip': 1.5,
            'queue_wait': 1, 'capture': 1.5, 'perception': .5,
            'ocr_engine': .5, 'decision': .5, 'unknown': .5})
        self.assertFalse(report['live_automation_verified'])
        self.assertIn('does not identify model', report['operation_notes']['supervisor_wait'])
        # Unlinked external work cannot be guessed into a worker's wait span.
        unlinked = window() + [event('span_begin', 'wait', 0, operation='manual_wait'),
            event('span_end', 'wait', 10), event('span_begin', 'external', 2, operation='capture'),
            event('span_end', 'external', 4)]
        conflict = summarize_events(unlinked)['nodes'][0]
        self.assertEqual(conflict['operation_seconds']['manual_wait'], 8)
        self.assertEqual(conflict['operation_seconds']['unknown'], 2)
        self.assertEqual(conflict['overlap_unknown_seconds'], 2)

    def test_import_binds_same_read_request_snapshot_and_parent_interval(self):
        snapshot, read_id = 'a' * 64, 'b' * 32
        with tempfile.TemporaryDirectory() as directory:
            clock = {'now': 0}
            with patch('currency_wars_profile.time.monotonic_ns', side_effect=lambda: clock['now']):
                recorder = ProfileRecorder(directory, run_id='fixture', enabled=True)
                recorder.set_context(match_id='match', stage='1-1', start_complete=True)
                parent = recorder.start_span('perception', operation='perception',
                    request_id='request', snapshot_id=snapshot)
                clock['now'] = 10_000_000_000
                timing = {'schema': perception.PERCEPTION_TIMING_SCHEMA, 'read_id': read_id,
                    'snapshot_id': snapshot, 'clock': 'same_process_monotonic',
                    'ocr_scope': 'perception_engine_calls', 'error': None,
                    'start_ns': 1_000_000_000, 'end_ns': 9_000_000_000,
                    'ocr_intervals': [{'start_ns': 2_000_000_000, 'end_ns': 4_000_000_000, 'outcome': 'returned'},
                                      {'start_ns': 5_000_000_000, 'end_ns': 7_000_000_000, 'outcome': 'raised'}]}
                imported = recorder.record_perception_timing(timing, parent_id=parent,
                    request_id='request', snapshot_id=snapshot)
                self.assertEqual(len(imported), 2)
                recorder.end_span(parent)
                recorder.close(complete=True)
            events, issues = read_events([recorder.path])
            report = summarize_events(events, issues)
            self.assertEqual(report['issues'], [])
            self.assertEqual(report['nodes'][0]['operation_seconds']['ocr_engine'], 4)
            self.assertEqual(report['nodes'][0]['operation_seconds']['perception'], 6)
            engines = [span for span in report['spans'] if span['operation'] == 'ocr_engine']
            self.assertEqual({span['snapshot_id'] for span in engines}, {snapshot})
            self.assertEqual({span['request_id'] for span in engines}, {'request'})
            self.assertEqual({span['read_id'] for span in engines}, {read_id})
            self.assertEqual({span['timing_source'] for span in engines}, {'perception_read'})

    def test_invalid_or_reused_timing_cannot_fill_the_parent_window(self):
        faults = ('snapshot', 'request', 'request_missing', 'clock', 'scope', 'parent_closed', 'before_parent',
                  'overlap', 'future', 'cached_interval', 'duplicate', 'timer_error')
        for fault in faults:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                recorder = ProfileRecorder(directory, run_id='fixture', enabled=True)
                recorder.set_context(match_id='match', stage='1-1')
                snapshot, read_id = 'a' * 64, 'b' * 32
                parent = recorder.start_span('perception', operation='perception',
                    request_id='request', snapshot_id=snapshot)
                start = perception.time.monotonic_ns()
                end = perception.time.monotonic_ns()
                timing = {'schema': perception.PERCEPTION_TIMING_SCHEMA, 'read_id': read_id,
                    'snapshot_id': snapshot, 'clock': 'same_process_monotonic',
                    'ocr_scope': 'perception_engine_calls', 'error': None,
                    'start_ns': start, 'end_ns': end,
                    'ocr_intervals': [{'start_ns': start, 'end_ns': end, 'outcome': 'returned'}]}
                kwargs = {'parent_id': parent, 'request_id': 'request', 'snapshot_id': snapshot}
                if fault == 'snapshot':
                    timing['snapshot_id'] = 'c' * 64
                elif fault == 'request':
                    kwargs['request_id'] = 'other'
                elif fault == 'request_missing':
                    recorder._spans[parent]['request_id'] = None
                    kwargs['request_id'] = None
                elif fault == 'clock':
                    timing['clock'] = 'other_machine'
                elif fault == 'scope':
                    timing['ocr_scope'] = 'all_engines_inferred'
                elif fault == 'parent_closed':
                    recorder.end_span(parent)
                elif fault == 'before_parent':
                    timing['start_ns'] = 0
                elif fault == 'overlap':
                    timing['ocr_intervals'] *= 2
                elif fault == 'future':
                    timing['end_ns'] += 10**18
                elif fault == 'cached_interval':
                    timing['cache_hit'] = True
                elif fault == 'duplicate':
                    self.assertEqual(len(recorder.record_perception_timing(timing, **kwargs)), 1)
                else:
                    timing['error'] = 'monotonic_clock_unavailable'
                self.assertEqual(recorder.record_perception_timing(timing, **kwargs), [])
                recorder.end_span(parent)
                recorder.close()
                events, issues = read_events([recorder.path])
                report = summarize_events(events, issues)
                self.assertEqual([issue['reason'] for issue in report['issues']], ['unverified_perception_timing'])
                self.assertEqual(len([span for span in report['spans'] if span['operation'] == 'ocr_engine']),
                                 1 if fault == 'duplicate' else 0)

    def test_cache_and_primary_reuse_record_only_this_read_actual_calls(self):
        with declared_reader('shop') as (reader, path):
            original, calls = reader.engine, []

            def engine(array, **kwargs):
                calls.append(array.shape[:2])
                raw, unused = original(array, **kwargs)
                if array.shape[:2] == (720, 1280):
                    raw = [row for row in raw if not row[1].startswith('Lv.')]
                    raw.append(([[250 / 1.5, 935 / 1.5], [340 / 1.5, 935 / 1.5],
                                 [340 / 1.5, 960 / 1.5], [250 / 1.5, 960 / 1.5]], '0/20', .99))
                return raw, unused

            reader.engine = engine
            reads = []
            for options in ({'scope': 'rewards'}, {'reuse_primary': True}, {}, {'force': True}):
                previous = len(calls)
                result = reader.read(path, **options)
                timing = result['read_timing']
                self.assertEqual(len(timing['ocr_intervals']), len(calls) - previous)
                self.assertEqual(timing['snapshot_id'], result['snapshot_id'])
                self.assertEqual(timing['ocr_scope'], 'perception_engine_calls')
                self.assertIsNone(timing['error'])
                self.assertIs(reader.engine, engine)
                self.assertIs(reader._primary_cache[2], engine)
                self.assertIs(reader.cache[2], engine)
                for interval in timing['ocr_intervals']:
                    self.assertLessEqual(timing['start_ns'], interval['start_ns'])
                    self.assertLessEqual(interval['start_ns'], interval['end_ns'])
                    self.assertLessEqual(interval['end_ns'], timing['end_ns'])
                reads.append(result)
            self.assertEqual(len({read['read_timing']['read_id'] for read in reads}), 4)
            self.assertTrue(reads[1]['read_timing']['primary_ocr_reused'])
            self.assertGreater(len(reads[1]['read_timing']['ocr_intervals']), 0)  # Actual HUD ROI only.
            self.assertTrue(reads[2]['read_timing']['cache_hit'])
            self.assertEqual(reads[2]['read_timing']['ocr_intervals'], [])
            self.assertEqual(calls.count((720, 1280)), 2)
            self.assertIsNone(reads[1]['fields']['level'])

    def test_timing_failure_preserves_ocr_result_and_original_exception(self):
        with declared_reader() as (reader, path):
            first = reader.read(path)
            with patch('currency_wars_perception.time.monotonic_ns', side_effect=RuntimeError('clock unavailable')):
                second = reader.read(path, force=True)
            for key in ('snapshot_id', 'page', 'rows', 'fields', 'semantic', 'read_contract'):
                self.assertEqual(first[key], second[key])
            self.assertEqual(second['read_timing']['error'], 'monotonic_clock_unavailable')
            self.assertEqual(second['read_timing']['ocr_intervals'], [])
            failure = RuntimeError('original engine failure')

            def engine(*args, **kwargs):
                raise failure

            reader.engine = engine
            with patch('currency_wars_perception.time.monotonic_ns', side_effect=RuntimeError('clock unavailable')):
                with self.assertRaises(RuntimeError) as raised:
                    reader.read(path, force=True)
            self.assertIs(raised.exception, failure)

    def test_legacy_ocr_and_missing_timing_are_never_relabelled_as_engine(self):
        legacy = summarize_events(window() + [event('span_begin', 'old', 0, operation='ocr'),
                                               event('span_end', 'old', 10)])
        self.assertEqual(legacy['nodes'][0]['operation_seconds']['ocr'], 10)
        self.assertEqual(legacy['nodes'][0]['operation_seconds']['ocr_engine'], 0)
        with tempfile.TemporaryDirectory() as directory:
            recorder = ProfileRecorder(directory, run_id='fixture', enabled=True,
                                       comparison_key='synthetic-contract-only')
            recorder.set_context(match_id='match', stage='1-1')
            parent = recorder.start_span('perception', operation='perception', request_id='r', snapshot_id='a' * 64)
            self.assertEqual(recorder.record_perception_timing({'primary_ocr_executed': True},
                parent_id=parent, request_id='r', snapshot_id='a' * 64), [])
            recorder.end_span(parent)
            recorder.close()
            events, issues = read_events([recorder.path])
            report = summarize_events(events, issues)
            self.assertEqual(report['issues'], [])
            self.assertEqual(report['nodes'][0]['operation_seconds']['ocr_engine'], 0)
            self.assertEqual(set(report['nodes'][0]['operation_seconds']), set(OPERATIONS))
            self.assertEqual(legacy['operation_contracts'], [None])
            self.assertEqual(report['operation_contracts'], [2])
            comparison = compare_reports(legacy, report)
            self.assertFalse(comparison['comparable'])
            self.assertIn('计时分类来源契约不同', comparison['reason'])


if __name__ == '__main__':
    unittest.main()
