"""Read an existing Currency Wars shop screenshot. No capture or input APIs.

Recognition ideas: Shasnow/StarRailAssistant, fixed f4276ca (AGPL-3.0-or-later).
This implementation does not import/copy its controller or task classes.
Local template provenance is in shop_reader_resources/SOURCES.json.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time

import cv2
import numpy as np
from PIL import Image


RESOURCE_DIR = Path(__file__).resolve().with_name("shop_reader_resources")
EXPECTED_SIZE = (1920, 1080)
CENTERS = (490, 760, 1029, 1298, 1567)
SCHEMA = "currency-wars-shop-observation/v1"


def _unknown_slots(reason: str) -> list[dict]:
    return [dict(slot=i + 1, status="unknown", bounds=None, position=None,
                 name=None, cost=None, recommended=None,
                 evidence={}, reasons=[reason]) for i in range(5)]


class ShopReader:
    """One reusable OCR instance; read() only reads its explicit image path."""

    def __init__(self, resources: Path = RESOURCE_DIR):
        self.resources = Path(resources)
        self.engine = None
        self.templates = {}
        self.manifest = None
        self.names = set()

    def _load(self):
        if self.engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # Bound CPU use; use the already installed package's local models.
            manifest = json.loads((self.resources / "SOURCES.json").read_text("utf-8"))
            names = json.loads((self.resources / "names.json").read_text("utf-8"))
            templates = {}
            for item in manifest["resources"]:
                with Image.open(self.resources / item["file"]) as im:
                    templates[item["file"]] = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
            engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
            self.manifest, self.templates, self.names, self.engine = manifest, templates, set(names["names"]), engine

    @staticmethod
    def _rectangles(rgb):
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        overlays = []
        for contour in contours:
            x, y, w, h = map(int, cv2.boundingRect(contour))
            if 345 < x < 1500 and 40 < y < 105 and 205 < w < 275 and 235 < h < 300:
                candidates.append((x, y, w, h))
            if 300 < w < 1350 and 145 < h < 780 and w * h > 60000 and 90 < y < 620:
                polygon = cv2.approxPolyDP(contour, 0.015 * cv2.arcLength(contour, True), True)
                if len(polygon) == 4:
                    patch = rgb[y + 8:y + h - 8, x + 8:x + w - 8]
                    if patch.size:
                        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
                        dark = float(np.mean(hsv[:, :, 2] < 110))
                        if dark > 0.75:
                            overlays.append(dict(bounds=[x, y, w, h], dark_fraction=round(dark, 3)))
        found = {}
        for i, center in enumerate(CENTERS):
            choices = [r for r in candidates if abs(r[0] + r[2] / 2 - center) < 45]
            if choices:
                found[i] = min(choices, key=lambda r: abs(r[2] - 244) + abs(r[3] - 273) * .5
                               + abs(r[0] + r[2] / 2 - center) * .25)
        rects = []
        if len(found) >= 3:
            # Opening animation also changes card spacing; infer missing frames
            # from the measured row rather than translating fixed old centers.
            slope, intercept = np.polyfit(list(found), [r[0] + r[2] / 2 for r in found.values()], 1)
            y = int(np.median([r[1] for r in found.values()]))
            w = int(np.median([r[2] for r in found.values()]))
            h = int(np.median([r[3] for r in found.values()]))
            for i, center in enumerate(CENTERS):
                rects.append(found.get(i, (round(intercept + slope * i - w / 2), y, w, h)))
        else:
            rects = [(c - 122, 59, 244, 273) for c in CENTERS]
        frame_scores = []
        for x, y, w, h in rects:
            sides = []
            for column in (x, x + w - 1):
                band = edges[y + 12:y + h - 20, max(0, column - 6):column + 7]
                sides.append(float(np.mean(np.max(band, axis=1) > 0)) if band.size else 0.)
            frame_scores.append(round(min(sides), 3))
        return rects, dict(detected_rectangles=len(found), frame_scores=frame_scores, overlays=overlays)

    def _ocr(self, atlas):
        result, _ = self.engine(np.array(atlas), use_cls=False)
        return result or []

    def _fields(self, image, rects):
        # One OCR atlas, fixed row per original slot. It never compacts slots.
        atlas = Image.new("RGB", (440, 7 * 96), (22, 22, 22))
        boxes = []
        for i, (x, y, w, h) in enumerate(rects):
            region = (x + 8, y + h - 47, x + w - 51, y + h - 6)
            boxes.append(region)
            atlas.paste(image.crop(region).resize((366, 82)), (8, i * 96 + 5))
        atlas.paste(image.crop((1575, 466, 1658, 512)).resize((166, 82)), (8, 5 * 96 + 5))
        atlas.paste(image.crop((1570, 963, 1670, 1009)).resize((178, 82)), (8, 6 * 96 + 5))
        rows = [[] for _ in range(7)]
        for box, text, confidence in self._ocr(atlas):
            center = float(np.mean([point[1] for point in box]))
            index = int(center // 96)
            if 0 <= index < 7:
                rows[index].append(dict(raw=str(text), confidence=round(float(confidence), 4),
                                        atlas_box=box, left=float(min(p[0] for p in box))))
        for row in rows:
            row.sort(key=lambda item: item["left"])
        return rows, boxes

    @staticmethod
    def _match(search, template):
        if search.shape[0] < template.shape[0] or search.shape[1] < template.shape[1]:
            return 0., None
        score = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, point = cv2.minMaxLoc(score)
        return float(maximum), list(map(int, point))

    def _empty(self, rgb, rect):
        x, y, w, h = rect
        card = cv2.cvtColor(rgb[y + 5:y + h - 5, x + 5:x + w - 5], cv2.COLOR_RGB2GRAY)
        choices = []
        for suffix in ("", "_flash"):
            top, top_point = self._match(card[30:125], self.templates[f"empty_top{suffix}.png"])
            bottom, bottom_point = self._match(card[150:245], self.templates[f"empty_bottom{suffix}.png"])
            choices.append((min(top, bottom), top, bottom, top_point, bottom_point, suffix))
        _, top, bottom, top_point, bottom_point, variant = max(choices)
        dark = float(np.mean(card < 80))
        aligned = top_point is not None and bottom_point is not None and abs(top_point[0] - bottom_point[0]) <= 5
        empty = top >= .87 and bottom >= .87 and dark >= .60 and aligned
        return empty, dict(top_outline_score=round(top, 4), bottom_outline_score=round(bottom, 4),
                           dark_fraction=round(dark, 4), aligned=bool(aligned), variant=variant or "stable")

    def _cost(self, image, rect):
        x, y, w, h = rect
        region = (x + w - 40, y + h - 50, x + w - 2, y + h - 2)
        crop = cv2.cvtColor(np.array(image.crop(region)), cv2.COLOR_RGB2GRAY)
        candidates = {}
        for filename, template in self.templates.items():
            if filename.startswith("cost_"):
                label = int(filename.split("_")[1].split(".")[0])
                resized = cv2.resize(template, (30, 40))
                # Both appearance and edge shape must agree, avoiding color-only matches.
                appearance = cv2.matchTemplate(crop, resized, cv2.TM_CCOEFF_NORMED)
                edge = cv2.matchTemplate(cv2.Canny(crop, 50, 150), cv2.Canny(resized, 50, 150),
                                        cv2.TM_CCOEFF_NORMED)
                score = float(np.max(.5 * appearance + .5 * edge))
                candidates[label] = max(candidates.get(label, -1), score)
        ranked = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
        label, score = ranked[0]
        margin = score - ranked[1][1]
        evidence = dict(bounds=list(region), method="observed_digit_template", template_scores={
                        str(k): round(v, 4) for k, v in candidates.items()}, margin=round(margin, 4),
                        confidence=round(score, 4), raw_ocr=[])
        if score >= .68 and margin >= .12:
            return label, evidence
        # No roster/default fee. Unsupported/uncertain digits remain unknown.
        padded = Image.new("RGB", (100, 65), "white")
        padded.paste(image.crop((x + w - 36, y + h - 46, x + w - 6, y + h - 6)).resize((45, 60)), (27, 2))
        result, _ = self.engine(np.array(padded), use_det=False, use_cls=False)
        for row in result or []:
            text, confidence = row[-2:]
            evidence["raw_ocr"].append(dict(raw=str(text), confidence=round(float(confidence), 4)))
            if re.fullmatch("[1-5]", str(text)) and float(confidence) >= .90:
                evidence.update(method="digit_ocr", confidence=round(float(confidence), 4))
                return int(text), evidence
        return None, evidence

    def _recommended(self, rgb, rect):
        x, y, w, h = rect
        patch = rgb[y + 3:y + 80, x + 3:x + 82]
        gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
        score, point = self._match(gray, self.templates["recommend_badge.png"])
        whiteout = float(np.mean(np.all(patch > 240, axis=2)))
        blackout = float(np.mean(np.max(patch, axis=2) < 60))
        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
        yellow = float(np.mean((hsv[:, :, 0] > 19) & (hsv[:, :, 0] < 40)
                               & (hsv[:, :, 1] > 130) & (hsv[:, :, 2] > 130)))
        evidence = dict(method="yellow_gift_badge_template", bounds=[x + 3, y + 3, 79, 77],
                        match_score=round(score, 4), yellow_fraction=round(yellow, 4),
                        whiteout_fraction=round(whiteout, 4), blackout_fraction=round(blackout, 4), match_offset=point)
        if score >= .72 and yellow >= .06:
            return True, evidence
        if score < .50 and yellow < .04 and whiteout < .65 and blackout < .90:
            return False, evidence
        return None, evidence

    def read(self, image_path: str | Path) -> dict:
        started = time.perf_counter()
        path = Path(image_path)
        output = dict(schema=SCHEMA, ok=False, status="error", input=dict(path=str(path)),
                      page=dict(reliable_open_shop=False, reasons=[]),
                      slots=_unknown_slots("input_not_validated"), errors=[])
        try:
            if not path.is_absolute():
                raise ValueError("input_path_must_be_absolute")
            if not path.is_file():
                raise FileNotFoundError("input_image_missing")
            if path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError("input_image_exceeds_25_mib")
            data = path.read_bytes()
            output["input"].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            with Image.open(io.BytesIO(data)) as opened:
                output["input"].update(size=list(opened.size), format=opened.format)
                if opened.format not in ("PNG", "JPEG"):
                    raise ValueError("unsupported_image_format")
                if opened.size != EXPECTED_SIZE:
                    raise ValueError("expected_1920x1080_without_resize")
                image = opened.convert("RGB")
            self._load()
            rgb = np.array(image)
            rects, geometry = self._rectangles(rgb)
            rows, name_boxes = self._fields(image, rects)
            controls = ["".join(item["raw"] for item in row) for row in rows[5:]]
            reasons = []
            if "刷新" not in controls[0] or "收起" not in controls[1]:
                reasons.append("expanded_shop_controls_not_visible")
            # A missing card interior/OCR contour must not erase the original
            # slot. Confirm each frame plus both controls independently.
            if min(geometry["frame_scores"]) < .60:
                reasons.append("five_card_frames_not_confirmed")
            if geometry["overlays"]:
                reasons.append("large_modal_or_overlay_detected")
            output["page"] = dict(reliable_open_shop=not reasons, reasons=reasons,
                                  controls_raw=controls, geometry=geometry)
            if reasons:
                output.update(status="rejected", slots=_unknown_slots("page_not_reliable_open_shop"))
                return output
            slots = []
            for i, rect in enumerate(rects):
                x, y, w, h = rect
                empty, empty_evidence = self._empty(rgb, rect)
                slot = dict(slot=i + 1, bounds=list(rect), position=[round(x + w / 2), round(y + h / 2)],
                            status="unknown", name=None, cost=None, recommended=None,
                            evidence=dict(empty=empty_evidence), reasons=[])
                if empty:
                    slot.update(status="empty", recommended=False)
                else:
                    fragments = [item for item in rows[i] if item["confidence"] >= .70]
                    raw = "".join(item["raw"] for item in fragments).strip()
                    confidence = min((item["confidence"] for item in fragments), default=0.)
                    name = raw if re.fullmatch(r"[\u3400-\u9fffA-Za-z·.0-9]{2,16}", raw) else None
                    name_evidence = dict(raw=raw, confidence=confidence, fragments=rows[i],
                                         bounds=list(name_boxes[i]), method="ocr")
                    alias = self.manifest.get("ocr_name_aliases", {}).get(raw)
                    if alias and name:
                        name = alias["name"]
                        name_evidence.update(method="explicit_observed_ocr_alias", alias_basis=alias["basis"])
                    if name:
                        canonical = {known.casefold(): known for known in self.names}.get(name.casefold())
                        if canonical and canonical != name:
                            name = canonical
                            name_evidence["method"] = "ocr_case_normalization"
                    if name not in self.names:
                        name = None
                        name_evidence["validation"] = "not_in_fixed_source_name_list"
                    cost, cost_evidence = self._cost(image, rect)
                    recommended, recommended_evidence = self._recommended(rgb, rect)
                    slot.update(name=name, cost=cost, recommended=recommended)
                    slot["evidence"].update(name=name_evidence, cost=cost_evidence,
                                            recommended=recommended_evidence)
                    for field, value in (("name", name), ("cost", cost), ("recommended", recommended)):
                        if value is None:
                            slot["reasons"].append(field + "_unknown")
                    slot["status"] = "recognized" if not slot["reasons"] else "unknown"
                slots.append(slot)
            output["slots"] = slots
            complete = all(slot["status"] in ("recognized", "empty") for slot in slots)
            output.update(ok=complete, status="ok" if complete else "partial")
            return output
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            output.update(status="error", errors=[reason], slots=_unknown_slots(reason))
            return output
        finally:
            output["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, action="append", required=True,
                        help="Absolute saved PNG/JPG; repeat at most 20 times to reuse OCR")
    args = parser.parse_args()
    if len(args.image) > 20:
        parser.error("at most 20 input images per bounded invocation")
    reader = ShopReader()
    results = [reader.read(path) for path in args.image]
    print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False))
    return 0 if all(result["ok"] for result in results) else 2


if __name__ == "__main__":
    sys.exit(main())
