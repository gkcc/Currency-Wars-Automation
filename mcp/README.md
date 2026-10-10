# Currency Wars stdio MCP

这是原执行器的薄适配。MCP 进程不启动游戏、执行器或 broker，不提供 start/resume/raw input；服务启动和工具列举不会导入游戏输入层。不访问模型服务、不需要 OpenAI API key。

| 工具 | 行为 |
|---|---|
| `get_state` | 原 `status` 核验的当前字段、unknown、来源、快照、请求/epoch、源码版本和准备清单。不主动截取新画面。 |
| `get_task_status` | 原 `status` 的运行身份、进展、待决项；候选经济契约可用时读取同 run/match/stage 原生台账的实花、购牌和 pending。 |
| `submit_plan` | 将完整 `reply` 原样传给原 `decide`。须包含当前 request_id、snapshot_id、resume_epoch 和有限语义动作；原身份、期限、PNG、准备阶段、预算、唯一 broker 守卫继续有效。 |
| `pause` | 原应急 pause CLI，锁定已有暂停意图，不继续或输入。 |
| `run_economy_plan` | 原生有界经济计划桥接；本 main 基线返回 `unsupported`，不会替换成盲刷/盲买循环。见下面的候选边界。 |

`submitted` 只说明 `decide` 接受了回复，worker 尚须核验和执行。它不是购买、刷新、准备完成或整局完成的证明。执行器可能因新画面、暂停、未知资源或准备未完成拒绝。CLI 超时返回 `submission_unknown`；先核状态，不自动重发。空 session、历史截图模式或无效原身份均不会发布游戏操作。

main 的旧全局 Lv 正则会把角色/首领等级误读为玩家等级；MCP 不把它当成已知玩家字段。只有同快照、actor=player 且有原生 HUD 出处的读取才能输出 player_level，否则保持 unknown，不补猜截图里的真实等级。

## 安装和 D 盘注册

SDK 采用独立可选依赖，不能装进已经有严格安装指纹的游戏 `.venv`。使用 Python 3.11–3.13，在 PowerShell 中：

```powershell
$project = 'D:\Codex\Workspaces\2026-10-10\currency-wars-mcp'
$env:TEMP = 'D:\Codex\Temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\Codex\Temp\currency-wars-mcp-pip'
python -m venv "$project\.mcp-venv"
& "$project\.mcp-venv\Scripts\python.exe" -m pip install -r "$project\mcp\requirements.txt"
codex mcp add currency-wars -- "$project\.mcp-venv\Scripts\python.exe" -B -X utf8 "$project\tools\currency_wars_mcp.py" --runner-project $project --runner-python "$project\.venv\Scripts\python.exe"
codex mcp list
```

`.venv` 是当前实际部署项目的游戏 runtime；若 MCP 源码与被核准的 runtime 不在同一项目，将 `--runner-project` 和 `--runner-python` 指向实际部署路径。仅登记 MCP 不产生运行授权。此分支不会安装/复制现有游戏 runtime、模板、就绪批准或输入组件。

注册后的默认结果为 `no_session`。操作已有 runner 时，必须在启动 Codex/MCP 的进程环境提供该次**已授权**运行的原身份：`CURRENCY_WARS_CHAT_ID`、`CURRENCY_WARS_RUN_DIR`、`CURRENCY_WARS_RUN_TOKEN`。不用工具参数填写 token，不从 CURRENT_RUNNER 自动接管其他聊天身份，不在 Git、README 或模型工具结果中保存 token。三个环境变量只绑定当前 MCP 服务生命周期，重启服务才能换 run。原 runner 会再次验证标准归属标记、创建身份和 token。实际 Codex 宿主还须允许继承这些变量；缺失时工具保持 `no_session`。不要把示例身份当成授权或凭据。

历史截图的只读注册示例（不需要任何 run 凭据）：

```powershell
codex mcp add currency-wars-offline -- "$project\.mcp-venv\Scripts\python.exe" -B -X utf8 "$project\tools\currency_wars_mcp.py" --runner-project $project --runner-python 'D:\codexWorkSpace\货币战争\.venv\Scripts\python.exe' --offline-image "$project\handoff\2026-10-07\fixtures\shop_actual_player_level_8.png"
```

该模式调用原 `Perception.read` 和 `ShopReader`，标明 `offline_image_perception`。未知本机模板仍是未知；截图不是当前游戏状态。`get_task_status`、`submit_plan`、`pause` 在此模式返回 offline，不操作已有游戏。

## 有界经济计划的候选契约

已只读检查 PR16 `5938648de4cfd4b5cd3cd723898abb3c2f806a48`。其原 Worker 已有购买→免费刷新→有限付费刷新→经验的本地连续事务、准备核验、预算台账、部分/未知结果停止和唯一 broker。该候选依赖尚未合入 main 的 PR1–16；此 MCP 分支没有擅自合入它们，main 缺少该公开计划契约。因此 main 的 `run_economy_plan` 明确返回 unsupported。

若未来另行审查、批准并部署了该**精确候选**，服务启动可显式加 `--enable-candidate-economy`，并将 runner 项目指向那个部署目录。适配检查七个关键候选源文件 SHA256；其他版本保持 unsupported，不通过函数名猜测兼容。它不生成 RUNNER_READY，不解除暂停，不批准候选安装，不绕过原始 broker 固定源码校验。

调用参数：

- `goal`：当前已核目标与停止理由，1–1000 字符。
- `budget`：`purchase`、`refresh`、`experience` 三个非负整数累计上限，均不超过 10000。
- `max_refreshes`：0–100，与原计划 paid_search.max_refreshes 一致。付费次数、免费次数和原生输入总预算继续由原 Worker 控制。
- `binding`：当前 `request_id`、`snapshot_id`、`resume_epoch`、`match_id`，四者均须匹配。
- `reviewed_plan`：候选的完整 `currency-wars-economy/v1`，包含 schema、reviewer、revision、stage、mode、shop_reviewed、purchase_only_targets、targets、budget、paid_search、experience、reserve、fields。字段/ROI 必须来自当前真实完整原图，unknown 写 null；预算与 max_refreshes 须和独立参数相等。参见候选原 [B005 实现契约](https://github.com/gkcc/Currency-Wars-Automation/blob/5938648de4cfd4b5cd3cd723898abb3c2f806a48/handoff/2026-10-07/B005_IMPLEMENTATION.md)。

适配只构建当前请求 proof，以独立无输入 `finish_preparation_review` 动作经原 `decide` 提交 `context_update.economy_plan`。它不把准备阶段标为完成，不推断未知费用，不移植另一套购买/刷新控制器。原 Worker 接受、核验后自行有界推进，后续 `get_task_status` 报告原台账实际效果与待决项；没有台账或不能核其身份时效果为 unknown。当前尚未实机验证候选计划提交、Windows 键位、预算实际扣减或完整原生连续流程。

注册语法已核本机 `codex mcp add --help`；本次仅交付命令，没有修改用户全局 MCP 配置。协议使用官方 [MCP Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/)，独立环境锁定 1.30.0；源码版尚没有整局无人值守保证。
