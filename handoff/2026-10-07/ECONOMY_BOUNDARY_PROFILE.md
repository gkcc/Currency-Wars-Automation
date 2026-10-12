# 原生经济入口补证交付

## 优先结论

本批交付现有原生经济循环的精确停点记录。历史慢流程运行 `3878f9c`，当时没有当前经济循环；当前 PR14 的 D/F 又不依赖完整阵容。下一步应先核经济入口及刷新控件的读数契约，不能继续把所有回传归为阵容识别不足或模型慢。

本批没有减少 ROOT 请求，也没有整局提速证据。完整业务归因、历史事件索引、成熟实践与最小素材清单见 [业务瓶颈复核](BUSINESS_BOTTLENECK_REVIEW.md)。本轮没有启动游戏、控制器、broker 或 GUI，没有当前游戏采图、候选安装和在线模型调用。

## 基线与修改范围

- 完整代码基线：PR14 `85ba75af2b1d43cf34f3b116b3767783709d82f5`。
- 最新公共证据：main `c8c09213aade00748a722452a0bf4fc5dca095b6`，发布前再次 fetch 核对。
- 独立分支：`fix/offline-business-bottleneck-20261007`。以 PR14 分支为草稿 PR 目标，不把 main 旧核心当开发基线。
- 合并证据后的开发起点：`76711d9739679328997b504518ea408a131c80e5`，两父分别为上述 PR14 和 main。运行前未提交的生产修改，以运行前后文件 SHA256 绑定；最终发布 SHA 由草稿 PR 与回读记录给出。

生产只改 [runner](../../tools/currency_wars_runner.py) 和 [profile](../../tools/currency_wars_profile.py)。读取器、经济规则、原始 Entry、奖励和布阵/出战守卫没有业务修改。新增 [离线驱动](../../tools/replay_currency_wars_economy_boundary.py) 和 [聚焦检查](../../tools/test_currency_wars_economy_boundary.py)。

`advance_economy()` 在已有可选 profile 启用时记录入口和各个实际出口。`tick` 的商店/备战入口分别标明来源，直接调用保留 `direct_call`。日志构造复用已经计算的 policy，不额外观察、调用 OCR 或计算一次经济策略；八步上限、输入顺序、原返回值及请求文案保持原样。诊断失败会关闭本次记录，原业务继续按原结果返回；原业务异常仍原样抛出。

## 点事件说明

使用既有本地记录器，事件 `kind=economy_flow`；同次调用的 `entry/exit` 共享 `flow_id`。汇总 JSON 的 `economy_flow_events` 单独保留这些原事件，不将它们计为 ROOT 请求或时间区间，也不按与某条请求相邻就自动建立因果。

| 内容 | 具体含义 |
| --- | --- |
| 当前观察身份 | `snapshot_id`、`capture_request_id`、`frame_id`、`captured_at`、页面、局/节点/epoch |
| 实际调用来源 | `tick_shop`、`tick_preparation` 或 `direct_call`；不能用于追认旧核心曾进入该循环 |
| 预算 | 是否有绑定、原 scope、与记录时 scope 是否匹配、revision；不导出完整策略知识 |
| 已计算策略 | available、原 reason、依赖阶段/原因、商店完整性、下一动作类型、经验是否解决 |
| 策略来源身份 | 另记 policy 的输入帧和经济读数 SHA。最后后图与此前策略输入可能不同，两者不混用 |
| 必要字段 | 依据原 pending 动作、下一动作或已计算依赖确定；没有 available policy 时保持 null，完成复核不要求无关经验字段 |
| 原未决交易 | 从已载入内存台账取原请求、类型、已有结果和发布标记；`ledger_loaded=false` 时 pending=null 不证明磁盘无遗留交易 |
| 完成情况 | `completion_attempted`、`completion_predicate`、`completion_accepted` 分开；`completion_guard_detail=null`，不能据此确定是哪条 CAS/控制/收据守卫拒绝 |
| 本次推进 | 只数原经济执行函数实际返回成功的动作数；发布了但效果未知/为零的输入不会计为成功动作 |

出口 `policy_unavailable` 保留原失败原因。它既可能是预算尚无绑定，也可能是已有预算但当前证据失效，不能用这个词直接统计“缺预算”。`no_actions` 只说明原无动作分支未完成；必须结合原依赖、必要字段、pending 和完成谓词判断。`economy_completed` 才表示原完成函数已接受；`transaction_return` 保留原未决交易交接；`step_limit` 是八步界限，不能当成预算完成。另保留阶段不符、升级后策略不可用及原异常出口。

旧 `return_reason.category=unclassified` 没有被改成猜测分类。报告中的 `normal_root_requests` 只沿用“非 exception”的计数口径，并另列 `unclassified_root_requests`；它不代表必要策略次数。

## 同终点验收

### 七个自然入口协议窗口

驱动先丢弃旧 fixture 自己构造的初始化请求，再由真正 `Worker.tick()` 产生初始预算请求。基线与候选使用同一冻结脚本、独立进程和相同 case 顺序。初始合成观察在窗口前已经准备好；报告只计自然请求、答复和消费者引起的读取，不拿这个读取数和 PR14 的另一计时窗口直接相减。

以下所有数值帧、空商店、攻略/预算、前三阶段完成标记和匹配回执都是**显式协议前提**。没有真实 OCR，不等于真实六 D/三 F，不表示前三阶段实际完成，也没有补成完整备战 6/6。

| 场景 | 原始请求计数：非异常 / 异常 | 惰性输入与实记 | 停点 |
| --- | --- | --- | --- |
| 未绑定预算 | 1 / 0 | 0 笔、0 实记 | 经济；明确无绑定，必要字段不猜 |
| 有预算但未提供免费次数 ROI | 2 / 0 | 0 笔、0 实记 | 经济；实际依赖免费次数，不要求完整阵容 |
| 有预算但读取函数返回证据错误 | 2 / 0 | 0 笔、0 实记 | 经济；保留证据错误，不误标无预算 |
| 一笔 F 返回零效果 | 1 / 1 | 惰性 F 1 笔，经验实记 0 | 首个 `economy_result`，原 pending 保留 |
| 完成谓词满足但守卫拒绝 | 2 / 0 | 0 笔、0 实记 | 经济；完成未接受 |
| 六次 D 达停止条件 | 2 / 0 | 惰性 D 6 笔，刷新实记 12 | `lineup_equipment`，pending=false |
| 三次 F 达目标 | 2 / 0 | 惰性 F 3 笔，经验实记 12 | 同上，出战仍未验收 |

初始和待布阵请求均纳入，保持原请求分类。未决交易场景止于首个异常交接；强制重入防重发由原有专门检查验证，不冒充主循环在等待决策时自然再次 `tick`。

比较保留原请求原因、帧 SHA、费用、键参数、pending 和报告定义的完整终点。独立进程会生成不同请求 ID，因此只将 `economy_result` 原因中**经当前台账核对的那一个 pending 请求 ID**映射为同一个占位符；原 ID 和原原因仍在 profile 证据中。除此之外未归一化比较字段。本驱动没有坐标动作，也未把 wait 参数纳入 endpoint；坐标与等待守卫沿用原验收及本批静态未改核对，不声称新增运行覆盖。

原始记录：[PR14 自然入口](economy-boundary/pr14-natural-entry.json)、[候选自然入口及对照](economy-boundary/candidate-natural-entry.json)。实际七项 `same_endpoint` 全为 true、差异字段全为空；请求序列、读取次数和 `economic_policy` 调用次数均一致，没有记录器错误或 profile issues。候选仅新增入口/退出点事件。这是这些协议窗口的行为等价和诊断验证，没有量化性能收益。

### 聚焦检查与独立审查

[验收 JSON](economy-boundary/FOCUSED_ACCEPTANCE.json) 和 [原始日志](economy-boundary/focused-tests.log) 记录新诊断检查与直接受影响的原检查：Linux 本轮实际 9 项通过、0 失败、0 错误、0 跳过，用时 6.7429 秒。覆盖自然预算入口、无关策略失败、必要字段、完成守卫、原 pending、开启/关闭后的同终点与读取次数、交易完成后记录失败、原业务异常和点事件不增加时间/ROOT 请求。此用时是协议检查耗时，不能换算游戏提速。

独立静态审查未发现生产阻断问题；已据审查把 pending 驱动停在首个异常交接，并明确内存台账未加载和记录器失败的出口。[静态保留记录](economy-boundary/UNCHANGED_GUARDS.json) 核对八个 Worker 方法 AST、五个未改文件字节与 PR14 一致；这是静态证据，不是额外测试通过数。没有重跑不受影响的历史全套或真实 OCR。PR14 的 q02 / q00-q01 Windows 实测、原 completed 坐标及 wait 不匹配拒绝，继续引用原验收；本批不冒称重新执行了这些真图窗口。

### 本批源码 SHA256

四个源码文件在最终聚焦运行前后摘要相同；自然入口两次对照使用同一冻结驱动。以下摘要绑定未提交测量时的源码内容，最终提交身份另以发布回读为准。

| 文件 | SHA256 |
| --- | --- |
| `tools/currency_wars_runner.py` | `dbdb3e94d83fb0f3a91fc3879c3a9822abfaa4a60d572dc0d67c9b55e384ed9a` |
| `tools/currency_wars_profile.py` | `e913f23d6d2b86d21ea178130a32f7e0b1daa6d2f8d0e161024be896d24717cc` |
| `tools/replay_currency_wars_economy_boundary.py` | `e3fdedc2e41b5be09a03a945785fd06cbaf5d65417aa1572f48afccf5c30781a` |
| `tools/test_currency_wars_economy_boundary.py` | `c871ca3e2b0427a18ecfc799928a5a2ecdec0e0f2bfc110ff1c7527009b3b668` |

## Windows 聚焦复核命令

在独立候选检出中使用本机现有 Python 依赖。这组测试与驱动不需要复制 57 个私有图片资源，也不需要运行游戏组件。

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'tools')
$selectors = @(
  'test_currency_wars_economy_boundary',
  'test_currency_wars_economy.EconomyTests.test_published_zero_effect_stops_without_replay_and_reconcile_is_atomic',
  'test_currency_wars_profile.ProfileTests.test_business_steps_partition_nested_time_and_keep_actual_return_reasons'
)
python -B -X utf8 -m unittest -v @selectors
```

同一冻结候选驱动分别加载完整 PR14 和候选代码。两个命令按顺序、独立进程执行，输出目录按本机实际填写。代码输入必须是独立检出，不指向已安装游戏运行目录。

```powershell
$baselineRoot = 'C:\offline-review\pr14'
$candidateRoot = (Get-Location).Path
$driver = Join-Path $candidateRoot 'tools\replay_currency_wars_economy_boundary.py'
python -B -X utf8 $driver --code-root $baselineRoot --output boundary-before.json
python -B -X utf8 $driver --code-root $candidateRoot --compare-to boundary-before.json --output boundary-after.json
```

命令退出码必须为 0；比较项 `same_endpoint` 全为 true、`different_endpoint_fields` 全为空，且无 `profile_error` 或 `profile_issues`。候选每次实际进入循环有对应点事件；原基线事件为空是未安装此诊断的事实，不能当成未调用循环。

## 已交付与下一步

已修：现有原生经济调用的入口/退出缺少可直接审查的关联证据。已离线验：新点事件对应原代码分支，诊断开关和失败不改变已核业务终点，生成数据保持显式前提。尚缺：Windows 独立复核、旧刷新控件免费/收费表达的可靠语义、真实预算与原七图的字段绑定，以及完整备战/整局的执行与时间证据。

ROOT 下一次优先补已有 r00 刷新按钮的完整控件说明和来源；有免费态旧例则给其现有索引，没有就明确无。**不要寻找或伪造一个未显示的“免费次数 0”数字 ROI。** 如果来源确认当前按钮的免费/收费表达足以决定下一次动作，则下一批沿原守卫修这一个字段契约；如果仍缺互斥语义，先保持 unknown。此时不要求阵容全读，也不接 Jev 或视觉服务。

历史其他往返如需进一步归因，只提取对应已有请求/回执/前后图与策略记录的最小关联，原始缺口继续为缺口。87.58 分钟工具外时间仍未知，本批没有把它变成可节省的模型时间。每次 ROOT 出战鲜帧验收继续保留。
