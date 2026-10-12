"""Offline native unit-tooltip properties from an explicit saved PNG.

Only the observed one-star header, displayed coin values 1/2/3, and three
complete empty equipment cells are covered. No capture, control, identity,
catalogue, cache write, ownership or slot-association API is used.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image


SCHEMA = 'currency-wars-unit-properties-read/v1'
PROFILE = 'retained-native-tooltip-1920x1080-v1'
ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIRECTORY = 'debug/runner-01a1271b-309974afdb78'
# Values are visual annotations of the complete controls in these immutable
# native frames, not a mapping from the input file/name to a unit property.
REFERENCES = (
    ('unit-slot2-tooltip-original.png',
     'ea5175ebf1ddce09df4f6c057dce49090ac420ce46c49f065cddde105fa1b05f', 1),
    ('unit-slot4-tooltip-original.png',
     '63d8e6aa1c0dcca2c76a4ed876800f06e5cd9bd3efabe37cc25858abb21f3f98', 3),
    ('unit-slot5-tooltip-original.png',
     '8d31263c0e18e3ceed44e37a353b9fc9c52ab334f6929cccdea444070b69f9ae', 2),
)
STAR_HEADER = (1552, 157, 1707, 190)  # Both endcaps and the whole star strip.
DISPLAYED_COIN = (1793, 188, 1869, 232)  # Coin, full value cell and its margins.
GEAR_SLOTS = ((1608, 780, 1682, 855), (1695, 780, 1769, 855), (1782, 780, 1856, 855))
ANCHORS = ((1402, 889, 1593, 941), (1402, 790, 1577, 842))  # Details / equipment recommendation.
# Calibration: held-out gray/green frames differ by <=3 RGB levels and mean
# <=.043 from their native reference. Keep whole controls; never mask stars,
# equipment symbols, digits or edges, align images, rescale, or fill a clip.
MAX_CHANNEL_DELTA = 4
MAX_MEAN_DELTA = .1


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _rgb(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or image.size != (1920, 1080):
            raise ValueError('unsupported_format_or_native_geometry')
        if 'A' in image.getbands() and np.any(np.array(image.getchannel('A')) != 255):
            raise ValueError('transparent_source_not_native_visible_frame')
        return np.array(image.convert('RGB'))


def _crop(rgb, bounds):
    left, top, right, bottom = bounds
    return rgb[top:bottom, left:right]


class UnitPropertiesReader:
    """Load pinned local references once; every input is independently read.

    Reference arrays are immutable in-memory evidence after their full PNG SHA
    is verified. Missing/changed references fail closed; no resource is created.
    Instantiate again to use another reference root. This is an offline reader,
    not a production-ready source manifest or live frame binding.
    """

    def __init__(self, reference_root=None):
        started = time.perf_counter()
        self.references, self.reference_errors = [], []
        directory = Path(reference_root) if reference_root is not None else ROOT / REFERENCE_DIRECTORY
        for filename, expected, value in REFERENCES:
            path = directory / filename
            try:
                data = path.read_bytes()
                if len(data) > 25_000_000 or _sha(data) != expected:
                    raise ValueError('reference_sha256_differs')
                rgb = _rgb(data)
                rgb.setflags(write=False)
                self.references.append({'source_path': str(path.resolve()), 'source_sha256': expected,
                                        'displayed_coin_value': value, 'rgb': rgb})
            except (OSError, ValueError) as error:
                self.reference_errors.append({'file': filename, 'reason': str(error)})
        self.reference_load_ms = round((time.perf_counter() - started) * 1000, 3)

    def _match(self, rgb, bounds, frame_sha256, references=None):
        patch = _crop(rgb, bounds)
        matches = []
        for reference in self.references if references is None else references:
            template = _crop(reference['rgb'], bounds)
            delta = np.abs(patch.astype(np.int16) - template.astype(np.int16))
            maximum, mean = int(delta.max()), float(delta.mean())
            if maximum <= MAX_CHANNEL_DELTA and mean <= MAX_MEAN_DELTA:
                matches.append({'bounds': list(bounds), 'frame_sha256': frame_sha256,
                    'method': 'pinned_complete_native_rgb_control', 'max_channel_delta': maximum,
                    'mean_delta': round(mean, 8), 'roi_rgb_sha256': _sha(patch.tobytes()),
                    'reference': {'source_path': reference['source_path'],
                        'source_sha256': reference['source_sha256'], 'bounds': list(bounds),
                        'roi_rgb_sha256': _sha(template.tobytes())},
                    **({'displayed_coin_value': reference['displayed_coin_value']}
                       if tuple(bounds) == DISPLAYED_COIN else {})})
        return sorted(matches, key=lambda item: (item['max_channel_delta'], item['mean_delta']))

    def read(self, image_path):
        started = time.perf_counter()
        path = Path(image_path)
        result = {'schema': SCHEMA, 'profile': PROFILE, 'source_path': str(path.resolve()),
            'frame_sha256': None, 'source_format': None, 'size': None, 'status': 'unknown',
            'star': {'value': None, 'bounds': list(STAR_HEADER), 'status': 'unknown', 'evidence': None},
            'displayed_coin': {'value': None, 'semantic': 'tooltip_displayed_coin_value',
                'bounds': list(DISPLAYED_COIN), 'status': 'unknown', 'evidence': None},
            'equipment': {'slots': [{'slot_index': index + 1, 'bounds': list(bounds),
                                    'status': 'unknown', 'evidence': None} for index, bounds in enumerate(GEAR_SLOTS)],
                          'complete': False, 'empty_count': None, 'occupied_count': None},
            'panel_evidence': [], 'reasons': [], 'name': None, 'position': None, 'role_id': None,
            'roster_slot': None, 'physical_uid': None, 'owned': None, 'runtime_ownership_bound': False,
            'needs_binding': True, 'production_approved': False, 'input_sent': False,
            'reference_load_ms': self.reference_load_ms}
        try:
            if not path.is_file() or not 0 < path.stat().st_size <= 25_000_000:
                raise ValueError('missing_or_oversized_source')
            data = path.read_bytes()
            result['frame_sha256'] = _sha(data)
            with Image.open(io.BytesIO(data)) as image:
                result.update(source_format=image.format, size=list(image.size))
            rgb = _rgb(data)
            if self.reference_errors or len(self.references) != len(REFERENCES):
                raise ValueError('missing_or_changed_pinned_native_reference')
            for bounds in ANCHORS:
                matches = self._match(rgb, bounds, result['frame_sha256'], self.references[:1])
                if not matches:
                    raise ValueError('complete_native_tooltip_anchors_missing_or_covered')
                result['panel_evidence'].append(matches[0])
            stars = self._match(rgb, STAR_HEADER, result['frame_sha256'])
            if stars:
                result['star'].update(value=1, status='readable', evidence=stars[0])
            else:
                result['reasons'].append('full_star_header_not_in_observed_one_star_profile')
            coins = self._match(rgb, DISPLAYED_COIN, result['frame_sha256'])
            if coins and len({item['displayed_coin_value'] for item in coins}) == 1:
                result['displayed_coin'].update(value=coins[0]['displayed_coin_value'],
                                                status='readable', evidence=coins[0])
            else:
                result['reasons'].append('complete_coin_and_value_not_in_observed_1_2_3_profile')
            for slot in result['equipment']['slots']:
                matches = self._match(rgb, slot['bounds'], result['frame_sha256'], self.references[:1])
                if matches:
                    slot.update(status='empty', evidence=matches[0])
            if all(slot['status'] == 'empty' for slot in result['equipment']['slots']):
                result['equipment'].update(complete=True, empty_count=3, occupied_count=0)
            else:
                result['reasons'].append('one_or_more_equipment_cells_not_complete_known_empty_template')
            result['status'] = 'readable_properties' if not result['reasons'] else 'partial_or_unknown'
        except (OSError, ValueError) as error:
            result['reasons'].append(str(error))
        result['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    args = parser.parse_args()
    print(json.dumps(UnitPropertiesReader().read(args.image), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
