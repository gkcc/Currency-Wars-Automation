# 同业务四面板检查点：有来源完成、一次承接、按缺项继续

## 范围与已读依据

本候选以冻结 PR23 `02e0a7c86f82e74216b759bed60a1a7cb55849eb` 为唯一直接父。生产只改 `tools/currency_wars_runner.py`；沿现有 `business` 文件、`business.lock`、CAS、`business_resume`、`finish_inspection` 和 Worker 调度，不增加控制器。

已实际读取并 fetch ROOT 的 [PR22 真实 stop 记录](https://github.com/gkcc/Currency-Wars-Automation/blob/be05c7fd8cad5b1c0f1a79aaae9051a89262cc39/handoff/2026-10-08/ROOT_PR22_NATIVE_STOP_ACCEPTANCE.json)，原 blob 为 `8858755dd90b1d42ae2f34c6625d2cdb9969b99a`。原记录证明该次退出及来源保留；本候选没有复跑该 stop，也没有据此宣称新的 start 或检查点复用已经实机通过。最新用户反馈的 PR23 Windows/原 PNG 复核沿用其明确边界，不拼造缺失的历史 reply/current observation。

已核生产原因：原四项完成仅留在 `self.inspections` 与日志，新租期从空集合和第一个面板开始。另一方面，原 `ask()` 已在原归属回放目录保留请求 PNG 与展示 JPEG，原业务有 lease 和持久收据能力。此次直接把新接受的检查输出绑定这些原来源，并让当前页调度消费已明确批准的完成集合。

**首次切换不能自动恢复 PR23 的旧内存结果。** 没有本契约的完整来源、覆盖和接受记录，就没有可复用资格；不从旧日志猜 request/epoch，不把手写已读结论补成原生来源。候选生效后正常完成一次有来源检查，之后同业务换租期才能复用。旧 `finish_inspection` 输出不带新契约时，仍可结束当次面板，但不取得跨租期复用资格。

## 保存什么，何时生效

四项固定为 `bonds / income / promotion / advantages`。每次新接受的 `finish_inspection` 保存：

- 原 `result`，不清洗或改写结论；实际已读页签及是否完整滚动、当前可领取是否已空、是否未决。优势另需本次配置检查声明。
- `currency-wars-inspection/v1` 检查契约；这是当前可处理事项的核查，不表示全部活动奖励已完成。
- 原业务及 lease、原 request/kind/page/snapshot/frame/capture、历史 epoch、完整原 PNG SHA、展示 JPEG SHA、接受输出文件 SHA、原 capture 收据 SHA、生产者 runner SHA。
- 当前正式结算记录的摘要和本次 Entry 收据水位。结算记录为空也只摘要其真实空值，不推导结算次数或“没有新变化”。

原 `ask()` 的 `evidence_file` 是展示 JPEG；snapshot 绑定的是独立 `*-strategy-original.png`。二者分别验证，绝不用 JPEG 冒充原 PNG。输出另存小的 `*-inspection.json`；capture 收据用原 `archive_business_receipt` 保存到原归属目录，checkpoint 只接受这份原记录与其摘要。没有复制 PNG、大型 OCR 对象或私有素材进交付。

完成必须是独立单动作。执行先走原新帧/页面/请求/epoch 检查，再核请求后只读水位和当前 pending；写出小记录后，在业务 CAS 内再次检查 pending、水位、当前请求、期限、前台、manual 与 stop。**业务写入成功才启用内存完成。** 被 CAS/stop/pending 拒绝时，可能留下未被 checkpoint 引用的小记录，不能作为已完成，也不重发输入。这个末次检查不声称与 Entry 发布锁构成跨文件原子事务；输入单写者与人工交接仍依赖原守卫。

## 原接口用法

### 1. 当次真实核完面板

继续回答原 `post_match_<panel>` 请求，独立 `finish_inspection`。原外层 request_id/snapshot_id/resume_epoch、panel 和 evidence 必须来自本次请求；`result` 在原实际输出上补这些字段：

```json
{
  "evidence": "<本次 request.evidence_file>",
  "inspection_contract": "currency-wars-inspection/v1",
  "coverage": {
    "tabs": ["<实际已读页签>"],
    "all_tabs_reviewed": true,
    "scroll_complete": true,
    "claimable_remaining": false,
    "pending": false
  },
  "findings": "<ROOT 本次实际核查输出>"
}
```

优势页另加 `coverage.allocation_reviewed=true`。这些是 ROOT 的已读覆盖声明，不是新加的原生 OCR 能力；看不到或没读完整时不能照抄 true。显式新契约中覆盖不全、未知或 pending 会拒绝完成。旧无契约输出保留当次兼容性但不可复用。

### 2. 新租期一次 `business_resume`

原承接请求新增 `inspection_checkpoint`，逐项提供 `eligible / reason / record_sha256 / result / coverage / source`，以及 revision 和正式结算摘要。继续原当前 proof、当前业务身份和只读观察序列，仅在原 `context_update.business_resume.value` 加以下 `inspection_resume`；原动作仍为独立无输入的 `finish_preparation_review`，不新建 CLI 或恢复旧动作。

```json
{
  "contract": "currency-wars-inspection/v1",
  "checkpoint_revision": "<本请求实际整数 revision>",
  "settlement_sha256": "<本请求实际提供的摘要>",
  "settlement_change": "unchanged",
  "basis": {
    "source": "supervising_agent_continuity",
    "through_snapshot_id": "<本次请求 snapshot_id>",
    "unobserved_interval": false,
    "details": "<ROOT 持续监督、操作收据及变化范围的具体事实依据>"
  },
  "reuse": {
    "bonds": "<本请求 eligible 项的 record_sha256>",
    "income": "<本请求 eligible 项的 record_sha256>",
    "promotion": "<本请求 eligible 项的 record_sha256>",
    "advantages": "<本请求 eligible 项的 record_sha256>"
  },
  "changed": [],
  "unknown": []
}
```

例中占位字符串必须替换为本请求实际值；revision 使用整数。不允许缺项被默认为未变：`reuse` 的键与 `changed/unknown` 两列表必须互斥、恰好覆盖四项。只将真实确认未变、原来源完整的项放入 reuse；新可领取、进度或面板状态变化放 changed；不确定放 unknown。不是依据“无红点”自动推断未变，也不先重开四页证明可跳过四页。

`settlement_change` 可为 `unchanged / changed / unknown`。有复用项必须明确 unchanged 且无未观察空档；有新结算或结算不明就不能复用任何旧项。全部不复用时可如实写 `unobserved_interval=true`、四项 unknown，不必虚称持续观察。该声明只失效检查资格，不会伪造一份正式结算。

缺少整个 `inspection_resume` 时沿原承接继续，但保守清空复用资格。坏契约/损坏外层检查点降级为无完成的新工作记录，之后可以通过新来源重新核查；不能把坏旧字段“升级”为真。

## 失效和当前页调度

| 触发 | 持久结果与调度 |
|---|---|
| 四项已保存、来源完整、当前承接明确全部未变 | 同一次 CAS 记承接，再启用四项完成；当前大厅直接走原 `new_match` 请求 |
| 仅 income 变化，当前在已复用的 advantages | 只失效 income；返回大厅后进入 income，核完再继续，不重开另三页 |
| 当前已在首个待核页 | 直接沿原领取/核查路径，不先退出再打开 |
| 原 PNG/接受记录/收据缺失或字节/lease 不符 | 相应项不 eligible；不能因字段看起来完整而复用 |
| 历史未知输入 | 拒绝复用；明确不复用后，允许原未知留存并用新 run 的当前完整来源重新核查，不永久堵恢复 |
| 本次当前 pending、请求后的输入/未知收据 | 拒绝完成或复用；不重发、不把已点击等同领空 |
| 正式 `confirm_match_result` 成功 | 同一次 business CAS 写正式结算并清四项资格；失败则两者均不提交，计数不先增加 |
| 同 run epoch 改变 | 撤销当期有效完成与展示；历史候选保留，不能继续沿旧批准跳页 |
| 大厅仅确认另开意图 | 保留原业务身份/未决状态，不假结算；真实到 setup 才沿原逻辑建新业务并清旧集合 |
| 页面未知/模态、stop/manual、超期或 CAS 被替换 | 沿原处理或拒绝；真实用户中止优先 |

恢复的只是一组有历史来源的面板完成输出。新请求用独立的 `inspection_completion_sources` 标明历史来源；当前动态字段不由它回填。旧动作、epoch、经济预算/绑定、准备复核、策略上下文和出战批准均不恢复。正式整局结果与局后检查分开计数。

## 冻结检查与边界

`tools/replay_currency_wars_inspection_checkpoint.py` 只加载新类 **10 个方法**。既有 business/economy/runtime 三个测试模块仅提供惰性夹具，不运行其旧测试类。实际执行 Worker、Entry 接收、临时持久文件与业务 CAS；PNG、读取字段、页面到达与变更声明为明确合成协议，无游戏/GUI/controller、无原生 OCR 或真实截图。

10 项覆盖：四源保存及一次续接到 new_match；单项失效导航；当前待核页；单动作和覆盖/迟到收据；坏源/坏格式可恢复；未知/观察空档/水位；早晚 stop/epoch/期限/CAS/pending；结算原子失效及写失败；另开意图与实际 setup；未知页面及 epoch 撤销。断言没有恢复经济绑定、准备复核或出战批准，协议物理输入仅用于明确的两次导航/开局模拟。

Linux / Python 3.12.14：**10 passed，0 failure/error/skip，24.283349694 秒**。这是离线聚焦选择耗时。

29 项来源（24 生产 + 2 新 driver/测试 + 3 原夹具）测前后不变；source-set：`06525d0b5dbbd63fa11ef6c226f8e68b0e9fca4c7b6b6fc2d5a088275939a3cb`。

| 文件 | SHA-256 |
|---|---|
| tools/currency_wars_runner.py | 9ae2cbe65f6caa7083c147c6726f8b766084bcef5af45751c0fbb4413bb75236 |
| tools/replay_currency_wars_inspection_checkpoint.py | f96e56939805f0f14b6e5b87293d5c2a5db583d0b2d69a48cf45d6ad6f1f130a |
| tools/test_currency_wars_inspection_checkpoint.py | 1e5772f383ce48707063cbf568d48cabd3163beeae5afbffb230a76b35cd09e4 |

[inspection-checkpoint/acceptance.json](inspection-checkpoint/acceptance.json) 为 7869 字节原生报告，含 10 项结果、29 项摘要、环境与明确边界；未重复上传 tests.txt。测时 HEAD 如实为 PR23 父提交，修改字节由测前后完整 source-set 绑定；最终发布树另行回读，不伪填测试 head。

开发首轮是 10 项、0 failure、1 error、0 skip：一个负例直接请求 key，被原 runner-owned 守卫正确拒绝，已改成只读收据的矛盾终态声明；同时测试文件在首轮运行中补入子场景，before/after 不一致，因此未算冻结通过。随后 10 项稳定通过 21.973401486 秒；最终又按真实只读并发路径补 CAS 末检及无复用空档子场景，该补充首轮有 1 failure、0 error、0 skip（24.050612971 秒）：late_pending 夹具只清 ledger，仍留真实惰性 result.json 终态，因此原对账正确恢复为完成；修成两处都无终态，才实际覆盖未决。冻结结果以上述最终报告为准。没有隐藏开发错误，也没有松动输入守卫。

### Windows 入口

在候选独立归档根，用 ROOT 原 Python 环境：

```powershell
python -B -X utf8 tools/replay_currency_wars_inspection_checkpoint.py --output inspection-checkpoint-windows.json
```

期望 10 项、0 failure/error/skip、29 项摘要与上文一致、测前后差异为空。无 `.git` 归档的 `tested_checkout_head=null` 合法；字节由冻结 archive 与 source-set 绑定。不需要本机原 57 素材，不需要新游戏帧。不跑无关旧套、GUI 共享 fixture 或经济优化选择。

## 局后切换与真实验收

不热改 ROOT 当前运行源码。本候选局后按既有 stop/独立审查/来源绑定/check-launch/start 路径评估；runner 一个生产文件改变，生产清单仍 24 项。原 GUI、bridge/control pins、requirements、资源/provider 声明均未改，已有验证匹配时复用；新 runner 字节必须经原 source activity/独立审查绑定到真实 READY，不能沿用旧完整 binding 或手填通过。本次未安装、未改 READY、未操作游戏。

ROOT 的最小自然窗口应从本候选一次真实四页完成开始，保存原接受输出及本地不可变来源；正常 stop/new lease 后，在一个真实 `business_resume` 中声明可复用范围，并记录：

1. business/match 和正式结算水位是否仍一致；原来源与原未知是否保留；新请求/epoch 确实新生。
2. 一次承接完成到下一原生 `new_match` 请求之间，四页打开数及重复 `post_match_*` 请求数应为 0；若当前仍在旧完成页，只计实际退出，不冒称没有导航。
3. 有一项实际变化时，只重验该项；新结算必须四项失效。未知或未决要保持拒绝，用户 stop 优先。
4. 分别记请求等待、原来源校验/工具、观察/OCR 和主管空档；比较相同起点/终点。协议里的少请求不能直接换算节省分钟或整局倍率。

本次完成的是可复现协议切片。Windows、原生跨租期复用、实际页面跳过与新结算失效端点尚待 ROOT；不将 ROOT 之前的 PR22 stop 或 PR23 守卫验收归为本候选实链通过。运行期限、策略求值、GUI fixture、经济与 PR20/21 均不扩入。
