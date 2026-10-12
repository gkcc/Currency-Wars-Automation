"""Frozen native reward calibration plus inert Worker/Entry acceptance.

No game, controller, new capture, OCR, online model, install or READY mutation.
The three unchanged native scan crops are calibration data, not full frames or
post-input results. Generated successors are reported separately as protocols.
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


BASE = '5cf3d9b3ae693d5df670203352d0d55bae3d19c5'
EVIDENCE_COMMIT = '4edab30f9da025953b92f306070dcc2cf0af78ae'
SELECTION = (
    'test_currency_wars_native_reward_detector.NativeRewardDetectorTests',
    'test_currency_wars_manual_reward_roi.ManualRewardRoiTests',
    'test_currency_wars_reward_roi_flow.RewardRoiFlowTests',
)
SUPPORT = (
    'tools/replay_currency_wars_rewards.py', 'tools/replay_currency_wars_preparation.py',
    'tools/test_local_runtime_compatibility.py', 'tools/test_currency_wars_submission_queue.py',
)
ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def hashes():
    from currency_wars_source_guard import production_files
    names = set(production_files(ROOT)) | set(SUPPORT) | {'tools/' + Path(__file__).name}
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(names)}


def artifacts():
    names = ['handoff/2026-10-07/reward-sequence/' + name for name in ('manifest.json', 'q00.png', 'q01.png')]
    names += ['handoff/2026-10-08/ROOT_NATIVE_REWARDS/' + name for name in
              ('diagnosis.json', 'initial-scan.png', 'manual_before-scan.png', 'fresh_worker-scan.png')]
    names += ['tools/reward_resources/' + name for name in
              ('manifest.json', 'blue-orb-q01.png', 'native-orbs-sources.json', 'initial-blue.png', 'initial-gray.png')]
    knowledge = ROOT/'docs/GAME_KNOWLEDGE.json'
    if knowledge.exists():
        names.append(knowledge.relative_to(ROOT).as_posix())
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(names)}


def resource_contract():
    import numpy as np
    from PIL import Image
    import currency_wars_rewards as rewards
    manifest = json.loads((ROOT/'tools/currency_wars_runtime_sources.json').read_bytes())
    spec = next(value for value in manifest['resource_manifests']
                if value['path'] == 'tools/reward_resources/native-orbs-sources.json')
    if spec['layout'] != 'resources' or set(spec['required_files']) != {'initial-blue.png', 'initial-gray.png'}:
        raise ValueError('New reward resources are not required by the shared runtime manifest')
    sources = json.loads((rewards.RESOURCE_DIR/'native-orbs-sources.json').read_bytes())
    with Image.open(ROOT/'handoff/2026-10-08/ROOT_NATIVE_REWARDS/initial-scan.png') as image:
        scan = np.array(image)
    checked = []
    for item in sources['resources']:
        data = (rewards.RESOURCE_DIR/item['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Reward appearance bytes differ from source map')
        with Image.open(io.BytesIO(data)) as image:
            actual = np.array(image)
        bounds = item['source_bounds']
        expected = scan[bounds[1]-230:bounds[3]-230, bounds[0]-1320:bounds[2]-1320]
        if not np.array_equal(actual, expected):
            raise ValueError('Appearance is not the unmodified retained scan crop')
        checked.append(item['file'])
    if set(checked) != set(spec['required_files']):
        raise ValueError('Reward source map omits or adds calibration assets')
    return {'required_calibration_assets': checked, 'exact_source_crop_rgb_verified': True,
            'shared_runtime_manifest': 'tools/currency_wars_runtime_sources.json',
            'ready_or_complete_private_resource_binding_verified': False}


class Result(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.perf_counter()
        self.before_problems = len(self.failures)+len(self.errors)+len(self.skipped)
        super().startTest(test)

    def stopTest(self, test):
        if not hasattr(self, 'outcomes'):
            self.outcomes = []
        failed = len(self.failures)+len(self.errors)+len(self.skipped) != self.before_problems
        self.outcomes.append({'test': test.id(), 'status': 'failed' if failed else 'passed',
                              'seconds': time.perf_counter()-self.started})
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    os.environ['CW_NATIVE_REWARD_EVIDENCE_DIR'] = str(ROOT/'handoff/2026-10-08/ROOT_NATIVE_REWARDS')
    before, data_before = hashes(), artifacts()
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, stderr=subprocess.DEVNULL,
                                       text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    report = {'schema': 'currency-wars-reward-roi/v1', 'basis_commit': BASE, 'tested_checkout_head': head,
        'source_binding_authority': 'Exact tested bytes; checkout HEAD may precede uncommitted changes or be absent in an archive.',
        'environment': {'system': platform.system(), 'python': platform.python_version(),
            'packages': {name: importlib.metadata.version(name) for name in ('numpy', 'Pillow', 'psutil', 'opencv-python')}},
        'source_sha256': before, 'source_file_count': len(before), 'runtime_source_count': 24,
        'artifact_sha256': data_before, 'evidence_commit': EVIDENCE_COMMIT,
        'resource_contract': resource_contract(), 'game_inputs': 0, 'controllers_started': 0,
        'new_game_captures': 0, 'new_ocr_calls': 0, 'online_model_called': False,
        'candidate_installed': False, 'ready_changed': False, 'whole_game_speedup': None,
        'native_post_input_chain_verified': False, 'natural_position_generalization_verified': False}
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    if loader.errors or suite.countTestCases() != 14:
        raise ValueError('Frozen new fourteen-test selection did not resolve: ' + str(loader.errors))
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    report['focused_checks'] = {'selection': list(SELECTION), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'seconds': time.perf_counter()-started, 'results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures+result.errors]}
    from test_currency_wars_native_reward_detector import NativeRewardDetectorTests
    from test_currency_wars_reward_roi_flow import EVIDENCE as consumer_protocol
    report['native_crop_calibration'] = NativeRewardDetectorTests.metrics
    report['consumer_protocol'] = consumer_protocol
    after, data_after = hashes(), artifacts()
    loaded = sorted({Path(module.__file__).resolve().relative_to(ROOT).as_posix()
        for module in tuple(sys.modules.values()) if getattr(module, '__file__', None)
        and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)})
    report.update(source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        source_unchanged=before == after, artifacts_unchanged=data_before == data_after,
        loaded_source_closure_missing=sorted(set(loaded)-set(before)))
    report['ok'] = bool(result.wasSuccessful() and not result.skipped and report['source_unchanged']
                        and report['artifacts_unchanged'] and not report['loaded_source_closure_missing'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    args.output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('ok', 'source_file_count', 'source_set_sha256_before',
        'source_set_sha256_after', 'artifacts_unchanged', 'loaded_source_closure_missing')}
        | {key: report['focused_checks'][key] for key in ('tests', 'failures', 'errors', 'skips', 'seconds')}))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
