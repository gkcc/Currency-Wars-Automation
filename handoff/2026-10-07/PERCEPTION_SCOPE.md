# 领奖与经济按需读取：契约已修，总提速尚未成立

## 交付结论

本候选基于完整 PR12 `5e46351372ff26980a92a9806909c732607f50ea`，同时整合 main `d454c0626c1bd1014b15565b475d1b1e8be467b1` 的 Windows 验收和交接证据。没有从 main 的旧生产核心另起一套修复，也没有丢弃 PR12 的上游候选。

本批修的是实际消费者与读取范围不一致：默认完整读取保留；原生领奖循环用 `rewards`，D/F 后图用 `economy`；两者省略阵容、库存，领奖还省略商店卡片和玩家 HUD 补读。所有省略均明确标为 `not_read`。ROOT 全场复核、完整策略、购牌和转入布阵前，仍从当前同请求不可变 PNG 强制完整读取。

**真实同图测量没有证明总读取提速。** warm 十图 full 为 12.3426 秒、scoped 为 13.1078 秒，后者本次多 0.7653 秒；第一遍含一次冷起则为 12.2149 → 11.9990 秒，方向不同。减少无关调用已经实证，用户关心的总速度尚未解决。没有增加循环挑选更好数字，也没有把同 PNG 缓存命中算作 OCR 提速。

完整逐图和嵌套计时见 [实测报告](perception-scope/RESULTS.md)、[PR12 热点](perception-scope/BASELINE_HOTSPOTS.md)、[full 原始区间](perception-scope/candidate-full.json)、[scoped 原始区间](perception-scope/candidate-scoped.json)。聚焦检查的原始记录、执行时源码摘要与最后源码摘要分开保存在 [验收记录](PERCEPTION_SCOPE_ACCEPTANCE.json)。

## 实际调用方与完整读取边界

| 入口/消费者 | 本批读取方式 | 目的与后续边界 |
| --- | --- | --- |
| `Perception.read(path)`；人工原帧校验；原 replay 默认入口 | `full` | 未显式选择范围的调用保持原行为 |
| Worker 初始观察、被动观察、ROOT 回复执行前的观察 | `full` | 当前完整帧供策略、布阵、装备和出战前复核 |
| 原生领奖每步重新定位、单步命令后图 | `rewards` | 页面、节点、金币、当前奖励目标/提示；仍逐球后验 |
| 原生 D/F 命令后图 | `economy` | 当前商店、玩家 HUD 与现有经济 ROI 实读；不要求全阵容 |
| 命令后图不可用而仅补一次新观察 | 沿原命令 scope | 不重发原输入；新只读请求继续原帧、收据边界 |
| 经济下一步为购牌，或经济结束准备进入布阵 | 当前原 `frame_result` 强制 `full`，重算 policy | 购牌共同入口也执行完整读取；不拿 D/F 窄结果供阵容消费者 |
| `preparation_policy` | 先保证 `full` | 完整策略与同场联动不消费窄状态 |
| `ask` 的所有 ROOT 回传 | 去重之前保证 `full` | history/request 只保存完整读取语义和独立副本 |

`ensure_full_observation` 不发布新的 observe。它核对当前 `last_observation`、`frame_result` 的请求 ID、帧 ID、PNG SHA，再经原 `entry.observation_frame` 校验文件并强制读取相同 PNG。缺当前帧、旧请求或身份不一致均拒绝，不能把旧图升级成当前事实。完整解析仍可能 unknown，**完整读取不等于完整识别**。

原 `validate_plan` 已要求经济动作和库存改变分别是独立单动作计划。因此“ROOT 同一多动作计划先 D/F 再 drag”本就不能经正常入口执行；审查时曾提出的这条假设已撤回，没有为不可达路径扩补丁或构造绕过验证的测试。ROOT 新计划起始观察本来就是 full。

## 范围契约

窄 scope 只在已经按原分类器识别出的 `preparation/shop` 页面生效。其他页面实际回到 `full`，保留 `requested_scope` 和 `fallback_reason` 供核对；未知页仍按原未知页处理，不硬凑可操作事实。

| 读取内容 | full | rewards | economy |
| --- | --- | --- | --- |
| 完整主 OCR、原页面/模态分类 | 读取 | 读取 | 读取 |
| 页面、节点、金币和奖励目标/选择提示 | 按原规则 | 按原规则 | 按原规则 |
| 展开商店的 ShopReader | 读取 | 明确未读 | 读取 |
| 玩家等级、经验、人口/HP 的额外派生或补读 | 按原规则 | 明确未读；level/deployed 为 None | 按原规则 |
| StateReader 阵容/板凳/库存 | 按原规则 | 明确未读 | 明确未读 |

主 OCR 输入仍是原完整预览缩放至 1280×720。`read_contract.page_ocr='full_frame'` 如实反映它没有局部化。窄结果的 team/inventory/state_read 含 `status='not_read'`、原因、scope、snapshot，`checked/fully_read` 为 false；空的 units/items 只配合这个未读状态存在，不能读成场上/库存为空。领奖的 player_hud 同样明确未读。

额外经济数字仍由现有 `economy.observe_fields` 按已核字段绑定读取。`economic_policy` 直接求攻略阶段许可，省掉每笔 D/F 对完整 `preparation_decision` 的阵容和购牌候选排名；当前攻略 proof、模式、等级和 `no_reroll_phases` 禁令均保留。它不新增刷新许可或更改标准/超频的预算规则。

## 缓存和来源

- Perception 缓存键为 `(PNG SHA256, requested scope, READ_CONTRACT_VERSION)`。同 PNG 的窄读不能命中完整结果契约；契约版本改变也不命中旧值。
- 缓存保存与每次返回均为独立深拷贝。Worker 补当前 request/frame/captured_at 不会修改旧请求或缓存中的快照。
- `read_timing.cache_hit` 明确记录本次是否命中；命中返回的 `elapsed_ms` 是本次实际缓存读取时间，不再反复累计第一次 OCR 耗时。
- 未读占位不写入 `strategy_reads`，窄状态不能从中回填 team/gear/bonds/xp。team 复用还须是同 PNG 的当前来源，不能以旧 checked 阵容盖掉新 full 结果的 unknown。攻略正文仍沿原 proof、模式、epoch 与有效期规则复用。
- 经济读数缓存也加入读取 scope/契约；ROOT history 仍可用 SHA 索引，因为 `ask` 已先保证 full。`verified_source` 拒绝窄语义来源。
- 新 `perception_read` 日志留存 scope、缓存标志与本次实际读取耗时。既有 `statistics.ocr_ms` 是历史名称，包含 Perception 整体成本；纯 OCR、规则和读取器的分段以本批方法计时为准。

## 同图测量与热点判断

按 q00/q01/q02、r00…r06 固定顺序，每种模式一个独立进程；每张 `force=True` 真实 OCR，随后单列同图缓存。每模式两遍，不并行跑另一组 OCR。首帧构造引擎，模块导入另列；其余第一遍图片不能称为逐张冷起。

| warm 十图互斥区间，秒 | full | scoped |
| --- | ---: | ---: |
| 主 OCR | 10.5398 | 11.8607 |
| 额外数字 OCR | 0.1546 | 0.1092 |
| ShopReader 内 OCR | 0 | 0 |
| ShopReader 其余处理 | 0.3524 | 0.3226 |
| StateReader | 0.4273 | 0 |
| 奖励规则 | 0.0325 | 0.0448 |
| 其他解码、字段、哈希和读取处理 | 0.8356 | 0.7703 |
| Perception 方法总区间 | 12.3422 | 13.1076 |

方法包含区间先扣除直接子区间，以上可相加；外层总读取的少量计时包装成本另外保留。ShopReader 总时间、其内部 OCR、主 OCR 的检测/识别成员不能再重复相加。

这两组只比较 Perception 读图函数，未包含 Worker 从窄读进入 ROOT/购牌/布阵前的额外 full 重读。该升级会再次运行真实 OCR，其成本不能从本表中省略后宣称闭环净收益；本批没有测得完整 Worker 业务链墙钟提速。

StateReader 调用每遍 10→0，ShopReader 8→7，玩家 HUD 和数字补读分别 10→7；主 OCR 始终 10 次。独立补测 q01/q00 的主 OCR 中，文字识别约占 81.6%，检测约占 18.0%。这支持继续审查感知架构，但不能据此宣称任意区域可安全跳过。

公开检出缺 `tools/shop_reader_resources/SOURCES.json` 等本机私有素材，八张展开商店的 full 读取均实际 error/unknown，内部商店 OCR 没有发生。ROOT 已有 57 个原资源并证明七商店 `shop_ok=true`；本报告不推断本机也缺资源，不用合成空店代替实图。完整资源下跳 StateReader/ShopReader 的实际成本，需 ROOT 用同脚本补一次对照。

共同实读结果保持：页面/节点/金币/奖励目标一致；七刷新图玩家 8、金币 70→58；q01 仍是两个限定范围内蓝球，q02 仍需选择/推荐复核；`all_rewards_cleared` 不被补为 true。两次历史领奖之间缺中间图/独立回执的边界不变。

## 本批聚焦验证

1. 新 scope 文件的 7 个方法通过，使用声明式 OCR 行与惰性 Entry fixture，核默认/full、显式未读、scope/版本/副本缓存隔离、同请求 full 升级、D/F/领奖后图和攻略许可。
2. 随后只补验受影响的经济方法中购牌入口子情形，以及一个原等级/攻略 selector；其中经济方法与前一组重叠，不累加覆盖。购牌子情形验证先 full 再由原预算/目标规则拒绝，并非虚构购牌成功。
3. 只重跑三个受影响正常路径 selector：三 F 本地经济完成、六 D 一次预算回传、收店逐球后最后 ROOT 复核，全部通过。原 50/15/58 和 PR12 四组没有整体扩跑。

基础 fixture 的 read 签名只补 `force=False, *, scope='full'`。原等级 selector 的 team fixture 加当前 snapshot 来源，原断言未改。执行时未记录的摘要保持 unknown；追加执行的最后只读摘要单列，没有回填成执行时证明。各组 stdout、来源和生产摘要详见验收 JSON。

用户关心的是效果和总时间，以上测试数量不是速度证明。真实 OCR 的准确性对比来自十张完整 PNG 的方法测量和既存 PR12 报告核对，不来自这些合成 fixture。

## 借鉴的具体原则与适用边界

- [SikuliX Region](https://sikulix-2014.readthedocs.io/en/latest/region.html) 明确建议把查找限制在相关区域。这里用于按消费者减少不需要的读取；现有页面分类的全域提示依赖没有被删除。
- [Playwright Locators](https://playwright.dev/docs/locators) 每次动作重新定位当前元素。这里保留逐步当前帧重定位与后图验证；浏览器 DOM 的保证不能直接套到截图后的异步游戏弹窗。
- [Microsoft UIA 缓存](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients) 让客户端明确选择属性、范围并更新快照，新快照不修改旧引用。这里落实为显式 scope、未读字段、独立快照及完整读取消费者边界，并未声称游戏已有 UIA 树。

以上官方资料于本批重新读取。RapidOCR 的实际已安装源码也已核：有检测/识别拆分，没有现成 ROI 筛选参数；跳行还会改变识别批次。现分类器对全屏文字有优先级，裁掉弹层文字后仍可能判为 preparation，不能靠 unknown 回退救回。因此本批不以局部主 OCR 或修改阈值作为已验提速。

## ROOT 最小复核与仍缺证据

使用现有 Windows Python 与原 57 资源，不重装资源、不启动生产入口。在仓库根目录，令 tools 在 PYTHONPATH 后，仅选新 scope 模块、原等级 selector及上述三个正常路径；精确 selectors 在验收 JSON 中。

随后顺序执行一次完整读取和一次阶段读取，每个脚本自身已有两遍真实读取与独立缓存计时：

```powershell
$env:ORT_DISABLE_TELEMETRY='1'
python -B -X utf8 tools/replay_currency_wars_perception_scope.py --code-root . --mode full --output offline-results/perception-full.json
python -B -X utf8 tools/replay_currency_wars_perception_scope.py --code-root . --mode scoped --compare-to offline-results/perception-full.json --output offline-results/perception-scoped.json
```

只需回传两份 JSON 和所检出 SHA，不要求公开私人素材、完整知识缓存或新采游戏图。报告中模型/玩家等待、完整备战、整局提速仍 unknown；历史工具外 87.58 分钟不能据本次秒级读图归因成 OCR/思考。1920 规范化支持没有证明当前 4K 输入坐标或实机布局。

**已修：**阶段依赖与读取成本对齐、未读/未知区别、缓存和快照归属、全场消费者升级。**离线已验：**必要阶段事实保留、无关调用确实省略、原正常路径保持。**仍缺：**完整私有资源下稳定总收益、其它页面/遮挡的真实覆盖，以及完整阶段和整局的端到端改善。候选仍是离线草稿，未安装、未部署。

用户追加的“OCR 是否值得作为长期主架构”审查单独处理；不会以本补丁的测试通过或局部调用减少预设其答案。
