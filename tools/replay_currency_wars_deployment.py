"""Focused deployment acceptance plus a separate saved-PNG production read.

No game, native controller, broker process, GUI, installation or model service.
The explicit new test selection uses inert protocols; its timing is not play
speed. The retained PNG probe calls the unchanged local OCR engine pipeline.
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

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

BASE = '8bcff97b5a9287a6ffda405ee33a17473d1e32ac'
SELECTION = (
    'test_currency_wars_deployment_roles.DeploymentRolesTests',
    'test_currency_wars_deployment_reading.DeploymentReadingTests',
    'test_currency_wars_deployment_reading.DeploymentScopeTests',
    'test_currency_wars_deployment_flow.DeploymentFlowTests',
    'test_currency_wars_deployment_pending.DeploymentPendingTests',
    'test_currency_wars_deployment_runtime.DeploymentRuntimeTests',
)
FIXTURE_SOURCES = (
    'tools/test_local_runtime_compatibility.py',
    'tools/replay_currency_wars_preparation.py',
    'tools/test_currency_wars_source_runtime.py',
    'handoff/2026-10-07/reward-sequence/q02.png',
    'handoff/2026-10-07/reward-sequence/manifest.json',
)
SLOTS = [{'row': 'bench', 'slot': 1}, {'row': 'front', 'slot': 4}]


def hashes(root):
    from currency_wars_source_guard import production_files
    names = set(production_files(root)) | set(FIXTURE_SOURCES)
    names.add('tools/' + Path(__file__).name)
    names.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    # Only these already public byte-fixture assets are copied by the selected
    # runtime source test; no private shop/state-reader resource is exported.
    for folder in ('refresh_offer_resources', 'reward_resources'):
        names.update(p.relative_to(root).as_posix() for p in (root / 'tools' / folder).rglob('*') if p.is_file())
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def probe(root):
    from currency_wars_perception import Perception
    from currency_wars_deployment import project
    path = root / 'handoff/2026-10-07/reward-sequence/q02.png'
    manifest = json.loads((path.parent / 'manifest.json').read_text(encoding='utf8'))
    expected = next(item['export_image_sha256'] for item in manifest['frames'] if item['file'] == path.name)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('retained public PNG differs from its existing source manifest')
    reader = Perception()
    observed = reader.read(path, scope='deployment', deployment_slots=SLOTS)
    state = observed.get('state_read') or {}
    shop_resources = root / 'tools/shop_reader_resources'
    local_files = [p for p in shop_resources.rglob('*') if p.is_file()]
    return dict(kind='real_retained_png_production_read', source=path.relative_to(root).as_posix(),
        sha256=expected, requested_slots=SLOTS,
        # Selection for evidence projection only, not an authorized game plan.
        actual=project(observed, dict(source=SLOTS[0], target=SLOTS[1])), read_contract=observed.get('read_contract'),
        read_timing=observed.get('read_timing'),
        team_checked=(state.get('team') or {}).get('checked'),
        team_fully_read=(state.get('team') or {}).get('fully_read'),
        selected_statuses=[{key: slot.get(key) for key in ('row', 'slot', 'status', 'name', 'position', 'reasons')}
            for slot in (state.get('team') or {}).get('slots', [])
            if {'row': slot.get('row'), 'slot': slot.get('slot')} in SLOTS],
        unrelated_not_read=sum(s.get('status') == 'not_read' for s in (state.get('team') or {}).get('slots', [])),
        resource_context=dict(scope='this process only; not ROOT resource availability',
            shop_tree_files=len(local_files), shop_tree_bytes=sum(p.stat().st_size for p in local_files),
            native_resource_version=state.get('resource_version')),
        frame_protocol=None, capture_request_id=None, equipment_verified=False,
        deployment_effect_verified=False,
        limitation='Saved PNG read only; no real named before/after successor or immutable action receipt supplied.')


def run(output, *, run_probe=True):
    root = Path(__file__).resolve().parents[1]
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    before = hashes(root)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(SELECTION)
    ids = [item.id() for item in cases(suite)]
    if loader.errors or not ids or len(ids) != len(set(ids)) or any(
            not any(name.startswith(prefix + '.') for prefix in SELECTION) for name in ids):
        raise ValueError('explicit deployment selection failed; no fallback discovery')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    elapsed = time.perf_counter() - started
    native, native_error = None, None
    if run_probe:
        try:
            native = probe(root)
        except Exception as error:
            native_error = {'type': type(error).__name__, 'message': str(error)}
    after = hashes(root)
    changes = {name: [before.get(name), after.get(name)] for name in set(before) | set(after)
               if before.get(name) != after.get(name)}
    try:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    from test_currency_wars_deployment_flow import EVIDENCE
    from currency_wars_source_guard import production_files
    report = dict(schema='currency-wars-deployment-acceptance/v1', basis_commit=BASE,
        tested_checkout_head=head, source_binding_authority='exact file bytes before/after; HEAD alone does not identify working-tree changes',
        environment=dict(system=platform.system(), python=platform.python_version()),
        selection=list(SELECTION), tests=result.testsRun, test_ids=ids,
        failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
        failure_details=[dict(test=test.id(), traceback=trace) for test, trace in result.failures + result.errors],
        elapsed_seconds=elapsed, source_count=len(before), runtime_source_count=len(production_files(root)),
        source_sha256=before, source_sha256_after_differences=changes,
        source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        protocol_measurements=EVIDENCE, real_png_probe=native, real_png_probe_error=native_error,
        real_png_probe_requested=run_probe, live_automation_verified=False, installed=False,
        equipment_result_source_available=False, whole_game_speedup=None,
        raw_test_output='same output stem with .tests.txt; produced locally, not implicitly published')
    report['ok'] = result.wasSuccessful() and not result.skipped and not changes and not native_error
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf8')
    print(json.dumps({key: report[key] for key in ('ok', 'tests', 'failures', 'errors', 'skips',
        'elapsed_seconds', 'source_count', 'runtime_source_count', 'source_set_sha256_before',
        'source_set_sha256_after', 'real_png_probe_error')}, ensure_ascii=False))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tests-only', action='store_true', help='Skip the separate real-PNG OCR probe, record the omission.')
    args = parser.parse_args()
    return run(args.output, run_probe=not args.tests_only)


if __name__ == '__main__':
    sys.exit(main())
