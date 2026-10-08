"""Standard-artifact adapter for the frozen, single-window safety broker.

Only directory provenance and bounded request transport live here. All game
input, pause, foreground and process guards remain in the pinned broker.
"""
import argparse
import contextlib
from datetime import datetime
import errno
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import secrets
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

import currency_wars_artifacts as artifacts

SOURCE = Path(__file__).with_name('currency_wars_control.py')
PINNED = 'E499928DC305815B21D1F06314D34D7FF758C44433F2F27A108DD716090F496F'


def read_json(path, limit=2_000_000):
    path = Path(path)
    deadline = time.monotonic() + .8
    while True:
        try:
            if path.stat().st_size > limit:
                raise ValueError('JSON size bound exceeded')
            value = json.loads(path.read_text(encoding='utf8'))
            if not isinstance(value, dict):
                raise ValueError('expected a JSON object')
            return value
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32) or time.monotonic() >= deadline:
                raise
            time.sleep(.025)


class ObservationUnavailable(ValueError):
    """The receipt's frame is unusable; only a fresh observe may be requested."""


def _observation_paths(run, result):
    if not isinstance(result, dict):
        raise ValueError('observation receipt is missing')
    observation, request_id = result.get('observation'), result.get('id')
    if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 100
            or not isinstance(observation, dict) or type(observation.get('frame_protocol')) is not int
            or observation.get('frame_protocol') != 1
            or observation.get('request_id') != request_id
            or not re.fullmatch(r'[0-9a-f]{32}', str(observation.get('frame_id', '')))
            or not isinstance(observation.get('captured_at'), str)
            or datetime.fromisoformat(observation['captured_at']).tzinfo is None):
        raise ValueError('immutable observation receipt identity or capture time is unavailable')
    run = Path(run).absolute()
    frame_dir = run / 'frames' / (hashlib.sha256(request_id.encode()).hexdigest() + '-' + observation['frame_id'])
    if (observation.get('snapshot') != str(frame_dir / 'preview.png')
            or observation.get('original') != str(frame_dir / 'original.png')):
        raise ValueError('observation path does not belong to its request')
    return frame_dir, observation


def _no_frame_links(path):
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('observation path contains a link or junction')


def _frame_payload(path, observation, role):
    _no_frame_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 64 * 1024 * 1024:
        raise ValueError('observation file type or size is invalid')
    expected, size = observation.get(role + '_sha256'), observation.get(role + '_size')
    if (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)
            or not isinstance(size, list) or len(size) != 2
            or any(type(value) is not int or not 1 <= value <= 16384 for value in size)):
        raise ValueError('observation hash or dimensions are invalid')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('observation hash mismatch')
    return payload, size


def observation_frame(run, result, *, original=False):
    """Validate and return this receipt's immutable frame, without input retries.

    Bytes are read once and fully decoded from memory. The path remains stable
    after this function returns because publication never reuses frame names.
    Old mutable screenshot receipts deliberately do not satisfy this contract.
    """
    from PIL import Image
    try:
        frame_dir, observation = _observation_paths(run, result)
        role, filename = ('original', 'original.png') if original else ('snapshot', 'preview.png')
        path = frame_dir / filename
        # Reading one immutable payload prevents an open/load race. Do not use
        # LOAD_TRUNCATED_IMAGES, relax confidence, or recover an older image.
        payload, size = _frame_payload(path, observation, role)
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != 'PNG' or list(image.size) != size or (not original and image.size != (1920, 1080)):
                raise ValueError('observation format or dimensions mismatch')
            image.load()
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        return path
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, Image.DecompressionBombError) as error:
        raise ObservationUnavailable(str(error) + '; request only a new observation, never resend input') from error


def release_observation(run, result):
    """Explicitly release a consumed, final receipt's pair; never remove ledger.

    Callers must finish all reads and copy any durable proof first. Each caller
    releases only its own request, so another request's readers keep their files.
    An old receipt still returns its original input outcome after release; it
    cannot be republished to obtain the screenshot again.
    """
    run = Path(run).absolute()
    try:
        frame_dir, observation = _observation_paths(run, result)
        ledger = run / 'request-ledger' / (hashlib.sha256(result['id'].encode()).hexdigest() + '.json')
        _no_frame_links(ledger)
        receipt = read_json(ledger)
        if (receipt.get('id') != result['id'] or not isinstance(receipt.get('request'), dict)
                or receipt['request'].get('id') != result['id']
                or receipt.get('result') != result):
            raise ValueError('frame release requires this exact final receipt; pending or altered result is retained')
        _no_frame_links(frame_dir.parent)
        if not frame_dir.exists() and not frame_dir.is_symlink():
            return {'released': False, 'already_released': True, 'released_bytes': 0}
        _no_frame_links(frame_dir)
        paths = [(frame_dir / 'original.png', 'original'), (frame_dir / 'preview.png', 'snapshot')]
        present = {path.name for path in frame_dir.iterdir()}
        if not present.issubset({'original.png', 'preview.png'}):
            raise ValueError('frame directory has unrecognized contents; retained')
        # Validate every remaining file before any removal. Hash checks bind
        # release to the published bytes; readers already performed full decode.
        payloads = [(path, len(_frame_payload(path, observation, role)[0]))
                    for path, role in paths if path.name in present]
        for path, unused_size in payloads:
            path.unlink()
        frame_dir.rmdir()
        return {'released': True, 'already_released': False,
                'released_bytes': sum(size for unused_path, size in payloads)}
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ObservationUnavailable(str(error) + '; frame retained where possible; never resend input') from error


def backend():
    payload = SOURCE.read_bytes()
    if hashlib.sha256(payload).hexdigest().upper() != PINNED:
        raise ValueError('safety broker source changed; no operation permitted')
    spec = importlib.util.spec_from_file_location('currency_wars_safety', SOURCE)
    module = importlib.util.module_from_spec(spec)
    if SOURCE.read_bytes() != payload:
        raise ValueError('safety broker source changed during loading; no operation permitted')
    # Execute exactly the verified bytes, never a second loader read or pyc.
    exec(compile(payload, str(SOURCE), 'exec'), module.__dict__)
    return module


def authenticate(run, chat, token, control):
    """Emergency authentication never parses binding or display state."""
    run = Path(run).absolute()
    # RunAs can inherit Explorer's old TEMP value. Validate the declared run
    # root instead of substituting the elevated process's temp directory.
    marker = artifacts.read_marker(run, root=run.parent)
    owner = read_json(run / 'owner.json')
    if (owner.get('owner') != 'currency-wars-control' or owner.get('chat_id') != chat
            or not secrets.compare_digest(str(owner.get('run_token', '')), token)
            or owner.get('artifact_run_id') != marker['run_id']
            or owner.get('artifact_chat_id') != marker.get('session_hint', {}).get('id')):
        raise ValueError('standard artifact / fixed broker ownership mismatch')
    control.ROOT, control.OWNER = str(run), owner
    return run, owner


def load(run, chat, token, control):
    run, owner = authenticate(run, chat, token, control)
    binding = read_json(run / 'binding.json')
    control.BINDING = binding
    return run, owner, binding


def owned_broker_identity(control, run):
    try:
        identity = read_json(run / 'broker-process.json')
    except FileNotFoundError:
        return None
    control.assert_owner(identity.get('chat_id'), identity.get('run_token'))
    if type(identity.get('pid')) is not int or not isinstance(identity.get('creation_id'), (str, int)):
        raise ValueError('broker persistent identity malformed; no guessed PID')
    return identity


def exit_probe(control, pid, expected_creation):
    probe = control.process_probe(pid, expected_creation)
    return {'pid': int(pid), **probe, 'expected_creation_id': str(expected_creation)}


def emergency_status(control, run):
    """Only owner, PID+creation and matching pause ACK; no foreground APIs."""
    identity = owned_broker_identity(control, run) or getattr(control, 'EMERGENCY_BROKER_IDENTITY', None)
    probe = (exit_probe(control, identity['pid'], identity['creation_id']) if identity else
             {'state': 'unknown', 'error': 'broker not yet identified'})
    pause = control.read_optional('manual-pause.json')
    ack = control.read_optional('pause-ack.json')
    for value in (pause, ack):
        if value:
            control.assert_owner(value.get('chat_id'), value.get('run_token'))
    acknowledged = bool(identity and pause and ack and probe['state'] == 'running'
        and ack.get('pause_id') == pause.get('pause_id') and ack.get('acknowledged') is True
        and ack.get('owned_inputs_released') is True and ack.get('broker_pid') == identity['pid']
        and ack.get('broker_creation_time') == identity['creation_id'])
    return {'protocol_version': 2, 'run_dir': str(run), 'chat_id': control.OWNER['chat_id'],
        'ready': bool(identity and probe['state'] == 'running' and (run / 'broker-ready.json').exists()),
        'broker_state': probe, 'broker_pid': identity.get('pid') if identity else None,
        'broker_creation_time': identity.get('creation_id') if identity else None,
        'paused': bool(pause), 'input_halted': (run / 'input-halted.json').exists(),
        'pause_id': pause.get('pause_id') if pause else None, 'acknowledged': acknowledged,
        'ack_time': ack.get('time') if acknowledged else None, 'game_foreground': None,
        'game': {'error': 'emergency channel does not read binding or restore focus'},
        'reason': pause.get('reason') if pause else None}


def emergency_pause(control, run, reason):
    # The frozen broker's own guard consumes this owner-checked pause record.
    value = control.latch_pause(reason)
    end = time.monotonic() + 2
    while True:
        state = emergency_status(control, run)
        if state['pause_id'] != value['pause_id']:
            return {'ok': False, **state, 'error': 'newer pause retained'}
        if state['acknowledged']:
            return {'ok': True, **state}
        if state['broker_state']['state'] in ('absent', 'exited', 'reused') or time.monotonic() >= end:
            return {'ok': False, **state, 'error': 'persistent pause written; exact broker ACK unverified'}
        time.sleep(.025)


UNSET = object()


def install_expected_resume(control):
    """Compare the expected pause at the original function's actual read.

    An outer precheck is racy: a later pause could otherwise become the new
    pause the original function consumes. No input implementation is replaced.
    """
    original_execute = control.execute_request
    original_read = control.read_optional
    def execute(request):
        if request.get('kind') != 'resume':
            return original_execute(request)
        if 'expected_pause_id' not in request:
            raise ValueError('expected pause identity required; no handoff')
        expected = request['expected_pause_id']
        if expected is not None and not isinstance(expected, str):
            raise ValueError('invalid expected pause identity')
        forwarded = {k: v for k, v in request.items() if k != 'expected_pause_id'}
        first = True
        def read(name):
            nonlocal first
            value = original_read(name)
            if name == 'manual-pause.json' and first:
                first = False
                if (value.get('pause_id') if value else None) != expected:
                    raise RuntimeError('new pause before original resume read; no handoff')
            return value
        control.read_optional = read
        try:
            return original_execute(forwarded)
        finally:
            control.read_optional = original_read
    control.execute_request = execute


class SubmissionDeadlineExpired(TimeoutError):
    """A new request was refused before publication, not a missing reply."""

    request_published = False


class SubmissionQueueTimeout(TimeoutError):
    """The original submission mutex stayed busy; no request was published."""

    request_published = False


class _SubmissionLease:
    """Use the already held broker mutex only in this caller's active scope."""

    def __init__(self, control):
        self.control, self.active = control, True
        self.caller = (os.getpid(), threading.get_ident())
        self.queue_wait_diagnostic_failed = False

    def _check(self):
        if not self.active or self.caller != (os.getpid(), threading.get_ident()):
            raise RuntimeError('submission lease is not active for this caller; no request published')

    def __getattr__(self, name):
        self._check()
        return getattr(self.control, name)

    @property
    def submission_lease_active(self):
        self._check()
        return True

    @contextlib.contextmanager
    def submission_lock(self):
        self._check()
        yield


@contextlib.contextmanager
def submission_lease(control, *, timeout=2.0, submit_deadline=None, queue_wait=None):
    """Bounded acquisition of the SAME mutex for observe and input requests.

    Only a refused ``__enter__`` may wait. Once held, no body/publisher/reply
    exception causes a retry. The yielded control can also cover a bounded
    observe/read/publish transaction; nested Entry requests reuse that mutex.
    The lease never deletes another caller's lock or republishes a request.
    """
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 5
            or submit_deadline is not None and (type(submit_deadline) not in (int, float)
                or not math.isfinite(submit_deadline))
            or queue_wait is not None and not callable(queue_wait)):
        raise ValueError('submission acquisition requires a finite 0-5 second bound')
    start_ns, contended = time.monotonic_ns(), False
    leased, diagnostic_failed = None, False
    reused = getattr(control, 'submission_lease_active', False) is True

    def report(outcome):
        nonlocal diagnostic_failed
        if queue_wait is not None:
            try:
                queue_wait({'start_ns': start_ns, 'end_ns': time.monotonic_ns(), 'outcome': outcome,
                            'contended': contended, 'lease_reused': reused, 'request_published': False})
            except Exception as error:
                # Diagnostics cannot replace a refusal, cancel an acquired
                # request or make any published input look unissued. Retain a
                # boolean and reuse the optional recorder's type-only failure;
                # callback text may contain private paths or credentials.
                diagnostic_failed = True
                if leased is not None:
                    leased.queue_wait_diagnostic_failed = True
                try:
                    profile = getattr(getattr(queue_wait, '__self__', None), 'profile', None)
                    if profile is not None:
                        profile._disable(error)
                except Exception:
                    pass

    queue_deadline = time.monotonic() + timeout
    deadline = min(queue_deadline, submit_deadline) if submit_deadline is not None else queue_deadline
    try:
        while True:
            if submit_deadline is not None and time.monotonic() >= submit_deadline:
                raise SubmissionDeadlineExpired('submission deadline expired before acquisition; no request published')
            manager = control.submission_lock()
            try:
                manager.__enter__()
            except RuntimeError as error:
                if str(error) != 'another request is pending; no concurrent submission':
                    raise
                contended = True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if submit_deadline is not None and time.monotonic() >= submit_deadline:
                        raise SubmissionDeadlineExpired('submission deadline expired before acquisition; no request published') from error
                    raise SubmissionQueueTimeout('submission queue deadline expired; no request published; lock retained') from error
                time.sleep(min(.025, remaining))
            else:
                break
    except BaseException as error:
        report('deadline' if isinstance(error, SubmissionDeadlineExpired) else
               'busy' if isinstance(error, SubmissionQueueTimeout) else 'refused')
        if diagnostic_failed:
            error.queue_wait_diagnostic_failed = True
        raise
    if submit_deadline is not None and time.monotonic() >= submit_deadline:
        manager.__exit__(None, None, None)
        report('deadline')
        raise SubmissionDeadlineExpired('submission deadline expired during acquisition; no request published')
    leased = _SubmissionLease(control)
    try:
        report('acquired')
        yield leased
    except BaseException:
        if not manager.__exit__(*sys.exc_info()):
            raise
    else:
        manager.__exit__(None, None, None)
    finally:
        leased.active = False


def _guard_runner_owned_input(control, value):
    physical = value.get('handoff') is True or any(
        action['type'] in ('click', 'key', 'drag', 'scroll') for action in value.get('actions', []))
    if (value.get('kind') != 'resume' and physical
            and Path(control.ROOT, 'runner-owner.json').exists()
            and getattr(control, 'business_guarded_submission', False) is not True):
        raise ValueError('runner-owned input requires current business intent; use runner manual-step/decide; no request published')


def request(control, kind, tokens, rid, handoff, expected_pause_id=UNSET, *, submit_deadline=None, queue_wait=None):
    if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
        raise ValueError('bounded request ID required')
    state = control.status()
    if not state['ready']:
        raise RuntimeError('broker not ready; no request published')
    value = {'id': rid, 'kind': kind, 'chat_id': control.OWNER['chat_id'],
             'run_token': control.OWNER['run_token'], 'handoff': handoff}
    if kind == 'resume':
        if not handoff or tokens:
            raise ValueError('resume forbids all game actions and requires handoff')
        value['expected_pause_id'] = state.get('pause_id') if expected_pause_id is UNSET else expected_pause_id
    else:
        value['actions'] = control.validate_actions([
            {'type': p[0], 'args': p[1:]} for p in (t.split(':') for t in tokens)])
    ledger = Path(control.ROOT, 'request-ledger')
    receipt = ledger / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
    # An existing ID may only reconcile its original payload/outcome. Its
    # expired publication deadline must not prevent reading that old result.
    acquisition_deadline = None if receipt.exists() else submit_deadline
    if not receipt.exists():
        _guard_runner_owned_input(control, value)
    with submission_lease(control, submit_deadline=acquisition_deadline, queue_wait=queue_wait):
        if (submit_deadline is not None and time.monotonic() >= submit_deadline
                and not receipt.exists()):
            raise SubmissionDeadlineExpired('submission deadline expired before publication; no request published')
        if receipt.exists():
            previous = read_json(receipt)
            if previous.get('id') != rid or previous.get('request') != value:
                raise ValueError('request ID reused with different payload; no input')
            if previous.get('result') is not None:
                return previous['result']
            # A previous publication might already have executed. Waiting for
            # that exact ID is allowed; publication is never repeated.
        else:
            _guard_runner_owned_input(control, value)
            if submit_deadline is not None and time.monotonic() >= submit_deadline:
                raise SubmissionDeadlineExpired('submission deadline expired before publication; no request published')
            ledger.mkdir(exist_ok=True)
            control.write_json(receipt, {'id': rid, 'request': value, 'result': None})
            if submit_deadline is not None and time.monotonic() >= submit_deadline:
                # Only this new, still-unpublished receipt exists. Do not leave
                # an incomplete ledger entry that a later call could wait on.
                receipt.unlink()
                raise SubmissionDeadlineExpired('submission deadline expired before publication; no request published')
            control.publish_request(value)
        # The caller knows the ID before publishing. An unreadable reply never
        # triggers a resend, and no different result can be accepted.
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                result = read_json(Path(control.ROOT, 'result.json'))
                if result.get('id') == rid:
                    if kind == 'resume':
                        result['status'] = control.status()
                        result['resumed'] = bool(result.get('ok') and not result['status']['paused']
                                                 and not result['status']['input_halted'])
                    control.write_json(receipt, {'id': rid, 'request': value, 'result': result})
                    return result
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            except OSError as exc:
                if not (getattr(exc, 'winerror', None) in (5, 32)
                        or (getattr(exc, 'winerror', None) is None
                            and isinstance(exc, PermissionError)
                            and exc.errno in (errno.EACCES, errno.EPERM))):
                    raise
                if time.monotonic() >= deadline:
                    break
            if not Path(control.ROOT, 'broker-ready.json').exists():
                raise RuntimeError('broker exited; input outcome unknown, never resend')
            time.sleep(.05)
    raise TimeoutError('same request outcome unverified; never resend input')


def stop(control, run, chat, token):
    identity = owned_broker_identity(control, run)
    (run / 'broker-stop').touch()
    if identity is None:
        raise RuntimeError('stop flag written; broker identity missing, preserve runtime')
    deadline = time.monotonic() + 10
    while True:
        probe = exit_probe(control, identity['pid'], identity['creation_id'])
        if probe['state'] in ('absent', 'exited', 'reused'):
            return {'stopped': True, 'exit_evidence': probe, 'owned_run_removed': False}
        if probe['state'] == 'unknown' or time.monotonic() >= deadline:
            raise RuntimeError('exact broker exit unverified; preserve runtime')
        time.sleep(.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'status', 'pause', 'resume', 'submit', 'stop'])
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--run-token', required=True)
    parser.add_argument('--request-id', default=None)
    parser.add_argument('--handoff', action='store_true')
    parser.add_argument('--reason', default='manual takeover')
    parser.add_argument('actions', nargs='*')
    args = parser.parse_intermixed_args()
    c = backend()
    if args.command in ('pause', 'stop'):
        run, unused_owner = authenticate(args.run_dir, args.chat_id, args.run_token, c)
    else:
        run, unused_owner, unused_binding = load(args.run_dir, args.chat_id, args.run_token, c)
    if args.command == 'serve':
        install_expected_resume(c)
        try:
            c.serve()
        except Exception as exc:
            if c.BROKER_IDENTITY is None:
                c.write_json(run / 'broker-start-error.json',
                             {'error': str(exc), 'time': c.utc_now(), 'game_inputs': 0})
            raise
        return
    if args.command == 'status':
        result = c.status()
    elif args.command == 'pause':
        result = emergency_pause(c, run, args.reason)
    elif args.command == 'stop':
        result = stop(c, run, args.chat_id, args.run_token)
    else:
        result = request(c, 'resume' if args.command == 'resume' else 'actions', args.actions,
                         args.request_id or uuid.uuid4().hex, args.handoff)
    print(json.dumps(result, ensure_ascii=False))
    if result.get('ok') is False or result.get('resumed') is False:
        sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'outcome': 'unverified; no input resend'}, ensure_ascii=False))
        sys.exit(2)
