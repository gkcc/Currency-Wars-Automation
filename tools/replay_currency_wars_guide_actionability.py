"""Focused guide actionability acceptance and optional existing-pair diagnosis.

No game, broker, GUI, capture, native full-screen OCR, installation or READY
mutation. Native crops and complete declared consumer protocols stay separate.
The source map covers the combined PR29 + current candidate consumer closure.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BASE = 'b7f3965d6e75a8aac094b457b13d5163eb6839a4'
EVIDENCE_COMMIT = '99809b924393ca4540bfa4ced5c17cb89452ad73'
BASE_MANIFEST_SHA256 = '1c90624daed8f1a3ff58656238685b901174a7ad7fc927427f511cedae0b0668'
SELECTION = ('test_currency_wars_guide_actionability.GuideCropTests',
             'test_currency_wars_guide_actionability.GuideVisualProtocolTests',
             'test_currency_wars_guide_actionability.GuideConsumerTests')
PARENT_AFFECTED_SELECTION = (
    'test_currency_wars_reward_recovery.RewardRecoveryTests.test_exhausted_pending_current_recovery_blue_once_then_new_epoch_full_review',
)
PARENT_SUPPORT = (
    'tools/replay_currency_wars_rewards.py', 'tools/replay_currency_wars_preparation.py',
    'tools/test_local_runtime_compatibility.py', 'tools/test_currency_wars_submission_queue.py',
    'tools/test_currency_wars_reward_roi_flow.py', 'tools/test_currency_wars_native_reward_detector.py',
    'tools/test_currency_wars_reward_hud_flow.py', 'tools/test_currency_wars_reward_recovery.py',
    'tools/replay_currency_wars_reward_recovery.py',
)


def sha(path):
    return path and hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sources():
    from currency_wars_source_guard import production_files
    runtime = set(production_files(ROOT))
    names = runtime | set(PARENT_SUPPORT) | {'tools/' + Path(__file__).name,
        'tools/test_currency_wars_guide_actionability.py'}
    return {name: sha(ROOT / name) for name in sorted(names)}, sorted(runtime)


def artifacts():
    names = ['handoff/2026-10-08/ROOT_PR28_STARTUP_GUIDE/' + name for name in (
        'diagnosis.json', 'source-navigation.png', 'source-header.png', 'source-front-caption.png',
        'current-navigation.png', 'current-header.png', 'current-front-caption.png')]
    # Bind previously frozen parent dependencies without presenting them as a
    # newly executed suite. The optional single affected parent consumer uses
    # these exact bytes; parent reports stay separately attributed.
    names += ['handoff/2026-10-07/reward-sequence/' + name for name in ('manifest.json', 'q00.png', 'q01.png')]
    names += ['handoff/2026-10-08/ROOT_NATIVE_REWARDS/' + name for name in (
        'diagnosis.json', 'initial-scan.png', 'manual_before-scan.png', 'fresh_worker-scan.png')]
    names += ['tools/reward_resources/' + name for name in (
        'manifest.json', 'blue-orb-q01.png', 'native-orbs-sources.json', 'initial-blue.png', 'initial-gray.png')]
    knowledge = ROOT / 'docs/GAME_KNOWLEDGE.json'
    if knowledge.exists():
        names.append(knowledge.relative_to(ROOT).as_posix())
    return {name: sha(ROOT / name) for name in sorted(names)}


def read_object(path):
    data = path.read_bytes()
    if len(data) > 2_000_000:
        raise ValueError('retained JSON exceeds its explicit 2 MB bound')
    value = json.loads(data.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('retained JSON must be an object')
    return value, hashlib.sha256(data).hexdigest()


def diagnose_pair(spec_path):
    """Only inspect retained files; never reconstruct missing identities."""
    from currency_wars_perception import fingerprint
    from currency_wars_visual_guards import stable_preparation_icon_target
    spec_path = spec_path.resolve()
    spec, spec_sha = read_object(spec_path)
    if (type(spec.get('schema')) is not int or spec['schema'] != 1
            or spec.get('observation_origin') not in ('original_production', 'independent_read_only_reconstruction')):
        raise ValueError('pair schema=1 and explicit observation_origin are required')
    def path(key):
        value = spec.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('retained pair file missing: ' + key)
        return (spec_path.parent / value).resolve()
    request, request_sha = read_object(path('request_json'))
    reply, reply_sha = read_object(path('reply_json'))
    actual, actual_sha = read_object(path('current_observation_json'))
    request = copy.deepcopy(request)
    request['original_png'] = str(path('original_png'))
    actions = reply.get('actions')
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError('pair diagnosis supports the single explicit guide action only')
    png_shas, fingerprints = [], []
    for key in ('original_png', 'current_png'):
        payload = path(key).read_bytes()
        if len(payload) > 25_000_000:
            raise ValueError('retained PNG exceeds bound')
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                raise ValueError('retained pair needs actual 1920x1080 PNGs')
            fingerprints.append(fingerprint(image))
        png_shas.append(hashlib.sha256(payload).hexdigest())
    original = request.get('observation') or {}
    digests_match = (png_shas[0] == request.get('snapshot_id') == original.get('snapshot_id')
                     and png_shas[1] == actual.get('snapshot_id'))
    fingerprints_match = all(observed.get('fingerprint') == value
        for observed, value in zip((original, actual), fingerprints))
    source_identities_present = all(isinstance(observed.get(key), str) and observed[key]
        for observed in (original, actual) for key in ('capture_request_id', 'frame_id', 'snapshot_id'))
    diagnostic = {}
    eligible = stable_preparation_icon_target(actions[0], request, actual, path('current_png'), diagnostic)
    return dict(spec_sha256=spec_sha, request_json_sha256=request_sha, reply_json_sha256=reply_sha,
        current_observation_json_sha256=actual_sha, source_png_sha256=png_shas,
        observation_origin=spec['observation_origin'], identities_present=bool(source_identities_present),
        source_digests_match=digests_match, fingerprints_match=fingerprints_match,
        visual_eligibility=bool(eligible), byte_bound_visual_eligibility=bool(eligible and digests_match),
        diagnostic=diagnostic, historical_qualification=False,
        original_actual_observation_reconstructed=False, current_epoch_deadline_owner_pending_verified=False,
        input_published=False, navigation_outcome_verified=False)


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
    parser.add_argument('--pair-spec', action='append', type=Path, default=[])
    parser.add_argument('--pairs-only', action='store_true', help='Only diagnose supplied retained pairs; run no tests')
    parser.add_argument('--include-parent-recovery', action='store_true',
        help='Also run the one PR29 consumer affected by shared manual-step changes, never all frozen eight')
    parser.add_argument('--parent-affected-only', action='store_true',
        help='Only the one affected PR29 consumer; does not repeat the current eight')
    args = parser.parse_args()
    if args.pairs_only and (not args.pair_spec or args.include_parent_recovery or args.parent_affected_only):
        parser.error('--pairs-only requires --pair-spec and excludes --include-parent-recovery')
    if args.parent_affected_only and (args.pair_spec or args.include_parent_recovery):
        parser.error('--parent-affected-only excludes --pair-spec and --include-parent-recovery')
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
    report = dict(schema='currency-wars-guide-actionability/v1', basis_commit=BASE,
        tested_checkout_head=head, evidence_commit=EVIDENCE_COMMIT,
        source_binding_authority='Exact tested bytes; HEAD may precede uncommitted changes or be null in an archive.',
        environment=dict(system=platform.system(), python=platform.python_version(), packages=packages),
        source_sha256=before, source_file_count=len(before), runtime_source_files=runtime,
        runtime_source_count=len(runtime), artifact_sha256=data_before, artifact_file_count=len(data_before),
        manifest_unchanged_from_pr29=before['tools/currency_wars_runtime_sources.json'] == BASE_MANIFEST_SHA256,
        game_inputs=0, controllers_started=0, new_game_captures=0, native_ocr_calls=0,
        online_model_called=False, candidate_installed=False, ready_changed=False,
        native_scope='Six unedited crops calibrate local appearance only; complete consumer frames and successors are declared protocols.',
        public_checkout_original_full_game_pngs_available=False, historical_qualification=False,
        natural_navigation_success_verified=False, whole_game_speedup=None,
        parent_frozen_eight_rerun=False,
        current_focused_selection_run=not args.pairs_only and not args.parent_affected_only,
        parent_affected_selection_rerun=args.include_parent_recovery or args.parent_affected_only)
    passed, log = True, ''
    if not args.pairs_only:
        selection = (PARENT_AFFECTED_SELECTION if args.parent_affected_only else
                     SELECTION + (PARENT_AFFECTED_SELECTION if args.include_parent_recovery else ()))
        if args.include_parent_recovery or args.parent_affected_only:
            import os
            os.environ['CW_NATIVE_REWARD_EVIDENCE_DIR'] = str(ROOT / 'handoff/2026-10-08/ROOT_NATIVE_REWARDS')
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(selection)
        expected_count = (0 if args.parent_affected_only else 8) + int(args.parent_affected_only or args.include_parent_recovery)
        if loader.errors or suite.countTestCases() != expected_count:
            raise ValueError('Focused selection did not resolve: ' + str(loader.errors))
        stream = io.StringIO()
        started = time.perf_counter()
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
        log = stream.getvalue()
        report['focused_checks'] = dict(selection=list(selection), tests=result.testsRun,
            failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
            seconds=time.perf_counter() - started, results=getattr(result, 'outcomes', []),
            failure_details=[dict(test=test.id(), traceback=trace) for test, trace in result.failures + result.errors])
        if not args.parent_affected_only:
            from test_currency_wars_guide_actionability import EVIDENCE
            report['native_and_consumer_evidence'] = EVIDENCE
        if args.include_parent_recovery or args.parent_affected_only:
            from test_currency_wars_reward_recovery import EVIDENCE as PARENT_EVIDENCE
            report['parent_affected_consumer_evidence'] = PARENT_EVIDENCE
        passed = bool(result.wasSuccessful() and not result.skipped)
    report['retained_pairs'] = [diagnose_pair(path) for path in args.pair_spec]
    after, runtime_after = sources()
    data_after = artifacts()
    loaded = sorted({Path(module.__file__).resolve().relative_to(ROOT).as_posix()
        for module in tuple(sys.modules.values()) if getattr(module, '__file__', None)
        and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)})
    report.update(source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        source_unchanged=before == after and runtime == runtime_after, artifacts_unchanged=data_before == data_after,
        loaded_source_closure_missing=sorted(set(loaded) - set(before)))
    report['ok'] = bool(passed and report['source_unchanged'] and report['artifacts_unchanged']
        and not report['loaded_source_closure_missing'] and report['manifest_unchanged_from_pr29'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if log:
        args.output.with_suffix('.tests.txt').write_text(log, encoding='utf-8')
    focused = report.get('focused_checks') or {}
    print(json.dumps({key: report[key] for key in ('ok', 'source_file_count', 'artifact_file_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'artifacts_unchanged', 'loaded_source_closure_missing')}
        | {key: focused.get(key) for key in ('tests', 'failures', 'errors', 'skips', 'seconds')}
        | dict(retained_pair_count=len(report['retained_pairs']))))
    if not report['ok']:
        print(log)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
