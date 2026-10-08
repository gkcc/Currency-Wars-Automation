# 领奖未决的当前恢复

唯一父：已部署 PR28 `e9e6ca7f4e4d943612d85ed3d28bd483b8224958`。本文件所在候选由最终草稿 PR head 和源码 map 绑定；不含 PR24，不改变 ROOT 生产、READY、GUI 或游戏。

## 当前结论与原版本可用出口

ROOT 反馈：PR28 Windows 原 8/8 通过，真实 RapidOCR 金币 4 为 .9993116856；原独立审查、24 源码＋64 资源/provider 绑定和 check-launch 已通过。本批沿用这些已报证据，没有重跑金币 OCR、GUI/Rust 或旧冻结选择。

这次是另一个 B 类死路：原灰球一笔 completed；4→6、灰球不在、蓝球仍在，但自动效果仍 unknown。原输入后图及第一张只读后图 context_unverified，第二张只读后图才是首个 absence_candidate；两次预算和三帧归档已满，无法取得第二张稳定 absent。具体自然状态来自 ROOT 本轮消息，本环境没有该次完整原始后图或收据，不能说独立重放了自然灰球效果，也不能直接把 context_unverified 归因于动画。

PR28 同 run 的 `pending_reward_step` 按 match 保留原未决。`collect_rewards`、主管单球 ROI、全场 rewards review 都不能覆盖它；`inspect` 不补原自动额度，换 epoch 也不能使它消失。旧提示“当前全场复核”不能解除这一原 pending，本候选改为指出明确的 recover_reward 操作。

### 现在不改源码，可以做什么

已有 takeover、真实 1-1 和当前 `$manualId` 时，原只读命令仍可用；broker paused 也可执行，无须先交接、不需要 checkpoint：

```powershell
$auth = @('--run-dir', $runDir, '--chat-id', $chatId, '--run-token', $runToken)
$runner = 'tools/currency_wars_runner.py'
$inspectId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation inspect --request-id $inspectId
```

它调用原 Worker 的 full observe，再生成新 preparation_strategy。它不是 narrow-rewards CLI，不增加/重置灰球 verification_reads，也不能独自继续蓝球。`recover_overlay` 仅在实际原生 reward_overlay 可用；当前 preparation 不能借它发键。`reviewed_plan` 禁止 context_update，也没有现成灰球恢复字段。

**原正常 stop→新 lease→business_resume 是更重、但无需源码更新的合法继续路径。** 原灰球历史 unknown 会进入新请求的 unresolved_requests；新 run 不复制旧 runtime 的 pending 指针。它支持按当前余额和当前局面继续蓝球，不要求伪报灰球成功：

```powershell
& $py -B -X utf8 $runner stop @auth
# 只有原 Worker/broker 的 PID+creation 实际 exited/absent、持久来源完整后：
& $py -B -X utf8 $runner start --chat-id $chatId --profile --continue-matches --max-matches 20 --max-seconds $originalAuthorizedMaxSeconds
```

不能以删除文件、只换 epoch、复制 token/旧请求或手填隐藏 business-resume-json 模拟它。新 business_resume 必须使用新 q 的 proof、previous/current chat/run、match，完整列齐 `origin_run_id:request_id`，`prior_outcomes_remain_unknown=true`、`remaining_policy=fresh_reviews_and_current_balance`，并真正实读当前 stage/coins/level/XP/deployed/roster/selected_strategy。当前若仍同局，disposition=same_match。旧灰 identity 仅在新 q 实际包含时才列；不能硬填成唯一 unknown。

本次没有执行这条自然 stop/start 链，也不要求 ROOT 为只读诊断先停止 controller。它是当前可选的原出口；同 Worker 的轻量出口需要下面候选。

## 最小实现与没有改变的历史

只改原清单内三份生产 Python：

| 文件 | 改动 |
| --- | --- |
| currency_wars_runner.py | 增加同 Worker `recover_reward`；原两次核效前各 .25/.5 秒可停止等待；pending 查询识别合法历史恢复记录 |
| currency_wars_manual_steps.py | 原 mailbox 接受明确恢复 proof、输出独立结果；验证原收据/不可变帧/完整水位与持久来源；相同 job 不重做 |
| currency_wars_manual_stage.py | 原历史业务 pending 仍保留；规划阻塞查询认同一份合法恢复记录，避免下个节点重新卡旧灰球 |

恢复只接受一个已发布、完整 completed 的蓝/灰球原步骤，且是当前同局同节点原 unknown。它不接受原交付 unknown、部分输入、收店或腾位等其他效果，也不从净金币变化推导物品/费用。原自动输入之后可以有已核只读收据和原 pause-id resume 控制交接；出现其他物理输入或未知交付继续拒绝。

原灰 `status=pending/outcome=unknown/verification_reads=2/verification_frames=3/absence_candidate` 以及原 request/before PNG/receipt **完全不改**。自动核效不增加第三次读取，不回退预算，仍要求两张独立稳定 absent；.25/.5 秒只是有上限的本地等待，最多增加 .75 秒，不能保证自然页面因此稳定。等待被 stop、交接或总期限中断时，已扣额度不退；原输入不重发。`reward_effect_wait` 单独记录规则等待，不把它计作实测游戏动画。

新恢复的 proof 来自 ROOT 当前实际判断，source=supervising_agent，不能改成 native_reader。生产还会独立核：

1. 当前 request/page/stage/match/epoch/checkpoint/deadline、ManualPhase、原 PNG/capture/frame 及原完整收据来源；不能把旧 request 改 kind。当前 reward_result 与 preparation_strategy 均可用，且 phase=rewards。
2. ROOT 所见当前图的 native 金币可靠且不低于原输入前；生产 target_effect 对原目标在此图上已得到 absent。
3. 同一 Entry observation/input lease 内只新增 **一笔 observe、零输入**。原读取 scope=rewards；须新的不可变 capture/frame，完整水位恰好多这一笔，当前币值与 ROOT 所见图相同。
4. 新图对原目标也须 absent，两份当前图通过原 stable_absence。页面、遮挡、目标还在/移动、金币缺失或变动均拒绝。没有放宽视觉阈值或把 ROOT 的 true 当本地识别成功。

通过后另写 `reward-continuation-<原step>.json`。它绑定原 pending canonical SHA、原 receipt SHA、原 manual job/checkpoint 和两份当前帧，语义是“历史效果仍未知，允许按当前状态重新规划”。它没有新动作、坐标授权、预算、阶段完成或出战批准。后续 epoch/节点可以读取这项同 run/match 的历史处置事实，但所有新动作仍须现有新帧守卫；不会再拿旧灰 ROI 检查未来可能同位置生成的新球。

`pending_business_requests` 和 `manual_stage.business_pending` 仍列原灰 unknown。只有“是否阻止当前重新规划”的查询会辨认有效 continuation。缺原 archive、PNG、receipt、mailbox、完整交付/水位，或任一来源摘要改变，就重新保留阻塞；旧灰记录不被新蓝 runtime 指针覆盖为成功。

若恢复已只读完成、但后续生成回复失败，旧 job 必须已确定 refused、零输入、完整交付与水位、无未知收据，才允许先保留该失败报告，然后由 **新 inspect 帧＋新 request/capture/frame** 发起另一次明确当前审查。旧 proof 换 job ID、returned/running 或交付未明的恢复均不能重做。坏 JSON/归档错误会保留原操作错误及次级证据错误，不在 finally 覆盖原异常；来源损坏不授权。

## 候选的精确手操调用

仅在 ROOT 自行完成候选验收/停止旧 controller/源码绑定/原 start 后可用。升级若已经换 lease 并经 business_resume 承接，则旧灰只作为历史 unknown：**不要把它拷回新 runtime 来调用本操作**。新 run 可直接走原 native rewards。以下用于同一 run 内自然再次出现原 pending 的轻量恢复。

### 1. 当前接管与 checkpoint

沿原 takeover 取得当前 `$manualId`，保留同一真实 stage。已有 pending rewards checkpoint 直接用，不再创建第二个；缺少时才调用：

```powershell
$cp = (& $py -B -X utf8 $runner manual-checkpoint @auth --manual-id $manualId --phase rewards | ConvertFrom-Json)
$checkpointId = $cp.checkpoint_id
```

checkpoint 的只读前图不会消耗原灰自动核效额度。新恢复本身允许 broker paused，不需为恢复先做物理交接。若当前 q 已过期、缺完整来源或未呈现稳定当前状态，用上面的 inspect 取得新 q；不要修改旧 deadline。

### 2. ROOT 核当前状态，提交唯一恢复 proof

从实际 `runner-state.json.decision_request` 取得 q，不能用删掉了 rows 的展示投影补造原 observation。以下 true 仅在 ROOT 确认当前原目标已不在、同意按当前剩余球继续后填写；代码仍会作前述原生像素/来源校验。

```powershell
$s = Get-Content (Join-Path $runDir 'runner-state.json') -Raw | ConvertFrom-Json
$q = $s.decision_request
$o = $q.observation
$p = Get-Content (Join-Path $runDir 'reward-step.json') -Raw | ConvertFrom-Json
$proof = [ordered]@{
    source = 'supervising_agent'
    request_id = $q.request_id
    snapshot_id = $q.snapshot_id
    capture_request_id = $o.capture_request_id
    frame_id = $o.frame_id
    page = $o.page
    match_id = $q.match_id
    stage = $o.fields.stage
    resume_epoch = $q.resume_epoch
    checkpoint_id = $checkpointId
    deadline_at = $q.deadline_at
    reward_step_id = $p.step_id
    input_request_id = $p.request_id
    prior_outcome_remains_unknown = $true
    target_now_absent = $true
    continue_current_rewards = $true
    findings = $currentActualFindings
}
# $currentActualFindings 是本次实读说明；不要复制本例当未看的画面结论。
$recoveryFile = Join-Path $localEvidenceDir 'reward-recovery-reply.json'
[IO.File]::WriteAllText($recoveryFile, (@{reward_recovery=$proof} | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
$recoveryId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation recover_reward --checkpoint-id $checkpointId --reply-file $recoveryFile --request-id $recoveryId
```

这是恢复操作的完整 17 字段 proof；无 actions、经济/金币补值或 hidden flag。原 manual-step 限定等待最多25秒；queued/running 时用同一 `$recoveryId` 和完全相同参数读取原结果，不产生新 ID。

必须核实际结果：`status=returned`，`result.receipt_watermark_verified=true`、`receipt_delivery_verified=true`、`input_receipt_ids=[]`，无 evidence/finalization/prior unknown 错误；`result.reward_recovery.continuation_allowed=true`。

独立 `reward_recovery` 出口还给出 `prior_outcome_remains_unknown=true`、原 request/step ID、当前 snapshot/coins、record_file/record_sha256。相邻 `result.reward_step` 仍诚实显示原 pending。顶层 pending 不再把这个已处置的历史效果当正在运行的恢复，但 **returned 不等于原灰成功或全场领空**。

### 3. 用原循环继续当前蓝球

如果 broker 仍 paused，在恢复成功之后、物理领奖之前才做原显式同 broker 交接：

```powershell
$handoffId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 tools/currency_wars_broker_entry.py resume @auth --handoff --request-id $handoffId
```

核原ID的真实成功，Worker manual intent 不被此操作消费。若此前已经交接，不重复。随后原 native rewards 一次调用，不把旧灰坐标放入 payload：

```powershell
$collectId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation collect_rewards --checkpoint-id $checkpointId --request-id $collectId
```

它重新 observe 当前页/币值/球，每个目标一笔原收据和核效。灰历史状态不改，蓝有自己的新 request/step。单球 verified 仍 `all_rewards_cleared=null`，购买/D/F/出战不能因此越过 rewards。蓝也未知时会保留蓝自己的新 pending；不能复用灰恢复给蓝。

### 4. 当前全场领空，再续原准备链

ROOT 实际全场确认领空后，manual-result 只保存真实人工链：`$phaseReviewFile` 包含当前实际 `{reviewer, phase:'rewards', stage, completed:true, outcome:'success', all_claimed:true, rescanned_after_claim:true, findings}`。`$inputIdsJson` 必须列齐 checkpoint **之后**所有非 zero_input 原收据，包括之后发生的 control handoff；若灰输入在 checkpoint 之前，就不列入该清单。不是简单只拼蓝 ID，也不凭 completed 猜整段结果。

```powershell
& $py -B -X utf8 $runner manual-result @auth --checkpoint-id $checkpointId --input-receipt-ids-json $inputIdsJson --reply-file $phaseReviewFile
$current = (& $py -B -X utf8 $runner status @auth | ConvertFrom-Json)
$guardJson = $current.state.resume_guard | ConvertTo-Json -Depth 20 -Compress
& $py -B -X utf8 $runner resume @auth --handoff --resume-guard-json $guardJson
```

原 CAS 不匹配时停止该次恢复，不替换 guard 重发未知输入。成功后新 epoch 的完整请求仍须 ROOT 当前全场复核，用原 `decide --reply-file`、唯一 `finish_preparation_review` 和当前 `context_update.preparation_review`；完整 JSON 结构复用父交接 `REWARD_HUD_CLOSURE.md` 的“实际结果出口与全场关闭”。不能在 manual-step reviewed_plan 中塞 context_update，也不继承旧 all_claimed。随后阶段为 startup_guide，仍不能直接经济/出战。

## 离线验收与本机入口

```powershell
python -B -X utf8 tools/replay_currency_wars_reward_recovery.py --output reward-recovery-windows.json
```

默认固定 8 项新选择：预算耗尽→当前恢复→蓝一次→原 ManualPhase/新 epoch/当前全场复核；proof 各来源/期限否决；原交付未知先拒 capture；变页、金币不足/未知、目标仍在/移动/遮挡否决；来源及结果完整性、重复恢复；stop/期限优先；原控制 handoff＋paused 只读恢复；失败恢复报告保留后仅全新 proof 重审。

全部通过真实生产 Worker/Entry/mailbox/ManualPhase/target_effect/stable_absence 的惰性协议。原生球 crop 复用旧公开 blob；完整 canvas、OCR 行、4→6、灰/蓝移除及控制传输是声明协议，**不是自然后继图，也不是 Windows/游戏验收**。未调用 OCR 引擎、未新增/下载模板。当前同 run 恢复后再 resume 的协议有覆盖；“先换 Worker epoch、再恢复旧步骤”的单跳分支仅复用旧 CAS 校验，本批未新增该自然/协议覆盖。

冻结报告 `reward-recovery/acceptance.json` 记录精确结果、33 项源码、12 项原素材及加载闭包；`tested_checkout_head` 允许为父 SHA 或 archive 中的 null，源码 byte map 才是测试绑定，不回填测试 head。本机完整 24 源码＋64 私有/公开资源/provider 绑定仍由 ROOT 独立执行；不导出原57私有素材。

本环境固定选择实际 **8/8 通过，0 failure/error/skip，68.581263047 秒**。33 项源码测前后 source-set 均为 `5a47d188631f38b6b05a052d8937ea5d97de661c4feec797cd8d00f6b105799f`，12 项素材不变，实际加载源码闭包缺项 `[]`。这是 Linux 离线协议结果；Windows 与实际游戏恢复待 ROOT 验收。

| 修改源码 | SHA-256 |
| --- | --- |
| tools/currency_wars_runner.py | 7a0b1f9d126205fa54bd3fb5e3db40dbadf429b011574f4a9ca735429d19179a |
| tools/currency_wars_manual_steps.py | 4a6cebb59b9abb157e4e454201964d137d050236a88a512afe6709af0f67a34a |
| tools/currency_wars_manual_stage.py | 1f0ac7cf85896b5e8c92b625de494ca5de5269e8aa8d0574e4b79641c6ede5ef |
| tools/replay_currency_wars_reward_recovery.py | c8e21ef0d01820c30a44733a10b7e643142832aebaa881ddf7c4b3a153b49c7f |
| tools/test_currency_wars_reward_recovery.py | 51f58c3738bd650ad758b2f741b8be00b64fc3976923ace8f0410ba0811de6ab |

本次源码 manifest、GUI/Rust、依赖与生产资源不变。ROOT 已为 PR27 清单重建的 GUI 可复用；仍须重新审查新 Python 字节、生成对应 READY 绑定并运行原 check-launch，不能继承旧源码 hash。没有声称候选已部署、自然 pending 已解除或整局提速。

## 往返开销：只推进两项

ROOT 报的未完 1-1 窗口 507.906 秒，supervisor 314.983＋manual 138.048＝453.031 秒，约89.2%。观察28.076、perception9.008、OCR14.464及36次observe/37次perception/109个OCR span不能简单相加；主管/手操等待混有ROOT、宿主和Pro空档，不能改名为纯模型时间。它支持优先减少异常兜圈和机械请求，不支持整局速度倍率。

1. **用一次当前经济授权吃满原循环，不改经济引擎。** PR19 已有 `accept_economy_plan → advance_economy → reconcile_economy → finish_local_economy`。购买缺口、免费刷新、预算内付费搜牌、停止后XP是本地逐笔调度；每 tick 最多8笔，达到8笔不是自动ask，下一tick继续。成功逐D/F不需ROOT回复。若当前同一帧已真正完成inventory_cleanup并足够绑定预算，普通decide可依次提交context_update.preparation_review、economy_plan和唯一finish_preparation_review，省一次机械预算登记。必须用有序对象保留该消费顺序，不能走禁止context_update的manual reviewed_plan。此用法来自源码审计，本批没有重跑经济回放或自然通过声明。
2. **失败停在原逻辑步骤，由一次明确当前恢复续接。** 本候选给奖励实现这个出口，避免“inspect→原pending→重复接管”循环。未来通用异常去重应按原step/request/原因及新证据变化定义，不能仅按动画改变的snapshot重问；本批不扩其他动作框架。六阶段是业务关闭条件，不是六次固定主管调用；全场领奖、当前指南/待售、阵容装备和ROOT每次出战仍各有必要来源。当前battle_acceptance已有可靠前阶段时，可由同一普通reply提交当前验收＋唯一出战动作；原policy若要求额外approve-battle仍保留。此处也是源码可达建议，不是本批验收端点。

主源借鉴仅取规则，不引入服务/框架：

- Playwright 官方 actionability 把唯一目标、可见、稳定、未遮挡、启用分开；断言等待后置条件。借鉴局部条件＋有界等待，不把整页背景静止当目标稳定。https://playwright.dev/docs/actionability ；GitHub 主源 https://github.com/microsoft/playwright/blob/main/docs/src/actionability.md
- Temporal 官方 Retry Policy 支持次数/总期限和不可重试错误，并明确重复整个工作流不能修复未变的永久条件。只读暂时性失败可有界等待；游戏输入不重发；来源/策略问题交给明确分支，不照搬其默认无限 Activity retry。https://docs.temporal.io/encyclopedia/retry-policies ；GitHub 主源 https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx

后续自然验收使用已有profile：经济窗口从 economic_budget_verified 到本地 economy 完成或首个异常，记录同预算成功动作数、ROOT逐笔回传数（目标0）、费用和pending；整节点从首稳定准备帧到ROOT当前出战验收后的真实发布，记录总时长、六阶段、返回原因、同一异常的工具往返数、原input重发数（必须0）。只把完整且相同端点窗口比较；未完成节点和12秒批次不计算整局提速。
