# 开发检查与来源边界

- 唯一父 PR28 e9e6ca7f4e4d943612d85ed3d28bd483b8224958，实际 fetch 并由 GitHub get_pr_info 回读。生产只改 runner/manual_steps/manual_stage；不含 PR24。
- ROOT 本轮自然灰球一笔 completed、4→6、2次只读/3帧仍 pending 的事实来自用户消息；本环境没有那次原全帧/原收据。PR28 Windows 8/8、金币 .9993116856 和实际部署由 ROOT 已报，本批不重复声明为本机独立测试。
- 第一轮开发选择6项有2个error：两个stop子例在原 _manual_binding 提前抛错，实际无新capture/input；夹具原来期待 returned summary，已改为验证该原早拒。测试期间生产源码有编辑，source-set不一致，该报告不用于冻结。
- 两个 stop/control handoff 受影响方法定位2/2，20.062秒。随后返回完整性/失败报告保留2/2，13.695秒。没有运行无关旧冻结类。
- 最终默认固定8项：8 passed，0 failure/error/skip，68.58126304699908秒；33源码、12素材前后一致，loaded_source_closure_missing=[]。精确逐项结果与完整map见 acceptance.json，原输出见 acceptance.tests.txt。测试head仍为父提交，实际测试字节由map绑定，发布后再次逐字节校验。
- 独立只读源码审查提出并修复：恢复报告失败永久占槽；returned但交付水位不完整仍放行；finally读取坏恢复报告覆盖原异常。分别以新当前proof、原失败证据保留、严格结果来源以及次级证据错误记录处理，没有退款自动预算或改原effect。
- 恢复记录是同run/match的历史处置事实；跨epoch读取不携带动作/预算/阶段/出战权限。当前覆盖“恢复后再原CAS/new epoch全场复核”，先换epoch再恢复旧pending的单跳分支未新增测试。不引入epoch继承框架。
- 没有安装新依赖/下载模型、调用OCR引擎/在线模型、启动游戏/broker/GUI、修改ROOT READY或导出私有素材。协议使用原公开裁片、声明完整canvas/OCR行/4→6及球消失后继、惰性Entry传输，不是假称自然连续图。
- Windows同一8项、真实当前恢复→剩余球→全场复核及完整准备节点/整局profile仍由ROOT独立验收，未声称自动化完整通过或速度改善。
