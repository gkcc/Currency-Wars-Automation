# 推荐徽标共同读取与买前资格

## 交付判断

本批闭合一个局部契约：**同一种橙金书形徽标的原生读取，与跨动画单笔购牌的稳定性资格，使用同一外观组合和当前来源。** 同一公开校准素材、同一三张原 PNG、同一槽位终点下，完整 PR16 的 f01 第 5 槽为 `recommended=null`，候选为 `true`；另外 14 个真实无书形槽保持 `false`。这是局部读数修复，未证明真实购牌成功、主管往返减少或整局提速。

需要纠正因果范围：`purchase_slot` 本来允许徽标 unknown，只要求当前实名、实价及原来源等条件；`Worker.economy_observation` 沿该函数组装购买信息，**正常 native 经济循环没有因本批增加推荐字段依赖**。旧缺口直接造成 `ShopReader` 槽位 unknown / 整店 partial，并拒绝 `stable_purchase_slot` 的跨动画资格；`record_node_progress` 的完整商店变化证据也受 partial 影响。这里没有证据把每次 ROOT 往返都归因于徽标。

代码基线为完整 PR16 `5938648de4cfd4b5cd3cd723898abb3c2f806a48`；证据 main 为 `4cf23830e7832ee68b4ef40ba688b006ee3064fc`，发布前再次实际 fetch，仍为该 SHA。本分支保留这两个父来源；最终发布 SHA 由草稿 PR 和最终交接给出。

## 最小生产差异

只改两个生产文件：

- `tools/currency_wars_shop_reader.py`：集中外观规则、把实际加载的模板/manifest 身份绑定到徽标证据，并让读取与稳定性复核共同调用 `_read_recommended`。共同判定一次返回结果及外观分支，避免再次复制条件。
- `tools/currency_wars_visual_guards.py`：一处调用增加 `images=images`，传入 `_frames` 已核过两份 PNG 字节摘要、格式及尺寸的 RGB 数组。

当前固定几何仍为 1920×1080，徽标宽 ROI 79×77，受限搜索 37×38，模板 47×51、内部书形 26×27。保持原尺度，没有扩大到全牌或 HUD。

| 条件 | 既有黄色 | 新增橙金 |
|---|---:|---:|
| OpenCV 8 位 H | 20–39 | 16 或 17 |
| S / V | 均严格大于 130 | 相同 |
| 内部书形分数 | ≥ 0.90 | ≥ 0.97 |
| 排除最佳峰邻近 2 像素后的其他峰 | < 0.90 | < 0.97 |
| 宽 ROI 相应颜色比例 | ≥ 0.06 | ≥ 0.06 |
| 同一匹配内区相应颜色比例 | ≥ 0.06 | ≥ 0.06 |
| 完整徽标模板分数及位置 | 无新增要求 | ≥ 0.80；与内部匹配位置按模板偏移对应，每轴误差 ≤ 2 像素 |

黄色色域和形状分数没有放宽，并新增同位置颜色、非邻近高分候选及来源约束。橙金是有来源的窄分支；没有把所有暖色、灰度高分或人工标签直接当成推荐。旧不支持几何的低阈值退路不取得新资格。只有形状低于 0.50、两类宽色比例均低于 0.04、并通过原白屏/黑屏限制，才表达明确无徽标；其余歧义继续 `null`。

原生证据记录当前 snapshot、卡框、匹配区域、ROI RGB 摘要、实际模板文件与灰度摘要、manifest 摘要及原始分数。缺模板、模板几何或有效纹理不支持、来源不足均 unknown。稳定性复核只重新读当前徽标和 manifest，双读防加载期间变化；不执行 `_load` 全素材加载或 OCR。它从两张已验 PNG 逐帧重派生，并与各自原生证据完整比对。

`stable_purchase_slot(images=...)` 裸函数不验证数组对应哪份编码 PNG；这层保证来自生产唯一调用链 `_frames → _shop → stable_purchase_slot`。整个 manifest 版本变化会保守地使旧资格失效，即使徽标文件没变，也需当前重新读取。

购买前原槽、名字、价格、位置、页面、金币及视觉稳定性检查仍在；策略/预算/guide、epoch/CAS、receipt、pending、业务顺序和每次 ROOT 出战鲜帧审批没有改动。`recommended=true` 是读取结果，不是购买或攻略授权。

## 真实图与素材来源

原图复用 main 的 [free-refresh-sequence](free-refresh-sequence/manifest.json)，没有新截图或重新上传三图。历史组内仍是混合动作、共享 `game-preview.png` 别名，缺原生不可变收据帧身份。本批未拆动作、未补坐标/wait 成功，也没有把 f00/f01/f02 串成购牌或刷新闭环。

公开树没有本机完整商店素材。本批新增的 `tools/badge_calibration/recommend_badge.png` 只是 f01 的原分辨率 RGB 精确裁片：源 SHA `288b6a4f4ecc67476cc8a12bd806e37accf2ffe6c4445c058fd24f0116b2ea11`，裁框 `[1455,68,1502,119]`，47×51。`SOURCES.json` 绑定裁片和来源；`names.json` 刻意为空，只供局部 `_load` 使用，不能支持实名或整店读取。生产默认资源目录没有改动，不替换或安装 ROOT 原 57 素材，也没有导出它们。

两次离线人工查看了 15 块 79×77 原像素 ROI。`ROI_ANNOTATIONS.json` 单独绑定三图 SHA、每槽卡框、ROI 坐标和 RGB 摘要。f01 第 1 槽是肖像金饰，f00 第 5 槽是白色冠饰；其余 14 槽均无目标圆框书/星徽标。标注从不传入生产推荐判定。

f01 第 5 槽既是模板源，也是新增外观规则的校准样本，**独立泛化正例为 0**。driver 按实际灰度匹配像素识别同源校准，重新编码 PNG 不会制造新增覆盖；即使传入本机既有模板，该 f01 外观也仍是本批校准样本。

ROOT 历史原模板实测的 `.9807 / .8232 / yellow=0` 保留在 [ROOT_F01_BADGE_AUDIT.json](ROOT_F01_BADGE_AUDIT.json)。下面的公开校准自匹配接近 1，不能替代或冒充本机原模板复测；新增其他峰、当前来源及本机完整消费者结果仍由 ROOT 独立验收。

## 冻结的实际 ROI 读取

[roi-read.json](recommended-badge/roi-read.json) 保存双方完整原生徽标输出、来源及 unknown。对照源码从准确的 PR16 Git 对象读取，并核源码 SHA；双方用同供公开校准资源，均执行实际生产 `_load / _rectangles / _recommended`。一个只会抛错的 OCR 边界对象禁止调用，不提供识别结果；没有初始化模型、调用 OCR、执行全商店读取或填名价。

| 公开原图 | PR16 五槽推荐字段 | 候选五槽推荐字段 |
|---|---|---|
| f00 | false, false, false, false, false | 相同 |
| f01 | false, false, false, false, null | false, false, false, false, true |
| f02 | false, false, false, false, false | 相同 |

三图实际几何均 5 槽、框分数均 1、无 overlay。f01 正槽候选内部形状 `0.999999523`、完整模板 `1.0`、其他峰 `0.599487722`；宽橙金比例 `0.117540687`、匹配内区 `0.481481481`。这些是模板相关分数和像素比例，不是识别概率。真实报告约 41.8 KB，包含 15 ROI 及下面 6 个反例，无重复 Worker 大对象。

公开环境只证明徽标 ROI。它没有证明五槽姓名/价格/完整 ShopReader 状态，也不改变 ROOT 先前实际 57 素材下 `ok / partial / ok` 的资源事实。

## 必要反例与消费者协议

6 种生成像素反例单列在同一报告，均返回 `null`，不能计为真实图覆盖：

| 生成反例 | 实际结果及作用 |
|---|---|
| 满橙色、无书形 | 形状 0；不能靠颜色取得资格 |
| 原书形整体去色 | 形状约 1；颜色不足保持 unknown |
| 仅书形内区去色、外围保留原橙色 | 形状约 1、完整模板 1、宽橙色 0.061976 仍过门槛；内区色为 0，独立检验同位置颜色约束 |
| 中心 4×4 遮挡 | 内形 0.941229、完整模板 0.982564；不能沿黄色较低形状门槛接受橙色 |
| 保留内形、外框区域改为平橙色 | 内形约 1、内区橙色 0.481481；完整模板 0.487801，保持 unknown |
| 原书形改为青色 H90 | 形状 0.999689、完整模板 0.991384；不在支持色域，保持 unknown |

独立的 `test_currency_wars_badge_consumers.py` 只表达协议：生成两份真正 PNG、生成几何书形和资源，名/价/页面/金币/预算/guide 等明确为测试前提；徽标由生产函数读取。它验证两帧稳定资格及真实 `_frames` 身份检查，来源/ROI/槽/页/价/推荐/遮挡变化、模板或 manifest 变化、缺素材/来源时继续拒绝。模板 PNG 仅 metadata 改变而解码像素相同，也拒绝旧来源。正例禁止消费者调用 `_load` 和 OCR。

Worker 子例复用既有 inert 夹具，只检查策略与授权边界：显式目标和预算齐全时允许进入资格；无目标、无预算、guide 未完、模式冲突、epoch 改变、原 pending 均拒绝。所有分支零动作发布、零实花，pending 原值不变；没有生成购买成功收据。它们不是三份历史图的连续动作回放。

## 聚焦验收与 Windows 复核

[acceptance.json](recommended-badge/acceptance.json) 与 [tests.txt](recommended-badge/tests.txt)：Linux 既有离线依赖，**11 项全部通过，0 失败/错误/skip，8.670235832 秒**。其中直接受影响旧徽标 5 项、新消费者 4 项、真实 ROI 与局部反例 2 项。没有机械重跑 PR16 原 20 项。10 项绑定文件测前后相同，独立 CLI 真图报告绑定相同摘要；三图、输入 manifest 和徽标资源读取前后未变。

runner、economy、Perception、state_reader 与完整 PR16 逐字节相同；`purchase_slot`、原几何/名价读取、`_frames` 及原语义计划守卫的 AST 也相同。最初用默认 Python 导入新消费者时因缺 `psutil` 失败，未进入测试方法；随后使用既有依赖路径，未安装包。该环境失败如实记录于验收 JSON。

ROOT 在新独立克隆及原有依赖环境中，仅需本批选择：

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = Join-Path (Get-Location) 'tools'
python -B -X utf8 -m unittest -v test_currency_wars_shop_reader test_currency_wars_badge_consumers test_currency_wars_badge_replay
if ($LASTEXITCODE -ne 0) { throw '推荐徽标聚焦选择失败' }
```

该选择已经包含公开校准 ROI。按 ROOT 既有方式在隔离克隆覆盖原 57 素材后，用同一冻结 driver 另取本机原素材的局部结果：

```powershell
python -B -X utf8 tools/replay_currency_wars_badges.py --resources tools/shop_reader_resources --output badge-roi-local.json
if ($LASTEXITCODE -ne 0) { throw '本机徽标原生读取存在问题，请保留原始报告' }
```

driver 不输出素材内容；`resource_context` 记录当前实际加载上下文，不能把公开限制外推本机。它在显式素材下仍只读三图局部徽标，不额外全屏 OCR。精简记录中 `issues`、unknown、其他峰及来源都保留。

本机后续最小补证：原 57 素材下三图 Perception/五槽当前读数及原玩家字段；用 f01 实际名价/新徽标证据核买前资格相关路径。三图不是同槽连续买前帧，不得强拼稳定性成功。缺合法第二帧时继续把稳定性成功限于显式协议。旧混合收据身份、坐标/wait 缺口、真实出战、ROOT 等待、生产发布与整局收益均未验证。该局部闭合后，只根据下一份真实消费者缺字段清单继续选择问题。

## 成熟实践与交付体积

本次仅借鉴 OpenCV 官方的 [局部模板匹配与峰值定位](https://docs.opencv.org/4.13.0/df/dfb/group__imgproc__object.html) 和 [HSV 区间筛选](https://docs.opencv.org/4.13.0/df/d9d/tutorial_py_colorspaces.html)，沿现有输入者及当前帧校验接线。灰度归一化相关衡量形状相似，不给颜色或购买语义；PIL 输入继续按 RGB 转 HSV。H16/17、0.97/0.80/0.06 及位置容差是本项目校准与守卫选择，不是官方给出的游戏阈值或误报率。没有新控制器、在线视觉/文本模型、服务密钥或新依赖。

发布复用 main 的 PNG 和新增证据 Git blob，仅上传本批代码、冻结 driver/测试、小型公开裁片与紧凑验收；不再尝试已知缺凭据的 CLI push，不传重复 Worker 全对象。完整采用结果已放在上述小报告；探索中间文件不冒称已公开，也不承诺额外真实覆盖。没有合并、安装或游戏输入。

## 源码摘要

下列 10 项摘要同时存在于两个冻结 JSON，供 Windows 测前后核对。最终提交号另见草稿 PR；这些内容摘要不依赖本地临时合并提交号。

| 文件 | SHA-256 |
|---|---|
| `tools/currency_wars_shop_reader.py` | `55760b092da7b45f73b18633b62b88ab9d74574449b4b511bd38964712bfd8b8` |
| `tools/currency_wars_visual_guards.py` | `5308b62f27078af011d6ee3f3d6992937d3111ce1ebf7181dd9b201c101cd70c` |
| `tools/replay_currency_wars_badges.py` | `e2c0c46892730cddc6cad19c1b1d9f81470a9fb49bb6da208d5dbd8dfbc33534` |
| `tools/test_currency_wars_shop_reader.py` | `94a2d2004ed828299bb7db7b38164356cfab0c71a6e48d84c3521437261d069f` |
| `tools/test_currency_wars_badge_consumers.py` | `bf4f8f344338d63597c2705d60be6d394e367abc5de39f170711f40a47ec8ba6` |
| `tools/test_currency_wars_badge_replay.py` | `74674bc1b16bbffc5079d6a694d5c01321bca7f4056c4038e4bfa69a708190db` |
| `tools/badge_calibration/SOURCES.json` | `78621090c0625fa1bf400cced0e8d38894c73fe73e7793b23b3f5f7d5862d793` |
| `tools/badge_calibration/names.json` | `760f6135d506f9309f62591b63f937fea5f93c49ba28c4d39ef7e3d8a212ff79` |
| `tools/badge_calibration/recommend_badge.png` | `84b5071ffe28e0d57ee682a4b292706ac3f3aba04b4f686182f3272d665ec7aa` |
| `tools/badge_calibration/ROI_ANNOTATIONS.json` | `2be5df00536727ada38401347f89a77aac96dfa5c9a869e0cc29585b4599dfeb` |
