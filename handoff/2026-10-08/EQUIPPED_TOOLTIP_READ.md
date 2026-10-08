# 单角色实际装备来源：只读接入与下一份最小证据

## 本批结论与交付范围

用户指出的“当前场上角色 → 具名角色页 → 该页实际装备槽 → 完整装备工具提示”是可探索的真实穿戴归属来源。PR20 的旧 `equipped=None` 只说明当时消费者读的是攻略推荐，并未连接这条实际来源；不能据此断言游戏没有来源。

本批按允许范围完成只读部分：**现有 OCR 的完整标题/类型候选实际进入生产 Perception、Worker 当前观察与 ManualPhase 原始留证。** 当前公共材料不足以校准具名实际角色页和实际槽定位，因此没有发布两个自动导航点击，也没有将某个装备候选绑定给调用者填写的角色或槽。实际穿戴归属、装备动作结果及出战验收仍未闭合。

代码直接父为完整 PR20 `8666ac5ae1482f85e1c856073566e205b4ebf651`，独立分支 `feat/equipped-slot-evidence-20261008`。已实际 fetch/main 并用 GitHub 接口回读 main `4cf23830e7832ee68b4ef40ba688b006ee3064fc`、PR20 head/draft/未合并状态。新草稿以 PR20 分支为比较基线，增量可单独审查；不改 PR19/PR20 分支，不成为 PR19 部署前置。最终 head 与发布回读见 PR，不在文件内自填自身提交 SHA。

## 已修的具体代码缺口

此前存在两道无关限制：`StateReader.read` 要先成功加载阵容素材才执行 `_tooltips`；`Perception.read` 只在 preparation/shop/investment_summary 调用 StateReader。真实角色页若分类为 unknown，原来连具名装备候选也不会进入输出。

现在 `StateReader.read_tooltips(path, rows, page, rows_snapshot_id)` 独立核当前保存 PNG 的格式、1920×1080 尺寸及 SHA，并核原 OCR 行属于同一 SHA。它复用原 `_tooltips` 的标题/类型相邻规则，不加载 roster 模板、不做新的 OCR、不生成输入点。

| 事实 | 本批规则 | 不可推出的结论 |
|---|---|---|
| 工具提示标题与类型 | 唯一完整标题、相邻完整类型行；原文和有效框、显示与原始置信度均保留 | 一段相邻文字不证明它位于真实角色的实际穿戴槽 |
| 置信度 | 原始分数与显示分数均须在 0.90–1.00；0.89996 显示成 0.9000 仍拒绝 | 不用四舍五入、别名或剥问号提高置信度 |
| 类型 | 简易装备/进阶装备为装备类型候选；消耗品保留独立类型，`wearable_type=false` | 消耗品不成为穿戴正例 |
| 姓名 | 装备名保留 `Lv.`；原角色等级后缀规则仅作用于 unit 类型，原行仍在 evidence 内 | 不能用目标计划补姓名或替换原文 |
| 歧义 | 重复标题、冲突类型、缺标题/类型、非法框、低置信、OCR 来源不符均不选一个凑数 | 不能认为没有候选就代表空装备槽 |
| 攻略页 | guide/unit_gear 标记 `guide_recommendation_only` | 原 `semantic.gear.equipped=None` 及 inventory_checked=false 保持 |

候选标记 `status='candidate'`；总读取结果始终 `status='unknown'`、`checked=false`，`equipped/owned/location/owner/slot=null`。原因明确为：

- `actual_unit_panel_source_missing`：没有已校准的实际角色页身份规则；
- `actual_equipment_slot_source_missing`：没有该页当前实际槽定位规则；
- `owner_selection_receipt_unbound`：未证明原部署角色选择与后续槽点击的来源连续性。

这些不是 OCR 再多跑几次就会消失的条件。本批没有为固定缺源建立轮询、业务 pending 或新的执行动作。

## 真实生产接线与输入边界

`Perception.read(..., scope='equipment_tooltip')` 已实际接入；读取契约升为 v4。这个 scope 在 unknown 页也执行，只用现有一次全帧 primary OCR 做页面/文字发现，跳过玩家 HUD、商店、刷新、人口、阵容、库存、奖励和其余 ROI。它明确把这些字段标为 not_read，不伪装成完整准备观察。

同一不可变 PNG、同一引擎对象与 OCR 契约下，可用 `reuse_primary=True` 重新派生 full 或 equipment_tooltip 语义。新 PNG、新引擎、OCR 契约变化均不得复用；force=True 实际重读。full 的原 `state_read.tooltips` 与新 `semantic.native_tooltips` 共用保留原始置信度的行，避免两条通道一严一松。

正常 full 读取直接附加 `semantic.native_tooltips`，不增加 OCR 次数。因此原 `Worker.command` 的动作后 `read_frame`、`Worker.observe`、`ManualPhase._manual_capture` 都可通过现有 full 入口得到这份候选，不需要增加新动作类型或手操控制器。既有 Worker 保存当前 capture/frame/captured_at；ManualPhase 将原观察、原始 PNG 和完整收据水位写入原 checkpoint。

本批没有触碰普通坐标/文字守卫。用户提供的第一槽参考点 `(1645,816)` 没有成为生产定位证据。当前普通坐标路径仍须原/新帧中的完整唯一文字目标；不能扩大 ROI 借旁边角色名或“装备推荐”文字批准无来源图标。将来若支持导航，必须先有实际角色页和实际槽的当前锚点，再逐个动作消费，页面/布局改变就废弃未执行目标。

原 completed 回执只证明交付，仍不完成装备阶段。本批读取结果没有装备成功分支，不改变经济/奖励顺序、pending、epoch/CAS、手操接管、唯一输入者或 ROOT 每次出战验收。旧 `observe` 的至多两次是不可用帧的有界传输重取，不是装备动作重发。本批也未增加 unknown 时的捕获循环；原晚到回执折叠/未知结果守卫保持原样。

Runner 仅改两处过强的旧“无来源”文案，将部署摘要的装备原因改为 `actual_unit_equipment_slot_binding_unverified`，准确表达当前尚未绑定实际角色/槽。

## 实际保存 PNG 读取：真实负例

[紧凑冻结验收报告](equipment-tooltip/acceptance.json)含一次真正的 `Perception` 新 scope 调用，输入复用已公开的 `reward-sequence/q02.png`，未改像素，SHA256：

`ffb4bb93d77eb9b96fa1860cd78595d2b7c1d4373061412982868c54cf00e366`

| 项目 | 本次实记 |
|---|---|
| 当前页面 | preparation |
| 原生候选 | 0；类型行列表为空 |
| 总读取状态 | unknown，checked=false；全部归属字段 null |
| 原 primary OCR | 1 次；无额外 ROI OCR；cache_hit=false、primary_ocr_reused=false |
| 此次读取耗时 | 1244.26 ms；单次保存图读取，不是动作/节点/对局速度 |
| 阵容、库存及店 | not_read；没有重读 PR20 的两个部署槽，也没有补另外17槽 |
| 原动作不可变身份 | frame_protocol/capture_request_id/frame_id=null；没有伪填 |
| 真实穿戴正例 | 0 |

q02 的场上装备图标和右上推荐合成控件不等于本次要求的具名角色详情页/实际槽工具提示。这张图只证明普通准备页不会被补为已打开的实际穿戴详情，不能作为新归属链正例。本切片尚未读取任何真实角色详情/装备 tooltip PNG，因为当前未提供这类受检图。

这个 scope 未加载 roster 模板。报告的 resource_context 只描述本次进程，不推断 ROOT 本机原57资源是否覆盖其他控件；没有导出、上传或修改这些资源。

## 17 项新增聚焦选择

Linux / Python 3.12.14，17 passed、0 failure/error/skip，1.8154559550002887 秒。原 requirements 对应版本为 Pillow 11.3.0、numpy 2.3.5、opencv-python 4.13.0.92、rapidocr-onnxruntime 1.4.4、psutil 7.2.2。此计时仅为新协议检查，不表示游戏提速。

| 明确选择 | 数量 | 覆盖 |
|---|---:|---|
| `EquippedTooltipReadingTests` | 6 | 原文/类型、消耗品、来源、缺失/歧义/置信度、推荐页隔离 |
| `EquippedTooltipScopeTests` | 6 | unknown/shop 实际接线、full 原通道、not_read、cache/reuse/force、新帧/引擎/契约、禁止坐标参数 |
| `EquippedTooltipConsumerTests` | 5 | 原 Worker、Entry 帧校验、ManualPhase 持久化、读取期间 epoch 变化、completed 不等于业务完成 |

三类都是本批新增选择。消费者只调用已有 compatibility 文件的惰性夹具 helper，没有继承或运行旧 TestCase。合成黑色 PNG、显式 OCR 行与预置旧 completed 账目属于声明协议；实际 Perception/StateReader、Worker.read_frame、Entry 不可变帧校验及 ManualPhase 均走生产实现。惰性发行者只接受 observe/handoff=false，发布物理输入数为0；用于验证 completed 边界的账目直接预置，不是被发行者点击出来的实际证据。

最先一次命令在加载选择前失败：当前默认 Python 未带入既有本地测试依赖目录，缺 cv2/psutil；未执行任何测试，也未读取 PNG。随后复用已存在的本地依赖目录，未安装软件，执行上述一次完整17项及一次 q02 实读，全部通过。没有把初次加载失败藏进通过计数，也没有以 sleep 修时间夹具。原始本地测试文本可由同 driver 生成；公开只保留紧凑 JSON，不重复上传大型对象或完整 traceback。

此次无关旧冻结选择、Windows、GUI24、Rust6、桥安装、READY、真实自然准备节点均未运行。已有 PR19/PR20 验收结论仍只按 ROOT 独立证据解释。

## 源码绑定

报告完整列出31项源码/夹具摘要，含原生产清单25项；测前后无差异。源码集合 SHA256：

`e61a05a548799a7f4768db7fd347be0b234ded93b74980b144d85095f9dddf9a`

| 文件 | SHA256 |
|---|---|
| `currency_wars_state_reader.py` | `43ae0932c1fe63123516132c2a19652441e157e7d6affde5d250335d1c36d432` |
| `currency_wars_perception.py` | `b2b64742532c144f578d7d357fe1d48b06927a91ba38f893fa0c1aae774b5eff` |
| `currency_wars_runner.py` | `3cf00768b5f048ec5dd6048b10b2dd0ae612cc97beb4111f87e14ffcf1773eb9` |
| `replay_currency_wars_equipped.py` | `5266f1814043f9009246f865cd90906ec4d76bf6c8b8c508bb85ca5eb63583a3` |

测试时 checkout 的 HEAD 仍为 PR20，修改字节由完整测前后摘要绑定；没有把后来发布的 head 填回测试报告。发布后再 fetch 最终提交，按上述摘要逐文件核最终提交字节。无 .git 归档运行时 head 应保持 null。

没有新增生产模块、素材目录或清单条目，`currency_wars_runtime_sources.json` 仍为原25项；它已包含本批两个 reader 与 Runner。未修改 READY/source_guard 规则、GUI、输入桥或 broker。将来整合时必须通过原有新源码字节验证，不能沿用旧 READY。

## ROOT 局后 Windows 入口

在新草稿不可变 head 的独立 checkout 或 archive、原 Python 3.12.2 与现有依赖中运行：

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'tools'
python -B -X utf8 tools/replay_currency_wars_equipped.py `
  --output "$acceptDir/equipment-tooltip.json"
```

默认只跑本批17项，再读一次已有公开 q02；不会运行游戏、GUI、broker、安装或模型服务。`--tests-only` 可明确跳过 PNG，报告保留该状态。31项摘要和原生产25项计数均由 driver 实际计算，不预填 Windows 成绩。

若已有一张当前角色的实际装备工具提示保存图，可只读该文件：

```powershell
python -B -X utf8 tools/replay_currency_wars_equipped.py `
  --read-only --image "$savedTooltipPng" --expected-sha256 "$pngSha256" `
  --unit "$unitName" --row "$boardRow" --board-slot $boardSlot --equipment-slot 1 `
  --output "$acceptDir/one-equipped-slot.json"
```

这不是采图命令。目标参数必须一起提供，只表达本次请求的一个实名角色、一个原生前/后台槽和第一装备槽；均记录为 `caller_intent_not_native_evidence`。即使目标名与候选名碰巧相同，也不会填 owner/slot 或批准输入。缺原生角色/槽来源时，这个命令应继续 unknown；这不是失败需要放宽的阈值。

## 下一条归属闭环的最小补证

只为校准实际角色页/实际槽，最少需要同一角色的两张已稳定保存图；若已有留存可直接复用，不要求本轮新操作：

1. **实际角色详情页 PNG**：当前唯一角色实名、可区分攻略推荐的页面锚点、底部第一实际装备槽完整边框；保留原分辨率和逻辑全局位置。需要读出角色名原文/置信度/框、页面锚点框、实际槽边界和槽序号来源。前后台类型仍按已核缓存约束，不因本次计划静默改写。
2. **点击同一页第一实际槽后的 PNG**：完整装备标题、装备类型及完整工具提示正文，另保留能证明仍属同一角色页的可见锚点。若 tooltip 遮住角色名，应明确仍能核的面板连续性锚点；不能粘贴上一帧姓名填空。

本机可以保留两张完整 PNG，只回传新 driver 的原生读数、图 SHA 和必要锚点/槽 ROI；若需本端做形状校准，只公开两张的去敏受检区域及全局裁框/图 SHA/遮罩外像素一致依据。新小模板的来源必须标为校准样本，不能把同一张图再算独立泛化正例。无需上传原57素材或全场/全库存。

要进一步证明“场上部署槽 → 该具名角色页 → 实际装备槽 → 该装备 tooltip”的归属，还需点击角色前一张当前准备帧及两笔原始单动作收据。每笔必须保留动作/等待参数、前后 snapshot/capture/frame 身份、run/match/epoch 绑定和完整水位；任何中间换角/导航/穿戴/未知输入都使旧关联失效。共享 game-preview 别名、两个标签或 completed 的局部条目不足以拼成链。去敏公开材料不含 owner/token/UID。

在上述来源校准完成前，本批真实闭合端点仅是“当前 PNG 的标题/类型候选进入原 Worker/ManualPhase 留证”，不是角色穿戴归属、装备生效或全队装备验收。局后优先部署并采 PR19 自然节点/profile 的顺序保持；PR20 和本草稿后续再整合。运行期限、通用策略求值、奖励动画误点、F后部署动作顺序均没有借本批宣称已修复。
