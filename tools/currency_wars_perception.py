"""Local, read-only Currency Wars OCR. Unknown fields stay unknown."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image


OBSERVED_TEXT_ALIASES = {
    '进入标准博奔': ('进入标准博弈', 'human-read standard-entry-animation-fresh-5561.png SHA256 6e4a772f8e9518869d60016b37f1a99ba51c42eef5b2e2734572df63e93b5733; exact entry label font error'),
    '标准博奔': ('标准博弈', 'human-read d174-after-return-original.jpg; same exact OCR font error'),
    '超频博奔适用': ('超频博弈适用', 'human-read d007-after-guide-detail.jpg; exact label font error'),
    '超频博奔': ('超频博弈', 'human-read debug/4k-coaching-current.png 2026-10-06 SSS settlement; exact mode label font error'),
    '超频博奔|A5紫金3': ('超频博弈|A5紫金3', 'human-read same live SSS settlement; OCR merged mode and tier row'),
    '李生素数': ('孪生素数', 'human-read d061-after-three-strategies.jpg by root; exact title font error')
}

# Only these actual 1920x1080 layouts were read from retained game evidence.
# Callers cannot supply a smaller replacement rectangle or invent a layout.
OPTION_LAYOUTS = {
    'environment': ((207, 198, 671, 868), (727, 198, 1193, 868), (1253, 198, 1717, 868)),
    'investment': ((257, 196, 665, 816), (757, 196, 1165, 816), (1257, 196, 1665, 816)),
    'supply': ((262, 292, 596, 784), (616, 292, 950, 784),
               (970, 292, 1304, 784), (1324, 292, 1658, 784)),
}
# Original environment reader layout; its confirm label is right of center.
# Investment and supply retain their own independent confirmation layouts.
ENVIRONMENT_CONFIRM_BOUNDS = (770, 950, 1160, 1025)
SUPPLY_FIVE_CARD_LAYOUT = ((84, 292, 419, 783), (439, 292, 774, 783),
                           (793, 292, 1129, 783), (1147, 292, 1482, 783),
                           (1501, 292, 1836, 783))
GOLD_HUD = [1613, 892, 1687, 950]
DEPLOYED_COUNT_ROI = [890, 210, 1029, 280]
# Complete native player Lv label and number, including the taller numeral.
# This is the lower-left purchase-experience panel, never a card/boss title.
PLAYER_LEVEL_ROI = [240, 880, 358, 936]
# Current parsing support, not a claim about the game's permanent maximum.
MAX_SUPPORTED_POPULATION = 12
READ_CONTRACT_VERSION = 2
READ_SCOPES = ('full', 'rewards', 'economy')
# Raw full-frame OCR only: 1920x1080 RGB, Pillow RGB default resize to
# 1280x720, RapidOCR with use_cls=False. Bump when that pipeline changes.
# The same engine object binds its loaded models; runtime defaults and the
# thresholds RapidOCR can mutate are also part of the per-instance key.
OCR_CONTRACT_VERSION = 1
PERCEPTION_TIMING_SCHEMA = 'currency-wars-perception-timing/1'


class _ReadTiming:
    """Invocation timing only; failures never change the actual read result.

    Keep the real engine object in Perception and its cache keys. The local
    callable below measures only calls made in this read, including ROI calls
    delegated to semantic readers. No OCR output, image or owner is recorded.
    """
    def __init__(self, snapshot_id):
        self.read_id, self.snapshot_id = None, snapshot_id
        self.intervals, self.error = [], None
        try:
            self.read_id = uuid.uuid4().hex
        except Exception:
            self.error = 'read_identity_unavailable'
        self.start_ns = self.clock()

    def clock(self):
        try:
            value = time.monotonic_ns()
            if type(value) is not int or value < 0:
                raise ValueError('invalid monotonic clock')
            return value
        except Exception:
            self.error = 'monotonic_clock_unavailable'
            return None

    def engine(self, original):
        def measured(*args, **kwargs):
            start, outcome = self.clock(), 'returned'
            try:
                return original(*args, **kwargs)
            except BaseException:
                outcome = 'raised'
                raise
            finally:
                try:
                    end = self.clock()
                    if start is not None and end is not None and start <= end and len(self.intervals) < 64:
                        self.intervals.append({'start_ns': start, 'end_ns': end, 'outcome': outcome})
                    else:
                        self.error = self.error or 'ocr_interval_unavailable'
                except Exception:
                    self.error = 'ocr_timing_unavailable'
        return measured

    def finish(self):
        return {'schema': PERCEPTION_TIMING_SCHEMA, 'read_id': self.read_id,
                'snapshot_id': self.snapshot_id, 'clock': 'same_process_monotonic',
                'ocr_scope': 'perception_engine_calls',
                'start_ns': self.start_ns, 'end_ns': self.clock(),
                'ocr_intervals': self.intervals, 'error': self.error}


def clean(text):
    return re.sub(r"\s+", "", str(text))


def _native_promotion_page(rows):
    """The retained native page has two labels and central progress."""
    def unique_field(bounds, pattern):
        matches = [row for row in rows if row['confidence'] >= .90
                   and re.fullmatch(pattern, clean(row['text']))
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        return len(matches) == 1

    return (unique_field((80, 20, 260, 85), '晋升等级')
            and unique_field((820, 380, 1100, 445), '晋升等级')
            and unique_field((840, 430, 1080, 490), r'[0-9]+/[0-9]+'))


def _native_advantages_page(rows):
    """Native headers and both tabs, independent of the selected skill body."""
    for label, bounds in (
            ('货币战争', (80, 20, 240, 80)),
            ('优势布局', (80, 55, 250, 110)),
            ('常驻优势', (600, 90, 960, 160)),
            ('赛季优势', (965, 90, 1340, 160))):
        matches = [row for row in rows if row['confidence'] >= .90
                   and clean(row['text']) == label
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        if len(matches) != 1:
            return False
    return True


def _native_guide_detail_page(rows):
    """Fixed native header and application button, independent of scrolling."""
    for labels, bounds in (
            (('货币战争',), (80, 20, 240, 80)),
            (('攻略详情',), (80, 55, 250, 110)),
            (('应用攻略', '取消应用'), (1610, 950, 1890, 1025))):
        matches = [row for row in rows if row['confidence'] >= .90
                   and clean(row['text']) in labels
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        if len(matches) != 1:
            return False
    return True


NODE_RESULT_STAGE_CROP = [880, 268, 938, 306]


def _native_node_result_labels(rows):
    """Five complete native result labels, without asserting victory."""
    for pattern, bounds in (
            (r'挑战(?:成功|结束)', (800, 180, 1110, 280)),
            (r'小队生命值(?:100|[1-9]?[0-9])', (640, 470, 900, 535)),
            ('获得金币总览', (500, 535, 710, 595)),
            ('数据统计', (1095, 530, 1245, 600)),
            ('继续挑战', (880, 850, 1045, 935))):
        matches = [re.fullmatch(pattern, clean(row['text'])) for row in rows
                   if .90 <= row['confidence'] <= 1.
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        matches = [match for match in matches if match is not None]
        if len(matches) != 1:
            return False
    return True


def _native_node_result_stages(rows):
    stages, crossed_stages = [], []
    for row in rows:
        box = row['box']
        crossed_swords = re.fullmatch(r'([1-3]-[1-9])X战斗', clean(row['text']))
        minimum = .85 if crossed_swords is not None else .90
        if not (minimum <= row['confidence'] <= 1.
                and 840 <= box[0] < box[2] <= 1080 and 250 <= box[1] < box[3] <= 320):
            continue
        pattern = r'([1-3]-[1-9])(?:战斗|Y?遭遇|奖励)'
        if (row.get('normalization_basis') == 'fixed_native_node_result_stage_roi'
                and box == NODE_RESULT_STAGE_CROP):
            pattern = r'([1-3]-[1-9])'
        match = crossed_swords or re.fullmatch(pattern, clean(row['text']))
        if match is not None:
            (crossed_stages if crossed_swords is not None else stages).append(match.group(1))
    # A streak badge moves the complete battle header left of the normal ROI.
    # This branch requires the adjacent complete badge; normal/X/crop conflicts
    # and duplicates still pass through the original collection below.
    streaks = [row for row in rows if .90 <= row['confidence'] <= 1.
               and re.fullmatch(r'火热连胜×(?:0|[1-9][0-9]?)', clean(row['text']))
               and 955 <= row['box'][0] < row['box'][2] <= 1145
               and 260 <= row['box'][1] < row['box'][3] <= 315]
    if len(streaks) == 1:
        badge = streaks[0]['box']
        for row in rows:
            box = row['box']
            text = clean(row['text'])
            match = re.fullmatch(r'([1-3]-[1-9])(?:X?战斗|Y?遭遇|奖励)', text)
            minimum = .85 if re.fullmatch(r'[1-3]-[1-9]X战斗', text) else .90
            if (match is not None and minimum <= row['confidence'] <= 1.
                    and 760 <= box[0] < 840 and box[0] < box[2] <= 965
                    and 260 <= box[1] < box[3] <= 315
                    and -4 <= badge[0] - box[2] <= 20
                    and abs(badge[1] - box[1]) <= 8 and abs(badge[3] - box[3]) <= 8):
                stages.append(match.group(1))
    # A retained real crop and one agreeing X header describe the same field.
    # Conflicts or duplicate normal/X candidates retain ambiguity.
    if len(stages) == 1 and crossed_stages in ([], stages):
        return stages
    return stages + crossed_stages


def _native_node_result_stage(rows):
    """Six unique high-confidence native anchors; a node result is not a match result."""
    stages = _native_node_result_stages(rows)
    return stages[0] if _native_node_result_labels(rows) and len(stages) == 1 else None


def _native_boss_result_stage(rows):
    """Retained final-boss progress page, before the real match settlement."""
    for pattern, bounds in (
            ('挑战成功', (800, 180, 1110, 280)),
            (r'3-7首领', (760, 250, 970, 320)),
            (r'[√✓]?剩余[1-9][0-9]?轮击败首领', (780, 320, 1045, 375)),
            ('挑战进度', (860, 360, 1070, 415)),
            ('前往结算', (860, 850, 1100, 935))):
        matches = [row for row in rows if .90 <= row['confidence'] <= 1.
                   and re.fullmatch(pattern, clean(row['text']))
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        if len(matches) != 1:
            return None
    return '3-7'


def _native_battle_stage(rows):
    """Four fixed native battle HUD anchors; floating damage numbers do not qualify."""
    matched_fields = []
    for pattern, bounds in (
            (r'([1-3]-[1-9])', (680, 0, 765, 55)),
            (r'(?:100|[1-9]?[0-9])%', (760, 0, 820, 55)),
            (r'(?:100|[1-9]?[0-9])', (990, 0, 1070, 55)),
            ('总伤害', (1780, 75, 1900, 125))):
        matches = [re.fullmatch(pattern, clean(row['text'])) for row in rows
                   if .90 <= row['confidence'] <= 1.
                   and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                   and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        matches = [match for match in matches if match is not None]
        if len(matches) != 1:
            return None
        matched_fields.append(matches[0])
    return matched_fields[0].group(1)


def classify(rows):
    words = [clean(item["text"]) for item in rows if item["confidence"] >= .72]
    joined = "|".join(words)
    exact = set(words)
    # The retained season notice mentions several panels in its body. Its
    # native header and two modal labels must win over those paragraph words.
    if (any('货币战争' in clean(row['text']) and '赛季扩充说明' in clean(row['text'])
            for row in rows_in(rows, [420, 240, 1190, 310]))
            and find_text(rows, '详情', [1200, 240, 1420, 310])
            and find_text(rows, '扩充内容概览', [420, 710, 900, 810])):
        return 'update_notice'
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
    if _native_node_result_stage(rows):
        return "node_result"
    if _native_boss_result_stage(rows):
        return 'boss_result'
    # A selected advantage can describe "战斗中". Its four native page
    # anchors identify the current surface; body copy does not identify play.
    # Keep actual modals/results above this page-level recognition.
    if _native_advantages_page(rows):
        return "advantages"
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
    # Battle needs the existing native HUD, never a keyword in skill text.
    if _native_battle_stage(rows):
        return "battle"
    if "竞争对手" in joined and ("下一步" in joined or "生成" in joined):
        return "opponents"
    if _native_guide_detail_page(rows):
        return "guide"
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
    if _native_promotion_page(rows):
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
    if page == 'supply':
        titles = [rows_in(rows, [box[0]+28, 540, box[2]-28, 600])
                  for box in SUPPLY_FIVE_CARD_LAYOUT]
        if all(len(found) == 1 for found in titles):
            for label, bounds in (('补给阶段', (800, 120, 1120, 190)),
                                  ('确认', (1580, 950, 1810, 1025))):
                found = [row for row in rows if .90 <= row['confidence'] <= 1.
                         and clean(row['text']) == label
                         and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                         and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
                if len(found) != 1:
                    return []
            for index, (box, title) in enumerate(zip(SUPPLY_FIVE_CARD_LAYOUT, titles), 1):
                trial = rows_in(rows, [box[0]+28, 320, box[2]-20, 415])
                tags = rows_in(rows, [box[0]+28, 420, box[2]-20, 546])
                gear = rows_in(rows, [box[0]+90, 645, box[2]-20, 750])
                effects = trial + tags + gear
                if (title[0]['confidence'] < .90 or not tags or len(gear) != 1 or len(trial) > 1
                        or any(clean(row['text']) != '试用' for row in trial)
                        or any(not .90 <= row['confidence'] <= 1. for row in effects)
                        or any(not (box[0] <= row['box'][0] < row['box'][2] <= box[2]
                                    and box[1] <= row['box'][1] < row['box'][3] <= box[3])
                               for row in [*title, *effects])):
                    return []
                cards.append({'card_index': index, 'bounds': list(box), 'title': title[0]['text'],
                              'effect_lines': [row['text'] for row in effects]})
            return cards
    if page == 'environment':
        for label, bounds in (('投资环境', (850, 55, 1070, 135)),
                              ('确认', ENVIRONMENT_CONFIRM_BOUNDS)):
            matches = [row for row in rows if row['confidence'] >= .90
                       and clean(row['text']) == label
                       and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                       and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
            if len(matches) != 1:
                return []
        for index, box in enumerate(OPTION_LAYOUTS[page]):
            titles = rows_in(rows, [box[0]+20, 360, box[2]-20, 414])
            effects = rows_in(rows, [box[0]+20, 414, box[2]-20, 690])
            if len(titles) != 1 or not effects:
                return []
            cards.append({'card_index': index+1, 'bounds': list(box), 'title': titles[0]['text'],
                          'effect_lines': [r['text'] for r in effects]})
        return cards
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


def supply_read_details(rows, page, options):
    """Keep per-card evidence when one uncertain field blocks the whole choice."""
    if page != 'supply':
        return None
    header = [row for row in rows if row.get('confidence', 0) >= .90
        and clean(row.get('text', '')) == '补给阶段'
        and 800 <= row['box'][0] < row['box'][2] <= 1120
        and 120 <= row['box'][1] < row['box'][3] <= 190]
    titles = [rows_in(rows, [box[0]+28, 540, box[2]-28, 600]) for box in SUPPLY_FIVE_CARD_LAYOUT]
    if len(header) != 1 or not all(len(found) == 1 for found in titles):
        return None
    cards = []
    for index, (box, title) in enumerate(zip(SUPPLY_FIVE_CARD_LAYOUT, titles), 1):
        evidence = [row for row in rows if box[0] <= row['box'][0] < row['box'][2] <= box[2]
            and box[1] <= row['box'][1] < row['box'][3] <= box[3]]
        verified = next((item for item in options if item['card_index'] == index), None)
        cards.append({'card_index': index, 'bounds': list(box),
            'title': title[0]['text'] if title[0]['confidence'] >= .90 else None,
            'candidate_title': title[0]['text'], 'verified_option': verified,
            'evidence_rows': evidence,
            'low_confidence_rows': [row for row in evidence if row.get('confidence', 0) < .90]})
    return {'origin': 'native_five_card_read', 'complete': len(options) == 5,
        'cards': cards, 'input_allowed': len(options) == 5,
        'retry_regions': [card['bounds'] for card in cards if card['low_confidence_rows']],
        'rule': '定向补读未知字段；完整卡片和确认按钮未核齐时不选择'}


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


def guide_targets(body_lines):
    """Extract named targets from the applied guide, never from a guessed lineup."""
    targets = {key: [] for key in ('early', 'transition', 'core', 'tracking')}
    basis, no_reroll, deadlines = [], [], []
    try:
        names = json.loads((Path(__file__).parent / 'shop_reader_resources' / 'names.json').read_text(encoding='utf-8-sig'))['names']
        names = {name.casefold(): name for name in names if isinstance(name, str) and name}
    except (OSError, ValueError, KeyError, TypeError):
        return {'targets': targets, 'targets_basis': [], 'targets_read': False}
    if not names:
        return {'targets': targets, 'targets_basis': [], 'targets_read': False}
    pattern = re.compile('|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True)), re.I)
    phase = None
    for raw in body_lines:
        if not isinstance(raw, str):
            continue
        parts = re.split(r'(前期|中期|后期)[：:]', raw)
        for index, part in enumerate(parts):
            if index % 2:
                phase = {'前期': 'early', '中期': 'transition', '后期': 'core'}[part]
                continue
            if phase is None:
                continue
            selected, tracked = [], []
            for clause in re.split(r'[，,。；;]', part):
                if re.search(r'不要|不买|不留|别买|不推荐|放弃|卖掉|出售|替换掉', clause):
                    continue
                for match in pattern.finditer(clause):
                    name = names[match.group().casefold()]
                    if name not in selected:
                        selected.append(name)
                    if re.search(r'主[CcTt]|核心|装备优先|装备给', clause[:match.start()]):
                        if name not in tracked:
                            tracked.append(name)
            for key, values in ((phase, selected), ('tracking', tracked)):
                targets[key].extend(name for name in values if name not in targets[key])
            if selected:
                basis.append({'phase': phase, 'text': raw, 'units': selected})
            if re.search(r'(?:不能|不要|不|禁止)搜牌|(?:不要|禁止)刷新', part) and phase not in no_reroll:
                no_reroll.append(phase)
            for stage, level in re.findall(r'([1-3]-[1-9])上([1-9]|1[0-2])(?!\d)', part):
                value = {'stage': stage, 'level': int(level)}
                if value not in deadlines:
                    deadlines.append(value)
    targets['tracking'] = targets['tracking'][:3]
    return {'targets': targets, 'targets_basis': basis, 'targets_read': bool(basis),
            'operating_rules': {'no_reroll_phases': no_reroll, 'level_deadlines': deadlines,
                                'use_transition_equipment': any('有就合' in line for line in body_lines)}}


def semantic_facts(rows, image, page, engine=None, snapshot_id=None):
    """Factual fields are produced locally, never from a strategy assertion."""
    facts = {'options': option_facts(rows, page)}
    if page in ('preparation', 'shop'):
        from currency_wars_rewards import detect
        facts['rewards'] = detect(image, page, rows, snapshot_id)
    if page == 'settlement':
        settlement = settlement_facts(rows, image, engine, snapshot_id)
        if settlement is not None:
            facts['settlement'] = settlement
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
            facts['guide'].update(guide_targets([r['text'] for r in body if r['confidence'] >= .90]))
            # A guide portrait preview is not evidence that this unit is owned.
            preview_names = rows_in(rows, [1430, 70, 1775, 117])
            recommendation = find_text(rows, '装备推荐', [1350, 640, 1550, 730])
            if (len(preview_names) == 1 and preview_names[0]['confidence'] >= .90
                    and preview_names[0]['text'] in {name for values in facts['guide']['targets'].values() for name in values}
                    and recommendation and recommendation['confidence'] >= .90
                    and find_text(rows, '运营思路', [850, 770, 1070, 825])):
                facts['unit_preview'] = {'name': preview_names[0]['text'], 'source': 'guide',
                    'owned': None, 'snapshot_id': snapshot_id,
                    'recommendation_action': {'type': 'click_text', 'text': '装备推荐',
                        'exact': True, 'bounds': [1350, 640, 1550, 730],
                        'guard_texts': [preview_names[0]['text'], '攻略详情'],
                        'expected_page': 'guide', 'reason': '读取攻略角色的装备推荐与追踪状态'}}
    if page == 'unit_gear':
        names = rows_in(rows, [1490, 205, 1775, 254])
        if (len(names) == 1 and find_text(rows, '攻略推荐', [970, 550, 1200, 620])
                and find_text(rows, '取消', [1260, 550, 1375, 620])
                and find_text(rows, '装备推荐', [1393, 770, 1605, 857])):
            facts['guide_tracking'] = {'enabled': True, 'units': [names[0]['text']],
                                       'tracking_state': '攻略推荐/取消'}
            facts['gear'] = {'unit_name': names[0]['text'], 'snapshot_id': snapshot_id,
                'recommendation_texts': [r['text'] for r in rows_in(rows, [900, 300, 1790, 850])
                                         if r['confidence'] >= .90],
                'slot_labels': [], 'checked': True, 'inventory_checked': False,
                'scope': 'guide_recommendation_only', 'equipped': None}
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
        elif button and xp and gold >= .15 and engine is not None:
            # Only the actual native HUD's numeric subregion, excluding its icon.
            digit_bounds = [1638, 900, 1687, 940]
            crop = image.crop(digit_bounds).resize((98, 80))
            try:
                result, unused = engine(np.array(crop), use_det=False, use_cls=False)
            except Exception:
                result = None
            if result is not None and len(result) == 1:
                text, confidence = result[0][-2:]
                raw = str(text).strip()
                if re.fullmatch(r'[0-9]+', raw) and .90 <= float(confidence) <= 1.:
                    facts['coins'] = {'value': int(raw), 'bounds': GOLD_HUD,
                                      'currency_icon_gold_fraction': round(gold, 3),
                                      'method': 'fixed_hud_digit_ocr', 'ocr_bounds': digit_bounds,
                                      'raw_text': raw, 'confidence': round(float(confidence), 4)}
    return facts


def settlement_facts(rows, image, engine=None, snapshot_id=None):
    """Complete native first settlement page, including real grade and rewards.

    The gold SSS font needs its pale fill separated from the animated gold
    background. This is OCR of a fixed complete grade crop, not a low-score
    alias or a conclusion from a boss result. Other layouts remain unknown.
    """
    if image.size != (1920, 1080):
        return None
    evidence = {}
    specs = {
        'outcome': (r'对局(?:胜利|失败|结束)', (440, 160, 620, 235)),
        'hp': (r'小队生命值(100|[1-9]?[0-9])', (1360, 160, 1630, 235)),
        'tier': (r'A[1-9][^|]{1,12}', (520, 445, 705, 520)),
        'promotion_level': (r'晋升等级([0-9]{1,3})', (290, 580, 460, 650)),
        'material_reward': (r'[0-9]{1,6}', (670, 650, 745, 708)),
        'promotion_points': (r'[0-9]{1,7}', (655, 708, 745, 775)),
    }
    values = {}
    for key, (pattern, bounds) in specs.items():
        found = [row for row in rows if .90 <= row.get('confidence', 0) <= 1.
                 and re.fullmatch(pattern, clean(row['text']))
                 and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                 and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        if len(found) != 1:
            return None
        row = found[0]
        match = re.fullmatch(pattern, clean(row['text']))
        values[key] = int(match.group(1) if match.lastindex else row['text']) if key not in ('outcome', 'tier') else clean(row['text'])
        evidence[key] = dict(bounds=row['box'], confidence=row['confidence'], method='native_row_ocr')
    for label, bounds in (('奖励信息', (330, 515, 475, 575)),
                          ('获取材料', (290, 650, 450, 710)),
                          ('晋升点', (290, 708, 440, 775)),
                          ('我的阵容', (760, 365, 910, 425)),
                          ('下一页', (890, 865, 1030, 930))):
        found = [row for row in rows if .90 <= row.get('confidence', 0) <= 1.
                 and clean(row['text']) == label
                 and bounds[0] <= row['box'][0] < row['box'][2] <= bounds[2]
                 and bounds[1] <= row['box'][1] < row['box'][3] <= bounds[3]]
        if len(found) != 1:
            return None
        evidence[label] = dict(bounds=found[0]['box'], confidence=found[0]['confidence'], method='native_row_ocr')
    mode_bounds = [397, 450, 549, 515]
    grade_bounds = [375, 280, 667, 410]
    if engine is None:
        return None
    try:
        raw, unused = engine(np.array(image.crop(mode_bounds)), use_cls=False)
        modes = []
        for unused_box, text, confidence in raw or []:
            alias = OBSERVED_TEXT_ALIASES.get(str(text))
            value = clean(alias[0] if alias else text)
            if value in ('标准博弈', '超频博弈') and .90 <= float(confidence) <= 1.:
                modes.append((value, float(confidence)))
        if len(modes) != 1:
            return None
        import cv2
        hsv = cv2.cvtColor(np.array(image.crop(grade_bounds)), cv2.COLOR_RGB2HSV)
        mask = ((hsv[:, :, 1] < 160) & (hsv[:, :, 2] > 215)).astype(np.uint8) * 255
        crop = cv2.copyMakeBorder(255-mask, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
        grade, unused = engine(cv2.cvtColor(crop, cv2.COLOR_GRAY2RGB), use_det=False, use_cls=False)
        if grade is None or len(grade) != 1:
            return None
        rating, confidence = grade[0][-2:]
        if str(rating).strip() != 'SSS' or not .90 <= float(confidence) <= 1.:
            return None
        values.update(mode=modes[0][0], rating='SSS', snapshot_id=snapshot_id)
        evidence['mode'] = dict(bounds=mode_bounds, confidence=round(modes[0][1], 4), method='fixed_native_mode_ocr')
        evidence['rating'] = dict(bounds=grade_bounds, confidence=round(float(confidence), 4), method='fixed_native_sss_fill_ocr')
        values['evidence'] = evidence
        return values
    except Exception:
        return None  # An unread grade/mode never relaxes the screen-change gate.


def fingerprint(image):
    # Screen-change check, not a scene classifier. It is never sufficient alone.
    array = np.array(image.convert("L").resize((65, 36)))
    return np.packbits(array[:, 1:] > array[:, :-1]).tobytes().hex()


def hash_distance(a, b):
    left, right = bytes.fromhex(a), bytes.fromhex(b)
    if len(left) != len(right):
        return 1.
    return sum((x ^ y).bit_count() for x, y in zip(left, right)) / (len(left) * 8)


def valid_population_counts(occupied, capacity):
    """Team capacity is independent of player level and visible slot geometry."""
    return (type(occupied) is int and type(capacity) is int
            and 0 <= occupied <= capacity <= MAX_SUPPORTED_POPULATION and capacity >= 1)


def _valid_population_match(text):
    match = re.fullmatch(r'i?([0-9]{1,2}/[0-9]{1,2})', clean(text))
    if match is None:
        return None
    occupied, capacity = map(int, match[1].split('/'))
    return match if valid_population_counts(occupied, capacity) else None


def native_player_hud(rows, image, page, engine, snapshot_id):
    """A player-owned panel and full Lv read; no global level fallback."""
    result = {'actor': 'player', 'origin': 'native_player_hud', 'snapshot_id': snapshot_id,
              'layout': 'native_purchase_experience_1920x1080/v1',
              'level': None, 'xp': None, 'evidence': {}, 'reason': 'player_hud_not_visible'}
    if page not in ('preparation', 'shop'):
        return result

    def inside(row, bounds):
        box = row.get('box')
        return (isinstance(box, list) and len(box) == 4
                and bounds[0] <= box[0] < box[2] <= bounds[2]
                and bounds[1] <= box[1] < box[3] <= bounds[3])

    labels = [row for row in rows if clean(row.get('raw_text', row.get('text'))) == '购买经验'
              and .90 <= row.get('confidence', 0) <= 1. and inside(row, [235, 835, 360, 883])]
    progress = [(row, re.fullmatch(r'([0-9]{1,4})/([0-9]{1,4})', clean(row.get('raw_text', row.get('text')))))
                for row in rows if .90 <= row.get('confidence', 0) <= 1.
                and inside(row, [250, 933, 346, 969])]
    progress = [(row, match) for row, match in progress if match and 0 <= int(match[1]) < int(match[2])]
    if len(labels) != 1 or len(progress) != 1:
        return result
    result['xp'] = [int(progress[0][1][1]), int(progress[0][1][2])]
    result['evidence'] = {'purchase_experience': labels[0], 'experience_progress': progress[0][0]}
    # Preserve the original OCR rows, including low-confidence or partial Lv.
    # A differing plausible number cannot be voted away by another crop.
    candidates = [(row, re.fullmatch(r'Lv\.?([0-9]+)', clean(row.get('raw_text', row.get('text'))), re.I))
                  for row in rows if inside(row, [235, 875, 363, 943])]
    candidates = [(row, int(match[1])) for row, match in candidates if match]
    if len(candidates) > 1 or any(not re.fullmatch(r'Lv\.?([1-9]|10)',
            clean(row.get('raw_text', row.get('text'))), re.I) for row, unused in candidates):
        result['reason'] = 'conflicting_or_invalid_player_level'
        return result
    if candidates and .90 <= candidates[0][0].get('confidence', 0) <= 1.:
        row, value = candidates[0]
        result.update(level=value, reason=None)
        result['evidence']['level'] = {'source': 'current_player_hud_row', 'row': row}
        return result
    result['reason'] = 'player_level_unreadable'
    try:
        recognized, unused = engine(np.array(image.crop(PLAYER_LEVEL_ROI)), use_det=False, use_cls=False)
        if recognized is not None and len(recognized) == 1:
            raw, confidence = recognized[0][-2:]
            raw, confidence = str(raw), float(confidence)
            match = re.fullmatch(r'Lv\.?([1-9]|10)', clean(raw), re.I)
            result['evidence']['level'] = {'source': 'complete_player_level_roi', 'raw_text': raw,
                'confidence': confidence, 'bounds': list(PLAYER_LEVEL_ROI)}
            if match and .90 <= confidence <= 1. and all(value == int(match[1]) for unused, value in candidates):
                result.update(level=int(match[1]), reason=None)
    except Exception:
        pass  # A failed focused read is unknown; no fabricated OCR row.
    return result


def native_deployed_count(rows):
    """Unique, confident count from the native central roster HUD only."""
    candidates = [(row, re.fullmatch(r'i?([0-9]{1,2}/[0-9]{1,2})', clean(row.get('raw_text', row.get('text', '')))))
        for row in rows if .90 <= row.get('confidence', 0) <= 1.
        and isinstance(row.get('box'), list) and len(row['box']) == 4
        and 820 <= row['box'][0] < row['box'][2] <= 1050
        and 190 <= row['box'][1] < row['box'][3] <= 300]
    candidates = [(row, match[1]) for row, match in candidates if match]
    valid = [(row, value) for row, value in candidates if _valid_population_match(value)]
    if len(valid) != 1:
        return None
    row, value = valid[0]
    if len(candidates) == 1:
        return value
    # Only an actual recognition of the established digits-only crop may
    # resolve impossible full-row readings such as an icon joined to "18/8".
    # Original OCR rows remain unchanged. Two plausible readings still fail.
    if (row.get('normalization_basis') == 'fixed_native_deployed_count_roi'
            and row['box'] == DEPLOYED_COUNT_ROI):
        return value
    return None


class Perception:
    def __init__(self):
        self.engine = None
        self.shop_reader = None
        self.state_reader = None
        self.cache = None
        self._primary_cache = None

    def _ocr_contract(self):
        postprocess = getattr(getattr(self.engine, 'text_det', None), 'postprocess_op', None)
        return (OCR_CONTRACT_VERSION, getattr(self.engine, 'use_det', None),
                getattr(self.engine, 'use_rec', None), getattr(self.engine, 'text_score', None),
                getattr(postprocess, 'box_thresh', None), getattr(postprocess, 'unclip_ratio', None))

    def read(self, path, force=False, *, scope='full', reuse_primary=False):
        if scope not in READ_SCOPES:
            raise ValueError('unsupported perception read scope: ' + str(scope))
        if reuse_primary and scope != 'full':
            raise ValueError('primary OCR reuse requires a full semantic read')
        started = time.perf_counter()
        path = Path(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        timing = _ReadTiming(digest)
        cache_key = (digest, scope, READ_CONTRACT_VERSION, self._ocr_contract())
        if (self.cache and self.cache[0] == cache_key and self.cache[2] is self.engine
                and not force and not reuse_primary):
            # Worker adds the current request/frame identity. Neither that
            # identity nor a consumer's changes may mutate a cached snapshot.
            result = copy.deepcopy(self.cache[1])
            result['image'] = str(path)
            result['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
            result['read_timing'] = {'cache_hit': True, 'primary_ocr_reused': False,
                'primary_ocr_executed': False, 'primary_ocr_contract_version': OCR_CONTRACT_VERSION,
                'elapsed_ms': result['elapsed_ms'], **timing.finish()}
            return result
        with Image.open(io.BytesIO(data)) as opened:
            if opened.size != (1920, 1080):
                raise ValueError("1920x1080 broker preview required")
            image = opened.convert("RGB")
        if self.engine is None:
            # Offline image reading does not authorize ORT telemetry. Set the
            # documented initialization switch before importing the runtime;
            # its API alone can be later than the initial telemetry event.
            os.environ['ORT_DISABLE_TELEMETRY'] = '1'
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from rapidocr_onnxruntime import RapidOCR
            self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        engine = timing.engine(self.engine)
        ocr_contract = self._ocr_contract()
        primary_key = (digest, ocr_contract)
        cached = self._primary_cache
        primary_reused = bool(reuse_primary and not force and cached
                              and cached[0] == primary_key and cached[2] is self.engine)
        if primary_reused:
            raw = cached[1]
        else:
            raw, unused = engine(np.array(image.resize((1280, 720))), use_cls=False)
            # Freeze only the original OCR output. Later aliases, ROI rows,
            # page classification and semantic facts belong to each read.
            raw = tuple((tuple(tuple(point) for point in box), text, float(confidence))
                        for box, text, confidence in raw or [])
            self._primary_cache = (primary_key, raw, self.engine)
        cache_key = (digest, scope, READ_CONTRACT_VERSION, ocr_contract)
        rows = []
        for box, text, confidence in raw or []:
            xs, ys = [p[0] * 1.5 for p in box], [p[1] * 1.5 for p in box]
            alias = OBSERVED_TEXT_ALIASES.get(text)
            rows.append({"text": alias[0] if alias else text, "raw_text": text,
                         "normalization_basis": alias[1] if alias else None,
                         "confidence": round(float(confidence), 4),
                         "box": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]})
        page = classify(rows)
        if page == 'supply':
            # The retained native controller decoration is read as "）".
            # Normalize only this fixed button, with the full five-card page
            # anchored; raw OCR and provenance remain available.
            header = [row for row in rows if clean(row['text']) == '补给阶段'
                      and .90 <= row['confidence'] <= 1.
                      and 800 <= row['box'][0] < row['box'][2] <= 1120
                      and 120 <= row['box'][1] < row['box'][3] <= 190]
            titles = [rows_in(rows, [box[0]+28, 540, box[2]-28, 600])
                      for box in SUPPLY_FIVE_CARD_LAYOUT]
            if len(header) == 1 and all(len(found) == 1 and found[0]['confidence'] >= .90 for found in titles):
                buttons = [row for row in rows if clean(row['raw_text']) in ('）确认', ')确认')
                           and .90 <= row['confidence'] <= 1.
                           and 1580 <= row['box'][0] < row['box'][2] <= 1810
                           and 950 <= row['box'][1] < row['box'][3] <= 1025]
                if len(buttons) == 1 and not find_text(rows, '确认', [1580, 950, 1810, 1025]):
                    buttons[0]['text'] = '确认'
                    buttons[0]['normalization_basis'] = 'fixed_native_supply_confirm_decoration'
        if page == "unknown":
            # Only the observed native second-plane transition's left digit.
            native_plane = all(len([row for row in rows
                if .90 <= row["confidence"] <= 1. and clean(row["text"]) == label
                and bounds[0] <= row["box"][0] < row["box"][2] <= bounds[2]
                and bounds[1] <= row["box"][1] < row["box"][3] <= bounds[3]]) == 1
                for label, bounds in (("2", (900, 405, 1020, 535)),
                                       ("3", (1415, 405, 1545, 535)),
                                       ("点击空白处继续", (845, 925, 1090, 995)),
                                       ("位面", (400, 350, 490, 405)),
                                       ("位面", (900, 310, 1020, 370)),
                                       ("位面", (1430, 350, 1525, 405))))
            trusted_one = any(.90 <= row["confidence"] <= 1. and clean(row["text"]) == "1"
                              and 350 <= row["box"][0] < row["box"][2] <= 520
                              and 350 <= row["box"][1] < row["box"][3] <= 540 for row in rows)
            left_digit_rows = [row for row in rows
                               if 350 <= row["box"][0] < row["box"][2] <= 520
                               and 405 <= row["box"][1] < row["box"][3] <= 540]
            if (native_plane and not trusted_one
                    and all(clean(row["raw_text"]) == "1" for row in left_digit_rows)):
                one_bounds = [422, 416, 470, 520]
                try:
                    result, unused = engine(np.array(image.crop(one_bounds)), use_det=False, use_cls=False)
                    if result is not None and len(result) == 1:
                        text, confidence = result[0][-2:]
                        raw, confidence = str(text).strip(), float(confidence)
                        if raw == "1" and .90 <= confidence <= 1.:
                            rows.append({"text": raw, "raw_text": str(text), "confidence": confidence,
                                         "normalization_basis": "fixed_native_plane_left_one_roi",
                                         "box": one_bounds})
                except Exception:
                    pass  # Unread or conflicting left digit remains below the existing gate.
        if page == 'unknown' and _native_node_result_labels(rows) and not _native_node_result_stages(rows):
            # Fixed complete stage digits only; exclude the crossed-swords icon.
            # Keep all original low-confidence header rows unchanged.
            try:
                result, unused = engine(np.array(image.crop(NODE_RESULT_STAGE_CROP)), use_det=False, use_cls=False)
                if result is not None and len(result) == 1:
                    text, confidence = result[0][-2:]
                    raw, confidence = str(text).strip(), float(confidence)
                    if re.fullmatch(r'[1-3]-[1-9]', raw) and .90 <= confidence <= 1.:
                        rows.append({'text': raw, 'raw_text': str(text), 'confidence': confidence,
                                     'normalization_basis': 'fixed_native_node_result_stage_roi',
                                     'box': list(NODE_RESULT_STAGE_CROP)})
                        page = classify(rows)
            except Exception:
                pass  # Missing/uncertain stage remains unknown; never guess victory.
        # Page/overlay discovery still reads the complete frame. In particular,
        # omitted central modal text could otherwise leave preparation anchors
        # visible and prevent an "unknown" fallback from ever being reached.
        effective_scope = scope if page in ('preparation', 'shop') else 'full'
        narrow = effective_scope != 'full'
        def unread(field):
            return {'status': 'not_read', 'reason': 'outside_requested_read_scope',
                    'read_scope': effective_scope, 'snapshot_id': digest,
                    'field': field, 'checked': False, 'fully_read': False}

        shop = None
        if page == 'shop' and effective_scope == 'rewards':
            shop = {**unread('shop'), 'ok': False, 'slots': [], 'input': {'sha256': digest}}
        elif page == "shop":
            if self.shop_reader is None:
                from currency_wars_shop_reader import ShopReader
                self.shop_reader = ShopReader()
            shop = self.shop_reader.read(path.absolute())
        # Labels/positions have evidence. Unlabelled bare counters are not
        # guessed into HP/gold/promotion fields.
        fields = {}
        joined = "|".join(clean(r["text"]) for r in rows)
        matched = re.search(r"(?:备战|战斗中).*?(\d[-－]\d)", joined)
        player = ({**unread('player_hud'), 'actor': 'player', 'level': None, 'xp': None,
                   'evidence': {}} if effective_scope == 'rewards' else
                  native_player_hud(rows, image, page, engine, digest))
        fields = {'stage': matched.group(1) if matched else None,
                  'level': str(player['level']) if player['level'] is not None else None,
                  'deployed': None}
        if page == "preparation" and effective_scope != 'rewards':
            if native_deployed_count(rows) is None:
                native_layout = all(len([row for row in rows
                    if .90 <= row["confidence"] <= 1. and clean(row["text"]) == label
                    and bounds[0] <= row["box"][0] < row["box"][2] <= bounds[2]
                    and bounds[1] <= row["box"][1] < row["box"][3] <= bounds[3]]) == 1
                    for label, bounds in (("备战阶段", (410, 20, 540, 65)),
                                          ("出战", (1760, 710, 1875, 790)),
                                          ("商店", (1575, 950, 1675, 1020))))
                if native_layout:
                    # One complete native count crop, excluding the blue icon.
                    # Keep original rows and reject conflicting central counts.
                    count_bounds = list(DEPLOYED_COUNT_ROI)
                    try:
                        result, unused = engine(np.array(image.crop(count_bounds)), use_det=False, use_cls=False)
                        if result is not None and len(result) == 1:
                            text, confidence = result[0][-2:]
                            raw, confidence = str(text).strip(), float(confidence)
                            count = _valid_population_match(raw)
                            previous = [_valid_population_match(row["raw_text"])
                                        for row in rows
                                        if 820 <= row["box"][0] < row["box"][2] <= 1050
                                        and 190 <= row["box"][1] < row["box"][3] <= 300]
                            if (count and raw == count[1] and .90 <= confidence <= 1.
                                    and all(match is None or match[1] == raw for match in previous)):
                                derived = {"text": raw, "raw_text": str(text),
                                           "normalization_basis": "fixed_native_deployed_count_roi",
                                           "confidence": confidence, "box": count_bounds}
                                rows.append(derived)
                    except Exception:
                        pass  # Read failure leaves the count unknown; no inferred value.
            fields["deployed"] = native_deployed_count(rows)
            # One unscaled, complete native HP crop, only when trusted HP is absent.
            hp_pattern = r"(?:100|[1-9]?[0-9])"
            hp_bounds = [1420, 60, 1490, 100]
            hp_rows = [row for row in rows
                       if re.fullmatch(hp_pattern, clean(row["raw_text"]))
                       and 1400 <= row["box"][0] < row["box"][2] <= 1500
                       and 45 <= row["box"][1] < row["box"][3] <= 105]
            native_hp_layout = all(len([row for row in rows
                if .90 <= row["confidence"] <= 1. and re.fullmatch(pattern, clean(row["text"]))
                and bounds[0] <= row["box"][0] < row["box"][2] <= bounds[2]
                and bounds[1] <= row["box"][1] < row["box"][3] <= bounds[3]]) == 1
                for pattern, bounds in (("备战阶段", (410, 20, 540, 65)),
                                         (r"[1-3]-[1-9]", (420, 50, 520, 105)),
                                         (r"i?[0-9]{1,2}/[0-9]{1,2}", (820, 190, 1050, 300)),
                                         ("出战", (1760, 710, 1875, 790)),
                                         ("商店", (1575, 950, 1675, 1020))))
            count = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", fields["deployed"] or "")
            if (native_hp_layout and count and valid_population_counts(int(count[1]), int(count[2]))
                    and not any(row["confidence"] >= .90 for row in hp_rows)):
                try:
                    result, unused = engine(np.array(image.crop(hp_bounds)), use_det=False, use_cls=False)
                    if result is not None and len(result) == 1:
                        text, confidence = result[0][-2:]
                        raw, confidence = str(text).strip(), float(confidence)
                        if (re.fullmatch(hp_pattern, raw) and .90 <= confidence <= 1.
                                and all(clean(row["raw_text"]) == raw for row in hp_rows)):
                            rows.append({"text": raw, "raw_text": str(text), "confidence": confidence,
                                         "normalization_basis": "fixed_native_preparation_hp_roi",
                                         "box": hp_bounds})
                except Exception:
                    pass  # Missing or conflicting HP remains unread; no guessed value.
        elif page in ('shop', 'investment_summary') and effective_scope != 'rewards':
            fields['deployed'] = native_deployed_count(rows)
        elif page == "node_result":
            fields["stage"] = _native_node_result_stage(rows)
            fields["deployed"] = None
        elif page == 'boss_result':
            fields['stage'] = _native_boss_result_stage(rows)
            fields['deployed'] = None
        elif page == "battle":
            fields["stage"] = _native_battle_stage(rows) or fields["stage"]
        semantic = semantic_facts(rows, image, page, engine=engine, snapshot_id=digest)
        semantic['player_hud'] = player
        from currency_wars_refresh_offer import read_offer, unread_offer
        semantic['refresh_offer'] = (unread_offer(digest, page) if effective_scope == 'rewards' else
                                     read_offer(rows, image, page, engine, digest))
        option_read = supply_read_details(rows, page, semantic.get('options', []))
        if option_read:
            semantic['option_read'] = {**option_read, 'snapshot_id': digest}
        state_read = None
        if narrow:
            semantic['team'] = {**unread('team'), 'units': []}
            semantic['inventory'] = {**unread('inventory'), 'items': []}
            state_read = {**unread('state_read'), 'team': copy.deepcopy(semantic['team']),
                          'inventory': copy.deepcopy(semantic['inventory'])}
        elif page in ('preparation', 'shop', 'investment_summary'):
            from currency_wars_state_reader import StateReader, native_slots
            if self.state_reader is None:
                self.state_reader = StateReader()
            state_read = self.state_reader.read(path, rows=rows, page=page)
            team = state_read['team']
            population = re.fullmatch(r'([0-9]{1,2})/([0-9]{1,2})', native_deployed_count(rows) or '')
            board = [unit for unit in team['units'] if unit['location'] == 'board']
            supported_board_slots = len([slot for slot in native_slots() if slot['location'] == 'board'])
            # Complete card identity and star evidence plus the independent
            # central HUD are required; partial geometry stays unchecked.
            checked = bool(team['fully_read'] and population
                and valid_population_counts(int(population[1]), int(population[2]))
                # Numeric capacity up to 12 does not prove an expanded layout:
                # this reader only has the observed front4/back6 board slots.
                and int(population[2]) <= supported_board_slots
                and int(population[1]) == len(board)
                and all(unit.get('position') in ('前台', '后台', '前后台') for unit in board))
            semantic['team'] = {**team, 'checked': checked,
                'count_reconciliation': {'hud': fields.get('deployed'), 'observed_board': len(board),
                    'supported_board_slots': supported_board_slots}}
            inventory = state_read['inventory']
            semantic['inventory'] = {**inventory, 'items': [{**item,
                'verified': bool(item.get('name') and item.get('confidence', 0) >= .90
                    and item.get('evidence', {}).get('identity_margin', 0) >= .10)}
                for item in inventory['items']]}
        contract = {'version': READ_CONTRACT_VERSION, 'requested_scope': scope,
                    'effective_scope': effective_scope, 'page_ocr': 'full_frame',
                    'unread': (['team', 'inventory'] +
                        (['shop', 'player_hud', 'deployed', 'hp', 'refresh_offer'] if effective_scope == 'rewards' else []))
                        if narrow else [],
                    'fallback_reason': 'page_requires_full_read' if effective_scope != scope else None}
        result = {"snapshot_id": digest, "page": page, "rows": rows, "fields": fields,
                  "fingerprint": fingerprint(image), "shop": shop,
                  "semantic": semantic, "state_read": state_read,
                  "read_contract": contract,
                  "image": str(path), "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        result['read_timing'] = {'cache_hit': False, 'primary_ocr_reused': primary_reused,
            'primary_ocr_executed': not primary_reused, 'primary_ocr_contract_version': OCR_CONTRACT_VERSION,
            'elapsed_ms': result['elapsed_ms'], **timing.finish()}
        self.cache = cache_key, copy.deepcopy(result), self.engine
        return result
