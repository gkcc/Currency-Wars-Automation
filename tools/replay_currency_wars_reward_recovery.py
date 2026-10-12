"""Frozen eight-check recovery consumer acceptance; no game, OCR or installation.

Only the new recovery selection runs. Earlier crop calibration and earlier
acceptance suites are dependencies/evidence, not rerun successes. All complete
frames, gray/blue successors and transport are explicitly inert protocols.
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
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASE = 'e9e6ca7f4e4d943612d85ed3d28bd483b8224958'
EVIDENCE_COMMIT = '4edab30f9da025953b92f306070dcc2cf0af78ae'
BASE_MANIFEST_SHA256 = '1c90624daed8f1a3ff58656238685b901174a7ad7fc927427f511cedae0b0668'
SELECTION = ('test_currency_wars_reward_recovery.RewardRecoveryTests',)
SUPPORT = (
    'tools/replay_currency_wars_rewards.py', 'tools/replay_currency_wars_preparation.py',
    'tools/test_local_runtime_compatibility.py', 'tools/test_currency_wars_submission_queue.py',
    'tools/test_currency_wars_reward_roi_flow.py', 'tools/test_currency_wars_native_reward_detector.py',
    'tools/test_currency_wars_reward_hud_flow.py',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sources():
    from currency_wars_source_guard import production_files
    runtime = set(production_files(ROOT))
    names = runtime | set(SUPPORT) | {'tools/' + Path(__file__).name}
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: sha(ROOT / name) for name in sorted(names)}, sorted(runtime)


def artifacts():
    names = ['handoff/2026-10-07/reward-sequence/' + name for name in ('manifest.json', 'q00.png', 'q01.png')]
    names += ['handoff/2026-10-08/ROOT_NATIVE_REWARDS/' + name for name in (
        'diagnosis.json', 'initial-scan.png', 'manual_before-scan.png', 'fresh_worker-scan.png')]
    names += ['tools/reward_resources/' + name for name in (
        'manifest.json', 'blue-orb-q01.png', 'native-orbs-sources.json', 'initial-blue.png', 'initial-gray.png')]
    knowledge = ROOT / 'docs/GAME_KNOWLEDGE.json'
    if knowledge.exists():
        names.append(knowledge.relative_to(ROOT).as_posix())
    return {name: sha(ROOT / name) for name in sorted(names)}


class Result(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.perf_counter()
        self.before_problems = len(self.failures) + len(self.errors) + len(self.skipped)
        super().startTest(test)

    def stopTest(self, test):
        if not hasattr(self, 'outcomes'):
            self.outcomes = []
        failed = len(self.failures) + len(self.errors) + len(self.skipped) != self.before_problems
        self.outcomes.append(dict(test=test.id(), status='failed' if failed else 'passed',
                                 seconds=time.perf_counter() - self.started))
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    os.environ['CW_NATIVE_REWARD_EVIDENCE_DIR'] = str(ROOT / 'handoff/2026-10-08/ROOT_NATIVE_REWARDS')
    before, runtime = sources()
    data_before = artifacts()
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
            stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    packages = {}
    for name in ('numpy', 'Pillow', 'psutil', 'opencv-python'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    report = dict(schema='currency-wars-reward-recovery/v1', basis_commit=BASE,
        tested_checkout_head=head, evidence_commit=EVIDENCE_COMMIT,
        source_binding_authority='Exact tested bytes; HEAD may precede uncommitted changes or be null in an archive.',
        environment=dict(system=platform.system(), python=platform.python_version(), packages=packages),
        source_sha256=before, source_file_count=len(before), runtime_source_files=runtime,
        runtime_source_count=len(runtime), artifact_sha256=data_before, artifact_file_count=len(data_before),
        manifest_unchanged_from_pr28=before['tools/currency_wars_runtime_sources.json'] == BASE_MANIFEST_SHA256,
        original_natural_endpoint=dict(source='ROOT user report; not independently replayed here',
            stage='1-1', gray_input_receipts=1, delivery='completed',
            coins_before=4, coins_after=6, gray_currently_absent=True, blue_currently_present=True,
            original_effect_status='pending', original_effect_outcome='unknown',
            verification_reads=2, verification_frames=3,
            verification_reasons=['context_unverified', 'context_unverified', 'first_absence_candidate']),
        native_evidence_scope='Previously frozen unchanged scan crops used within declared protocol canvases; no new native OCR or complete natural successor pair.',
        game_inputs=0, controllers_started=0, new_game_captures=0, new_ocr_calls=0,
        online_model_called=False, candidate_installed=False, ready_changed=False,
        full_original_current_game_pngs_available=False, native_pending_recovery_verified=False,
        whole_game_speedup=None, old_frozen_selections_rerun=False)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    if loader.errors or suite.countTestCases() != 8:
        raise ValueError('Frozen eight-test selection did not resolve: ' + str(loader.errors))
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    report['focused_checks'] = dict(selection=list(SELECTION), tests=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
        seconds=time.perf_counter() - started, results=getattr(result, 'outcomes', []),
        failure_details=[dict(test=test.id(), traceback=trace) for test, trace in result.failures + result.errors])
    from test_currency_wars_reward_recovery import EVIDENCE
    report['consumer_protocol'] = EVIDENCE
    after, runtime_after = sources()
    data_after = artifacts()
    loaded = sorted({Path(module.__file__).resolve().relative_to(ROOT).as_posix()
        for module in tuple(sys.modules.values()) if getattr(module, '__file__', None)
        and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)})
    report.update(source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        source_unchanged=before == after and runtime == runtime_after,
        artifacts_unchanged=data_before == data_after,
        loaded_source_closure_missing=sorted(set(loaded) - set(before)))
    report['ok'] = bool(result.wasSuccessful() and not result.skipped
        and report['source_unchanged'] and report['artifacts_unchanged']
        and not report['loaded_source_closure_missing'] and report['manifest_unchanged_from_pr28'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'source_file_count', 'artifact_file_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'artifacts_unchanged',
        'loaded_source_closure_missing')}
        | {key: report['focused_checks'][key] for key in ('tests', 'failures', 'errors', 'skips', 'seconds')}))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
