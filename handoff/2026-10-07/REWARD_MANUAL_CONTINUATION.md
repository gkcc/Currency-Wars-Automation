# 奖励顺序、手操续接与发布队列的整合候选

## 结论和版本边界

本批以冻结 PR18 `1c48d12f88944a5532371ac3192710b85984cc42` 为唯一直接父，独立分支 `fix/reward-manual-continuation-20261007`。实际再次 fetch 的 main 仍为 `4cf23830e7832ee68b4ef40ba688b006ee3064fc`，证据已经在父链内。PR18不改写、不合并、不安装，保留给ROOT原Windows验收。本批最终远端SHA/树以草稿PR和回复的实际回读为准。

优先修的是：把重复领奖与受监督的程序输入交给现有Worker，在同一Entry发布锁内用当前帧核意图，不让ROOT原始坐标绕开准备顺序。保留原经济循环和逐阶段ROOT策略/出战验收。没有新控制器、broker进程或在线模型。

| 问题 | PR18及此前已覆盖 | 本轮仍需补的B类缺口 |
| --- | --- | --- |
| ROOT在1-2恢复后沿旧球坐标误买两张 | 原生Worker已会逐控件读图、核原收据；不能把该次ROOT操作错误归到Worker | raw手操客户端未要求业务意图；普通command没有共同经济控件资格检查；handoff前帧没有后续布局稳定证明 |
| 1-3投资返回后商店延迟展开 | PR18解决两类导航的局部新鲜度，原epoch/CAS/pending仍在 | 奖励动作要另外证明两次当前观察的布局/球位稳定，输入后遇选择或布局变化停止该批 |
| 两次client-submit.lock发布前拒绝 | 原锁阻止并发发布，原ID不能重发 | 手操期间90秒被动观察可能竞争，锁获取只有立即拒绝；尚无本轮历史锁持有者证据 |
| 手操完成后仍认旧节点 | 既有ManualPhase能归档原收据并在新epoch复核，历史all_claimed不能复制 | 原绑定依赖旧runner节点，缺少显式当前节点桥；交付completed与业务effect pending不能混同 |
| ROOT逐点击决策、时间不可归因 | 原生领奖/经济循环、有限布阵装备动作及profile已存在 | 缺同Worker手操批次入口，以及主OCR调用、发布前排队、主管等待的独立实记分类 |

ROOT报告的原收据 `34daf6e5da314e29bb7b1aa22e90257a` 及本轮原转场帧不在已fetch公共树中。本批依据ROOT报告确认问题范围、依据源码和惰性协议验证修复；没有伪造这份收据、拆解历史组回执或构造连续真实转场。

## 当前目标和意图的共同发布边界

`Worker.command`的锁内publication guard现在检查实际解析的完整文字目标、点位及控件域。当前奖励未核空，购买/D/F继续拒绝；已有旧完成项也不能覆盖当前可见奖励、待选提示或原生不确定读数。刷新标签、D、数量/价格、控件背景都归到刷新意图；XP完整控件、上方五卡商店区域和当前原生slot也只能由原预算事务授权。拒绝域不是正向定位证据，不用它们生成购买坐标。

合法经济首发校验当前这一笔未发布pending的请求ID、action、归一化broker actions、页面/节点/epoch、不可变帧身份、预算revision/policy及累计支出。不会再次调用“有pending就不选新动作”的候选器来拒绝自己的首笔。原价、免费优先、付费停止后XP、实际费用核验仍由原经济消费者执行。文字“刷新/购买经验”按原实现转换为D/F时也保持这份绑定。当前已核的单次领奖腾位保留原独立容量证据及首发pending，不变成一般出售许可。

runner-owned会话中，Entry raw physical/handoff actions与旧control公开raw客户端在发布前、取得原锁后两处拒绝，错误指向`runner manual-step/decide`。read-only observe/wait、原ID对账、原显式resume和紧急pause/stop保留。该约束覆盖项目支持的程序客户端；它不是抵抗任意Python篡改、直接写IPC文件或人手键鼠的OS安全边界。

## 领奖批次：先收店，稳定当前布局，再逐控件核效

复用原`advance_rewards`，每批仍最多8步。每一步持同一Entry lease完成当前补观察、局部新鲜度/语义前提、单个输入、原收据及后帧核效，然后释放锁。下一个球重新定位，不沿用上一个中心。页面/节点、可见OCR语义、球数量/位置、目标暴露或锚点改变时停止该批；下一周期可纯观察当前状态。商店已展开时先走当前“收起”，不能点击被它改布局后的旧球位。

局部检查复用PR18 visual guards的当前字形/RGB/halo和可见语义比较，不提高全局指纹容忍度。公开q00的商店“备战阶段”是低于原亮字cutoff的灰色标题；仅这一个非点击标题允许同位置原生高置信文字、局部对比度与整块RGB稳定组合。其余锚点和实际点击控件保留原守卫。没有新模板、额外全屏OCR或私有素材导出。

该锁串行化程序提交者，不能冻结游戏动画。最后一次截图到真实输入仍有桌面TOCTOU窗口；不可观察覆盖、未支持球种或区域仍未知。原蓝球读取只覆盖既定有限区域，因此单个/多个球领取成功都不写领空，`all_rewards_cleared=null`。最终全场领空仍由ROOT对当前完整帧复核。

## 手操例外交给现有Worker

新增`manual-step`是所属Worker消费的有界mailbox，没有启动第二进程或输入者。支持四种明确操作：

| operation | 本次可执行范围 |
| --- | --- |
| inspect | 一次当前完整观察/现有决策请求；暂停时仍可只读 |
| collect_rewards | pending rewards ManualPhase中的原生重复领奖；一次批次结果回传 |
| recover_overlay | 仅当前识别的reward_overlay单次Escape，随后新帧回传 |
| reviewed_plan | 一个当前原有受检action；不顺带context_update/新预算/完成证明 |

步骤有稳定32位十六进制ID、60秒有效期、至多一个queued/running，CLI最多等待25秒。超时查询相同ID，不创建新job；running先落盘，再观察/输入。每次发布前重核manual_id、旧epoch、match、checkpoint仍pending、前台、暂停/停止状态。物理步骤不自行解除broker暂停，沿原显式手操handoff；runner的manual锁仍在，普通自动执行不能混入。

前后receipt水位分别核验。不可枚举、旧ID消失或单条解析失败时，保留原ID和未知，`receipt_watermark_verified=false`时`input_receipt_ids=null`，不填空数组冒称零输入。原执行错误、证据错误与归档错误分别保留；两处结果均写失败则原running继续作为不重播边界。

部署后ROOT可用已有本机会话参数数组`@runArgs`、当前`$manualId`及原`manual-checkpoint`所得`$checkpointId`执行：

```powershell
$stepId = [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe -B -X utf8 tools/currency_wars_runner.py manual-step `
  @runArgs --manual-id $manualId --checkpoint-id $checkpointId `
  --request-id $stepId --operation collect_rewards
```

相同命令/相同ID可查询结果。实际动作收据ID在`result.input_receipt_ids`，供原`manual-result`归档使用。遇选择，用`inspect`的当前Worker请求和`reviewed_plan`单次受检动作处理；未知目标保留回传。不要把旧helper的坐标批次直接交给raw客户端。

## 新节点桥和最小续接闭环

如果手操已跨节点，先用`manual-stage @runArgs --manual-id $manualId`建立显式桥。它先对账原ID，再做两次独立当前纯观察；只接受原生备战/商店HUD中的唯一高置信节点和稳定锚点，保存真实不可变PNG、原收据水位以及from/to stage。建立桥本身不恢复、不发输入、不写阶段完成。

原ManualPhase实际动作、当前后帧和原review归档后，继续使用原公开resume CAS。恢复后Worker再用新epoch纯观察核桥，只采用一次当前scope，清旧动态准备proof/预算/决策，保留实际支出与未决效果。已完成ManualPhase的后续输入必须全量属于同一binding并有原始收据和前后原图，不能用“做过了”覆盖来源缺口。原恢复事件不改写；from→to差异只在这条已验证桥中认可。

恢复后普通HUD缺读/过渡不会直接pause_internal：最多两次下周期只读补观察，仍不能读则回传当前节点问题。原图/收据/归档损坏、epoch冲突、已可靠读到其他节点和未归档后续输入仍硬拒绝。原领奖pending不再因stage变化消失；原经济completed但费用/后图未核仍阻止新动作。桥保存的小集只在非空时核固定源，真实业务终态才解除，不能仅看当前pointer空了。

继续采用已有顺序：

1. Worker反复领取支持的奖励，必要选择回ROOT；最后ROOT当前全场领空复核。
2. 当前第二入口创业指南/章节目标及顺手任务；已完成目标不重复选攻略。
3. 清明确无用库存，策略要求保留的卡不机械出售/购买。
4. ROOT给当前缺口及预算，Worker原经济循环处理购买、免费offer、当前实价付费搜索和停止条件，再XP；D不要求无关等级或完整阵容。
5. 布阵、装备、合成机会与策略同场条件使用已有当前语义和受检动作；未知实名/槽位/装备回ROOT。**本批没有补成完整阵容自动规划器或未知装备执行器。**
6. 每次出战仍由ROOT鲜帧验收，沿原审批策略及单次批准执行。

历史all_claimed仍不继承到新epoch。手操批次终点、恢复后的当前全场复核、经济完成和出战是不同边界；不能把第一个短窗口当成完整备战闭环。该适配让支持的重复工作回到脚本，策略、动态缺字段和出战留在原监督接口。

## 有界排队和节点profile

取消manual分支的90秒被动截图竞争。Entry在原O_EXCL锁的获取阶段对精确busy做默认最多2秒等待（上限5秒，并受原提交deadline限制）；锁内guard、publisher或回执失败不进入重试。对已发布请求只核原ID。迟到终态先折叠回原ledger，再允许新observe覆盖共享result通知，不删除别人锁。

这证明代码有Worker观察和手操竞争的可能路径，不能证明本轮两次拒绝究竟由谁持锁。新profile只记录实际区间：

| 分类 | 含义与边界 |
| --- | --- |
| queue_wait | 本进程Entry发布前租约获取，来源entry_submission_lease |
| tool_roundtrip | Worker请求外层扣除已测子段的剩余，不冒称纯IPC |
| capture | 实记观察/帧发布/校验等子段，不冒称纯截图硬件耗时 |
| ocr_engine | 仅Perception主引擎及其委托ROI的实际调用；不含ShopReader独立OCR |
| perception | 感知包扣除已测引擎后的残余，仍可能含未单测OCR |
| supervisor_wait / manual_wait | Worker等待区间扣除明确关联子段，不等同模型、思考或宿主工具外空闲 |
| unknown / unassigned | 未分类、未关联重叠或无节点归属，继续保留 |

采用operation_contract=2；旧ocr仍是旧Perception总包。旧新埋点合同禁止计算倍率。缓存命中不复制上次引擎时间，主OCR重用只记本次实际ROI调用。诊断故障停用profile并保留error，不改变输入结果或重试发布。

ROOT后续自然运行时，复用原`--profile`及`currency_wars_profile.py --output-prefix ... <原events文件>`。最小补证是一段明确起止的当前节点，保留从开始领奖到ROOT出战验收的阶段边界、动作/observe原ID、等待起止和三类回传原因（策略/缺读/异常）。宿主工具并集需ROOT自己的实际工具起止证据；本批不把原87.58分钟unknown重命名为模型或OCR。

## 冻结验收、真实覆盖和失败记录

[冻结driver](../../tools/replay_currency_wars_reward_manual_boundary.py)的精确选择在[acceptance.json](reward-manual-boundary/acceptance.json)：新队列7、阶段桥12、计时6、mailbox证据3、共同业务消费者11，合计**39通过，0 failure/error/skip，15.628463081秒**。41个生产/driver/测试/留存fixture摘要测前后相同；生产唯一清单24项。报告约18KB，没有重复Worker大对象。

| 新消费者窗口 | 实际终点 | 证据类别 |
| --- | --- | --- |
| 延迟展开商店 | 旧球位输入0；下一pass先收店，再两个单球；回preparation_strategy，领空仍null | 公开留存像素＋声明的惰性后继协议 |
| 同Worker手操批次 | 收店＋2球共3个输入收据、4个零输入收据、1个Worker→ROOT请求；manual锁/epoch保留，同ID不重播 | 协议；不含后续resume边界 |
| 单笔合法F | key70一笔，协议实记4，pending解除 | 声明数值协议；未重跑旧三F/六D |
| 跨节点续接 | 原resume＋新epoch当前帧＋scope采用＋原ManualPhase来源验收；旧effect pending阻止新输入 | 声明HUD与真实文件/Entry/CAS函数，非真实连续局 |

局部动画、位移、改字/页面/覆盖、frame/source、epoch/pause/checkpoint变化及非法控件路径分别做了可区分负例。q00/q01原PNG作为已公开校准像素复用，没有新真实转场图或全五槽/私有资源验收。原混合completed组、坐标/wait差异与共享preview身份缺口没有被修写或宣称通过。

开发中曾误用模块级discovery带出旧类，实际中止exit130，已见16个旧ERROR且无最终异常摘要；这是违反不重跑旧选择的操作错误，完整方法名和本批新选择的开发失败均见[开发记录](MANUAL_STAGE_DEVELOPMENT.md)。最终driver增加执行前ID白名单，以上39只计新类。没有把旧误跑、定向开发通过和最终选择相加。

## ROOT最小Windows复核与一次切换路径

PR18继续独立冻结；本批完整候选直接面向main，最终只安排一次整合切换。当前没有Windows/Rust构建、真实游戏输入、安装或整局提速通过结论。

在新的独立候选检出中运行下面唯一新driver，无需复制私有素材、启动游戏、GUI或broker：

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'tools'
.\.venv\Scripts\python.exe -B -X utf8 tools/replay_currency_wars_reward_manual_boundary.py `
  --output "$acceptDir/reward-manual-boundary.json"
```

用报告的41项摘要核源文件，复用PR9–18既有冻结材料；PR18未完成的独立验收按ROOT原安排，不因这份Linux结果提前通过。tests.txt仅是同次日志，ROOT需要时由driver本机生成；公开保留紧凑JSON和所有失败事实。

本批两份新增模块已进入统一来源清单；原刷新模块、徽标消费者及素材manifest的校验继续继承。新清单24项改变GUI内嵌字节，须重建GUI；旧22项二进制应拒绝READY。暂停/停止不新增来源门槛。C系统TEMP与D固定runtime根继续沿PR18的验证后子进程环境传递，不改全局环境。

本批实际修改control公开客户端，因此输入组件pin也变了：

| 组件 | 本候选SHA256 |
| --- | --- |
| currency_wars_control.py / Entry broker pin | e499928dc305815b21d1f06314d34d7ff758c44433f2f27a108dd716090f496f |
| currency_wars_bridge_task.py / driver | b83259ee0741a67bd2d396823279b7ddcb2ffc07edeeb8c3f95882ca59e69911 |

ROOT打完当前局、旧所属进程确认退出后：独立验新候选；用原`build_input_bridge.py --game-path <本机实际路径>`构修复包，经原独立核包，再走原`Install-InputBridge.ps1`；`gui/Build-GUI.ps1`构建新GUI；按24项源码及本机实际素材/provider重新形成真实READY审查，运行`check_install.py --check-launch`。原installer、requirements和资源不改，不复制pyc，清本仓库应用缓存后使用新进程。勿手改安装pin或将旧ready=true当新授权。

本批只发布必要代码、driver、紧凑摘要/验收与本交接，复用既有PNG/blob；不重试已知缺凭据的CLI push。不会代ROOT合并、安装或切换其游戏。真实验证由ROOT按本轮结束后的切换安排执行；若阶段/原receipt仍有来源缺口，保持例外，不能补值制造闭环。

## 成熟实践的最小借鉴

复核[pywinauto官方等待说明](https://pywinauto.readthedocs.io/en/latest/wait_long_operations.html)的可见/可用条件与异步初始化限制，以及[Airtest官方loop_find源码](https://airtest.readthedocs.io/en/latest/_modules/airtest/core/cv.html)的当前截图匹配和有限超时。本批只借鉴当前目标前置条件与有界等待，仍使用现有视觉守卫、Entry、immutable receipt、epoch/CAS及ManualPhase；未接入它们的控制器，也未引入Jev或视觉模型服务。
