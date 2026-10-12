# 环境选卡：原生灰字与书标校准

## 结论与来源

本候选唯一父为 PR25 `30870dd5113456fae930d5a23d44b7556611605e`，未并入 PR24。已实际 fetch 证据分支冻结提交 `6ef3793a827af65ff047ef89941714f7dd7a0017`，在本地核过 12 份原生 RGB 数组摘要、4 张 PNG 的导出摘要，以及确认数组与 PNG 裁片的逐像素一致性。没有把编码数组打印到对话，没有新截图或 OCR。

只修改一个生产文件 `tools/currency_wars_visual_guards.py`（80 行新增、10 行删除），无新生产依赖或素材。三个已报告拒绝点均已接入现有生产选卡消费者；**实证仅到原生裁片/字形校准，未完成原整图独立比较或新请求实操。**

证据原件仍在上述独立证据提交，未重复搬入候选。原 production actual 的 OCR rows 缺失；独立重读不会填补历史身份、旧 epoch 或旧输入资格。

## 改动和真实校准结果

| 检查 | 原生两帧测值 | 新规则 |
|---|---|---|
| 确认灰字 | 白字0/0；灰字368/361，交并比0.980978；Vmax187/187 | 仅环境选卡：S<40、100≤V≤190，字形至少24像素、占比1.5%～60%、交并比≥0.98；任一V>190拒绝 |
| 确认附近外观 | 字形并集RGB最大差8；向外2像素最大差15；整字框最大差17、约52.3%像素变化 | 字形RGB最大差≤8，2像素邻域≤16；不按整ROI均值批准 |
| 角色/装备 | 完整小框RGB相同，边缘246/253 | 唯一高置信OCR、原结构位置，加完整RGB相同及非空轮廓；两个文字消费点共用 |
| 三个黄书 | 完整RGB相同，边缘106/102/104；旧白字检查全false | 固定书框完整颜色/状态字节相同，轮廓至少24像素；双空白不通过 |

确认的新规则只批准“可继续核验选卡目标”，不批准点击禁用确认本身。原亮字分支保留；本次灰源不能与亮字跨分支匹配。书标轮廓检查是存在性检查，不是新书形身份识别或泛化模板。

全部卡片语义、结构标签归属、真实效果完整覆盖、原布局/边条选择状态、页面锚点、单动作目标证明、源PNG摘要与发布前守卫保留。投资/补给色域和一般正文规则不变。没有新增四角完美RGB约束，也没有声称原边条检查等于四角全验。

## 聚焦验收

Linux Python 3.12.14，唯一新类 `NativeCardInkTests`：**6 passed，0 failure/error/skip，5.322360242秒**；本轮首次运行即通过，没有重跑旧冻结类。29项来源（24生产＋新driver/测试＋原driver/测试/fixture）及7个外部证据文件前后不变。

source-set：`e444dc4765b0350823180ffac7a719454902966f67e21c152f139ee258da8154`

| 文件 | SHA-256 |
|---|---|
| tools/currency_wars_visual_guards.py | f667ad734c0af1ef110074c58b05a2b0be011cdeb77683a89dac532de1eb5881 |
| tools/replay_currency_wars_native_card_ink.py | 5196e3da4427655ac7ab2133186f947921ebe86485c3541ff795eb486ac643f4 |
| tools/test_currency_wars_native_card_ink.py | 811815456d076773d6ec48a8f3115bf311f8a54fd859758d31a43321cd9fcce7 |

[native-card-ink/acceptance.json](native-card-ink/acceptance.json) 原样保留12497字节报告：完整29项源码摘要、7项证据摘要、6个真实ROI对的测值、各用例结果及限制。测时HEAD如实为父PR25，未提交字节由前后摘要绑定；发布后另核不可变提交。

验收分两层：

- **原生派生裁片：** 确认背景变、字形稳定通过；两个灰标题和三个黄书通过。灰字变白、涂空、有色、50%暗覆、局部覆盖或位移拒绝；后者是原裁片上的合成负例，不是更多真实样本。
- **混合协议：** 其余画布生成，贴入原生ROI，使用新的PNG摘要，实际经过Worker→Entry一次发布，`outcome_confirmed=false`。标题/效果、书、边条状态、页面、结构/正文遗漏、覆盖、源SHA、epoch、deadline、两动作、发布前pending/请求替换拒绝。未恢复任何原整帧资格。

## Windows 最小入口

在本候选独立归档执行。若7个证据文件尚未解出，从已fetch的冻结证据提交导出即可，无需合并证据分支：

```powershell
git archive --format=zip --output=pr26-evidence.zip 6ef3793a827af65ff047ef89941714f7dd7a0017 handoff/2026-10-08/ROOT_ENVIRONMENT_CONFIRM_RGB.json handoff/2026-10-08/ROOT_ENVIRONMENT_CAPTIONS_RGB.json handoff/2026-10-08/ROOT_NATIVE_CONFIRM_CALIBRATION
Expand-Archive pr26-evidence.zip -DestinationPath pr26-evidence
python -B -X utf8 tools/replay_currency_wars_native_card_ink.py --evidence-dir pr26-evidence/handoff/2026-10-08 --output native-card-ink-windows.json
```

`git archive` 在有该证据提交的原仓库执行；Python在候选归档执行，`--evidence-dir` 可用实际绝对路径。归档无.git时head=null合法。driver先拒绝任何证据字节变化，不自动联网下载；完整摘要在同一报告。

随后可沿原pair-spec运行父版只读入口，**不带`--protocol`重跑旧六项**：

```powershell
python -B -X utf8 tools/replay_currency_wars_card_confirmation.py --pair-spec retained-environment-pair.json --output environment-full-png-independent.json
```

它会调用本候选生产守卫。检查独立视觉比较与来源绑定结果；`historical_qualification_available`仍应false。原driver保留的白字计数/旧书白字诊断false是旧规则读数，不能当作新守卫失败；其native enabled/disabled字段也仍unknown，没有由人工注释改写。

## 切换与实际端点

ROOT完成Windows及原整图独立复核后，按既有完整生产源码绑定、check-launch、start流程切换。当前PR23生产不能只拷本次一个文件：使用本候选完整24项生产闭包，包括父PR25的reader/runner修复。新批次无GUI/Rust/安装器改动，无新增生产素材；原资源/provider绑定仍由ROOT独立核查。

实际选择需全新request/snapshot/epoch及完整目标proof，经原broker发布一次，再以新帧核选中状态；completed不是选中成功，确认另取新请求。未测Windows、原整图独立新结果、真实选择/确认、进入1-1或整局速度。

`ROOT_PR23_NATIVE_SETUP_EXIT.json`记录的是600秒空闲租期对应的broker退出、worker失败清理与原业务可load；没有调用正常stop，不计作正常stop验收。本候选未操作游戏、进程、READY或用户持续整大局授权。开局人工桥、检查点、GUI共享fixture问题继续留原待办。
