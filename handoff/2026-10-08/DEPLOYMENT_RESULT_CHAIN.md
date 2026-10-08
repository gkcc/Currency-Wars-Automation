# 指定角色部署结果切片

## 结论与发布边界

本批缩到 **当前容量核实 → 指定备战席角色进入指定空槽 → 原席清空、目标实名、人口加一同时核实 → 回当前 ROOT 请求**。装备归属没有足够的原生读源，因此不执行装备拖动、不自动完成 `lineup_equipment`，也不自动完成出战验收。它不是完整自动备战或整局提速交付。

独立分支为 `feat/deployment-result-chain-20261008`，唯一代码基线及直接父为 PR19 已复验的 `8bcff97b5a9287a6ffda405ee33a17473d1e32ac`。新草稿继承完整栈；PR19 分支保持冻结，本批不成为 PR19 部署前置。最终发布 head、tree 和 GitHub 回读结果见本草稿 PR 的发布说明，本文不自填自身提交 SHA。

实际读取 GitHub：main 仍为 `4cf23830e7832ee68b4ef40ba688b006ee3064fc`，PR19 head 仍为 `8bcff97…`、draft=true、未合并。ROOT 的 [Windows 原 39 项复验](https://github.com/gkcc/Currency-Wars-Automation/blob/faf023ec4ac9daa2b63579559305b7790099ce50/handoff/2026-10-08/ROOT_PR19_WINDOWS_FIXTURE_RECHECK.json) 属于 PR19，不能转记为本候选 Windows 或部署通过。

## 为什么只做部署

现有 `StateReader.read` 确实从当前 PNG 读取原生 front4/back6/bench9 槽：原生行锚点、空槽模板、角色身份模板及身份间隔、前后台徽标。`Perception` 还有独立中央人口读数。新消费者直接使用这些产物，不以计划姓名填未知，也不需要其他角色姓名、星级、玩家等级或整队 `checked=true`。

装备端的实际来源不足：`semantic_facts` 在角色装备页提供的是 `guide_recommendation_only`，`equipped=None`；`StateReader._inventory` 是可见库存，工具提示没有可信的 `owned/location`，没有将“已穿戴装备”绑定到当前角色槽位的原生生产者。因此“指定装备已属于指定角色”不能闭合。后续只需补**当前角色／槽位、实际已穿戴装备及同帧来源**，无需导出全部知识或 57 份私有资源。本批保留这一缺口。

此前海瑟音放错行属于 ROOT 的实际决策错误；本批修复它暴露的 B 类实现缺口：有效缓存类型原来可被计划的 `position` 静默盖过。`deployment_position` 现在要求缓存别名一致，计划须精确声明同一类型及缓存 SHA；原生类型如与缓存冲突也拒绝。没有把这段历史当作 Worker 自动部署失败的证据。

## 触发与停止规则

仅支持**一名已识别备战席角色 → 一个当前已识别空的前台或后台槽**。不交换两个占用槽，不购买、出售、合成，不求解阵容。角色必须满足已核缓存部署类型；目标只能是已有 `native_slots()` 的槽号，回复不能传坐标。

1. 当前请求必须是 `preparation_strategy`，同局、同节点、当前 epoch，且原准备顺序的奖励、创业指南、库存清理、经济四阶段已关闭。已有经济、领奖、腾位或部署待验继续阻止新输入。
2. 复用同一 Entry submission lease，在输入前取当前观察。原请求即使还是 `7/7`，也只能在新观察明确读到计划要求的 `7/8` 后继续。最多三次前置观察；未知不填值、不发拖动。
3. 原请求和当前帧的指定源槽、目标槽及两条原生行锚点须局部稳定；同时核当前原生姓名、独立空位、页面、覆盖层、节点、资源版本。复用现有 visual guards，没有放宽全图阈值。
4. 发布前再次经过原 Entry、当前请求、deadline、前台、ManualPhase、epoch/CAS、阶段与 pending 守卫。由已核槽位中心生成 **四参数 drag + 原 `wait:0.7`**，一次发布。
5. 精确原收据必须完整匹配 drag 与 wait。即使 `completed`，也必须同时读到原席空、目标同名、人口恰好加一、容量不变，并核前后行布局稳定。初次后帧未明时最多再做两次纯观察；页面／布局／来源冲突立即停止，绝不再次拖动。
6. 只把这一部署步骤标记为已核。清除的是受此次站位变化影响的阵容／装备／出战旧复核；已经完成的领奖和经济阶段不重开。剩余装备归属和本次出战仍交 ROOT 鲜帧验收。

锁只串行化本项目观察与输入，不能冻结游戏进程。最终截图到原生输入之间的桌面 TOCTOU 窗口依然存在；新后置核验用于发现未达到意图的结果，不声称消除了所有动画或外部布局变化。

## 当前计划接口

普通 `decide` 和同 Worker 的 `manual-step reviewed_plan` 都复用既有回复包。下列是**结构示例**，不是当前游戏授权；角色、槽号、容量及 SHA 必须取本次已核状态与缓存：

```json
{
  "request_id": "当前请求ID",
  "snapshot_id": "当前请求PNG的SHA256",
  "resume_epoch": "当前代次",
  "actions": [{
    "type": "deploy_unit",
    "name": "风堇",
    "bench_slot": 1,
    "target": {"row": "front", "slot": 4},
    "capacity": 8,
    "position": "前台",
    "knowledge_sha256": "当前static_knowledge.sha256",
    "expected_page": "preparation",
    "reason": "按已核前台类型部署到当前空的前台四号槽"
  }]
}
```

此切片只接受独立一项动作，不附带 `context_update`、阶段完成或下一次装备坐标。对已发布且待验的步骤，下一份当前请求只可选择原 `step_id`：

```json
{
  "type": "check_deployment",
  "step_id": "原32位步骤ID",
  "expected_page": "preparation",
  "reason": "只补核原部署效果，不重发"
}
```

补读先核原 ID 并折叠晚到终态，再发当前纯观察，避免共享 `result.json` 通知被覆盖。原非空 receipt 禁止改写。跨 epoch 只允许原已有的精确单跳恢复事件及同节点只读补证；新物理部署不会沿用旧 epoch。原节点改变、混合后继输入、原请求／wait 不匹配、旧共享图片别名缺不可变身份都不能解除待验。

## 部署记录与手操桥

`deployment-step.json` 是当前指针；`records/deployment-step-<step_id>.json` 保留原步骤。记录包含原意图、容量、原前帧、后帧、捕获 request/frame ID、原收据和两槽最小读数投影。PNG 是既有原生不可变帧的原字节存档，不是新控制器截图或合成后继。

`ManualPhase` 的业务 pending 扫描同时检查指针与原归档，不能靠改变指针、节点、epoch 或把状态写成 verified 清债；它使用生产 `deployment.after` 重派生后读，并核完整原始来源。原 completed 但效果未明在 `manual-step` 结果中仍是 `deployment_effect_pending=true`。部署动作已经生成当前请求时，手操桥不再清掉它另问一次。

未知时保留只读检查、精确原 ID 对账和原紧急暂停／停止；已识别的奖励覆盖层仍可走现有恢复入口。未决部署不会授权新装备、出战或重拖。若发布前最后一次意图写盘失败，只有 GuardedSubmission 确认尚未调用 publisher、原 ledger 不存在且记录身份精确匹配时，才把本次自建意图归档为拒绝；已调用 publisher 的路径不做这项清理。清理本身失败继续保守保留，不能按异常推导零效果。

## 读取与性能边界

新增 `scope='deployment', deployment_slots=[备战席槽, 目标槽]`，缓存键绑定当前 PNG、规范化两槽选择和读取契约 v3。生产 `_slot` 只读取两个选择，另 17 槽为 `not_read`；不扫描星级、装备库存、工具提示、商店、玩家等级或刷新字段。当前奖励检测保留。

每张新帧仍运行既有整帧主 OCR，作为页面／节点／人口入口；必要时补中央人口小 ROI。没有跨新帧复用旧 OCR。返回 ROOT 时沿既有同一不可变帧 `reuse_primary` 升级完整观察，不额外截图，主 OCR 可复用，但所需完整语义仍重新派生。这不是“零 OCR”方案。新增 `deployment_capacity`、`deployment_result` 规则节点接原 profile，观察／OCR／工具／主管空档继续按原协议区分。

## 已做的真实读取与协议验收

### 真实旧 PNG：只有读源检查，没有部署成功

[真实读取报告](deployment-result/real-png-read.json)来自一次实际 `Perception` 新 scope 调用，复用已公开 `q02.png`，SHA256 为 `ffb4bb93d77eb9b96fa1860cd78595d2b7c1d4373061412982868c54cf00e366`，未改像素。

- 页面 `preparation`，节点 `3-6`。
- 原整屏 `18/8`、confidence `0.8039` 保留；独立人口 ROI 实读 `8/8`、confidence `0.9500414927800497`，没有强清洗低置信原文。
- 指定 front4、bench1 均 `unknown`、姓名／类型为 null；另外 17 槽 `not_read`，整队 checked/fully_read 均 false。
- 本公开检出中 shop/state 私有资源为 0 文件，`resource_version=null`。这是本环境限制，**不代表 ROOT 本机缺少原 57 资源**。没有要求上传资源。
- 一次主 OCR 加一次人口 ROI 调用，原计时 `1585.51 ms`。只是该次本地读数，不是稳定收益、同终点提速或游戏速度证据。

此调用在首次开发运行中完成；那次运行同时有夹具错误，绝不将它记作整套通过。报告保留相关实际生产读源的摘要，它们与最终候选逐字节一致。最终冻结 31 项用 `--tests-only`，没有再次重读该 PNG 来堆正例；强制缺资源的单项 StateReader 负例与上述实际 Perception 读数分别标注。

### 最终新选择：Linux 31 项

[冻结验收及完整 40 文件摘要](deployment-result/acceptance.json)：Linux / Python 3.12.14，**31 passed、0 failure/error/skip，16.664732053999614 秒**。只执行六个明确新类，没有 discovery 旧测试。协议不启动控制器，实际 `Worker.command`、Entry、原生动作参数归一化、不可变回执及 ManualPhase 用惰性发布者驱动。

| 新选择 | 方法数 | 核心端点 |
| --- | ---: | --- |
| `DeploymentRolesTests` | 4 | 缓存类型不可静默覆盖、前四后六与唯一槽 |
| `DeploymentReadingTests` | 8 | 两槽＋独立人口的前／后条件、未知、投影重派生 |
| `DeploymentScopeTests` | 5 | 两槽实生产读法、其他未读、选择／新帧缓存隔离、缺资源 |
| `DeploymentFlowTests` | 8 | 实际 Worker/Entry 一次拖动、容量与结果延迟、换页／锚点、原 wait、晚到原收据、一次手操回传 |
| `DeploymentPendingTests` | 4 | 原部署债务、指针／归档、坏帧／混合动作／虚报结果仍拒绝 |
| `DeploymentRuntimeTests` | 2 | 新依赖进入统一清单、缺文件或任一字节变化拒绝 |

协议中的 `7/7 → 7/8 → 8/8` 只模拟先等容量、再一次拖动、再等实际效果；一次 ROOT 新请求。另一个原 completed 但仍 `7/8` 的窗口保留 pending，后续只观察原步骤后闭合。既不是 ROOT 历史 F+拖动的真实回放，也没有证明动画是历史失败原因。装备成功和真实出战均不在通过端点内。

| 绑定 | SHA256 |
| --- | --- |
| 40 文件闭包，测前／后相同 | `d2f480361665e271b71d5157405c8317d8532ba988b34b1633a11355a36eb813` |
| 新部署消费者 | `49f78e41152045e8639a39738f7f862226d3c3d0a7f2a9203f96933932b166ec` |
| 冻结 driver | `523884dd1e5061f4d2f09f74085c0cc88b3324335f68c7b29e643906101ddd97` |

完整文件集合以 driver 为准：25 项统一生产文件，加新测试、driver、真正用到的旧夹具 helper 和公共字节素材，共 40 项。不是 PR19 的 41 项，也未把旧 39 项重跑一次。报告 `tested_checkout_head` 如实为提交前的 `8bcff97…`；实际新增字节由完整测前／后摘要绑定，发布后与新树逐项核对，不事后伪改测试 head。

### 开发失败记录

- 首次新 31 项：0 failure、13 error、0 skip；生成短锚点图时内缩量超出高度，同一夹具构建错误产生 13 个子项 error，Worker 成功路径尚未执行。
- 修正夹具后单跑新 Flow 8 项：2 failure、5 error；夹具缺准备页标题／出战按钮，原安全分类拒绝。检查实际生产参数又发现本次新拖动错误加了第五个 duration，已改回 broker 原四参数；夹具现在直接执行生产纯归一化函数，避免宽松 mock 隐藏参数错误。
- 随后新 Flow 8 项通过；补后帧锚点拒绝条件后，最终冻结新 31 项全过。中间单窗口诊断只用于定位上述拒绝，不计入成功选择或速度证据。
- 没有重跑 PR9～19 未受影响冻结类，也没有安装、GUI、Rust、Windows 或真实游戏输入验收。原始 `.tests.txt` 在本地生成，公开紧凑记录保留失败事实，不上传重复 Worker 大对象。

## ROOT 局后 Windows 入口

在新不可变 head 的独立检出或 git archive 中，沿用 ROOT 已核的原 Python 3.12.2 与既有依赖及 `$acceptDir`。协议不需要私有模板；默认的另一个真实 PNG probe 会使用该独立目录实际拥有的资源，若 ROOT 按原规则复制 57 资源，其来源与五槽／选槽读数以本机输出为准，不公开资源。

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'tools'
python -B -X utf8 tools/replay_currency_wars_deployment.py `
  --output "$acceptDir/deployment-result.json"
```

这一个入口执行新 31 项及一次真实 PNG 新 scope；若只核契约可加 `--tests-only`，报告会明确该次没有 Perception probe。应核 31 个 ID、0 failure/error/skip、40 项 source-set 一致及测前／后差异为空。无 `.git` 的归档应保留 `tested_checkout_head=null`，仍按实际不可变归档与摘要绑定。不要复写旧 PR19 报告，也不要重跑其原 39 项来凑本批通过数。

本批统一清单从 24 项变为 25 项，新模块字节也受 Python 与 GUI 同一来源表约束。未来切换此候选时需重新构建嵌有新清单的 GUI、重新做本候选独立来源／资源／READY 核验；旧 READY 不搬用。本 PR 没改 broker/control、固定桥 driver 或其 pin；若 PR19 输入组件已按原流程验过，可核相同组件字节后复用。PR19 原 24 项 GUI／Rust／桥升级／READY 与自然节点验收仍按 ROOT 原计划进行，不能因这份后续草稿增加其阻塞项。

## 仍需 ROOT 的真实端点

1. 本机原资源确实能在当前自然准备节点识别所选角色原席、目标独立空槽和缓存部署类型。若两槽之一仍未知，这个角色／槽位不在当前可闭合覆盖内，应返回具体读源缺口，不换成假实名。
2. 一份自然 `deploy_unit` 请求实际发布一次 drag，原回执与每个后帧完整持久化；有容量／布局延迟时仅读并按上述界限停止。由真实生产读数核对原席空、目标同名和人口加一。
3. 如果经过 takeover/resume，核原 ManualPhase、精确恢复事件、原 pending 和当前节点桥；保留既有未决，不手改终态。
4. 当前装备归属仍由 ROOT 验，最后保留该次出战的鲜帧验收。没有真实完整准备节点前，不声称减少真实往返、整局加速或已经部署。

运行期限（7200 秒）、通用策略条件求值、待售集合自动关闭和完整装备归属读源另列；本 PR 不扩这些功能。
