"""Frozen PR19 page/stop regression selection; no desktop, process launch or OCR.

Run this file from the same candidate archive that supplies the tested sources.
The public evidence contains native OCR rows, not the private original PNG.
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


BASE = '8bcff97b5a9287a6ffda405ee33a17473d1e32ac'
SELECTION = (
    'test_currency_wars_page_structure.PageStructureTests',
    'test_currency_wars_page_structure.PageRoutingTests',
    'test_currency_wars_stop_projection.StopProjectionTests',
)
FIXTURE_SOURCES = (
    'requirements.txt',
    'handoff/2026-10-08/ROOT_PR19_ADVANTAGES_MISCLASSIFICATION.json',
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


def run(root, output):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    root = Path(root).resolve()
    if Path(__file__).resolve().parent != root / 'tools':
        raise ValueError('driver and tested sources must come from the same candidate')
    before = hashes(root)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    selected = [test.id() for test in cases(suite)]
    if loader.errors or len(selected) != 14 or any(not any(name.startswith(prefix + '.test_') for prefix in SELECTION)
                                                  for name in selected):
        raise ValueError('frozen 14-test selection did not resolve; refuse imported/old discovery')
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    elapsed = time.perf_counter() - started
    after = hashes(root)
    differences = {key: {'before': before.get(key), 'after': after.get(key)}
                   for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}
    from currency_wars_perception import classify, _native_advantages_page, _native_battle_stage
    from currency_wars_source_guard import production_files
    from currency_wars_broker_entry import PINNED
    from test_currency_wars_page_structure import retained, EVIDENCE_COMMIT, EVIDENCE_BLOB, EVIDENCE_PATH
    evidence = retained()
    try:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                      text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    report = {
        'schema': 'currency-wars-page-stop-acceptance/v1',
        'basis_commit': BASE, 'tested_checkout_head': head,
        'source_binding_authority': 'exact before/after bytes; a checkout HEAD does not identify uncommitted files',
        'environment': {'system': platform.system(), 'python': platform.python_version(),
            'packages': {name: importlib.metadata.version(name) for name in ('numpy', 'Pillow', 'psutil', 'opencv-python')}},
        'selection': list(SELECTION), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'elapsed_seconds': elapsed, 'test_results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors],
        'source_file_count': len(before), 'runtime_source_count': len(production_files(root)),
        'source_sha256': before, 'source_sha256_after_differences': differences,
        'source_set_sha256_before': digest(before), 'source_set_sha256_after': digest(after), 'broker_pin': PINNED,
        'retained_native_ocr': {
            'evidence_commit': EVIDENCE_COMMIT, 'path': EVIDENCE_PATH, 'git_blob': EVIDENCE_BLOB,
            'receipt_id': evidence['receipt_id'], 'original_png_sha256_reference': evidence['snapshot_sha256'],
            'row_count': len(evidence['rows']), 'historical_recorded_page': evidence['page_reader'],
            'candidate_page': classify(evidence['rows']),
            'native_advantages': _native_advantages_page(evidence['rows']),
            'native_battle_stage': _native_battle_stage(evidence['rows']),
            'raw_png_replayed': False, 'new_ocr_calls': 0, 'uid_row_present': False},
        'protocol_coverage': {
            'worker': 'real tick dispatch with inert ask/input/observation boundaries; no live decision receipt',
            'stop_start': 'real stop CLI and _start_cli with inert process probes; Popen intercepted before launch',
            'synthetic_variants': 'anchor mutations, native HUD coordinates and modal overlays are protocol cases'},
        'live_automation_verified': False, 'windows_acceptance_performed': platform.system() == 'Windows',
        'original_resources_read_or_exported': False, 'candidate_installed': False,
        'whole_game_speedup': None, 'same_endpoint_speedup': None,
        'limits': [
            'The private original screenshot was not read; only the published UID-redacted OCR rows were replayed.',
            'No game, GUI, broker, input, online model service, READY change or installation was performed.',
            'A reviewed page request is not new-match entry or a completed business_resume proof.',
            'Stop/start uses filesystem and identity protocols; ROOT must verify real Windows process lifetimes.',
            'No PR19 frozen 39-test selection or unrelated GUI/Rust/PR20/PR21 tests were rerun.',
        ],
    }
    report['ok'] = result.wasSuccessful() and not result.skipped and not differences
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'tests', 'failures', 'errors', 'skips', 'elapsed_seconds',
        'source_file_count', 'runtime_source_count', 'source_set_sha256_before', 'source_set_sha256_after')}))
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
