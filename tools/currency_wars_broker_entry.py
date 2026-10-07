"""Standard-artifact adapter for the frozen, single-window safety broker.

Only directory provenance and bounded request transport live here. All game
input, pause, foreground and process guards remain in the pinned broker.
"""
import argparse
from datetime import datetime
import errno
import hashlib
import importlib.util
import io
import json
import re
import secrets
import stat
import sys
import time
import uuid
from pathlib import Path

import currency_wars_artifacts as artifacts

SOURCE = Path(__file__).with_name('currency_wars_control.py')
PINNED = '187F826FEB6E29BEAE8175CE9854845D19354F8344723C84663F6FE79EC0DCA5'


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


def request(control, kind, tokens, rid, handoff, expected_pause_id=UNSET, *, submit_deadline=None):
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
    with control.submission_lock():
        ledger = Path(control.ROOT, 'request-ledger')
        receipt = ledger / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
        if (submit_deadline is not None and time.monotonic() >= submit_deadline
                and not receipt.exists()):
            raise SubmissionDeadlineExpired('submission deadline expired before publication; no request published')
        ledger.mkdir(exist_ok=True)
        if receipt.exists():
            previous = read_json(receipt)
            if previous.get('id') != rid or previous.get('request') != value:
                raise ValueError('request ID reused with different payload; no input')
            if previous.get('result') is not None:
                return previous['result']
            # A previous publication might already have executed. Waiting for
            # that exact ID is allowed; publication is never repeated.
        else:
            if submit_deadline is not None and time.monotonic() >= submit_deadline:
                raise SubmissionDeadlineExpired('submission deadline expired before publication; no request published')
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
