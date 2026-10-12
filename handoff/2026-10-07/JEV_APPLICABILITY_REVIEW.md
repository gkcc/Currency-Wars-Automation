# 决策模型适用性审查

审查日期：2026-10-07。范围为官方资料与作者源码核查；未调用或下载模型，未取得新游戏观察、执行游戏输入或改动生产代码。

**结论：Jev 可候选替代部分已有可靠文本状态、仍需 ROOT 做的窄语义判断；不能替代读图，也没有证据表明接入后业务净成本下降。** 适用例子是从程序已列出的合法选项中依据明确规则选择，或对当前文本作有限分类。已能由代码确定的 D/F、预算、停止条件和单候选动作无需增加模型调用。复杂策略联动和出战验收不能默认拆成互不相关的小判断。

Jev 接受普通字符串或 JSON，不要求输入全部预先结构化，但当前只支持文本，不支持图片、音频或视频。因此它不能直接消除主 OCR，不能补出未知角色实名、阵容或奖励控件位置；这些仍须外部感知提供。当前版本主要以英文训练，官方说明中文等 CJK 内容准确率较低，本游戏适用性尚未验证。

输出是 Choice、Score 或 Noul：分别用于有限选项、等级评分和“是”的概率。Choice／Score 的 confidence 来自返回的概率分布；校准不保证单次正确。模型概率不能替代当前帧来源、原始 completed、实际费用或阶段完成证据。官方同时承认精确算术、计数、多层推理和选项顺序敏感等限制；费用计算、数量、预算、既定业务顺序及停止守卫继续由代码负责。

公开官方路径是带 API key 的远端 API；本次未找到官方权重下载、离线推理程序、参数量或本机硬件要求，不能将其称为已可离线运行的“小模型”。当前严格纯离线工作不接入该服务。本轮没有本机模型实测，也没有已验证的 API key 可用性记录；这不等于用户没有 key。

发布文的 70–500 ms 是厂商报告的端到端请求响应范围，并说明公开评测通常从美国西海岸笔记本调用同区域服务。它不是本机 P95，不包含我们的读图、状态构造、执行与后验。官方 Doom 演示也使用结构化文本状态，不能作为视觉能力证据。

已核作者 Pokémon 仓库的固定版本直接读取模拟器 RAM：金币、队伍、事件标志及屏幕 tilemap 文本均由程序解码；寻路和菜单操作由执行层承担，Jev 只选提供的候选。源码在只有一个候选时直接返回；决策日志计时包含节流、重试，缓存命中也会记录。它展示的是程序与模型分工，不能移植其感知前提或决策时间来承诺本游戏收益。

同一 Worker 到达同一可核业务终点的 [本轮成本审计](observation-evidence-reuse/RESULTS.md) 已优先处理可核的重复主 OCR。Jev 的后续比较仍须核算可避免的 ROOT 判定时间，并扣除新增状态整理、API、回退和恢复。若主要损耗是重复 OCR、阵容未识别或主管绕过原生入口，Jev 不直接解决这些原因。是否采用仍需本游戏证据，不因 API 单价低或演示快而预先认定 ROI。

## 来源与接口

| 核查项 | 可核来源与口径 |
| --- | --- |
| 文本输入 | [官方 State](https://docs.typesafe.ai/concepts/state)：字符串／JSON；不支持图片、音频、视频。 |
| 接口与输出 | [官方 API](https://docs.typesafe.ai/api)：`POST https://api.typesafe.ai/v1/systemone`；Choice 返回选项与分布，Score 返回等级加权值，Noul 返回概率且无独立 confidence。 |
| 版本与价格 | [官方 Models](https://docs.typesafe.ai/models)：本次页面为 `jev-1.13.0`；每百万输入 token $0.042，输出免费；别把移动别名当固定版本。 |
| 概率与限制 | [System One](https://docs.typesafe.ai/concepts/system-one)、[Confidence](https://docs.typesafe.ai/confidence)、[1.13 限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)：最后一页标注 2026-10-02 更新。 |
| 延迟、Doom | [2026-09-15 官方发布](https://typesafe.ai/blog/introducing-system-one-models-and-jev)：70–500 ms、地域与短输入限制；Doom 为文本状态。 |
| 公开运行路径 | [官方 Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python)：API 客户端；SDK 开源不等于权重开放。 |
| Pokémon 状态来源 | 作者仓库 SHA `20cdcb846ff995383fe3da1d48ad5eac6d8d1abd`：[state.ts](https://github.com/christianmat/jev-pokemon/blob/20cdcb846ff995383fe3da1d48ad5eac6d8d1abd/src/game/state.ts#L32)，含 `emu.mem`、`wPlayerMoney`、`wEventFlags`、`wTileMap`。 |
| Pokémon 执行边界 | 同 SHA：[client.ts](https://github.com/christianmat/jev-pokemon/blob/20cdcb846ff995383fe3da1d48ad5eac6d8d1abd/src/jev/client.ts#L70) 的计时、缓存、单候选直返；[gateway.ts](https://github.com/christianmat/jev-pokemon/blob/20cdcb846ff995383fe3da1d48ad5eac6d8d1abd/src/jev/gateway.ts#L4) 通过 Vercel AI Gateway 调远端 Jev。 |
