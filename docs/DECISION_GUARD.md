# 货币战争：由 AI 自行运行的只读决策校验

**本工具不能自动认出截图，也不会计算“最优阵容”。** 它只检查小青蛙已经从本局画面和可信游戏规则中整理出的 JSON：路线是否无声漂移、计划是否超支、团队减伤改变后主C防装是否值得复查。**用户只需继续正常截图和操作，无须填表、执行命令、提供额外审查意见。**

## 哪些节点调用

| 触发 | checks | 自行核实的关键事实 |
| --- | --- | --- |
| 升级越过攻略规定搜牌等级、正式改主轴 | `["route","economy"]` | 原搜牌等级、核心目标是否已达、改变前后目标、金币与经验成本 |
| 多次付费刷新、购买经验、最后一战支出 | `["economy"]`（需要时加route） | 当前真实余额、付费刷新价与次数、计划购买、至少保留的购牌费、是否最后一战 |
| 主C换装、减伤词缀、猫猫糕／很硬的甲等团队效果变化 | `["gear"]`（需要时加route） | 主C前后台、真实在身上的防装、团队加成适用对象与是否生效、实际可用输出装备、叠加方式 |

不要每次商店刷新、每个点击都调用。每次调用取**本轮最新真实状态**；一张画面看不到全部字段时，从本局之前已验证的资料补齐，并标记机制不确定性。无法得知的决定性事实不得编零或编 `verified_active: true`；可以先只运行有充分数据的检查，其他项标注“未执行/证据不足”，不得把局部 `PASS` 当作全盘通过。

## 命令（小青蛙自己运行，不交给用户）

仓库主线、无需第三方 Python 依赖：

```powershell
python -B -X utf8 tools/decision_guard.py --input path/to/current_state.json --output path/to/check_result.json
```

也支持标准输入／标准输出（不指定参数），结果为 JSON。退出码0表示 `PASS`、1表示 `REVIEW`、2表示 `BLOCK`。程序只读输入并可选写出本地检查报告，**不读取游戏画面、不联网、不访问任何账号、不操控鼠标键盘**。

- `BLOCK`：算术错误、缺必要字段或违反明确阶段规则；**不能照原方案直接执行**，AI 应先修正并重跑。
- `REVIEW`：发现潜在漏算或路线转型，AI 必须自行重新比较（如主C防装与现有输出装）；如果叠加条件仍未知，不可把假设作为确定收益。
- `PASS`：本次填入的数据没触发已编码的冲突；**绝不表示截图正确、机制全面、胜率可靠或装备最优**。报告的 `input_sha256` 只绑定 JSON 内容，不认证原截图。
- **工具不可用**：清楚说“数值校验未运行”，而不是冒称通过；优先减少不可逆转型、避免将未知当确定事实。不要求用户自己安装 Python、打 JSON 或手工检查。

## 输入样例（仅展示结构，不是第三局真实快照）

下例模拟小青蛙想在5级追三星的核心未达成时升到6级；`route` 检查应发出 `ROUTE_CHANGE_UNDECLARED`。若决定正式转型，必须补 `switch` 对象的 `to/reason/new_core_goal/new_roll_level/buy_priority`，届时仍为 `REVIEW`，不会自动判定转型正确。

```json
{
  "snapshot_id": "illustration-only-not-live",
  "checks": ["route"],
  "route": {
    "name": "5级搜低费三星",
    "core_goal": "小黑塔三星",
    "current_level": 5,
    "planned_level": 6,
    "roll_level": 5,
    "core_goal_complete": false,
    "core_owned_copies": 4,
    "core_target_stars": 3,
    "claim_complete": false
  }
}
```

**金币字段**（启用 `economy` 时必填）：`gold`、`refresh_cost`、`planned_paid_refreshes`、`planned_buy_cost`、`planned_exp_cost`、`reserve_for_target` 为非负整数，`final_battle` 为布尔值。最后一战留有明显未使用金币时返回 `FINAL_GOLD_UNUSED`；如果经过商店和板凳核查确实没有有效提升，可以写入 `unspent_reason`，这仅是**已作解释的文字记录，工具不会验证该理由正确**。

**装备字段**（启用 `gear` 时必填）：`main_carry`、`position`（front/back）、`damage_reduction_threshold_pct`、`team_effect_changed`、`defensive_items_on_carry`、`available_offense_items`、`stacking`（additive/multiplicative/maximum/unknown）、`team_dr_excluding_carry_items`。最后一项数组中的每个效果包含 `name`、`pct`、`scope`（front/back/all）、`verified_active`。**必须排除主C自己装备提供的减伤**，才能判断撤掉主C防装之后团队减伤还剩多少。

例如大黑塔前台，队伍另有猫猫糕15%和很硬的甲全队18%减伤，若已确认两项同时生效且按乘法叠加，则不含主C装备的减伤计算为30.3%，达到20%词缀门槛；但若叠加规则未知，工具返回 `STACKING_UNVERIFIED`，不能当作已达标。即使门槛已达，工具也仅建议**重新比较自适应外骨骼的生存价值与现有输出装**，绝不自动卸装。

## 验收口径

运行以下离线用例（不操作游戏）：

```powershell
python -B -X utf8 -m unittest discover -s tools -p test_decision_guard.py -v
```

这些检查至少覆盖：5级追三却计划升级、11金币留4金币购牌时刷新超支、最后31金币未解释地留到终战、团队减伤改变后主C装备复核、叠加方式未知、不实三星主张和输入字段缺失。

**目前在独立的 Python 离线环境验证；未在小青蛙的电脑或真实游戏窗口中执行。** 这份工具只有在小青蛙能够自己调用并真正用其结果修订决策时才算接入。使用它不代表任务自动完成或可以保证通关。
