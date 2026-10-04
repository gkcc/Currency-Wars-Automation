"""Standard-artifact adapter for the frozen, single-window safety broker.

Only directory provenance and bounded request transport live here. All game
input, pause, foreground and process guards remain in the pinned broker.
"""
import argparse
import hashlib
import importlib.util
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

import currency_wars_artifacts as artifacts

SOURCE = Path(__file__).with_name('currency_wars_control.py')
PINNED = '930198830AA224B9E6AA38058E2783F430CC1B8D081DF8F6120C1B5AF816EB9C'


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


def backend():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest().upper() != PINNED:
        raise ValueError('safety broker source changed; no operation permitted')
    spec = importlib.util.spec_from_file_location('currency_wars_safety', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def authenticate(run, chat, token, control):
    """Emergency authentication never parses binding or display state."""
    run = Path(run).absolute()
    marker = artifacts.read_marker(run)
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


def request(control, kind, tokens, rid, handoff, expected_pause_id=UNSET):
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
        ledger.mkdir(exist_ok=True)
        receipt = ledger / (hashlib.sha256(rid.encode()).hexdigest() + '.json')
        if receipt.exists():
            previous = read_json(receipt)
            if previous.get('id') != rid or previous.get('request') != value:
                raise ValueError('request ID reused with different payload; no input')
            if previous.get('result') is not None:
                return previous['result']
            # A previous publication might already have executed. Waiting for
            # that exact ID is allowed; publication is never repeated.
        else:
            control.write_json(receipt, {'id': rid, 'request': value, 'result': None})
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
                if getattr(exc, 'winerror', None) not in (5, 32):
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
        c.serve()
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
