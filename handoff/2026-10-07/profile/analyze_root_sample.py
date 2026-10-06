#!/usr/bin/env python3
"""Reproduce a descriptive report for the pinned partial ROOT helper sample.

This deliberately does not import or emit currency-wars-profile/1 spans.
Only the Python standard library is required. No game or broker is contacted.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import html
import json
import math
from pathlib import Path
import re
import statistics


SOURCE_COMMIT = "7e3400b47dba338ddfc3694c2724bb615e8e008b"
SOURCE_BLOB = "2d45708bb665e0bdaaec7619c74b64e8b0dc578d"
SOURCE_PATH = "handoff/2026-10-07/ROOT_PROFILE_SAMPLE.json"
SOURCE_URL = f"https://github.com/gkcc/Currency-Wars-Automation/blob/{SOURCE_COMMIT}/{SOURCE_PATH}"
SCHEMA = "root-manual-profile-analysis/v1"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
PHASES = {
    "rewards": "奖励/选择",
    "startup_guide": "创业指南（未独立标注）",
    "inventory_cleanup": "整理/清库",
    "economy": "购买/刷新/升级",
    "lineup_equipment": "布阵/装备/合成",
    "battle_acceptance": "验收/出战 helper",
    "battle": "完整战斗（未测量）",
    "settlement": "结算/下一页",
    "recovery": "恢复/恢复状态查询",
    "mixed_guide_equipment": "指南与装备混合",
    "battle_progress_query": "战斗进度查询",
    "mixed_battle_settlement": "战斗或结算观察",
    "unknown": "未标注阶段",
}
# Every assignment is an operator-label interpretation, never native phase proof.
# Mixed labels stay mixed; neither their duration nor unrecorded gaps are split.
LABEL_PHASES = {
    "current-game": "unknown",
    "equip-and-guide": "mixed_guide_equipment",
    "shop-open": "economy",
    "free-refresh-and-xp": "economy",
    "buy-feixiao-free-refresh": "economy",
    "prep-acceptance": "battle_acceptance",
    "departure": "battle_acceptance",
    "native-battle-progress": "battle_progress_query",
    "native-progress": "battle_progress_query",
    "battle-or-result": "mixed_battle_settlement",
    "result-or-combat": "mixed_battle_settlement",
    "native-fight": "recovery",
    "next-page": "settlement",
    "result-continue": "settlement",
    "result": "settlement",
    "continue": "settlement",
    "rewards": "rewards",
    "investment-reward": "rewards",
    "sell-drawer": "inventory_cleanup",
    "sell-before-shopping": "inventory_cleanup",
    "level-after-shop-stop": "economy",
    "cipher-role-read": "lineup_equipment",
    "deploy-eighth": "lineup_equipment",
    "reward-choice": "rewards",
    "armor-read": "rewards",
    "armor-choice-and-tooltip": "rewards",
    "select-recommended-armor": "rewards",
    "confirm-reward": "rewards",
    "gear-preparation": "lineup_equipment",
    "accept-armor-recommendation": "lineup_equipment",
    "return-gear-and-merge": "lineup_equipment",
    "clean-before-economy": "inventory_cleanup",
    "resume-state": "recovery",
    "fresh-before-sale": "inventory_cleanup",
    "sell-surplus-before-budget": "inventory_cleanup",
    "final-board-before-departure": "battle_acceptance",
    "continue-battle": "recovery",
}
LIMITS = [
    "这是恢复 3-1 后的部分 ROOT helper 样例；不覆盖整局，也没有 1-1、1-2 的节点样本。",
    "seconds 是各 helper 进程在 imports 和 owner load 之后的 monotonic 耗时；time 是完成 UTC。没有统一跨进程 monotonic 起止或 parent_id。",
    "估算区间为 [完成 UTC - seconds, 完成 UTC]；依赖 UTC 在样例期间没有显著跳变。区间并集不是父子调用去重，也不是完整操作轨迹。",
    "helper 边界内也没有截图、OCR、接管、输入动画、决策的独立耗时；所有此类子项均未知。窗口内未覆盖区间不能全归因于模型思考。",
    "kind=battle 是出战 helper，不是整场战斗计时；state 的 0.000 秒也只是报告精度内的 helper 耗时。",
    "label 是主管注释；phase 表按注释归组，既不是原生页面证明，也不是各业务阶段完整耗时。",
    "event.stage 是执行后节点。跨节点 continue/奖励确认单独列出，不能直接当作标签所指节点或新节点的完整耗时，更不能据此新增胜场。",
    "缺少 ok 的 15 条记录保持未知；ok=true 只表示 helper 报告成功，不表示动作生效、奖励到账、出战验收或脚本自主成功。",
    "event.id 是样例 helper 事件身份，不等同于已经核验的 broker request_id；image_basename 内 UUID 也不能替代实际收据对账。",
    "本样例没有完整实际输入收据。所有失败的输入效果均未知，包括含 no request sent 的 0.265/0.266 秒错误字符串。",
    "没有同口径改后样本；不能据此宣称提速、预计省下全部空档、完成全局奖励或整局自动化验收通过。",
]


def utc_us(value):
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise ValueError("UTC completion must contain an offset")
    delta = instant.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def iso(value):
    return (EPOCH + timedelta(microseconds=value)).isoformat(timespec="microseconds")


def seconds(value):
    return round(value / 1_000_000, 6)


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def duration_stats(rows):
    durations = [row["_duration_us"] for row in rows]
    if not durations:
        return {"event_count": 0, "helper_raw_sum_seconds": 0.0,
                "mean_seconds": None, "median_seconds": None,
                "p95_nearest_rank_seconds": None, "min_seconds": None, "max_seconds": None}
    return {
        "event_count": len(rows),
        "helper_raw_sum_seconds": seconds(sum(durations)),
        "mean_seconds": round(statistics.mean(durations) / 1_000_000, 6),
        "median_seconds": round(statistics.median(durations) / 1_000_000, 6),
        "p95_nearest_rank_seconds": seconds(sorted(durations)[math.ceil(.95 * len(rows)) - 1]),
        "min_seconds": seconds(min(durations)), "max_seconds": seconds(max(durations)),
    }


def grouping(rows, key, ordered_keys=None):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return [{"group": name, **duration_stats(groups[name])}
            for name in (ordered_keys if ordered_keys is not None else sorted(groups))]


def subset_window(rows):
    start, end = min(row["_start_us"] for row in rows), max(row["_end_us"] for row in rows)
    coverage = sum(b - a for a, b in union((row["_start_us"], row["_end_us"]) for row in rows))
    return {
        "event_indices": [row["event_index"] for row in rows],
        "estimated_start_utc": iso(start), "completion_end_utc": iso(end),
        "estimated_window_seconds": seconds(end - start),
        "helper_raw_sum_seconds": seconds(sum(row["_duration_us"] for row in rows)),
        "estimated_interval_union_seconds": seconds(coverage),
        "unclassified_outside_estimated_intervals_seconds": seconds(end - start - coverage),
        "business_outcome": "not_evaluated_without_receipts",
    }


def public_row(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def analyze(source):
    raw = source.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if blob != SOURCE_BLOB:
        raise ValueError(f"Expected the pinned sample blob {SOURCE_BLOB}; got {blob}")
    data = json.loads(raw)
    if data["schema"] != "root-manual-profile-sample/v1":
        raise ValueError("Unsupported source schema")
    rows = []
    seen = set()
    for index, event in enumerate(data["events"], 1):
        if event["id"] in seen or event["run_id"] != data["run_id"]:
            raise ValueError("Duplicate event id or inconsistent run identity")
        seen.add(event["id"])
        duration = Decimal(str(event["seconds"])) * 1_000_000
        if not duration.is_finite() or duration < 0 or duration != duration.to_integral_value():
            raise ValueError("Duration must be a nonnegative number of microseconds")
        duration = int(duration)
        end = utc_us(event["time"])
        match = re.match(r"^(\d+-\d+)-(.*)$", event["label"])
        node_hint, suffix = (match.group(1), match.group(2)) if match else ("", event["label"])
        phase = "economy" if re.fullmatch(r"paid-roll-\d+", suffix) else LABEL_PHASES.get(suffix, "unknown")
        after = event.get("stage", "")
        transition = bool(node_hint and after and node_hint != after)
        bucket = f"{node_hint}→{after}" if transition else (node_hint or after or "unlabelled")
        row = {
            "event_index": index, "id": event["id"],
            "id_origin": "source_helper_event_id_not_verified_broker_request_id",
            "kind": event["kind"], "label": event["label"],
            "reported_helper_seconds": event["seconds"], "completion_utc": event["time"],
            "estimated_start_utc": iso(end - duration),
            "stage_after": after, "label_node_hint": node_hint,
            "annotation_node_bucket": bucket, "cross_node_transition": transition,
            "phase_label": phase, "phase_zh": PHASES[phase],
            "phase_evidence": "operator_annotation_only",
            "ok_present": "ok" in event, "ok": event.get("ok"),
            "helper_status": ("reported_true" if event.get("ok") is True else
                              "reported_false" if event.get("ok") is False else "missing"),
            "business_outcome": "not_evaluated_without_receipts",
            "input_effect": "unknown_from_this_sample",
            "error": event.get("error"), "image_basename": event.get("image_basename", ""),
            "run_id": event["run_id"],
            "_duration_us": duration, "_start_us": end - duration, "_end_us": end,
        }
        rows.append(row)
    if len(rows) != 53 or any(b["_end_us"] < a["_end_us"] for a, b in zip(rows, rows[1:])):
        raise ValueError("Expected 53 events in completion order")
    merged = union((r["_start_us"], r["_end_us"]) for r in rows)
    first, last = merged[0][0], merged[-1][1]
    covered = sum(end - start for start, end in merged)
    raw_sum = sum(row["_duration_us"] for row in rows)
    gaps = []
    for (start, end), (next_start, next_end) in zip(merged, merged[1:]):
        before = max((r for r in rows if r["_end_us"] <= end), key=lambda r: r["_end_us"])
        after = min((r for r in rows if r["_start_us"] >= next_start), key=lambda r: r["_start_us"])
        gaps.append({
            "gap_index": len(gaps) + 1,
            "before_event_index": before["event_index"], "after_event_index": after["event_index"],
            "before_helper_event_id": before["id"], "after_helper_event_id": after["id"],
            "before_label": before["label"], "after_label": after["label"],
            "start_utc": iso(end), "end_utc": iso(next_start),
            "unclassified_seconds": seconds(next_start - end),
            "attribution": "unknown_no_causal_or_phase_assignment",
        })
    phase_rows = grouping(rows, "phase_label", PHASES)
    for row in phase_rows:
        row.update(phase_zh=PHASES[row["group"]], full_phase_elapsed_seconds=None,
                   evidence="operator_annotation_only",
                   note=("无可独立归属调用；不代表实际耗时为零" if not row["event_count"]
                         else "仅 helper 耗时之和；不含空档，不代表完整阶段"))
    stage_rows = [
        {"grouping": kind, **item, "full_node_elapsed_seconds": None}
        for kind, key in (("event_stage_after", "stage_after"), ("annotation_node_bucket", "annotation_node_bucket"))
        for item in grouping(rows, key)
    ]
    paid_rows = [r for r in rows if re.fullmatch(r"3-4-paid-roll-[1-6]", r["label"])]
    if [r["label"] for r in paid_rows] != [f"3-4-paid-roll-{n}" for n in range(1, 7)]:
        raise ValueError("Expected the six annotated paid-refresh helpers")
    paid = subset_window(paid_rows)
    paid["scope"] = "Six operator-labelled paid-refresh helpers; actual charges/effects are unverified."
    paid["completion_to_completion_seconds"] = [
        seconds(b["_end_us"] - a["_end_us"]) for a, b in zip(paid_rows, paid_rows[1:])]
    previous = rows[paid_rows[0]["event_index"] - 2]
    paid["preceding_event_index"] = previous["event_index"]
    paid["leading_unclassified_seconds"] = seconds(paid_rows[0]["_start_us"] - previous["_end_us"])
    paid["preceding_completion_to_last_completion_seconds"] = seconds(paid_rows[-1]["_end_us"] - previous["_end_us"])
    repeated = []
    for label in ("prep-acceptance", "3-1-departure", "3-2-departure"):
        repeated.append({"label": label, **subset_window([r for r in rows if r["label"] == label])})
    window = {
        "estimated_start_utc": iso(first), "completion_end_utc": iso(last),
        "estimated_window_seconds": seconds(last - first),
        "estimated_interval_union_seconds": seconds(covered),
        "helper_raw_sum_seconds": seconds(raw_sum),
        "estimated_overlap_seconds": seconds(raw_sum - covered),
        "unclassified_outside_estimated_intervals_seconds": seconds(last - first - covered),
        "estimated_coverage_percent": round(covered / (last - first) * 100, 6),
        "unclassified_percent": round((last - first - covered) / (last - first) * 100, 6),
        "positive_gap_count": len(gaps),
        "parent_child_deduplicated": False,
        "utc_no_jump_assumption": True,
        "artifact_capture_after_last_event_seconds": seconds(utc_us(data["captured_at"]) - last),
        "artifact_capture_tail_included": False,
    }
    # Arithmetic checks concern this descriptive transformation, not game acceptance.
    assert raw_sum == 287_461_000 and covered == raw_sum
    assert sum(round(g["unclassified_seconds"] * 1_000_000) for g in gaps) + covered == last - first
    assert sum(round(p["helper_raw_sum_seconds"] * 1_000_000) for p in phase_rows) == raw_sum
    assert sum(p["event_count"] for p in phase_rows) == len(rows)
    for view in ("event_stage_after", "annotation_node_bucket"):
        view_rows = [r for r in stage_rows if r["grouping"] == view]
        assert sum(r["event_count"] for r in view_rows) == len(rows)
        assert sum(round(r["helper_raw_sum_seconds"] * 1_000_000) for r in view_rows) == raw_sum
    assert round(window["estimated_window_seconds"], 3) == data["descriptive_totals"]["first_command_start_to_last_end_seconds"]
    assert round(window["unclassified_outside_estimated_intervals_seconds"], 3) == data["descriptive_totals"]["outside_command_intervals_seconds"]
    assert round(seconds(raw_sum), 3) == data["descriptive_totals"]["sum_command_seconds"]
    return {
        "schema": SCHEMA,
        "source": {"path": SOURCE_PATH, "url": SOURCE_URL, "commit": SOURCE_COMMIT,
                   "git_blob_sha1": blob, "sha256": hashlib.sha256(raw).hexdigest(),
                   "byte_count": len(raw), "schema": data["schema"], "captured_at": data["captured_at"]},
        "run_id": data["run_id"], "main_base_sha": data["main_base_sha"],
        "pro_head_sha": data["pro_head_sha"], "core_changed_locally": data["core_changed_locally"],
        "checkpoint": data["checkpoint"], "source_timing_contract": data["timing_contract"],
        "method": {"name": "partial-helper-duration-and-estimated-utc-window",
                   "duration_quantile": "nearest rank ceil(p*n); no interpolation",
                   "native_profile_spans_emitted": False, "parent_child_deduplicated": False,
                   "full_match_or_node_timing_available": False, "before_after_comparison_available": False,
                   "stage_views": "Two alternative partitions of the same events; do not sum both views.",
                   "fine_operation_breakdown": None, "phase_rule_map": LABEL_PHASES,
                   "paid_roll_phase_rule": "3-4-paid-roll-[1-6] => economy annotation"},
        "limitations": LIMITS, "window": window, "helper_duration_statistics": duration_stats(rows),
        "kind_statistics": grouping(rows, "kind"),
        "helper_status_statistics": grouping(rows, "helper_status"),
        "phase_statistics": phase_rows, "stage_statistics": stage_rows,
        "cross_node_events": [public_row(r) for r in rows if r["cross_node_transition"]],
        "paid_refresh_annotation_window": paid, "repeated_annotation_windows": repeated,
        "largest_gaps": sorted(gaps, key=lambda g: g["unclassified_seconds"], reverse=True)[:10],
        "gaps": gaps, "events": [public_row(r) for r in rows],
        "verification": {"source_blob_matched": True, "source_totals_reproduced": True,
                         "event_count": len(rows), "phase_partition_sum_matched": True,
                         "window_union_plus_gaps_matched": True,
                         "game_acceptance_performed": False, "runtime_files_changed": False},
    }


def fmt(value, digits=3):
    return "—" if value is None else f"{value:,.{digits}f}"


def md_table(headers, rows):
    esc = lambda value: str(value).replace("|", r"\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(esc, headers)) + " |",
                      "| " + " | ".join("---" for _ in headers) + " |"] +
                     ["| " + " | ".join(map(esc, row)) + " |" for row in rows])


def markdown_report(report):
    w, p, stats = report["window"], report["paid_refresh_annotation_window"], report["helper_duration_statistics"]
    phases = md_table(["按主管注释归组", "调用数", "helper 合计秒"], [
        (row["phase_zh"], row["event_count"], fmt(row["helper_raw_sum_seconds"]) if row["event_count"] else "未独立测量")
        for row in report["phase_statistics"]])
    kinds = md_table(["kind", "调用数", "合计秒", "中位数秒", "P95 秒"], [
        (r["group"], r["event_count"], fmt(r["helper_raw_sum_seconds"]), fmt(r["median_seconds"]),
         fmt(r["p95_nearest_rank_seconds"])) for r in report["kind_statistics"]])
    gap_table = md_table(["前→后事件", "标签边界", "未分类秒"], [
        (f"e{g['before_event_index']}→e{g['after_event_index']}",
         f"{g['before_label']} → {g['after_label']}", fmt(g["unclassified_seconds"]))
        for g in report["largest_gaps"][:5]])
    transitions = md_table(["事件", "原 label", "执行后 stage", "注释归属"], [
        (f"e{r['event_index']}", r["label"], r["stage_after"], r["annotation_node_bucket"])
        for r in report["cross_node_events"]])
    return f"""# 53 条 ROOT helper 计时样例分析

这份部分样例记录了 **{fmt(w['helper_raw_sum_seconds'])} 秒 helper 耗时**。用每条完成 UTC 减去本条耗时，得到的首尾观察窗口为 **{fmt(w['estimated_window_seconds'])} 秒（46 分 57.532 秒）**，其中 **{fmt(w['unclassified_outside_estimated_intervals_seconds'])} 秒、约 {w['unclassified_percent']:.1f}%** 没有细分计时。当前证据首先支持补齐观测与缩短多次调用往返，尚不能确定 OCR、决策、战斗分别占多少，也不构成任何前后提速结论。

## 来源与局面边界

- [固定提交源样例]({SOURCE_URL})；Git blob SHA-1：{SOURCE_BLOB}。
- 源文件 SHA-256：{report['source']['sha256']}；实际字节数 {report['source']['byte_count']}。
- 源主线基准：{report['main_base_sha']}；样例记录的 PR1 head：{report['pro_head_sha']}；core_changed_locally=false。
- 观察窗口：{w['estimated_start_utc']} 至 {w['completion_end_utc']}。源 captured_at 为 {report['source']['captured_at']}；最后事件后 {fmt(w['artifact_capture_after_last_event_seconds'])} 秒只是文件记录时间差，不扩入性能窗口。
- 样例 checkpoint 为 **3-4 战斗中，100 血、58 金币、8 级 2/72、8/8、飞霄三星、6 追击**；最后明确胜场仍是 **3-2，12 连胜**。不能推断 3-3 胜利、整局结束或全部奖励拿完。
- 源 timing_contract 自述机械输入当前来自 ROOT 外部 broker 动作、Worker reports 0；该样例不是脚本自主运行的验收。
- event.id 是 helper 事件身份；image_basename 内 UUID 与其不同。两者都不能默认等同于已核验的 broker request_id，仍需原始实际收据建立对应关系。

## 统计口径

1. 原始 helper 总和直接加总 53 个 seconds：{fmt(w['helper_raw_sum_seconds'])} 秒。它们的起点在 imports 与 owner load 之后，不包括整个进程启动或 helper 外的活动。
2. 每条估算区间为 [完成 UTC − seconds, 完成 UTC]。只对这些估算区间求数学并集：{fmt(w['estimated_interval_union_seconds'])} 秒；本样例没有估算重叠，所以恰好等于原始总和。**没有 parent_id 或跨进程 monotonic 起止，不能宣称已经完成父子调用去重。** 这个估算还依赖期间 UTC 没有显著跳变。
3. 首尾估算窗口减去并集，得到 {len(report['gaps'])} 段未覆盖空档。空档是未知；helper 内的截图、OCR、接管、输入动画、决策子项也都未知。没有将任何空档自动标成思考或战斗。
4. 阶段表按主管 label 的意图归组，不是原生阶段证明。混合标签保留混合；创业指南与完整战斗无独立计时。表中的调用合计不能当作整段阶段时长。
5. event.stage 是调用后的节点。过场事件同时保留标签来源节点和执行后节点；没有完整节点起止，1-1、1-2 缺样本，3-1 也只是恢复后的部分片段。
6. 统计均基于报告的毫秒精度；P95 用 nearest rank（ceil(0.95 × n)），不插值。state 的 0.000 秒不代表真实执行时间严格为零。

总体中位数 {fmt(stats['median_seconds'])} 秒、平均 {fmt(stats['mean_seconds'])} 秒、P95 {fmt(stats['p95_nearest_rank_seconds'])} 秒、最大 {fmt(stats['max_seconds'])} 秒。下面的 kind 只表示 helper 类型：

{kinds}

kind=battle 的 6 次调用合计 32.358 秒，都是出战 helper；不能把这个数字报成 6 场或整段战斗用时。

## 阶段注释与跨节点归属

{phases}

这些归组对 53 条事件只分配一次，合计仍为 287.461 秒；这叫事件归组核对，不是调用树去重。stage_summary.csv 同时提供执行后节点分组与注释节点/过场分组：每个视图各自合计 53 条、287.461 秒，两个视图不能再相加；所有完整节点耗时均为空。

{transitions}

e34 是 3-3 奖励确认后执行后节点变为 3-4。它不证明打赢 3-3；e17、e29 也不凭单个 ok 或 label 单独新增胜场。

## 先追踪哪些时间

### 最大未知空档

{gap_table}

最大空档 e40→e41 从 17:39:44.859037 至 17:44:30.858200 UTC，长 **285.999 秒**。它只说明两条记录之间缺少可归因计时；不能据 resume-state 注释断言此前都在恢复，也不能据 clean-before-economy 断言此前都在整理。

### 六次付费刷新注释窗口

e44–e49 的标签为 3-4-paid-roll-1 至 6；六条都报告 ok=true，但缺少实际收费/槽位效果收据，本报告不将它们升级为六次付费成功验收。

- 首次估算开始至第六次完成：**{fmt(p['estimated_window_seconds'])} 秒**（{p['estimated_start_utc']} 至 {p['completion_end_utc']}）。
- 六个 helper 合计：**{fmt(p['helper_raw_sum_seconds'])} 秒**，每次平均 {p['helper_raw_sum_seconds']/6:.3f} 秒。
- 中间五段未分类空档合计：**{fmt(p['unclassified_outside_estimated_intervals_seconds'])} 秒**；两次完成时刻之间的间隔依次为 {', '.join(fmt(v) for v in p['completion_to_completion_seconds'])} 秒。这些间隔包含后一次 helper 以及此前空档，不是单次纯刷新用时。
- 若把窗口扩到此前 e43 销售 helper 完成，则总窗为 {fmt(p['preceding_completion_to_last_completion_seconds'])} 秒；多出的 {fmt(p['leading_unclassified_seconds'])} 秒是首次刷新前的未知空档。与将来对比时必须保持窗口边界一致。

这提供了一个具体的 B-002/B-005 验证切口：在已核奖励→指南→清库、预算与攻略停止条件后，由既有 Worker 连续做一次刷新→新帧读动态数值和商店→核缺口/预算，再决定下一步。记录每次真实收据和暂停边界，出战仍保留最终新帧验收。稳定装备/策略文字缓存可单独记录命中与耗时。这里没有证据支持直接将 70.829 秒全部当成可节省时间。

另外，四次 prep-acceptance 注释（e6–e9）的 helper 合计 {fmt(report['repeated_annotation_windows'][0]['helper_raw_sum_seconds'])} 秒，首尾估算窗 {fmt(report['repeated_annotation_windows'][0]['estimated_window_seconds'])} 秒。应以实际动作收据核对这些调用分别完成了什么，再判断能否减少主管逐次往返；重复 label 本身不证明重复点击或无效工作。

## 成功、失败与原因分类

- 34 条 ok=true，helper 合计 186.480 秒；这是 helper 的报告状态，不是业务成功数。
- 4 条 ok=false，合计 4.734 秒：e25 出战 error 为空串；e31 为 unsupported action or argument count；e36/e38 为 physical input changed; no request sent。
- 15 条缺少 ok，合计 96.247 秒，保持未知。**0.265/0.266 秒与错误字符串不足以判定真实零输入**；没有完整收据就不能授权重发。
- **B 类已确认缺口**：计时缺父子关系、外层完整边界和子操作区间；机械往返窗口可复现于这份日志。其延迟成因仍需补证。
- **C 类未确认**：空档分别花在哪里、错误前是否发布/发生过输入、重复验收是否必要、某次刷新或装备动作是否生效。样例不足以将其写成已查明根因。
- **A 类策略/特殊**：本报告不据时间标签评判当时选卡、装备或搜牌策略是否正确；实际局面与策略证据应另核。

## 同口径后续采集

继续使用现有唯一 broker 和 PR1 的 profile 路径；本分析不改运行时，也不把旧 schema 填成完整原生 span。下一份样本应在真正发生的边界记录：

| 边界 | 最小证据 | 目的 |
| --- | --- | --- |
| 节点/阶段 | run、match、stage_before/after、节点开始/结束、phase enter/exit、新帧/过场收据 | 以同一节点边界对齐 1-1、1-2、3-1；未完成节点明确标 partial |
| 每次请求 | 唯一 request_id、parent_span_id、真实开始/结束、时钟/进程/同机启动身份、UTC 仅用于关联 | 在已确认兼容的 monotonic 时钟域求并集/排除父子重复；禁止直接拼接未知时钟域 |
| 子操作 | 实际截图/编码/解码、全屏与聚焦 OCR、缓存命中、接管、输入与动画等待、决策/等待回复 | 有证据才拆分子项，未覆盖仍记 unknown；helper 外层从启动前计时 |
| 动作与结果 | 发布状态、completed/input_attempted、实际 receipt、epoch/CAS、新帧与效果核验 | 不把错误或 ok 当成零输入/成功，也不因为观测失败重发输入 |
| 六次刷新窗口 | 从第一请求的同一真实边界到最后效果复核；每次费用/免费次数/目标/停止原因 | 同时报告调用总和、去重覆盖、未分类空档、动作数与业务结果；避免把少做操作当提速 |

前后报告应分别列模式、策略、节点、代码 SHA、是否主管输入、动作数、当前预算/奖励/任务状态、缺失比例。先满足同边界和真实验收，再描述耗时差异；本样例不能直接与人工十几分钟一局或将来完整自动化一局相比。

## 复跑与交付

在仓库根目录运行（仅 Python 标准库，不启动游戏、不导入 runner、不联系 broker）：

    python handoff/2026-10-07/profile/analyze_root_sample.py

脚本固定核对源 blob，重新计算全部结果并写入本目录。也可传 --source /绝对路径/ROOT_PROFILE_SAMPLE.json 与 --output-dir /临时目录。原样例不会被修改。

- [分析 JSON](ROOT_PROFILE_ANALYSIS.json)：独立 derived schema，方法、身份、原始事件派生列、所有空档与核对结果。
- [逐事件 CSV](events.csv)：53 行，保留原时间/耗时/label/stage/ok/error，另列估算开始与注释归属。
- [阶段表 CSV](phase_summary.csv) / [节点表 CSV](stage_summary.csv) / [全部空档 CSV](gaps.csv)。
- [独立 HTML](report.html)：总览、阶段表、空档、刷新窗口及可搜索的 53 条事件表。
- [复跑脚本](analyze_root_sample.py)：与报告一并保存，未引入运行时依赖或测试基础设施。

转换内已核对源 blob、53 个唯一事件、单一 run_id、源总数/总耗时/首尾窗、阶段与每个节点视图的分组之和、区间并集加空档等式。此处的核对只保证分析复现，**不等于实机输入或本局出战验收**。
"""


def html_table(headers, rows, table_id=""):
    attr = f' id="{html.escape(table_id, quote=True)}"' if table_id else ""
    return "<div class='table-wrap'><table" + attr + "><thead><tr>" + "".join(
        f"<th>{html.escape(str(h))}</th>" for h in headers) + "</tr></thead><tbody>" + "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows
    ) + "</tbody></table></div>"


def html_report(report):
    w, paid = report["window"], report["paid_refresh_annotation_window"]
    esc = html.escape
    cards = "".join(f"<div class='card'><div>{title}</div><strong>{value}</strong><small>{note}</small></div>"
                    for title, value, note in [
                        ("helper 调用", "53 条", "34 报告 ok；15 缺 ok；4 报告失败"),
                        ("helper 原始总和", "287.461 秒", "按各进程自身 seconds 相加"),
                        ("估算首尾窗口", "2817.532 秒", "仅恢复 3-1 后的部分片段"),
                        ("未分类空档", "2530.071 秒", "约 89.8%；不得全算思考或战斗")])
    kinds = html_table(["kind", "条数", "helper 合计秒", "中位数秒", "P95 秒"],
        [(r["group"], r["event_count"], fmt(r["helper_raw_sum_seconds"]), fmt(r["median_seconds"]),
          fmt(r["p95_nearest_rank_seconds"])) for r in report["kind_statistics"]])
    phases = html_table(["注释归组", "条数", "helper 合计秒", "证据边界"],
        [(r["phase_zh"], r["event_count"], fmt(r["helper_raw_sum_seconds"]) if r["event_count"] else "未测量", r["note"])
         for r in report["phase_statistics"]])
    stages = html_table(["执行后 stage", "条数", "helper 合计秒", "完整节点时长"],
        [(r["group"] or "缺失", r["event_count"], fmt(r["helper_raw_sum_seconds"]), "未知/partial")
         for r in report["stage_statistics"] if r["grouping"] == "event_stage_after"])
    transitions = html_table(["事件", "标签", "执行后节点", "保留的过场归属"],
        [(r["event_index"], r["label"], r["stage_after"], r["annotation_node_bucket"])
         for r in report["cross_node_events"]])
    gaps = html_table(["边界事件", "标签边界", "空档秒", "UTC 起止"],
        [(f"e{r['before_event_index']} → e{r['after_event_index']}", r["before_label"] + " → " + r["after_label"],
          fmt(r["unclassified_seconds"]), r["start_utc"][11:26] + " → " + r["end_utc"][11:26])
         for r in report["largest_gaps"]])
    paid_events = html_table(["事件", "刷新注释", "helper 秒", "估算开始 UTC", "完成 UTC", "helper 状态"],
        [(r["event_index"], r["label"], fmt(r["reported_helper_seconds"]), r["estimated_start_utc"][11:26],
          r["completion_utc"][11:26], r["helper_status"]) for r in report["events"]
         if r["event_index"] in paid["event_indices"]])
    events = html_table(["#", "kind", "label", "秒", "估算开始 UTC", "完成 UTC", "执行后节点",
                         "注释阶段", "ok 状态", "error"],
        [(r["event_index"], r["kind"], r["label"], fmt(r["reported_helper_seconds"]),
          r["estimated_start_utc"][11:26], r["completion_utc"][11:26], r["stage_after"] or "缺失",
          r["phase_zh"], r["helper_status"], r["error"] if r["error"] is not None else "")
         for r in report["events"]], "events")
    limits = "".join("<li>" + esc(note) + "</li>" for note in report["limitations"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>53 条 ROOT helper 计时样例</title><style>
:root{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1e293b;background:#f1f5f9;line-height:1.65}}
body{{margin:0}}main{{max-width:1260px;margin:auto;padding:28px}}h1{{line-height:1.25;font-size:2rem}}h2{{margin-top:2rem}}
.lead{{font-size:1.1rem;max-width:1050px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}}
.card,section{{background:white;border:1px solid #dbe3ed;border-radius:12px;padding:20px}}
.card strong{{display:block;font-size:1.7rem;color:#0f766e}}.card small{{display:block;color:#475569}}
.notice{{border-left:4px solid #d97706;background:#fffbeb;padding:16px 20px;margin:20px 0}}a{{color:#0369a1}}
.coverage{{height:35px;background:#cbd5e1;display:flex;border-radius:7px;overflow:hidden}}
.coverage span{{display:block;background:#0f766e;width:{w['estimated_coverage_percent']}%}}
.muted{{color:#64748b}}.table-wrap{{overflow-x:auto;margin:16px 0}}table{{border-collapse:collapse;width:100%;font-size:.91rem}}
td,th{{padding:9px 11px;border-bottom:1px solid #e2e8f0;text-align:left;vertical-align:top}}
th{{background:#eaf0f7;position:sticky;top:0}}tbody tr:nth-child(even){{background:#f8fafc}}
input{{font:inherit;width:min(600px,90%);padding:10px;border:1px solid #94a3b8;border-radius:6px}}
.links{{display:flex;flex-wrap:wrap;gap:16px}}code{{overflow-wrap:anywhere}}
@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:14px}}}}
@media print{{body{{background:white}}main{{max-width:none;padding:0}}.cards{{grid-template-columns:repeat(4,1fr)}}input{{display:none}}}}
</style></head><body><main>
<p class="muted">B-002 · 独立描述性分析 · 完整局与前后对比均不可用</p>
<h1>53 次调用只覆盖估算窗口的 {w['estimated_coverage_percent']:.1f}%</h1>
<p class="lead">helper 合计 287.461 秒；首尾估算窗口 46 分 57.532 秒。最值得继续追踪的是最长 286 秒空档，以及六次刷新注释中的往返时间。现有证据不能把空档拆成 OCR、思考或战斗，也不能说明修复后已经提速。</p>
{cards}
<section><h2 style="margin-top:0">测到了什么</h2>
<div class="coverage" role="img" aria-label="估算 helper 区间覆盖10.2%，未分类空档89.8%"><span></span></div>
<p><strong style="color:#0f766e">■ 287.461 秒估算覆盖</strong>　<span class="muted">■ 2530.071 秒未分类空档</span></p>
<p>每条估算开始 = 完成 UTC − 本条 seconds。对这些区间求并集也是 287.461 秒，本样例估算无重叠；没有 parent_id 或跨进程 monotonic 起止，因此<strong>不是父子去重结果</strong>。helper 内的细项也没有独立计时。</p>
<p>UTC 窗口：<code>{esc(w['estimated_start_utc'])}</code> 至 <code>{esc(w['completion_end_utc'])}</code>。需假设 UTC 无显著跳变。</p>
</section>
<div class="notice"><strong>局面边界：</strong>源 checkpoint 为 3-4 战斗中、100 血、58 金币、8 级 2/72、8/8、飞霄三星、6 追击；最后明确胜 3-2、12 连胜。不能推断 3-3 胜利、整局结束或全部奖励拿完。kind=battle 仅表示出战 helper。</div>
<h2>helper 类型</h2>{kinds}
<h2>阶段注释：调用合计，不是整段阶段耗时</h2>
<p>每个事件只归组一次；混合标签保持混合。创业指南和整段战斗未被独立测量。全部阶段的完整 elapsed 仍未知。</p>{phases}
<h2>节点与过场归属</h2><p>原 stage 是执行后节点；前 15 条没有 stage。1-1、1-2 缺样本，3-1 是恢复片段。下面均不能作为完整节点耗时：</p>{stages}{transitions}
<h2>最大的十段未知空档</h2>
<p>e40→e41 的 285.999 秒最多；前后标签只是定位线索，不能直接充当该空档的根因或阶段。</p>{gaps}
<h2>六次标注付费刷新的窗口</h2>
<p>e44–e49 首次估算开始至最后完成 <strong>105.032 秒</strong>；helper 合计 <strong>34.203 秒</strong>；其余 <strong>70.829 秒</strong>未知。六条 ok=true 没有被升级为费用或效果成功验收。</p>{paid_events}
<p>若从前一条销售 helper 完成计起，窗口是 147.992 秒；多出的 42.960 秒是第一条刷新前的未知空档。未来比较必须使用相同边界。可在既有 Worker 里验证有界刷新/读数/预算循环是否减少逐次主管往返，但出战仍须最终新帧验收，不能预先把未知空档全部计为收益。</p>
<h2>成功状态不等于业务验收</h2>
<p>34 条报告 ok=true（186.480 秒）、4 条报告 false（4.734 秒）、15 条缺 ok（96.247 秒）。e36/e38 的 0.265/0.266 秒错误文本含 no request sent，缺完整收据仍保持输入效果未知。重复 label 也不足以证明重复输入或无效工作。</p>
<h2>全部事件</h2><p>可搜索 label、阶段、执行后节点、状态或错误。各项子操作与业务效果仍未知；原 label、stage 和错误在 CSV/JSON 中完整保留。</p>
<input id="filter" type="search" placeholder="例如 paid-roll、reported_false、3-4" aria-label="筛选事件">
<span id="count" class="muted">53 / 53</span>{events}
<h2>解释边界与后续采集</h2><ul>{limits}</ul>
<p>后续在真实边界记录节点/阶段起止、外层 helper 请求起止、request_id/parent_span_id、时钟身份、收据发布与实际效果、当前 epoch、新帧，以及截图/OCR/缓存/接管/输入动画/决策子段。有证据才拆分，缺项保持 unknown；按同节点、模式、策略、代码 SHA 与主管输入比例报告比较。本报告没有修改 runtime profile。</p>
<h2>来源与复现文件</h2>
<p><a href="{esc(SOURCE_URL)}">固定 GitHub 源样例</a> · blob <code>{SOURCE_BLOB}</code><br>
SHA-256 <code>{report['source']['sha256']}</code> · {report['source']['byte_count']} 字节。</p>
<div class="links"><a href="ROOT_PROFILE_ANALYSIS.json">分析 JSON</a><a href="events.csv">53 条事件 CSV</a>
<a href="phase_summary.csv">阶段表 CSV</a><a href="stage_summary.csv">节点表 CSV</a><a href="gaps.csv">空档 CSV</a>
<a href="README.md">分析与方法</a><a href="analyze_root_sample.py">复跑脚本</a></div>
<p class="muted">schema: {SCHEMA}；源 schema 原样保留为 root-manual-profile-sample/v1。未转换为原生 profile span，未进行实机验证。</p>
</main><script>
const input=document.getElementById('filter'),rows=[...document.querySelectorAll('#events tbody tr')];
input.addEventListener('input',()=>{{const q=input.value.toLowerCase();let n=0;for(const row of rows){{const show=row.textContent.toLowerCase().includes(q);row.hidden=!show;if(show)n++;}}document.getElementById('count').textContent=n+' / '+rows.length;}});
</script></body></html>
"""


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent / "ROOT_PROFILE_SAMPLE.json")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    report = analyze(args.source)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "ROOT_PROFILE_ANALYSIS.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_dir / "events.csv", report["events"])
    write_csv(args.output_dir / "phase_summary.csv", report["phase_statistics"])
    write_csv(args.output_dir / "stage_summary.csv", report["stage_statistics"])
    write_csv(args.output_dir / "gaps.csv", report["gaps"])
    (args.output_dir / "README.md").write_text(markdown_report(report), encoding="utf-8")
    (args.output_dir / "report.html").write_text(html_report(report), encoding="utf-8")
    print(json.dumps({"schema": report["schema"], "event_count": len(report["events"]),
                      "helper_seconds": report["window"]["helper_raw_sum_seconds"],
                      "estimated_window_seconds": report["window"]["estimated_window_seconds"],
                      "source_blob": report["source"]["git_blob_sha1"],
                      "output_dir": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
