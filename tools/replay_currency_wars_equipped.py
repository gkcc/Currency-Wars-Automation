"""Focused, offline acceptance and actual saved-PNG equipment-tooltip reading.

This tool reads a file. It cannot capture, click, open a broker, install code or
query a model service. The optional target is caller intent, never native owner
evidence. A tooltip name does not close an actual unit/equipment-slot binding.
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
import re
import subprocess
import sys
import time
import unittest

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

BASE = '8666ac5ae1482f85e1c856073566e205b4ebf651'
SELECTION = (
    'test_currency_wars_equipped_reading.EquippedTooltipReadingTests',
    'test_currency_wars_equipped_reading.EquippedTooltipScopeTests',
    'test_currency_wars_equipped_consumers.EquippedTooltipConsumerTests',
)
DEFAULT_IMAGE = 'handoff/2026-10-07/reward-sequence/q02.png'
FIXTURE_SOURCES = (
    'tools/test_local_runtime_compatibility.py',
    DEFAULT_IMAGE,
    'handoff/2026-10-07/reward-sequence/manifest.json',
)


def hashes(root):
    from currency_wars_source_guard import production_files
    paths = set(production_files(root)) | set(FIXTURE_SOURCES)
    paths.add('tools/' + Path(__file__).name)
    paths.update('tools/' + name.split('.')[0] + '.py' for name in SELECTION)
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted(paths)}


def digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def failure_detail(test, trace):
    # Exact traces stay in the local .tests.txt file. A library assertion or
    # traceback may contain the account path; do not copy it to compact JSON.
    last = trace.rstrip().splitlines()[-1] if trace.rstrip() else ''
    kind = last.split(':', 1)[0]
    return dict(test=test.id(), error_type=kind if re.fullmatch(r'[A-Za-z_][\w.]*', kind) else 'see_local_trace',
                details='same output stem with .tests.txt; inspect/redact before publication')


def target_intent(args):
    values = (args.unit, args.row, args.board_slot, args.equipment_slot)
    if all(value is None for value in values):
        return None
    if (any(value is None for value in values) or not 1 <= len(args.unit.strip()) <= 32
            or args.row not in ('front', 'back')
            or not 1 <= args.board_slot <= (4 if args.row == 'front' else 6)
            or args.equipment_slot != 1):
        raise ValueError('intent requires one named native board slot and equipment slot 1; no coordinates')
    return dict(origin='caller_intent_not_native_evidence', unit_name=args.unit,
                row=args.row, board_slot=args.board_slot, equipment_slot=1,
                authorizes_input=False, proves_ownership=False)


def probe(root, path, expected_sha, target):
    from currency_wars_perception import Perception
    data = path.read_bytes()
    before = hashlib.sha256(data).hexdigest()
    if expected_sha is not None and before != expected_sha:
        raise ValueError('saved PNG bytes differ from the supplied SHA256')
    if path.resolve() == (root / DEFAULT_IMAGE).resolve():
        manifest = json.loads((path.parent / 'manifest.json').read_text(encoding='utf8'))
        source_sha = next(row['export_image_sha256'] for row in manifest['frames']
                          if row['file'] == path.name)
        if before != source_sha:
            raise ValueError('public retained PNG differs from its frozen source manifest')
        source = DEFAULT_IMAGE
        coverage = 'real_retained_preparation_negative_not_actual_unit_equipment_page'
    else:
        # Keep local directories, account names and other owner metadata out of
        # the small report. The input is bound by bytes, not by its filename.
        source = 'explicit_local_saved_png'
        coverage = 'new_local_read_source_not_automatically_calibrated_or_verified'
    reader = Perception()
    observed = reader.read(path, scope='equipment_tooltip')
    native = observed['semantic']['native_tooltips']
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    if (before != after or observed.get('snapshot_id') != before
            or native.get('snapshot_id') != before):
        raise ValueError('saved PNG or native read source changed during inspection')
    return dict(kind='real_saved_png_production_read', source=source, coverage=coverage,
        sha256=before, requested_target=target, page=observed['page'],
        native_tooltips=native, read_contract=observed['read_contract'],
        read_timing=observed['read_timing'],
        original_type_rows=[row for row in observed['rows']
            if row.get('raw_text', row.get('text')) in
                ('简易装备', '进阶装备', '消耗品', '前台', '后台', '前后台')],
        frame_protocol=None, capture_request_id=None, frame_id=None,
        immutable_action_chain_supplied=False, ownership_verified=False,
        all_equipment_checked=False, battle_ready=False,
        resource_context=dict(scope='this read only; no statement about ROOT resources',
            roster_templates_loaded=False, equipment_panel_calibration_available=False),
        limitation='Native title/type candidates only. No source-backed actual unit-panel/slot locator or original click chain is supplied.')


def run(args):
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    target = target_intent(args)
    before = hashes(root)
    stream, ids, result, elapsed = io.StringIO(), [], None, None
    if not args.read_only:
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(SELECTION)
        ids = [item.id() for item in cases(suite)]
        if loader.errors or not ids or len(ids) != len(set(ids)) or any(
                not any(name.startswith(prefix + '.') for prefix in SELECTION) for name in ids):
            raise ValueError('explicit equipment selection failed; no fallback discovery')
        started = time.perf_counter()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        elapsed = time.perf_counter() - started
    native, native_error = None, None
    if not args.tests_only:
        try:
            native = probe(root, args.image or root / DEFAULT_IMAGE, args.expected_sha256, target)
        except Exception as error:
            # Native library/file errors may include a Windows account path.
            # Keep diagnostics useful without publishing local owner metadata.
            native_error = dict(type=type(error).__name__, errno=getattr(error, 'errno', None),
                                stage='saved_png_production_read')
    after = hashes(root)
    changes = {path: [before.get(path), after.get(path)] for path in set(before) | set(after)
               if before.get(path) != after.get(path)}
    try:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    from currency_wars_source_guard import production_files
    report = dict(schema='currency-wars-equipment-tooltip-acceptance/v1', basis_commit=BASE,
        tested_checkout_head=head,
        source_binding_authority='exact file bytes before/after; HEAD alone does not identify working-tree changes',
        environment=dict(system=platform.system(), python=platform.python_version(),
            packages={name: importlib.metadata.version(name) for name in
                      ('Pillow', 'numpy', 'opencv-python', 'rapidocr-onnxruntime', 'psutil')}),
        selection=list(SELECTION) if result is not None else [], test_ids=ids,
        tests=result.testsRun if result is not None else 0,
        failures=len(result.failures) if result is not None else 0,
        errors=len(result.errors) if result is not None else 0,
        skips=len(result.skipped) if result is not None else 0,
        failure_details=[failure_detail(test, trace) for test, trace in
            result.failures + result.errors] if result is not None else [],
        elapsed_seconds=elapsed, tests_requested=result is not None,
        source_count=len(before), runtime_source_count=len(production_files(root)),
        source_sha256=before, source_sha256_after_differences=changes,
        source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
        real_png_probe_requested=not args.tests_only,
        real_png_probe=native, real_png_probe_error=native_error,
        ownership_verified=False, real_equipment_positive_coverage=0,
        installed=False, live_automation_verified=False, whole_game_speedup=None)
    report['ok'] = (result is None or result.wasSuccessful() and not result.skipped) and not changes and native_error is None
    # Files are closed before any caller assertion/removal, including Windows.
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    if result is not None:
        output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf8')
    print(json.dumps({key: report[key] for key in ('ok', 'tests_requested', 'tests', 'failures',
        'errors', 'skips', 'elapsed_seconds', 'source_count', 'runtime_source_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'real_png_probe_error')}, ensure_ascii=False))
    if not report['ok'] and result is not None:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--tests-only', action='store_true', help='Only the new explicit test selection, no saved-PNG OCR.')
    mode.add_argument('--read-only', action='store_true', help='Only saved-PNG production reading, no tests and no capture.')
    parser.add_argument('--image', type=Path, help='Existing local PNG; defaults to the public retained preparation negative.')
    parser.add_argument('--expected-sha256', help='Optional expected SHA256 of the input PNG.')
    parser.add_argument('--unit', help='Caller intent only; never fills native owner/name evidence.')
    parser.add_argument('--row', choices=('front', 'back'))
    parser.add_argument('--board-slot', type=int)
    parser.add_argument('--equipment-slot', type=int)
    args = parser.parse_args()
    if args.tests_only and any(value is not None for value in
            (args.image, args.expected_sha256, args.unit, args.row, args.board_slot, args.equipment_slot)):
        parser.error('PNG/target arguments require a saved-PNG read')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
