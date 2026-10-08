"""Frozen HUD crops and the existing reward consumers; never a game run.

Run with the already-installed local RapidOCR. A missing package or failed
native read is an ERROR, not a skip, download, synthetic score or native value.
The full original screenshots are unavailable here; native evidence stops at
the unchanged crops. Complete pages, successors and transport are protocols.
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
BASE = 'd536da965fa42d800b24992aae363246c9f3d67c'
EVIDENCE_COMMIT = 'f6c7588c932cd5406a238267783e0723a7cccb16'
BASE_MANIFEST_SHA256 = '1c90624daed8f1a3ff58656238685b901174a7ad7fc927427f511cedae0b0668'
SELECTION = (
    'test_currency_wars_reward_hud.RewardHudPixelTests',
    'test_currency_wars_reward_hud.RewardHudNativeOcrTests',
    'test_currency_wars_reward_hud_flow.RewardHudFlowTests',
)
SUPPORT = (
    'tools/replay_currency_wars_rewards.py', 'tools/replay_currency_wars_preparation.py',
    'tools/test_local_runtime_compatibility.py', 'tools/test_currency_wars_submission_queue.py',
    'tools/test_currency_wars_reward_roi_flow.py', 'tools/test_currency_wars_native_reward_detector.py',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sources():
    from currency_wars_source_guard import production_files
    names = set(production_files(ROOT)) | set(SUPPORT) | {'tools/' + Path(__file__).name}
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: sha(ROOT / name) for name in sorted(names)}


def artifacts():
    names = ['handoff/2026-10-08/ROOT_PR27_HUD/' + name for name in (
        'diagnosis.json', 'resume-coins.png', 'resume-player.png',
        'reward_block-coins.png', 'reward_block-player.png')]
    names += ['handoff/2026-10-07/reward-sequence/' + name for name in ('manifest.json', 'q00.png', 'q01.png')]
    names += ['handoff/2026-10-08/ROOT_NATIVE_REWARDS/' + name for name in (
        'diagnosis.json', 'initial-scan.png', 'manual_before-scan.png', 'fresh_worker-scan.png')]
    names += ['tools/reward_resources/' + name for name in (
        'manifest.json', 'blue-orb-q01.png', 'native-orbs-sources.json', 'initial-blue.png', 'initial-gray.png')]
    knowledge = ROOT / 'docs/GAME_KNOWLEDGE.json'
    if knowledge.exists():
        names.append(knowledge.relative_to(ROOT).as_posix())
    return {name: sha(ROOT / name) for name in sorted(names)}


def evidence_sources():
    from PIL import Image
    data = json.loads((ROOT / 'handoff/2026-10-08/ROOT_PR27_HUD/diagnosis.json').read_bytes())
    result = []
    for item in data['source_map']:
        path = ROOT / item['file']
        bounds = item['source_bounds']
        with Image.open(path) as image:
            if (sha(path) != item['crop_sha256'] or image.mode != 'RGB'
                    or image.size != (bounds[2] - bounds[0], bounds[3] - bounds[1])):
                raise ValueError('Frozen native crop source differs: ' + item['file'])
        result.append(item)
    if len(result) != 4:
        raise ValueError('All four unchanged context crops must be accounted for')
    return result


class Result(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.perf_counter()
        self.before_problems = len(self.failures) + len(self.errors) + len(self.skipped)
        super().startTest(test)

    def stopTest(self, test):
        if not hasattr(self, 'outcomes'):
            self.outcomes = []
        failed = len(self.failures) + len(self.errors) + len(self.skipped) != self.before_problems
        self.outcomes.append({'test': test.id(), 'status': 'failed' if failed else 'passed',
                              'seconds': time.perf_counter() - self.started})
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    os.environ['CW_NATIVE_HUD_EVIDENCE_DIR'] = str(ROOT / 'handoff/2026-10-08/ROOT_PR27_HUD')
    before, data_before = sources(), artifacts()
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
            stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    packages = {}
    for name in ('numpy', 'Pillow', 'psutil', 'opencv-python', 'rapidocr-onnxruntime', 'onnxruntime'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    report = {'schema': 'currency-wars-reward-hud/v1', 'basis_commit': BASE,
        'evidence_commit': EVIDENCE_COMMIT, 'tested_checkout_head': head,
        'source_binding_authority': 'Exact tested bytes; HEAD may precede uncommitted changes or be null in an archive.',
        'environment': {'system': platform.system(), 'python': platform.python_version(), 'packages': packages},
        'source_sha256': before, 'source_file_count': len(before), 'runtime_source_count': 24,
        'artifact_sha256': data_before, 'native_source_map': evidence_sources(),
        'manifest_unchanged_from_pr27': before['tools/currency_wars_runtime_sources.json'] == BASE_MANIFEST_SHA256,
        'game_inputs': 0, 'controllers_started': 0, 'new_game_captures': 0,
        'online_model_called': False, 'candidate_installed': False, 'ready_changed': False,
        'full_original_pngs_available': False, 'native_reward_chain_verified': False,
        'whole_game_speedup': None}
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    if loader.errors or suite.countTestCases() != 8:
        raise ValueError('Frozen eight-test selection did not resolve: ' + str(loader.errors))
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    report['focused_checks'] = {'selection': list(SELECTION), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'seconds': time.perf_counter() - started, 'results': getattr(result, 'outcomes', []),
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors]}
    from test_currency_wars_reward_hud import RewardHudPixelTests, RewardHudNativeOcrTests
    from test_currency_wars_reward_hud_flow import EVIDENCE as consumer_protocol
    report['native_crop_geometry'] = RewardHudPixelTests.metrics
    report['native_crop_ocr'] = RewardHudNativeOcrTests.metrics
    report['native_ocr_test_passed'] = any(
        value['test'].startswith('test_currency_wars_reward_hud.RewardHudNativeOcrTests.')
        and value['status'] == 'passed' for value in report['focused_checks']['results'])
    report['consumer_protocol'] = consumer_protocol
    after, data_after = sources(), artifacts()
    loaded = sorted({Path(module.__file__).resolve().relative_to(ROOT).as_posix()
        for module in tuple(sys.modules.values()) if getattr(module, '__file__', None)
        and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)})
    report.update(source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        source_unchanged=before == after, artifacts_unchanged=data_before == data_after,
        loaded_source_closure_missing=sorted(set(loaded) - set(before)))
    report['ok'] = bool(result.wasSuccessful() and not result.skipped and report['native_ocr_test_passed']
        and report['source_unchanged'] and report['artifacts_unchanged']
        and not report['loaded_source_closure_missing'] and report['manifest_unchanged_from_pr27'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'native_ocr_test_passed', 'source_file_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'artifacts_unchanged', 'loaded_source_closure_missing')}
        | {key: report['focused_checks'][key] for key in ('tests', 'failures', 'errors', 'skips', 'seconds')}))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
