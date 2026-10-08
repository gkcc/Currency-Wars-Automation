# PR30 后的恢复去重与导航未决链修正

唯一基线：`51798ecf7a62d8f778a799c130fc111fc2f836a5`（PR30）。本包是完整可应用的源补丁，不是已发布 PR；ROOT 在隔离目录应用后负责冻结提交、发布、独立验收与部署。PR24 不在此基线链中。

## 两项生产修改

### PR29-R1

`currency_wars_manual_steps.py` 在原 `manual-checkpoint.lock` 内、排入新作业之前枚举已有有界 mailbox。按原 `reward_step_id`/`input_request_id` 与 run/match/stage 识别义务；不依赖 continuation 是否曾经落盘。

旧来源 request/capture/frame 中任何一个被重复使用，拒绝新作业，不捕获、不释放 blocker。findings 或 deadline 变化不是新来源。相同 job ID 仍只读原结果。

允许重试必须同时具备：旧作业 terminal refused、完整水位/交付、零输入、无未知/证据错误，持久报告与 mailbox 相符；当前 proof 来自这些拒绝之后本 manual binding 中一次真实完成的 inspect；其新 request/capture/frame 与每次旧尝试均不同，原 PNG 和纯观察原收据再次认证。PNG SHA 可以完全相同。旧结果文件不删除，原 `_prepare_recovery_record` 继续保留已经写出的失败 continuation。

### PR30-R1

`currency_wars_runner.py` 的 `check_startup_navigation_attempt` 从最新指针沿 `prior_unknown_navigation_request_id` 跟踪原归档。中间零输入拒绝不能解除更早 pending。逐段核 schema/run/match、归档相等、原 PNG SHA、capture/frame 及同 owner 的原收据；缺失、循环、超出128边界或未知交付全部拒绝。存在已分配 Entry ID 的零输入拒绝，也须核实原零输入收据。

找到 pending 后仍要求原单输入 completed，以及当前显式 supervising_agent 的原17字段流程；不能用普通 native plan 再点。完整 proof、当前前台/owner/epoch/deadline/ROI/单动作/准备顺序和后继读取，仍由原消费者执行。新记录继续指向原 pending。原 unknown 归档、输入、两次只读预算不改变。只见“创业指南”标题仍不关闭 startup_guide。

不改视觉校准、OCR、经济、GUI、manifest、资源/provider、READY、控制器、操作类型或现有 proof 字段。

## 固定最小验收

```powershell
python -B -X utf8 tools/replay_currency_wars_retry_obligations.py --output retry-obligations-windows.json
```

只运行两个新增生产消费者回归和原一项受影响的“continuation 已落盘后失败再恢复”检查，共3个方法。没有重新运行 PR29 8项、PR30 9项或旧经济/视觉/GUI套件。新模块只复用旧原生裁片和原声明协议，不引入图片。

1. 首次恢复在 continuation 写盘前因金币未知拒绝；同 ID 只读、旧 proof 换 ID/说明零额外捕获；新 inspect 可以同 PNG 字节而不同身份；新恢复一次只读，旧 pending 和失败报告不变。
2. pending 导航 -> 新主管意图 -> 发布前失去前台/零输入拒绝 -> 再 inspect 的普通 native plan 零额外捕获/输入 -> 新主管 proof 可继续；原归档与预算不变。缺失旧链源拒绝，只见标题不完成阶段。
3. 保留原“落盘之后报告失败”回归，防止新去重把合法新 inspect 恢复封死。

报告生成37项源码（24 production、原PR30的11支持文件、新driver/回归各1）、19项原素材/证据摘要与实际 loaded-source closure。无.git归档中 head=null 合法；测试前后字节实际计算，不伪填commit。完整消费者结果以ROOT在完整隔离PR30基础运行此入口所得报告为准；本制作环境无法materialize完整仓库，未宣称这些完整消费者已执行。

## 原接口和退出

- 奖励：保持原 `manual-step recover_reward --checkpoint-id ... --reply-file ... --request-id ...`。同ID查询原结果。首次失败且未写 continuation，也必须先 `manual-step inspect`，核返回原帧及新 request/capture/frame，再依据真实当前状态填原17字段。无需改金币/checked/native。
- 导航：正常首动作仍用原 decide；曾有已发布未知导航时，只允许当前 startup_guide ManualPhase 的新 inspect + 原17字段 `reviewed_plan`。新尝试零输入拒绝后，下一次仍需新主管 proof，不能改回无source的普通 native plan。guard_texts 保持原三个锚点。
- 接管后执行物理步骤前仍须原 broker explicit handoff；inspect 不伪造阶段。unknown delivery 先对账原请求。归档缺失、来源冲突或新页面不适用则只读/回ROOT，不删锁、不重置预算、不重发未知球或导航。
- 业务推进、全场领空、创业指南任务验证和每次出战仍是原合同；这两项修正不改变连续整局授权。

## 应用与发布

附带严格检查器先验证原35源码和19素材的冻结SHA，再在临时目录以 git apply --check/实际apply 验证整份patch、编译新/改Python，并与明确的字节替换计划逐项比较。--check不写输入目录；--apply只用于ROOT指定的隔离归档/checkout，不操作生产/READY。

应用后生成的来源报告包含所有新源码SHA、候选source-set和补丁SHA；这是新全文件字节的真实摘要。当前包不伪造未读取的完整runner新摘要，也不伪造远端head。ROOT在基于上述PR30的独立分支上只提交本补丁的5个路径，才能确保唯一父为PR30。

本批不改 manifest 或GUI编译输入。已有PR30/组合PR29 Windows结果由ROOT报告提供，不能冒称本修正通过。最终冻结候选仍须ROOT独立review、Windows、本机24源码+64素材/provider/GUI inputs绑定、READY/check-launch及真实导航。
