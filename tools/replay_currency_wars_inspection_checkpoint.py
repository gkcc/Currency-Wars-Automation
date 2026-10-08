"""Frozen inspection-checkpoint acceptance through inert Worker/Entry fixtures.

Run from the same candidate checkout/archive as the tested sources. This does
not capture a desktop, launch a controller or establish native skip-page gains.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import unittest


BASE = '02e0a7c86f82e74216b759bed60a1a7cb55849eb'
SELECTION = ('test_currency_wars_inspection_checkpoint.InspectionCheckpointTests',)
FIXTURE_SOURCES = (
    'tools/test_currency_wars_business.py',
    'tools/test_currency_wars_economy.py',
    'tools/test_local_runtime_compatibility.py',
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
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes, self.started = [], {}

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


def checkout_head(root):
    try:
        values = subprocess.check_output(
            ['git', '-C', str(root), 'rev-parse', '--show-toplevel', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).splitlines()
        # An archive nested inside another checkout does not inherit its HEAD.
        return values[1] if len(values) == 2 and Path(values[0]).resolve() == root else None
    except (OSError, subprocess.CalledProcessError):
        return None


def environment():
    packages = {}
    for name in ('numpy', 'Pillow', 'psutil', 'opencv-python'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {'system': platform.system(), 'python': platform.python_version(), 'packages': packages}


def run(root, output):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    root = Path(root).resolve()
    if Path(__file__).resolve().parent != root / 'tools':
        raise ValueError('Driver and tested sources must come from the same candidate')
    before = hashes(root)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    selected = [test.id() for test in cases(suite)]
    if (loader.errors or len(selected) != 10 or len(set(selected)) != 10
            or any(not name.startswith(SELECTION[0] + '.test_') for name in selected)):
        raise ValueError('Frozen 10-test selection did not resolve; refuse imported/old discovery')
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    elapsed = time.perf_counter() - started
    after = hashes(root)
    differences = {key: {'before': before.get(key), 'after': after.get(key)}
        for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}
    from currency_wars_source_guard import production_files
    report = {
        'schema': 'currency-wars-inspection-checkpoint-acceptance/v1',
        'basis_commit': BASE, 'tested_checkout_head': checkout_head(root),
        'source_binding_authority': 'Exact before/after bytes; checkout HEAD does not identify uncommitted files.',
        'environment': environment(), 'selection': list(SELECTION),
        'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'skips': len(result.skipped),
        'elapsed_seconds': elapsed, 'test_results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors],
        'skip_details': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
        'source_file_count': len(before), 'runtime_source_count': len(production_files(root)),
        'source_sha256': before, 'source_sha256_after_differences': differences,
        'source_set_sha256_before': digest(before), 'source_set_sha256_after': digest(after),
        'source_unchanged': not differences,
        'protocol_coverage': {
            'execution': 'Real Worker business-resume/inspection consumers and Entry with inert transport.',
            'storage': 'Temporary durable records and actual file-based business CAS; no production checkpoint writes.',
            'observations': 'Generated fixture frames and declared reader facts, not native OCR or game receipts.',
            'selection': 'Only the ten named-class methods; imported business/economy/runtime classes supply helpers only.',
        },
        'game_started': False, 'game_inputs_sent': False, 'controllers_started': False,
        'online_model_called': False, 'new_game_captures': False, 'native_png_replayed': False,
        'private_resources_read_or_exported': False, 'ready_changed': False, 'candidate_installed': False,
        'native_cross_lease_verified': False, 'native_page_skipping_verified': False,
        'whole_game_speedup': None, 'same_endpoint_speedup': None,
        'limits': [
            'Protocol acceptance does not prove a live stop/start/business-resume chain.',
            'Skipped protocol panel requests are not native page-skipping or whole-match speed evidence.',
            'No game, GUI, broker, input, online model, installation or production READY is used.',
            'No unrelated frozen test classes or GUI/Rust selections are executed.',
        ],
    }
    report['ok'] = result.wasSuccessful() and not result.skipped and not differences
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'tests', 'failures', 'errors', 'skips',
        'elapsed_seconds', 'source_file_count', 'runtime_source_count',
        'source_set_sha256_before', 'source_set_sha256_after')}))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    return run(args.root, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
