"""Local, read-only Currency Wars OCR. Unknown fields stay unknown."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import numpy as np
from PIL import Image


OBSERVED_TEXT_ALIASES = {
    '标准博奔': ('标准博弈', 'human-read d174-after-return-original.jpg; same exact OCR font error'),
    '超频博奔适用': ('超频博弈适用', 'human-read d007-after-guide-detail.jpg; exact label font error'),
    '李生素数': ('孪生素数', 'human-read d061-after-three-strategies.jpg by root; exact title font error')
}

# Only these actual 1920x1080 layouts were read from retained game evidence.
# Callers cannot supply a smaller replacement rectangle or invent a layout.
OPTION_LAYOUTS = {
    'investment': ((257, 196, 665, 816), (757, 196, 1165, 816), (1257, 196, 1665, 816)),
    'supply': ((262, 292, 596, 784), (616, 292, 950, 784),
               (970, 292, 1304, 784), (1324, 292, 1658, 784)),
}
GOLD_HUD = [1613, 892, 1687, 950]


def clean(text):
    return re.sub(r"\s+", "", str(text))


def classify(rows):
    words = [clean(item["text"]) for item in rows if item["confidence"] >= .72]
    joined = "|".join(words)
    exact = set(words)
    if any(word in joined for word in ("获得物品", "获得奖励", "领取成功")):
        return "reward_overlay"
    # These are actual whole-match screens read from d173/d174. A boss rating
    # or a bare SSS/mode label alone is insufficient.
    if all(word in joined for word in ('对局评价', '当前职级', '职级晋升', '下一步')):
        return "settlement_grade"
    if (any(mode in joined for mode in ('标准博弈', '超频博弈'))
            and all(word in joined for word in ('奖励信息', '晋升等级', '晋升点', '我的阵容'))
            and any(word in joined for word in ('对局胜利', '对局失败', '对局结束'))):
        return "settlement"
    if "朝露公馆" in joined and "货币战争" in joined:
        return "world_entry"
    if sum(name in joined for name in ("创业指南", "优势布局", "羁绊链路", "预期收益")) >= 3:
        return "lobby"
    if ('投资环境' in exact and '投资策略' in exact
            and not any(w in joined for w in ('请选择', '确认'))):
        return 'investment_summary'
    if ('攻略推荐' in exact and '装备推荐' in exact and '取消' in exact
            and '装备追踪中' in joined):
        return 'unit_gear'
    if "收起" in exact and "刷新" in joined:
        return "shop"
    if "备战" in joined and "出战" in joined:
        return "preparation"
    if "战斗中" in joined and "出战" not in joined:
        return "battle"
    if "竞争对手" in joined and ("下一步" in joined or "生成" in joined):
        return "opponents"
    if "投资环境" in joined and ("选择" in joined or "确认" in joined):
        return "environment"
    if "投资策略" in joined and ("确认" in joined or "选择" in joined):
        return "investment"
    if "补给" in joined and ("确认" in joined or "0/1" in joined):
        return "supply"
    if "预期收益" in joined and any(w in joined for w in ("常规挑战", "限时", "任务", "领取")):
        return "income"
    if "羁绊链路" in joined and any(w in joined for w in ("阵营", "流派", "领取")):
        return "bonds"
    if "晋升等级" in joined and any(w in joined for w in ("奖励", "晋升点", "领取")):
        return "promotion"
    if "优势布局" in joined and any(w in joined for w in ("等价钻钞", "强化", "升级", "重置")):
        return "advantages"
    if "攻略" in joined and any(w in joined for w in ("应用", "运营", "推荐")):
        return "guide"
    return "unknown"


def find_text(rows, target, bounds=None, exact=True):
    wanted = clean(target)
    found = []
    for item in rows:
        text = clean(item["text"])
        box = item["box"]
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if item["confidence"] < .78:
            continue
        if not (text == wanted if exact else wanted in text):
            continue
        if bounds and not (bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]):
            continue
        found.append(item)
    return found[0] if len(found) == 1 else None


def rows_in(rows, bounds):
    return sorted((r for r in rows if r['confidence'] >= .78
        and bounds[0] <= (r['box'][0]+r['box'][2])/2 < bounds[2]
        and bounds[1] <= (r['box'][1]+r['box'][3])/2 < bounds[3]),
        key=lambda r: (r['box'][1], r['box'][0]))


def option_facts(rows, page):
    """Trusted complete cards, with title and all visible effect/reward rows."""
    cards = []
    if page == 'investment' and not find_text(rows, '请选择投资策略', [800, 60, 1130, 145]):
        return cards
    if page == 'supply' and not find_text(rows, '补给阶段', [800, 120, 1120, 190]):
        return cards
    for index, box in enumerate(OPTION_LAYOUTS.get(page, ())):
        if page == 'investment':
            titles = rows_in(rows, [box[0]+20, 460, box[2]-20, 515])
            effects = rows_in(rows, [box[0]+20, 515, box[2]-20, 790])
        else:
            titles = rows_in(rows, [box[0]+28, 540, box[2]-28, 600])
            effects = rows_in(rows, [box[0]+90, 645, box[2]-20, 750])
        if len(titles) != 1 or not effects:
            return []
        cards.append({'card_index': index+1, 'bounds': list(box), 'title': titles[0]['text'],
                      'effect_lines': [r['text'] for r in effects]})
    return cards


def selected_summary(rows, image):
    """Read the selected-policy popup, never the three-card offer screen."""
    env = find_text(rows, '投资环境', [650, 145, 1280, 880])
    policy = find_text(rows, '投资策略', [650, 145, 1280, 880])
    if not env or not policy or policy['box'][1] <= env['box'][3]:
        return None
    # The dark popup bounds come from the image, not a caller-provided ROI.
    import cv2
    hsv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2HSV)
    mask = np.zeros((1080, 1920), np.uint8)
    mask[140:1020, 500:1420] = (hsv[140:1020, 500:1420, 2] < 120).astype(np.uint8)
    count, unused_labels, stats, unused_centers = cv2.connectedComponentsWithStats(mask)
    matches = []
    for x, y, w, h, area in stats[1:]:
        if (400 <= w <= 800 and 250 <= h <= 850 and area > w*h*.50
                and x < env['box'][0] and x+w > env['box'][2]
                and y < env['box'][1] and y+h > policy['box'][3]):
            matches.append([int(x), int(y), int(x+w), int(y+h)])
    if len(matches) != 1:
        return None
    box = matches[0]
    env_rows = rows_in(rows, [box[0]+115, env['box'][3], box[2]-8, policy['box'][1]])
    body = rows_in(rows, [box[0]+115, policy['box'][3], box[2]-8, box[3]-8])
    if len(env_rows) < 2 or not body:
        return None
    # A name starts each visually separated paragraph; all following lines are
    # retained verbatim. Unknown spacing/content rejects the entire inventory.
    groups = []
    for row in body:
        if not groups or row['box'][1] - groups[-1][-1]['box'][3] >= 15:
            groups.append([row])
        else:
            groups[-1].append(row)
    if any(len(g) < 2 or len(clean(g[0]['text'])) > 32 for g in groups):
        return None
    items = [{'name': g[0]['text'], 'effect': ''.join(r['text'] for r in g[1:]),
              'effect_lines': [r['text'] for r in g[1:]]} for g in groups]
    return {'investments': items, 'environment': {'name': env_rows[0]['text'],
            'effect': ''.join(r['text'] for r in env_rows[1:])}, 'bounds': box}


def semantic_facts(rows, image, page):
    """Factual fields are produced locally, never from a strategy assertion."""
    facts = {'options': option_facts(rows, page)}
    if page == 'guide' and find_text(rows, '攻略详情', [35, 25, 300, 105]):
        title_rows = rows_in(rows, [40, 109, 780, 155])
        body = rows_in(rows, [50, 810, 1870, 942])
        applied = find_text(rows, '取消应用', [1610, 958, 1890, 1025])
        if title_rows and len(body) >= 2 and applied and find_text(rows, '运营思路', [850, 770, 1070, 815]):
            title = title_rows[0]['text']
            for row in title_rows[1:]:
                part = row['text']
                if title.endswith('）') and part.startswith('）'):
                    part = part[1:]
                title += part
            modes = rows_in(rows, [790, 109, 1210, 157])
            facts['guide'] = {'title': title, 'applied': True, 'body_read': True,
                'body_lines': [r['text'] for r in body],
                'mode_label': modes[0]['text'] if len(modes) == 1 else None,
                'application_state': '取消应用'}
    if page == 'unit_gear':
        names = rows_in(rows, [1490, 205, 1775, 254])
        if (len(names) == 1 and find_text(rows, '攻略推荐', [970, 550, 1200, 620])
                and find_text(rows, '取消', [1260, 550, 1375, 620])
                and find_text(rows, '装备推荐', [1393, 770, 1605, 857])):
            facts['guide_tracking'] = {'enabled': True, 'units': [names[0]['text']],
                                       'tracking_state': '攻略推荐/取消'}
    if page == 'investment_summary':
        summary = selected_summary(rows, image)
        if summary:
            facts.update(summary)
    if page in ('preparation', 'shop', 'investment_summary'):
        button = (find_text(rows, '收起', [1554, 943, 1694, 1020])
                  or find_text(rows, '商店', [1554, 943, 1694, 1020]))
        xp = find_text(rows, '购买经验', [220, 815, 375, 889])
        numbers = rows_in(rows, GOLD_HUD)
        # Location, two native HUD labels and the gold icon all must agree.
        import cv2
        icon = cv2.cvtColor(np.array(image.crop((1572, 891, 1626, 949))), cv2.COLOR_RGB2HSV)
        gold = float(np.mean((icon[:, :, 0] >= 15) & (icon[:, :, 0] <= 40)
                             & (icon[:, :, 1] > 90) & (icon[:, :, 2] > 160)))
        if (button and xp and len(numbers) == 1 and clean(numbers[0]['text']).isdigit()
                and numbers[0]['confidence'] >= .90 and gold >= .15):
            facts['coins'] = {'value': int(clean(numbers[0]['text'])), 'bounds': GOLD_HUD,
                              'currency_icon_gold_fraction': round(gold, 3)}
    return facts


def fingerprint(image):
    # Screen-change check, not a scene classifier. It is never sufficient alone.
    array = np.array(image.convert("L").resize((65, 36)))
    return np.packbits(array[:, 1:] > array[:, :-1]).tobytes().hex()


def hash_distance(a, b):
    left, right = bytes.fromhex(a), bytes.fromhex(b)
    if len(left) != len(right):
        return 1.
    return sum((x ^ y).bit_count() for x, y in zip(left, right)) / (len(left) * 8)


class Perception:
    def __init__(self):
        self.engine = None
        self.shop_reader = None
        self.cache = None

    def read(self, path, force=False):
        started = time.perf_counter()
        path = Path(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if self.cache and self.cache[0] == digest and not force:
            return self.cache[1]
        with Image.open(path) as opened:
            if opened.size != (1920, 1080):
                raise ValueError("1920x1080 broker preview required")
            image = opened.convert("RGB")
        if self.engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        raw, unused = self.engine(np.array(image.resize((1280, 720))), use_cls=False)
        rows = []
        for box, text, confidence in raw or []:
            xs, ys = [p[0] * 1.5 for p in box], [p[1] * 1.5 for p in box]
            alias = OBSERVED_TEXT_ALIASES.get(text)
            rows.append({"text": alias[0] if alias else text, "raw_text": text,
                         "normalization_basis": alias[1] if alias else None,
                         "confidence": round(float(confidence), 4),
                         "box": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]})
        page = classify(rows)
        shop = None
        if page == "shop":
            if self.shop_reader is None:
                from currency_wars_shop_reader import ShopReader
                self.shop_reader = ShopReader()
            shop = self.shop_reader.read(path.absolute())
        # Labels/positions have evidence. Unlabelled bare counters are not
        # guessed into HP/gold/promotion fields.
        fields = {}
        joined = "|".join(clean(r["text"]) for r in rows)
        for name, pattern in (("stage", r"(?:备战|战斗中).*?(\d[-－]\d)"),
                              ("level", r"(?:Lv\.?|等级)(\d{1,2})"),
                              ("deployed", r"(\d{1,2}/\d{1,2})")):
            matched = re.search(pattern, joined, re.I)
            fields[name] = matched.group(1) if matched else None
        result = {"snapshot_id": digest, "page": page, "rows": rows, "fields": fields,
                  "fingerprint": fingerprint(image), "shop": shop,
                  "semantic": semantic_facts(rows, image, page),
                  "image": str(path), "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        self.cache = digest, result
        return result
