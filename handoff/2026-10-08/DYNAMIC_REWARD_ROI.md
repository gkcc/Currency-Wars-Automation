# 动态奖励定位与当前单球救援

唯一父为 PR26 `5cf3d9b3ae693d5df670203352d0d55bae3d19c5`。本文件所在提交为候选；最终完整 SHA 由草稿 PR 和交付回复给出。PR24、GUI、经济策略、运行期限不在本批。没有操作 ROOT 游戏、broker、生产源码或 READY。

## 修复与边界

旧蓝球最高全块 NCC `.780296862/.864655733/.865824819` 位于旧 `.75–.90` 拒绝区，虽然颜色通过仍清空 targets。此原因已定位。现在用原 OpenCV 在整个受支持区域 `[1320,230,1660,500]` 搜索圆形前景；保留原 q01 外观，新增有源蓝／灰裁片，未增加任何随机位置 profile。

输入资格同时检查环带 NCC≥.96、内部 NCC≥.90、4×4 前景分区 RGB 均差≤32及蓝／灰颜色与亮度。源图位置不作为搜索位置。旧弱参考的拒绝诊断保留在 `candidate_diagnostics`；同一个球的强参考可以成立，其他独立可疑目标仍阻止自动继续。缺失／篡改外观资源保持 unknown。

三次自然观察的蓝／灰均被定位，框与原标注一致。蓝环带 `1/.983664/.986818`，内部 `1/.938307/.946911`；灰环带 `1/.979582/.983347`，内部 `1/.979510/.978870`。**initial 是校准样本，另两张也是同球同位置的时间复核，不是自然换位置或尺度泛化。** 所有完整生产页、输入资格与领奖后效仍须新请求验证。

生产仅改4个文件：

| 文件 | 变化及结果 |
|---|---|
| `currency_wars_rewards.py` | 蓝／灰动态定位；独立页面锚点与单目标暴露比较；单目标消失、移动／覆盖否决和稳定后图检查。|
| `currency_wars_manual_steps.py` | 原 collect_rewards 增加显式单 ROI，复用邮箱与 ManualPhase；先对账原未知收据；回传真实业务 pending。|
| `currency_wars_runner.py` | 原 Worker／Entry 执行一个目标；原收据和视觉结果分开核；每笔最多2次后继只读，共用持久预算；灰球也使库存验收失效。|
| `currency_wars_runtime_sources.json` | 原24源码闭包保留；奖励素材新增2 PNG＋1来源清单，进入原统一字节验证。|

没有新输入者。自动路径保留每批最多8笔、每笔只有1个物理目标；逐笔新帧重定位。手操标注路径严格只执行1枚球。球移动、页／布局／可见覆盖变化时废弃未发布坐标。读数不够、资源缺失不补字段。当前金币 HUD 必须可核，免费领奖后的金币未知／下降不能记作0花费。

## 原始来源 map

实际 fetch 的证据提交：`4edab30f9da025953b92f306070dcc2cf0af78ae`，分支 `root-environment-guard-evidence-20261008`。

| 资料 | 用途 |
|---|---|
| `ROOT_NATIVE_REWARDS/diagnosis.json` | 原 full PNG SHA、9份裁片摘要／边界、原失败分数、独立主管标注；原字节保留。|
| initial/manual_before/fresh_worker 三张 `*-scan.png` | 未修改340×270原生扫描；候选复用同一公开 Git blob。|
| `tools/reward_resources/initial-blue.png` | 72×73校准裁片，SHA `d362f664af664e6864a55193f20d5d9faadf5122c0eb0651b154b4c079aa55fd`。|
| `tools/reward_resources/initial-gray.png` | 47×46校准裁片，SHA `72d266178de25affc73cc0c1d5fd68a047e8a485258b76d394a3bfc0c98d69fd`。|
| `tools/reward_resources/native-orbs-sources.json` | 两份素材的证据提交、原路径、完整原帧SHA、原ROI、校准限定。|
| `dynamic-rewards/acceptance.json` | 完整源码 map、测试支持文件、输入素材摘要、环境、聚焦结果和紧凑消费者指标。|

三个完整原帧SHA分别为 `29206504af8d05cb2780f23660b32fd540bdce24390682b1930130b57c8552f1`、`bb9fbd80669e0818aa41135401c12f285df50a3ed2bed94e77e76b32b48b8dbd`、`378fac4756e60846d847d196412b78f1eb886854457117ebaccc65fb79bf6af9`。没有取得它们的完整PNG／完整当前观察；不以坐标容器或协议声明补造历史帧身份。未上传原57素材、UID、owner/token。

## Windows 聚焦复核与切换

在完整候选的独立目录运行：

```powershell
python -B -X utf8 tools/replay_currency_wars_reward_roi.py --output reward-roi-windows.json
```

driver仅运行本批3个新类共14项。Linux Python3.12.14 的统一冻结运行：14 passed、0 failure/error/skip，32.997295681秒。32项源码测前后 source-set 为 `fd642112b0a2961c4dd78a08cd0c2b0903ab037feee0d23779c78795777703ef`，12份选定素材也未改变；实际加载源码闭包无遗漏。

完整源码与素材测前后摘要写入 JSON，逐项日志写同名 `.tests.txt`；任何失败保留 traceback，exit非0。归档没有.git时 `tested_checkout_head=null` 合法，字节由来源map绑定；不能伪填测试head。这不是 Windows 或部署验收。

先核独立结果，再沿原正常 stop、退出身份和 owned runtime 清理流程切换。不要直接覆盖运行中代码。候选为 PR26 单父，已在 PR26 的本机可用 `git merge --ff-only <候选完整SHA>`；不要把本地未保存修改 reset 掉。**后续实机纠正：PR27修改的资源清单由GUI的`include_str!`嵌入，须运行原Build-GUI增量重建并记录新的BUILD_STATE，不能复用PR26旧构建。** 24源码＋**实际**资源/provider完整绑定必须重新计算，READY 不继承旧hash。原61资源若未增减，本批新增3份公开奖励文件后应为64，最终以本机枚举为准。公开环境缺私有商店根不能推到本机。后续候选若GUI来源、清单与依赖字节均未变，则不因已有清单中的Python源码变化重复编译GUI，仍须独立审查新运行字节。

沿原 `python -B -X utf8 tools/check_install.py --check-launch` 和已授权的正常 start／business_resume 使用新租期与新epoch。本批不恢复旧请求，不改变连续整局授权，也不要求 ROOT 等 PR24。

## 精确的同 broker 调用

以下变量使用 ROOT 当前已认证运行的本机值；不要把 owner/token 上传。`$py` 为原已审 Python，`$runDir/$chatId/$runToken` 来自当前原生运行。已有本节点 pending rewards checkpoint 可复用；stage变更先用原 `manual-stage`，不可伪填1-1。

```powershell
$auth = @('--run-dir', $runDir, '--chat-id', $chatId, '--run-token', $runToken)
$runner = 'tools/currency_wars_runner.py'
```

1. 如尚未处于 Worker manual，沿原 `takeover`，从真实返回／当前 `runner-manual.json` 取 `$manualId`。若已有 manual，使用它。**物理 broker 仍 paused 时，先做原 Entry 的独立 resume**：

```powershell
$handoffId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 tools/currency_wars_broker_entry.py resume @auth --handoff --request-id $handoffId
```

这是 `kind=resume`、请求不携带领奖／经济动作、带原 Entry 实际 pause-id CAS 的显式交接。它保留 Worker manual intent。不是 `submit observe --handoff`；后者仍受 runner-owned raw 守卫拒绝。仅在该原ID `ok/resumed` 已核、broker foreground/health满足后排物理步骤。queued超时不是游戏拒绝；不删锁、不重发已发布请求。

2. 若没有本节点奖励 checkpoint，创建并保存返回的ID：

```powershell
$cp = (& $py -B -X utf8 $runner manual-checkpoint @auth --manual-id $manualId --phase rewards | ConvertFrom-Json)
$checkpointId = $cp.checkpoint_id
```

3. 先取当前 Worker 完整请求。若目前没有 `preparation_strategy`，或请求过期、换页、源图缺失，使用原只读 inspect：

```powershell
$inspectId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation inspect --request-id $inspectId
```

inspect需真实准备节点绑定。排队/运行中仅以同ID查询结果，不创建等价重试。然后读取 `runner-state.json` 的 `decision_request`，ROOT 看该请求的 `original_png`，只标注其中一枚完整暴露的当前球。

4. 以下例子**仅在新请求当前图仍支持这枚蓝球时使用**；旧标注坐标不自动取得新资格。灰球例子改 `kind=gray_orb`、bounds `[1463,303,1510,349]`、center `[1486,326]`，也必须来自新请求实读。

```powershell
$s = Get-Content -Raw -LiteralPath (Join-Path $runDir 'runner-state.json') | ConvertFrom-Json
$q = $s.decision_request
$o = $q.observation
$annotation = @{ reward_roi = @{
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
  kind = 'blue_orb'
  bounds = @(1333,393,1405,466)
  center = @(1369,429)
  findings = 'ROOT已查看本请求原PNG：此单球完整暴露，非商店卡槽、非覆盖层；这是主管标注。'
}}
$annotationFile = Join-Path $runDir 'root-one-reward-roi.json'
[IO.File]::WriteAllText($annotationFile, ($annotation | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
$stepId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation collect_rewards --checkpoint-id $checkpointId --request-id $stepId --reply-file $annotationFile
```

`reward_roi`必须恰含上面15个字段；不能放原生true、控制器flag、任意文件路径、第二个目标或附带D/F。中心须为bbox整数中点。request/snapshot/frame/capture/page/match/stage/epoch/checkpoint/deadline全部精确核对；PNG从已有Worker请求的 `original_png`读取并复算SHA。手操作业最长60秒，且不超过原请求deadline。claim与发布前再次核验。

同 `$stepId`、同payload重复调用只读原结果。换ID、改说明或延长deadline不能复用同一意图。下一枚标注要当前**新请求／新图**；原业务pending时不能提交第二球。

5. 如原生动态扫描已确定，可不用标注，直接让原 Worker 循环：

```powershell
$stepId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation collect_rewards --checkpoint-id $checkpointId --request-id $stepId
```

这仍是逐笔执行＋核效，最多8笔，并非把8个旧坐标预先发出。若窗口中只有一球确定、其他区域unknown，显式单 ROI可只处理这一球，原生unknown不会改成完整读取。

## 结果从哪里读，何时继续

- CLI `result.reward_step`：`step_id/status/outcome/request_id/source/after_snapshot_id/verification_reads/pending/record_file`。`status=verified`且`outcome=one_visible_orb_removed`仅表示这枚球在受检区域中不再可见。
- CLI `result.input_receipt_ids`与`receipt_states`：原请求逐笔交付证据。`completed`不是业务完成；顶层`pending`同时反映未知交付和未知业务效果。
- CLI `result.snapshot_id/capture_request_id/frame_id/decision_request_id/decision_kind`：实际最后观察与下一请求。不能拿过去manual标注继续点。
- runtime `manual-steps/<stepId>.json`；journal目录 `manual-step-<stepId>.json`；`reward-step-<rewardStepId>.json`及不覆盖的 before/after PNG。每次核效帧记录 `original_input/read_only_successor`、capture/frame/SHA和诊断；原收据缺图的补读也占同一两次额度。

成功仍为 `all_rewards_cleared=null`、`automatic_phase_completion=false`。其他球unknown不关闭rewards，购买/D/F、创业任务、库存、预算和出战审批不变。ROOT实际关闭商店、重新扫全场领空后，才按既有 ManualPhase 与当前 `preparation_review` 关闭阶段；换epoch仍需当前全场复核，旧clear不继承。单球结果不能冒充该复核。

`pending=true`时：只对账原ID，保留原图和诊断；不要再领同球。未知原输入先查原回执，自动核效不会先发新capture。回执完整后最多2次只读；失败／进程异常不退还额度。出现选择／覆盖／新页面则回ROOT；不能用效果未知的图片宣布到账或把两个历史混合输入拆开。

## 验收端点

本批原生正例只到3份scan的蓝／灰定位与局部稳定，以及两个校准PNG与原scan的逐像素关系；完整来源清单原样列出9份裁片摘要。没有新OCR。新消费者检查使用真实Worker／Entry／ManualPhase代码与惰性publisher；完整协议画布、行数据、移除后图明确为合成。包含原生unknown不改写、蓝／灰一次输入、原生2→1→0循环、缺原后图与无效果两次读预算、未知交付零补读、页/移动/覆盖/来源/epoch/deadline拒绝、重复不重发及原ManualPhase消费。未运行GUI/Rust或无关冻结类。

原生不同位置、完整新页守卫、当前金币、一次真实点击、其两个独立后图和真实领空仍由ROOT本机验收。消失核效局限于受检scan；平坦或不支持的背景保守unknown，不证明奖励物品已入库，也不形式化排除完美伪装背景的覆盖。没有整局提速或历史授权恢复结论。

开发过程的夹具错误和修复见 `dynamic-rewards/development-notes.md`，最终判定以同目录冻结acceptance及ROOT独立结果为准。
