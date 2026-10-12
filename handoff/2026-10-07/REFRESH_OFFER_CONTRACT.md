# 刷新控件字段契约：离线交付

## 结论与基线

本批修复一个明确阻塞点：当前刷新控件的数字没有模式归属，消费者因而要求画面未显示的免费次数 `0`。现在生产感知返回带当前帧来源的 `refresh_offer`，免费剩余数量和收费价格分域；三张留存 PNG 已分别实际读得免费 `2`、免费 `1`、收费 `2`。

- 完整代码基线：PR15 `060ec1cfea03af1d474e0296f5ec0df73b16fa91`。
- 合入证据基线：main `b5da98042a07d91a41e11baaacd34cc1f55e7c34`。
- 独立分支：`fix/refresh-offer-contract-20261007`；草稿 PR 以 PR15 分支为目标，不合并、不安装。
- 测试时本地 merge HEAD：`91780afa3faddab286f624d22bd218d8b36d55ba`，父为上述 PR15 和 main。它只说明工作基线；**最终交付源码由测前、测后 SHA256 绑定，最终远端 SHA 以草稿 PR 与交付回复的实际回读为准**。
- 全程未启动游戏、GUI、实际控制器或 broker，未采新游戏图、安装候选或调用在线模型服务。Worker / Entry 只在惰性协议测试中实例化。

本批证明字段读取及对应消费者协议成立；没有真实逐笔刷新链、ROOT 往返减少、完整备战或整局提速结论。

## 三张真实 PNG：生产读取结果

输入为 [原清单](free-refresh-sequence/manifest.json) 中已公开的三张 1920×1080 留存图。它们经过当前 `Perception.read(scope='economy', force=True)` 和当前 `consume_offer`；人工标签只在读取结束后作对照，不进入生产 reader。

| 图 | 未修改的主 OCR | 本批数字来源 | 当前 offer | 原玩家等级 |
|---|---|---|---|---|
| f00 | `免费刷新`，`2` / 0.9993 | 复用本帧主 OCR，0 次数字补读 | `free_remaining=2`，`paid_cost=null` | unknown |
| f01 | `免费刷新`，数字 `1` 漏检 | 原尺寸 `[1603,516,1636,554]`，原文 `1` / 0.9990580678；1 次补读 | `free_remaining=1`，`paid_cost=null` | `7` |
| f02 | `刷新`，`?2` / 0.7261 | 原尺寸 `[1626,516,1665,554]`，原文 `2` / 0.9963898063；1 次补读 | `free_remaining=null`，`paid_cost=2` | unknown |

三图控件原主 OCR 行及全部原玩家字段与 [ROOT 原生读取](ROOT_REFRESH_WIDGET_READ.json) 相同，`?2` 仍保留在主 OCR 原文中。没有去掉问号、降低 0.90 置信度门槛、补玩家等级、读完整阵容或给收费状态写免费 `0`。免费动作费用 `0` 是免费模式的经济语义，不是 OCR 读到的免费剩余次数或价格。

[TRUE_PNG_READ.json](refresh-offer/TRUE_PNG_READ.json) 含每图完整主 OCR 行一次、数字补读原输出、置信度、坐标、控件与数字区域 RGB 摘要、PNG 摘要、模式、未知字段、调用数及读取耗时。正式独立 driver 中两次数字补读分别为 29.592 ms / 13.163 ms；仅是单次调用记录，不作稳定速度判断或基线倍率。

本环境只有公开树资源，三图商店均保留 `ok=false/status=error`；`team` 保留 `not_read`。ROOT 本机已有 57 资源的事实不受影响。本批不需要额外导出私有素材，也没有把公开树的商店缺项提升成完整五槽证据。

## 最小源码改动与来源规则

### 控件读取

新增 [currency_wars_refresh_offer.py](../../tools/currency_wars_refresh_offer.py)，其余生产改动限于 `currency_wars_perception.py`、`currency_wars_economy.py`、`currency_wars_runner.py`。

`semantic.refresh_offer` 的已知值仅有两种互斥形式：

```json
{"mode":"free","free_remaining":2,"paid_cost":null}
{"mode":"paid","free_remaining":null,"paid_cost":2}
```

完整外层还保存 `schema/version/status/snapshot_id/page/widget_bounds/widget_rgb_sha256/raw_rows/evidence/reasons`。数字必须为正整数。`unknown` 和 `not_read` 不携带已知模式或数值。full / economy 范围读取该控件，rewards 显式标为 `not_read`；read contract 版本升为 2，原全屏 OCR 链及其缓存版本不变。

读取先核当前 shop 页面、原生 1920×1080、固定完整控件、唯一高置信标题和 `D`；再核币标与数字的组合。已有主 OCR 足够则直接使用，否则只在当前数字小框调用同一已有 RapidOCR 引擎一次，`use_det=False/use_cls=False`，不缩放或填边，不再执行第二次全屏 OCR。局部结果独立保存，不污染原主 OCR 行或主 OCR 缓存。

币标使用从公开 f02 原图 `[1592,517,1625,549]` 裁出的 33×32 RGB 小模板，来源见 [SOURCES.json](../../tools/refresh_offer_resources/SOURCES.json)。运行时核模板大小和 RGB 摘要，再于当前原尺度搜索框内做灰度模板匹配，门槛 0.97。免费标签要求币标不匹配；收费标签要求币标匹配；资源损坏或读不到也保持 unknown。模板只覆盖这个已见外观，f02 同时是模板来源图，不能把它算成独立泛化正样本；旧 r00 同区域像素相同，也不多计正样本。

数字另有小范围几何守卫：在 `[1585,510,1675,558]` 中，付费模式只排除本帧已验证的币标框，要求 `gray<100` 的深色前景非空且全部落在当前模式的数字框中。三原图前景像素数为 137 / 81 / 109。这样发现数字框外笔画时会在 OCR 前拒绝；consumer 重新计算同一范围和摘要，不能仅相信保存的布尔值。这里的 100 是已观察深色文字的范围检查条件，OCR 置信度门槛没有改变。未见多位价格、低对比文字、变色、其他尺度或布局的真实覆盖仍缺失，不能由两个合成越界负例推成一般识别保证。

未采用的局部尝试也说明如下：包含币标的宽行 ROI 数字结果不可靠；原 25 像素价格框虽能读 `2`，仍有截断多位数的风险，最终向右扩至 1665 并加上述范围守卫；币标门槛 0.90 不能拒绝所测中心小遮挡，最终取 0.97。三个明确合成遮挡和两处合成越界笔画均有公开测试源码，不冒称新真实图或所有遮挡鲁棒性。探索性临时 raw 文件未作为公开验收交付；采用方案的全部真实原输出、拒绝断言和复现代码已公开。

### 现有经济消费者

| 接点 | 现在的行为 |
|---|---|
| `economy.refresh_offer / refresh_cost` | 只从当前域取数量或价格；typed unknown 禁止 fallback。完全旧版且从未声明新契约的数字协议保留兼容。 |
| `Worker.read_economy_fields` | native 契约出现或 `read_contract.version>=2` 后，先移除旧刷新数字 ROI 再读其余必需字段。新契约首次缺 key 也保持 unknown。 |
| `Worker.economy_observation` | 缓存键含当前模式证据、页面和契约；命中前仍核当前 PNG 字节 SHA，同 PNG 的新语义也不能复用旧结果。合法命中不重复解码或 OCR。 |
| 预算接受 | 明确提交的同域数字与当前已知控件冲突时拒绝；另一模式的旧字段不消费，并记入 `ignored_legacy_refresh_fields`。 |
| D 字段依赖 | 当前金币和当前刷新 offer；不添加无关等级、经验或阵容要求。付费搜牌原有攻略阶段授权若依赖等级，继续执行其原要求。 |
| 效果对账 | 免费 `2→1`、最后免费 `1→paid` 需原费用为 0；付费本笔按动作前实价核支出，动作后新价格只约束下一笔。后帧仍需当前金币及完整五槽。 |
| pending 恢复 | 保存原后帧 page、read contract 和 offer，绑定既有不可变 PNG；先核原归档，再允许原 SHA 相同的合法 unknown 用当前已验证事实补读。损坏来源不能被当前已知值覆盖。 |

一侧旧数字、一侧 typed 的原 pending 效果继续 unknown；没有自动把旧数字证明升级成新模式来源。零效果、未知效果、费用不符、免费数量跳变或输入回执不匹配继续保留 pending，不自动重发。

`command`、备战动作守卫、receipt fence、`advance_economy` 原 8 步循环、效果记账、显式恢复 CAS、出战审批，以及原数字绑定、预算和支出守卫均与 PR15 **AST 一致**，逐项摘要在验收 JSON。领奖／必要选择、攻略及任务、清明确无用库存、当前缺口／免费刷新／预算付费搜牌停止条件、再经验、上场及装备合成策略的顺序未改；标准利息／超频及最后备战当前来源确认沿用原规则。ROOT 每次出战鲜帧验收仍保留。

## 验收、真实边界与最小后续证据

[ACCEPTANCE.json](refresh-offer/ACCEPTANCE.json) 和 [完整选择日志](refresh-offer/focused-tests.log)：**20 个测试方法通过，0 失败、0 错误、0 skip，Linux / Python 3.12.14，14.696829 秒**。子用例不重复计数。选择由 5 个控件读取／负例方法、8 个 Worker 协议方法，以及直接受影响的 7 个原检查组成；未扩跑 PR15 原 9 项、7 个自然入口窗口或 PR14 全套。

8 个 Worker 方法使用生成 PNG、显式声明的控件事实、已匹配的惰性 Entry 回执、当前预算及攻略授权，不把三张混合动作后图拼成刷新链。已验内容包括：

- 协议 offer `free2→free1→paid2→paid3→paid3`：4 次 D，逐笔费用 `[0,0,2,3]`、实记 5、付费次数 2、终点无 pending；未声称来自真实四笔动作或自然 ROOT 往返统计。
- 当前价格 3、剩余刷新预算 2 时零发布；免费 D 不要求等级／经验／阵容。
- 零效果与 unknown 保留首笔 pending，显式再次调用不重发；v2 缺 key 禁止旧数字 fallback。
- epoch、页面、帧身份、同缓存键下 PNG 字节变化均拒绝；后帧归档恢复与来源损坏分别验收。
- 两份旧 group 的 `completed` 原样保留；单 D 协议回执中移植这些原列表只用于隔离动作不匹配的拒绝，生成的传输身份不冒称修复旧身份。未加身份的原投影另经实际 `observation_frame` 拒绝。

三张真图的 current offer 来源已经足以表达互斥控件语义。**仍缺真实逐笔效果证据**：原两组是 `D+三F` 和 `购牌+D`，组内无独立 D 后图，原 receipt.snapshot 仍是已缺失的共享 `game-preview.png` 别名。操作 ID / 标签关联无法替代接收器原生不可变帧绑定；不拆组、不推金币差、不补每笔 D 成功。

以后若只用既有留存补实际 D 效果，最小单元是一笔原单 D 请求及原 `completed`、该动作前后的原生不可变 PNG 身份、当前金币和商店五槽事实；要验证最后免费切收费则该笔前控件须为免费 1、后控件为当前收费 offer。找不到就保留此项未证实。本批不索取新游戏图，也不为 D 索要完整阵容或全套私有资源。

## 源码绑定与 Windows 聚焦复核

以下四个生产源码摘要同时见于测试和真实读取报告；另外 6 个 driver／测试／资源摘要也完整保存在两份 JSON，测前后均相同：

| 文件 | SHA256 |
|---|---|
| `tools/currency_wars_refresh_offer.py` | `1cd55c43821ba1f99fb0b07f4ae945f4a5146d83de5a538645d47a25454faa59` |
| `tools/currency_wars_perception.py` | `5f6ea6f6e874aa4719cf4dc15c35bac4ec15ddb050a638e0c505254e1c04293b` |
| `tools/currency_wars_economy.py` | `7a2c704510d9a064f893a4164921de7a0a7b1bdeef0594a9e783792e093ec9c7` |
| `tools/currency_wars_runner.py` | `5ea9659bc43c0fc1af1ce08968a135fdc03acffb9158d0686d4cbba126bf9e53` |

在新独立克隆根目录，用 ROOT 已有本地 OCR 环境的 `python` 运行；克隆保持 `core.autocrlf=false` 以核原字节。无需 Setup、GUI 或候选安装。下面只核这 20 项及三原图：

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'tools')
$acceptance = Get-Content 'handoff/2026-10-07/refresh-offer/ACCEPTANCE.json' -Raw -Encoding UTF8 | ConvertFrom-Json
function Assert-RefreshSources {
    foreach ($source in $acceptance.source_sha256.PSObject.Properties) {
        $actual = (Get-FileHash -LiteralPath $source.Name -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -cne $source.Value) { throw ('Source mismatch: ' + $source.Name) }
    }
}
Assert-RefreshSources
$checks = @($acceptance.selectors)
python -B -X utf8 -m unittest -v @checks
if ($LASTEXITCODE -ne 0) { throw 'Focused checks failed' }
python -B -X utf8 tools/replay_currency_wars_refresh_offer.py --output refresh-widget-windows.json
if ($LASTEXITCODE -ne 0) { throw 'Retained PNG read failed' }
Assert-RefreshSources
```

输出的 Windows JSON 仍保留完整原主 OCR、局部补读及未知；不需要把大型 Worker 状态重复上传。ROOT 可独立核对这里的字段与 source hash 后集中反馈。本批 Windows 结果留待 ROOT 实测，不把 Linux 结果称为 Windows 验收。

## 精简发布与成熟实践

本批不再重复尝试已知缺凭据的 CLI push。采用已授权 GitHub 接口上传本批代码、driver、小币标及紧凑交接，main 已公开的三图和 ROOT 大记录复用原 Git blob；不重新传图。发布后必须回读远端 commit 父链、tree、分支和 draft 状态，再 fetch 验证源码摘要。不得把本地提交当成已发布。

新增验收 JSON 25,749 字节、三图完整读取 32,379 字节、测试日志 4,130 字节，共 62,258 字节；没有删失败／unknown，也没有把未公开临时 raw 称成交付。后续完整系统 raw 若 ROOT 需要，可由同一冻结 driver 在本机复现并用 ROOT 可用 SSH 上传。这次字段结论的原输出已包含在公开的小报告内，不依赖额外私下材料。

本批重新打开两项既有第一手参考，只借鉴具体约束：[MaaFramework 管线协议](https://github.com/MaaXYZ/MaaFramework/blob/main/docs/en_us/3.1-PipelineProtocol.md) 将识别 ROI、匹配框和动作目标分开，并提供有界识别及错误出口；本批据此把完整控件、数字小框与未知出口显式表达。[Temporal 幂等说明](https://docs.temporal.io/activity-definition#idempotency) 说明动作可能已完成但回报丢失，去重必须由接收方实施；本批继续保留 pending 和原回执对账，不能靠请求 ID 或新后图自动重发。没有引入这些框架、第二控制器或追踪服务。它们不是本游戏识别率或速度的证据。
