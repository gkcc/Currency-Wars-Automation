# B-002：节点耗时的最小计时协议

初始实现基准：`3878f9ce9cdf427efd2fe58539a93c3794fbf3f8`。第一批提供可选的本地计时与离线报告工具，生产调用点由同批 runner 补丁接入。后续已实际读取本机上传至 `7e3400b47dba338ddfc3694c2724bb615e8e008b` 的53条部分主管计时样例，独立分析见 [PR #4 的报告](https://github.com/gkcc/Currency-Wars-Automation/blob/b41510c27caa08de7e5d1f57ae7ef9402ee4a336/handoff/2026-10-07/profile/README.md)。旧样例不满足本协议的完整节点/父子区间要求；当前仍没有改前改后的实机耗时结论，也不把用户反馈的 17:00 至 00:30 计算成精确测量。

## 在现有执行器中启用

本机按当前局冻结规则集成后，在原有公开 `start` 命令增加 `--profile`；需要前后比较时同时增加本机明确约定的 `--profile-comparison-key`。保留原有 `--chat-id` 等参数、唯一控制器和续接条件：

```powershell
python -B -X utf8 tools/currency_wars_runner.py start --chat-id $chatId --profile --profile-comparison-key standard_same_settings_v1
```

也可仅在启动当前 GUI/执行器的进程环境中设置 `CW_PROFILE=1`，不修改系统环境变量。`start` 会将显式参数传给所属 worker。正常收尾或受控失败的 `finally` 中调用 `finish_profile`，自动生成该次 `debug/runner-…/profile-summary.json`、`.csv`、`.html`；强制杀进程留下的未闭合 JSONL 可用下方离线命令诊断。

当前生产记录的 `source_sha` 是实际 `currency_wars_runner.py` 文件字节的 SHA256，**不是 Git 提交 SHA**。本机验收仍须另记实际检出的提交 SHA。测试和报告不会生成游戏通关标记。

## 采用方式

保留现有唯一输入 broker、请求台账、暂停优先、CAS、epoch 与出战验收。工具仅用 Python 标准库写入诊断事件，不调用截图或输入接口，不添加常驻进程，也不安装监控框架。

```python
from currency_wars_profile import ProfileRecorder

profile = ProfileRecorder(
    records_dir,
    run_id=owner['run_id'],
    source='worker',
    enabled=profile_enabled,
    source_sha=actual_source_revision,
    comparison_key='standard_same_settings_v1',
)

# stage 必须来自当前真实观察；其他字段只用于关联，不能替代业务证明。
profile.set_context(match_id=match_id, stage='3-1',
                    resume_epoch=current_epoch, phase='rewards')

with profile.span('read_frame', operation='ocr', snapshot_id=snapshot_id):
    observed = perception.read(immutable_frame)

# 跨 tick 等待：开始和结束绑定同一个 span，不把每次轮询都算一遍。
waiting = profile.start_span('await_decision', operation='decision', request_id=request_id)
# ... 当前请求得到有效答复、取消或失效时 ...
profile.end_span(waiting, outcome='returned')

# 控制器租期结束不是最后节点实际结束。
profile.close(complete=False)
```

默认关闭；关闭时不创建事件文件。开启后每个记录器独占 `profile-events-<source>-<session>.jsonl`，实际路径为 `profile.path`。worker 与主管各写自己的文件，在报告步骤统一读入。不要让多个进程覆盖同一个 JSONL 文件。

计时写入错误只设置 `profile.error`、关闭后续计时，不改变原业务异常或收据结果。特别是输入发布后，诊断写入失败不能被解释为输入未发布，更不能触发重发。报告生成在诊断/收尾路径处理失败，不应插在输入发布事务中。

跨 tick 的等待 span 由显式 `start_span/end_span` 管理；`current_span_id` 返回当前上下文中的父 span。内部操作优先挂到该 span，外层没有时再挂到当前等待 span。这样实际采集、OCR 和输入不会与主管等待重复相加。

## 实际接入点和口径

| 现有路径 | 记录内容 | 边界 |
| --- | --- | --- |
| `Worker.read_frame` | 识别阶段 `ocr` | 当前 `Perception.read` 还包含视觉状态解析，因此先沿用现有识别总耗时口径，不能宣称是纯 OCR 引擎计算时间。存储、完整解码校验可作为单独子段。 |
| `Worker.observe` 的 broker 往返 | 外层 `unknown`，名称说明 `broker_roundtrip`；收据有真实采集子段时归 `capture` | 不能把排队、IPC、采集和落盘总包全称为纯截图，也不能从总包时长猜各子段。 |
| `Worker.command` 的既有请求 | 外层总往返和实际收据子段；之后的识别另记 | 保留原请求 ID 与实际收据。计时不是动作生效证明。 |
| `ask` 至当前有效答复 | `decision` | 请求废弃、超时、epoch 改变时结束旧等待；新的等待用新 span。被动采集、实际恢复等子段应从外层等待中扣除。 |
| 明确接管及前台恢复 | `takeover`，阶段可为 `recovery` | 不根据非 worker 输入推断物理用户身份；仍以实际控制事务为准。 |
| 准备阶段状态机 | 原有准备阶段名 | 奖励、指南、整理、经济、布阵装备、出战验收分别记；未知页或阶段缺失保持未知。 |
| 实读战斗/结算页 | `battle` / `settlement` 阶段 | 战斗是业务阶段，不额外与截图/OCR等操作层相加。 |

仅 worker 文件覆盖时，`controller_wait` 的 `takeover` 表示 worker 等待外部接管结束的区间；它不能自行拆出主管实际识别、思考和输入时间。主管需要在同一运行时写自己的诊断文件并绑定同一节点/时钟来源；若与 worker 等待区间重叠且没有已关联的父子关系，仍保守归未知，不能按生产者名称猜优先级。本批已明确关联的是 worker 按本请求实际收据导入的 broker 子段；主管临时执行器尚待本机具体接入。原 broker 台账的外部输入计数仍单独保留，不能拿计时 span 次数当实际输入数量。

输入 broker 若提供同机同一运行时的真实 `profile_intervals`，可导入外层请求 span 的子区间：

```python
with profile.span('broker_roundtrip', operation='unknown', request_id=rid) as outer:
    result = existing_request(...)  # 现有唯一 broker
    # 调用侧先核验结果所属请求、运行时、clock 来源和实际收据。
    for part in result.get('profile_intervals', []):
        profile.record_interval(
            part['name'], start_ns=part['start_ns'], end_ns=part['end_ns'],
            operation=part['operation'], parent_id=outer,
            request_id=rid, receipt_id=rid,
        )
```

`record_interval` 只接受实际整数单调时钟区间。区间倒置、未来时间或错误类型会记录诊断并保留未知，不抛出使业务重入的异常。它不认证收据，不授予任何输入权限，也不自行推断来自另一台机器或重启前的时间是否可比。

阶段值固定为 `rewards`、`startup_guide`、`inventory_cleanup`、`economy`、`lineup_equipment`、`battle_acceptance`、`battle`、`settlement`、`recovery`、`unknown`。前六项与现有准备检查一致。操作层为 `capture`、`ocr`、`takeover`、`input_animation`、`decision`、`unknown`。

## JSONL 协议

每行包含 `schema=currency-wars-profile/1`、唯一 `event_id`、`session_id`、所属 `run_id`、`clock_id`、生产者 `source`、实际 `source_sha`、`monotonic_ns`、日志写入时的 `utc`。业务关联字段是 `match_id`、`stage`、`resume_epoch`，可为空；请求/收据/帧的 ID 仅在实际存在时填写。

事件类型为 `session_begin/end`、`node_begin/end`、`phase_begin/end`、`span_begin/end`，用同一 `id` 配对。span 可有 `parent_id`。`diagnostic` 保留不合法导入等明确缺口。

仅 `monotonic_ns` 用于时长和去重。`utc` 是写日志时的定位辅助，特别是导入 broker 子区间时会晚于实际动作；不能拿它替换动作开始时间。协议不记录账号、token、原图、输入坐标或整段模型正文。

运行时 `run_id` 默认同时作为 `clock_id`。同一个真实所属运行时里的 worker 与 broker 使用同机的单调时钟；不能仅因数值相似就合并不同租期。报告按 `(run_id, clock_id)` 独立计时，不跨系统重启猜时钟，不用 UTC 填满两个日志窗口之间的间隔。

## 去重、未知与完整性

1. 同一事件 ID 的相同副本只算一次，冲突副本列为证据问题。缺 begin/end、时钟倒退、边界身份不符均保留问题记录。
2. 阶段与操作是同一段时间的两种视图。每个节点各自满足“阶段之和 = 节点记录总时长”和“操作之和 = 节点记录总时长”，两张表不能相加。
3. 子段从父段中按区间并集扣除，父段自身时长为“父段区间减去所有后代区间的并集”。例如总请求 10 秒，实际输入 1–4 秒、采集 4–6 秒，则输入 3 秒、采集 2 秒、外层未知 5 秒，不能算成 15 秒。
4. 对互不构成父子的重叠操作，同类取并集；不同类别冲突部分归未知，不任意选择优先级。独立 span 的自身时长表用于审阅嵌套，不能再次把所有行相加作为整局耗时。
5. 节点从“首次可靠观察到该节点”计到“首次可靠观察到下一节点”。这是稳定可比较的观察边界，不能宣称精确到游戏像素刚出现的一刻。启动时首见节点默认标部分；未知后重识别、切换局身份同样不伪造完整起点。
6. 最后节点只有调用方证明实际结束才 `close(complete=True)`；7200 秒租期到期、进程退出、用户暂停等使用 False。未闭合 span 不猜操作时长；崩溃窗口最多计到日志最后一个实际时间点，其余留未知/未覆盖。
7. 同时出现不同节点身份的区间计入 `unassigned_seconds`，不会给两个节点各加一遍。节点身份冲突、部分边界不会生成该节点的改前改后比较。
8. 位面表明确列出已覆盖节点，当前按实际记录节点汇总；未证明已覆盖整个位面时标为 `recorded_nodes_only`。跨租期表是多个已记录窗口的合计，不是整局连续时长。

## 生成报告

```powershell
$profileFiles = (Get-ChildItem -Path $runRecords -Filter 'profile-events-*.jsonl').FullName
python -B -X utf8 tools/currency_wars_profile.py @profileFiles --output-prefix "$runRecords/profile"
```

输出 `profile.json`、`profile.csv`、`profile.html`。JSON 含节点、每个位面首节点、位面汇总、父子自身耗时、较大已测操作、源版本与证据缺口。CSV 的 `scope` 分为 `node`、`plane_first_node`、`plane`，须先筛选粒度，不能把三种粒度直接求和。HTML 给出同口径的可读表格与边界说明。

改前改后：

```powershell
python -B -X utf8 tools/currency_wars_profile.py @profileFiles --output-prefix "$runRecords/profile-after" --before "$baselineRecords/profile.json"
```

只比较协议相同、计时方法相同、生产者覆盖相同、显式 `comparison_key` 相同且两端完整的唯一同节点样本。未声明相同场景、日志缺失或多个同名节点无法唯一匹配时不生成提升结论。`comparison_key` 应说明双方约定的同模式/等级/目标/控制要求；不能仅因两个节点同为 3-1 就默认具有可比性。代码 SHA 单列为版本证据，不能用它代替场景键。

后续已读取的 `ROOT_PROFILE_SAMPLE.json` 使用 `root-manual-profile-sample/v1`：53条helper完成UTC与各进程自身seconds，缺完整节点、parent_id、外层起止及实际输入收据。分析保留该来源并另用 `root-manual-profile-analysis/v1`，没有填成本协议span。工具不会把旧耗时或UTC文本冒充精确的跨进程单调区间。要完成本协议的归因与前后比较，仍需原始头尾与完整父子操作样例、时间单位/时钟来源、节点/请求/收据关联和缺失说明；仅适配能验证的部分。

## 如何解释第一份真实报告

先看未知空档和部分边界，再看耗时最大的已测操作。采集/OCR占比高时核对重复次数和调用范围；接管/决策占比高时核对是否发生逐点击往返、所有者未变化却重复交接；输入动画占比高时核对真实收据与状态驱动等待。较大时长只能定位继续查证的范围，不能自动证明根因。

优先优化现有路径中重复的全屏识别、稳定说明文字重复读取、机械动作逐次主管往返；之后才根据实际热点补局部读取或状态驱动批次。第一批没有改经验键；后续本机已实证 F（70），B-005 的 `e05ed2cf45d4c1ba5bc7dd8c34b8aa26119e7414` 已统一备战/商店的 F 经验与 D（68）刷新，E（69）在这两个页面明确拒绝。它们仍走现有 broker，并按统一预算和实际免费次数、金币、经验差额逐笔回验；大世界 F 仍按页面执行原交互。B-005尚未在本局安装，不能将本机临时助手的F成功算作新分支实机通过。动态资源和每次出战验收继续使用新帧。

B-005 的经济依赖现已实现：先购买当前明确缺口，免费刷新并处理新缺口，明确付费搜牌预算、次数、购牌留资和停止条件，再决定购买经验。参见 [B005_IMPLEMENTATION.md](B005_IMPLEMENTATION.md)。每笔仍有当前感知路径、实际结果守卫和新帧；自动减少全部全屏 OCR、标准末轮的版本规则与实测提速尚未完成，计时接入本身不证明这些目标。

本批聚焦检查使用合成计时时间验证算法与文件路径，不包含真实游戏输入，不证明新版本完整自动化通过。运行：

```text
python -B -X utf8 -m unittest discover -s tools -p test_currency_wars_profile.py
```

另已复用现有 `manual_bridge_fixture/frame_worker` 做一次启用计时的产品路径检查：真实 `Worker.observe` 两次、跨 tick 等待、`Worker.command` 一次及 `finish_profile`，生成 28 条事件，问题列表为空，三种报告均可读，首见/最后节点保持部分。夹具只有内存结果和合成 PNG，真实游戏输入为 0；这验证接入与收尾路径，不提供实机速度数据。

## 成熟做法及采用范围

- [Python 单调时钟文档](https://docs.python.org/3/library/time.html#time.monotonic)：时间差用不会随系统时钟调整倒退的单调时钟；纳秒整数保留原始区间精度。Windows 的实现使用性能计数器。只在已确认的同机时间域内合并，实际测量分辨率仍受系统和观察调用点限制。
- [OpenTelemetry 跟踪规范](https://opentelemetry.io/docs/specs/otel/trace/api/)：外层请求覆盖完整往返，子段记录具体操作，并通过父子身份关联。此处借鉴这种区间表达和关联方式，实现最小本地 JSONL；没有接入其 SDK、采集服务或远程导出。区间并集、冲突归未知是本项目为可审查的墙钟归因作出的明确实现选择。

以上主源于 2026-10-07 实际读取。网页描述的通用能力不等于本游戏已验证的速度改进。
