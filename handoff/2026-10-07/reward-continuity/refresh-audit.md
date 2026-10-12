# 经济字段、回传原因与六次刷新回放

## 结论

PR11 的经济循环已经允许在一份已核预算内最多连续执行 8 笔购买、D 刷新或 F 经验。`advance_economy` 没有要求每一笔都返回主管；D/F 的执行谓词也没有要求 `team.checked` 或 `team.fully_read`。因此，“先放宽阵容完整性就能消除六次 D 的主管往返”不是当前代码支持的修复方向。本批保留经济字段、购买、阵容和出战条件；由原回执负例发现并补齐的初次交易交付核验见后文，不以放宽守卫制造覆盖率。

不过，`Perception.read` 在 `shop` 页仍调用 `StateReader.read` 读取场上、板凳与库存；`economic_policy` 还调用较广的 `preparation_decision`，主要用它的攻略阶段和搜牌许可。这里存在阶段局部观察的优化机会。是否耗时显著须看同机相同序列的组件计时，不能从“代码会读”直接推导它占据 87.58 分钟。

六张 D 后图和一张前图现在能独立回读金币 70→68→66→64→62→60→58、玩家等级全部为 8。六笔 2 金币差额合计 12，原回执中六次 D68 都有 `completed`。这补上了历史付费刷新子链；并没有补上原来的预算、攻略授权、免费次数绑定、前置领奖/任务/清库、购牌或完整备战。

## 分阶段实际依赖

来源为完整 PR11 的 [`currency_wars_economy.py`](../../../tools/currency_wars_economy.py) 与 [`currency_wars_runner.py`](../../../tools/currency_wars_runner.py)，本次候选继续沿用这些经济条件并补交易交付核验。下表的“必要”按现有代码区分数值条件、业务条件和效果核验；不把下游阵容未知都解释成 D/F 的阻塞。

| 业务步骤 | 本次动作必要读数 | 实际业务与发布条件 | 后图/回传条件 |
| --- | --- | --- | --- |
| 初次预算 | 当前金币；各计划数值字段按可见性绑定原帧，隐藏字段保留未知 | 当前局/节点/epoch/请求；前置阶段顺序；明确目标用途、购买上限、刷新金额/次数、购牌留资、经验目标、模式与储备；同节点已花金额不清零 | 缺当前预算或来源时 `shop_strategy` / `preparation_strategy`；这是一份策略输入，不是每笔动作都必须复核 |
| 当前明确缺口购买 | 金币；目标单槽姓名、实际价格、完整槽框与位置 | 目标属于已核 `targets`，未达要求张数；共享购买预算、储备/关键缺口额度；原/新请求目标槽一致；原/新 PNG 身份正确 | 后图目标槽必须可靠变空且金币差额匹配；零/部分/未知保留待验并 `economy_result`。无关槽未知可以不阻止已核单槽购买 |
| 免费 D | 金币、免费次数大于 0；当前五槽及缺口完整核对 | 前置三阶段已核；无可先买的目标缺口；不存在 pending；同一预算 | 后图免费次数减少 1，金币不减少，五槽重新读全。它不依赖全阵容、经验或刷新价格 |
| 付费 D | 金币、免费次数为 0、刷新价格；五槽及缺口完整核对；当前等级用于攻略阶段 | 明确目标、实际次数/金额未达上限，购牌留资仍够；标准储备与超频分别计算；当前局已应用攻略正文/模式给出对应等级搜牌许可 | 后图金币少本笔价格、免费次数仍 0、完整五槽可读；再次抽到相同牌也可按真实费用成功。未知攻略阶段不允许把未解决的刷新预算先挪给经验 |
| F 经验 | 金币、免费次数、玩家等级、当前经验分子/分母、经验价格与增量 | 当前缺口、完整商店、免费 D、付费搜牌停止条件先解决；经验目标和余额允许下一笔；无 pending | 后图金币差额、等级与经验变化必须符合本笔增量。不能用角色 Lv999 或首领等级替代玩家 HUD |
| 经济完成 | 当前金币、免费次数；无 pending、无下一动作，经验剩余计划已解决 | 复用 `economy_complete`；本局/节点/epoch/原帧/预算一致；完整商店或同 epoch 有效商店读数；当前终态回执、停止/健康/归属条件 | PR11 可写 `origin=local_economy_execution`；不写主管 reviewer，不补 confidence 或 fully_read；只进入待布阵 |
| 布阵、装备、合成、投资联动 | 实际角色、星级、位置、人口、装备/库存、合成机会与投资约束 | 继续当前阶段独立核验；本次六 D 不覆盖这些动作 | 阵容未知在此确实需要补证，不能从“D 不依赖全阵容”推导可忽略 |
| 出战 | 当前鲜帧人口与阵容、完整前序检查、当前请求的单次批准 | 保留主管每次出战验收；预算完成不授予出战批准 | 不复用旧批准，不因本回放写入战斗准备完成 |

购牌的目标用途由当前已核预算提供，现有单槽校验主要保证“买的是该名字和该价格”。它本身不能证明库存空槽、合成收益或新增角色的当前收益。本批没有购牌轨迹，不能把“空槽/收益仍需当前核验”写成已验证能力；更没有为了让六次 D 成功而放宽购买守卫。

经济条件中 `required_fields('refresh')` 为金币/免费次数，免费次数为 0 才加价格；`required_fields('experience')` 再加等级/经验/价格/增量；`required_fields('review')` 只要求金币/免费次数。完整五槽、攻略与停止条件由 `dependencies` / `economic_policy` 另行约束，不能只看这组数值字段就省略业务条件。

## 回传的归因

| 回传或等待 | 当前归因 | 本批判断 |
| --- | --- | --- |
| 尚未确认奖励领空、创业指南或无用牌清理 | 顺序必要，具体机械能力可能不足 | 不能直接跳阶段。常见领奖结构化识别与单次效果核验是本次主修复方向 |
| 初次明确预算/角色目标、变更策略、当前攻略阶段不可确定 | 策略必要或来源不足 | 稳定正文可缓存，当前应用/模式/版本来源须保留；动态金币/位置另读 |
| 金币/免费次数/价格/完整五槽未知 | 当前读取能力或证据不足 | 逐字段回传缺什么，不能概括为“全部阵容未读完” |
| pending、零效果、部分效果、未知输入/回执身份 | 事务效果尚未核清 | 对原请求补证，禁止重发；不增加新控制器或交易框架 |
| 已核预算内六次纯 D 仍由 ROOT 每笔临时执行 | ROOT 绕过了现有本地经济入口，或其预算/攻略前提从未建立；逐笔的具体原因未导出 | 历史 Worker 原生输入 0 支持入口未使用，不能声称六次回传均由 Worker 所迫。原生同序列合同只有初次预算请求 |
| 经济已完成后再返回一次经济复核 | PR11 已修补该额外往返 | 本次 PR11→候选六 D 比较应保持 1→1，而非伪造新的下降 |
| 页面、目标或 epoch 改变；手动/停止/健康冲突 | 动作前置条件变化 | 结束旧计划；当前观察后再异步弹出的风险另列，裸 Entry 不具备语义保证 |

## 三层证据及其边界

[`refresh-before.json`](refresh-before.json) 与 [`refresh-after.json`](refresh-after.json) 使用同一个脚本和同一份历史 manifest。前者的生产代码来自 `bcec3280677e6a419675cc7448bf0dca8fe36ec3` 的独立只读展开；后者的实际源码摘要保存在报告 `source` 中。

1. **原始图片读取层**：先核七张 PNG 的 SHA256，再经现有惰性 Entry 的不可变同请求帧 → `Worker.observe` → 真实 `Perception.read`。没有执行 `Worker.tick`，没有原前置阶段或预算，不注入合成商店、阵容、置信度或完成字段。故这里的 ROOT 请求 0 仅表示执行了读取；不能与完整 Worker 策略请求数比较。真实历史完成阶段仍为 0/6。
2. **原回执核对层**：六份 `completed` 原样输出。原 D68 后的等待是 0.4 秒，而当前生产请求是 0.7 秒。将原回执与假定的当前精确请求对照，`manual_receipt_state` 均判 `unknown_input=true`；没有把 0.4 改成 0.7。原始未脱敏回执字节没导出，其原 SHA 是来源记录，不冒称已重新哈希核验。PNG 独立差额可核 12 实花；免费次数等必要字段没有当前绑定时，`classify_effect` 仍返回 unknown。
3. **独立协议层**：复用既有 `EconomyTests.worker` 的生成数字帧、空商店、数字 OCR 替身和惰性 Entry；真实调用 `Worker.execute_plan` 与 `Worker.tick`。只有 D68 与六笔历史金额序列做关联。所有匹配的模拟回执标为 `inert_protocol_adapter`，记录原 0.4 与请求 0.7 的差 0.3 秒；前置三阶段、标准模式、预算 5/12/0、保留 50、8 级搜牌攻略、免费次数 0、价格 2、五个空槽、测试节点 2-3 均明确是 fixture。原七图标注为 3-4 且商店有牌，不伪装成这些空槽。历史原 PNG 摘要和生成帧摘要并列，字段 `generated_frame_is_historical_png=false`。

协议层预期并实际得到：六笔 D、每笔实记 2、刷新总花费 12、无 pending，正常 ROOT 请求 1、异常 0，终点 `lineup_equipment`，出战未批准。只测试经济一个阶段；前 3 阶段不是完成证据。候选正常轨迹保留 1→1 次正常 ROOT 请求；本批不宣称这条子链减少了新的 ROOT 请求，也不宣称完整备战或整局更快。

## 原回执负例发现的实际缺口

上述原回执分类最初只是独立账目核对。继续经过完整 Worker 负例后，实际确认 PR11 的 `execute_economic_action` 初次路径只按 `command.ok` 与经济后图结算，原 completed 不一致仍会继续；最后的完成守卫并不能保护中间每笔。候选在原提交锁内核原请求动作、交付分类与输入 fence，明确交付后才结算；未知保持 pending 并返回一次异常，禁止下一笔。

| 相同原 completed 不匹配负例 | PR11 基线 | 本次候选 |
| --- | --- | --- |
| 惰性 Worker 已发布 D 笔数 | 6 | 1 |
| 消费到的生成后图金币变化 | 70→58，`observed_coin_change=-12` | 70→68，`observed_coin_change=-2` |
| 正式确认到经济台账的刷新花费 | 12 | 0，归属待核 |
| pending | 无 | 保留第一笔 |
| 正常/异常 ROOT 请求 | 2 / 0 | 1 / 1 |
| 终点 | 经济完成守卫拒绝，仍在 economy | 首笔异常即停，仍在 economy |
| 再次 advance | 没有新发布 | 没有新发布，未重发第一笔 |

这里的 **候选台账 0 不能解释为没花钱**：对应的历史图片及生成后图都已经显示第一笔金币少 2，报告保留该观察事实。0 表示原 completed 与本次精确请求不一致时，不能把差额直接确认归属并开启下一笔。负例只把原 completed 原样放进惰性传输，帧/请求身份仍是 fixture；它没有重新执行历史真实动作。其可用输入源序列相同，候选按守卫截断在第一笔，不把不同消费长度的负例耗时算作提速。

基线真实 OCR 的已完成结果保留，加入负例时只重跑了协议层；脚本校对整组源码摘要、manifest 和机器均一致后合并两段，`execution_segments` 保存分段口径，不累加两段计时。候选首次实际运行完整脚本；最终仅奖励/回执守卫和计时补丁变化后，只重跑协议。候选的 `real_png.source` 保留真实 OCR 当时的完整生产源码摘要，顶层 `source` 与协议对比使用最终源码摘要，绝不把早先识别结果重新署为最新源码执行。

另增加 **缓存后图与回执都已存在时的主管补证** 检查：从同一个首笔待验状态调用真实 `Worker.accept_economy_plan`，提供当前后图、revision 2、原 pending 请求 ID 与当前 proof。即使经济差额能够判成功，原 completed 仍是 0.4 对请求 0.7，补证必须在原交付守卫处拒绝；内存台账、持久台账、pending 请求 ID 保持不变，0 正式花费与观察到少 2 金币同时保留，没有新发布。该补证是明确的协议 fixture，不宣称有历史主管回复；结果在 `protocol_original_completed.cached_acknowledgement` 中，补证验证不混入原六 D 的计时。

## 公共检出与本机素材分开

公共检出缺少 `tools/shop_reader_resources/SOURCES.json`，本次七图 `shop_ok=false` 是实际环境缺口，报告保存原错误。ROOT 的 [`ROOT_LOCAL_RESOURCE_REPLAY.json`](../ROOT_LOCAL_RESOURCE_REPLAY.json) 则明确：57 个本机资源原样复制、生产读取器不变后，七图 `shop_ok=true`，金币和等级正确，`team.checked=false`。不能以云端缺少文件推导本机也缺资源。

商店展开遮住阵容，与商店收起后识别仍不完整，不能合并成同一种缺口。提交前最新 main `282bf4004d0e8836a55291d6244cb92caf27214b` 的 [`ROOT_ROSTER_RESOURCE_AUDIT.json`](../ROOT_ROSTER_RESOURCE_AUDIT.json) 已进一步澄清：q01/q02 人口8/8、占用场上槽5，但实名已读0、星级已读0，并非识别了5名角色。9个 unit 模板只代表声明覆盖，素材覆盖与裁剪、尺度、遮挡须分别诊断。原PR11领奖三图没有原生奖励球语义字段；本次六 D 不需要以补齐完整阵容作为前提。

若下一次要在云端复现 **真实商店读取与 D 闭环**，最小素材范围是商店根目录的 `SOURCES.json`、`names.json` 以及 `SOURCES.json` 引用的现存商店 PNG 模板（ROOT 已报告根目录共 13 文件）；不需要那 44 个 `state_reader` 文件，也不需要整份私人知识缓存。除此仍须当前原帧的免费次数/刷新价格读数绑定，以及原策略预算/攻略来源；只有素材不能补出这些授权。可以先补七图完整 `shop` 结果的脱敏结构与 SHA 供审计，但这仍不是本机原模板的独立识别复现。

## 计时口径

每张真实图和协议预算/六次刷新/经济完成分别输出 `recognition`、`rules`、`publication_simulation`、`entry_io_unattributed`。嵌套计时只把最内层区间归给一个类别，因此四类可相加。规则类别是指定现有策略/守卫/完成方法的调用范围，不能再当成纯算术 CPU 耗时；方法内未单独观测的文件处理也不能猜成模型等待。

真实图层的识别覆盖生产 OCR 与现有字段读取；协议层的识别是数字帧替身与数字区域读取，不能与实机 OCR 混同。没有模拟游戏动画或主管人工等待，两个字段均为 `null`；原回执里的等待参数及历史 helper 时长单列，不加入离线 active total。两版使用同机/同七图/同生成序列；报告不计算单遍倍率。

历史 [`ROOT_STAGE_PROFILE.json`](../ROOT_STAGE_PROFILE.json) 仅有 77 个明确节点标签，22 个保持未归类；`3-1` 有 6 个标注事件，但没有完整节点进入/退出。`1-x`、`2-x` 和对应 `-1` 的完整起止并未补出。100.47 分钟部分窗口的宿主工具并集约 12.88 分钟、工具外约 87.58 分钟保持未归因；helper 与工具窗口不能相加。六 D 子链与“最终五次刷新”不是同序列，不给倍率结论。

## 重跑与聚焦验收

脚本只需要现有 Pillow、numpy、RapidOCR/ONNX Runtime。必须在导入 ORT 前设置 `ORT_DISABLE_TELEMETRY=1`，脚本随后调用 `disable_telemetry_events()`。没有新框架，没有截图 API，没有 Windows 控制器。

```bash
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_refresh.py \
  --code-root /path/to/pr11-bcec328 \
  --evidence handoff/2026-10-07/refresh-sequence \
  --output offline-results/refresh-before.json

ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_refresh.py \
  --code-root . --evidence handoff/2026-10-07/refresh-sequence \
  --before offline-results/refresh-before.json --output offline-results/refresh-after.json

ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps:tools python -B -m unittest test_currency_wars_refresh_replay
```

本次仅新增五项聚焦检查：六 D 的真实 Worker/Entry 协议预算与终点、原 0.4 回执不可改写成当前精确成功、PNG 摘要变化在回放前拒绝、候选首笔回执不匹配时保留已扣 2 的观察事实但不结清 pending/继续，以及缓存后图和回执不能经主管补证绕过原交付守卫。它们与原 PR9/10/11 数量不累加；完整实机备战仍待连续原图与原动作回执补证。
