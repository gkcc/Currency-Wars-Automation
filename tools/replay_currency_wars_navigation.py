"""Frozen offline integration checks and retained navigation-pair eligibility.

No capture, GUI, controller initialization, action publication, or model service.
Pair files remain local; the compact result contains no rows, paths, or tokens.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import platform
import time
import unittest

from PIL import Image

from currency_wars_perception import fingerprint, hash_distance
from currency_wars_source_guard import production_files
from currency_wars_visual_guards import navigation_target, stable_semantic_plan


PROJECT = Path(__file__).resolve().parents[1]
BASELINE = '290b9b9006309c02f2542931d97eb5f8b0a045b1'
SELECTORS = (
    'test_currency_wars_navigation.NavigationTests',
    'test_currency_wars_source_runtime.SourceRuntimeTests',
    'test_local_runtime_compatibility.RuntimeRootTests.test_public_start_passes_verified_root_and_invalid_config_never_launches',
    'test_local_runtime_compatibility.RuntimeRootTests.test_worker_marker_child_registration_and_cleanup_keep_selected_root',
    'test_currency_wars_badge_consumers.BadgeConsumerTests.test_badge_eligibility_preserves_current_worker_policy_guards',
)
EXTRA_SOURCES = (
    'gui/src/main.rs', 'gui/src/source_guard.rs', 'gui/src/protocol.rs', 'tools/check_install.py',
    'tools/replay_currency_wars_navigation.py', 'tools/test_currency_wars_navigation.py',
    'tools/test_currency_wars_source_runtime.py', 'tools/test_local_runtime_compatibility.py',
    'tools/test_currency_wars_badge_consumers.py',
)


def hashes():
    return {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest()
            for name in sorted(set(production_files(PROJECT)) | set(EXTRA_SOURCES))}


def read_object(path):
    data = Path(path).read_bytes()
    if len(data) > 2_000_000:
        raise ValueError('Retained JSON exceeds its bound')
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError('Retained JSON must be an object')
    return result, hashlib.sha256(data).hexdigest()


def replay_pair(spec_path):
    """Use original request/rows/identities; never manufacture a missing frame.

    spec schema=1, with request_json/reply_json/current_observation_json,
    original_png/current_png paths (absolute or relative to the local spec).
    This is a read-only visual replay, not a deadline/epoch/receipt approval.
    """
    spec_path = Path(spec_path).resolve()
    spec, spec_hash = read_object(spec_path)
    if type(spec.get('schema')) is not int or spec['schema'] != 1:
        raise ValueError('Retained pair specification schema is invalid')
    def path(key):
        name = spec.get(key)
        if not isinstance(name, str) or not name:
            raise ValueError('Retained pair file is missing: ' + key)
        return (spec_path.parent / name).resolve()
    request, request_hash = read_object(path('request_json'))
    reply, reply_hash = read_object(path('reply_json'))
    actual, actual_hash = read_object(path('current_observation_json'))
    request = copy.deepcopy(request)
    # A relocated retained file is acceptable only with the original SHA.
    # All original capture/frame fields stay exactly as supplied.
    request['original_png'] = str(path('original_png'))
    original = request.get('observation', {})
    pngs, prints = [], []
    for key in ('original_png', 'current_png'):
        payload = path(key).read_bytes()
        pngs.append(hashlib.sha256(payload).hexdigest())
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                raise ValueError('Pair must contain native 1920x1080 PNGs')
            prints.append(fingerprint(image))
    identities_present = all(isinstance(obs.get(key), str) and bool(obs[key])
        for obs in (original, actual) for key in ('capture_request_id', 'frame_id', 'snapshot_id'))
    digests_match = (pngs[0] == request.get('snapshot_id') == original.get('snapshot_id')
                     and pngs[1] == actual.get('snapshot_id'))
    fingerprints_match = all(obs.get('fingerprint') == value
                             for obs, value in zip((original, actual), prints))
    diagnostic = {}
    eligible = stable_semantic_plan(reply, request, actual, path('current_png'), diagnostic)
    return dict(spec_sha256=spec_hash, request_json_sha256=request_hash,
                reply_json_sha256=reply_hash, current_json_sha256=actual_hash,
                png_sha256=pngs, page=original.get('page'),
                control_id=navigation_target((reply.get('actions') or [{}])[0], request),
                actual_fingerprint_distance=hash_distance(*prints),
                original_source_identities_present=identities_present,
                original_png_digests_match=digests_match,
                original_fingerprints_match=fingerprints_match,
                visual_eligibility=eligible,
                byte_bound_visual_eligibility=bool(eligible and digests_match and fingerprints_match),
                native_capture_authentication_replayed=False,
                current_epoch_pending_foreground_verified=False,
                input_published=False, navigation_outcome_verified=False)


def run_protocol():
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromNames(SELECTORS)
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return dict(selectors=list(SELECTORS), tests_run=result.testsRun,
                failures=[dict(test=str(test), traceback=error) for test, error in result.failures],
                errors=[dict(test=str(test), traceback=error) for test, error in result.errors],
                skipped=[dict(test=str(test), reason=reason) for test, reason in result.skipped],
                passed=result.wasSuccessful() and not result.skipped,
                seconds=time.perf_counter()-started), stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', action='store_true')
    parser.add_argument('--pair-spec', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.protocol and not args.pair_spec:
        parser.error('Select --protocol or an existing --pair-spec')
    before = hashes()
    report = dict(schema='currency-wars-navigation-integration/v1', baseline_sha=BASELINE,
                  python=platform.python_version(), platform=platform.system(), source_sha256=before,
                  game_inputs=0, controllers_started=0, new_game_captures=0,
                  candidate_installed=False, rust_compilation_verified=False,
                  true_navigation_success_pairs=0, whole_game_speed_verified=False)
    if args.protocol:
        report['protocol'], log = run_protocol()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix('.tests.txt').write_text(log, encoding='utf8')
    report['retained_pairs'] = [replay_pair(spec) for spec in args.pair_spec]
    after = hashes()
    report['source_sha256_after_differences'] = {name: digest for name, digest in after.items()
                                               if before.get(name) != digest}
    report['source_set_sha256_before'] = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    report['source_set_sha256_after'] = hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()
    report['source_unchanged'] = before == after
    passed = (report['source_unchanged'] and report.get('protocol', {}).get('passed', True)
              and all(pair['byte_bound_visual_eligibility'] for pair in report['retained_pairs']))
    report['checks_passed'] = passed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(dict(output=str(args.output), source_unchanged=report['source_unchanged'],
                          protocol=report.get('protocol'), retained_pair_count=len(report['retained_pairs'])),
                     ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
