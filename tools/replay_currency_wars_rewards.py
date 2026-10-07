"""Offline reward contracts and separate, unchanged historical observations.

Only the existing inert compatibility fixture supplies transport receipts.
Generated single-click successors are explicitly synthetic; the real retained
two-click reward group is never split or attributed to a new Worker request.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import functools
import hashlib
import io
import json
import os
from pathlib import Path
import time
import uuid
from types import SimpleNamespace

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

from PIL import Image, ImageDraw

import currency_wars_runner as runner
import currency_wars_rewards as rewards
from currency_wars_perception import Perception, fingerprint
import replay_currency_wars_preparation as preparation
import test_local_runtime_compatibility as compatibility


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'handoff/2026-10-07/reward-sequence'


def _ready(worker):
    # Reuse the existing process-only initializer without configure(), which
    # intentionally refuses a combined test process that initialized ORT.
    preparation.runner = runner
    preparation.ready_fixture(worker, ROOT)
    worker.state['control_mode'] = 'auto'
    worker.profile_wait_id, worker.profile_wait_kind = None, None


def _source(name='q01.png'):
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf8'))
    path = FIXTURES / name
    case = next(case for case in manifest['frames'] if case['file'] == name)
    if hashlib.sha256(path.read_bytes()).hexdigest() != case['export_image_sha256']:
        raise ValueError('historical source hash mismatch')
    with Image.open(path) as opened:
        return opened.convert('RGB')


@functools.lru_cache(maxsize=8)
def _protocol_frame(points, *, coins=65, selection=False, shop=False):
    """Generated pixels and declared fixture HUD; never production OCR output."""
    image = _source('q00.png' if shop else 'q01.png')
    if not shop:
        background = image.crop((1330, 380, 1404, 449))
        for point in ((1550, 265), (1497, 322)):
            image.paste(background, point)
        template = _source().crop(rewards.TEMPLATE_SOURCE_ROI)
        for point in points:
            image.paste(template, point)
    draw = ImageDraw.Draw(image)
    draw.rectangle((1638, 900, 1687, 942), fill='black')
    draw.text((1640, 902), str(coins), fill='white')
    draw.rectangle((1350, 1035, 1770, 1075), fill='black')
    draw.text((1360, 1040), 'OFFLINE SYNTHETIC RECEIPT CONTRACT', fill='white')
    if selection:
        draw.rectangle((350, 180, 1570, 900), fill=(70, 70, 70))
        draw.text((870, 200), 'SYNTHETIC SUPPLY SELECTION', fill='white')
    payload = io.BytesIO()
    image.save(payload, format='PNG')
    data = payload.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    rows = [{'text': text, 'raw_text': text, 'confidence': .99, 'box': box,
             'normalization_basis': 'declared_offline_protocol_fixture'}
        for text, box in (('备战阶段', [430, 33, 513, 60]), ('3-6', [441, 58, 502, 98]),
                         ('出战', [1784, 730, 1852, 770]), ('商店', [1596, 966, 1650, 998]))]
    if selection:
        rows = [{'text': '补给阶段', 'raw_text': '补给阶段', 'confidence': .99,
                 'box': [860, 142, 1050, 173], 'normalization_basis': 'declared_offline_protocol_fixture'}]
    elif shop:
        rows = [{'text': text, 'raw_text': text, 'confidence': .99, 'box': box,
                 'normalization_basis': 'declared_offline_protocol_fixture'}
            for text, box in (('备战阶段', [242, 56, 334, 87]), ('3-6', [257, 92, 314, 124]),
                             ('收起', [1596, 966, 1650, 998]), ('刷新', [1592, 472, 1646, 506]),
                             ('出战', [1784, 730, 1852, 770]))]
    page = 'supply' if selection else 'shop' if shop else 'preparation'
    observed = {'snapshot_id': digest, 'page': page,
        'fields': {'stage': None if selection else '3-6', 'level': '8', 'deployed': None if shop else '8/8'}, 'rows': rows,
        'semantic': {'rewards': rewards.detect(image, page, rows, digest),
            'coins': {'value': coins, 'bounds': runner.GOLD_HUD, 'currency_icon_gold_fraction': .8,
                      'source': 'declared_offline_protocol_fixture'},
            'team': {'checked': False, 'fully_read': False, 'units': [], 'unknown_slots': ['fixture-unread']},
            'inventory': {'checked': False, 'items': [], 'unknown_slots': ['fixture-unread']}},
        'shop': None, 'elapsed_ms': 0., 'fingerprint': fingerprint(image)}
    return {'payload': data, 'observation': observed}


@contextlib.contextmanager
def protocol_fixture(effect='normal', *, profiling=False, start_shop=False):
    """Actual Worker + command + Entry, with one inert transport publisher."""
    frames = {'two': _protocol_frame(((1550, 265), (1497, 322))),
              'one_after': _protocol_frame(((1502, 322),)),
              'one_fresh': _protocol_frame(((1505, 324),)),
              'empty': _protocol_frame(()),
              'coins_down': _protocol_frame(((1502, 322),), coins=63),
              'selection': _protocol_frame((), selection=True),
              'shop': _protocol_frame((), shop=True)}
    by_digest = {item['observation']['snapshot_id']: item['observation'] for item in frames.values()}
    fixture = compatibility.RuntimeCompatibilityTests()
    with fixture.manual_bridge_fixture() as (runtime, records, owner, control, reader):
        (runtime / 'runner-manual.json').unlink()
        control.action_index, control.orb_index = 0, 0
        control.current_frame = 'shop' if start_shop else 'two'
        control.shop_closed = not start_shop
        control.publication_trace = []
        control.validate_actions = lambda actions: [
            {'type': action['type'], 'args': [float(value) for value in action.get('args', [])]}
            for action in actions]

        def read(path):
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            return copy.deepcopy(by_digest[digest])
        reader.read = read

        def inert(value):
            actions = value.get('actions', [])
            mutations = [action for action in actions if action['type'] not in ('observe', 'wait')]
            if any(action['type'] not in ('click', 'key', 'observe', 'wait') for action in actions):
                raise AssertionError('inert reward fixture received an unsupported action')
            external = value['id'].startswith('external-')
            if mutations and not external:
                control.action_index += 1
                if not control.shop_closed:
                    control.shop_closed = True
                    control.current_frame = 'two'
                else:
                    control.orb_index += 1
                if control.orb_index == 1:
                    control.current_frame = {'zero_effect': 'two', 'drop_two': 'empty',
                        'coins_down': 'coins_down', 'unknown_selection': 'selection'}.get(effect, 'one_after')
                elif control.orb_index > 1:
                    control.current_frame = 'empty'
            elif not mutations and control.orb_index == 1 and effect == 'normal':
                # The unclicked orb moves again between result and next input.
                control.current_frame = 'one_fresh'
            item = frames[control.current_frame]
            data, digest = item['payload'], item['observation']['snapshot_id']
            frame_id = uuid.uuid4().hex
            directory = runtime / 'frames' / (hashlib.sha256(value['id'].encode()).hexdigest() + '-' + frame_id)
            directory.mkdir(parents=True)
            for filename in ('original.png', 'preview.png'):
                (directory / filename).write_bytes(data)
            completed = copy.deepcopy(actions)
            attempted = copy.deepcopy(mutations)
            if mutations and not external and effect in ('grouped_completed', 'unknown_selection'):
                extra = {'type': 'click', 'args': [1534., 356.]}
                completed.append(extra)
                attempted.append(extra)
            result = {'id': value['id'], 'ok': True, 'completed': completed,
                'input_attempted': bool(mutations), 'attempted_actions': attempted,
                'offline_fixture_receipt': True,
                'observation': {'frame_protocol': 1, 'request_id': value['id'], 'frame_id': frame_id,
                    'captured_at': runner.now(), 'snapshot': str(directory / 'preview.png'),
                    'original': str(directory / 'original.png'), 'snapshot_sha256': digest,
                    'original_sha256': digest, 'snapshot_size': [1920, 1080], 'original_size': [1920, 1080]}}
            control.published.append(copy.deepcopy(value))
            control.publication_trace.append({'request_id': value['id'], 'actions': copy.deepcopy(actions),
                'after_frame': control.current_frame, 'snapshot_id': digest, 'frame_id': frame_id,
                'completed': completed, 'external': external})
            control.write_json(runtime / 'result.json', result)
        control.publish_request = inert
        worker = fixture.frame_worker(runtime, records, owner, control, reader)
        _ready(worker)
        if profiling:
            from currency_wars_profile import ProfileRecorder
            worker.profile = ProfileRecorder(records, run_id=owner['run_id'], source='worker', enabled=True,
                comparison_key='synthetic-reward-controls-v1')
        bundle = SimpleNamespace(worker=worker, control=control, frames=frames, runtime=runtime,
                                 records=records, owner=owner, reader=reader, start_shop=start_shop)
        try:
            yield bundle
        finally:
            if profiling:
                worker.profile.close(complete=False)


def _summary(bundle, elapsed):
    worker, control = bundle.worker, bundle.control
    requests = [event['request'] for event in worker.log_events if event.get('event') == 'strategy_request']
    steps = [json.loads(path.read_text(encoding='utf8')) for path in sorted(bundle.records.glob('reward-step-*.json'))]
    steps.sort(key=lambda step: step.get('before', {}).get('observation', {}).get('captured_at', ''))
    checklist = worker.preparation_checklist(worker.last_observation)
    return {'kind': 'synthetic_single_click_contract', 'physical_inputs': 0, 'fresh_game_captures': 0,
        'start_page': 'shop' if bundle.start_shop else 'preparation',
        'simulated_clicks': [action['args'] for event in control.publication_trace
            for action in event['actions'] if action['type'] == 'click' and not event['external']],
        'normal_root_requests': sum(request['return_reason']['category'] != 'exception' for request in requests),
        'exception_root_requests': sum(request['return_reason']['category'] == 'exception' for request in requests),
        'requests': [{'kind': request['kind'], **request['return_reason']} for request in requests],
        'next_phase': checklist['phase'], 'completed_phases': [phase for phase in runner.coaching.PHASES
            if worker.preparation_reviews.get(phase, {}).get('completed') is True],
        'phase_denominator': list(runner.coaching.PHASES), 'battle_ready': checklist['battle_ready'],
        'steps': [{key: step.get(key) for key in ('kind', 'status', 'outcome', 'request_id',
            'publication_attempted', 'observed_coin_delta', 'after_coins', 'all_rewards_cleared', 'reason')}
            for step in steps],
        'publication_trace': control.publication_trace, 'elapsed_seconds': elapsed,
        'timing_scope': 'Synthetic pixel/semantic/receipt contract; no real OCR, animation or supervisor wait. '
            'Fixture construction and native detector preparation occur before the measured Worker window.',
        'profile_operation_meaning': {'capture': 'Immutable PNG/hash/path validation; no game screenshot capture.',
            'ocr': 'Fixture semantic lookup, not OCR engine time.',
            'publication': 'Inert fixture receipt/frame file writes; no desktop or game input.',
            'input_animation': 'Not executed; .7-second wait tokens are simulated without sleeping.'},
        'full_team_read': False, 'all_rewards_cleared': None}


def escape_reply(worker):
    request = worker.state['decision_request']
    return {'request_id': request['request_id'], 'snapshot_id': request['snapshot_id'],
        'resume_epoch': worker.epoch(), 'actions': [{'type': 'key', 'args': [27],
            'expected_page': 'supply', 'guard_texts': ['补给阶段'],
            'reason': '人工协议fixture：交付不明后尝试通用退出，发布必须被原pending拒绝'}]}


def mutation_publications(control):
    return sum(any(action['type'] not in ('observe', 'wait') for action in request.get('actions', []))
               for request in control.published)


def replay_protocols():
    results = []
    cases = [('normal', 'normal', False), ('normal_shop', 'normal', True)]
    cases.extend((effect, effect, False)
        for effect in ('zero_effect', 'drop_two', 'coins_down', 'grouped_completed', 'unknown_selection'))
    for case_id, effect, start_shop in cases:
        with protocol_fixture(effect, profiling=True, start_shop=start_shop) as bundle:
            begin = time.perf_counter()
            bundle.worker.tick(bundle.worker.observe())
            result = _summary(bundle, time.perf_counter()-begin)
            if effect != 'normal':
                before = len(bundle.control.published)
                bundle.worker.advance_rewards(bundle.worker.last_observation)
                result['pending_retry_publications'] = len(bundle.control.published)-before
            if effect == 'unknown_selection':
                count = mutation_publications(bundle.control)
                try:
                    bundle.worker.execute_plan(escape_reply(bundle.worker))
                except (ValueError, RuntimeError) as exc:
                    result['generic_escape_refused'] = str(exc)
                else:
                    raise AssertionError('Unknown reward delivery allowed a generic ESC reply')
                result['generic_escape_new_mutation_publications'] = mutation_publications(bundle.control)-count
                if result['generic_escape_new_mutation_publications']:
                    raise AssertionError('Unknown reward delivery published a generic ESC reply')
            from currency_wars_profile import read_events, summarize_events
            bundle.worker.profile.close(complete=False)
            events, issues = read_events([bundle.worker.profile.path])
            result['profile'] = summarize_events(events, issues)
            result['id'] = case_id
            results.append(result)
    return results


def replay_real_observations():
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf8'))
    reader, results = Perception(), []
    for case in manifest['frames']:
        path = FIXTURES / case['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != case['export_image_sha256']:
            raise ValueError('historical frame hash mismatch')
        begin = time.perf_counter()
        observed = reader.read(path)
        results.append({'file': case['file'], 'snapshot_id': observed['snapshot_id'],
            'kind': 'unchanged_historical_png_production_perception', 'page': observed['page'],
            'fields': observed['fields'], 'rewards': observed['semantic'].get('rewards'),
            'coins': observed['semantic'].get('coins'),
            'team_checked': observed['semantic'].get('team', {}).get('checked'),
            'team_fully_read': observed['semantic'].get('team', {}).get('fully_read'),
            'elapsed_seconds': time.perf_counter()-begin, 'engine_cold': not results,
            'physical_inputs': 0, 'fresh_game_captures': 0})
    return {'observations': results, 'original_receipt_groups': manifest['receipts'],
        'current_native_action_replay': False,
        'limitation': 'Original close wait=.3 and grouped claim waits=.4/.5 are not current single-click wait=.7 receipts. '
            'No per-click historical successor is invented; observed disappearance does not clear all rewards.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reuse-historical', type=Path,
        help='Reuse previously read unchanged PNG observations only when both reader source hashes still match.')
    args = parser.parse_args()
    sources = ('currency_wars_runner.py', 'currency_wars_perception.py', 'currency_wars_rewards.py')
    source_hashes = {name: hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest() for name in sources}
    if args.reuse_historical:
        previous = json.loads(args.reuse_historical.read_text(encoding='utf8'))
        historical = copy.deepcopy(previous['historical_evidence'])
        old_hashes = historical.get('source_hashes', previous['source_hashes'])
        for name in ('currency_wars_perception.py', 'currency_wars_rewards.py'):
            if old_hashes[name] != source_hashes[name]:
                raise ValueError('Historical production reader changed; unchanged-Reader reuse is unavailable')
        historical['source_hashes'] = {name: old_hashes[name]
            for name in ('currency_wars_perception.py', 'currency_wars_rewards.py')}
        historical.setdefault('read_report_created_at', previous['created_at'])
        historical['reused_without_rerunning_ocr'] = True
    else:
        historical = replay_real_observations()
        historical['source_hashes'] = {name: source_hashes[name]
            for name in ('currency_wars_perception.py', 'currency_wars_rewards.py')}
        historical['read_report_created_at'] = runner.now()
        historical['reused_without_rerunning_ocr'] = False
    report = {'schema': 'currency-wars-offline-reward-continuity/v1', 'offline_only': True,
        'created_at': runner.now(), 'physical_inputs': 0, 'fresh_game_captures': 0,
        'source_hashes': source_hashes, 'protocol_source_hashes': source_hashes,
        'synthetic_contracts': replay_protocols(), 'historical_evidence': historical,
        'baseline_comparison': {'baseline_sha': 'bcec3280677e6a419675cc7448bf0dca8fe36ec3',
            'baseline_evidence': 'Source inspection of the baseline Worker.tick shop/preparation branches.',
            'baseline_behavior': 'The original shop branch can close the shop. The preparation branch asks ROOT '
                'while rewards are unreviewed, without initiating blue-orb claims locally. '
                'Existing fixed-loot guards for ROOT-supplied plans are not claimed absent.',
            'same_endpoint_comparison_available': False,
            'reason': 'The initial ROOT review and the post-local-controls full-field review are different endpoints; '
                'no invented before/after ROOT reduction or complete-preparation speedup.'}}
    if any(hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest() != digest
           for name, digest in source_hashes.items()):
        raise RuntimeError('Production source changed during replay; rerun after edits finish')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(str(args.out))


if __name__ == '__main__':
    main()
