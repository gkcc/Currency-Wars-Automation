"""Replay four retained public PNGs through production OCR; no game access."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
# Process-local privacy choice before any inference runtime import, not a
# network-policy change. The API alone may miss an initialization event.
os.environ['ORT_DISABLE_TELEMETRY'] = '1'
import onnxruntime
onnxruntime.disable_telemetry_events()
from currency_wars_perception import Perception
BASELINE_REF = '4830dfe8510171519b1961b2116968495d51a292'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=('before', 'after'), default='after')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixtures = ROOT / 'handoff/2026-10-07/fixtures'
    manifest = json.loads((fixtures / 'manifest.json').read_text(encoding='utf8'))
    reader_type = Perception
    source_path = 'tools/currency_wars_perception.py'
    source_bytes = (ROOT / source_path).read_bytes()
    if args.version == 'before':
        # Execute the unchanged production reader from the specified Git blob.
        # Its normal resource paths remain rooted in this checkout's tools/.
        source_bytes = subprocess.check_output(['git', 'show', BASELINE_REF + ':' + source_path], cwd=ROOT)
        baseline = types.ModuleType('currency_wars_baseline_perception')
        baseline.__file__ = str(ROOT / source_path)
        exec(compile(source_bytes, baseline.__file__, 'exec'), baseline.__dict__)
        reader_type = baseline.Perception
    result = {'schema': 'currency-wars-player-hud-replay/v1', 'version': args.version,
        'base_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'fixture_commit': '9c3329c', 'game_inputs': 0, 'fresh_captures': 0,
        'telemetry_control': {'ORT_DISABLE_TELEMETRY': '1', 'api_disabled_before_session': True},
        'reader_source_ref': BASELINE_REF if args.version == 'before' else 'working_tree_sha256',
        'source_sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in ('tools/currency_wars_perception.py', 'tools/currency_wars_state_reader.py')},
        'packages': {p: importlib.metadata.version(p)
            for p in ('rapidocr-onnxruntime', 'onnxruntime', 'opencv-python', 'numpy', 'Pillow')},
        'limitations': [
            'One serial pass of four existing PNGs; first read includes engine initialization; this is not node or end-to-end timing.',
            'The public repository has no private shop/state templates; missing resources stay unknown.'],
        'cases': []}
    result['source_sha256'][source_path] = hashlib.sha256(source_bytes).hexdigest()
    reader = reader_type()
    for index, case in enumerate(manifest['cases']):
        path = fixtures / case['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != case['export_sha256']:
            raise ValueError('Public fixture export hash mismatch: ' + case['id'])
        start = time.perf_counter()
        observed = reader.read(path)
        elapsed = time.perf_counter() - start
        # Portable report paths; the image bytes, not a filename, bind evidence.
        observed['image'] = str(path.relative_to(ROOT))
        result['cases'].append({'id': case['id'], 'export_sha256': case['export_sha256'],
            'engine_cold': index == 0, 'seconds': elapsed, 'observed': observed})
        print(case['id'], round(elapsed, 6), observed['page'], observed['fields'], flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
