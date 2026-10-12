"""Stdio MCP adapter. Importing this module never loads the game input layer."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import sys
import uuid

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
SEMANTIC_FIELDS = ('options', 'option_read', 'team', 'inventory', 'rewards', 'gear',
                   'bonds', 'purchase_units', 'guide', 'guide_candidates', 'guide_tracking',
                   'unit_preview', 'player_hud', 'refresh_offer', 'coins', 'coins_read',
                   'investments', 'environment', 'bounds', 'settlement', 'shop_terms', 'experience_offer')
REQUEST_FIELDS = ('context', 'static_knowledge', 'knowledge_boundary', 'inspection_results',
                  'progression_plan', 'reward_capacity_pending', 'return_reason',
                  'battle_stage', 'coaching', 'comparison_view')


def read_object(path, limit=2_000_000):
    if path.stat().st_size > limit:
        raise ValueError('JSON exceeds size limit')
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('Expected a JSON object')
    return result


def propose_economic_step(state):
    """Authorize a local recomputation, never caller-selected spending or input."""
    result = {'schema': 'currency-wars-economic-step-proposal/v1', 'status': 'blocked',
              'input_sent': False, 'execution_eligible': False, 'actions': []}
    binding = state.get('binding') or {}
    checklist = state.get('preparation_checklist') or {}
    steps = checklist.get('steps') or []
    completed = {step.get('id') for step in steps
                 if isinstance(step, dict) and step.get('completed') is True}
    if (state.get('economy_macro') or {}).get('status') == 'active':
        result['reason'] = 'economic_macro_authority_active'
        return result
    if (state.get('status') != 'observed'
            or state.get('source') != 'authenticated_runner_status'
            or state.get('control_mode') != 'waiting_decision'
            or state.get('decision', {}).get('kind') not in ('preparation_strategy', 'shop_strategy')
            or checklist.get('phase') != 'economy'
            or not {'rewards', 'shop_inspection', 'inventory_cleanup'} <= completed
            or state.get('page') not in ('preparation', 'shop')
            or any(binding.get(key) is None or binding.get(key) == ''
                   for key in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id'))):
        result['reason'] = 'current_authenticated_economy_phase_and_completed_predecessors_required'
        return result
    action = {'type': 'advance_local_economy', 'expected_page': state['page'],
              'reason': '本地联合比较当前购牌、升级和有界刷新；最多一笔经济输入，原收据回读后重新决策。'}
    result.update(status='proposed', binding=dict(binding), actions=[action],
                  reply={**binding, 'actions': [action], 'context_update': {}},
                  scope='Original executor revalidates current evidence and derives its own budget; proposal alone sends no input.')
    return result


def propose_economic_macro(state, limits):
    """Optional explicit macro caps; the original executor alone owns the loop."""
    from currency_wars_economy import MACRO_CONTRACT, validate_macro_limits
    result = propose_economic_step(state)
    result['schema'] = 'currency-wars-economic-macro-proposal/v1'
    if result['status'] != 'proposed':
        return result
    if (state.get('decision', {}).get('economic_macro_contract') != MACRO_CONTRACT
            or 'advance_local_economy_macro' not in (state.get('decision', {}).get('allowed_action_types') or [])):
        return {**result, 'status': 'unsupported', 'actions': [], 'reply': None,
                'reason': 'current_executor_does_not_advertise_macro_support'}
    try:
        caps = validate_macro_limits(limits)
    except ValueError as exc:
        return {**result, 'status': 'blocked', 'actions': [], 'reply': None, 'reason': str(exc)}
    action = {'type': 'advance_local_economy_macro', 'expected_page': state['page'], 'limits': caps,
              'reason': '显式上限内逐笔本地联合重算，原收据与效果核验后继续；任何未决或控制变化立即结束。'}
    return {**result, 'actions': [action], 'reply': {**result['binding'], 'actions': [action], 'context_update': {}}}


class Adapter:
    def __init__(self, project=PROJECT, runner_python=sys.executable, offline_image=None,
                 enable_candidate_economy=False, scratch=None):
        self.project = Path(project).resolve()
        self.runner_python = str(runner_python)
        self.offline_image = Path(offline_image).resolve() if offline_image else None
        self.enable_candidate_economy = enable_candidate_economy
        self.scratch = Path(scratch).resolve() if scratch is not None else None
        # A binding is explicit and fixed for this server lifetime. Never adopt
        # CURRENT_RUNNER or discover another chat's owner token.
        self.chat = os.environ.get('CURRENCY_WARS_CHAT_ID')
        self.run = os.environ.get('CURRENCY_WARS_RUN_DIR')
        self.token = os.environ.get('CURRENCY_WARS_RUN_TOKEN')
        self.mutations = asyncio.Lock()
        self._catalogue = None

    def catalogue(self):
        if self._catalogue is None:
            from currency_wars_decision import Catalogue
            self._catalogue = Catalogue(self.project / 'data/currency_wars/catalog.sqlite3')
        return self._catalogue

    async def lookup_catalogue(self, name, kind, cost=None, star=1, detail=False):
        try:
            return self.redact(self.catalogue().query(name, kind, cost, star, detail))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
            return self.result('unavailable', input_sent=False, reason=str(exc))

    async def lookup_rules(self, name=None, kind=None):
        try:
            return self.redact(self.catalogue().rules(name, kind))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
            return self.result('unavailable', input_sent=False, execution_eligible=False, reason=str(exc))

    async def decision(self, policy=None):
        from currency_wars_decision import propose
        state = await self.state()
        result = propose(state, self.catalogue(), policy)
        result['state_status'] = state.get('status')
        if state.get('status') != 'observed':
            result['status'] = state.get('status', 'unavailable')
        # No automatic submit: proposals never bypass original request/epoch guards.
        return self.redact(result)

    async def advice(self, policy=None, macro_limits=None):
        """Conditional local suggestions; never an executable runner plan."""
        state = await self.state()
        if state.get('status') not in ('observed', 'offline'):
            return self.redact({**state, 'execution_eligible': False})
        try:
            from currency_wars_advisor import advise
            from currency_wars_deployment import propose_deployment
            result = advise(state, self.catalogue(), policy)
            result['state_status'] = state.get('status')
            result['deployment_proposal'] = propose_deployment(state, result)
            result['economic_step_proposal'] = propose_economic_step(state)
            if macro_limits is not None:
                result['economic_macro_proposal'] = propose_economic_macro(state, macro_limits)
            return self.redact(result)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error) as exc:
            return self.result('unavailable', input_sent=False,
                               execution_eligible=False, reason=str(exc))

    def redact(self, value):
        credential_keys = {'run_token', 'gui_token', 'owner_token'}
        secrets = {self.token} if self.token else set()

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

        def cleaned(item):
            if isinstance(item, dict):
                return {key: cleaned(content) for key, content in item.items() if key not in credential_keys}
            if isinstance(item, list):
                return [cleaned(content) for content in item]
            if isinstance(item, str):
                for secret in sorted(secrets, key=len, reverse=True):
                    item = item.replace(secret, '[redacted]')
            return item

        collect(value)
        return cleaned(value)

    def result(self, status, **details):
        return self.redact({'schema': SCHEMA, 'status': status, **details})

    def carry_cleanup(self, result, *sources):
        warnings = []
        for source in (result, *sources):
            warning = source.get('artifact_cleanup')
            if warning and warning not in warnings:
                warnings.append(warning)
        if warnings:
            result['artifact_cleanup'] = self.redact(warnings[0] if len(warnings) == 1 else
                {'status': 'retained', 'runs': warnings})
        return result

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

    async def run_cli(self, args, *, timeout, reply=None):
        # Loaded only for an actual invocation. This lifecycle helper has no
        # game input imports and never adopts the bound runner's directory.
        import currency_wars_artifacts as artifacts
        process, response, directory = None, None, None
        try:
            # A unique purpose also prevents the installed helper's optional
            # custom-root sweep from matching another invocation's directory.
            with artifacts.scratch_directory('currency-wars-mcp-' + uuid.uuid4().hex,
                                             root=self.scratch) as directory:
                if reply is not None:
                    path = directory / 'reply.json'
                    path.write_text(json.dumps(reply, ensure_ascii=False), encoding='utf8')
                    args = [*args, '--reply-file', str(path)]
                artifacts.protect_children(directory, [], root=self.scratch, complete=False)
                try:
                    process = await asyncio.create_subprocess_exec(*args, cwd=self.project,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    status, identity = artifacts.process_identity(process.pid)
                    if status == 'active' and identity:
                        artifacts.protect_children(directory,
                            [{'pid': process.pid, 'process_identity': identity}], root=self.scratch)
                    elif status != 'dead':
                        raise artifacts.ArtifactError('CLI child creation identity could not be verified')
                    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
                    response = {'stdout': stdout, 'returncode': process.returncode}
                except asyncio.TimeoutError:
                    response = {'timeout': True}
                finally:
                    if process is not None:
                        if process.returncode is None:
                            process.kill()
                        await process.communicate()
                        await process.wait()
                    artifacts.protect_children(directory, [], root=self.scratch, complete=True)
        except (artifacts.ArtifactError, OSError):
            if response is not None:
                response['artifact_cleanup'] = {'status': 'retained', 'path': str(directory),
                    'reason': 'Owned invocation cleanup could not be verified; no alternative cleanup attempted.'}
            else:
                return {'unavailable': True, 'process_started': process is not None,
                        'reason': 'Owned CLI invocation could not be prepared or verified.'}
        return response

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
        transport = await self.run_cli(args, timeout=20, reply=reply)
        if transport.get('timeout') or transport.get('unavailable'):
            return self.result('submission_unknown' if command != 'status' else 'unavailable',
                reason=transport.get('reason') or 'Runner CLI timed out; inspect status before any further decision. No automatic retry.',
                input_sent=None if not transport.get('unavailable') or transport.get('process_started') else False,
                artifact_cleanup=transport.get('artifact_cleanup'))
        stdout, exit_code = transport['stdout'], transport['returncode']
        try:
            if len(stdout) > 2_000_000:
                raise ValueError('Runner output exceeds limit')
            value = json.loads(stdout.decode('utf8'))
            if not isinstance(value, dict):
                raise ValueError('Invalid runner response')
        except (ValueError, UnicodeError):
            if exit_code != 0:
                return self.result('rejected', reason='Original runner CLI rejected the request.',
                    cli_exit_code=exit_code, input_sent=None if command != 'status' else False,
                    artifact_cleanup=transport.get('artifact_cleanup'))
            return self.result('submission_unknown' if command != 'status' else 'unavailable',
                               reason='Runner response unavailable; do not retry an input.', input_sent=None,
                               artifact_cleanup=transport.get('artifact_cleanup'))
        value = self.redact(value)
        if exit_code != 0:
            details = {key: value[key] for key in
                ('error', 'reason_kind', 'needs_user_confirmation', 'command_id', 'request_id') if key in value}
            return self.result('rejected', ok=False,
                reason=value.get('reason') or value.get('error') or 'Original runner CLI rejected the request.',
                cli_exit_code=exit_code, input_sent=value.get('input_sent', None if command != 'status' else False),
                artifact_cleanup=transport.get('artifact_cleanup'), **details)
        if transport.get('artifact_cleanup'):
            value['artifact_cleanup'] = transport['artifact_cleanup']
        return self.redact(value)

    async def state(self):
        if self.offline_image:
            # Perception also calls the existing ShopReader. A separate runtime
            # interpreter keeps OCR dependencies outside the MCP SDK environment.
            args = [self.runner_python, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                    '--perceive', str(self.offline_image), '--runner-project', str(self.project)]
            transport = await self.run_cli(args, timeout=30)
            try:
                stdout = transport.get('stdout', b'')
                if transport.get('timeout') or transport.get('unavailable') or transport.get('returncode') or len(stdout) > 2_000_000:
                    raise ValueError('Offline perception unavailable')
                observation = json.loads(stdout.decode('utf8'))
                if not isinstance(observation, dict):
                    raise ValueError('Invalid offline perception response')
            except (ValueError, UnicodeError):
                return self.result('unavailable', reason='Offline perception failed.',
                    reason_kind='timeout' if transport.get('timeout') else 'invalid_response',
                    cli_exit_code=transport.get('returncode'), input_sent=False,
                    artifact_cleanup=transport.get('artifact_cleanup'))
            result = self.project_state({'observation': observation}, offline=True)
            if transport.get('artifact_cleanup'):
                result['artifact_cleanup'] = self.redact(transport['artifact_cleanup'])
            return result
        envelope = await self.invoke('status')
        if not envelope.get('ok'):
            return envelope
        return self.carry_cleanup(self.project_state(envelope['state']), envelope)

    def project_state(self, state, offline=False):
        request = state.get('decision_request') or {}
        observed = request.get('observation') or state.get('observation') or {}
        macro = state.get('economy_macro') or {}
        if macro.get('status') == 'active':
            observed = state.get('observation') or {}
        semantic, fields = observed.get('semantic') or {}, observed.get('fields') or {}
        player = semantic.get('player_hud') or {}
        snapshot = observed.get('snapshot_id')
        player_bound = bool(snapshot and player.get('actor') == 'player'
            and player.get('origin') == 'native_player_hud' and player.get('snapshot_id') == snapshot)
        # Main's old global Lv regex can read a shop character/boss as player.
        # It is not a player fact. Only an actor-bound native HUD read qualifies.
        level = player.get('level') if (player_bound
            and player.get('evidence', {}).get('level')) else None
        xp = player.get('xp')
        if not (player_bound and player.get('evidence', {}).get('experience_progress')
                and isinstance(xp, list) and len(xp) == 2 and all(type(x) is int for x in xp)
                and 0 <= xp[0] < xp[1]):
            xp = None
        refresh = semantic.get('refresh_offer')
        refresh_bound = bool(snapshot and isinstance(refresh, dict)
            and refresh.get('schema') == 'currency-wars-refresh-offer/v1'
            and type(refresh.get('version')) is int and refresh['version'] == 1
            and refresh.get('snapshot_id') == snapshot and refresh.get('page') == observed.get('page'))
        refresh_known = bool(refresh_bound and refresh.get('status') == 'known'
            and observed.get('page') == 'shop' and refresh.get('evidence')
            and refresh.get('mode') in ('free', 'paid'))
        if refresh_known:
            active, other = ('free_remaining', 'paid_cost') if refresh['mode'] == 'free' else ('paid_cost', 'free_remaining')
            refresh_known = type(refresh.get(active)) is int and refresh[active] >= 0 and refresh.get(other) is None
        coin_read = semantic.get('coins')
        coin_value = coin_read.get('value') if isinstance(coin_read, dict) else coin_read
        coin_known = type(coin_value) is int and coin_value >= 0
        if isinstance(coin_read, dict):
            coin_known = bool(coin_known and snapshot and coin_read.get('snapshot_id') == snapshot)
        experience = semantic.get('experience_offer') or {}
        price_proof = experience.get('evidence', {}).get('cost') or {}
        xp_cost = experience.get('buy_cost')
        price_bound = (player_bound and experience.get('actor') == 'player'
            and experience.get('origin') == 'native_experience_offer'
            and experience.get('snapshot_id') == snapshot and experience.get('cost_checked') is True
            and type(xp_cost) is int and 0 <= xp_cost <= 10000
            and price_proof.get('source') == 'current_native_experience_price'
            and price_proof.get('snapshot_id') == snapshot and price_proof.get('value') == xp_cost)
        necessary = {'stage': fields.get('stage'), 'player_level': level,
                     'population': fields.get('deployed'), 'coins': coin_read if coin_known else None,
                     'xp': xp, 'refresh': {key: refresh.get(key) for key in
                        ('mode', 'free_remaining', 'paid_cost')} if refresh_known else None,
                     'xp_cost': xp_cost if price_bound else None, 'xp_gain': None}
        projected = {key: {'value': value, 'known': value is not None,
                          'source': ('native_player_hud' if key in ('player_level', 'xp')
                                     else 'native_experience_offer' if key == 'xp_cost'
                                     else 'native_refresh_offer' if key == 'refresh' else 'perception')
                                    if value is not None else 'unknown'} for key, value in necessary.items()}
        # Keep v1 coins.value's native-object/scalar shape for existing clients.
        # New consumers use normalized_value; all original reads remain below.
        projected['coins'].update(normalized_value=coin_value if coin_known else None,
                                 proof=coin_read if isinstance(coin_read, dict) else semantic.get('coins_read'))
        projected['xp']['proof'] = player.get('evidence', {}).get('experience_progress')
        projected['xp_cost']['proof'] = price_proof if price_bound else None
        projected['refresh'].update(proof={'semantic_key': 'refresh_offer', 'snapshot_id': snapshot},
            reason=None if refresh_known else
            'native_offer_unknown_or_not_bound_to_current_snapshot')
        public_semantic = {key: semantic[key] for key in SEMANTIC_FIELDS if key in semantic}
        public_request = {key: request[key] for key in REQUEST_FIELDS if key in request}
        public_state_read = {key: (observed.get('state_read') or {}).get(key)
            for key in ('schema', 'snapshot_id', 'origin', 'resource_version', 'team', 'inventory', 'tooltips', 'requests')}
        return self.result('offline' if offline else 'observed', input_sent=False,
            version={'adapter': SCHEMA, 'runner_sha256': self.source_digest(),
                     'economy_contract': PR16 if self.economy_supported() else 'unsupported'},
            source='offline_image_perception' if offline else 'authenticated_runner_status',
            freshness={'captured_at': None if offline else observed.get('captured_at'),
                       'capture_source': 'offline_image' if offline else 'runner_capture' if observed.get('captured_at') else 'unknown',
                       'legacy_observed_at': observed.get('observed_at'), 'heartbeat_at': state.get('heartbeat_at'),
                       'request_deadline': request.get('deadline_at'), 'live_capture_requested': False},
            binding={key: request.get(key) for key in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')},
            page=observed.get('page'), snapshot_id=observed.get('snapshot_id'),
            fields=projected,
            observation={**{key: observed.get(key) for key in
                         ('snapshot_id', 'capture_request_id', 'frame_id', 'page', 'read_contract', 'read_timing')},
                         'captured_at': None if offline else observed.get('captured_at')},
            native_fields=fields,
            semantic=public_semantic, state_read=public_state_read,
            comparison_view=request.get('comparison_view'),
            request=public_request,
            fact_scopes={'semantic': 'current_observation; retain native unknown/checked flags',
                         'request.context': 'runner_context; original match/epoch/proof/age still apply',
                         'request.inspection_results': 'runner_inspection_results; original provenance still applies',
                         'request.static_knowledge': 'historical_definitions_only; never current holdings or active bonds'},
            run_id=state.get('run_id'), match_id=state.get('match_id'), state_sequence=state.get('state_sequence'),
            shop=observed.get('shop'), options=semantic.get('options'),
            control_mode=state.get('control_mode'), broker=state.get('broker'),
            economy_macro=macro,
            preparation_checklist=request.get('preparation_checklist'),
            decision={'kind': request.get('kind'), 'reason': request.get('reason'),
                      'economic_macro_contract': request.get('economic_macro_contract'),
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
        return self.carry_cleanup(self.result('observed', input_sent=False, control_mode=state.get('control_mode'),
            phase=state.get('phase'), reason=state.get('reason'), heartbeat_at=state.get('heartbeat_at'),
            worker_state=state.get('worker_state'), broker=state.get('broker'),
            binding={key: request.get(key) for key in ('request_id', 'snapshot_id', 'resume_epoch', 'match_id')},
            statistics=state.get('statistics'), preparation_checklist=request.get('preparation_checklist'),
            actual_effects=effects, effects_source='native_economy_ledger' if effects else 'unknown',
            pending_decision={'kind': request.get('kind'), 'reason': request.get('reason')}), envelope)

    async def submit(self, reply):
        async with self.mutations:
            result = await self.invoke('decide', reply=reply)
            if result.get('ok'):
                return self.carry_cleanup(self.result('submitted', request_id=reply.get('request_id'),
                    effects_verified=False, reason='Accepted by decide; worker execution and effects remain pending.'), result)
            return result

    async def economy(self, goal, budget, max_refreshes, reviewed_plan, binding):
        if not self.economy_supported():
            return self.result('unsupported', input_sent=False,
                reason='The local runner has a native economic engine, but this adapter contract is not approved for '
                       'the current source set. Requires explicit PR16 candidate opt-in and matching source '
                       'fingerprints; no replacement click loop.',
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
                return self.carry_cleanup(self.result('needs_review', input_sent=False,
                    reason='Current request/epoch or preparation prerequisites do not permit economy.'), envelope)
            proof = {'source': 'observed_screen', 'snapshot_id': binding['snapshot_id'],
                     'evidence_file': request.get('evidence_file'), 'resume_epoch': binding['resume_epoch']}
            plan = {**reviewed_plan, 'reason': goal.strip()}
            reply = {**binding, 'context_update': {'economy_plan': {'proof': proof, 'value': plan}},
                     'actions': [{'type': 'finish_preparation_review', 'reason': goal.strip()}]}
            result = await self.invoke('decide', reply=reply)
            if result.get('ok'):
                return self.carry_cleanup(self.result('submitted', request_id=binding['request_id'], effects_verified=False,
                    reason='Native worker will validate the plan and run its existing bounded economic flow. '
                           'Use get_task_status for actual ledger effects or pending review; submission is not completion.'), envelope, result)
            return self.carry_cleanup(result, envelope)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner-project', type=Path, default=PROJECT)
    parser.add_argument('--runner-python', default=sys.executable)
    parser.add_argument('--offline-image', type=Path)
    parser.add_argument('--scratch', type=Path, help='Optional parent for new ownership-marked invocation runs; defaults to OS temp.')
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

    @server.tool(annotations=readonly)
    async def lookup_catalogue(name: str, kind: str, cost: int | None = None,
                               star: int | None = 1, detail: bool = False) -> dict:
        """Offline source/version-bound definitions. Default compact; no current ownership or pool claim."""
        return await adapter.lookup_catalogue(name, kind, cost, star, detail)

    @server.tool(annotations=readonly)
    async def lookup_rules(name: str | None = None, kind: str | None = None) -> dict:
        """Cached released rule components and unknowns. With no filters returns coverage.

        Never current ownership, activated bonds, game effects or execution permission.
        """
        return await adapter.lookup_rules(name, kind)

    @server.tool(annotations=readonly)
    async def get_decision(policy: dict | None = None) -> dict:
        """Locally rank catalogue-backed candidates from authenticated fresh state; zero model or game input.

        Missing facts return needs_review. Disabled XP/refresh never get guessed rules.
        Scores are uncalibrated published power proxies, not damage or win probabilities.
        Does not submit or execute; original binding and battle approval still apply.
        """
        return await adapter.decision(policy)

    @server.tool(annotations=readonly)
    async def get_advice(policy: dict | None = None, macro_limits: dict | None = None) -> dict:
        """Local conditional suggestions with explicit unknowns and per-action requirements.

        Published power is an approximate proxy. This tool sends no game input;
        every suggestion is non-executable and retains original state provenance.
        get_decision and original submission/ownership/budget/battle guards apply
        independently; this tool never fills their missing facts.
        """
        return await adapter.advice(policy, macro_limits)

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
