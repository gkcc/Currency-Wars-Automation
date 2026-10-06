"""Input-free economic dependencies, a single node budget, and actual effects.

Supervisor readings retain their own provenance. Matching fresh numeric ROIs
may reuse a read; changed ROIs must be read again, never copied into native OCR.
"""
from __future__ import annotations

import hashlib
import re

SCHEMA = 'currency-wars-economy/v1'
FIELDS = ('coins', 'level', 'xp', 'xp_cost', 'xp_gain', 'free_refreshes', 'refresh_cost')
XP_KEY, REFRESH_KEY = 70, 68
PREPARATION_PAGES = ('preparation', 'shop')
POLICY_FIELDS = ('mode', 'targets', 'budget', 'paid_search', 'experience', 'reserve')


def economic_action(action, page):
    if page not in PREPARATION_PAGES:
        return None
    kind, text, keys = action.get('type'), action.get('text', ''), action.get('args')
    if kind == 'buy_shop':
        return 'purchase'
    if kind == 'buy_xp' or kind == 'key' and keys == [XP_KEY] or kind == 'click_text' and '购买经验' in text:
        return 'experience'
    if kind == 'key' and keys == [REFRESH_KEY] or kind == 'click_text' and text.strip() == '刷新':
        return 'refresh'
    return None


def _integer(value, low=0, high=1000000):
    return type(value) is int and low <= value <= high


def valid_field(name, value):
    if name == 'xp':
        return (isinstance(value, list) and len(value) == 2 and _integer(value[0])
                and _integer(value[1], 1) and value[0] < value[1])
    return _integer(value, 1 if name in ('level', 'xp_cost', 'xp_gain', 'refresh_cost') else 0,
                    10 if name == 'level' else 1000000)


def parse_field(name, text):
    text = re.sub(r'\s+', '', text)
    pattern = r'(\d{1,6})/(\d{1,6})' if name == 'xp' else (
        r'(?:Lv\.?|等级)?(\d{1,2})' if name == 'level' else r'\+?(\d{1,6})')
    match = re.fullmatch(pattern, text, re.I)
    if not match:
        return None
    value = [int(match[1]), int(match[2])] if name == 'xp' else int(match[1])
    return value if valid_field(name, value) else None


def _region(bounds):
    return (isinstance(bounds, list) and len(bounds) == 4 and all(type(v) is int for v in bounds)
            and 0 <= bounds[0] < bounds[2] <= 1920 and 0 <= bounds[1] < bounds[3] <= 1080
            and bounds[2] - bounds[0] >= 4 and bounds[3] - bounds[1] >= 4)


def bind_fields(values, image, *, gold_bounds):
    """The caller has verified the current request, original PNG and reviewer."""
    if not isinstance(values, dict) or not set(values) <= set(FIELDS):
        raise ValueError('经济字段只接受金币、等级、经验/价格/增量、免费次数及付费价格；未见字段省略或null')
    if image.size != (1920, 1080):
        raise ValueError('经济读数须绑定完整原生预览帧')
    result = {}
    for name in FIELDS:
        item = values.get(name)
        if item is None:
            result[name] = {}  # Hidden/unread fields remain explicitly unknown.
            continue
        if (not isinstance(item, dict) or not valid_field(name, item.get('value'))
                or not _region(item.get('bounds'))):
            raise ValueError('经济字段的真实值/数字区域无效：' + name)
        if name == 'coins' and item['bounds'] != gold_bounds:
            raise ValueError('金币须使用固定金额HUD完整区域')
        result[name] = {'value': item['value'], 'bounds': item['bounds'],
                        'rgb_sha256': hashlib.sha256(image.crop(item['bounds']).convert('RGB').tobytes()).hexdigest()}
    return result


def observe_fields(bound, image, engine=None):
    """Fresh pixels or confident crop OCR, with the actual source kept separate."""
    values, evidence, unknown = {}, {}, []
    for name in FIELDS:
        item = bound.get(name, {})
        bounds = item.get('bounds')
        value, source = None, None
        if _region(bounds):
            crop = image.crop(bounds).convert('RGB')
            if hashlib.sha256(crop.tobytes()).hexdigest() == item.get('rgb_sha256'):
                value, source = item['value'], 'supervisor_read_with_identical_current_roi'
            elif engine is not None:
                try:
                    import numpy as np
                    found, unused = engine(np.array(crop), use_det=False, use_cls=False)
                    if found is not None and len(found) == 1:
                        raw, confidence = found[0][-2:]
                        if .90 <= float(confidence) <= 1.:
                            value = parse_field(name, str(raw))
                            if value is not None:
                                source = 'current_numeric_crop_ocr'
                                evidence[name] = {'raw': str(raw), 'confidence': float(confidence)}
                except Exception:
                    pass
        if value is None:
            unknown.append(name)
        else:
            values[name] = value
            evidence.setdefault(name, {}).update(source=source, bounds=bounds)
    return {'values': values, 'evidence': evidence, 'unknown': unknown}


def new_ledger():
    return {'spent': {'purchase': 0, 'refresh': 0, 'experience': 0},
            'paid_refreshes': 0, 'critical_spent': 0, 'pending': None,
            'revision': 0, 'budget': None, 'shop_complete': False, 'purchased': {}}


def validate_budget(plan, observed, ledger):
    if (not isinstance(plan, dict) or plan.get('schema') != SCHEMA
            or plan.get('reviewer') != 'supervising_agent'
            or not _integer(plan.get('revision'), 1) or not isinstance(plan.get('reason'), str) or not plan['reason'].strip()
            or plan.get('mode') not in ('标准博弈', '超频博弈')):
        raise ValueError('经济计划须为本节点主管明确读取与决策的版本化预算')
    budget, search, experience = plan.get('budget'), plan.get('paid_search'), plan.get('experience')
    targets = plan.get('targets')
    if (not isinstance(targets, list) or len(targets) > 32 or any(
            not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip()
            or not _integer(item.get('copies'), 1, 9) or not isinstance(item.get('reason'), str) or not item['reason'].strip()
            or type(item.get('critical')) is not bool for item in targets)
            or len({item['name'] for item in targets}) != len(targets)
            or plan.get('shop_reviewed') is not True or plan.get('purchase_only_targets') is not True):
        raise ValueError('须明确本节点购牌目标、最大基础张数与用途，并实读商店后确认只购买这些目标')
    if (not isinstance(budget, dict) or set(budget) != {'purchase', 'refresh', 'experience'}
            or any(not _integer(value) for value in budget.values())
            or not isinstance(search, dict) or not _integer(search.get('max_refreshes'))
            or not _integer(search.get('purchase_reserve')) or not isinstance(search.get('reason'), str)
            or not search['reason'].strip() or not isinstance(search.get('stop_conditions'), list)
            or not {'budget_exhausted', 'max_refreshes', 'purchase_reserve', 'target_acquired'} <= set(search['stop_conditions'])
            or not isinstance(search.get('targets'), list) or any(not isinstance(name, str) or not name.strip() for name in search['targets'])
            or not isinstance(experience, dict) or not _integer(experience.get('target_level'), 1, 10)
            or not isinstance(experience.get('reason'), str) or not experience['reason'].strip()):
        raise ValueError('购买/搜牌/经验须共享明确金额上限，搜牌须有次数、目标、购牌留资和停止条件')
    if budget['refresh'] and (search['max_refreshes'] < 1 or search['purchase_reserve'] < 1 or not search['targets']):
        raise ValueError('付费搜牌须预留搜出目标后的购买资金，并明确目标及次数')
    if search['purchase_reserve'] > budget['purchase']:
        raise ValueError('搜牌购牌留资不能超过同一预算中的购买额度')
    if not set(search['targets']) <= {item['name'] for item in targets}:
        raise ValueError('搜牌目标必须属于同一购买预算的明确角色目标')
    reserve = plan.get('reserve', {})
    amount, rounds = reserve.get('coins'), reserve.get('remaining_interest_rounds')
    if (not _integer(amount, 0, 50) or not _integer(reserve.get('critical_allowance'))
            or not isinstance(reserve.get('reason'), str) or not reserve['reason'].strip()
            or rounds is not None and not _integer(rounds, 0, 100)):
        raise ValueError('利息储备与关键缺口例外须有明确金额、理由和真实剩余结息回合来源')
    if plan['mode'] == '超频博弈' and amount != 0:
        raise ValueError('超频无利息，不套用标准50金币储备')
    if plan['mode'] == '标准博弈' and amount < 50:
        if rounds is None or not isinstance(reserve.get('rounds_evidence'), dict):
            raise ValueError('降低常规50储备须当前结息读数或有来源的末关确认及原帧proof，不能用节点字符串猜终盘')
        if rounds == 0 and amount != 0:
            raise ValueError('最后备战没有下轮利息，不为不存在的利息保留金币')
    if (any(budget[key] < ledger['spent'][key] for key in budget)
            or reserve['critical_allowance'] < ledger['critical_spent']
            or plan['revision'] < ledger['revision']
            or ledger['budget'] is not None and budget != ledger['budget'] and plan['revision'] <= ledger['revision']):
        raise ValueError('同节点已证实花费不能清零；预算变更须显式提高修订号并保留历史实花')
    if (ledger.get('policy') is not None and {key: plan[key] for key in POLICY_FIELDS} != ledger['policy']
            and plan['revision'] <= ledger['revision']):
        raise ValueError('角色目标、停止条件、储备或人口目标变化须显式提高修订号')
    coins = observed['values'].get('coins')
    if coins is None:
        raise ValueError('新帧金币未知，不能分配预算')
    remaining = sum(budget[key] - ledger['spent'][key] for key in budget)
    allowance = max(0, reserve['critical_allowance'] - ledger['critical_spent'])
    if remaining > min(coins, max(0, coins - amount) + allowance):
        raise ValueError('购买、搜牌与经验剩余额度重复占用同一笔钱，超过新帧可用资金')
    return {'reserve_coins': amount, 'remaining_interest_rounds': rounds,
            'interest_source': reserve.get('rounds_evidence', {}).get('source', 'observed_screen')
                if rounds is not None else None}


def required_fields(kind, values):
    required = {'coins'} if kind == 'purchase' else {'coins', 'free_refreshes'}
    if kind == 'experience':
        required.update(('level', 'xp', 'xp_cost', 'xp_gain'))
    if kind == 'refresh' and values.get('free_refreshes') == 0:
        required.add('refresh_cost')
    return required


def paid_search_status(plan, values, ledger, gaps, *, guide_permission=None):
    search, budget = plan['paid_search'], plan['budget']
    remaining = budget['refresh'] - ledger['spent']['refresh']
    purchase_remaining = budget['purchase'] - ledger['spent']['purchase']
    if gaps:
        return {'finished': False, 'reason': '当前明确缺口须先购买', 'allowed': False}
    if values.get('free_refreshes') is None:
        return {'finished': False, 'reason': '免费次数尚未实读', 'allowed': False}
    if values['free_refreshes'] > 0:
        return {'finished': False, 'reason': '先用免费刷新并处理新缺口', 'allowed': False}
    if budget['refresh'] == 0:
        return {'finished': True, 'reason': search['reason'], 'allowed': False}
    targets = {item['name']: item['copies'] for item in plan['targets']}
    if all(ledger['purchased'].get(name, 0) >= targets[name] for name in search['targets']):
        return {'finished': True, 'reason': '已按实际购买结果取得本次搜牌目标', 'allowed': False}
    if isinstance(search.get('stop_reason'), str) and search['stop_reason'].strip():
        return {'finished': True, 'reason': search['stop_reason'], 'allowed': False}
    if remaining <= 0 or ledger['paid_refreshes'] >= search['max_refreshes']:
        return {'finished': True, 'reason': '本节点实际搜牌花费或次数已达预算停止条件', 'allowed': False}
    if purchase_remaining < search['purchase_reserve']:
        return {'finished': True, 'reason': '购牌留资上限不足，停止搜牌', 'allowed': False}
    if guide_permission is not None and not guide_permission.get('allowed') and guide_permission.get('phase') is not None:
        return {'finished': True, 'reason': '当前已核攻略阶段禁止付费搜牌', 'allowed': False}
    cost = values.get('refresh_cost')
    if cost is None:
        return {'finished': False, 'reason': '付费刷新价格未知', 'allowed': False}
    reasons = []
    if remaining < cost:
        reasons.append('付费搜牌金额上限已到')
    if values.get('coins', -1) - cost < plan['reserve']['coins'] + search['purchase_reserve']:
        reasons.append('新帧金币不足以同时保留利息和搜出牌购买资金')
    if not reasons and guide_permission is not None and not guide_permission.get('allowed'):
        return {'finished': guide_permission.get('phase') is not None,
                'reason': ('当前已核攻略阶段禁止付费搜牌' if guide_permission.get('phase') is not None
                           else '攻略当前阶段未知，付费预算保持未解决，不能先挪给经验'), 'allowed': False}
    return {'finished': bool(reasons), 'reason': '；'.join(reasons) if reasons else '在同节点剩余搜牌预算内刷新一次',
            'allowed': not reasons}


def dependencies(plan, observation, ledger, gaps, *, shop_complete, guide_permission=None):
    values = observation['values']
    search = paid_search_status(plan, values, ledger, gaps, guide_permission=guide_permission)
    if gaps:
        phase, reason = 'purchase', '先购买当前明确缺口，回读槽位与金币'
    elif not shop_complete:
        phase, reason = 'shop_read', '当前商店五槽及明确缺口尚未完整核对'
    elif values.get('free_refreshes') is None:
        phase, reason = 'free_refresh', '免费刷新次数未知，不能先买经验'
    elif values['free_refreshes'] > 0:
        phase, reason = 'free_refresh', '先免费刷新一次，再重新核商店缺口'
    elif not search['finished']:
        phase, reason = 'paid_search', search['reason']
    else:
        phase, reason = 'experience', '当前缺口、免费刷新与付费搜牌条件已解决，再分配人口经验'
    return {'phase': phase, 'reason': reason, 'paid_search': search,
            'experience_allowed': phase == 'experience', 'unknown': list(observation.get('unknown', []))}


def require_spending(kind, cost, plan, observation, ledger, *, critical=False):
    values = observation['values']
    if not _integer(cost, 0 if kind == 'refresh' else 1) or values.get('coins', -1) < cost:
        raise ValueError('实际费用或当前金币未知/不足，未提交')
    if ledger['pending'] is not None:
        raise ValueError('上次经济动作仍待实际效果回验，不能重发或继续抢用同一预算')
    if ledger['spent'][kind] + cost > plan['budget'][kind]:
        raise ValueError('该动作超过同节点剩余预算')
    below = max(0, min(cost, plan['reserve']['coins'] - (values['coins'] - cost)))
    if below and (kind == 'refresh' or not critical
                  or ledger['critical_spent'] + below > plan['reserve']['critical_allowance']):
        raise ValueError('动作将动用利息储备，且没有本次明确关键缺口例外额度')
    return below


def classify_effect(kind, before, after, *, expected_cost, target_level=None, slot=None):
    """One published transaction. Unknown/partial never authorizes a replay."""
    a, b = before.get('values', {}), after.get('values', {})
    if 'coins' not in a or 'coins' not in b:
        return {'outcome': 'unknown', 'reason': '前后金币未实读'}
    spent = a['coins'] - b['coins']
    if kind == 'experience':
        required = ('level', 'xp', 'xp_gain')
        if any(key not in a for key in required) or any(key not in b for key in ('level', 'xp')):
            return {'outcome': 'unknown', 'reason': '前后等级/经验未实读', 'observed_spent': spent}
        same = a['level'] == b['level'] and a['xp'] == b['xp']
        if a['level'] == b['level']:
            gain = b['xp'][0] - a['xp'][0] if a['xp'][1] == b['xp'][1] else None
        elif b['level'] == a['level'] + 1:
            gain = a['xp'][1] - a['xp'][0] + b['xp'][0]
        else:
            gain = None
        changed, expected = not same, gain == a['xp_gain']
    elif kind == 'refresh':
        if 'free_refreshes' not in a or 'free_refreshes' not in b or not after.get('shop_complete'):
            return {'outcome': 'unknown', 'reason': '刷新后免费次数/完整五槽未实读', 'observed_spent': spent}
        changed = a['free_refreshes'] != b['free_refreshes'] or before.get('shop') != after.get('shop')
        expected = ((a['free_refreshes'] > 0 and b['free_refreshes'] == a['free_refreshes'] - 1 and expected_cost == 0)
                    or a['free_refreshes'] == b['free_refreshes'] == 0 and expected_cost > 0)
    else:
        previous = next((item for item in before.get('shop', []) if item['slot'] == slot), None)
        current = next((item for item in after.get('shop', []) if item['slot'] == slot), None)
        if not previous or not current:
            return {'outcome': 'unknown', 'reason': '购后目标槽位未可靠回读', 'observed_spent': spent}
        changed = previous != current
        expected = current.get('status') == 'empty'
    if spent == 0 and not changed:
        return {'outcome': 'zero', 'reason': '金额和对应界面状态均未变化', 'observed_spent': 0}
    if spent == expected_cost and expected:
        return {'outcome': 'success', 'observed_spent': spent,
                'target_reached': kind == 'experience' and target_level is not None and b.get('level', 0) >= target_level}
    return {'outcome': 'partial' if spent or changed else 'unknown',
            'reason': '实际金额/经验/免费次数或槽位变化与本笔预期不一致', 'observed_spent': spent}
