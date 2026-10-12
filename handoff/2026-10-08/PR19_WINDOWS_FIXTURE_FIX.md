# PR19 Windows 两项夹具错误修复

本次只改 `tools/test_currency_wars_node_profile.py` 的两个测试方法，沿现有草稿 PR19 分支快速前进。唯一直接父为原 PR19 `ceee2f0e94f9c717bd04a0e5e4104305b08a81bf`，其父 PR18 `1c48d12f88944a5532371ac3192710b85984cc42` 保持冻结。最终发布 head 以 PR19 和完整回复中的实际回读为准；本文件所在提交包含这次修复。

已实际读取 [ROOT Windows 失败报告](https://github.com/gkcc/Currency-Wars-Automation/blob/2bb9c1fbb921458e5036abbef5a120fe53f13f6c/handoff/2026-10-07/ROOT_PR19_WINDOWS_ACCEPTANCE.json)：原 39 项为 0 failure、2 error、0 skip，8.6498693 秒。原 PR18 14 项由 ROOT 验过，本轮未重跑。实际 fetch 并通过 GitHub 接口回读的 main 仍为 `4cf23830e7832ee68b4ef40ba688b006ee3064fc`；用户给出的证据提交通过其不可变 URL 读取，不冒称已在 main。

## 原因与最小修复

| 失败 | 代码原因 | 本次修复及保留边界 |
| --- | --- | --- |
| overlap 期望空列表，却得到两个 OCR UUID；随后 WinError32 覆盖断言 | 连续真实 monotonic 读数可相等，两个零区间合法，并非正时长重叠；断言早于 `close()` | 父窗口固定为 `[1,4]` 秒，OCR 为 `[2,3]` 秒，再复制该正区间验证拒绝。全部原 12 个反例保留；在同一方法中增加合法零跨度场景。录制阶段的断言置于 `try/finally` 内，在临时目录退出前关闭 JSONL。 |
| legacy/missing timing 的 `nodes[0]` 越界 | 夹具整个观测窗口可能为零；汇总仅为正时长分配 node bucket | 同一方法分别固定为 1 秒和 0 秒。前者验证恰一节点、perception=1、OCR engine=0；后者验证 `nodes=[]`、全局各耗时为 0，保留原零跨度 perception span。也使用 `finally` 关闭资源。 |

零跨度 read 及其两个同点 OCR 调用明确验证：两个不同 ID、无 issues、两个 span 的 inclusive/exclusive 均为 0；正父窗口中的 perception 仍为 3 秒。旧 OCR 与缺失 timing 不被重标为 engine，旧/新计时来源契约仍不可比较。

没有 sleep，不依赖内部读钟次数，不把 `start == end` 改成拒绝，不强造节点。生产 `currency_wars_profile.py`、Perception、消费者、来源清单和 bridge pin 均未修改。两方法内新增 `subTest`，原冻结 driver 和 39 个测试 ID 不变。

## 本机执行与源码绑定

[本次紧凑验收](pr19-windows-fixture/acceptance.json)由原 driver 实际生成：Linux / Python 3.12.14，**39 passed、0 failure、0 error、0 skip，14.537579183 秒**。本轮只运行这份原聚焦选择一次，没有扩跑旧类。独立只读复核检查了两个方法的差异，未另跑测试。

报告完整列出 41 项生产文件、driver、测试及公共 fixture 的 SHA256，测前测后差异为空。对比原 PR19 报告，文件集合和测试 ID 完全相同；40 项摘要不变，唯一变化为本次目标测试文件。24 项生产来源文件全部不变。

| 绑定 | SHA256 |
| --- | --- |
| 新 41 文件 source-set，测前与测后相同 | `eeae5e854276d74ee16d976cb8b80001e79dac50b0f062708e39c220936b2e7f` |
| 修改的 `test_currency_wars_node_profile.py` | `148f376d8f1b812c0b80c13689df23e713e09aae6ddc44d65c9c05eb24b824aa` |
| 未变的冻结 `replay_currency_wars_reward_manual_boundary.py` | `e557968754b0e96a4f0e4a667d784a77f6a0c4ddb9d6d07a843231dd813d4c05` |
| 未变的生产 `currency_wars_profile.py` | `25a4402b6b4bcbc1709cd23a938535d0562bbb974cb4e905d8a1f9479342641d` |

driver 在提交前执行，报告 `tested_checkout_head` 如实记录原 `ceee2f0…`，不能单凭该字段把未提交修改当作旧提交字节；实际验收对象以完整 41 文件测前/后摘要为准。发布后再 fetch 新提交，将这 41 项与发布树逐文件核对；结果由完整回复和 PR19 当前发布说明记录。报告不事后改写为新 head。

## ROOT 局后 Windows 聚焦复核

在最终新 head 的独立检出中，沿用 ROOT 原验收已具备依赖的 Python 环境和 `$acceptDir`；不需要私有素材、不启动游戏或 broker：

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'tools'
python -B -X utf8 tools/replay_currency_wars_reward_manual_boundary.py `
  --output "$acceptDir/pr19-windows-fixture.json"
```

核原五类/39 个 ID，0 failure/error/skip，41 项摘要与本次报告相同，测前测后差异为空。driver 同时在本机生成 `.tests.txt`；本次公开紧凑 JSON 17,711 字节，不重复上传该日志或既有 PNG。

**本次 Linux 通过不代表 Windows 已通过或已部署。** GUI 24 项来源校验、Rust 6 项、原桥构建/独立核包/安装及真实 READY 仍待 ROOT 原安排，未执行或提前判通过。组件升级仍沿 [原 PR19 交接](../2026-10-07/REWARD_MANUAL_CONTINUATION.md)；本次没有变更任何生产字节，因此也没有新增组件 pin。

## 后续问题边界

已读 [整局复盘](https://github.com/gkcc/Currency-Wars-Automation/blob/2bf13da4cb8a18672cab0103c5542aaf05be82c7/handoff/2026-10-08/ROOT_FULL_MATCH_REVIEW.html)、[下一任务](https://github.com/gkcc/Currency-Wars-Automation/blob/2bf13da4cb8a18672cab0103c5542aaf05be82c7/handoff/2026-10-08/ROOT_PRO_NEXT_TASK.md)与 [PR19 评论](https://github.com/gkcc/Currency-Wars-Automation/pull/19#issuecomment-6048806641)。待售集合/奖励领空的阶段关闭、布局变化废弃剩余坐标、7200 秒到期等问题不属于本次夹具修复，未宣称已解决。未扩普通动作契约，未操作 ROOT 游戏、GUI、broker、安装候选或在线模型；没有整局提速结论。

原冻结报告、Windows 失败证据及历史开发失败记录均保留。本次仅发布一个测试文件、紧凑验收和本文三个 blob，复用现有公开素材；不上传私有 57 素材、UID、owner/token，不重试已知缺凭据的 CLI push。
