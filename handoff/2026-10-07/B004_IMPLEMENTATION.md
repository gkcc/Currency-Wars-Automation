# B-004：领奖腾位的单次闭环与任务资源候选

独立开发基准：`beaea343ee7edf29bcba70ba8351a7dc462a83da`。交付父提交为 B-003 的 `ddcb460c412811991ee5a96abbf867bd6a6c396d`，分支：`fix/reward-capacity-20261007`，按叠加PR逐SHA验收。
本批只在分支准备；当前未结束的标准局继续冻结核心。云端没有发游戏输入，也没有证明实机领奖或整局自主成功。

## 已有能力与本批改动

沿用 `coaching.PHASES`、`preparation_status`、`Worker.guard_preparation_action`、原生前4/后6/备战席9布局、原 `drag` 动作、唯一 broker、当前请求/epoch/暂停守卫和不可变帧。没有新增输入控制器。

正常顺序仍为全部奖励/选择 → 创业指南 → 常规清无用牌 → 经济 → 布阵装备 → 新帧出战验收。只有当前满备战席实际阻塞角色奖励时，允许单独腾一席；这个例外不放行普通清仓、购买、刷新或经验。卖后原收据与新帧没有对账前，不发第二次输入。

| 路径 | 行为及边界 |
| --- | --- |
| `StateReader.native_capacity` | 新增 `semantic.team.capacity`：单独报告9个备战席是否读全、占用及空位。原生尚无临时溢出模型，`overflow_checked=False`、`overflow_count=None`。场上8/8不改变这些字段。 |
| `coaching.reviewed_capacity` | 接受主管明确逐席与独立溢出实读，保持 `origin=supervising_agent`，不写原生 `fully_read/checked/confidence`。 |
| `coaching.reward_status` | 已接入 `Worker.review_preparation` 的当前奖励领空判据。没有全场模板匹配不等于领空。仅当前请求原图的主管全场复扫可为 `clear`；旧帧完成标记为 `unknown`；奖励选择页为 `pending`。身份仍由 Worker 验证，B-003 的历史奖励仍先暂缓。 |
| `Worker.reward_capacity_action` | 只支持原生备战页满9席、零临时溢出、当前角色奖励被容量阻塞、一个实名/星级/实售价/不再需要的备战席角色、已明确识别的出售控件。库存与钱若有原生可信读数，主管声明必须一致。 |
| `Worker.capacity_rois` | 用请求持久原PNG和输入前新PNG的真实字节身份，完整解码并比较9席、金币、独立溢出检查区、被阻塞奖励及出售控件区域。相关区域变动即重新观察，不放宽动画阈值。 |
| `Worker.command` | 实际输入请求ID生成后立即绑定 `reward-capacity.json`；发布前明确拒绝可以记录 `refused`。一旦可能发布，结果未知不能当作未出售。原完成收据和截图错误仍分开处理，只补 `observe`。 |
| `Worker.review_reward_capacity` | 独立无输入复核：原请求动作/收据、新捕获的请求ID/帧ID/哈希/时间、9席及独立溢出、金币差额全部对账。正常成功须9→8席、原选槽空、无溢出、钱增加实售价；成功后立即回 `rewards`，不标清单完成。 |

当前没有可靠的全场奖励扫描器、临时溢出布局或通用出售按钮模板。本批使用已有主管当前请求 proof 通道；不能把任意拖往屏幕边缘声明为销售。若出售控件只在拖拽中显示、无法在当前证据中明确确认，或已有非零/未知溢出，此窄路径拒绝执行并回传缺少的事实。不能把本批宣称为通用自动清库存。

## 主管输入契约

仍发原 `drag`，一个计划只能包含这一个库存动作。使用：

- `purpose="reward_capacity"`，`expected_page="preparation"`，原文字守卫不省略。
- `target_evidence.control_id="reward_capacity_sale"`，同当前请求 `snapshot_id`；`bounds` 为合法原生ROI。
- `capacity_review.proof`：原有 `source="observed_screen"`、当前 `snapshot_id/evidence_file/resume_epoch`，180秒有效。
- `capacity_review.value`：`reviewer="supervising_agent"`、当前 `stage`、具体 `findings`、实读 `coins`。
- `value.inventory`：`bench_capacity=9`，`slots` 必须逐列1至9、每槽 `status="occupied"`，选售槽必须有 `name/star`；`overflow_checked=true`、`overflow_count=0`、实际检查区域 `overflow_bounds`。
- `value.blocked_reward`：`pending=true`、`blocked_by_capacity=true`、`kind="unit_reward"`、当前目标 `bounds` 与具体阻塞 `findings`。
- `value.sale`：`slot/name/star/sale_value`、`not_required=true` 及核过攻略/升星/强制同场需要的 `reason`、`control_verified=true`、`control_text="出售"` 与实际 `control_bounds`。拖动起点必须在该原生备战槽，终点必须在这个已核出售控件。

这些布尔值是主管看过真实图后的明确结论，不是让执行器填默认值。缺证据就不发送腾位动作。当前 native 事实冲突时，不能以主管字段强行覆盖。

动作后 Worker 回传 `reward_capacity_pending` 和新请求。下一条回复用原 `finish_preparation_review`，配 `context_update.reward_capacity={proof,value}`。`value` 带当前完整 `inventory/coins/stage/findings/reviewer` 和原 `input_request_id`。它必须是独立无输入计划；正常闭环对账成功后，再由当前状态规划奖励领取。

运行态 `reward-capacity.json` 以及所属 debug 下 `<真实输入ID>-reward-capacity.json` 保留原前置证据、输入ID、watermark、收据和后置 proof。它们不改 request-ledger，也不复制旧阶段完成标记。

## 与 B-003 恢复的接口

同epoch正常腾位不依赖 B-003。跨epoch或人工替代出口复用 B-003 的真实 `manual_receipt_state`、`verified_resume_event(run,owner,control,epoch)`、`Worker.verified_manual_source` 和其 mutation fence；未安装这些能力时保留 pending，明确等待恢复对账。

1. 原出售已完整执行时，当前 proof 可附 `resume_event_id`。必须是 pending旧epoch → 当前epoch 的那个持久CAS事件、同run/match/stage、原resume收据一致且新帧在恢复之后。fence只放行该一个已核resume，额外输入/中间恢复仍拒绝。旧动作不重发。
2. 原出售效果未知或后来人工改变库存时，主管可附 `resolution="manual_reconciled"`、`manual_checkpoint_id`、`resume_event_id`。必须读取 B-003 原落盘的成功 rewards/inventory_cleanup checkpoint；其 before 在原腾位之后，原输入ID在前水位，完整真实收据、前后图、CAS及后续fence全部通过。当前库存/钱/独立溢出仍用本请求 fresh proof 复核。
3. 第二条只将旧记录标 `superseded`，原 `sale_outcome` 仍是 `unknown`。它不把人工整理归功于旧出售，不继承旧领空标记；关闭后从当前 `rewards` 重新完整复核。没有普通context覆盖pending的入口。

原出售的成功归因只支持确切的一次旧新epoch衔接。经过多次交接后，当前完整人工整理仍可用第二条出口关闭旧pending：只认最新真实CAS和当前checkpoint覆盖的库存，不要求旧出售属于最新CAS的上一代，也不将旧出售记成功。换局/换节点或不全收据继续回传，不猜CAS链。

## 特权赋予卡升级0/2：主动候选，尚无安全执行器

现有 `progression_plan` 已能把未完成任务关联到特权赋予卡，当前库存实名资源可用 `verified_resources_available`。但没有同时具备可靠识别与输入守卫的进阶装备目标/使用升级入口，所以不能将 `execute_ready` 改为true。

本批补 `resource_reward_candidates`：当前选择页的完整标题和效果文本提到所需卡时，输出同snapshot的卡序、完整效果、ROI和任务资源优先级，供既有选择守卫采用。文字提到资源不必然表示授予，仍需核完整效果与当前奖励取舍。候选与升级动作均保持 `execute_ready=false`；旧进度不产生当前自动执行证书。

推进该任务还缺本机最小证据：当前卡实名/数量；合格进阶装备实名与升级资格；实际使用/升级控件；原输入收据；消耗与0/2→1/2的新帧。未提供前不标任务完成，也不虚称自动升级已经落地。

## 实际离线验证

复用现有 `manual_bridge_fixture`、真实 Entry 请求/ledger、Worker执行路径和已有测试文件，没有新测试服务。合成PNG和字段只验证合同，不冒充真实游戏识别样本。

独立 B-004：下面命令共23项，20通过，3项因尚未安装 B-003 恢复函数而明确跳过。随后在同一测试进程载入 B-003 工作树的实际恢复/对账函数和两个 Worker 验证方法，三个恢复项均实际通过；没有修改 B-003 工作树。这是实际函数的组合检查，尚不代表最终合并分支或 Windows 实机验收。

```bash
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python - <<'PY'
import unittest
from test_local_runtime_compatibility import RuntimeCompatibilityTests
suite = unittest.TestSuite()
extra = {'test_unclaimed_rewards_block_spending_and_selling',
         'test_preparation_reviews_survive_navigation_but_inventory_invalidates_downstream'}
for name in unittest.defaultTestLoader.getTestCaseNames(RuntimeCompatibilityTests):
    if name.startswith(('test_reward_capacity_', 'test_reward_clear_')) or name in extra:
        suite.addTest(RuntimeCompatibilityTests(name))
suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_currency_wars_state_reader.NativeCapacityTests'))
suite.addTests(unittest.defaultTestLoader.loadTestsFromName('test_currency_wars_progression'))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

根已在 B-003 实际父提交上叠加本批，并接入 `reward_status`。最终25项聚焦检查全部通过、没有跳过：上述23项，加当前监督复核与指南后回扫两条真实路径。三个CAS/人工替代检查现在使用完整组合源码运行。首轮发现旧复核fixture漏写历史帧自己的snapshot_id，并仍断言指南领奖后保留旧领空；已按真实history结构和新依赖修正测试，未放松生产守卫。完整源码摘要及初次/最终结果见[B004_VERIFICATION.json](B004_VERIFICATION.json)。

关键覆盖：单次销售后立即回领奖；未知/非零溢出、未满席、未确认出售目标、旧epoch或ROI变化拒绝；经济伪装拒绝；输入完成而图坏仅重观察；金币/空槽不符或矛盾/未知输入收据保持pending；后续输入不能被归因给原出售；合法恢复原收据与人工替代出口均不重售、不伪造历史成功。根对7份修改Python文件做AST解析，暂存差异检查通过；没有运行Windows打包或实机。

实机门槛：本局结束后安装经最终合并SHA核过的版本；用真实1920×1080满席阻塞角色奖励与已核销售对象，验证一次销售、收据、空槽/钱、立即回领奖以及脚本继续。非零溢出和暂时重排不在当前支持范围。B-004本身不改固定输入组件；B-001的已安装组件更新要求仍适用。按“SHA、B-004、run_id、节点、原输入ID、前后事实、实机结果”回传。
