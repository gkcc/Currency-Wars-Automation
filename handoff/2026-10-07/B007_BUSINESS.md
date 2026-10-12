# B-007：整局身份跨有界租期延续

集成基准：PR #7 / `dc4b26440e0cf95851493937628f9bb7f1691f76`，已包含经过验证的运行根目录与 GUI 身份链。这是代码与替身收据验证，未安装到游戏输入环境，未在 Windows 游戏进程验证。最新本机交接 `9c3329c8` 明确当前只允许离线工程优化；不启动、不读取或干预游戏，不推断用户接管后的结算。以下实际游戏门槛只能在用户重新授权后执行。

## 行为与原路径

- `start` 保留 60–7200 秒硬期限与明确的局数上限。旧 worker 与旧 broker 都须用原 PID **及创建身份**实际探测为 absent/exited/reused；unknown/running 阻止新租期。reused 只证明旧创建身份已不在，不终止当前复用 PID。没有 broker 身份时，仅接受原 worker/run/launch 绑定的完整 not_launched/launch_failed 事实。已有持久 broker 身份优先，不能被矛盾的未启动字串覆盖；runtime 与持久身份两源冲突时拒绝。
- `_start_cli → prepare_business_start` 在创建新子进程前归档。`_worker_cli` 在 `scratch_directory` 可能清理旧目录**之前**再次核原身份、启动合同与归档。没有恢复合同的直接 `_worker` 不能跳过已有 CURRENT。
- 新 worker 使用新 run、新 token、新 epoch 与现有唯一 broker。桌面重启换 chat 可走原公开 `start`：合同绑定 previous_chat_id/target_chat_id，旧原始 chat 和每个 lease 的 chat 均保留；新会话不能借旧 token 发输入。新租期首先只有观察权限，必须明确当前业务复核后才允许游戏动作。
- 当前未完成整局的 `match_id` 保留；旧准备完成标记、战斗批准、ROI、经济政策、原生识别置信度、fully_read 均不继承。`business_resume` 只是当前局归属和未定事实的确认，准备仍从奖励开始逐阶段复核。
- stopped/failed/运行级 completed/7200 秒到期均不关闭整局。状态额外给出 `continuation_required` 与 `awaiting_next_lease`。只有原 `execute_plan(confirm_match_result)` 对当前真实 **settlement** 的原有严格检查成功，才将该 business 标为 completed；单节点结果不成立。`all_rewards_completed` 仍为 false。
- 同进程 `new_match` 请求得到实际 opponents/environment/investment setup 页面后才建立新 ID，并切换业务文件。没有结算的旧业务保持 unresolved。跨租期当前监督确认“已经新局”也保留旧业务未决；当前若只是大厅，先登记意图，仍等实际 setup 才创建新 ID。

## 持久化与未知边界

沿用 `debug/runner-<chat>-<run>/` 回放目录，无新增模块或第二控制器。业务检查点为初始回放目录中的 `business-<match_id>.json`；CURRENT 只给指针与状态摘要。每次业务写入核 revision 与当前 lease 的 chat/run/PID/创建身份，旧 lease 不可晚到覆盖新归属。每个业务最多 128 个显式租期；没有自动续期/重启循环。

| 原件 | 保存位置及含义 |
|---|---|
| 原始请求与实际结果 | 每个原 run 的 `business-receipts/<request_id 哈希>.json`，保持原 run/原 chat/PID/创建身份；递归去除 run_token/gui_token。终态不可改写。 |
| 发布前意图 | `command.publication_guard` 在唯一发布器前保存原 request/result:null；仅表示可能尝试，崩溃不能推断已输入或零效果。 |
| 刚返回但未折入 ledger 的结果 | 清理前另存 `business-last-result.json`；不伪造原 ledger 已完成。 |
| 经济与腾位 | 原 `economy-*.json`、`business-reward-capacity.json`；business 保留节点账本及原件路径。若节点写成功、manifest 写之前崩溃，下一 start 从原持久节点账本补回实际 spent。 |
| 当前业务复核 | 新 records 的 `<request_id>-business-resume.json`，保留原 proof、当前只读 capture、通过围栏的收据集合、原未定请求及 `input_resent:false`。 |

正常 shutdown 必须在 `protect_children(... complete=True)` 和 scratch 清理前完成归档、最终业务/进程退出状态与日志持久化。归档成功而最终 publish/CAS/写盘失败时也不把子进程清理标成完成，不删除原目录。崩溃后 start 仅在旧双进程真实退出后补读原件；缺实际材料时保持 unknown，不补发原请求。

这是**原请求/收据归档**，不新增全量旧截图拷贝。收据内旧 transport PNG 路径可能随 runtime 清理而失效；`business-archive.json` 明示该范围与 runtime-only 来源可能缺失。原 records 内已有策略 PNG/手操证据继续保留。旧图缺失不能包装成新 proof。

未定列表包括投递未知、经济 pending、腾位未核；已完成输入投递不等于业务成功，空列表也不代表全局成功，旧阶段仍逐项重验。已知原 business 入口前的收据由该 lease 的水位排除，避免同一 run 开新局时混入前一局未知输入。

## 当前复核的实际调用方式

仍用公开 `decide --reply-file ...`，无新命令。新 worker 的当前请求 kind 为 `business_resume`，只接受一个 `finish_preparation_review`，且唯一 context_update 为 `business_resume`。示例中的值须来自当前请求与 ROOT 的实际观察，不能机械照抄：

```json
{
  "request_id": "本次请求ID",
  "snapshot_id": "本次原PNG摘要",
  "resume_epoch": "新租期当前epoch",
  "context_update": {
    "business_resume": {
      "proof": {
        "source": "observed_screen",
        "snapshot_id": "本次原PNG摘要",
        "capture_request_id": "本次request.observation.capture_request_id",
        "evidence_file": "本次request.evidence_file",
        "resume_epoch": "新租期当前epoch"
      },
      "value": {
        "reviewer": "supervising_agent",
        "match_id": "本次请求保留的原match_id",
        "previous_run_id": "choices.previous_run_id",
        "previous_chat_id": "choices.previous_chat_id",
        "current_chat_id": "choices.current_chat_id",
        "disposition": "same_match",
        "continuity_basis": "填写持续跟踪旧局与当前实读阵容、策略、任务的具体同局依据；同PID或同节点不够。",
        "unresolved_requests": ["原run_id:原request_id，须与choices完整集合按字典序一致"],
        "prior_outcomes_remain_unknown": true,
        "remaining_policy": "fresh_reviews_and_current_balance",
        "current_state": {
          "page": "shop",
          "stage": "2-3",
          "coins": 62,
          "level": 7,
          "xp": [44, 52],
          "deployed": "7/7",
          "roster": ["当前实读单位"],
          "selected_strategy": "当前实读策略"
        }
      }
    }
  },
  "actions": [{"type": "finish_preparation_review", "reason": "只确认业务连续性，后续逐阶段新复核"}]
}
```

备战/商店要求主管独立读取人口、等级、经验、金币、阵容和策略；合法的原生事实不能与主管读数冲突，未知原生字段仍保持未知，不回写 confidence。业务身份门中的人口只做 `0≤已上场≤正容量` 算术与来源一致性核验，容量独立于玩家等级；等级7与7/12等读数不会因旧10人上限或容量=等级假设被拒。这不代表B006已会识别扩人口布局，也不授予准备完成、经济预算或出战批准。领奖/选择/结算/评级/指南/装备/投资摘要/说明模态等无法显示完整阵容的页面，改为 `current_state: {"page":"实际page", "visible_labels":["本请求画面实际文字"]}`；仍须明确业务连续性依据和完整未知列表。大厅不能证明原局已结束；可明确 `disposition:new_match`，旧业务保留 unresolved。

`execute_plan` 仍先取得当前 run 的新只读帧。业务复核原 proof 绑定原请求 capture/evidence/epoch 与 PNG；从该请求水位到新 capture 之间只允许可证实的 observe/wait，无 handoff、游戏输入或矛盾/未知输入元数据。当前 page/stage 与可靠数值必须不冲突。纯动画导致全屏字节变化可通过这个无输入围栏；不把全屏 hash 不同直接当业务失败。任何中间变更输入须重新发当前请求复核。

battle/未知过渡页只观察，沿原硬期限等可核页面；不发一个永远无法复核的战斗请求。等待业务回答期间每 3 秒以内仅重观察；页面/节点变了即退休旧请求。暂停/停止/新 epoch 继续优先，不借观察恢复控制。

## 经济跨租期合同

同 match、同节点只携已确认 `spent/purchased/critical_spent/paid_refreshes/revision`。原 pending 放入 `prior_run_unknown`，带原 run/request/原件与 planned_cost_not_actual；它不是新 run pending，不能套当前 fence 或重发。原预算/政策/ROI 失效。即使已进入新节点，原未定集合仍须显式承认。

业务复核后，第一份独立当前 `economy_plan` 还须：

- `revision` 严格高于带入版本。
- `prior_run_unknown_requests` 完整列出原未知 ID，即使为空也显式给 `[]`。
- `prior_run_budget_policy: "current_balance_after_unknown_inputs"`。
- 当前新 proof/ROI/读数及既有 B005 所有依赖、总余额与利息限制验证。

`budget` 仍是**本节点累计预算上限**，可用新增剩余为 `budget - confirmed_spent`，不可把“剩余预算”直接当累计总额。未知费用不计成 0，也不伪装为已证实 spent；主管根据动作后的当前余额重新分配可用剩余。首次通过后解除“首份更高 revision”门槛，同 revision 的后续当前纯复核可继续；未知原件及来源永久留在账本中。

## 聚焦验证与实机待验

新增 `tools/test_currency_wars_business.py::BusinessTests`，复用已有 `EconomyTests.worker/manual_bridge_fixture` 与真实 Worker/Entry 方法。替换的是 Windows 进程探针、游戏截图/输入后端，不是另建测试服务。

13 项覆盖：双创建身份退出/复用 PID；公开 start 在 Popen 前完整归档与终态不可写；真实 F 已发布但读帧失败跨 lease 后只观察并保留 spent；动画与输入/未知围栏；stale CAS/缺 broker 身份；运行级终态不关闭业务/缺人口拒绝；新 chat 新 token 与明确承接；原真实结算分支；battle 等待与大厅新局意图；归档失败不清理及节点写/manifest 写崩溃窗口；实际 new_match→setup 换 ID；新暂停优先；评级/指南/装备/投资摘要/说明模态等稳定页可复核但不能误标新局。

最终根在 PR #7 与本批实际叠加源码运行 **50/50 PASS、0 failure/error/skip，10.74188 秒**：13 个 business 方法、15 个B005、8个既有准备/动作方法、1个手操不延长硬期限、13个PR7运行目录/GUI/固定配置检查。完整选择、实际输出、最终源码SHA256和环境范围见 [B007_BUSINESS_VERIFICATION.json](B007_BUSINESS_VERIFICATION.json)。人口容量独立域的5个子例包含在原business方法内，不额外加数。

独立作者37项与复核者13项覆盖上述选择，不能与根的50项累加成新总数。最终身份优先级、CURRENT/业务记录合并、原件归档与清理顺序已经交叉审查；根另核并复跑人口与等级分域子例。3个本批Python文件编译检查与diff检查通过，固定broker/driver以及PR7 GUI、输入桥、经济与coaching源文件逐字节不变。

当前只运行离线检查。Windows可用项目现有Python环境按交付JSON中的相同选择复跑，无需新框架：

```powershell
$env:PYTHONPATH = "tools"
python -B -X utf8 -c "import json,unittest; from pathlib import Path; d=json.loads(Path('handoff/2026-10-07/B007_BUSINESS_VERIFICATION.json').read_bytes()); r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(d['test_selection'])); raise SystemExit(not r.wasSuccessful())"
```

本批没有Rust改动；PR7新增6项Rust检查仍待本机离线运行，本环境没有Cargo。没有跑整套Windows suite，没有把历史离线PASS写成实机通过。新main交接的四张图，本轮读取了manifest及本机真实回放报告，尚未在此环境独立回放PNG；角色/首领等级污染、原生扩人口识别、正常机械流程主管往返和末关经济规则另按B006/B002/B005修复。

未来用户重新授权实时验证后，需实测：旧有界 lease 正常到期/显式停机与一次可控崩溃；原双 PID/创建身份退出；C/D TEMP 组合启动；换 chat GUI 启动后新 token、当前 proof 承接；上一笔已发 F/购买/腾位未知不得重发；已确认 spent 留存、独立当前预算恢复；每次出战仍重新验收；真实整局 settlement 才闭合 business。性能获益、所有奖励完成、Windows 打包与整局自主运行仍待本机证据。
