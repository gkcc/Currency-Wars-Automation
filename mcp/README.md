# Currency Wars stdio MCP

这是原执行器的薄适配。MCP 进程不启动游戏、执行器或 broker，不提供 start/resume/raw input；服务启动和工具列举不会导入游戏输入层。不访问模型服务、不需要 OpenAI API key。

| 工具 | 行为 |
|---|---|
| `get_state` | 原 `status` 核验的当前字段、unknown、来源、快照、请求/epoch、源码版本、准备清单及公开观测/上下文/静态定义。不主动截取新画面。 |
| `get_task_status` | 原 `status` 的运行身份、进展、待决项；候选经济契约可用时读取同 run/match/stage 原生台账的实花、购牌和 pending。 |
| `lookup_catalogue` | 只读本地版本化数据库，返回角色、羁绊、装备、投资和敌人定义；默认精简字段，可请求详细信息。不截屏、不输入。 |
| `get_decision` | 读取一次当前绑定状态，经数据库解析后调用本地求解器。返回候选与缺失事实，不提交或执行候选。无会话、旧帧、准备证据不足时保持 `needs_review`。 |
| `submit_plan` | 将完整 `reply` 原样传给原 `decide`。须包含当前 request_id、snapshot_id、resume_epoch 和有限语义动作；原身份、期限、PNG、准备阶段、预算、唯一 broker 守卫继续有效。 |
| `pause` | 原应急 pause CLI，锁定已有暂停意图，不继续或输入。 |
| `run_economy_plan` | 原生有界经济计划桥接；当前适配契约及源码指纹尚未核准，返回 `unsupported`。不会替换成盲刷/盲买循环。见下面的候选边界。 |

`submitted` 只说明 `decide` 接受了回复，worker 尚须核验和执行。它不是购买、刷新、准备完成或整局完成的证明。执行器可能因新画面、暂停、未知资源或准备未完成拒绝。CLI 超时返回 `submission_unknown`；先核状态，不自动重发。空 session、历史截图模式或无效原身份均不会发布游戏操作。

只有同快照、actor=player 且有原生 HUD 出处的读取才能输出 player_level。XP 从同一 HUD 的 experience_progress 证据读取；购买经验费用和每次增量未实读时仍 unknown。刷新从同快照 refresh_offer 读取，付费模式不会把未知免费次数变成0。

`fields.coins.value` 保留 v1 的原生读数对象或旧标量形状；新增 `normalized_value` 给数值消费者使用，原生对象缺值或快照不符时保持 unknown。完整刷新证据在 `semantic.refresh_offer`，`fields.refresh` 提供模式/次数/费用摘要及证据引用。

`semantic`、`native_fields`、`state_read` 保留原 checked、unknown、scope、请求补读及出处；`request.context` 和 `inspection_results` 保留原 match/epoch/proof/age，不能据曾经核过就当当前资源；`request.static_knowledge` 仅作历史定义。`fact_scopes` 标明这些区别，静态条目不会提升任何动态字段的 known。`freshness.captured_at` 使用真实采集时间，旧 observed_at 单独标为 legacy；离线图片采集时间为 null，heartbeat 不代表新截图。

原 CLI 非零退出时先解析有界 JSON，保留拒绝原因、reason_kind 和用户确认需求；无法解析才使用通用拒绝。不返回 stderr/命令行，凭据键与已知凭据文本会脱敏。每次 CLI/OCR 调用使用唯一归属临时目录，登记子进程创建身份，退出并等待后清理；不接管会话目录，不自动重发未知输入。清理未获确认时，成功结果也保留 artifact_cleanup 告警；经济流程的多个告警分别放在 artifact_cleanup.runs 中。

## 安装和 D 盘注册

2026-10-11，本机 `D:\货币战争` 已安装独立 `.mcp-venv`，并注册到 `D:\Codex\home\config.toml` 的 `currency-wars` 服务。当前 Codex 对话已实际调用 `get_state`、`get_task_status`；均返回 `no_session`、`input_sent=false`，符合游戏停止状态。安装来源为远端 MCP 实现提交 `e65bd21111c348fef144a163493189258c9e1475`，未合并或覆盖本地游戏主线。

注册前配置回滚保存在 `D:\Codex\home\backups\currency-mcp-registration-20261011-122437\config.toml`。下方是重新安装示例，现有本机注册无需重复执行。实时验收和后续实机绑定前需处理的兼容问题见 [核验说明](VERIFICATION.md) 及 [当前验证记录](../docs/CURRENT_VERIFICATION.json)。

SDK 采用独立可选依赖，不能装进已经有严格安装指纹的游戏 `.venv`。使用 Python 3.11–3.13，在 PowerShell 中：

```powershell
$project = 'D:\货币战争'
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
codex mcp add currency-wars-offline -- "$project\.mcp-venv\Scripts\python.exe" -B -X utf8 "$project\tools\currency_wars_mcp.py" --runner-project $project --runner-python "$project\.venv\Scripts\python.exe" --offline-image "$project\handoff\2026-10-07\fixtures\shop_actual_player_level_8.png"
```

该模式调用原 `Perception.read` 和 `ShopReader`，标明 `offline_image_perception`。未知本机模板仍是未知；截图不是当前游戏状态。`get_task_status`、`submit_plan`、`pause` 在此模式返回 offline，不操作已有游戏。

## 有界经济计划的候选契约

2026-10-12有另行完整独审中的独立经济宏候选，复用原runner `decide`。`get_advice(policy=None, macro_limits=None)`默认仍给旧单步建议；只有显式macro_limits及当前已认证请求广告 `economic_macro_contract/allowed_action_types` 时才返回 `economic_macro_proposal`。循环执行在原Worker内部，MCP不自行点击、启动或恢复；`submit_plan(reply)`原绑定提交ACK始终不代表效果已验。宏活跃进度单列，不能把不可变父截图当最新局面、重复发新宏。

macro_limits五项均须显式整数：max_economic_actions、max_total_spent、max_paid_refreshes、max_navigation_clicks、max_wall_seconds。它们是授权上限，具体买/升/刷预算由当前本地模型逐笔重算；不得在调用者给虚构预算或坐标。宏行为、停止及来源见[契约](../docs/ECONOMY_MACRO_CONTRACT.md)，当前审批/实机状态见[当前验证](../docs/CURRENT_VERIFICATION.json)。该候选不解除下述PR16旧run_economy_plan的精确指纹门槛，不复用它冒充新版本支持。

已只读检查 PR16 `5938648de4cfd4b5cd3cd723898abb3c2f806a48`。本机当前 Worker 已有原生经济引擎、准备核验、预算台账及部分/未知结果停止；不能再把 unsupported 解释为本地没有经济引擎。当前 MCP 经济接口仍只支持显式核准的 PR16 精确七文件契约，本机有四个指纹不匹配。因此 `run_economy_plan` 继续返回 unsupported，原指纹及所有 runner/broker 守卫保留。新的兼容版本须另行审查，本次只修读取适配。

若未来另行审查、批准并部署了该**精确候选**，服务启动可显式加 `--enable-candidate-economy`，并将 runner 项目指向那个部署目录。适配检查七个关键候选源文件 SHA256；其他版本保持 unsupported，不通过函数名猜测兼容。它不生成 RUNNER_READY，不解除暂停，不批准候选安装，不绕过原始 broker 固定源码校验。

调用参数：

- `goal`：当前已核目标与停止理由，1–1000 字符。
- `budget`：`purchase`、`refresh`、`experience` 三个非负整数累计上限，均不超过 10000。
- `max_refreshes`：0–100，与原计划 paid_search.max_refreshes 一致。付费次数、免费次数和原生输入总预算继续由原 Worker 控制。
- `binding`：当前 `request_id`、`snapshot_id`、`resume_epoch`、`match_id`，四者均须匹配。
- `reviewed_plan`：候选的完整 `currency-wars-economy/v1`，包含 schema、reviewer、revision、stage、mode、shop_reviewed、purchase_only_targets、targets、budget、paid_search、experience、reserve、fields。字段/ROI 必须来自当前真实完整原图，unknown 写 null；预算与 max_refreshes 须和独立参数相等。参见候选原 [B005 实现契约](https://github.com/gkcc/Currency-Wars-Automation/blob/5938648de4cfd4b5cd3cd723898abb3c2f806a48/handoff/2026-10-07/B005_IMPLEMENTATION.md)。

适配只构建当前请求 proof，以独立无输入 `finish_preparation_review` 动作经原 `decide` 提交 `context_update.economy_plan`。它不把准备阶段标为完成，不推断未知费用，不移植另一套购买/刷新控制器。原 Worker 接受、核验后自行有界推进，后续 `get_task_status` 报告原台账实际效果与待决项；没有台账或不能核其身份时效果为 unknown。当前尚未实机验证候选计划提交、Windows 键位、预算实际扣减或完整原生连续流程。

协议使用官方 [MCP Python SDK v1](https://py.sdk.modelcontextprotocol.io/v1/)，独立环境锁定 1.30.0。2026-10-10远端首次交付仅提供注册命令；2026-10-11本机已完成注册与当前宿主调用，源码版尚没有整局无人值守保证。

## 本地数据库和决策接入

2026-10-12新增上述两个只读工具，服务源码共七个工具。现有宿主连接可能仍缓存原五个工具；新启动的真实 SDK stdio 服务可列出七个工具。数据库来源与查询说明见 [DATABASE.md](../docs/DATABASE.md)，精确版本核验见 [DECISION_INTEGRATION.json](../docs/DECISION_INTEGRATION.json)。

`get_decision` 不调用模型服务。它核当前快照、请求、期限、完整库存及领奖/清理证明，再解析角色费用与逐星参数。当前评分使用发布表的基础战力作为代理，尚未模拟羁绊、装备、投资和对手词缀的战斗交互。升级及刷新规则未确认时明确关闭对应候选范围；商店购买还要求同帧确认普通一星商店及基础价格未改变。当前原生读取尚不能提供全部必要事实，实机执行和整局耗时未验收。合成协议夹具通过不代表游戏已按候选行动。
