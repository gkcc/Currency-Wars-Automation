"""Stdio MCP adapter. Importing this module never loads the game input layer."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parent.parent
SCHEMA = 'currency-wars-mcp/v1'
# Candidate support is deliberately version bound; this does not install or
# approve PR16. Its existing runner readiness and broker checks still apply.
PR16 = '5938648de4cfd4b5cd3cd723898abb3c2f806a48'
ECONOMY_SOURCES = {
    'currency_wars_runner.py': '5ea9659bc43c0fc1af1ce08968a135fdc03acffb9158d0686d4cbba126bf9e53',
    'currency_wars_economy.py': '7a2c704510d9a064f893a4164921de7a0a7b1bdeef0594a9e783792e093ec9c7',
    'currency_wars_broker_entry.py': '2aa1cf57f3dccc8512009c0a8de422b642d8211bb4fb7fcbba1df5089400ba81',
    'currency_wars_control.py': '187f826feb6e29beae8175ce9854845d19354f8344723c84663f6fe79ec0dca5',
    'currency_wars_perception.py': '5f6ea6f6e874aa4719cf4dc15c35bac4ec15ddb050a638e0c505254e1c04293b',
    'currency_wars_coaching.py': '9ee40dae3b476504f6bfb61dc29eeefb97e55fefac9eee5e7000cac701640895',
    'currency_wars_refresh_offer.py': '1cd55c43821ba1f99fb0b07f4ae945f4a5146d83de5a538645d47a25454faa59',
}


def read_object(path, limit=2_000_000):
    if path.stat().st_size > limit:
        raise ValueError('JSON exceeds size limit')
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object')
    return result


class Adapter:
    def __init__(self, project=PROJECT, runner_python=sys.executable, offline_image=None,
                 enable_candidate_economy=False, scratch=Path('D:/Codex/Temp/currency-wars-mcp')):
        self.project = Path(project).resolve()
        self.runner_python = str(runner_python)
        self.offline_image = Path(offline_image).resolve() if offline_image else None
        self.enable_candidate_economy = enable_candidate_economy
        self.scratch = Path(scratch).resolve()
        # A binding is explicit and fixed for this server lifetime. Never adopt
        # CURRENT_RUNNER or discover another chat's owner token.
        self.chat = os.environ.get('CURRENCY_WARS_CHAT_ID')
        self.run = os.environ.get('CURRENCY_WARS_RUN_DIR')
        self.token = os.environ.get('CURRENCY_WARS_RUN_TOKEN')
        self.mutations = asyncio.Lock()

    def redact(self, value):
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()
                    if k not in ('run_token', 'gui_token', 'owner_token')}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, str) and self.token:
            return value.replace(self.token, '[redacted]')
        return value

    def result(self, status, **details):
        return self.redact({'schema': SCHEMA, 'status': status, **details})

    def economy_supported(self):
        if not self.enable_candidate_economy:
            return False
        for name, digest in ECONOMY_SOURCES.items():
            try:
                if hashlib.sha256((self.project / 'tools' / name).read_bytes()).hexdigest() != digest:
                    return False
            except OSError:
                return False
        return True

    async def invoke(self, command, reply=None, reason=None):
        if self.offline_image or not all((self.chat, self.run, self.token)):
            return self.result('offline' if self.offline_image else 'no_session', input_sent=False,
                               reason='No explicitly bound live runner; no game operation was dispatched.')
        args = [self.runner_python, '-B', '-X', 'utf8',
                str(self.project / 'tools/currency_wars_runner.py'), command,
                '--chat-id', self.chat, '--run-dir', self.run, '--run-token', self.token]
        if reason is not None:
            args += ['--reason', reason]
        if reply is not None and len(json.dumps(reply, ensure_ascii=False).encode('utf8')) > 100_000:
            return self.result('rejected', reason='Plan exceeds original runner size limit.', input_sent=False)
        self.scratch.mkdir(parents=True, exist_ok=True)
        # The subprocess and its temporary reply belong only to this invocation.
        with tempfile.TemporaryDirectory(prefix='request-', dir=self.scratch) as directory:
            if reply is not None:
                path = Path(directory) / 'reply.json'
                path.write_text(json.dumps(reply, ensure_ascii=False), encoding='utf8')
                args += ['--reply-file', str(path)]
            process = await asyncio.create_subprocess_exec(*args, cwd=self.project,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return self.result('submission_unknown' if command != 'status' else 'unavailable',
                    reason='Runner CLI timed out; inspect status before any further decision. No automatic retry.',
                    input_sent=None)
        if process.returncode != 0:
            # Do not return tracebacks/command arguments containing credentials.
            return self.result('rejected', reason='Original runner CLI rejected the request.',
                               cli_exit_code=process.returncode, input_sent=None if command != 'status' else False)
        try:
            if len(stdout) > 2_000_000:
                raise ValueError('Runner output exceeds limit')
            value = json.loads(stdout.decode('utf8'))
            if not isinstance(value, dict):
                raise ValueError('Invalid runner response')
        except (ValueError, UnicodeError):
            return self.result('submission_unknown' if command != 'status' else 'unavailable',
                               reason='Runner response unavailable; do not retry an input.', input_sent=None)
        return self.redact(value)

    async def state(self):
        if self.offline_image:
            # Perception also calls the existing ShopReader. A separate runtime
            # interpreter keeps OCR dependencies outside the MCP SDK environment.
            args = [self.runner_python, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                    '--perceive', str(self.offline_image), '--runner-project', str(self.project)]
            process = await asyncio.create_subprocess_exec(*args, cwd=self.project,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
                if process.returncode or len(stdout) > 2_000_000:
                    raise ValueError('Offline perception unavailable')
                observation = json.loads(stdout.decode('utf8'))
            except (ValueError, asyncio.TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return self.result('unavailable', reason='Offline perception failed.',
                    reason_kind='timeout' if isinstance(exc, asyncio.TimeoutError) else 'invalid_response',
                    cli_exit_code=process.returncode, input_sent=False)
            return self.project_state({'observation': observation}, offline=True)
        envelope = await self.invoke('status')
        if not envelope.get('ok'):
            return envelope
        return self.project_state(envelope['state'])

    def project_state(self, state, offline=False):
        request = state.get('decision_request') or {}
        observed = request.get('observation') or state.get('observation') or {}
        semantic, fields = observed.get('semantic') or {}, observed.get('fields') or {}
        player = semantic.get('player_hud') or {}
        # Main's old global Lv regex can read a shop character/boss as player.
        # It is not a player fact. Only an actor-bound native HUD read qualifies.
        level = player.get('level') if (player.get('actor') == 'player'
            and player.get('origin') == 'native_player_hud'
            and player.get('snapshot_id') == observed.get('snapshot_id')
            and player.get('evidence', {}).get('level')) else None
        necessary = {'stage': fields.get('stage'), 'player_level': level,
                     'population': fields.get('deployed'), 'coins': semantic.get('coins'),
                     'xp': semantic.get('xp'), 'refresh': semantic.get('reroll')}
        return self.result('offline' if offline else 'observed', input_sent=False,
            version={'adapter': SCHEMA, 'runner_sha256': self.source_digest(),
                     'economy_contract': PR16 if self.economy_supported() else 'unsupported'},
            source='offline_image_perception' if offline else 'authenticated_runner_status',
            freshness={'captured_at': observed.get('observed_at'), 'heartbeat_at': state.get('heartbeat_at'),
                       'request_deadline': request.get('deadline_at'), 'live_capture_requested': False},
            binding={key: request.get(key) for key in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')},
            page=observed.get('page'), snapshot_id=observed.get('snapshot_id'),
            fields={key: {'value': value, 'known': value is not None,
                          'source': ('native_player_hud' if key == 'player_level' else 'perception')
                                    if value is not None else 'unknown'} for key, value in necessary.items()},
            shop=observed.get('shop'), options=semantic.get('options'),
            control_mode=state.get('control_mode'), broker=state.get('broker'),
            preparation_checklist=request.get('preparation_checklist'),
            decision={'kind': request.get('kind'), 'reason': request.get('reason'),
                      'choices': request.get('choices'), 'allowed_action_types': request.get('allowed_action_types'),
                      'evidence_file': request.get('evidence_file'), 'original_png': request.get('original_png'),
                      'preparation_decision': request.get('preparation_decision')})

    def source_digest(self):
        try:
            return hashlib.sha256((self.project / 'tools/currency_wars_runner.py').read_bytes()).hexdigest()
        except OSError:
            return None

    async def task_status(self):
        envelope = await self.invoke('status')
        if not envelope.get('ok'):
            return envelope
        state = envelope['state']
        request = state.get('decision_request') or {}
        effects = None
        stage = state.get('preparation_stage')
        match_id = state.get('match_id')
        if self.economy_supported() and isinstance(stage, str) and isinstance(match_id, str):
            digest = hashlib.sha256((match_id + ':' + stage).encode()).hexdigest()[:24]
            records = Path(state.get('journal_file', '')).resolve().parent
            # A display path cannot turn this tool into an arbitrary file reader.
            if records.is_relative_to(self.project / 'debug'):
                try:
                    value = read_object(records / ('economy-' + digest + '.json'))
                    if (value.get('schema') == 'currency-wars-economy/v1'
                            and all(value.get(key) == state.get(key) for key in ('run_id', 'match_id'))
                            and value.get('stage') == stage):
                        ledger = value['ledger']
                        effects = {key: ledger.get(key) for key in
                                   ('spent', 'paid_refreshes', 'purchased', 'pending', 'revision')}
                except (OSError, ValueError, KeyError, TypeError):
                    pass
        return self.result('observed', input_sent=False, control_mode=state.get('control_mode'),
            phase=state.get('phase'), reason=state.get('reason'), heartbeat_at=state.get('heartbeat_at'),
            worker_state=state.get('worker_state'), broker=state.get('broker'),
            binding={key: request.get(key) for key in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')},
            statistics=state.get('statistics'), preparation_checklist=request.get('preparation_checklist'),
            actual_effects=effects, effects_source='native_economy_ledger' if effects else 'unknown',
            pending_decision={'kind': request.get('kind'), 'reason': request.get('reason')})

    async def submit(self, reply):
        async with self.mutations:
            result = await self.invoke('decide', reply=reply)
            if result.get('ok'):
                return self.result('submitted', request_id=reply.get('request_id'),
                    effects_verified=False, reason='Accepted by decide; worker execution and effects remain pending.')
            return result

    async def economy(self, goal, budget, max_refreshes, reviewed_plan, binding):
        if not self.economy_supported():
            return self.result('unsupported', input_sent=False,
                reason='Main lacks a public native economic transaction contract. PR16 was inspected but not merged. '
                       'Requires explicit candidate opt-in and matching source fingerprints; no replacement click loop.',
                candidate=PR16)
        if (not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 1000
                or type(max_refreshes) is not int or not 0 <= max_refreshes <= 100
                or set(budget) != {'purchase', 'refresh', 'experience'}
                or any(type(x) is not int or not 0 <= x <= 10000 for x in budget.values())
                or reviewed_plan.get('budget') != budget
                or reviewed_plan.get('paid_search', {}).get('max_refreshes') != max_refreshes):
            return self.result('rejected', input_sent=False, reason='Explicit budget/refresh bounds must match reviewed plan.')
        async with self.mutations:
            envelope = await self.invoke('status')
            if not envelope.get('ok'):
                return envelope
            state = envelope['state']
            request = state.get('decision_request') or {}
            if (any(binding.get(k) != request.get(k) or not binding.get(k)
                    for k in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id'))
                    or request.get('kind') not in ('preparation_strategy', 'shop_strategy', 'economy_result')
                    or state.get('control_mode') != 'waiting_decision'
                    or request.get('preparation_checklist', {}).get('economy_allowed') is not True):
                return self.result('needs_review', input_sent=False,
                                   reason='Current request/epoch or preparation prerequisites do not permit economy.')
            proof = {'source': 'observed_screen', 'snapshot_id': binding['snapshot_id'],
                     'evidence_file': request.get('evidence_file'), 'resume_epoch': binding['resume_epoch']}
            plan = {**reviewed_plan, 'reason': goal.strip()}
            reply = {**binding, 'context_update': {'economy_plan': {'proof': proof, 'value': plan}},
                     'actions': [{'type': 'finish_preparation_review', 'reason': goal.strip()}]}
            result = await self.invoke('decide', reply=reply)
            if result.get('ok'):
                return self.result('submitted', request_id=binding['request_id'], effects_verified=False,
                    reason='Native worker will validate the plan and run its existing bounded economic flow. '
                           'Use get_task_status for actual ledger effects or pending review; submission is not completion.')
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner-project', type=Path, default=PROJECT)
    parser.add_argument('--runner-python', default=sys.executable)
    parser.add_argument('--offline-image', type=Path)
    parser.add_argument('--scratch', type=Path, default=Path('D:/Codex/Temp/currency-wars-mcp'))
    parser.add_argument('--enable-candidate-economy', action='store_true')
    parser.add_argument('--perceive', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.perceive:
        sys.path.insert(0, str(args.runner_project.resolve() / 'tools'))
        from currency_wars_perception import Perception
        print(json.dumps(Perception().read(args.perceive), ensure_ascii=False))
        return
    # SDK import is optional and confined to the MCP entrypoint.
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    adapter = Adapter(args.runner_project, args.runner_python, args.offline_image,
                      args.enable_candidate_economy, args.scratch)
    server = FastMCP('currency-wars', instructions='Read current evidence; submit only explicitly bound plans. '
                     'No start/resume/input tools. Submitted plans are not confirmed effects.')
    readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    mutation = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @server.tool(annotations=readonly)
    async def get_state() -> dict:
        """Current necessary fields and evidence provenance. Unknown remains unknown; no new live capture."""
        return await adapter.state()

    @server.tool(annotations=readonly)
    async def get_task_status() -> dict:
        """Authenticated runner progress and native economic ledger effects, or explicit unavailable state."""
        return await adapter.task_status()

    @server.tool(annotations=mutation)
    async def submit_plan(reply: dict) -> dict:
        """Submit an unchanged current request-bound semantic reply through original runner decide guards."""
        return await adapter.submit(reply)

    @server.tool(annotations=mutation)
    async def pause(reason: str) -> dict:
        """Latch existing runner emergency pause. Does not resume, launch, or send game input."""
        if not 1 <= len(reason.strip()) <= 200:
            return adapter.result('rejected', input_sent=False, reason='Pause reason must contain 1-200 characters.')
        # Pause remains independent of a waiting decision submission.
        return await adapter.invoke('pause', reason=reason.strip())

    @server.tool(annotations=mutation)
    async def run_economy_plan(goal: str, budget: dict, max_refreshes: int,
                               reviewed_plan: dict, binding: dict) -> dict:
        """Delegate a reviewed bounded plan to the existing native economic flow. Main returns unsupported.

        reviewed_plan uses currency-wars-economy/v1 including real fields/ROI,
        targets, reserve, experience, paid_search and revision. Never invent facts.
        binding includes request_id/snapshot_id/resume_epoch/match_id. A submit
        acknowledgement is not completion; inspect get_task_status for effects.
        """
        return await adapter.economy(goal, budget, max_refreshes, reviewed_plan, binding)

    server.run(transport='stdio')


if __name__ == '__main__':
    main()
