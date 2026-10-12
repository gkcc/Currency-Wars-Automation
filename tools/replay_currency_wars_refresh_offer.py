"""Read three retained PNGs through production Perception; no game/Worker replay.

The historical mixed-action groups are recorded verbatim, never attached to the
three images as a native transaction chain. Worker tests use separate, explicitly
declared inert protocol fixtures in test_currency_wars_refresh_offer_flow.py.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess

os.environ['ORT_DISABLE_TELEMETRY'] = '1'

from PIL import Image
import currency_wars_perception as perception
import currency_wars_refresh_offer as refresh


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'handoff/2026-10-07/free-refresh-sequence'
SOURCE_FILES = ('tools/currency_wars_refresh_offer.py', 'tools/currency_wars_perception.py',
    'tools/currency_wars_economy.py', 'tools/currency_wars_runner.py',
    'tools/replay_currency_wars_refresh_offer.py', 'tools/test_currency_wars_refresh_offer.py',
    'tools/test_currency_wars_refresh_offer_flow.py', 'tools/test_currency_wars_perception_scope.py',
    'tools/refresh_offer_resources/currency_coin.png', 'tools/refresh_offer_resources/SOURCES.json')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name: sha256(ROOT / name) for name in SOURCE_FILES}


def read_true_frames():
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))
    prior = json.loads((ROOT / 'handoff/2026-10-07/ROOT_REFRESH_WIDGET_READ.json').read_text(encoding='utf-8'))
    expected_files = ['f00.png', 'f01.png', 'f02.png']
    if ([item['file'] for item in manifest['frames']] != expected_files
            or [item['file'] for item in prior['frames']] != expected_files):
        raise ValueError('All three retained PNGs and their original native reads are required')
    reader, records = perception.Perception(), []
    for item, baseline in zip(manifest['frames'], prior['frames']):
        path = FIXTURES / item['file']
        if sha256(path) != item['export_image_sha256'] or item['file'] != baseline['file']:
            raise ValueError('Retained PNG identity or frame order differs')
        observed = reader.read(path, force=True, scope='economy')
        offer = observed['semantic']['refresh_offer']
        with Image.open(path) as opened:
            compact = refresh.consume_offer(offer, opened.convert('RGB'), observed['snapshot_id'], observed['page'])
        visible = item['visible_widget']  # Independent labels, used only AFTER the production read.
        expected = {'mode': 'free' if visible['mode_visual_label'] == 'free_offer' else 'paid',
                    'free_remaining': visible['free_count_literal'], 'paid_cost': visible['price_literal']}
        old_rows = [{'text': row['text'], 'box': row['box'], 'confidence': row['confidence']}
                    for row in offer['raw_rows']]
        original_fields_equal = observed['fields'] == baseline['native_player_fields']
        original_rows_equal = old_rows == baseline['native_refresh_widget_ocr_rows']
        records.append({'file': item['file'], 'png_sha256': observed['snapshot_id'],
            'page': observed['page'], 'native_player_fields': observed['fields'],
            'offer': offer, 'consumed_offer': compact, 'manual_expected': expected,
            'matches_independent_labels': compact == expected,
            'original_widget_rows_match_root_baseline': original_rows_equal,
            'original_player_fields_match_root_baseline': original_fields_equal,
            # One complete native row list per PNG; no repeated full Worker state.
            'native_rows': copy.deepcopy(observed['rows']),
            'read_contract': observed['read_contract'], 'read_timing': observed['read_timing'],
            'shop_summary': {key: (observed.get('shop') or {}).get(key) for key in ('ok', 'status', 'reason')},
            'team_status': observed['semantic']['team']['status'],
            'team_checked': observed['semantic']['team']['checked'],
            'fields_not_inferred': ['free_refreshes=0', 'budget', 'shop_complete', 'player_level', 'full_roster']})
        if sha256(path) != item['export_image_sha256']:
            raise ValueError('Retained PNG changed during read')
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before = source_hashes()
    records = read_true_frames()
    after = source_hashes()
    groups = []
    for name in ('f01-group-receipt.json', 'f02-group-receipt.json'):
        path = FIXTURES / name
        original = json.loads(path.read_text(encoding='utf-8'))
        groups.append({'file': name, 'file_sha256': sha256(path), 'id': original['id'],
            'source_receipt_sha256': original['source_receipt_sha256'], 'completed': original['completed'],
            'native_immutable_frame_identity_available': False, 'independent_per_action_images': False,
            'original_snapshot_alias': 'game-preview.png', 'original_snapshot_alias_exists': False,
            'used_as_worker_success': False})
    passed = before == after and all(row['matches_independent_labels']
        and row['original_widget_rows_match_root_baseline']
        and row['original_player_fields_match_root_baseline'] for row in records)
    versions = {}
    for package in ('rapidocr-onnxruntime', 'onnxruntime', 'Pillow', 'numpy', 'opencv-python'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    result = {'schema': 'currency-wars-refresh-offer-read-audit/v1',
        'code_base_sha': '060ec1cfea03af1d474e0296f5ec0df73b16fa91',
        'evidence_main_sha': 'b5da98042a07d91a41e11baaacd34cc1f55e7c34',
        'working_tree_head_at_run': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'binding_rule': 'Pre-publication code is bound by source SHA256 before/after, not by the earlier HEAD alone.',
        'source_sha256': before, 'source_sha256_after': after, 'source_unchanged': before == after,
        'python': platform.python_version(), 'platform': platform.system(), 'packages': versions,
        'offline_only': True, 'game_inputs': 0, 'new_game_captures': 0, 'candidate_installed': False,
        'online_model_calls': 0, 'frames': records, 'historical_groups': groups, 'passed': passed,
        'coverage': 'Three retained full PNGs through current production Perception and refresh consumer; no Worker transaction claimed.',
        'resource_limit': 'Only public checkout resources are available here; private-resource shop completeness is not inferred.',
        'timing_limit': 'Single local reads only; no before/after business timing ratio, real action cost or complete game speed claim.',
        'publication': 'Complete native rows once per PNG and all refresh crop outputs are included here. No unpublished raw file is required.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Compact output retains every row, crop result and failure; it omits duplicated Worker objects.
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'passed': passed, 'frames': [
        {'file': row['file'], 'offer': row['consumed_offer'], 'numeric_ocr_calls': row['offer']['numeric_ocr_calls'],
         'player_fields': row['native_player_fields']} for row in records]}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
