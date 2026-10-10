# Currency Wars MCP 独立审阅

结论：**pass（代码与已授权离线验收范围）；0 项可达、可行动缺陷。live 行为 unverified。**

- 审阅目标：`e65bd21111c348fef144a163493189258c9e1475`。
- 基线：`4cf23830e7832ee68b4ef40ba688b006ee3064fc`。
- 项目：`D:\Codex\Workspaces\2026-10-10\currency-wars-mcp`。
- reviewer runtime：`01a12688-b250-7511-829c-eb936992622a`，parent：`01a1266e-629f-77d1-b4bd-a14f58e4aac2`，不同于 worker `01a1267b-7079-73f0-9ba3-a38775b3399c`。
- 观察时间：2026-10-11 00:00:32 +08:00；审阅开始和结束所见 HEAD 均为目标提交，工作树干净。
- 读取全局 AGENTS、agent-workflow SKILL 及 engineering reference；项目及其 D 盘上级未发现额外 AGENTS。
- 直接读取完整真实 diff、runner CLI/decide/load/current_state、worker 发布与准备/购买守卫、固定 broker、原 Perception 和候选 Git blob。未依赖实现者结论作为通过证据。

## 验收状态

| 条件 | 结果 | 证据 |
|---|---|---|
| 真实 stdio 初始化及公开 runner 工具 | pass | 审阅真实 `protocol_check.py`：官方 ClientSession/stdio_client 启动服务子进程，initialize/list_tools/call_tool。读取协调者本轮实际复跑输出 `D:\Codex\Projects\sts2-mcp-demo\currency-mcp-protocol.json`，协议 2025-11-25、server currency-wars、版本 1.30.0、精确五工具。源码明确调用原 status/decide/pause CLI。 |
| 离线/错误入口不得误执行，状态来源及 unknown | pass | 同一实际协议输出：无身份四工具 no_session/input_sent=false，经济工具 unsupported/input_sent=false；fixture 为 offline/source=offline_image_perception；离线 submit/pause 无输入。直接检查源码及原字段生产路径，没有自动读取 CURRENT_RUNNER、自动启动、恢复、重发或替代控制器。 |
| 相关 focused checks | pass | reviewer 本轮独立运行 VERIFICATION 中同一 7 项 unittest，7/7，0 failure/error/skip，约 0.271 秒；固定目标 `git diff --check` 通过。未新增测试或测试文件。 |

协议复跑由协调者执行并保存，reviewer 读取其实际结果及复现脚本；本报告没有把该复跑写成 reviewer 亲自执行。单元及 diff 检查由 reviewer 亲自执行。

## 重点核对

### 提交结果真实性

`tools/currency_wars_mcp.py:78-123,225-231`：回复原样写入单次临时 JSON，通过明确绑定身份调用原 `decide`。只有 CLI 零退出且 JSON `ok` 才返回 `submitted`，明确 `effects_verified=false`、执行待定，没有把提交受理说成购买成功或整局完成。超时/无有效结果为 submission_unknown，不自动重发。CLI 非零退出保守返回 rejected，隐藏原详细错误与凭据；这降低错误诊断粒度，但没有虚报受理或输入效果。

原 `tools/currency_wars_runner.py:372-395` 核标准 marker、chat/token、run、worker 创建身份；`883-925,3904-3939` 验证 request/frame/epoch/deadline/有限动作，在原 decision-submit.lock 内重新读取，暂停/停止及已有回答优先。worker `2273-2357,2917-3074` 仍走原 publication guard、原帧 hash、鲜帧/准备条件、购买身份和资源检查；`2358-2385` 保存真实 broker 回执及后帧，效果还需分析。原 1200 次硬输入预算没有替换。原 broker_entry PINNED 校验、原控制器 mutex、submission_lock 和原 request ledger 保留。MCP 没有第二个执行器。

### 暂停身份

`tools/currency_wars_mcp.py:49-51,78-84,310-317`：服务生命周期固定三项显式环境身份，不从其他聊天恢复 token。pause 调用原 emergency CLI；无 session/历史截图模式直接拒绝。

原 runner `3811-3839` 在 emergency load 中仍验证归属与创建身份，先锁定 manual 意图，再调用原 emergency_pause。broker_entry `48-63,72-126` 核所属 token、精确 PID+creation 和对应 pause ACK；不假报持续暂停 ACK。MCP 没有调用 resume/start，也没有前台恢复逻辑。

### 候选经济计划

`tools/currency_wars_mcp.py:18-27,67-76,233-269`：明确 opt-in 且七个源文件 SHA256 同时匹配才支持候选；main 即使 opt-in 也 unsupported。reviewer 直接对 `5938648de4cfd4b5cd3cd723898abb3c2f806a48` 的七个 Git blob 计算 SHA256，全部与常量匹配。

有界参数检查非负整数累计预算/刷新上限，与 reviewed_plan 相等；在原 status 结果上核当前 request/snapshot/epoch/match、waiting_decision 和 economy_allowed。构建原当前 proof，通过独立无输入 finish_preparation_review + context_update.economy_plan 调用原 decide，没有刷买循环。候选原 accept_economy_plan 仍核原帧、本局节点、来源、计划字段、当前预算和 pending 结果；实际动作由原 Worker/broker 推进。

`193-223` 台账只在候选指纹成立时读取限定 debug 路径的 native economy 文件，schema/run/match/stage 必须相符；无可核台账 effects_source=unknown。没有拿预算上限当实际花费。

### 状态 unknown 与形状

`154-185` 保留历史/认证 runner 两种来源、snapshot、请求绑定和 fresh 信息，不主动截取新画面。player_level 须同快照 actor=player/native_player_hud 且有 level 证据；当前 main 的全局 Lv.999→99 误读被隐藏为 unknown。

实际 fixture 结果：shop、stage=3-4、coins 原生事实值 68；player_level/population/xp/refresh 为 null/known=false/source=unknown。五槽模板不足仍保留 unknown。snapshot=`7ffc0c065f665754a5ce2d7b4e278cbca3078c2cda0ee39403a0783dddca135c`。

注意 `fields.coins.value` 是保留的原生证据对象，例如 `{value:68,bounds:[...],currency_icon_gold_fraction:0.423}`，不是整数标量。VERIFICATION 的“coins 68”指其内层数值。直接核原 main/candidate semantic_facts：只有 HUD/金币图标/整数读数检查通过才产生 coins 对象，读取失败省略该字段，投影成 unknown。人工构造 `semantic.coins={value:null}` 可得到 known=true，但未发现原实际 producer 能生成该结构，不将不可达人工输入定为当前缺陷。候选的 player_hud.xp/refresh_offer 没有额外投影到 xp/refresh，保守显示 unknown；未伪造数值。

## 保留的 unverified 范围

授权 live runner 的 status/decide/pause、宿主实际 MCP 注册与环境继承、候选生产受理/连续流程/实际扣款、Windows 键位及整局运行均 unverified。本次没有操作星铁、没有新建 broker、没有安装、推送、修改全局配置、读取其他 session 的自动接管身份或输出 token。离线通过不能证明上述 live 行为。

对本提交的三个离线首版验收条件，未发现须先修复的问题；这不是对候选部署、live 授权或无人值守整局能力的批准。
