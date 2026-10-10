# MCP 首版离线核验

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
