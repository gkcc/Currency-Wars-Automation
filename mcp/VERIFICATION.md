# MCP 首版离线核验

## 本地读取适配刷新：2026-10-11

仅修改 MCP 适配及本目录说明，未修改 runner、输入、经济引擎、注册配置，未发送游戏操作。当前改动修正 captured_at、同帧 HUD XP、refresh_offer，新增有出处的公开 semantic、补读请求、context/inspection/static 分区；v1 coins.value 保留原形状，数值新增 normalized_value。PR16 七文件指纹和原执行守卫未放宽，当前经济接口仍 unsupported，原因是契约未核准而非缺少原生经济引擎。

- 既有4项 MCP 边界及3项准备/库存守卫通过，7/7。
- 原 refresh_offer 5项、PerceptionScope 3项及 WorkerReadScope 1项通过。扩展的12个测试方法另有3个 WorkerReadScope 方法产生7个子测试错误，均在未修改的 runner.ensure_full_observation 拒绝“当前请求完整读取契约未满足”。它们不调用 MCP，本次不越界修改 runner 或旧 scope fixture；不能报告整个扩展模块全绿。
- 归属临时 run 内的一过 fixture 验证 known/unknown、缺值/异帧拒绝、原 proof/age/checked/scope 保留、静态定义不提升动态 known、token/gui/owner 文本脱敏。实际子进程 exit2 的 JSON 原拒绝、reason_kind、needs_user_confirmation、input_sent 被保留；非JSON stdout才使用通用拒绝。所有 fixture 和逐调用目录实际删除，原子进程已退出并等待。
- 另用实际无游戏子进程核对超时和取消：两种路径都停止并等待原子进程，清理所属调用目录；取消继续向调用者传播，没有自动重试。
- 独立审查补修：成功 CLI 的 artifact_cleanup 告警在 get_state、get_task_status、submit_plan 和经济提交中保留；经济 status/decide 两次都有告警时以 runs 分别记录，原拒绝和 needs_review 也保留此前的告警。现有7项聚焦检查再次通过；一次性成功 transport fixture 覆盖6条路径，验证告警路径/脱敏/原拒绝，mock已还原，未调用游戏CLI，所属临时 run 实际删除。
- 真实历史 PNG `shop_actual_player_level_8.png` 经原 Perception 子进程及新适配，读出玩家8、金币68、XP2/72、付费刷新费用2；免费次数仍 null。人口、购买XP费用/增量保持 unknown。source 为 offline_image_perception，captured_at=null，不主动抓取当前窗口。首次沿用旧“XP/刷新unknown”期望失败，读取输出后确认它们原来已能被原生读取，失败来自旧期望，符合此次映射修复。
- 历史 PNG 是只读链验证，不代表当前局；本次未重新验证真实 SDK 宿主环境继承或授权 live runner 的 status/decide/effect。既有宿主服务需重新加载适配后再核新字段。原生经济接口仍须另行契约审查。

## 本机落实：2026-10-11

用户授权按 Git 最新方案落实 MCP，本次范围为连接与离线验收，游戏仍停止。单份当前结果见 [CURRENT_VERIFICATION.json](../docs/CURRENT_VERIFICATION.json) 的 `mcp_connection`；以下首版记录保留为历史，不能覆盖本次事实。

- 本地游戏基线 `d0056bdc03bc967d5138781f29518a970b7ceab9`；fetch 后远端 main 为 `ab16270`（#34 文档），MCP 实现取自 `e65bd21111c348fef144a163493189258c9e1475` 的五个新增文件，未合并主线。SDK 1.30.0 安装于本地独立 `.mcp-venv`，原游戏 `.venv` 未改。
- 既有四项 MCP 边界检查和三项准备阶段守卫检查通过，7/7。真实 SDK stdio 的 initialize/list/call 通过，五个工具齐全；无绑定返回 `no_session`，经济计划返回 `unsupported`，所有调用 `input_sent=false`。
- 同一历史 fixture 在本机当前原生 HUD 读取器下正确输出玩家8级和68金币，来源分别为 `native_player_hud`、`native_hud_row`；人口、经验、刷新仍 unknown。远端旧基线预期玩家等级 unknown，不适用于本机已具备原生 HUD 的读取器；核验改为检查来源与真实值，没有修改识别代码。
- 已实际注册全局 `currency-wars` 服务，当前对话原生 `mcp__currency_wars` 的状态与任务状态调用成功，经济入口确认 unsupported。Codex 注册命令曾删去无关的 `node_repl.args`，已从注册前回滚精确恢复；全文 TOML 语义对比确认除新增服务外原配置不变。配置不保存会话 token。
- 当时独立审查通过连接安装与离线边界；记录了采集时间、经验/刷新映射、结构化拒绝和临时归属四项问题。这些读取兼容问题已由上面的本地适配刷新处理；实机仍未验证，不能据连接成功批准实机部署。
- SDK/OCR 测试子进程已退出，安装与协议临时目录实际删除。额外宿主探针未返回结果，其所属 Python 创建身份经核验后单独停止，无子进程，原归属临时目录经受保护入口实际删除；它不替代已经成功的当前对话原生调用证据。

接口是本地 stdio 桥接，不是公开互联网服务，也没有实现远端主线文档中提出的自主求解器。下一步应在新的游戏授权下先完成上述少量兼容验收，再以原身份绑定既有执行器；不要扩大为全自动重写。

## 远端首次交付历史：2026-10-10

基线：`origin/main 4cf23830e7832ee68b4ef40ba688b006ee3064fc`，分支 `codex/currency-wars-mcp`。2026-10-10，Windows，原 CPython 3.12 runtime；SDK 1.30.0 独立安装于 D 盘临时环境。本文件为当前变更的单份核验记录，未启动星铁、未发送游戏输入、未调用模型 API、未配置 API key、未改变原游戏 runtime/模板/全局 MCP 配置。

## 实际协议证据

使用官方 SDK `ClientSession` + `stdio_client` 启动真正 `tools/currency_wars_mcp.py` 子进程，逐次执行 `initialize`、`list_tools`、`call_tool`；不是直接调用 Python 工具函数。

- initialize 协商 `protocolVersion = 2025-11-25`，server name `currency-wars`，SDK version `1.30.0`。
- list_tools 精确五个工具：get_state、get_task_status、pause、run_economy_plan、submit_plan。
- 无 session 模式：get_state/get_task_status/submit_plan/pause 均为 `no_session`、`input_sent=false`；run_economy_plan 为 `unsupported`、`input_sent=false`。
- 实际原尺寸 fixture `handoff/2026-10-07/fixtures/shop_actual_player_level_8.png`：get_state 为 `offline`、source `offline_image_perception`、page `shop`、stage `3-4`、coins `68`；snapshot SHA256 `7ffc0c065f665754a5ce2d7b4e278cbca3078c2cda0ee39403a0783dddca135c`。无私有商店模板，五槽 unknown。旧 Perception 把角色 Lv.999 误读为玩家 `99`，MCP 将未经 player actor/HUD 绑定的玩家等级保留为 unknown，没有编造实际8级。population/xp/refresh 同样 unknown。
- 历史 fixture 模式：submit_plan/pause 为 `offline`、`input_sent=false`。
- 两次服务器会话均已由 SDK context 退出；OCR 子进程 communicate 返回并核 exit code 0。

首次真实 stdio fixture 调用出现30秒超时；相同 Perception 直接进程约6.7秒返回。定位为子进程继承 MCP stdin 的差异：CLI 和 OCR 的 stdin 改成 DEVNULL 后，同一真实 initialize/list/call 全部通过，总命令约8.04秒。没有通过扩大等待或伪造已知字段规避。

## 最小检查

4项稳定公开边界检查及3项既有准备阶段/购买守卫通过（7/7，0失败/错误/跳过）。新增检查仅涉及误执行入口、无身份拒绝、main不冒充候选契约、unknown及凭据脱敏的显著边界风险；没有新增常规游戏动作mock/测试框架。

```powershell
$env:TEMP = 'D:\Codex\Temp\currency-wars-mcp-worker'
$env:TMP = $env:TEMP
$env:PYTHONPATH = 'D:\Codex\Workspaces\2026-10-10\currency-wars-mcp\tools'
& 'D:\codexWorkSpace\货币战争\.venv\Scripts\python.exe' -B -X utf8 -m unittest test_currency_wars_mcp test_local_runtime_compatibility.RuntimeCompatibilityTests.test_unclaimed_rewards_block_spending_and_selling test_local_runtime_compatibility.RuntimeCompatibilityTests.test_preparation_reviews_survive_navigation_but_inventory_invalidates_downstream test_local_runtime_compatibility.RuntimeCompatibilityTests.test_inventory_mutations_cannot_reuse_coordinates_within_a_plan -v
```

两个新 Python 文件 AST 解析和 `git diff --check` 通过。注册命令按本机实际 `codex mcp add --help` 验证；未实际写全局配置。

## 生产入口与边界

生产 get_state/get_task_status 实际入口是单独 runtime 子进程执行 `currency_wars_runner.py status --chat-id <绑定> --run-dir <绑定> --run-token <绑定>`。原 load/current_state 核归属、worker创建身份、原 broker 状态；MCP未自行抓窗口或控制输入。submit_plan 同样调用原 decide；pause调用原应急pause。服务自身仅标准库/SDK，启动不导入runner/control/input_bridge。

候选经济功能只读审查 PR16 SHA `5938648de4cfd4b5cd3cd723898abb3c2f806a48`，没有合入。精确版本、显式opt-in、原准备条件及全套计划事实均满足后，桥接原 `context_update.economy_plan`，随后由原 Worker.advance_economy 执行；原记录台账按run/match/stage核验读取。本main为unsupported。

未验证：授权live runner的status/decide/pause、真实MCP宿主注册/环境继承、候选的生产计划受理/连续流/扣款、Windows键位与整局行为。依用户暂停要求，这些仍未实机验证；离线成功不能代替它们。
