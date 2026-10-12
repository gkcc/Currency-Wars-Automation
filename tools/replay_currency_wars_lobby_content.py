"""Frozen lobby-content checks and optional read-only retained PNG-pair replay.

The public record supplies five anchors and a difference box, not original
PNGs. Retained files stay local; pair results omit paths, rows and credentials.
No capture, game, GUI, broker, model service, READY change or installation.
"""
from __future__ import annotations

import argparse
import copy
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


BASE = '5d3818034197c40a1c3b13cf940208e0000fce82'
SELECTION = ('test_currency_wars_lobby_content.LobbyContentTests',)
EVIDENCE_COMMIT = '48600a89f64e9e3bf18bd7e5a47e287a10fc27cd'
EVIDENCE_BLOB = '40f4f7f17b86fe7309787ad6ed6db36b90277825'
EVIDENCE_PATH = 'handoff/2026-10-08/ROOT_PR22_NATIVE_LOBBY_GUARD_FAILURE.json'
FIXTURE_SOURCES = ('requirements.txt', 'tools/test_local_runtime_compatibility.py', EVIDENCE_PATH)
BUTTON_ROI = (1360, 930, 1810, 1020)
CONTENT_ROI = (1360, 930, 1810, 1019)


def hashes(root):
    from currency_wars_source_guard import production_files
    names = set(production_files(root)) | set(FIXTURE_SOURCES)
    names.add('tools/' + Path(__file__).name)
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_object(path):
    data = Path(path).read_bytes()
    if len(data) > 2_000_000:
        raise ValueError('Retained JSON exceeds its bound')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Retained JSON must be an object')
    return value, hashlib.sha256(data).hexdigest()


def replay_pair(spec_path):
    """Check original bytes/identities; do not refresh or repair old authority.

    schema=1 specifies request_json, reply_json, current_observation_json,
    original_png and current_png. Paths are relative to the local spec or
    absolute. Only the in-memory original_png location is remapped. Expired
    deadlines and live epoch/pending/foreground are not replayed or approved.
    """
    from PIL import Image
    from currency_wars_perception import fingerprint
    from currency_wars_runner import stable_lobby_entry_navigation

    spec_path = Path(spec_path).resolve()
    spec, spec_hash = read_object(spec_path)
    if type(spec.get('schema')) is not int or spec['schema'] != 1:
        raise ValueError('Retained pair specification schema is invalid')

    def local_path(key):
        value = spec.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError('Retained pair file is missing')
        return (spec_path.parent / value).resolve()

    request, request_hash = read_object(local_path('request_json'))
    reply, reply_hash = read_object(local_path('reply_json'))
    actual, actual_hash = read_object(local_path('current_observation_json'))
    request = copy.deepcopy(request)
    request['original_png'] = str(local_path('original_png'))
    original = request.get('observation', {})
    if not isinstance(original, dict):
        raise ValueError('Original observation must be an object')
    pngs, prints, full_pixels, content_pixels = [], [], [], []
    for key in ('original_png', 'current_png'):
        payload = local_path(key).read_bytes()
        if len(payload) > 25 * 1024 * 1024:
            raise ValueError('Retained PNG exceeds its bound')
        pngs.append(hashlib.sha256(payload).hexdigest())
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG' or image.size != (1920, 1080):
                raise ValueError('Pair must contain original bound 1920x1080 PNGs; no resizing')
            prints.append(fingerprint(image))
            full_pixels.append(image.crop(BUTTON_ROI).convert('RGB').tobytes())
            content_pixels.append(image.crop(CONTENT_ROI).convert('RGB').tobytes())
    identities_present = all(isinstance(obs.get(key), str) and bool(obs[key])
        for obs in (original, actual) for key in ('capture_request_id', 'frame_id', 'snapshot_id'))
    digests_match = (pngs[0] == request.get('snapshot_id') == original.get('snapshot_id')
                     and pngs[1] == actual.get('snapshot_id'))
    fingerprints_match = all(obs.get('fingerprint') == value
                             for obs, value in zip((original, actual), prints))
    request_reply_match = all(isinstance(request.get(key), str) and bool(request[key])
        and reply.get(key) == request[key] for key in ('request_id', 'snapshot_id', 'resume_epoch'))
    eligible = stable_lobby_entry_navigation(reply, request, actual, local_path('current_png'))
    return {
        'spec_sha256': spec_hash, 'request_json_sha256': request_hash,
        'reply_json_sha256': reply_hash, 'current_json_sha256': actual_hash,
        'png_sha256': pngs, 'original_source_identities_present': identities_present,
        'original_png_digests_match': digests_match,
        'original_fingerprints_match': fingerprints_match,
        'request_reply_identity_match': request_reply_match,
        'full_roi_pixels_equal': full_pixels[0] == full_pixels[1],
        'content_roi_pixels_equal': content_pixels[0] == content_pixels[1],
        'visual_eligibility': bool(eligible),
        'byte_bound_visual_eligibility': bool(eligible and identities_present and digests_match
                                              and fingerprints_match and request_reply_match),
        'native_capture_authentication_replayed': False,
        'current_epoch_pending_foreground_verified': False,
        'input_published': False, 'navigation_outcome_verified': False,
    }


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


def run_protocol():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    selected = [test.id() for test in cases(suite)]
    if loader.errors or len(selected) != 7 or any(not name.startswith(SELECTION[0] + '.test_') for name in selected):
        raise ValueError('Frozen 7-test selection did not resolve; refuse imported/old discovery')
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    return {
        'selection': list(SELECTION), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skips': len(result.skipped),
        'elapsed_seconds': time.perf_counter() - started, 'test_results': result.outcomes,
        'failure_details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors],
        'skip_details': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
        'ok': result.wasSuccessful() and not result.skipped,
    }, stream.getvalue()


def environment():
    versions = {}
    for name in ('numpy', 'Pillow', 'psutil', 'opencv-python'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {'system': platform.system(), 'python': platform.python_version(), 'packages': versions}


def run(root, output, *, protocol=False, pair_specs=()):
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    root = Path(root).resolve()
    if Path(__file__).resolve().parent != root / 'tools':
        raise ValueError('Driver and tested sources must come from the same candidate')
    before = hashes(root)
    from currency_wars_source_guard import production_files
    try:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                      text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    report = {
        'schema': 'currency-wars-lobby-content-acceptance/v1',
        'basis_commit': BASE, 'tested_checkout_head': head,
        'source_binding_authority': 'Exact before/after bytes; checkout HEAD does not identify uncommitted files.',
        'environment': environment(), 'source_file_count': len(before),
        'runtime_source_count': len(production_files(root)), 'source_sha256': before,
        'public_calibration': {'evidence_commit': EVIDENCE_COMMIT, 'git_blob': EVIDENCE_BLOB,
            'evidence_file_sha256': before[EVIDENCE_PATH], 'raw_png_public': False,
            'public_record_is_native_png_replay': False, 'public_record_is_classification_replay': False},
        'game_inputs': 0, 'controllers_started': 0, 'new_game_captures': 0, 'new_ocr_calls': 0,
        'private_resources_read_or_exported': False, 'candidate_installed': False,
        'ready_changed': False, 'whole_game_speedup': None, 'same_endpoint_speedup': None,
        'limits': [
            'Protocol frames and declared reader rows are generated fixtures, not native gameplay evidence.',
            'Public anchors and difference bounds do not replace the private retained PNG pair.',
            'Retained-pair replay checks supplied bytes and identities, not native capture authenticity or live authority.',
            'No game, GUI, broker, input, online model, READY change or installation is performed.',
            'A passed visual guard is not entry into a new match or a measured gameplay speed improvement.',
        ],
    }
    log = None
    if protocol:
        report['protocol'], log = run_protocol()
    report['retained_pairs'], report['pair_errors'] = [], []
    for spec in pair_specs:
        try:
            report['retained_pairs'].append(replay_pair(spec))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            # File paths/decoder messages can contain private runtime names.
            report['pair_errors'].append({'error_type': type(exc).__name__})
    after = hashes(root)
    differences = {key: {'before': before.get(key), 'after': after.get(key)}
        for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)}
    report.update(source_sha256_after_differences=differences,
        source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        source_unchanged=not differences, retained_png_pair_replayed=bool(report['retained_pairs']))
    report['ok'] = (not differences and report.get('protocol', {}).get('ok', True)
        and not report['pair_errors']
        and all(pair['byte_bound_visual_eligibility'] for pair in report['retained_pairs']))
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if log is not None:
        output.with_suffix('.tests.txt').write_text(log, encoding='utf-8')
    summary = {key: report[key] for key in ('ok', 'source_file_count', 'runtime_source_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'retained_png_pair_replayed')}
    if protocol:
        summary.update({key: report['protocol'][key] for key in
            ('tests', 'failures', 'errors', 'skips', 'elapsed_seconds')})
    print(json.dumps(summary))
    if protocol and not report['protocol']['ok']:
        print(log)
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--protocol', action='store_true')
    parser.add_argument('--pair-spec', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.protocol and not args.pair_spec:
        parser.error('Select --protocol or an existing --pair-spec')
    return run(args.root, args.output, protocol=args.protocol, pair_specs=args.pair_spec)


if __name__ == '__main__':
    raise SystemExit(main())
