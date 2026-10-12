# 同请求完整升级：复用原始文字证据

## 结论与基线

本轮选择修复 `ensure_full_observation` 的重复主 OCR。先按 Worker 到达相同消费者终点的完整本地窗口测量，再改生产代码；不是继续只比较 `Perception.read`。两个真实 PNG 窗口都能删去一次同请求主 OCR，完整语义、回传原因和 pending 终点不变。本机单遍墙钟改善见 [分段结果](observation-evidence-reuse/RESULTS.md)，不宣称稳定倍率、Windows 收益或完整备战提速。

完整上游是 PR13 `257f84d8b036e6bcb0ef29ae51c722bcaf315e8d`，同时整合 main `d4d36e34b8d2912abbb2af57bc137b2aedc2d1a1` 的 Windows 验收和 full/scoped 证据。发布前又纳入 main `19e997da409e4484bb93d3135b7f3927715a7517` 的 [旧图位置／星符人工标签](ROOT_RETAINED_CARD_LABELS.json)，仅作后续能力评估资料，未参与本轮测量。独立分支 `fix/observation-evidence-reuse-20261007`，草稿 PR 以 PR13 分支为目标；候选没有安装或合入生产。

ROOT 的 57 个原资源、同顺序十图 warm 14.2661→13.2202 秒仍是独立 Windows 证据。本轮不重复这组函数实验，不把其 1.0458 秒与 Linux 的升级窗口相加或拼成跨机倍率。

## 最小生产修改

只改 [Perception](../../tools/currency_wars_perception.py) 和 [Worker](../../tools/currency_wars_runner.py) 两个生产文件。

`Perception.read(..., scope='full', reuse_primary=True)` 表示：验证和解码这次 PNG，允许复用同 SHA、同 OCR 契约、同引擎实例的一份不可变主 OCR 原始输出，随后重新执行完整语义读取。普通读取仍默认 full；显式复用只开放给 full。

原始证据槽只存 `(box, text, confidence)` 的不可变 tuple，位于别名归一、页面分类和 ROI 补读之前。它不存角色、页面、阶段、请求身份或任何完成结论。完整结果缓存仍按 scope／读取契约隔离；原始证据另绑定主 OCR 契约版本、引擎强引用及实际可变的 `use_det/use_rec/text_score/box_thresh/unclip_ratio`。主输入保持 1920×1080 RGB、原 1280×720 缩放与 `use_cls=False`；改变这条链须升级 OCR 契约版本。该原始缓存限于同一 Perception 实例的最后一份证据。

显式复用绕过完整结果缓存，重新派生别名、页面／模态、HUD、商店、阵容、库存与奖励状态。`not_read` 不能变成已确认；完整读取仍可能得到 unknown。`force=True` 阻止两层缓存，继续真正的主 OCR。三种路径分别记录 `cache_hit`、`primary_ocr_reused` 和 `primary_ocr_executed`，每次耗时重新计算；没有用缓存原耗时冒充本次读取。

Worker 只在原 `ensure_full_observation` 的当前请求、frame、SHA 核对之后启用复用，仍由唯一 Entry 验证原 PNG；`read_frame` 再核读取 SHA 并绑定本次回执身份。Perception 的摘要和解码改为使用同一次 `read_bytes()` 的内容。新 PNG、引擎或 OCR 契约不匹配就执行真读；不把旧请求字段移给新请求。购牌、策略联动、布阵装备和 ROOT 全场验收仍取得当前完整语义。

金额、pending、epoch/CAS、停止守卫、`.7` 等待参数和业务顺序均未改动。观察之后异步弹窗的时间窗仍未闭合，本补丁不能作为该问题已经解决的证据。

## 为什么先做这一支

| 消费者／缺口 | 本轮实际证据 | 对优先级的含义 |
| --- | --- | --- |
| q02 奖励选择 | 完整升级前后均是 `reward_selection`，原生原因是奖励相关推荐提示需要策略联动；正常 ROOT 1，异常 0 | 可以减少升级读图成本，不能把这次必要选择删掉或算成阵容失败 |
| q00→q01 收店后回传 | 原 completed 为 `click(1622,974), wait(.3)`，当前请求为 `click(1623,982), wait(.7)`；保持 pending／unknown，异常 ROOT 1 | 同时有坐标和等待差异；不是“没有读出阵容”导致这次回传，也不能改回执制造成功 |
| 六 D／三 F 后布阵交接 | 协议同终点都是预算请求＋布阵请求，非异常 2、异常 0；经济完成，布阵未验 | 末次原分类仍为 `unclassified`、步骤 `lineup_equipment`，不伪称全部原因标签已补齐；即使角色可读仍需该场策略和最终出战验收 |
| q01/q02 阵容实名／星级 | [ROOT 原资源记录](ROOT_ROSTER_RESOURCE_AUDIT.json) 是人口 8/8、占用槽 5、实名 0、星级 0、checked/fully_read false | 已有能力缺口，但未从历史中分离出“仅该缺口导致的额外 ROOT 请求数／时间”，因此不能承诺补阵容会省几轮 |

重复主 OCR 已在两个固定消费者窗口中观察到，可以直接删去一份重复工作。发布前 ROOT 已新增 q01/q02 的八张可见卡牌位置与星符标签（前台 3/3/2/2、后台 2/2/1/1），实名仍未标，边界是近似卡面而非可直接使用的点击点／后端槽号。本轮未拿这些标签回填模型输出。后续可先对照占用和星符缺口，再用实际消费者轨迹评估额外接管；不能先把 q01/q02 填满，再用完整度宣称提速。

## 通用方法落点

Microsoft UI Automation 的 [缓存说明](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients) 区分显式请求的属性／范围与当前访问。这里借鉴的是“明确证据范围、复用不可变快照、派生消费者所需状态”；本游戏没有被证明具有可用的 UIA 语义树，也没有直接使用 UIA 缓存。

[SikuliX Region](https://sikulix-2014.readthedocs.io/en/latest/region.html) 的局部定位和 [Playwright Locator](https://playwright.dev/docs/locators) 的动作时重新定位原则继续适用于当前目标，不意味着可以永久复用旧坐标。本轮没有缩小主 OCR 的页面观察区域；先消除同一不可变图的重复推理，保留全图模态判断。更换感知路线的完整审查和 [Jev 核查](JEV_APPLICABILITY_REVIEW.md) 分开保存；Jev 当前是文本决策服务，未作为视觉或离线推理能力接入。

## 验收和复核入口

- [聚焦验收](observation-evidence-reuse/FOCUSED_ACCEPTANCE.json)：新原始证据用例 3＋直接受影响的原缓存／Worker 方法 4，共 7，0 失败／错误／跳过。断言和 OCR 输出为显式 fixture，不表示模型识别准确率。
- [同终点原始记录与验收汇总](observation-evidence-reuse/NET_COST_ACCEPTANCE.json)：基线／候选真实两窗口和协议两窗口，均冷、暖各一轮。各个独立窗口不拼成完整阶段，也不与历史测试数累计。
- [窗口、冷暖和发布边界](observation-evidence-reuse/WORKER_COST_DESIGN.md)：实际写请求和证据文件，输入传输为惰性替身；生产 `publish/log` 状态文件／journal、模型和游戏等待未计时。
- [Windows 最小复核命令及剩余证据](observation-evidence-reuse/RESULTS.md)：使用原资源、同脚本、独立进程和相同输入顺序。没有要求补导出完整私有素材。

测量文件是在未提交工作树上执行，原始 `source_revision` 仍为 PR13 HEAD；**运行前后生产源码 SHA256 与脚本 SHA256 是本轮执行绑定**，不得将该字段误读成候选仍用了 PR13 生产文件。发布 SHA 及两父链由草稿 PR 和最终回读给出。

已修：同请求窄读→完整语义升级的重复主 OCR。已离线验：上述消费者终点一致、真实主 OCR 次数减少、force 和 scope／来源边界、协议费用和回传不变。仍缺：Windows 原资源同终点净成本、真实完整六 D 的初始数值 ROI／预算绑定、两球间逐次后图、剩余实名标签与阵容缺口的额外回传因果证据、完整备战／整局与模型实测。本轮没有游戏输入、当前游戏新图或候选部署。
