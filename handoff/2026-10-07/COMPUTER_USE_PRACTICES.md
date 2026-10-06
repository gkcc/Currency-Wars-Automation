# Windows 电脑操控实践：赛后复用方案

调研日期：2026-10-06。执行触发：当前整局结束、准备归并 `COACHING_BACKLOG.md` 时先读本文件。用户要求先参考成熟电脑操控实践，再决定哪些基础能力需要自己实现。本文完成官方文档及部分源码对照；尚未安装候选框架，也未验证它们对本游戏的实机兼容性。引用 main/master 会变化，试验前固定实际版本或提交。

## 选择结论

优先试验 Airtest 的图像定位与等待能力；Windows 控制台继续采用已有 UIA 路径，参考 pywinauto 的控件等待规则。借鉴 UI-TARS 的可替换 Operator 接口及 UFO 的失败回传机制。OmniParser 仅作为识别瓶颈仍存在时的候选。

以上排序是根据当前 Python 控制链和本局缺口作出的判断，尚无实测速度、成功率或整局收益对比。框架接管鼠标不等于理解游戏规则；奖励优先、超频经济、攻略阶段、羁绊适配仍由本项目负责。

| 候选与一手来源 | 已核实的能力 | 对本项目的具体用途与限制 |
| --- | --- | --- |
| [Airtest](https://github.com/AirtestProject/Airtest)；[图像 API](https://github.com/AirtestProject/Airtest/blob/master/airtest/core/api.py)；[匹配源码](https://github.com/AirtestProject/Airtest/blob/master/airtest/core/cv.py) | 支持 Windows 应用与游戏图像自动化；`wait` 有超时，`exists` 返回定位结果；模板携带录制分辨率与位置，支持多尺度匹配。 | 优先复用匹配模块，在本项目已有截图上定位按钮、奖励球与稳定图标。动态背景、相似头像、遮挡和布局重排仍须实测；不能把分辨率适配当成任意布局都可靠。 |
| [pywinauto 入门](https://pywinauto.readthedocs.io/en/latest/getting_started.html)；[等待机制](https://pywinauto.readthedocs.io/en/latest/wait_long_operations.html) | Win32/UIA 控件定位；可等控件出现、消失、启用或激活。官方建议先用检查工具确定目标是否暴露控件。 | 控制台的继续/暂停等采用控件身份和状态等待；游戏内部卡牌是否可用 UIA 须实际检查，不能按普通 Windows 按钮推定。控件未暴露时采用视觉定位。 |
| [UI-TARS Desktop](https://github.com/bytedance/UI-TARS-desktop)；[SDK](https://github.com/bytedance/UI-TARS-desktop/blob/main/packages/ui-tars/sdk/README.md) | Desktop 标明支持 Windows；SDK 将模型与 Operator 分离，Operator 实现截图和动作。动作含 hover/drag，参数包含物理分辨率、DPR 与坐标缩放；支持取消信号和循环上限。 | 参考接口组织，明确截图坐标到客户区、物理屏幕坐标的转换；增加受控悬停时参考其动作表达。SDK 需要模型服务配置，不能假定当前 Codex 对话可直接成为其 API；整套引入需要额外集成评估。 |
| [Microsoft UFO 的 AppAgent 状态](https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/app_agent/state.md) | 将继续、重新观察、询问、确认、结束及失败区分；可恢复失败归档后返回 HostAgent，让上层重试或选择替代方案。 | 参考失败类别及交接记录，落实脚本失败→助手判断→必要手操→验证→交回。既有 runner 已有交接保护，补缺口即可；教学提问与操作恢复分开，按用户授权由助手验收出战。 |
| [Microsoft OmniParser](https://github.com/microsoft/OmniParser) | 截图转结构化界面元素；另有 OmniTool 控制 Windows 11 虚拟机。 | 适合评估陌生图标、元素框定位；它本身不是完整操作或游戏决策器。虚拟机演示不能证明宿主游戏兼容性。需要模型权重；仓库及不同权重许可证有区别，采用时按具体版本核对。 |

## 本项目已经有的能力与真正缺口

当前源码已有窗口/进程绑定、客户区截图、动作请求、`snapshot_id` 和 `resume_epoch`、暂停/接管检查及单次出战审批。保留这些已有能力；外部识别结果经过既有输入 broker 提交。

本局反馈及待办显示，需要改进的是：缺少真正的悬停读取、动态页面与遮挡定位、操作后的效果判断、恢复入口衔接、脚本不能处理时的结构化回传。`currency_wars_runner.py` 的刷新判断还依赖 `semantic.strategy_phase`；缺失攻略阶段属于游戏语义问题，换点击库不会自动解决。

Airtest Windows 后端源码也值得作为限制证据：[win.py](https://github.com/AirtestProject/Airtest/blob/master/airtest/core/win/win.py) 已有窗口坐标换算、`mouse_move` 和前台切换，但连接时可能忽略前台切换异常。接入本项目时必须回读真实前台与结果，不能照搬“调用未报错即可继续”的假设。

## 最小试验与验收

1. 固定候选版本，在所属 OS 临时 run 中复用本局已有截图，先做无输入定位比较：1080p/4K、正常备战/商店布局、详情面板遮挡、相似角色和动态背景。报告准确定位、错误候选、未找到及耗时；优先衡量误点风险，不只看找到多少元素。
2. 先验证 Airtest 图像模块是否比现有识别更可靠；收益明确才接入。UIA 对控制台采用控件状态等待；游戏内部先检查是否暴露可用元素。
3. 经同一 broker 做有界实机操作：打开并关闭一个已知详情、悬停一个物品、读回提示；再验证一个常规上场动作。每次重新确认页面、目标可见性和窗口绑定，操作后检查预期效果。禁止出现两个鼠标控制器。
4. 使用本局明确失败场景验收：控制台恢复后游戏真实在前台；4K 命中正确元素；详情面板不遮挡目标；换人后人数/身份/位置正确；买牌或装备动作的库存与金币变化正确；失败回传保留原因及前后证据，不盲目重发。
5. 比较成功率、耗时和人工接管次数，形成一个紧凑验收记录。无收益则保留现有实现；有收益则先替换对应基础能力，再归并游戏流程待办。

最初调研只新增研究文档并关联待办。下文分别记录两局结束后的实际采用和验收，不把候选框架的能力当成本项目已实现的能力。

## 本次赛后试验（2026-10-06）

已实际下载并调用 Airtest 官方纯图像模块，固定提交 `d729c631d2032521be1e2168a249a2a89d79af2a`。在本局3张真实结算帧及1个移除按钮区域的负样本上，对比现有OpenCV NCC定位“下一页”：两个后端3/3命中正确位置、1/1拒绝无按钮场景；置信度约0.998–1.000。具体耗时、官方源码文件哈希和输入路径见 [AIRTEST_COMPARISON.json](AIRTEST_COMPARISON.json)。下载仅发生在所属OS临时run，结束已实际删除，未装第二输入控制器。

这个小样本没有证明Airtest更快、更可靠：其灰度模板核心和现有路径都使用 `cv2.TM_CCOEFF_NORMED`。本次保留现有CV依赖，复用Airtest的有界匹配、等待和失败后重新观察方式；针对真正的错误增加目标语义校验与准备流程阶段，而不是替换整个控制器。UFO的失败分类/上层回传、UI-TARS的Operator分离仍作为接口实践依据，不声称已接入它们的完整运行时。遮挡、相似角色头像、原生4K等复杂场景尚需实际验收；此处3正1负仅验证保留帧上的按钮匹配。

## 常规局赛后：通用问题到最小改动

阅读触发：处理常规局脚本卡点，或下一次整局比较复发率时读本节及 [COACHING_BACKLOG.md](COACHING_BACKLOG.md) 当前表。实际结果集中在 [NORMAL_MATCH_VERIFICATION.json](NORMAL_MATCH_VERIFICATION.json)。

| 通用问题 | 主源实践 | 采用及证据边界 |
| --- | --- | --- |
| 控件没有文字／视觉模板受背景影响 | [Airtest图像API](https://github.com/AirtestProject/Airtest/blob/master/airtest/core/api.py) 的图像定位和有界等待；现有OpenCV NCC | 第二创业指南增加固定图标身份与局部唯一模板，保持双原始PNG、页面锚点、遮挡及单次输入检查；商店小书采用内部图形，去除头像背景干扰。保留帧验证改善，不声称换框架后完整游戏兼容 |
| 页面出现但步骤尚未完成 | [pywinauto控件状态等待](https://pywinauto.readthedocs.io/en/latest/wait_long_operations.html) | 本项目用明确业务进展界定闲置超时；完成复核续下一步有限预算，截图、丢读或低置信变化不续时，原硬截止不延长。没有额外安装输入后端 |
| 上层接管后如何可靠恢复 | [UFO AppAgent状态](https://github.com/microsoft/UFO/blob/main/documents/docs/ufo2/app_agent/state.md) 的REOBSERVE／PENDING／ERROR | 维持当前唯一broker与epoch/CAS，补固定导航后减少不必要接管。完整手操结果桥接仍未实现：下一步须把实际结果绑定新epoch并重新核验，不能复制旧准备完成标记 |
| 成功统计混入上层机械代打 | [UFO会话追踪](https://github.com/microsoft/UFO/blob/main/documents/docs/infrastructure/modules/session.md) | 利用已有broker请求台账统计实际completed输入，区分worker内／外；坏回执、缺台账和pending保持partial；退出前刷新并保留紧凑统计。外部调用者身份不可从台账推定 |
| Windows文件替换暂时失败 | [Microsoft CreateFile共享标志](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilea) | 先核精确句柄共享、路径权限及发生阶段，再决定是否改发布／读取兼容。已有write_json有限重试，不因一次PermissionError就新增无限重试或修改ACL；本次事件具体根因仍C类待查 |

上述是对现有能力的补缺。整套Airtest、UFO、UI-TARS或OmniParser均未作为新控制器接入；不引入它们的模型服务、第二鼠标写入者或额外常驻进程。此次没有为验证重开第二局；完整常规局的修复后复发率、人工机械动作减少幅度仍需后续实机统计。
