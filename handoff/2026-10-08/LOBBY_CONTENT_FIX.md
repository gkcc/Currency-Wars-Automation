# 大厅开始按钮：分开定位范围与稳定内容

## 基线、证据和最小改动

本候选以冻结 PR22 `5d3818034197c40a1c3b13cf940208e0000fce82` 为唯一直接父；不改 PR22，不引入 PR20/21。生产只改 `tools/currency_wars_runner.py` 的大厅开始守卫及同一动作的消费触发。

已实际 fetch ROOT 证据提交 `48600a89f64e9e3bf18bd7e5a47e287a10fc27cd`。随本候选复用两份原 blob：

- `ROOT_PR22_NATIVE_HANDOFF.md`：`f3b302108293d36a59b7c8b5706f0f090805ab98`。
- `ROOT_PR22_NATIVE_LOBBY_GUARD_FAILURE.json`：`40f4f7f17b86fe7309787ad6ed6db36b90277825`。

公开记录的原请求 `f0950a27f7fc48d8920b5bbd435b9b65` 与只读 capture `52b869a294744c1c8b506d1c94b02d8e` 均为 lobby，五锚点唯一、置信度至少 .90、位移不超过4px，记录 input_attempted=false。完整 450×90 开始搜索框的差异 bbox 为局部 `[52,89,385,90]`，只碰绝对 y=1019 最后一行。开始文字原框 `[1468,956,1706,990]`、鲜框 `[1468,954,1706,988]` 均远离该行。

这是已定位的 B 类局部守卫误拒。公开资料没有两张原 PNG，不能独立确定该行究竟是背景动画、按钮边缘还是其他像素来源；不能将五锚点记录冒称完整页面分类或原 PNG 回放。上述样本仅校准这条边界，不充作独立泛化正例。

### 生产行为

1. **定位搜索范围不变**：`(1360,930,1810,1020)`，请求 bounds 仍必须精确匹配。仅将稳定内容范围设为 `(1360,930,1810,1019)`，其余 **450×89** RGB 像素仍逐字节相等；不放宽相似度、不裁其他三边。
2. 两帧完整开始文字框必须都在稳定内容内。执行时继续使用鲜框中心，未硬编码新的点击坐标。原五锚点、confidence≥.90、既有≤12px OCR 框容差、实际 lobby、单个 exact click_text、完整原生标签均保留。
3. 两个原 PNG 仍须各自符合完整文件 SHA、PNG 格式和1920×1080。底行仅不参与局部稳定比较，绝不从完整源文件摘要中删除，不改写原图。
4. 同一大厅开始目标现在**不论全图差异大小都先过该守卫**；错误 kind、显式子串、多个动作不能由小全图差异绕过。大全图差异路径复用该结果，不重复读取同一对 PNG。其他导航/经济入口与全局 .10 阈值不变。
5. 原 validate_plan、期限、snapshot/epoch、ManualPhase、Entry、GuardedSubmission、pending、前台及发布前请求身份校验继续生效。拒绝未发布动作；不重发未知输入。

实际页面改变、锚点失效或内容区被覆盖/变暗继续拒绝。该局部契约不声称能识别所有远离控件的覆盖层；没有新增完整覆盖层读源。

## 冻结聚焦检查

`tools/replay_currency_wars_lobby_content.py --protocol` 只选新 `LobbyContentTests` **7个方法**。沿既有无设备的临时 transport 调用实际 Worker/Entry；合成像素与声明的读取字段均明确是协议，不是游戏回执。未运行旧14/39、GUI/Rust或PR20/21选择。

| 聚焦端点 | 结果 |
|---|---|
| 仅底行变化；小/大全图差异 | 都只发布一次 click + 原wait，点击鲜中心 `(1587,971)`；原请求 PNG 不变 |
| 内容内邻接行一个像素、文字移位、中心遮挡、变灰 | 小全图差异下仍零输入拒绝 |
| 五锚点各自缺失/重复/低置信/过度位移；文字框碰底行 | 拒绝；不补锚点 |
| 原/鲜文件字节、snapshot、格式、尺寸或源缺失 | 拒绝 |
| 错误 kind、子串、多个动作、bounds、页面、文字、epoch/request | 拒绝；不靠大全图差异才检查 |
| 局部守卫通过后、发布前 epoch/pending/前台/请求被替换 | 零输入、request_published=false；原 pending 留存 |
| 本地五件配对只读复核 | 缺身份、reply epoch不符、文件替换不能通过；不修补源、不泄露私有字段 |

协议成功后图明确设为 unknown，断言 match_id未变、outcome_confirmed=false，绝不把 completed 当作已进新局。

Linux / Python3.12.14：**7 passed，0 failure/error/skip，6.836327420秒**。这是离线选择耗时，不是游戏或工具链速度。28项测前后来源摘要相同，其中生产来源24项；requirements 已在生产集合中，不重复计数。

Source-set：`954c5c61152ca39faf53abd2ec2cb189e7d50f82a913f376ad946fd05badf6ab`。

| 文件 | SHA-256 |
|---|---|
| tools/currency_wars_runner.py | 5e2602abc70a65eedcd3f147bd808993c0bad1afef6c9968c01b445428d96bbb |
| tools/replay_currency_wars_lobby_content.py | 1e30a30d6e4f2421f2887f616780be32cd1d9ea7aff8aedbe80a00c430287846 |
| tools/test_currency_wars_lobby_content.py | 3cef0cee1fff2590dc05f30d7c033744183921ec98fc1f23c9e9b228c8e77945 |

`lobby-content/acceptance.json` 为6,979字节原生紧凑报告，包含全部7项结果、28项摘要、环境和边界；没有重复上传 tests.txt。报告测时 HEAD 如实是父提交，修改字节由测前后摘要绑定。冻结发布提交由 PR/head 及同一树回读绑定，未伪填测试 head。

### Windows 独立选择

在候选独立归档根、使用 ROOT 原 Python 环境：

```powershell
python -B -X utf8 tools/replay_currency_wars_lobby_content.py --protocol --output lobby-content-windows.json
```

期望7项、0 failure/error/skip、同一28项source-set、测前后差异为空。归档无.git时 tested_checkout_head=null 是合法事实。此选择不需要原57素材或新截图。

### 本机原匹配帧对只读复核

仅在原请求、原回复、对应当前观察和两张受绑定的1920×1080 PNG仍实际留存时，用本地 JSON 指向它们：

```json
{
  "schema": 1,
  "request_json": "original-request.json",
  "reply_json": "original-reply.json",
  "current_observation_json": "actual-observation.json",
  "original_png": "original-request-frame.png",
  "current_png": "actual-frame.png"
}
```

```powershell
python -B -X utf8 tools/replay_currency_wars_lobby_content.py --pair-spec retained-lobby-pair.json --output lobby-content-pair.json
```

程序直接调用生产守卫。只在内存重映射原图片文件位置，不补 snapshot/frame/capture/epoch、不改文件。完整旧ROI应 false、候选内容ROI应 true，身份/完整SHA/指纹/原reply匹配均成立时 byte_bound_visual_eligibility=true。缺原件时明确缺失；不能从五锚点 JSON 造图或把4K缩放后借原身份。

输出仅文件摘要和判断，不含原行/路径/owner/token。native_capture_authentication_replayed、current_epoch_pending_foreground_verified、input_published、navigation_outcome_verified均为false。历史配对通过不恢复旧请求的输入授权。

## 最小升级与实机端点

1. ROOT 沿原 stop 停当前 PR22 所属进程，核真实退出及 CURRENT 的 journal/match/business/broker来源仍在。这正是 PR22 尚待验的真实 stop 端点，本候选未改该逻辑，不能提前宣称通过。
2. 退出并获原 source activity 写窗口后，将生产源码快进到本独立候选。没有运行中热换；原资源/业务记录保留，旧未发布计划废弃。
3. 只变 runner 一个生产文件。原 bridge/control pins、runtime manifest、requirements、GUI/Rust及资源声明字节均未变；原安装和GUI在既有验证仍匹配时可复用。ROOT 独立审查新24源码+原61resources/provider并绑定新的真实READY，不能沿用旧runner摘要或手填ready=true。按原升级流程处理旧pyc，使用全新进程。
4. `python -B -X utf8 tools/check_install.py --check-launch` 必须实际通过；沿已经验证的 runtime root 和原正常 start 参数续接。使用新请求、新 snapshot/epoch 和实际 business_resume；不导入旧raw动作、不伪造备战节点。
5. 验收新的实际 new_match：本轮 fresh 原生 lobby及五锚点通过，开始动作发布一次，有原不可变收据并鲜读实际到达页。若到达模式选择页，只记大厅导航完成；未知/pending继续回传，不把协议PASS记第三局开局或整局加速。

ROOT 后续可只记录该新请求发出→发布→收据→实际到达页的时刻、拒绝原因及本地守卫事件；维护/重新检查页面的空档单列。本批不恢复或优化旧检查点，不处理GUI共享fixture。
