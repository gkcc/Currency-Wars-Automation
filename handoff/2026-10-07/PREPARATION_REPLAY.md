# B-002 / B-005：备战机械链与离线回放

代码比较基准 `4830dfe8510171519b1961b2116968495d51a292`，本批父提交为 PR #10 的 `032a906c210ca60c65b6225659fa2246be010bff`。历史操作证据来自 `origin/main=8e5bebd5d598cfde0ba2c452e9e9a8a446293606` 中的 `ROOT_PROFILE_FULL.json`、`ROOT_HOST_TIMING.json`，本分支保留相同原始字节；四张公开真图/manifest 来自 `9c3329c8eaedf4f2b32a64ad3727b23e14b6c3fa`，已随 PR #10 保存。当前只做离线工程；没有启动 Windows 控制器、游戏或新的截图，物理输入为 0。

## 盘点结论

**`native_inputs=0` 不能单独证明脚本没有机械执行能力。** 历史 99 条助手记录标明运行源码是 `3878f9c`，不是本轮基准；其 67 条 actions、17 条 observe、8 条 battle、4 条 state、3 条 resume 是外部助手调用类别，不是 99 次 Worker 决策，也不是 99 次物理游戏输入。15 条没有导出 outcome，80 条报告成功，4 条报告拒绝/错误；helper 成功也不自动证明业务效果。

现有真实路径是 `Worker.tick → advance_economy → execute_economic_action → command → Entry.request → GuardedSubmission → 唯一 publisher`。经济批次已有最多 8 笔的本地循环；每笔新帧验证费用、免费次数/经验/槽位差额、持久 pending 和实花；零/部分/未知保留待验，不按发送次数记成功，不重发。ROOT 提交一份合格的当前预算后，不应为每个 F/D 再往返一次。

公开树没有历史 ROOT 临时 helper 的 `step` 源码、原动作数组或原完整收据，只含脱敏标签与计时。故不能从 label 推断每笔输入的守卫、input 数量或策略必要性。用户提供的“临时领奖坐标误买刃”是已报告的 B 类接口使用缺口；没有据此宣称 native `execute_plan` 复现了同样问题。当前原生 `execute_plan` 会重观察、核页面及目标双帧；本批还补 `command` 页面检查与旧 `click_text` observation 拒绝，完整边界见 [PREPARATION_ENTRY_AND_RULES.md](PREPARATION_ENTRY_AND_RULES.md)。

## 备战中何时必须返回 ROOT

| 当前条件/真实代码入口 | 返回原因 | 分类与可减少部分 |
|---|---|---|
| `business_needs_review`，B007 续接 | 当前局归属、原未知请求、当前余额计划尚未重验 | 必要授权/异常复核；不能复制旧 epoch 的阶段完成 |
| 商店已展开，阶段仍 `rewards` | 有唯一“收起”文本时本地先收起；缺文本回 ROOT | 动作有本地路径；公开真图缺收起后的真实 successor，回放停在此处 |
| 备战 `rewards` 未完成 | 没有完整奖励球/待选奖励的原生清空扫描器，必须当前全场复核 | B 读取能力缺口；不能以缺模板或画面未见推定全部领空 |
| 创业指南阶段 | 第二入口可推荐受双帧守卫的导航，但当前章节目标/领奖须实读；指南领奖后重扫奖励 | 一次策略/当前任务读取必要；导航仍需 ROOT 提交，正文缓存不能代替任务进度 |
| `inventory_cleanup` | 无用牌判定、实名/星级/空槽/独立溢出、出售收益尚未证明 | A 策略加 B 原生读数缺口；人口不能代替库存容量 |
| 经济预算不存在/过期/新 epoch，或当前字段缺失 | `economic_policy.available=False`；当前请求须给目标、预算、动态 ROI 与停止条件 | 必要初次决策；不是每次 F/D 都需要决策。仅按本动作要求必要字段 |
| 明确目标槽可核，其余槽未知 | 可单槽购买；刷新/经验/经济完成仍需必要商店检查完整 | 现有 B005 能力保留；不能把所有商店槽未读齐当成全面禁止购买 |
| 已核经济动作零/部分/未知、收据/后帧缺失 | `economy_result` 或停止锁；补原请求证据/明确剩余量 | 必要异常；只补观察，绝不重发已发布输入 |
| 经济条件已全部解决，无动作待发 | 原代码仍返回 ROOT 做无输入的 economy 完成标记 | **本批已修 B：复用相同 predicate，由本地记录完成，省去这一次机械确认** |
| 布阵/装备/合成/策略同场检查 | 实际角色、装备兼容、已穿戴、位置或羁绊缺口未完整核 | 必要当前战力决策；尚无通用装备/站位自主执行证明 |
| 出战验收与战斗动作 | 新帧人口、阵容及当前请求的单次批准 | 保留 ROOT 验收；本地经济完成绝不批准出战 |
| 页面未知、目标变化、外部输入、暂停/停止、deadline、节点无进展上限 | 读取不足或安全/身份守卫 | 停止或异常回传；未对未知空档强行分类为模型耗时 |

战略选择页（攻略、投资、环境、补给、对手）仍按当前选项返回 ROOT。普通单动作成功后也不保证旧坐标可用于下一动作；必须重新定位动态槽位。

公开检出 `docs/GAME_KNOWLEDGE.json` 不存在，故本次回放缓存实物为 unknown。代码已有 `load_knowledge` 和当前应用证明 `guide_reference` 的复用能力；不能从公共仓库缺文件推断本机不存在私有缓存，也没有向回放补入虚构正文。

## 最小生产修复

经济连续执行子项将 `review_preparation` 的 economy predicate 共用，修改 `advance_economy` 的完成分支，并新增 `finish_local_economy`。当且仅当：

- 前三个阶段仍在当前 match/stage/epoch 有效，当前阶段确为 economy，预算绑定当前 scope；
- 同一原有完成 predicate 判定缺口、免费次数、付费停止、经验剩余计划均解决，无 action、无 pending；
- 当前实际终态收据身份、原请求 frame ID/hash/capture time 与当前读数相符，原 PNG hash 未变化，结果没有 unknown input；
- 当前无新业务归属复核、手动/停止/前台/控制健康冲突；

才保存 `origin=local_economy_execution` 的完成证据、预算 proof、实花和当前完整收据。不会写 `reviewer=supervising_agent`，不会改 `fully_read`/confidence；后续库存、经济变化仍沿现有失效规则撤销下游完成。布阵与出战保持未完成。

最终检查与完成记录位于同一现有 `submission_lock`：取得锁后重验 scope/epoch/手动状态，要求 `result.json.id` 仍为当前 capture ID，且当前运行原台账没有 `result=None`。锁忙时直接拒绝；没有新增等待。经济计划尚不可用时沿原路径直接返回，不进入无收益的完成台账扫描。同 broker 的外部后续输入不能把旧帧变成完成证据。`await_existing_receipt(..., 0)` 是纯读取，不递归进入 Entry、不重入锁、不发布输入。未结清的旧纯观察请求也会保守阻止这项自动完成优化，须核原 ID 的真实终态；这不是全局死锁，既有 ROOT 当前复核出口保留，未知旧输入仍不可重发。

## 回放边界及同口径比较

[`replay_currency_wars_preparation.py`](../../tools/replay_currency_wars_preparation.py) 复用 `RuntimeCompatibilityTests.manual_bridge_fixture/frame_worker`，只替换物理发布为惰性终点。四张原 PNG 必须先通过 manifest SHA256，再经真实 `Perception.read → Worker.observe → Worker.tick`。不填合成 shop/team、旧完成标记或 confidence。

四图为 3-7 商店、3-4 商店、首领介绍、3-7 备战，**不是连续流程**。两个商店起点会提出收起动作，但没有后继原图/收据，惰性终点明确返回 `offline_fixture_missing_successor` 和零输入；不能给它虚构成功后图。首领页返回 unknown；备战页停在奖励当前复核。真正完成阶段 0/6，已保留的连续历史动作转移 0；所需 1-1/1-2/3-1 首节点真图覆盖 0/3。

另有清楚隔离的**合成事务协议轨迹**：复用现有生成数字帧和 crop OCR 替身，66→62→58→54、7级40/52→44/52→48/52→8级0/72，经真实 Worker/Entry 合同执行三次 F。它只测 economy 1/6 阶段；前3阶段是 fixture 前提，不是实读验证。相同序列 baseline 要初次预算+完成复核 2 次正常 ROOT 请求，修复后只需初次预算 1 次；异常回传均为 0。零效果轨迹前后都保留 1 次异常、0 实花、pending，不重发。这里没有人工等待或游戏动画，不能给出实机速度倍率。

两版均实际调用 `Worker.tick`；造成差异的旧版 economy 完成请求由生产代码自然生成，回放脚本不人为调用 `ask` 来增加 baseline 数量。正常请求计数也包含既有 fixture 初始化时通过 `Worker.ask` 建立的初始预算请求；这笔前后相同。只有旧版自然提出 economy 完成请求后，回放才提供显式标为合成的 ROOT 完成 reply，使双方抵达相同 `lineup_equipment` endpoint。合成计时在 fixture 初始化及初次预算请求之后开始，不能用该段 total 量化完整主管往返。**这些收据来自惰性 fixture，绝不是历史游戏收据。**

最终比较实际执行 exit 0，完整数据见 [JSON](preparation-replay/after.json)、[CSV](preparation-replay/after.csv)、[中文 HTML](preparation-replay/after.html)。

| 相同序列/起点 | 正常 ROOT 前→后 | 异常 ROOT 前→后 | 真正覆盖与结果 |
|---|---:|---:|---|
| 真图：3-7 商店角色 Lv999 | 0→0 | 0→0 | 到收起候选；两版均无后继证据而零输入拒绝。玩家等级 99→9 |
| 真图：3-4 实际玩家 Lv8 | 0→0 | 0→0 | 到收起候选；两版均无后继证据而零输入拒绝。玩家等级 99→8 |
| 真图：首领 Lv90 介绍 | 0→0 | 1→1 | 页面仍 unknown，玩家等级 90→unknown；未填“清空”或后续动作 |
| 真图：3-7 最后备战 | 1→1 | 0→0 | 当前领奖复核；玩家等级 9→9，人口 8/9。准备完成 0/6 |
| 合成：正常三 F 事务 | 2→1 | 0→0 | 仅经济 1/6；都核 12 实花，抵达相同待布阵状态 |
| 合成：F 零效果 | 1→1 | 1→1 | 原 pending、0 实花、无重发；经济不完成 |

本次 `after` 已在最终整合工作树重新实际运行，包含 PR #10 的 HUD 读取器、本地经济完成、页面/旧帧守卫及末关来源代码。runner SHA256 为 `0a6248e2934f0544ebf4ddeb3f0342bf472c1c9a5c2f27db15eafd4b6873fe99`，economy 为 `f82f4e18fd509863287eaf36087f06b17dafdc99ff21ec5eeb84d9497c28cfba`，Perception 为 `8e13f440d380836bb6896ac445c265f9da75824edc985d988651a3b7919d1140`。报告内部保存实际执行源码哈希；中间未整合结果没有冒充最终结果。

根最终整合后运行 **58/58 PASS，0 failure、0 error、0 skip，20.461 秒**。该去重选择包含原 PR #8 的业务/运行目录/准备/协议检查、当前 19 项经济检查及 4 项连续回放检查；新增用实际惰性 Entry 发布一次外部后续按键，确认旧帧不能完成经济。原经济零/部分/未知、F 字段、预算和 B-003 补证检查继续通过。完整测试 ID、输出、环境及源码哈希见 [verification.json](preparation-replay/verification.json)。此 58 项与历史 94/107/50/19/15 选择有重叠，不累加。Windows installed provider 的真实执行仍交由本机核验，本次没有 Windows/实机测试。

JSON、CSV、HTML 使用同 ID 与相同 PNG/合成帧 SHA 序列核比较。单遍耗时仅是该机器此次离线运行，首图包含引擎构造；采集/输入动画/主管等待并不存在于本回放，留作 null，不写 0 毫秒优化成功。报告将 Perception 总段与 Entry 未归因 I/O、规则及惰性发布分开且不重复相加。

## 历史宿主计时

99 条内部助手计时和 146 个宿主工具区间记录分开计算。报告复算约 6028.101 秒部分窗口、543.739 秒 helper 内部耗时、773.025 秒宿主工具区间并集；剩余 5255.076 秒仍为工具外未归因。原导出的 `timing_contract` 定义 `time` 为助手完成 UTC、`seconds` 为导入/owner 加载后的单调耗时；helper 区间据此构建。UTC 时钟对齐只是源证据的假设，未独立测量；异步助手可能长于宿主调用，因此 helper 与宿主时长不能相加或当成相互完全包含。

宿主 tags 是可重叠标签（如游戏 helper 同时带 repo diagnostic），报告通过时间边界切分为互斥 tag 组合；不能把各 tag 总时长简单相加。147 条带唯一 response ID 的 API 使用记录说明该窗口有大量宿主往返，但不是 147 次必要策略决定。公开标签不能分出截图、OCR、模型排队与思考的精确贡献，也不能证明每次 helper 是 Worker 所迫。六次逐轮刷新与五次本地批次的场景不同，继续不作倍率比较。

## 重跑

依赖沿用现有 Pillow、RapidOCR/ONNX Runtime、numpy；不安装新框架。`--code-root` 可指向完整基准检出或集成后的源码树；同一个 replay 脚本分别运行，从而避免只靠 monkeypatch 模仿旧版。

```bash
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_preparation.py \
  --code-root /path/to/baseline-4830dfe --fixtures handoff/2026-10-07/fixtures \
  --output handoff/2026-10-07/preparation-replay/before \
  --root-profile handoff/2026-10-07/ROOT_PROFILE_FULL.json \
  --host-timing handoff/2026-10-07/ROOT_HOST_TIMING.json

ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_preparation.py \
  --code-root . --fixtures handoff/2026-10-07/fixtures \
  --before handoff/2026-10-07/preparation-replay/before.json \
  --output handoff/2026-10-07/preparation-replay/after \
  --root-profile handoff/2026-10-07/ROOT_PROFILE_FULL.json \
  --host-timing handoff/2026-10-07/ROOT_HOST_TIMING.json

PYTHONPATH=/path/to/test-deps:tools python -B -m unittest \
  test_currency_wars_preparation_replay.PreparationReplayTests \
  test_currency_wars_economy.EconomyTests
```

Windows 在独立源码检出中使用现有 Python 依赖即可，不安装或切换输入组件。依次核 PR #9 的两个实际 provider 路径、PR #10 的四张 PNG，再运行本批记录的相同选择：

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'tools'
$selection = (Get-Content -Raw -Encoding UTF8 handoff/2026-10-07/preparation-replay/verification.json | ConvertFrom-Json).test_selection
python -B -X utf8 -m unittest @selection
python -B -X utf8 tools/replay_currency_wars_preparation.py --code-root . --fixtures handoff/2026-10-07/fixtures --before handoff/2026-10-07/preparation-replay/before.json --output offline-results/preparation-after --root-profile handoff/2026-10-07/ROOT_PROFILE_FULL.json --host-timing handoff/2026-10-07/ROOT_HOST_TIMING.json
```

这里的基准耗时是云端一次运行；本机如比较耗时，必须用同一脚本在同机分别运行 `4830dfe8510171519b1961b2116968495d51a292` 与当前完整 SHA 的源码树。固定 PNG/合成帧序列与主管请求数可逐字段核对，跨机器单遍秒数不能作为速度结论。报告中的惰性输入与回执不会到达游戏。

第一次真实 OCR 尝试的完成查询遭自动安全审查拒绝，原因是 ONNX Runtime 默认 Microsoft telemetry 外联未经授权。命令及原文保存在 [`APPROVAL_REJECTION.json`](preparation-replay/APPROVAL_REJECTION.json)，该尝试不计 PASS。官方 [ONNX Runtime Privacy](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md) 明确 API 可能晚于初始化事件，所以安全替代在进程导入 ORT 前设置 `ORT_DISABLE_TELEMETRY=1`，再调用官方 `disable_telemetry_events()`；只改本离线进程，不改系统环境、权限或代理。此配置下 baseline 重跑实际 exit 0。

当前未验证 Windows 实机、完整备战连续图/收据或改前改后整局速度。下一项必要证据是同一节点的奖励清空前后、创业指南任务/领奖及返回扫场、库存清理前后、商店预算及逐笔后的原图/收据、布阵与最终验收帧；获得前不伪造连续轨迹。
