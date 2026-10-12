# 备战入口与末关预算来源

本批只做离线代码与已公开材料回放。当前游戏没有被读取或操作，worker/broker 没有启动，输入组件没有安装；business 和奖励进度不据此结束。旧 ROOT 临时入口误买 2 费刃属于已确认的语义前置检查缺口；本材料不据该事件认定原生 Worker 也发生了同一错误。

## 已有能力与这次补齐的边界

| 入口 | 已有检查 | 本次处理及限制 |
| --- | --- | --- |
| 唯一 broker / Entry | 所属身份、前台、暂停、提交锁、原请求与原回执、不重发已发布输入 | 保留原实现及 pin。底层坐标传输不理解“领奖”或“购买”，不能单独替代语义入口。 |
| `Worker.execute_plan` | 先 `observe()`，对比当前页面、请求身份与 epoch；普通坐标验证原帧和新帧完整目标 ROI；商店购买只能走实名实价的 `buy_shop` | 主管计划继续从这里执行。布局变化应结束该旧计划，重新观察和定位；不能把原坐标重新排队。 |
| `Worker.command` | 发布锁内核当前请求、epoch、match、节点、暂停、前台与出战批准；经济专用路径前置核预算，锁内持久原交易 | 补 `expected_page` 的实际检查，并在锁内重复核页面。此前该参数主要只写日志，直接调用可能绕过页面约束。 |
| `Worker.click_text` | 根据 OCR 唯一文字目标取坐标 | 补调用方 observation 与当前 snapshot、capture request、frame、page、stage 一致性。旧文字位置不能因名字相同继续使用。 |
| 库存改变后 | `invalidate_preparation('inventory')` 清除下游阵容、装备、经验与经济绑定；坐标计划须重新读取 | 保留。稳定装备效果文字不能携带旧位置、库存数量或穿戴完成状态一起复用。 |

新增的下层检查没有增加每次点击的全屏 OCR。它拒绝已被当前 observation 证实过期的页面或文字位置，**没有把“屏幕读取到实际发布之间完全原子”作为已实现能力**。`command` 锁内没有另一次 broker 截图；游戏自身自动弹窗仍可能在观察后发生。不要把裸 `Entry.request` 或直接 `command` 包装成已经具备页面识别的 ROOT 通用入口。

ROOT 应提交带当前 `request_id`、`snapshot_id`、`resume_epoch` 的语义计划；文字动作从本地当前行重定位，普通坐标附完整 `target_evidence`，经济动作附既有预算。执行前页面或目标 ROI 不符时只回传当前证据并重定位。此前已发布动作仍只对账原请求，不重试整个计划。下一步如需进一步缩短自动弹窗时间窗，应先有实际连续前后帧，沿同一 broker 的观察请求设计页面/目标区域复核；本批未另起截图或输入控制器。

## 最后备战可采用明确确认来源

原实现要求屏幕存在“剩余 N 次结息/回合”等完整读数才能降低标准 50 金币储备。现在保留这条屏幕读数路径，并增加一种**当前主管确认**：仅用于已明确的最后备战（剩余利息回合为 0）。

确认仍必须绑定当前局、当前节点、当前请求、当前 epoch 的原帧及模式；还须保存确认来自用户还是已核规则、可追溯引用与确认原文。只有节点字符串、空引用、旧请求、不同模式/局/epoch、未知来源、非零回合都不能使用此分支。它作为预算决策来源写入原 `reserve.rounds_evidence` 和台账，不写入 OCR 行、native confidence 或 `fully_read`。

在现有 `context_update.economy_plan.value.reserve` 内使用如下结构。以下是字段示例，尖括号部分必须来自实际证据；**没有为当前对局生成末关确认**。

```json
{
  "coins": 0,
  "critical_allowance": 0,
  "reason": "已有来源确认当前为最后备战，按明确购买/搜牌/经验预算转为战力",
  "remaining_interest_rounds": 0,
  "rounds_evidence": {
    "source": "supervisor_confirmation",
    "reviewer": "supervising_agent",
    "remaining_interest_rounds": 0,
    "request_id": "<当前请求ID>",
    "match_id": "<当前局ID>",
    "stage": "<当前已读节点>",
    "mode": "标准博弈",
    "confirmation_source": "user_confirmation",
    "reference": "<用户确认的消息/证据编号，或已核规则的版本化引用>",
    "statement": "<实际确认最后备战的原文与依据>",
    "proof": {
      "source": "observed_screen",
      "snapshot_id": "<当前原帧SHA256>",
      "evidence_file": "<当前请求的证据文件>",
      "resume_epoch": "<当前epoch>"
    }
  }
}
```

`confirmation_source` 也可为 `reviewed_rule`，此时 ROOT 须实际读过相应规则并给出可回查的版本/提交引用。代码验证确认与当前请求的归属并保留来源；**不会下载该引用或自动判断规则内容真假**。没有确认时保留 unknown 与原常规储备。没有加入“3-7 永远是末关”等硬编码，也没有把外部来源当成新的游戏规则数据库。

原共享预算、累计已花费、先买缺口/免费刷新/付费预算停止条件再 F 经验、关键缺口例外、超频储备为 0、未知效果不重发均继续执行。末关取消储备只改变可分配资金，不能跳过流程或授予出战批准。

## 稳定文字缓存的实际盘点

现有 `coaching.load_knowledge` 能从 `docs/GAME_KNOWLEDGE.json` 加载正文与 SHA256，剔除历史动态进度；`guide_reference` 能把缓存攻略绑定当前已应用攻略、模式与无冲突说明。`game_version` 和 `version_verified=False` 已存在，不能把这两个字段当成版本已确认。

本次公共分支实际没有 `docs/GAME_KNOWLEDGE.json` 文件；交接文档引用了本机已读缓存。这说明云端公开回放缺少该实物，不能推断本机也没有。当前回放如缺缓存，就保留该能力的输入缺口，不填造装备说明或攻略知识。

装备/策略/羁绊正文的版本化复用仍有待补齐：需要实际缓存正文、版本/内容签名与当前适用来源。缓存值只保存文字与出处；金币、商店、任务进度、库存数量/排序/位置、穿戴状态和当前羁绊激活另从新帧读取。库存重排后应按当前图标/角色重新定位，不能延长旧装备坐标的有效期。本批没有声明这项自动缓存闭环已完成。

## 采用的成熟方法

| 官方来源 | 本项目采用的最小方法 | 未引入的范围 |
| --- | --- | --- |
| [Python `unittest.mock`：where to patch](https://docs.python.org/3/library/unittest.mock.html#where-to-patch) | 测试替身放在实际查找依赖的命名空间，修复 installed provider 的内部引用与安装选择边界 | 不修改生产身份或 pin 规则来迁就测试。 |
| [Playwright：Actionability](https://playwright.dev/docs/actionability) | 把目标可见、位置稳定和实际接收点击看作动作前置条件；已有新帧与完整 ROI 检查继续承担相应职责 | 本游戏没有 DOM；这里借用设计原则，不声称已经获得浏览器全部保障，也没有安装 Playwright 控制游戏。 |
| [Playwright：Locators](https://playwright.dev/docs/locators) | 每次动作基于当前语义重新定位；文字相同不代表旧坐标有效 | 不缓存动态库存坐标。 |
| [ONNX Runtime：Privacy](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md) | 离线回放在进程导入前设置官方 `ORT_DISABLE_TELEMETRY=1`，再关闭 API 遥测；不改变全局网络权限 | API 调用可能晚于初始化事件，不能仅在引擎创建后关闭就声称无外联。以实际重跑是否成功为准。 |

这些链接本次实际访问过；技术做法只采用原项目/官方文档。首轮回放曾被自动审批拒绝可能的遥测 HTTPS 请求，拒绝记录和关闭遥测后重跑的结果单列，不把被拒绝的执行计入通过。JEV 继续后置，没有密钥、没有实际 API 运行，也没有模型延迟收益结论。
