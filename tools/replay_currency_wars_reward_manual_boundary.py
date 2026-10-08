"""Frozen new reward/manual/source/profile contracts; no desktop or service.

Uses only explicit new test classes. Old modules supply inert fixture helpers,
never test discovery. Images are the already public retained/calibration PNGs
and declared protocol variants, not a reconstruction of ROOT's current game.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest


BASE = '1c48d12f88944a5532371ac3192710b85984cc42'
SELECTION = (
    'test_currency_wars_submission_queue.SubmissionQueueTests',
    'test_currency_wars_manual_stage.ManualStageTests',
    'test_currency_wars_node_profile.NodeProfileTests',
    'test_currency_wars_manual_step_evidence.ManualStepEvidenceTests',
    'test_currency_wars_reward_manual_boundary.RewardManualBoundaryTests',
)
FIXTURE_SOURCES = (
    'tools/test_local_runtime_compatibility.py',
    'tools/test_currency_wars_economy.py',
    'tools/test_currency_wars_profile.py',
    'tools/test_currency_wars_perception_scope.py',
    'tools/replay_currency_wars_rewards.py',
    'tools/reward_resources/blue-orb-q01.png',
    'tools/reward_resources/manifest.json',
    'handoff/2026-10-07/reward-sequence/manifest.json',
    'handoff/2026-10-07/reward-sequence/q00.png',
    'handoff/2026-10-07/reward-sequence/q01.png',
    'gui/src/source_guard.rs',
)


def hashes(root):
    from currency_wars_source_guard import production_files
    names = set(production_files(root)) | set(FIXTURE_SOURCES)
    names.add('tools/' + Path(__file__).name)
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cases(suite):
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            yield from cases(value)
        else:
            yield value


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = []
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        failed = any(item is test or getattr(item, 'test_case', None) is test for item, unused in self.failures)
        error = any(item is test or getattr(item, 'test_case', None) is test for item, unused in self.errors)
        skipped = any(item is test for item, unused in self.skipped)
        self.outcomes.append({'test': test.id(), 'status': 'error' if error else 'failed' if failed else
            'skipped' if skipped else 'passed', 'seconds': time.perf_counter() - self.started[test.id()]})
        super().stopTest(test)


def run(root, output):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    root = Path(root).resolve()
    if Path(__file__).resolve().parent != root / 'tools':
        raise ValueError('driver and tested checkout must be the same source tree')
    before = hashes(root)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    selected = [test.id() for test in cases(suite)]
    if loader.errors or not selected or any(not any(name.startswith(prefix + '.test_') for prefix in SELECTION)
                                          for name in selected):
        raise ValueError('explicit new selection did not resolve; refuse imported/old test discovery')
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    elapsed = time.perf_counter() - started
    after = hashes(root)
    differences = {key: {'before': before.get(key), 'after': after.get(key)}
                   for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}
    from test_currency_wars_reward_manual_boundary import EVIDENCE
    from currency_wars_source_guard import production_files
    from currency_wars_broker_entry import PINNED
    try:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    report = {
        'schema': 'currency-wars-reward-manual-boundary-acceptance/v1',
        'basis_commit': BASE, 'tested_checkout_head': head,
        'source_binding_authority': 'exact file hashes before/after; HEAD alone does not identify uncommitted bytes',
        'environment': {'system': platform.system(), 'python': platform.python_version()},
        'selection': list(SELECTION), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'elapsed_seconds': elapsed, 'test_results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors],
        'source_sha256': before, 'source_sha256_after_differences': differences,
        'source_set_sha256_before': digest(before), 'source_set_sha256_after': digest(after),
        'runtime_source_count': len(production_files(root)), 'broker_pin': PINNED,
        'measurements': EVIDENCE,
        'live_automation_verified': False, 'whole_game_speedup': None,
        'same_endpoint_before_after_speedup': None, 'current_root_receipt_replayed': False,
        'current_root_receipt_reference': '34daf6e5da314e29bb7b1aa22e90257a',
        'historical_limits': [
            'The reported current ROOT receipt/original transition frames were not in the fetched public tree.',
            'Old mixed completed groups and shared preview aliases were not upgraded to immutable receipt identities.',
            'Protocol reward batches end at current full-field ROOT review, not reward-clear or battle-ready.',
            'Manual-stage tests verify scope/receipts, not a real continuous match transition.',
            'Private local templates are neither read by this driver nor exported; public absence says nothing about ROOT resources.',
            'No game, broker process, GUI, online model, installation, Rust build, or Windows live acceptance was executed.',
        ],
        'development_record': 'handoff/2026-10-07/MANUAL_STAGE_DEVELOPMENT.md',
    }
    report['ok'] = result.wasSuccessful() and not result.skipped and not differences
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'tests', 'failures', 'errors', 'skips', 'elapsed_seconds',
        'runtime_source_count', 'source_set_sha256_before', 'source_set_sha256_after')}, ensure_ascii=False))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    return run(args.root, args.output)


if __name__ == '__main__':
    sys.exit(main())
