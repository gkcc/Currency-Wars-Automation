# 开发与审查记录

只记录本片发现、修复及范围，不把最终通过报告改写为从未失败。

1. 首次4项视觉选择，新增中央“是否确认”控件但备战锚点保留的声明覆盖层仍通过。补充当前中央明确模态词的拒绝条件后，这4项通过；没有恢复全屏 OCR 精确相等。
2. 首次完整新8项有3个 assertion failure、0 error：native来源实际 `native_visual_guard`，夹具误期望 `native`；错误 control ID 携带新主管 proof 时先落入通用路径，虽仍零输入，但多了一次观察；另一条是该观察造成的后续计数断言连带失败。修正测试来源名，并在 `validate_plan` 明确禁止新主管 proof 转入其他 control。最终保留零观察/零输入断言，没有通过放宽断言隐去分流问题。
3. 代码审查发现 `save_frame` 保存展示 JPEG，不可当原生后效 PNG。新增结果保存直接复制已核原 PNG 字节，在旧 transport frame 释放前保存，逐帧留 digest/capture/frame。
4. 后继核效每一帧都校验同局、epoch、期限和 stop；输入后换 epoch 即使声明后图有“创业指南”也不得记 observed，不补读或重发。
5. 主管明确当前恢复曾要求新 PNG 摘要必须变化。审查修为新 capture/frame 身份即可，允许真实新捕获恰有相同像素。回归走完整旧 mailbox：原 known completed / effect unknown 档案不变；ROOT 新当前 proof 仅生成一个新当前意图，无自动重试。
6. 独立审查确认本片没有把 JPEG来源、抗锯齿差或动画当作已证原因；可证的是旧 packed mask 与新 PNG 字形不一致、分数低于原阈值。没有受控 JPEG/PNG 原样对照。

最终新8项：8/0/0/0，10.72769773秒。随后仅 PR29 受影响的 `test_exhausted_pending_current_recovery_blue_once_then_new_epoch_full_review`：1/0/0/0，10.00510263秒。两次35份源码/19项素材映射均为 `495f5810972d758570624ec143a7d73b406a465f870ab4f5e0db69bbe3c4bf48`，测前后不变，闭包无缺失。没有重跑 PR29 全8、旧经济套或 GUI/Rust。

新报告 `tested_checkout_head` 保留测时父提交；候选未提交改动由源码 map 绑定。发布时还需将远端所有变更文件与本地被测字节逐一比对。Windows/原完整PNG的新鲜来源/真实导航/完整准备节点由 ROOT 验收，不能从声明协议推定通过。
