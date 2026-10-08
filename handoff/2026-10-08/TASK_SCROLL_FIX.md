# 创业指南任务列表局部滚动守卫修复

## 交付结论与冻结依据

本补丁只以 d2477df7c5620fe606340e068642bf684445a5fb 为父基线；它是 PR31 后继，不重做 PR29/30，也不重测已验收的原生导航。交付形式为完整二进制 Git patch 和逐文件 SHA256 清单，没有声称已推送候选、发布 PR、安装或部署。下一步由 ROOT 独立复核、Windows 验收、安装并验证真实滚动。

已实际读取该父提交中的 runner.check_target_roi、Worker/Entry 发布链及当前手操调用入口。已读取证据提交 0e43fadea064a52141fdd3b322ba4a8b14b04a62 中的 diagnosis.json、两张原生裁片、ROOT_PR31_INDEPENDENT_REVIEW.json、ROOT_PR31_DEPLOYMENT.json 及其保留的 ROOT_PR30_RETRY_FIX_WINDOWS_ACCEPTANCE.json；上一验收清单的 37 源码和 19 素材逐项与父提交字节匹配，无缺项。

原合法接口是所属 Worker 的 manual-step / reviewed_plan。普通 scroll 可携带当前唯一文字、完整 ROI 和 ROI 内输入点，但父提交最后仍要求目标 RGB 逐字节相同。因此，[1460,324,1512,359] 中心 [1486,341.5] 的合法“0/2”计划对本次原生配对仍会被拒绝；没有可受检使用的现成动画容忍参数。最初 missing text、point-outsideROI 两个坏计划分别是调用者构造错误，并非该守卫缺口的证据。

当前 runtime 已清理，历史 request/capture/frame/epoch 不能带入新运行。ROOT 验收后按原机制正常 start / business_resume，同一局当前创业指南阶段的新请求才可生成计划；不需要为本次滚动重新执行完整准备扫描或原第二图标导航。历史 unknown 档案不改写。

## 局部契约

本次只修改三个生产文件：currency_wars_visual_guards.py、currency_wars_runner.py、currency_wars_manual_steps.py；其余新增项是两份消费者入口、调用示例、说明和原生测试素材。

| 条件 | 本次实现 |
| --- | --- |
| 动作范围 | 单个 reviewed_plan，唯一 scroll，滚轮量仅 -120 或 +120，无 context_update；标记 startup_guide_task_list |
| 当前业务归属 | 原生 page 和 expected_page 都必须为 unknown，当前 request.kind 为 unknown_page；同 match、stage、epoch、manual owner，pending checkpoint 与 Worker 当前 phase 均为 startup_guide |
| 不可转用 | 标记不能批准 D/F、经济、战略选择、点击、拖动或出战；普通无标记滚动保留原逐字节守卫 |
| 来源 | 原 request、snapshot、capture、frame、完整 PNG SHA；实际复核帧必须来自同 Worker 的新 capture/frame 和原 Entry 收据 |
| 目标 | 当前原始完整 OCR 行，不准扩大 ROI；唯一完整文本，置信度沿用局部唯一行的 0.90 门槛，点须在 ROI 内；目标宽不超过 800、高不超过 100 |
| 页面与覆盖 | “创业指南”和当前准确章节行均唯一，原生识别结果仍为 unknown；固定锚点框不动，沿用已有弹层否决并核其完整像素与真实轮廓 |
| 目标输入前几何 | 新旧目标文字和完整框必须相同；不能以低幅阈值容忍目标移动 |
| 局部像素上限 | 完整 RGB ROI 同时满足 max_abs_rgb ≤ 4、mean_abs_rgb ≤ 0.06、changed_fraction ≤ 0.06；RGB 差值单位为 0–255 通道值，没有除以 255 |
| 发布边界 | 仍经同 Worker、GuardedSubmission 和原 Entry 提交租约；实际发布前再次核归属、局部证据和期限，记录 I/O 后再核期限 |
| 动作后条件 | 原 Entry 必须确认该唯一 scroll 完整交付；同一唯一文字宽高和横坐标不变，纵向位移绝对值 2–240，方向与滚轮量相符；移动后的对应 RGB ROI 再核同一上限 |

这些阈值仅围绕已提供的原生反例设置，没有修改全局 dHash、全局像素/置信度或关键动作守卫。标题/章节复用相同的保守局部界限，真实完整标题与章节尚待 ROOT 实机确认；公开裁片不足以保证它们必然通过。

任务文字源帧与当前帧的实测为：changed_fraction = 0.053296703296703295，mean_abs_rgb = 0.050183150183150185，max_abs_rgb = 4。两张裁片为原样 52×35 PNG。仅凭这些数值不能判断是抗锯齿、动画还是其他渲染因素。

正常路径复用原 scroll + wait 返回帧，额外复核读取为 0。仅返回帧缺失且原输入完整交付已核实时，最多额外 observe 一次，并在调用前将 verification_reads 记为 1；不重复滚动。输入前的新观察仍必须执行，不以旧帧代替。

## 调用端预检与结果

新 reviewed_plan 在 manual_steps.submit 创建 job 之前调用生产 validate_plan；原 decide 入口也继续使用同一校验器。普通 scroll 和标记 scroll 的 missing target_evidence.text、非有限坐标、非法滚轮量及 point-outsideROI 会先拒绝，不创建手操 job，不触发 Worker 观察或暂停。实际输入前的守卫仍独立执行。

同一 step ID 重查优先返回原 job，不会因为新请求或期限变化要求重新提交。给同一原决策请求更换 step ID、文案或章节声明也不能再发标记滚动。下一次人工决定滚动必须使用新当前观察所产生的新请求。

| task_scroll.status / outcome | 含义和后续 |
| --- | --- |
| verified / 位移已核实 | 核实同一文字发生有向位移；不等于已到底、奖励领空、章节完成或任务完成 |
| pending / 效果未确认 | 输入可能已经交付，但无位移、反向、文字消失/变化、覆盖、帧或来源不足等未满足后置条件；保留原证据，交 ROOT 检查，不自动重发 |
| refused / 未发布 | 输入发布前受检拒绝；读本步错误与原记录，不换 ID 重放旧请求 |

结果沿原 manual-step 返回，在 result.task_scroll 中含 input_request_id、status、outcome、shift_y、record_file、verification_reads、input_resent=false、automatic_phase_completion=false。原始受检前帧和结果位于所属 records/task-scroll-<decision_request_id>-before.png 与同名 JSON。早于 begin_task_scroll 的拒绝可能没有这份任务滚动记录，原 manual-step 错误及收据仍为依据。

## Windows 最小验收入口

在隔离目录确认 HEAD 精确等于唯一父提交，保持该隔离目录干净。只给 Git 进程指定行尾策略；不改全局 autocrlf，不改补丁。

    git rev-parse HEAD
    git -c core.autocrlf=false -c core.eol=lf apply --check --index --binary --whitespace=error <patch绝对路径>
    git -c core.autocrlf=false -c core.eol=lf apply --index --binary --whitespace=error <patch绝对路径>

原 evidence diagnosis.json 为 CRLF。其目录新增的局部 .gitattributes 只对这一原始 JSON 指定 -text -diff，使二进制补丁保留冻结 SHA；PNG 也按二进制保存。源代码和脚本保持交付的准确字节。应用后按包中 manifest.json 比对全部变更文件 SHA256。

使用 ROOT 已有、依赖齐全的 Python，仓库根为当前目录：

    & $py -B -X utf8 tools/replay_currency_wars_task_scroll.py --output $acceptanceJson
    if ($LASTEXITCODE -ne 0) { throw 'Task scroll acceptance failed' }

此入口只执行 10 个精确的新消费者方法，覆盖 26 个声明场景（其中 23 个 unittest subTest）；不能把 26 当作顶层测试个数。它使用真实 manual_steps.submit/process、Worker、GuardedSubmission 和 Entry 租约/收据消费者，底层读帧和输入发布边界为不向游戏发送动作的 fixture。

在实际资源完整目录，可追加 --require-runtime-resources，要求生产 resource_snapshot 与 provider 的绑定也成功。报告记录实际资源 SHA 和数量，不预填 64。此次 Linux 源码目录缺少 tools/shop_reader_resources，报告明确 runtime_resource_binding_complete=false；这不替代 ROOT 的实际 24 源码/64 资源/provider 及安装检查。没有修改 runtime 源码清单、资源/provider、GUI 或旧 check-launch 实现；是否通过新候选的 Windows/安装门仍由 ROOT 判定。

### 已执行的本机结果

- 10/10，failure=0、error=0、skip=0，12.400245427 秒。
- 28 源码，其中 24 个生产清单路径；3 份冻结原生证据。测试前后 SHA 相同，实际载入源码闭包 missing=[]。
- source_set_sha256：886f329c479100c41168a9e919107b6e87a687351a1be120c7f44f2dda4a8803。
- 覆盖低幅正例及实际位移、输入前目标移动/覆盖/变字/弹层、错原图/capture/frame、排队后及 Entry 锁内 phase 失效、请求/手操期限在最终局部守卫期间跨越、动作后无位移/反向/变字/覆盖、缺图一次只读补帧、调用端坏 payload、关键动作挪用标记、普通滚动仍严格拒绝。
- 另一次代码独立复核与定点越期消费者报告随包提供；这不是 ROOT 的下一轮独立 Windows review。

原生部分仅两张未编辑任务裁片；完整 1920×1080 画布、标题/章节 OCR、历史档案及滚动后继是声明 fixture。没有新游戏截图、原生 OCR、真实游戏输入、控制器启动、Windows 执行、安装、真实滚动验收或整局提速结论。生产三文件和消费者文件的准确 SHA 绑定在报告及交付 manifest 中。

## 当前所属接口的精确调用示例

以下由 ROOT 在正常 start / business_resume 和安装门通过后使用。$py、$chatId、$runDir、$runToken、$manualId、$checkpointId 来自当前所属运行与 pending startup_guide 手操阶段，不是已清理 runtime 的历史值。沿用原受检手操机制，不创建第二控制器。

物理步骤还要求所属 broker 已按原合法交接流程完成显式 handoff，处于 ready、game_foreground、paused=false、input_halted=false。pending checkpoint 本身不会恢复 broker；若仍暂停，先由 ROOT 完成原交接，再重新取得当前绑定和请求，不先排物理 job 等待超时。当前手册不替代原 handoff 的守卫参数或批准过程。

    $cwAuth = @('--chat-id', $chatId, '--run-dir', $runDir, '--run-token', $runToken)
    $requestFile = Join-Path $runDir 'decision-request.json'

如果当前还没有可用请求，或已有请求已过期，原接口的一次 inspect 可取得同 Worker 当前帧；它不触发整套准备流程。为该 inspect 保存一个固定 ID，重复查询保持这个 ID。

    $inspectStepId = [guid]::NewGuid().ToString('N')
    & $py -B -X utf8 tools/currency_wars_runner.py manual-step @cwAuth --manual-id $manualId --operation inspect --request-id $inspectStepId

从当前请求列出实际 OCR，只取其中完整且唯一的章节行。不要根据公开的“章节4”摘要臆造精确文本：

    $q = [IO.File]::ReadAllText($requestFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
    $q.observation.rows | Select-Object text, confidence, box | Format-Table -AutoSize
    $chapterText = Read-Host '复制当前OCR中完整且唯一的章节行文本'

生成器默认选择当前唯一“0/2”，自动使用该真实行完整框的中心。它不固定旧坐标、不读取旧 frame ID、不改 page；在生产 validate_plan 通过之前不创建输出文件，输出文件已存在则拒绝覆盖。PowerShell 脚本全部为 ASCII，中文常量由嵌入 Python 的 Unicode 转义表达，兼容旧 PowerShell 对无 BOM 文件的解析。

    $replyFile = Join-Path $env:TEMP ('cw-task-scroll-' + [guid]::NewGuid().ToString('N') + '.json')
    $cwBuildArgs = @{
        Python = $py
        RequestFile = $requestFile
        ChapterText = $chapterText
        OutputFile = $replyFile
        TargetText = '0/2'
        Delta = -120
    }
    & ./handoff/2026-10-08/New-TaskScrollReply.ps1 @cwBuildArgs
    if ($LASTEXITCODE -ne 0) { throw 'No plan was submitted' }

若当前文字行确实还是 ROI [1460,324,1512,359]，生成 args 正是 [1486,341.5,-120]；如框已变，必须以新请求实际框为准。生成文件供 ROOT 检查，内容中的 request_id/snapshot_id/resume_epoch 和 source/capture/frame 均从当前请求复制。

随后只提交这一笔，并保存 ID；超时、待确认或重复查询都复用同一个 ID 和同一份 reply：

    $scrollStepId = [guid]::NewGuid().ToString('N')
    & $py -B -X utf8 tools/currency_wars_runner.py manual-step @cwAuth --manual-id $manualId --checkpoint-id $checkpointId --operation reviewed_plan --reply-file $replyFile --request-id $scrollStepId

需要只读查询时，重复最后一行，不能重跑生成 ID 那一行。页面始终保留原生实读结果：成功滚动仍为 unknown；实际识别到其他页时后置条件不通过，不强制改回 unknown。ROOT 从返回帧检查末尾领取状态；本补丁不会将当前 6/7 或“累计使用2次特权赋予卡升级进阶装备”0/2 改写为完成。

## 通用实践取舍

- Playwright actionability 将唯一性、可见性、稳定几何和输入点是否接收事件分别检查，支持把像素噪声与控件身份分开核验的做法：https://playwright.dev/docs/actionability 。
- pixelmatch 分开描述单像素色差、差异像素数量及抗锯齿处理，说明不能由“非零变化比例”直接推断控件已换，也不能直接据此认定变化原因：https://github.com/mapbox/pixelmatch 。
- Playwright mouse.wheel 说明调用返回不保证滚动已完成，支持保留实际位移后置核验：https://playwright.dev/docs/api/class-mouse#mouse-wheel 。

这些资料只帮助补足现有局部守卫与后置条件，未引入浏览器自动化框架，也未照搬第三方阈值。当前严格几何、唯一文本和颜色上限仍可能拒绝本次公开素材之外的渲染变化；届时应保留新的原生配对供复核，不自动扩大容忍或重发。
