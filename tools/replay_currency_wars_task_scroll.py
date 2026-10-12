"""Focused offline acceptance for the PR31 task-list scroll successor.

Run only the ten exact new consumer methods. No old guide-navigation suite,
native OCR, controller, live capture, installation, GUI, or readiness mutation.
The two 52x35 native crops calibrate the target; complete frames, source rows,
historical unknown records, and scroll successors remain declared protocols.
"""
from __future__ import annotations

import argparse
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


ROOT = Path(__file__).resolve().parents[1]
PARENT = 'd2477df7c5620fe606340e068642bf684445a5fb'
EVIDENCE_COMMIT = '0e43fadea064a52141fdd3b322ba4a8b14b04a62'
MANIFEST_SHA256 = '1c90624daed8f1a3ff58656238685b901174a7ad7fc927427f511cedae0b0668'
SELECTION = (
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_native_pair_scroll_once_verifies_displacement_and_preserves_unknown',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_changed_position_cover_text_and_overlay_refuse_before_input',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_wrong_source_bytes_capture_and_current_frame_refuse_through_consumers',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_current_phase_invalidated_after_queue_and_inside_entry_lock_refuses',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_request_and_manual_deadline_crossed_during_final_local_guard_refuse',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_unverified_postconditions_return_pending_without_extra_reads_or_replay',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_missing_notification_counts_one_readonly_fallback_without_resending_scroll',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_caller_preflight_missing_text_and_outside_point_create_no_job_or_capture',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_critical_actions_cannot_reuse_task_scroll_marker',
    'test_currency_wars_task_scroll.TaskScrollConsumerTests.test_unmarked_scroll_still_requires_byte_identical_target',
)
SUPPORT = (
    'tools/test_local_runtime_compatibility.py',
    'tools/replay_currency_wars_preparation.py',
    'tools/test_currency_wars_task_scroll.py',
    'tools/replay_currency_wars_task_scroll.py',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sources():
    from currency_wars_source_guard import production_files
    runtime = sorted(production_files(ROOT))
    names = sorted(set(runtime) | set(SUPPORT))
    return {name: sha(ROOT / name) for name in names}, runtime


def artifacts():
    names = ['handoff/2026-10-08/ROOT_PR31_SCROLL_GUARD/' + name
             for name in ('diagnosis.json', 'source-progress.png', 'actual-progress.png')]
    if (ROOT / 'docs/GAME_KNOWLEDGE.json').exists():
        names.append('docs/GAME_KNOWLEDGE.json')
    return {name: sha(ROOT / name) for name in sorted(names)}


def runtime_binding():
    from currency_wars_source_guard import resource_snapshot, runtime_provider
    value = dict(resource_inventory=None, resource_sha256=None, runtime_provider=None,
                 resource_binding_error=None, runtime_provider_error=None)
    try:
        resources = resource_snapshot(ROOT)
        value.update(resource_inventory=resources['resource_inventory'], resource_sha256=resources['hashes'])
    except (OSError, ValueError) as error:
        value['resource_binding_error'] = dict(type=type(error).__name__, message=str(error))
    try:
        value['runtime_provider'] = runtime_provider()
    except (OSError, ValueError) as error:
        value['runtime_provider_error'] = dict(type=type(error).__name__, message=str(error))
    return value


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


class Result(unittest.TextTestResult):
    """Exact method outcomes and subcase counts, using normal unittest results."""
    def startTest(self, test):
        self.started = time.perf_counter()
        self.before_problems = len(self.failures) + len(self.errors) + len(self.skipped)
        if not hasattr(self, 'outcomes'):
            self.outcomes, self.subcases = [], []
        super().startTest(test)

    def addSubTest(self, test, subtest, err):
        self.subcases.append(dict(test=subtest.id(), status='failed' if err is not None else 'passed'))
        super().addSubTest(test, subtest, err)

    def stopTest(self, test):
        failed = len(self.failures) + len(self.errors) + len(self.skipped) != self.before_problems
        self.outcomes.append(dict(test=test.id(), status='failed' if failed else 'passed',
                                 seconds=time.perf_counter()-self.started))
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-runtime-resources', action='store_true',
        help='Also require a complete actual runtime resource/provider binding; public source archives may omit resources')
    args = parser.parse_args()
    packages = {}
    for name in ('numpy', 'Pillow', 'psutil', 'opencv-python'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    report = dict(schema='currency-wars-task-scroll/v1', basis_commit=PARENT,
        evidence_commit=EVIDENCE_COMMIT,
        source_binding_authority='Exact before/after bytes; HEAD alone does not certify uncommitted source.',
        environment=dict(system=platform.system(), python=platform.python_version(), packages=packages),
        execution_scope='Ten exact new methods through existing manual mailbox, Worker and Entry consumers.',
        game_inputs=0, controllers_started=0, new_game_captures=0, native_ocr_calls=0,
        online_model_called=False, candidate_installed=False, ready_changed=False,
        require_runtime_resources=args.require_runtime_resources,
        old_navigation_suite_rerun=False, public_original_full_game_pngs_available=False,
        natural_scroll_success_verified=False, whole_game_speedup=None,
        native_scope='Two unedited 52x35 crops only; complete canvases/OCR/successors/historical records are declared fixtures.')
    try:
        report['tested_checkout_head'] = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        report['tested_checkout_head'] = None
    stream = io.StringIO()
    try:
        before, runtime = sources()
        data_before, binding_before = artifacts(), runtime_binding()
        report.update(source_sha256=before, source_file_count=len(before),
            runtime_source_files=runtime, runtime_source_count=len(runtime),
            artifact_sha256=data_before, artifact_file_count=len(data_before),
            runtime_resource_sha256=binding_before['resource_sha256'],
            runtime_resource_count=(len(binding_before['resource_sha256'])
                                    if binding_before['resource_sha256'] is not None else None),
            runtime_resource_inventory=binding_before['resource_inventory'],
            runtime_provider=binding_before['runtime_provider'],
            runtime_resource_binding_complete=binding_before['resource_sha256'] is not None,
            runtime_resource_binding_error=binding_before['resource_binding_error'],
            runtime_provider_error=binding_before['runtime_provider_error'],
            optional_static_knowledge_present='docs/GAME_KNOWLEDGE.json' in data_before)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(SELECTION)
        if loader.errors or tuple(test_ids(suite)) != SELECTION:
            raise ValueError('The exact ten-method selection did not resolve: ' + repr(loader.errors))
        from test_currency_wars_task_scroll import EVIDENCE
        EVIDENCE.clear()
        started = time.perf_counter()
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
        report['focused_checks'] = dict(selection=list(SELECTION), tests=result.testsRun,
            failures=len(result.failures), errors=len(result.errors), skips=len(result.skipped),
            seconds=time.perf_counter()-started, results=result.outcomes,
            subcase_count=len(result.subcases), subcases=result.subcases,
            failure_details=[dict(test=test.id(), traceback=trace)
                             for test, trace in result.failures + result.errors])
        report['native_and_consumer_evidence'] = EVIDENCE
        after, runtime_after = sources()
        data_after, binding_after = artifacts(), runtime_binding()
        loaded = sorted({Path(module.__file__).resolve().relative_to(ROOT).as_posix()
            for module in tuple(sys.modules.values()) if getattr(module, '__file__', None)
            and str(module.__file__).endswith('.py') and Path(module.__file__).resolve().is_relative_to(ROOT)})
        report.update(source_set_sha256_before=digest(before), source_set_sha256_after=digest(after),
            source_sha256_after_differences={name: dict(before=before.get(name), after=after.get(name))
                for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)},
            source_unchanged=before == after and runtime == runtime_after,
            artifacts_unchanged=data_before == data_after,
            optional_runtime_bindings_unchanged=binding_before == binding_after,
            runtime_resources_unchanged=(binding_before['resource_sha256'] == binding_after['resource_sha256']
                and binding_before['resource_inventory'] == binding_after['resource_inventory']
                if binding_before['resource_sha256'] is not None else None),
            runtime_provider_unchanged=(binding_before['runtime_provider'] == binding_after['runtime_provider']
                if binding_before['runtime_provider'] is not None else None),
            loaded_source_files=loaded, loaded_source_closure_missing=sorted(set(loaded)-set(before)),
            manifest_unchanged_from_pr31=after['tools/currency_wars_runtime_sources.json'] == MANIFEST_SHA256)
        report['ok'] = bool(result.wasSuccessful() and not result.skipped
            and report['source_unchanged'] and report['artifacts_unchanged']
            and report['optional_runtime_bindings_unchanged']
            and (not args.require_runtime_resources or report['runtime_resource_binding_complete']
                 and report['runtime_provider'] is not None)
            and not report['loaded_source_closure_missing']
            and report['manifest_unchanged_from_pr31'] and len(runtime) == 24)
    except Exception as error:
        import traceback
        report.update(ok=False, setup_or_finalization_error=dict(type=type(error).__name__,
            message=str(error), traceback=traceback.format_exc()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in ('ok', 'source_file_count', 'artifact_file_count',
        'runtime_source_count', 'runtime_resource_count', 'source_set_sha256_before',
        'source_set_sha256_after', 'loaded_source_closure_missing')}
        | {key: (report.get('focused_checks') or {}).get(key)
           for key in ('tests', 'subcase_count', 'failures', 'errors', 'skips', 'seconds')}))
    if not report['ok']:
        print(stream.getvalue())
        if report.get('setup_or_finalization_error'):
            print(report['setup_or_finalization_error']['traceback'])
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
