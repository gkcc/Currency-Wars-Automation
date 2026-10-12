# 新节点桥开发记录

本说明只记录新增模块的开发验证；整合候选的最终源码绑定选择由主交付记录。

## 已知验证操作错误

首轮命令为：

```bash
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=tools:/workspace/scratch/5c64bf8ff52e/test-deps python -B -X utf8 -m unittest -v test_currency_wars_manual_stage
```

当时测试文件直接导入了 `RuntimeCompatibilityTests` 类。`unittest` 在新增6项显示 `ok` 后，又自动发现该导入类并误跑了旧检查，违反本批不重跑旧选择的要求。发现后实际发送 Ctrl-C，进程退出码130。终止前输出可见16项旧检查标记 `ERROR`，未取得其最终异常摘要；未追跑这些旧项或把它们算入本批通过数。错误原因不在本说明中猜测。

可见的16个旧错误方法名：

```text
test_authenticated_run_does_not_depend_on_elevated_temp_directory
test_boss_result_only_advances_once_and_does_not_confirm_a_match
test_external_marker_change_is_not_adopted_by_registration_or_close
test_incomplete_and_unknown_children_keep_owned_directory
test_installed_child_registration_and_verified_exit_cleanup
test_new_ipc_directory_grants_its_real_user_inheritable_access
test_progression_knowledge_loads_conditions_but_never_cached_progress
test_recognized_update_notice_only_closes_and_rereads
test_reward_capacity_after_input_bad_frame_only_reobserves_never_repeats_sale
test_reward_capacity_b003_manual_result_after_multiple_handoffs_closes_unknown_pending
test_reward_capacity_b003_manual_result_supersedes_unknown_sale_without_claiming_success
test_reward_capacity_exact_b003_resume_reconciles_original_sale_in_new_epoch
test_reward_capacity_missing_receipt_or_wrong_difference_stays_pending_without_input
test_reward_capacity_new_epoch_or_later_mutation_does_not_copy_completion
test_reward_capacity_single_sale_requires_receipt_and_new_capacity_then_returns_to_rewards
test_unchanged_frame_cannot_certify_fabricated_settlement_details
```

已改为模块导入 `import test_local_runtime_compatibility as compatibility`，并改用精确类选择 `test_currency_wars_manual_stage.ManualStageTests`。之后新增阶段桥7项检查一次完整结果为7通过、0失败/错误/skip、1.670秒。随后仅把阶段桥读取收窄为现有 `rewards` scope，最终整合选择另行绑定该源码；此处不将开发中的时间称为最终测量或真实游戏提速。

新增检查全部使用现有惰性控制 fixture、真实文件、真实 Entry/ManualPhase/CAS 函数以及声明的协议HUD；没有新游戏截图、游戏输入、在线模型或安装。用户报告的误买收据在本仓库未找到原物，未伪造或回放该真实收据。

## 整合开发中实际失败及修正

以下均属于本批新选择，最终通过不覆盖这些开发失败事实。未重复上传每次完整 Worker 对象或每次源码摘要表。

| 新选择/运行 | 实际结果 | 后续处理 |
| --- | --- | --- |
| Boundary 首轮8项 | 2 failure、1 error，9.309秒 | q00灰色商店标题不满足导航亮字条件；原fixture未写runner-owner，raw拒绝前提不成立。分别加有限局部标题核验、补声明的runner-owned fixture。 |
| 新失败三项定向复核 | 1 failure、2 error，4.284秒 | 新灰色锚点分支遗漏局部cv2 import；测试从被fixture替换的PROJECT读取生产源码。补import、改按测试文件真实目录读取。 |
| 上述三项再次定向 | 3通过，5.811秒 | 尚非最终冻结选择。 |
| 新控件/首笔授权/阶段消费者三项 | 2通过、1 error，0.993秒 | 最小frame_worker fixture无context；仅补fixture，不给生产默认填值。 |
| 第一次完整39项 | 38通过、1 error，15.911769927秒；源码前后相同 | 该最小fixture没有真实publish所需的state_sequence，补fixture的状态通知替身。 |
| 阶段消费者定向复核 | 1 failure，0.120秒 | fixture仅有HUD，无原出战按钮，先被原战斗目标守卫拒绝；补声明的当前CTA，使测试到达所要区分的旧经济pending守卫。 |
| 阶段消费者再次定向 | 1通过，0.245秒 | 没有修改生产守卫来迎合fixture。 |
| 最终完整新选择 | 39通过，0 failure/error/skip，15.628463081秒 | 41项源码/driver/fixture摘要前后一致，见reward-manual-boundary/acceptance.json。 |

最终driver在执行前枚举所有测试ID，只有五个明确新类的`test_`方法可以运行；loader错误或意外导入旧类会在执行前拒绝。后续开发定向通过不与最终39项相加。
