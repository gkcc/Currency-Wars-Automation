# 玩家 HUD 主体绑定：四张公开 PNG 的离线回放

本批只读取已公开、已遮挡 UID 的 PNG，没有运行游戏、截图或发出输入。四图与 `../fixtures/manifest.json` 从公开提交 `9c3329c` 提取，逐张核对 `export_sha256`，且在修改前实际查看完整图片；本批没有修改图片像素。源码基准为 `4830dfe8510171519b1961b2116968495d51a292`。

修复前，`Perception.read` 用全屏 `Lv/等级` 正则填玩家字段，实际把商店角色 `LV.999` 截成 `99`，把首领 `等级90` 填成玩家 `90`。修复将玩家字段绑定到当前 1920×1080 左下角的“购买经验”面板：唯一、高置信的按钮和经验分数锚点，再读完整的玩家 `Lv` 标签和数字；需要时只追加一次完整 ROI 识别。原始 OCR 文本、置信度和框不改写，不按经验分母猜等级。HUD 不可见、冲突、低置信、非法等级或布局不匹配时返回未知。

| 公开样例 | 可见玩家真值 | 修复前实际字段 | 修复后实际字段 | before 单次秒 | after 单次秒 |
| --- | --- | --- | --- | ---: | ---: |
| `shop_character_level_999` | 9 | 99 | 9 | 5.581759 | 2.986807 |
| `shop_actual_player_level_8` | 8 | 99 | 8 | 6.851914 | 2.146916 |
| `boss_level_90_intro` | HUD 不可见 | 90 | 未知 | 1.299517 | 0.690703 |
| `final_preparation_player_level_9` | 9 | 9 | 9 | 4.389615 | 1.862552 |

三张可见玩家 HUD 的精确可读率为 **1/3 → 3/3**；隐藏 HUD 正确保留未知为 **0/1 → 1/1**。四图精确期望合计为 **1/4 → 4/4**。测试将“不得污染玩家字段”和“可见真值确实读对”分开：已知可读的 8/9 返回未知会使公开回放测试失败，不能全部退回未知后报通过。当前环境在修复前就读对最终备战 Lv9，**没有复现本机报告的该图 unknown**；没有把它算成本次修好的复现结果。

`before.json` 与 `after.json` 保存四图完整 `observed`、原始 OCR rows、状态/商店/语义结果、实际源码 SHA256、依赖版本和外层计时。before 从上述 Git 对象执行未经修改的生产 reader；after 执行工作树生产 reader。两者都调用真实 OCR，每次进程内连续读四图，首图包含引擎初始化。秒数仅为该次 `Perception.read` 微基准：并行工作负载、预热和运行时状态不受控，**不能据此认定提速，更不是整节点、战斗或端到端耗时**。探索 ROI 和验证期间还有其他读取；这里保留的是关闭遥测后的最终完整四图回放，不是统计性重复实验。

## 数值域与布局证据

`valid_population_counts` 当前支持人口 `0 <= occupied <= capacity <= 12`、容量至少 1；12 是当前支持解析的范围，不宣称游戏永久最大值。玩家等级仍仅接受完整 `Lv1..10`。任务要求的团队容量 12 不受玩家等级上限控制。

现有 `StateReader.native_slots()` 只有已观察的前台 4、后台 6、替补 9 槽，本批不增加虚拟槽，不声称识别过 12 槽布局。`7/12` 数值有效时可保留，但 `team.checked` 仍要求容量不超过实际支持的 board 槽数，并满足原完整身份、星级、位置与人数核对。新增生产方法测试确认同份七人完整固定槽证据下 `7/10` 可核、`7/12` 不可核。没有真实 12 人口 PNG，扩展布局仍需实图。库存容量与人口独立，临时溢出仍未知，不能由 `8/9` 推定库存无问题。

公开包没有私有商店/角色/装备模板，相关识别继续保留 unknown/未完整读取；四图玩家字段通过不等于队伍、奖励、整局自动化或出战验收通过。`currency_wars_state_reader.py` 未修改；主代理已在同批 runner 的空人口建议、出战阶段复核与动作门复用同一人口 helper。既有回归增加 12/12 与不足/溢出子案例，只验证数值域，不假造完整 12 槽阵容证据或出战批准。

## 复跑与已完成检查

在仓库根目录运行；`PYTHONPATH` 的依赖目录按本机安装位置替换，依赖来自已有 `requirements.txt`，本批未安装新依赖。

```bash
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools \
  python handoff/2026-10-07/player-hud/replay_player_hud.py --version before --output handoff/2026-10-07/player-hud/before.json
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools \
  python handoff/2026-10-07/player-hud/replay_player_hud.py --version after --output handoff/2026-10-07/player-hud/after.json
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools \
  python -m unittest test_currency_wars_state_reader.NativePlayerHUDTests test_currency_wars_state_reader.NativePopulationTests test_currency_wars_state_reader.NativeCapacityTests test_currency_wars_state_reader.PublicPlayerHUDReplayTests -v
```

最终聚焦检查 **14 项通过、0 跳过，8.257 秒**；包含真实四图一次生产回放和保留原人口/库存守卫的检查。私有保留图测试不在这 14 项中，不把未运行私有检查计为通过。本批没有 Windows 或实机验证。after 生产 reader SHA256：`8e13f440d380836bb6896ac445c265f9da75824edc985d988651a3b7919d1140`；未改 state reader：`69acca4d20625fc23f41fa9872b9855fd9f3f526a1f29b949e432dd9422ed8a2`。

主代理将人口 helper 接入 runner 后，独立重跑以上选择并加既有 `test_old_completed_reviews_do_not_allow_current_underfilled_battle_input`，**15/15 PASS、0 skip、13.450 秒**。这 15 项包含上述 14 项，不相加。完整选择、源码 SHA256、fixture 字节核对与限制见 [verification.json](verification.json)。该提交叠加在测试依赖小修 `23944b1c85522b0d4e64778c3c9d511d89fb08bc` 之上。

## 离线执行与遥测处理

一次早期 after 运行的完成轮询被自动安全审查拒绝，原因是 ONNX Runtime 尝试向 Microsoft 发送未经授权的遥测 HTTPS；那次产物不计为成功、不作为这里的 after 证据。随后采用官方提供的更安全配置：新进程在导入 ORT 之前设置 `ORT_DISABLE_TELEMETRY=1`，并调用 `disable_telemetry_events()`；未更改网络权限或审批规则。这里保留的 before/after 都在该配置下实际完成、退出码 0。生产 reader 也在自身首次导入 ORT 前设置开关并禁用 API 遥测；如果别的调用方更早已初始化 ORT，无法追溯撤销它此前的行为。

实际读取的官方主源：[ONNX Runtime Privacy — Disabling Telemetry](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md#disabling-telemetry)。该文档说明非 Windows 初始化开关应早于运行时初始化；只在导入后调用 API 可能晚于最初事件。当前 Linux 离线回放按这两个层次执行，未以 Windows 实机结果替代证明。
