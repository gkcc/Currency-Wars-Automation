# B-005：经济依赖、统一预算与 F 经验

发布父提交：`c58f6a988194b25cef5c22f710f758692f864a3d`（已含 B-003、B-004）；准备分支：`fix/economy-dependencies-20261007`。最初在 `beaea343ee7edf29bcba70ba8351a7dc462a83da` 开发，发布前已在实际叠加代码上组合验收。当前局核心仍冻结，本文不是安装或本局脚本自主成功记录。

用户新实机证据已证明当前界面经验键为 F（70）：三次输入后金币 66→54、7 级 40/52→8 级 0/72。它证明这次总变化，不能把以后“三次已发送”当作三次成功。此批统一 `buy_xp`、购买经验文字、备战/商店 F、动作失效、计划/输入守卫；准备/商店 E（69）明确拒绝。大世界 F 仍是原交互；没有新增控制器、修改 broker 输入实现或绕过暂停/CAS/epoch。

## 已接入的执行顺序

Worker 保留已有准备复核：全部奖励/选择清空 → 第二入口创业指南与领奖 → 无用牌清理。只有这些完成后，统一经济链才可执行：

1. 先买**本次明确目标中的当前缺口**。目标单槽仍用现有 `purchase_slot` 验实名、实价、原/新帧和坐标；其他槽未知不阻止这笔已核购买。
2. 在当前商店检查完成后，先用实际剩余免费刷新，每次刷新后重核缺口。免费次数未知不能先买经验。
3. 处理明确付费搜牌预算及停止条件。预算可以为 0，但必须写理由。预算耗尽、次数已到、目标已购齐、购牌留资不足等实际停止条件先判；不会因为攻略阶段未知而把已停止的搜牌重新变成待执行。
4. 对**仍拟执行**的付费刷新，复用当前有效攻略/应用缓存及阶段规则。当前阶段明确禁刷时停止；阶段未知且尚未满足其他停止条件时回传。免费刷新按前面的新顺序处理。
5. 以上解决后才决定 F 经验。每笔读实际价格/金币、等级和经验差额；按目标等级或剩余预算停止。
6. 经济完成后仍需上场、装备、炉与特权卡处理，以及 ROOT 当前新帧的一次出战验收。经济计划不能代替这些复核。

`advance_economy` 每 tick 最多执行 8 笔，每笔仍经同一 `command` → Entry → broker 并接收新帧。主管给一次有界预算后，正常的逐笔资源变化可在本地继续；零、部分或未知效果立即停止该经济链并回传。每笔仍保留当前 Perception 路径；这批没有宣称已消除所有全屏 OCR 或给出实测提速比例。

## 当前请求的事实来源

基准代码实际没有生成足够的原生 `semantic.xp` / `semantic.reroll` 字段。本批没有伪造这些字段或提升 `fully_read` / confidence，而是新增明确的 `context_update.economy_plan` 来源：

- `proof` 使用当前决策请求的 `observed_screen`、`snapshot_id`、`evidence_file`、`resume_epoch`，绑定本局、本节点和本请求原始 PNG。
- `value.fields` 支持 `coins`、`level`、`xp`、`xp_cost`、`xp_gain`、`free_refreshes`、`refresh_cost` 七个键。已读值写为 `{"value":实际值,"bounds":[左,上,右,下]}`；`xp` 的值为 `[当前经验,升级所需经验]`。未见/隐藏的值写 `null` 或省略，保留为 unknown，不得猜。
- bounds 是本张 1920×1080 原图里经主管确认为相应字段的完整数字区。金币固定使用现有 `GOLD_HUD = [1613,892,1687,950]`；其他区域由该请求真实界面提供，本文不提供虚构坐标。
- 新帧同区域像素完全一致时，沿用这次主管读数并明确记录 `supervisor_read_with_identical_current_roi`。像素变化时，只调用现有 OCR 引擎对该数字区域读取；严格格式且 confidence ≥0.90 才使用，否则保持未知。
- 原生金币仍须通过原有 HUD、金币图标比例及 confidence 检查。与独立经济读数冲突时保持未知，不以某个来源覆盖冲突。
- 买已核单槽只要求其实际价格及金币；免费 D 不要求当时不可见的付费价格；F 需要等级、经验、费用、增量和免费次数。刷新与 F 的商店依赖仍必须完整。经验预算为 0、已达实际目标等级或余额不足一笔时，不为完成经济复核要求不存在的经验控件；金币和免费次数仍须实读。

这是显式主管读图的可执行桥接，不是宣称已有通用数字控件定位器。新布局导致 ROI 不再适用时，由当前请求重新绑定；不会把旧读数写入原生语义事实。

## 提交一份有界计划

通过现有决策回复提交 `context_update.economy_plan = {"proof":当前请求proof,"value":计划}`，配单独的 `finish_preparation_review` 无输入动作即可登记；也可以配该预算允许的单笔经济动作。预算登记不会把准备阶段自动标为完成。

计划的其余必需字段如下；下面的 66 金/12 经验预算是说明结构的例子，实操必须使用当前读数和实际节点：

```json
{
  "schema": "currency-wars-economy/v1",
  "reviewer": "supervising_agent",
  "revision": 1,
  "stage": "3-1",
  "mode": "标准博弈",
  "reason": "当前缺口与免费次数已核，无付费搜牌目标，预算12金达到8级",
  "shop_reviewed": true,
  "purchase_only_targets": true,
  "targets": [],
  "budget": {"purchase": 0, "refresh": 0, "experience": 12},
  "paid_search": {
    "max_refreshes": 0,
    "purchase_reserve": 0,
    "targets": [],
    "reason": "本次付费预算0，当前战力与攻略目标无需继续搜牌",
    "stop_conditions": ["budget_exhausted", "max_refreshes", "purchase_reserve", "target_acquired"]
  },
  "experience": {"target_level": 8, "critical": false, "reason": "增加已明确需要的上场位置"},
  "reserve": {"coins": 50, "critical_allowance": 0, "reason": "标准常规满血连胜储备"},
  "fields": {
    "coins": null, "level": null, "xp": null, "xp_cost": null,
    "xp_gain": null, "free_refreshes": null, "refresh_cost": null
  }
}
```

上例 `fields=null` 是待填位置，不是可执行读数。每个购买目标格式为 `{"name":"实读实名","copies":1,"critical":false,"reason":"当前升星/羁绊/同场或攻略缺口依据"}`。`copies` 是**该节点累计最多购买的基础张数**，不是当前持有张数、星级或“从最新回复再买几张”。主管须依据现有阵容、策略同场条件、升星/羁绊和攻略选择目标；代码不把当前商店推荐标记当作足够的购牌理由。

正数付费预算须有目标、最大次数和搜到目标后的购牌留资。留资不能超过购买预算，并参考现有角色基本费用；未知费用按现有商店 1–5 费范围预留 5。真正成交仍以新帧单槽价格为准。`paid_search.stop_reason` 可记录当前明确改变的停止决定，修改该政策需增加修订号。

`budget` 三个额度是同节点累计上限，更新计划须保留已经核实的实花。三者剩余额度合计不能重复使用同一笔钱，也不能因写了很大的 `critical_allowance` 创造不存在的金币。角色目标、付费停止条件、储备或人口目标变化也须增加 revision。常规标准储备 50；关键缺口可明确给出动用储备额度，并只对标记 critical 的购买/经验使用；付费刷新的留资与储备不可被该例外绕过。超频按无利息储备 0 处理。

降低标准储备需要当前原图上可核的实际剩余结息回合证据 `reserve.remaining_interest_rounds` / `rounds_evidence`，包括同请求 proof、实际数字、原文 reading 与 bounds；不能从 `3-x` 字符串推出最后备战。剩余 0 时储备必须为 0。本批没有引入未经版本核实的利息收益计算。

## 收据、台账与恢复

`records/economy-<match-stage-hash>.json` 通过现有原子 `write_json` 保存本 run/match/stage 预算、实际支出、已核购买数和 pending。发布前即保存实际请求 ID 与原字段、预算和收据水位；返回后先保存真实 broker 回执，再处理图像。epoch 变化使数值/目标绑定失效，**不清零实花或 pending**。

- 成功：精确金额变化加对应经验、免费次数或目标槽位变化，才推进台账。
- 零效果：状态与钱均无变化，停止并要求当前明确剩余计划；不因“没效果”自动重发。
- 部分：金额或对应状态变化与本笔预期不一致，保持待验，必须核真实差额后再定剩余计划。
- 未知：字段/后帧缺失或结果不能归因，保持原请求。后续只补观察，不补发原输入。

主管可在新 `economy_result` 请求里提供当前真实字段并写 `resolve_request_id`，对账原请求。原后帧不存在或仍是动画时，只有原收据身份正确，且水位后没有其他改变状态的输入，才允许用更新的只读帧补证。纯 observe 的截帧失败不会被当成输入；handoff 观察和未知控制交接不被视为无变化。后续回执统一使用 B-003 的 `manual_receipt_state`，未知输入或矛盾的物理输入尝试元数据一律拒绝；不能因动作名称是 observe 就跳过分类。跨 epoch 补证使用 B-003 的 `verified_resume_event` 精确 CAS/前后 epoch 事件，只允许本次单跳恢复。本公开分支依赖父提交中的 B-003，不复制另一套恢复协议。

预算外的库存修改会使经济计划绑定失效，要求重新读当前缺口，同时保留已花钱和已购数。奖励/道具或预算外来源改变了星级时，主管需更新实际目标；本批没有新增通用满星识别器。独立库存溢出、为领奖腾位和人口分离继续由 B-004 守卫处理。

## 聚焦验证与真实边界

执行：

```sh
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python -m unittest test_currency_wars_economy.EconomyTests -v
```

本批 15 项经济聚焦与 8 项既有相关回归独立验收通过。根再在同一最终源码上运行这 23 项及 5 项 B-003/B-004 接口回归，共 **28/28 PASS，0 skip**；重叠测试不相加。精确选择、耗时与源码摘要见 [B005_VERIFICATION.json](B005_VERIFICATION.json)。Python AST、暂存 diff、唯一 broker 的双处固定 SHA，以及 GUI 的 15 个核心文件路径全部通过检查；未执行新的 Windows 构建。

测试使用合成数字图与现有空输入 Worker/Entry fixture。覆盖 F/E/大世界语义、购买→免费→付费→经验依赖、单槽已知而其他槽未知、实际搜牌停止优先、储备/关键缺口/总预算、ROI 同像素与低置信未知、原生金币冲突、实际零/部分/未知、三笔 F 逐帧跨等级、同节点持久实花、失败补观察和后续其他输入 fence。新增真实 B-003 组合路径调用 `latch_manual`、`explicit_resume`、Entry 与恢复事件核验，证明错误 CAS 不发布，正确恢复后使用新 epoch、新帧、新 proof 只读对账原 F；累计实花为 4，全程只有一笔 F，原收据与旧审阅保持不变，没有手造成功恢复事件。

组合审查实际复现并修正了一个阻断：纯 observe 标签曾短路 B-003 对矛盾 `input_attempted/attempted_actions` 的 unknown 分类。失败与完整完成两种矛盾回执现在均拒绝对账，合法失败观察仍可补证。它们是既有测试内的两个 subtest，不另加测试数。以上仍不验证 Windows 键位、实际界面 ROI、全局自主打法或速度提升。

旧 `test_local_runtime_compatibility` 在本 Linux 环境实跑 38 项，其中 29 项通过，9 项因 Windows `mbcs` / `CREATE_NO_WINDOW` / 进程身份能力不可用而报错；不能标为全套通过。本批 Python 编译及 diff 检查另行执行。

尚待本机实机验收：当前请求数值区域绑定、真实免费 D、当前缺口单槽购买、付费留资/停止、F 每笔差额与目标停止、暂停后回传、ROOT 单次出战验收。末轮放开储备目前要求实际可核原文；如果当前 UI 不提供这类信息，仍需补可验证版本规则/真实阶段证据，不能宣称已自动把最后备战的钱花完。持久账本在同 run 和同 active_match_id 下可重载；完整 Worker 重建后的同局业务 ID 延续仍属于 B-007，本文不宣称跨租期续局已经通过。
