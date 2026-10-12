"""Focused accounting contracts; synthetic timings are not live game evidence."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from currency_wars_profile import (SCHEMA, ProfileRecorder, compare_reports,
    read_events, summarize_events, write_report)


def event(kind, item_id, second, **kwargs):
    value = {'schema': SCHEMA, 'event_id': kind + '-' + item_id, 'session_id': 'session',
             'run_id': 'run', 'clock_id': 'run', 'source': 'worker', 'source_sha': 'test-fixture',
             'comparison_key': 'synthetic-contract-only', 'kind': kind, 'id': item_id,
             'monotonic_ns': int(second * 1_000_000_000), 'utc': '2026-10-06T12:00:00+00:00',
             'match_id': 'match', 'stage': '1-1'}
    value.update(kwargs)
    return value


def window(end=10):
    return [event('session_begin', 'session', 0), event('session_end', 'session', end),
            event('node_begin', 'node', 0, complete=True), event('node_end', 'node', end, complete=True),
            event('phase_begin', 'phase', 0, phase='rewards'), event('phase_end', 'phase', end)]


class ProfileTests(unittest.TestCase):
    def test_business_steps_partition_nested_time_and_keep_actual_return_reasons(self):
        events = window() + [
            event('span_begin', 'scan', 0, operation='rules', business_step='reward_scan'),
            event('span_end', 'scan', 10),
            event('span_begin', 'claim', 2, operation='rules', business_step='blue_orb', parent_id='scan'),
            event('span_end', 'claim', 8),
            event('span_begin', 'ocr', 3, operation='ocr', parent_id='claim'), event('span_end', 'ocr', 5),
            event('span_begin', 'publish', 5, operation='publication', parent_id='claim'), event('span_end', 'publish', 6),
            event('root_return', 'return', 9, phase='rewards', business_step='reward_scan',
                  category='capability', reason='Full-field reward sweep not available', request_id='r', snapshot_id='s')]
        report = summarize_events(events + events)
        steps = {row['business_step']: row for row in report['business_steps']}
        self.assertEqual(report['issues'], [])
        self.assertEqual(sum(row['total_seconds'] for row in steps.values()), 10)
        self.assertEqual(steps['reward_scan']['total_seconds'], 4)
        self.assertEqual(steps['blue_orb']['total_seconds'], 6)
        self.assertEqual(steps['blue_orb']['operation_seconds']['ocr'], 2)
        self.assertEqual(steps['blue_orb']['operation_seconds']['publication'], 1)
        self.assertEqual(steps['blue_orb']['operation_seconds']['rules'], 3)
        self.assertEqual(len(report['root_returns']), 1)
        self.assertEqual(report['root_returns'][0]['category'], 'capability')
        self.assertEqual([row['stage'] for row in report['plane_first_nodes']], ['1-1'])

    def test_old_operation_coverage_is_not_silently_upgraded_for_comparison(self):
        before, after = summarize_events(window()), summarize_events(window())
        before['nodes'][0]['operation_seconds'].pop('publication')
        self.assertFalse(compare_reports(before, after)['comparable'])

    def test_nested_parent_and_overlapping_children_are_not_added(self):
        events = window() + [
            event('span_begin', 'parent', 0, operation='decision'), event('span_end', 'parent', 10),
            event('span_begin', 'capture', 1, operation='capture', parent_id='parent'), event('span_end', 'capture', 3),
            event('span_begin', 'ocr', 2, operation='ocr', parent_id='parent'), event('span_end', 'ocr', 5)]
        report = summarize_events(events + events)  # Copied files do not double count.
        node = report['nodes'][0]
        self.assertEqual(report['event_count'], len(events))
        self.assertEqual(report['observed_seconds'], 10)
        self.assertEqual(sum(node['phase_seconds'].values()), 10)
        self.assertEqual(sum(node['operation_seconds'].values()), 10)
        self.assertEqual(node['operation_seconds']['decision'], 6)
        self.assertEqual(node['operation_seconds']['capture'], 1)
        self.assertEqual(node['operation_seconds']['ocr'], 2)
        self.assertEqual(node['operation_seconds']['unknown'], 1)
        self.assertEqual(node['overlap_unknown_seconds'], 1)
        parent = next(span for span in report['spans'] if span['id'] == 'parent')
        self.assertEqual(parent['exclusive_seconds'], 6)

    def test_gaps_and_truncated_spans_remain_unknown(self):
        events = window() + [event('span_begin', 'capture', 1, operation='capture'),
                             event('span_end', 'capture', 3),
                             event('span_begin', 'lost', 5, operation='ocr')]
        report = summarize_events(events)
        self.assertEqual(report['nodes'][0]['operation_seconds']['capture'], 2)
        self.assertEqual(report['nodes'][0]['operation_seconds']['unknown'], 8)
        self.assertIn('missing_end', {issue['reason'] for issue in report['issues']})
        self.assertFalse(compare_reports(report, report)['comparable'])

    def test_first_nodes_planes_and_separate_leases(self):
        events = window()
        # Another lease may have the same numeric clock readings. Never union it
        # with the first lease or fabricate the time between their UTC anchors.
        events += [event('session_begin', 'session2', 0, session_id='s2', run_id='run2', clock_id='run2', stage='3-1'),
                   event('session_end', 'session2', 7, session_id='s2', run_id='run2', clock_id='run2', stage='3-1'),
                   event('node_begin', 'node2', 0, session_id='s2', run_id='run2', clock_id='run2', stage='3-1', complete=False),
                   event('node_end', 'node2', 7, session_id='s2', run_id='run2', clock_id='run2', stage='3-1', complete=False)]
        report = summarize_events(events)
        self.assertEqual(report['observed_seconds'], 17)
        self.assertEqual(len(report['clock_domains']), 2)
        self.assertEqual([node['stage'] for node in report['plane_first_nodes']], ['1-1', '3-1'])
        self.assertEqual([plane['total_seconds'] for plane in report['planes']], [10, 7])
        self.assertFalse(report['nodes'][1]['complete_boundaries'])

    def test_conflicting_nodes_do_not_count_same_time_twice(self):
        events = window() + [event('node_begin', 'other', 3, stage='1-2', complete=True),
                             event('node_end', 'other', 7, stage='1-2', complete=True)]
        report = summarize_events(events)
        self.assertEqual(report['node_conflict_seconds'], 4)
        self.assertEqual(report['unassigned_seconds'], 4)
        self.assertEqual(sum(node['total_seconds'] for node in report['nodes']), 6)
        self.assertFalse(report['nodes'][0]['complete_boundaries'])

    def test_recorder_transition_and_epoch_preserve_span_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('currency_wars_profile.time.monotonic_ns', side_effect=iter(range(0, 100000000000, 1000000000))):
                recorder = ProfileRecorder(directory, run_id='run', enabled=True)
                recorder.set_context(match_id='m', stage='3-1', resume_epoch='old', phase='rewards')
                span = recorder.start_span('wait_for_reply', operation='decision', request_id='request')
                recorder.set_context(resume_epoch='new')
                recorder.end_span(span)
                recorder.set_context(stage='3-2', phase='startup_guide')
                recorder.close(complete=True)
            events, issues = read_events([recorder.path])
            bound = [item for item in events if item['id'] == span]
            self.assertEqual([item['resume_epoch'] for item in bound], ['old', 'old'])
            report = summarize_events(events, issues)
            self.assertEqual(report['issues'], [])
            self.assertFalse(report['nodes'][0]['complete_boundaries'])
            self.assertTrue(report['nodes'][1]['complete_boundaries'])
            self.assertGreater(report['observed_seconds'], 0)

    def test_disabled_or_failed_recorder_does_not_change_business_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = ProfileRecorder(directory, run_id='run')
            with recorder.span(operation='capture'):
                pass
            self.assertEqual(list(Path(directory).iterdir()), [])
            recorder = ProfileRecorder(directory, run_id='run', enabled=True)
            calls = []
            with recorder.span(operation='input_animation') as span_id:
                self.assertEqual(recorder.current_span_id, span_id)
                calls.append('one_published_request')
                recorder._stream.close()  # Simulate diagnostic write failing after publication.
            self.assertIsNone(recorder.current_span_id)
            self.assertEqual(calls, ['one_published_request'])
            self.assertFalse(recorder.enabled)
            self.assertEqual(recorder.error, 'ValueError')
            with self.assertRaisesRegex(RuntimeError, 'original'):
                with recorder.span(operation='input_animation'):
                    raise RuntimeError('original')

    def test_actual_broker_intervals_are_children_of_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = {'now': 0}
            with patch('currency_wars_profile.time.monotonic_ns', side_effect=lambda: clock['now']):
                recorder = ProfileRecorder(directory, run_id='run', enabled=True)
                recorder.set_context(match_id='m', stage='1-1', phase='economy', start_complete=True)
                parent = recorder.start_span('broker_roundtrip', operation='unknown', request_id='r')
                clock['now'] = 10_000_000_000
                recorder.record_interval('batch_input', start_ns=1_000_000_000, end_ns=4_000_000_000,
                                         operation='input_animation', parent_id=parent, receipt_id='r')
                recorder.record_interval('frame_capture', start_ns=4_000_000_000, end_ns=6_000_000_000,
                                         operation='capture', parent_id=parent, receipt_id='r')
                recorder.end_span(parent)
                recorder.close(complete=True)
            events, issues = read_events([recorder.path])
            report = summarize_events(events, issues)
            self.assertEqual(report['issues'], [])
            operations = report['nodes'][0]['operation_seconds']
            self.assertEqual(operations['input_animation'], 3)
            self.assertEqual(operations['capture'], 2)
            self.assertEqual(operations['unknown'], 5)
            parent_row = next(span for span in report['spans'] if span['id'] == parent)
            self.assertEqual(parent_row['exclusive_seconds'], 5)
            self.assertEqual(sum(operations.values()), 10)

    def test_comparison_requires_same_contract_and_complete_samples(self):
        before = summarize_events(window())
        after = summarize_events(window(8))
        comparison = compare_reports(before, after)
        self.assertTrue(comparison['comparable'])
        self.assertEqual(comparison['nodes'][0]['delta_seconds'], -2)
        after['comparison_key'] = 'different-scenario'
        self.assertFalse(compare_reports(before, after)['comparable'])
        after['comparison_key'] = before['comparison_key']
        after['sources'].append({'source': 'supervisor'})
        self.assertFalse(compare_reports(before, after)['comparable'])
        after['sources'].pop()
        after['nodes'][0]['complete_boundaries'] = False
        self.assertFalse(compare_reports(before, after)['comparable'])

    def test_real_file_export_preserves_unknown_and_escapes_html(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'events.jsonl'
            events = window()
            source.write_text('\n'.join(json.dumps(item) for item in events) + '\n{"truncated":', encoding='utf-8')
            read, issues = read_events([source])
            report = summarize_events(read, issues)
            report['issues'].append({'reason': '<script>alert(1)</script>'})
            paths = write_report(report, Path(directory) / 'report')
            saved = json.loads(paths['json'].read_text(encoding='utf-8'))
            self.assertEqual(saved['nodes'][0]['operation_seconds']['unknown'], 10)
            with paths['csv'].open(encoding='utf-8-sig') as stream:
                row = next(csv.DictReader(stream))
                self.assertEqual(float(row['operation_unknown_seconds']), 10)
            page = paths['html'].read_text(encoding='utf-8')
            self.assertNotIn('<script>', page)
            self.assertIn('&lt;script&gt;', page)
            self.assertFalse(saved['live_automation_verified'])


if __name__ == '__main__':
    unittest.main()
