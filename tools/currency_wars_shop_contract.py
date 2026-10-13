"""Pure validation of already-read native shop facts; no visual runtime or input APIs."""
from pathlib import Path
import json

RESOURCE_DIR = Path(__file__).resolve().with_name("shop_reader_resources")
EXPECTED_SIZE = (1920, 1080)
SCHEMA = "currency-wars-shop-observation/v1"


def _priced_slot(observation: dict, slot_id: int, snapshot_id: str, *, resources=RESOURCE_DIR, supervised=False, allow_empty=False) -> dict | None:
    """One positive, priced card; incomplete other cards/badges stay unknown."""
    if (type(slot_id) is not int or not 1 <= slot_id <= 5
            or observation.get("schema") != SCHEMA
            or observation.get("input", {}).get("sha256") != snapshot_id
            or observation.get("input", {}).get("size") != list(EXPECTED_SIZE)
            or observation.get("input", {}).get("format") != "PNG"
            or observation.get("page", {}).get("reliable_open_shop") is not True):
        return None
    geometry = observation.get("page", {}).get("geometry", {})
    scores = geometry.get("frame_scores", [])
    if (len(scores) != 5 or any(type(v) not in (int, float) or not .60 <= v <= 1. for v in scores)
            or geometry.get("overlays") != []):
        return None
    matches = [slot for slot in observation.get("slots", []) if slot.get("slot") == slot_id]
    if len(matches) != 1:
        return None
    slot = matches[0]
    bounds, point = slot.get("bounds"), slot.get("position")
    if (not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) is not int for v in bounds)
            or not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point)):
        return None
    x, y, width, height = bounds
    if (not 0 <= x < x + width <= 1920 or not 0 <= y < y + height <= 1080
            or point != [round(x + width / 2), round(y + height / 2)]
):
        return None
    if allow_empty and slot.get('status') == 'empty':
        empty = slot.get('evidence', {}).get('empty', {})
        if (slot.get('name') is None and slot.get('cost') is None and empty.get('aligned') is True
                and all(type(empty.get(k)) in (int, float) and .87 <= empty[k] <= 1.
                        for k in ('top_outline_score', 'bottom_outline_score'))
                and type(empty.get('dark_fraction')) in (int, float) and .60 <= empty['dark_fraction'] <= 1.):
            return slot
        return None
    if (slot.get("status") == "empty" or type(slot.get("cost")) is not int
            or not 1 <= slot["cost"] <= 5 or not isinstance(slot.get("name"), str)):
        return None
    try:
        names = json.loads((Path(resources) / "names.json").read_text(encoding="utf-8-sig"))["names"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if slot["name"] not in names:
        return None
    evidence = slot.get("evidence", {})
    name, cost = evidence.get("name", {}), evidence.get("cost", {})
    if (name.get("validation") == "not_in_fixed_source_name_list"
            or name.get("method") not in ("ocr", "ocr_case_normalization", "explicit_observed_ocr_alias")
            or type(name.get("confidence")) not in (int, float) or not 0. <= name["confidence"] <= 1.
            or name["confidence"] < .90 and not supervised):
        return None
    digit = cost.get("method") == "digit_ocr"
    template = (cost.get("method") == "observed_digit_template"
                and cost.get("template_classification_used") is True
                and set(cost.get("template_scores", {})) == {"1", "2", "3", "4", "5"}
                and type(cost.get("confidence")) in (int, float) and .68 <= cost["confidence"] <= 1.
                and type(cost.get("margin")) in (int, float) and cost["margin"] >= .12)
    if not template and not (digit and type(cost.get("confidence")) in (int, float) and .90 <= cost["confidence"] <= 1.):
        return None
    for box in (name.get("bounds"), cost.get("ocr_bounds") if digit else cost.get("bounds")):
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                or not x <= box[0] < box[2] <= x + width
                or not y <= box[1] < box[3] <= y + height):
            return None
    # A complete high-confidence name plus its actually read fee is positive
    # occupied-card evidence, even when the empty/badge templates are missing.
    if digit and not any(row.get("raw") == str(slot["cost"])
               and type(row.get("confidence")) in (int, float) and .90 <= row["confidence"] <= 1.
               for row in cost.get("raw_ocr", [])):
        return None
    return slot



def purchase_slot(observation: dict, slot_id: int, snapshot_id: str, *, resources=RESOURCE_DIR) -> dict | None:
    """Original native purchase threshold; supervisory reads never grant buying."""
    return _priced_slot(observation, slot_id, snapshot_id, resources=resources)


def priced_read_slot(observation: dict, slot_id: int, snapshot_id: str, *, review=None, resources=RESOURCE_DIR) -> dict | None:
    """Read a native price/name, with explicitly bound ROOT name confirmation if needed."""
    if observation.get('status') not in ('ok', 'partial') or observation.get('errors'):
        return None
    supervised = False
    if isinstance(review, dict):
        slots, offers = observation.get('slots', []), review.get('offers', [])
        if (review.get('source') != 'supervising_agent' or review.get('snapshot_id') != snapshot_id
                or len(slots) != 5 or len(offers) != 5
                or any(not isinstance(row, dict) or type(row.get('slot')) is not int for row in slots + offers)
                or {row['slot'] for row in slots} != set(range(1, 6))
                or {row['slot'] for row in offers} != set(range(1, 6))):
            return None
        for slot in slots:
            offer = next(row for row in offers if row['slot'] == slot['slot'])
            if any(offer.get(key) != slot.get(key) for key in ('name', 'cost', 'recommended', 'bounds', 'position')):
                return None
        supervised = True
    slot = _priced_slot(observation, slot_id, snapshot_id, resources=resources, supervised=supervised, allow_empty=True)
    if slot is None:
        return None
    return {**slot, 'read_origin': 'supervising_agent' if supervised else 'current_native_shop',
            'purchase_eligible': (purchase_slot(observation, slot_id, snapshot_id, resources=resources) is not None
                                  and type(slot.get('recommended')) is bool)}
