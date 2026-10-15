"""Explicit, read-only MCP session client. Never starts/resumes/submits game input."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import stat
import sys
import time
import uuid
from unittest.mock import patch

import currency_wars_artifacts as artifacts

ROOT = Path(__file__).resolve().parents[1]
READ_TOOLS = ('get_state', 'get_task_status', 'get_decision', 'get_advice', 'lookup_catalogue', 'lookup_rules')
BINDING_KEYS = ('CURRENCY_WARS_CHAT_ID', 'CURRENCY_WARS_RUN_DIR', 'CURRENCY_WARS_RUN_TOKEN')


@contextlib.contextmanager
def sdk_diagnostics(log):
    """Keep SDK validation exceptions in the owned, disposable log.

    Malformed server stdout may embed credentials in exception input values.
    SDK loggers must not propagate those values to the client's stderr.
    """
    parent = logging.getLogger('mcp')
    loggers = [parent] + [value for name, value in logging.Logger.manager.loggerDict.items()
                          if name.startswith('mcp.') and isinstance(value, logging.Logger)]
    saved = [(logger, logger.handlers[:], logger.propagate) for logger in loggers]
    handler = logging.StreamHandler(log)
    try:
        for logger in loggers:
            logger.handlers = [handler] if logger is parent else []
            logger.propagate = logger is not parent
        yield
    finally:
        for logger, handlers, propagate in saved:
            logger.handlers = handlers
            logger.propagate = propagate
        handler.close()


def session_environment(run_dir=None, chat_id=None):
    """Only the explicitly named, live owner supplies child credentials.

    This is an early local check. The original runner status path independently
    authenticates its broker, source binding and owner on every MCP read.
    """
    env = {k: v for k, v in os.environ.items() if k not in BINDING_KEYS}
    if run_dir is None:
        if chat_id is not None:
            raise ValueError('chat_without_explicit_run')
        return env
    if not isinstance(chat_id, str) or not chat_id.strip() or len(chat_id) > 128:
        raise ValueError('explicit_chat_required')
    run = Path(run_dir).absolute()
    marker = artifacts.read_marker(run, root=run.parent)
    path = run / 'runner-owner.json'
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 16384
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('invalid_owner_file')
    owner = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(owner, dict) or owner.get('owner') != 'currency-wars-runner'
            or owner.get('chat_id') != chat_id or owner.get('run_id') != marker['run_id']
            or owner.get('runner_pid') != marker['pid']
            or not isinstance(owner.get('runner_creation_id'), str)
            or 'windows:' + owner['runner_creation_id'] != marker['process_identity']
            or owner.get('artifact_chat_id') != marker.get('session_hint', {}).get('id')):
        raise ValueError('explicit_session_owner_mismatch')
    if artifacts.process_identity(marker['pid']) != ('active', marker['process_identity']):
        raise ValueError('explicit_session_not_live')
    token = owner.get('run_token')
    if not isinstance(token, str) or len(token) != 48 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('invalid_session_credential')
    env.update(zip(BINDING_KEYS, (chat_id, str(run), token)))
    return env


def redacted(value, token=None):
    credential_keys = {'run_token', 'gui_token', 'owner_token'}
    secrets = {token} if token else set()
    def collect(item):
        if isinstance(item, dict):
            for key, content in item.items():
                if key in credential_keys and isinstance(content, str) and content:
                    secrets.add(content)
                else:
                    collect(content)
        elif isinstance(item, list):
            for content in item:
                collect(content)
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(content) for key, content in item.items() if key not in credential_keys}
        if isinstance(item, list):
            return [clean(content) for content in item]
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, '[redacted]')
        return item
    collect(value)
    return clean(value)


def compact(value):
    """Readable observation summary; original full replies are opt-in.

    Summaries cannot be submitted as execution plans. Keep blockers, bindings,
    freshness and native known/proof fields, avoiding OCR/static-data repetition.
    """
    if not isinstance(value, dict):
        raise ValueError('non_object_mcp_reply')
    keys = ('schema', 'status', 'state_status', 'source', 'binding', 'page', 'stage',
            'snapshot_id', 'match_id', 'freshness', 'fields', 'unknown', 'reason',
            'input_sent', 'effects_verified', 'auto_dispatch_allowed', 'runtime_ms',
            'model_api_calls', 'database_sha256', 'metadata', 'records', 'candidates',
            'progress', 'effects', 'artifact_cleanup', 'coverage', 'rules', 'scope',
            'execution_eligible', 'mechanics', 'joint_economy', 'economic_allocation',
            'economic_step_proposal', 'deployment_proposal', 'limitations', 'timing_ms')
    summary = {k: value[k] for k in keys if k in value}
    semantic = value.get('semantic') or {}
    team = semantic.get('team') or {}
    if team:
        summary['team'] = {k: team[k] for k in ('checked', 'fully_read', 'capacity', 'unknown_slots') if k in team}
        summary['team']['units'] = [{k: u.get(k) for k in ('row', 'slot', 'name', 'star', 'position')}
                                    for u in team.get('units', [])]
    shop = value.get('shop') or {}
    if shop:
        summary['shop'] = [{k: s.get(k) for k in ('slot', 'name', 'cost', 'status', 'purchase_eligible')}
                           for s in shop.get('slots', [])]
    summary['execution_capability'] = 'none; original request-bound submission is separate'
    return summary


async def read_session(args):
    # The optional SDK lives in .mcp-venv; importing this file never imports input.
    from mcp import ClientSession, StdioServerParameters
    import mcp.client.stdio as transport
    env = session_environment(args.run_dir, args.chat_id)
    token = env.get('CURRENCY_WARS_RUN_TOKEN')
    tool_args = {}
    if args.tool == 'lookup_catalogue':
        if not args.name or not args.kind:
            raise ValueError('catalogue_name_and_kind_required')
        tool_args = {'name': args.name, 'kind': args.kind, 'star': args.star, 'detail': args.detail}
    elif args.tool == 'lookup_rules':
        tool_args = {key: value for key, value in {'name': args.name, 'kind': args.kind}.items()
                     if value is not None}
    elif args.tool in ('get_decision', 'get_advice') and args.policy:
        if args.policy.stat().st_size > 65536:
            raise ValueError('policy_size_limit')
        tool_args = {'policy': json.loads(args.policy.read_text(encoding='utf-8-sig'))}
    processes = []
    creator = transport._create_platform_compatible_process
    purpose = 'cw-session-client-' + uuid.uuid4().hex
    scratch_root = artifacts.default_root()
    with artifacts.scratch_directory(purpose, root=scratch_root) as run:
        async def create_owned(**kwargs):
            artifacts.protect_children(run, [], root=scratch_root, complete=False)
            process = await creator(**kwargs)
            processes.append(process)
            state, identity = artifacts.process_identity(process.pid)
            if state != 'active' or not identity:
                # Stop/await the actual handle before allowing context cleanup.
                process.kill()
                await process.wait()
                artifacts.protect_children(run, [], root=scratch_root, complete=True)
                raise ValueError('sdk_child_identity_unknown')
            artifacts.protect_children(run, [{'pid': process.pid, 'process_identity': identity}],
                                       root=scratch_root, complete=False)
            return process
        params = StdioServerParameters(command=args.server_python,
            args=['-B', '-X', 'utf8', str(ROOT / 'tools/currency_wars_mcp.py'),
                  '--runner-project', str(ROOT), '--runner-python', args.runner_python],
            cwd=str(ROOT), env=env)
        with (run / 'sdk-stderr.log').open('w', encoding='utf-8') as log:
            try:
                with sdk_diagnostics(log), patch.object(transport, '_create_platform_compatible_process', create_owned):
                    async with transport.stdio_client(params, errlog=log) as (reader, writer):
                        async with ClientSession(reader, writer) as session:
                            async with asyncio.timeout(30):
                                await session.initialize()
                                available = {t.name for t in (await session.list_tools()).tools}
                            if args.tool not in READ_TOOLS or args.tool not in available:
                                raise ValueError('read_tool_unavailable')
                            for index in range(args.samples):
                                started = time.perf_counter()
                                async with asyncio.timeout(30):
                                    reply = await session.call_tool(args.tool, tool_args)
                                if reply.isError:
                                    raise ValueError('mcp_tool_error')
                                result = reply.structuredContent
                                if result is None:
                                    blocks = [c.text for c in reply.content if getattr(c, 'type', None) == 'text']
                                    if len(blocks) != 1:
                                        raise ValueError('ambiguous_mcp_response')
                                    result = json.loads(blocks[0])
                                result = redacted(result, token)
                                print(json.dumps({'schema': 'currency-wars-session-read/v1',
                                    'sample': index + 1, 'tool': args.tool,
                                    'round_trip_ms': round((time.perf_counter() - started) * 1000, 3),
                                    'result': result if args.full else compact(result)},
                                    ensure_ascii=False, allow_nan=False), flush=True)
                                if index + 1 < args.samples:
                                    await asyncio.sleep(args.interval)
            finally:
                # SDK transport closes stdin and awaits its exact child. On an
                # interrupted context, kill/await only the handles created here.
                for process in processes:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                artifacts.protect_children(run, [], root=scratch_root, complete=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tool', choices=READ_TOOLS, default='get_state')
    p.add_argument('--run-dir', type=Path)
    p.add_argument('--chat-id')
    p.add_argument('--server-python', default=str(ROOT / '.mcp-venv/Scripts/python.exe'))
    p.add_argument('--runner-python', default=str(ROOT / '.venv/Scripts/python.exe'))
    p.add_argument('--name')
    p.add_argument('--kind')
    p.add_argument('--star', type=int, choices=(1, 2, 3, 4), default=1)
    p.add_argument('--detail', action='store_true')
    p.add_argument('--policy', type=Path)
    p.add_argument('--full', action='store_true')
    p.add_argument('--samples', type=int, default=1)
    p.add_argument('--interval', type=float, default=1.)
    args = p.parse_args()
    if not 1 <= args.samples <= 1000 or not .25 <= args.interval <= 60 or args.samples * args.interval > 3600:
        p.error('samples/interval must be bounded to at most one hour')
    try:
        asyncio.run(read_session(args))
    except Exception as exc:
        # Exception text and server stderr can contain environment credentials.
        print(json.dumps({'status': 'unavailable', 'error_type': type(exc).__name__,
                          'input_sent': False, 'execution_capability': 'none'}), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
