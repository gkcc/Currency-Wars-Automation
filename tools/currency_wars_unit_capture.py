"""Bounded capture through an existing ROOT runner; never direct game input.

Every action goes through original decide, receipt, epoch and native guards.
Names are candidates from current OCR/catalogue; no ownership/star is invented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import currency_wars_runner as runner
from currency_wars_database import DEFAULT_DB, normalize_name, open_db
from currency_wars_state_reader import native_slots


def capture(run, chat, slots, *, close_current=False, max_seconds=240):
    run = Path(run).resolve()
    owner = runner.entry.read_json(run / 'runner-owner.json')
    if owner['chat_id'] != chat:
        raise ValueError('Explicit existing runner/chat ownership mismatch')
    runner.load(str(run), chat, owner['run_token'])
    began = time.monotonic()
    deadline = began + max_seconds
    database = open_db(DEFAULT_DB)
    results = []

    def request(kind):
        until = min(deadline, time.monotonic() + 35)
        while time.monotonic() < until:
            state = runner.entry.read_json(run / 'runner-state.json')
            if state['control_mode'] in ('manual', 'paused', 'stopped'):
                raise RuntimeError('Original runner paused/stopped; no new action')
            current = state.get('decision_request') or {}
            if current.get('kind') == kind:
                return state, current
            time.sleep(.25)
        raise TimeoutError('Same original runner awaits result; never resend input')

    def title(current):
        names = []
        for row in current['observation']['rows']:
            box = row.get('box', [])
            if (len(box) != 4 or row.get('confidence', 0) < .90
                    or not (1480 <= box[0] <= 1740 and 205 <= box[1] <= 245 and box[3] <= 250)):
                continue
            matches = {r[0] for r in database.execute(
                "SELECT e.name FROM entities e JOIN aliases a ON a.entity_id=e.id "
                "WHERE e.kind='characters' AND e.availability='current' AND a.normalized_name=?",
                (normalize_name(row['text']),))}
            if len(matches) == 1:
                names.append(row)
        if len(names) != 1 or not any(r['text'] == '出售' and r.get('confidence', 0) >= .90
                                      for r in current['observation']['rows']):
            raise ValueError('Unknown/ambiguous named unit panel; supervisor review needed')
        return names[0]

    def submit(state, current, action, filename):
        reply = {key: current[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')}
        reply['actions'] = [action]
        records = Path(state['journal_file']).parent
        path = records / filename
        path.write_text(json.dumps(reply, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result = runner.command_cli(SimpleNamespace(command='decide', chat_id=chat,
            run_dir=str(run), run_token=owner['run_token'], reply_file=str(path),
            reason=action['reason'], handoff=False))
        if result.get('ok') is not True:
            raise RuntimeError('Original publication refused; do not retry')

    def close(label):
        state, current = request('unknown_page')
        name = title(current)['text']
        submit(state, current, {'type': 'key', 'args': [27], 'expected_page': 'unknown',
            'guard_texts': [name, '出售'],
            'reason': '已保留完整姓名详情，关闭浮窗继续单次读取；不购物出售'},
            f'batch-close-{label}.json')
        request('preparation_strategy')

    try:
        if close_current:
            close('current')
        for number in slots:
            state, current = request('preparation_strategy')
            if current.get('preparation_checklist', {}).get('phase') != 'inventory_cleanup':
                raise ValueError('Current original reward-clear phase not satisfied')
            slot = next(s for s in native_slots() if s['row'] == 'bench' and s['slot'] == number)
            before = {key: current[key] for key in ('request_id', 'snapshot_id', 'resume_epoch', 'original_png')}
            submit(state, current, {'type': 'click_point', 'args': slot['point'],
                'expected_page': 'preparation', 'guard_texts': ['备战阶段', '出战', '商店'],
                'reason': '自动有限采集未识别备选槽详情，固定规则查询数据库；不作临场策略推理',
                'target_evidence': {'control_id': 'owned_unit_details', 'snapshot_id': current['snapshot_id'],
                                    'row': 'bench', 'slot': number, 'bounds': slot['bounds']}},
                f'batch-open-slot{number}.json')
            state, current = request('unknown_page')
            name = title(current)
            raw = Path(current['original_png']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != current['snapshot_id']:
                raise ValueError('Original successor PNG SHA changed')
            records = Path(state['journal_file']).parent
            target = records / f'unit-slot{number}-tooltip-original.png'
            target.write_bytes(raw)
            record = {'slot': number, 'name_candidate': name['text'], 'title_row': name, 'before': before,
                'after': {key: current[key] for key in ('request_id', 'snapshot_id', 'resume_epoch')},
                'passive_capture_request_id': current['observation']['capture_request_id'],
                'source_frame': str(target), 'star': None, 'gear': None,
                'runtime_identity_committed': False, 'model_api_calls': 0}
            (records / f'unit-slot{number}-capture.json').write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            results.append(record)
            print(json.dumps({'slot': number, 'native_full_title': name['text'],
                              'confidence': name['confidence'], 'saved': True}, ensure_ascii=False), flush=True)
            close(f'slot{number}')
        elapsed = round((time.monotonic() - began) * 1000, 2)
        summary = {'schema': 'currency-wars-unit-capture-batch/v1', 'run_id': owner['run_id'],
            'slots': results, 'model_api_calls': 0, 'elapsed_ms': elapsed, 'final_page': 'preparation',
            'input_entry': 'original runner decide only; no direct input', 'production_identity_written': False}
        (records / 'unit-batch-capture.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return summary
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--slots', required=True, type=int, nargs='+', choices=range(1, 10))
    parser.add_argument('--close-current', action='store_true')
    parser.add_argument('--max-seconds', type=int, default=240)
    args = parser.parse_args()
    if not 30 <= args.max_seconds <= 600 or len(set(args.slots)) != len(args.slots):
        parser.error('Unique bounded slots and 30..600 seconds required')
    result = capture(args.run_dir, args.chat_id, args.slots,
                     close_current=args.close_current, max_seconds=args.max_seconds)
    print(json.dumps({'batch_done': True, 'elapsed_ms': result['elapsed_ms'], 'model_api_calls': 0}))


if __name__ == '__main__':
    main()
