"""Read only the retained, visible overflow-row footprint in a saved PNG.

This offline candidate does not establish complete overflow inventory. In
particular, an empty visible row never sets overflow_checked or overflow_count.
There is no capture, controller, SDK, catalogue, or production-reader dependency.
The two pinned historical native frames provide the actual positive portraits
and independent empty background; no main-bench occupancy enters the decision.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import time

import cv2
import numpy as np
from PIL import Image


SCHEMA = "currency-wars-visible-overflow-probe/v1"
PROFILE = "retained-20261006-right-aligned-eight-portraits-1920x1080/v1"
SOURCE_DIR = Path(__file__).resolve().parents[1] / "debug/runner-01a10ddf-351522298f53"
SOURCES = {
    "positive": ("e3e844d9e47c4cc0a720f32a155f9736-after.png",
                 "0e5f6e9a0dea3122373b3686f00e56ae1e88823e43f842f68dcddd6ec39ac464"),
    "empty": ("cfa3696d089f4a598aaee9952b84f250-after.png",
              "3c1166e7fbd35068fa1f0394c6db0631b892043fb3e0452206f92492d1eb2d32"),
}
# Below the normal back-row cards (bottom ~= 740), above the main bench.
# These are the distinctive lower footprints, not full portrait/name ROIs.
FOOTPRINT = (380, 744, 1498, 820)
CARD_X = (758, 844, 930, 1015, 1101, 1187, 1272, 1358)
ANCHORS = {"preparation": (430, 34, 512, 57),
           "front_label": (905, 290, 1021, 321),
           "back_label": (905, 569, 1025, 596)}
MAX_BYTES = 16 * 1024 * 1024


def _crop(rgb, bounds):
    x1, y1, x2, y2 = bounds
    return rgb[y1:y2, x1:x2]


def _load_png(path, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("explicit_lowercase_sha256_required")
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("image_exceeds_16MiB")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError("frame_sha256_mismatch")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG" or image.size != (1920, 1080):
            raise ValueError("unsupported_native_png_geometry")
        if "A" in image.getbands() and image.getchannel("A").getextrema() != (255, 255):
            raise ValueError("nonopaque_native_frame")
        rgb = np.array(image.convert("RGB"))
    rgb.flags.writeable = False
    return rgb


def _comparison(actual, expected):
    difference = np.abs(actual.astype(np.float32) - expected.astype(np.float32))
    return {"mean_absolute_rgb_error": round(float(difference.mean()), 5),
            "large_error_fraction": round(float((difference.max(axis=2) > 60).mean()), 6)}


class OverflowProbe:
    """Pinned historical profile, independent of bench slots and population.

    ``visible_row.count`` is limited to the eight retained portrait footprints.
    Other portraits, shifted/scaled layouts, inconsistent pixels and occlusions
    yield unknown. The source game build, maximum number of overflow rows, and
    any collapsed/hidden inventory are unverified. Therefore the global solver
    gate stays unchecked for every result, including a visible count of zero.
    """

    def __init__(self, source_directory=SOURCE_DIR):
        directory = Path(source_directory)
        self._sources = {key: _load_png(directory / filename, digest)
                         for key, (filename, digest) in SOURCES.items()}
        self.source_evidence = {key: {"path": str((directory / filename).resolve()),
                                      "sha256": digest}
                                for key, (filename, digest) in SOURCES.items()}

    def read(self, path, *, frame_sha256):
        """Read explicit saved bytes. Digest/file errors raise; no side effects.

        Bad geometry is unknown. A caller must separately authenticate capture
        receipt, run, epoch and currentness before using any candidate field.
        """
        started = time.perf_counter()
        result = {"schema": SCHEMA, "profile": PROFILE, "offline_candidate": True,
                  "input": {"path": str(Path(path).resolve()), "sha256": frame_sha256},
                  "sources": self.source_evidence,
                  "visible_row": {"status": "unknown", "checked": False, "count": None,
                                  "footprint_roi": list(FOOTPRINT)},
                  "overflow_checked": False, "overflow_count": None,
                  "overflow_present": None, "currentness": "unverified",
                  "coverage": {"max_observed_visible_count": 8,
                               "other_portraits": "unsupported",
                               "complete_inventory": "unverified",
                               "hidden_or_additional_rows": "unverified",
                               "source_game_build": "unverified"},
                  "reasons": [], "evidence": {}}

        def finish():
            result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            return result

        try:
            actual = _load_png(path, frame_sha256)
        except ValueError as error:
            if str(error) not in ("unsupported_native_png_geometry", "nonopaque_native_frame"):
                raise
            result["reasons"].append(str(error))
            return finish()
        empty, positive = self._sources["empty"], self._sources["positive"]
        anchor_scores = {key: round(float(cv2.matchTemplate(_crop(actual, box), _crop(empty, box),
                                                           cv2.TM_CCOEFF_NORMED)[0, 0]), 6)
                         for key, box in ANCHORS.items()}
        result["evidence"]["anchor_scores"] = anchor_scores
        if not all(np.isfinite(score) and score >= .99 for score in anchor_scores.values()):
            result["reasons"].append("unverified_preparation_geometry_or_obscured_anchors")
            return finish()

        # A positive is an actual retained portrait match, never merely a
        # non-blue patch. Unfamiliar pixels cannot become an empty slot.
        expected = _crop(empty, FOOTPRINT).copy()
        cards, occupied = [], []
        for index, x1 in enumerate(CARD_X):
            box = (x1, 744, x1 + 76, 805)
            patch, template = _crop(actual, box), _crop(positive, box)
            metrics = _comparison(patch, template)
            score = float(cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED)[0, 0])
            matched = bool(np.isfinite(score) and score >= .985
                           and metrics["mean_absolute_rgb_error"] <= 5
                           and metrics["large_error_fraction"] <= .025)
            cards.append({"source_position": index + 1, "roi": list(box), "matched": matched,
                          "correlation": round(score, 6), **metrics})
            if matched:
                occupied.append(index)
                expected[:61, x1-FOOTPRINT[0]:x1-FOOTPRINT[0]+76] = template
        result["evidence"]["retained_portrait_matches"] = cards
        if occupied and occupied != list(range(occupied[0], len(CARD_X))):
            result["reasons"].append("unsupported_noncontiguous_retained_row")
            return finish()

        # Check every pixel in the continuous strip, including the unmodelled
        # left extension and the gaps. Broad averages alone would hide a small
        # extra card or an occluding panel, so validation is local in 64x32 tiles.
        strip = _crop(actual, FOOTPRINT)
        tiles = []
        for y in range(0, strip.shape[0], 32):
            for x in range(0, strip.shape[1], 64):
                metrics = _comparison(strip[y:y+32, x:x+64], expected[y:y+32, x:x+64])
                tiles.append({"roi": [x+380, y+744, min(x+64+380, 1498), min(y+32+744, 820)],
                              **metrics})
        bad = [tile for tile in tiles if tile["mean_absolute_rgb_error"] > 8
               or tile["large_error_fraction"] > .025]
        result["evidence"].update(tile_count=len(tiles), failed_tiles=bad,
                                  maximum_tile_error=max(t["mean_absolute_rgb_error"] for t in tiles),
                                  maximum_large_error_fraction=max(t["large_error_fraction"] for t in tiles))
        if bad:
            result["reasons"].append("unexplained_pixels_or_occlusion_in_visible_footprint")
            return finish()
        result["visible_row"].update(status="present" if occupied else "empty",
                                     checked=True, count=len(occupied))
        if occupied:
            result["overflow_present"] = True
        result["reasons"].append("global_overflow_coverage_not_established")
        return finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--sha256", required=True, help="SHA256 of the actual saved native PNG")
    args = parser.parse_args()
    print(json.dumps(OverflowProbe().read(args.image, frame_sha256=args.sha256),
                     ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
