# 代码定位和验证摘要

基准提交：`bd274cce5755094da3a3073427f4c3cfc365bfc0`。本次发布收录此前已经完成的监督恢复、准备状态、任务规划、补给提取和 GUI 源码保护修改；最新待办中的竞态/性能问题仍未修复。

| 文件 | 已有职责/变更 | 优先查看 |
| --- | --- | --- |
| tools/currency_wars_runner.py | 单窗口执行器、阶段、战略请求、CAS/epoch、手操结果与出战审查、外部输入统计、有限续时 | observe/read_frame、主循环 passive capture、manual phase、explicit_resume、tick_decision、reward/spending gate |
| tools/currency_wars_perception.py | 页识别、攻略、补给五卡与原始 OCR 证据 | 模式/标题/正文列、角色+多装备组合、瞬态结果页 |
| tools/currency_wars_state_reader.py | 角色位置/星级、HUD、库存与未知回传 | 独立身份、人口、库存容量/溢出；局部证据阈值 |
| tools/currency_wars_visual_guards.py | 控件语义、局部图像和页面/遮挡守卫 | 图标与文字控件契约、动态背景和目标稳定性 |
| tools/currency_wars_progression.py | 任务机会、奖励目标与准备期意图 | 必须从实读的当前缺口生成动作并回读进度 |
| tools/currency_wars_coaching.py | 带教上下文、已知角色/策略和失败上下文 | 结构化回传、版本化知识缓存 |
| tools/currency_wars_shop_reader.py | 5槽实名、价格、推荐缺口/空槽 | 空槽不等于失败；购后合星预告不代表基础张数 |
| tools/currency_wars_bridge_task.py | 已安装输入组件任务定义 | 所属根目录、窗口/进程、请求/暂停身份、1080p/4K映射 |
| tools/currency_wars_broker_entry.py / control.py | 请求台账、同一输入所有者、结果/暂停/恢复 | 每请求帧产物，不能并行重发已发布输入 |
| gui/src/main.rs / gui/ui/app.js | 核心源码守卫清单 | 包含新增模块，真实就绪记录不能伪造或复用异机身份 |

历史离线记录（2026-10-06 10:38:11 UTC）：runtime compatibility35、runner core26、goal planning8、state reader/HUD5、Rust现有隔离检查9，合计83；独立审查PASS；GUI release构建PASS；当时game_inputs=0、new_code_live_verified=false。

本次公开快照发布前校验历史验收绑定的13文件哈希全部未变；重新运行 `python -B -X utf8 -m unittest discover -s tools -p test_public_packaging.py`，4/4通过；`git diff --check`通过。没有宣称重新跑完83项，更没有将其当作整局实机通过。

公开仓库不提供个人原图与截图裁剪模板；部分图像回归依赖本机已核验资源。不能凭远端缺少这些样本就把未知场景改判已通过。按具体问题请求最小可分享样本，或使用不含账号数据的合成/语义样本保护稳定契约。
