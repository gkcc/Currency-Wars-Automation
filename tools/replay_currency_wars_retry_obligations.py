"""Only PR29-R1, PR30-R1 and the affected post-persistence recovery regression.

Uses the frozen PR30 fixture/source inventory without rerunning its old suite,
visual calibration, economy, GUI or Rust. No OCR/download/capture/game process.
Full canvases and successors are declared inert protocols, not live evidence.
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

import replay_currency_wars_guide_actionability as base

ROOT = Path(__file__).resolve().parents[1]
PARENT = '51798ecf7a62d8f778a799c130fc111fc2f836a5'
SELECTION = (
    'test_currency_wars_retry_obligations.RetryObligationTests.test_pre_persistence_refusal_requires_new_inspect_not_new_job_id',
    'test_currency_wars_retry_obligations.RetryObligationTests.test_refused_navigation_successor_preserves_prior_supervisor_obligation',
    'test_currency_wars_reward_recovery.RewardRecoveryTests.test_failed_read_only_report_retained_and_only_new_current_proof_may_retry',
)


def sources():
    values, runtime = base.sources()
    for name in ('tools/replay_currency_wars_retry_obligations.py',
                 'tools/test_currency_wars_retry_obligations.py'):
        values[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    return dict(sorted(values.items())), runtime


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['ORT_DISABLE_TELEMETRY'] = '1'
    os.environ['CW_NATIVE_REWARD_EVIDENCE_DIR'] = str(ROOT / 'handoff/2026-10-08/ROOT_NATIVE_REWARDS')
    report = dict(schema='currency-wars-retry-obligations/v1', basis_commit=PARENT,
        game_inputs=0, controllers_started=0, new_game_captures=0, native_ocr_calls=0,
        candidate_installed=False, ready_changed=False, whole_game_speedup=None,
        source_binding_authority='Exact before/after file bytes; no fabricated tested HEAD.',
        environment=dict(system=platform.system(), python=platform.python_version()),
        execution_scope='Two new consumer regressions plus one affected old method; inert parent fixtures only.')
    try:
        report['tested_checkout_head'] = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        report['tested_checkout_head'] = None
    stream = io.StringIO()
    try:
        before, runtime = sources()
        artifacts_before = base.artifacts()
        report.update(source_sha256=before, source_file_count=len(before),
            runtime_source_files=runtime, runtime_source_count=len(runtime),
            artifact_sha256=artifacts_before, artifact_file_count=len(artifacts_before))
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(SELECTION)
        if loader.errors or tuple(test_ids(suite)) != SELECTION:
            raise ValueError('Exact three-method selection did not resolve: ' + repr(loader.errors))
        started = time.perf_counter()
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=base.Result).run(suite)
        report['focused_checks'] = dict(selection=list(SELECTION), tests=result.testsRun,
            failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
            seconds=time.perf_counter() - started, results=getattr(result, 'outcomes', []),
            failure_details=[dict(test=test.id(), traceback=trace)
                             for test, trace in result.failures + result.errors])
        import test_currency_wars_retry_obligations as checks
        import test_currency_wars_reward_recovery as prior
        report['new_consumer_evidence'] = checks.EVIDENCE
        report['affected_parent_evidence'] = prior.EVIDENCE
        after, runtime_after = sources()
        artifacts_after = base.artifacts()
        loaded = sorted({Path(module.__file__).resolve().relative_to(ROOT).as_posix()
            for module in tuple(sys.modules.values()) if getattr(module, '__file__', None)
            and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)})
        report.update(source_set_sha256_before=base.digest(before), source_set_sha256_after=base.digest(after),
            source_sha256_after_differences={name: dict(before=before.get(name), after=after.get(name))
                for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)},
            source_unchanged=before == after and runtime == runtime_after,
            artifacts_unchanged=artifacts_before == artifacts_after,
            loaded_source_files=loaded, loaded_source_closure_missing=sorted(set(loaded) - set(before)),
            manifest_unchanged_from_pr30=after['tools/currency_wars_runtime_sources.json'] == base.BASE_MANIFEST_SHA256)
        report['ok'] = bool(result.wasSuccessful() and not result.skipped and report['source_unchanged']
            and report['artifacts_unchanged'] and not report['loaded_source_closure_missing']
            and report['manifest_unchanged_from_pr30'] and len(runtime) == 24)
    except Exception as error:
        import traceback
        report.update(ok=False, setup_or_finalization_error=dict(type=type(error).__name__,
            message=str(error), traceback=traceback.format_exc()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in ('ok', 'source_file_count', 'artifact_file_count',
        'source_set_sha256_before', 'source_set_sha256_after', 'loaded_source_closure_missing')}
        | {key: (report.get('focused_checks') or {}).get(key) for key in ('tests', 'failures', 'errors', 'skips', 'seconds')}))
    if not report['ok']:
        print(stream.getvalue())
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
