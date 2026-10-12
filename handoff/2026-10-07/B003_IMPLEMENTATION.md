# B-003：手操收据与新 epoch 恢复桥

基准：`beaea343ee7edf29bcba70ba8351a7dc462a83da`（PR1）。本批只修改既有 runner 的人工结果、恢复、阶段复核及其原有聚焦测试；没有新增输入控制器，也不依赖 B-004/B-005 尚未合入的模块。

这是代码与离线证据交付，尚未完成游戏实机验收。用户最新回报是同一未结束标准局 3-2 胜利、3-3 领奖，100 血／12 连胜、8 级 2/72、67 金、飞霄三星；属于本机回报，不能当作本补丁实测。该局核心仍冻结，局后再拉取候选分支。

## 1. 请求交付与业务结果分开

`manual_receipt_state` 按原请求、原终态回执、实际 `input_attempted`、`attempted_actions` 和 `completed` 前缀分类，不根据报错文案、PNG 是否变化或主管口述重建执行事实。

| 请求证据 | 记录 | 对恢复的影响 |
| --- | --- | --- |
| 无 handoff 的纯观察；或原回执明确未进入输入且没有尝试动作 | `zero_input` | 可对账失败观察／零输入拒绝；仍不自动完成业务阶段 |
| 原动作全部完成，即使没有可用截图 | `completed` | 只补当前观察，不重发该输入 |
| 失败后只有可信完成前缀，且没有额外已尝试动作 | `partial` | 保留准确前缀，后续依当前状态处理剩余目标 |
| 同 broker 成功 resume，原请求带精确 pause CAS | `control` | 单独记录控制交接；不声称完全无系统输入 |
| 输入已尝试但完成位置未知，或原事实缺失／矛盾 | `unknown` | 主管报告 success 也不能升级为历史阶段完成 |

`finish_manual_phase` 仍要求输入请求 ID、前水位、同 run／match／stage／manual_id／旧 epoch 和提交前 CAS。它会保存该水位后的原完整收据（仅脱敏密钥），控制恢复及失败纯观察可对账。可能输入的请求仍须显式列入 `input_receipt_ids`；找不到 ledger 不是发布前未提交的证明。单检查点最多 128 张收据，超限不截断证据。

业务结果另存 `success / no_effect / partial / unknown`。只有 success 才将历史记录写成 completed，而且恢复时仍需当前不变量复核。其余保持 pending。`completed=False` 可以回传部分或未知，例如：

```json
{
  "phase": "rewards",
  "stage": "3-3",
  "completed": false,
  "outcome": "partial",
  "reviewer": "supervising_agent",
  "findings": "原批次只有第一条输入完成；剩余奖励等待当前观察"
}
```

`no_effect` 可附 `effect_fields`，仅比较实际前后 `coins/xp/level/deployed/hp` 中列明且已知的字段，并明确 `effect_scope=listed_dynamic_fields_only`。金币相同不能证明拖动无效，也不会让该拖动越过 mutation fence。画面哈希比较只叫 `image_changed`；`native_automation_gate_passed` 仍为 false，不把人工完成改称脚本自主成功。

后帧暂时不可用时仍持久保存原收据和 observation_error，保持 unknown；再次结束同检查点只新增观察，不重复原输入。终态输入仍未知时，新增好帧也不会改写原交付事实。

## 2. 实际恢复路径与必要复核

沿用 `begin_manual_phase → entry.request → finish_manual_phase → explicit_resume → Worker`，没有另起流程。

1. 人工阶段结束为成功、部分或未知，归档实际请求和前后帧；未知终态仍如实保留。
2. `explicit_resume` 通过原同 broker 入口执行。原暂停 CAS、新接管优先和停止优先不变。成功收据与 old/new epoch 持久写成事件后，才提交新 epoch 并清除旧手动标记；持久化失败重新暂停，不再次派发 handoff。
3. Worker 在新 epoch 取得真实当前 observe 收据及完整 1920×1080 规范帧。校验原请求 ID、frame ID、capture 时间、PNG 哈希、水位、run／match／stage／进程身份和恢复事件。
4. `consume_manual_results` 只继承仍能验证的历史结果。缺当前 HUD、暂时不可读等通过 `ManualReviewDeferred` 等待新请求；同一请求只记录一次暂缓。身份冲突／未覆盖变更拒绝该历史 trace。
5. 历史 pending／unknown／拒绝不永久阻塞当前节点。主管可通过原 `review_preparation` 对新 epoch 的当前请求独立复核；通过后后续阶段可继续，而旧 unknown 收据保持 unknown。没有重放历史动作，也没有给旧完成标记套新 proof。

必要复核边界：

- 奖励领空没有原生全场 scanner。历史 rewards 不自动继承，必须当前监督复核 `all_claimed` 和 `rescanned_after_claim`；旧模板未匹配不是领空证据。
- 当前创业指南复核可保存真实目标，但会使此前奖励领空失效，返回奖励回扫。历史指南面板只在没有后续已记录输入变化且恢复身份完整时作为目标来源，并明确原 observation epoch；不伪称新读取。
- 库存清理继承须当前与历史 after 都覆盖 `native_slots()` 的完整 19 个唯一槽身份，空／占用明确，占用槽有名字与星级，且全部动态身份一致；另须各自同帧的 `team.capacity` 独立证明 `overflow_checked=True`、`overflow_count=0`。19 个固定槽不代表临时溢出，字段缺失、未知或仍有溢出都回当前监督复核。当前原生读取尚无临时溢出识别能力，本批不会补造这些字段。
- 经济继承要求当前 `fields.coins` 为整数、`fields.level` 为整数、`fields.xp` 为经验比例，并与实际 after 相符。PR1 原生感知尚不完整提供这组字段（金币另在 semantic、等级为字符串，缺经验字段），所以当前真实路径会暂缓，仍需当前主管复核；测试中显式提供的已知字段不是原生 OCR 能力。可信动态读数、统一预算和搜牌依赖由 B-005 后续补充，B-003 不声称真实经济自动续接已具备。
- 布阵继承仍核当前名单／星级／位置、强制同场约束及实际当前装备证据；缺原生装备读数时回到当前监督复核，不提高 `fully_read/confidence`。
- 出战人口取当前帧；既有单次新帧出战批准不继承。

## 3. 给 B-004 的最小恢复接口

```python
event, receipt = verified_resume_event(run, owner, control, epoch)
```

`epoch` 是实际 `runner-resume-epoch.json`。新增 `resume_event=id`；事件位于 `run/resume-events/<sha256(id)>.json`，并存入原 debug 证据目录。事件 schema 为 `manual-resume-event/v1`，包含 `run_id/match_id/stage/old_epoch/new_epoch/guard/consumed_manual_ids/receipt/recorded_at/input_resent`。

validator 核原 pause CAS、当前同 broker PID 与创建身份、前后 epoch、手动意图集合及脱敏后完全一致的原收据。调用者仍须核自身业务记录的 match／stage 和 old/new epoch，只能对这张精确 resume 收据放行；该接口不豁免其间的额外业务输入、多次不连续恢复或未知输入。

旧格式 manual 记录没有 `receipt_protocol=2` 或恢复事件时，不自动继承；可用当前请求独立监督复核，不修改历史字段来伪造兼容。

## 4. 本批实际验证

复用 `tools/test_local_runtime_compatibility.py` 的 `manual_bridge_fixture`，使用真实 runner／Entry 方法、真实临时 ledger／epoch／PNG 文件以及明确的控制器／感知替身。替身没有 Win32 输入，合成画面不代表游戏 OCR 质量。

以下选择在本批运行 **23 项，全部 PASS**：手操阶段、收据对账、epoch／暂停竞争、指南回扫、完整 19 槽复核，以及 PR1 的三项 Worker 帧检查。覆盖输入成功无截图、后帧截断仍归档、只补观察、失败的零输入拒绝、可信部分前缀、未知不伪成成功、后续当前复核可继续、缺 HUD 暂缓、新接管优先、被改写恢复事件、未归档拖动的围栏和当前人口约束。

在仓库根目录可重跑（依赖路径按本机调整）：

```bash
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python -B -X utf8 - <<'PY'
import unittest
import test_local_runtime_compatibility as m
extra = {
    'test_worker_reads_its_receipt_and_releases_only_consumed_previous_frame',
    'test_worker_capture_failure_reobserves_without_resending_completed_input',
    'test_worker_bad_observations_are_bounded_and_do_not_adopt_old_frame',
}
names = [n for n in unittest.defaultTestLoader.getTestCaseNames(m.RuntimeCompatibilityTests)
         if 'manual' in n or 'takeover_receipt' in n or n in extra]
suite = unittest.TestSuite(m.RuntimeCompatibilityTests(n) for n in names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

修改的两份 Python 文件做语法解析，`git diff --check` 通过。没有将历史 83 项、人工代打或当前活跃对局当成本批实机验证；没有宣称整局自主成功或实测提速。

本机局后验收应按提交 SHA、B-003、run_id、节点回传：原输入／恢复 ID、脱敏完整收据、前后及新 epoch 当前帧、业务预期与实际差额、当前复核推进到了哪一阶段、是否有任何重复输入发布。至少一次覆盖“输入部分或失败 → 同 broker 恢复 → 新 epoch 新帧 → 补必要复核 → 继续准备”，另确认指南领取后新奖励回扫及出战仍有新的单次验收。
