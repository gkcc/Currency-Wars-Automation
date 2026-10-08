# 环境选卡守卫：先修确定的不一致，灰色确认待原图校准

## 交付结论

本候选以冻结 PR23 `02e0a7c86f82e74216b759bed60a1a7cb55849eb` 为唯一父。**尚未解除本次灰色确认按钮的实际阻断，不建议把它当成可开局的完整修复安装。** 已实现两项有证据的契约一致性修复，并提供不启动游戏、不调用 OCR 的本地原图诊断入口；取得最小确认裁片后再定禁用态掩码。没有把白字掩码为零解释为按钮不存在，也没有用猜测色域批准输入。

PR24 的检查点变更没有混入。当前游戏、ROOT 源码、READY、broker 和 GUI 均未操作。开局战略页的同 broker 手操承接作为待办记录在本文件末尾，不扩本批。

## 已定位证据

本轮事实来自 ROOT 2026-10-08 的明确反馈，原图在 ROOT 本机；本候选没有取得以下两张完整原 PNG：

| 原件 | ROOT 报告的身份 |
|---|---|
| 请求 | `939061bf00684efab97fa416591f0483` |
| 原请求 PNG SHA-256 | `bdbc45bd1343b4b78ca11c1b626eccb7dec1767e422b36237c96736cfac57e85` |
| 原 actual PNG SHA-256 | `d38dce61543579231ae4e1144e182552af105bf9db017b5c7faf48c501e43da2` |

原 request/reply 和两张原生 1920×1080 PNG 留存。原生产 actual observation 缺失 OCR rows；ROOT 对 actual PNG 的独立重读是另一次读取，不能补进原生产观察或恢复旧输入资格。

源码核对与 ROOT 反馈一致：

1. 环境 `option_facts` 原已接受确认区域 `[770,950,1160,1025]`；两条视觉路径却复制 `[880,935,1040,1030]`。实读确认框 `[1054,968,1110,1000]` 必被窄框拒绝。
2. 选卡前确认禁用且为灰字；ROOT 实际测得原、鲜白字掩码均 0。`_paired_row → _text_equal` 要求至少 24 像素，所以改位置后仍拒绝。**零白字像素不是足够的灰色色域或禁用态证据。**
3. 环境效果读取只到 y=690，完整卡片文字守卫却覆盖到 y=848。左卡“角色” `[420,598,462,626]` 已混在原 effect_lines 内；“装备” `[422,744,459,766]` 没有进入 effect_lines，却被全卡覆盖检查看见，造成第二层拒绝。

公开树已核的 16 张留存 PNG 都属于商店、备战、奖励、刷新或首领介绍；没有这三类选卡页或灰确认原图。不能用其他页面的同名角色、协议图或人工 true 替代原控件校准。

## 生产改动

- **环境确认位置单一来源。** `ENVIRONMENT_CONFIRM_BOUNDS` 提取自已有原生 reader 的范围，reader 与通用视觉守卫共同使用。投资、补给各自确认区域不扩大。
- **结构行完整参与检查。** 仅环境页的 `角色/装备` 可作为居中、固定纵向区间的结构标签。它们必须在两帧分别唯一、置信度至少 .90、位置和字形稳定。已有 effect_lines 中的“角色”保持原样；未包含的“装备”补入守卫的完整覆盖核对，不写成新效果字段。其他未归属行、真实效果遗漏、低置信、重复或错位仍拒绝。
- **实际消费者不走旧旁路。** 环境 `check_target_roi` 总是验证完整合同，包括目标卡 RGB 恰好相同时；旧 `stable_environment_card_animation` 的环境分支也委托同一合同，避免旧百分比回退绕过结构、确认和其他卡片检查。投资分支保留原行为。
- **环境选卡必须独立单动作。** 不可在同一旧请求中拼选卡与确认；选择后必须新请求、新帧再处理后续动作。本次没有增加“确认可点击”资格，也未完成原生选中/启用态读取。

原完整 PNG 摘要、全部三卡语义、标题与全部效果、卡框边缘与选择状态、书图标、单动作目标 proof、epoch、期限及发布前 pending/前台/请求校验保留。**灰字规则尚未校准，稳定灰确认仍拒绝；不删除页面锚点，不改全屏 dHash 阈值。**

### 各页边界

| 页面 | 卡片布局 | 确认区域 | 本批状态 |
|---|---|---|---|
| 环境 | 原三卡 `OPTION_LAYOUTS.environment` | `[770,950,1160,1025]` | 共用原 reader 布局；结构行修复；灰字待原像素 |
| 投资 | 原三卡 `OPTION_LAYOUTS.investment` | `[880,935,1040,1030]` | 核源码；没有本轮真实图据，不变更 |
| 补给四卡/五卡 | 原两种独立布局 | `[1580,950,1810,1025]` | 原 reader/guard 范围一致；没有新灰字图据，不变更 |

不把环境页灰色外观外推为所有页的通用阈值。后续若主源证明控件样式相同，再共享对应识别规则。

## 聚焦验收

Linux / Python 3.12.14：**6 passed，0 failure/error/skip，4.944328115 秒**。只运行新类 `CardConfirmationTests`；原 runtime 模块只供惰性夹具，未运行其旧类。

27 项来源（24生产＋新driver/测试＋原runtime夹具）测前后一致；source-set：`e5d75b812b76020a0c2dfeb77e665a51c0565217c6c421487a838e16f886b64f`。

| 文件 | SHA-256 |
|---|---|
| tools/currency_wars_perception.py | d09a5c586c7f82fbadda5cbf43f5cb90b0c38e8a7da453cec5adc95153f15808 |
| tools/currency_wars_runner.py | 3a9e623ce3d33fce1e4e605a0119c20f5e4b64fe369771c06ee450511c4fe35d |
| tools/currency_wars_visual_guards.py | 73b379efb109181f028a6a9917f19bc03fe6cea24279ba312ccdfb12230a121e |
| tools/replay_currency_wars_card_confirmation.py | 74e3809dec67fdbccaa37bbf11b25478ee1f9793f1830312d170e96e545b7554 |
| tools/test_currency_wars_card_confirmation.py | 03600e0306d136cae063b521b5bce01113080bfa349e97653ffdc0db4008ffec |

[card-confirmation/acceptance.json](card-confirmation/acceptance.json) 为 6526 字节原生报告，包含全部结果、27项摘要、环境和限制。首次本轮选择全过，未重跑无关旧冻结类。测时 HEAD 如实为 PR23，实际变更字节由前后摘要绑定，发布树另行 fetch 回读。报告 `native_disabled_state=unknown`，真实原图对数为0。

```powershell
python -B -X utf8 tools/replay_currency_wars_card_confirmation.py --protocol --output card-confirmation-windows.json
```

归档无.git时 head=null 合法；期望6项、无失败/错误/跳过和相同source-set。

合成亮字确认正例仅验证位置与结构一致性、真实 Worker/Entry 的单次选择消费；**不是本次灰确认正例**。灰字稳定负例要求继续零输入拒绝。没有原 PNG 回放、原生灰态通过、真实选中结果或整局速度结论。

## 本机最小资料与诊断

将本机实际文件位置写入一个 spec；路径可相对于 spec 文件：

```json
{
  "schema": 1,
  "request_json": "original-request.json",
  "reply_json": "original-reply.json",
  "original_png": "original-request-frame.png",
  "current_png": "retained-actual-frame.png",
  "independent_observation_json": "independent-actual-read.json",
  "manual_annotation": {"confirm_disabled": true}
}
```

这里的人工禁用态注释来自本轮 ROOT 已核事实，仍不会写入 native 状态。请提供已有独立重读的 observation 对象；不要为配对而补原生产 actual 缺失的 rows。如果使用真实留存的历史 actual 对象，字段名改为 `current_observation_json`；两字段互斥。如果均没有，可省略它们，另写 `current_png_sha256` 为 ROOT 已保存的 actual PNG 摘要，此时仅做像素诊断，历史资格仍缺失。

```powershell
python -B -X utf8 tools/replay_currency_wars_card_confirmation.py --pair-spec retained-environment-pair.json --calibration-dir confirmation-calibration --output environment-confirmation-read.json
```

只读取指定文件，不调用 Perception/OCR、不截屏、不启动 broker、不发送输入。原图必须匹配原 request/observation 的 snapshot；actual 图必须匹配提供的读取对象或显式摘要。不匹配时在裁片导出前拒绝。原请求和读取文件不改写。

输出包含确认原行/置信/框、白字和既有文字掩码数、RGB/HSV分位数、局部差异、三卡有界行和native options匹配摘要、既有书/边框判定，以及四角各32×32的像素差异。原边框判定没有覆盖完整四角，不能把它的 true 写成四角全验。

显式 `--calibration-dir` 才导出两帧固定390×75确认区域；有唯一高置信确认行时，再分别导出文字框扩2px的小裁片。每项附原整帧SHA、裁剪框、裁片SHA及派生校准标记，不自动上传。

**诊断的 `ok=true` 只表示工具正常完成，不能解释为选卡可执行。** 历史生产观察缺 rows 时 `historical_qualification_available=false`，独立重读结果单列。native disabled/enabled 均保持 unknown，人工注释另列；所有真实输入、当前授权和选择/确认结果均未验证。

只需交回诊断 JSON 和确认控件的小裁片；原整图、本机 57 素材、UID、owner/token 均不需要。裁片是从已留存原 PNG 得到的校准样本，必须携带原完整 SHA、裁剪框和裁片 SHA；不是新截图或独立泛化正例。

要定灰字掩码，最小还缺：**原、鲜两帧确认区域实际 RGB/HSV 分布、字形与背景分离情况、同一控件禁用外观是否逐像素稳定。** 只给白字数量 0 无法区分灰字、空白、覆盖或其他不可见状态。诊断可先使用 ROOT 已有独立重读 JSON，不再做一次全屏 OCR。

原生产 actual rows 缺失时历史资格始终标为无法验证。独立重读的视觉比较另列，并且不代表旧 epoch/期限/前台/pending 有效。修复后的真实选择须等新请求、新来源、新代次，发布一次并核新帧选中状态；completed 不等于选择成功，更不等于已确认进入下一阶段。

## 保留的后续项

1. 灰色禁用确认的来源校准及 `disabled→disabled` 选卡正例；灰转亮/遮挡等变化必须拒绝旧选择，启用确认另用新请求。
2. 同 broker 的开局战略页手操承接：现 manual-step 依赖真实 prep stage，在尚未 1-1 时不能借用虚构备战节点恢复。需要后续最小适配，不能用第二控制器、raw 旁路或改锁解决。

当前用户连续整大局授权不被本交付改写；本文件中的“待校准”只说明候选能力，不代替 ROOT 的正常运行/暂停决定。
