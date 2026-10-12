# 当前金币补读与领奖调用

唯一父为已部署PR27 `d536da965fa42d800b24992aae363246c9f3d67c`。本文件所在候选的完整SHA以草稿PR和最终回复为准。PR24不在父链。本批不操作ROOT游戏、broker、生产源码、READY或安装。

## 结论和未完成的实证

当前原生两球检测通过，不等于领奖。ROOT最新真实记录仍是0领奖／0经济输入，Worker因当前绑定金币未知返回`reward_result`。

本批只修金币读取、原后图金币未知的有界补读，以及全场领奖关闭的未决／明确剩余目标否决。继续使用原Worker／Entry／ManualPhase，未增加控制器、GUI依赖、主管金币补值或新手操操作。当前实际RapidOCR能否把新预处理后的原生4读到≥.90，必须由下面默认Windows选择验证；本环境没有已安装RapidOCR／ORT，不能把声明OCR分数当实际原生识别结果。

本环境自动审批拒绝下载RapidOCR依赖，理由是本次仅允许复用已有组件、该下载不在授权内。没有换下载源或绕过。冻结报告原样保留依赖错误，不skip、不伪填native。ROOT现有本机引擎可直接执行同一driver；未通过8项完整选择前，不称候选满足安装条件。

## 原因与最小改动

实际fetch证据：`root-environment-guard-evidence-20261008` 的 `f6c7588c932cd5406a238267783e0723a7cccb16`。四张未改context PNG逐项SHA和尺寸已核，完整原PNG仅有ROOT给出的摘要，不在本环境。

当前两个49×40数字ROI的原RGB完全相同。4的完整暗字框为局部`[3,6,21,32]`，未触边；不能说这次4被截断。原2倍输入为98×80，留下大量尾部空白；原输出`4 `、.7972828低于.90。有限紧框是待真实引擎验证的输入改进，不是抬高原置信度。

生产`currency_wars_perception.py`现在：

1. 先核当前页面、完整包含且≥.90的“购买经验”“商店／收起”标签、原币标色域及比例。完整高置信数字行可直接使用；跨金币HUD的数字行、互相矛盾的数字行拒绝。
2. 小ROI补读须同时取得同帧原金币控件带`[1558,885,1687,950]`。完整唯一币标的右边界确定数字带，带内暗字须全部包含在原数字ROI内。币标随多位金额移动时，不能截掉一位仍输出剩余4。这个带只做完整性否决，不扩大OCR支持范围。
3. 先保留原98×80识别；未达到.90时，最多追加一次完整暗字紧框＋固定4像素补边＋2倍处理。当前裁片的补读输入为60×76。全部连通域、边缘、间距和底色须满足支持范围；不丢噪点、不强剥问号、不挑出一个喜欢的高分。
4. 低分原数值与补读数值冲突、额外数字、来源不符、字形不完整或补读仍低分，`semantic.coins`保持absent。`semantic.coins_read`保留原文、原置信、每次输入尺寸、原ROI／控件带、几何和拒绝原因；它不是成功字段。

只验证当前单字4及对应控件外观；不声称任意金额、字体、缩放或多位数字均已通过。原完整高分数字行仍保留，当前小框不完整时不能靠小框补读救出部分金额。玩家等级、XP、阵容没有修改。

生产`currency_wars_runner.py`现在：

- 原输入前金币缺失仍拒绝，后图不能补造发布前资格。
- 原收据已完整交付、后图金币暂未知时，复用原持久化最多2次只读预算；下降仍硬拒绝。原交付未知先对账，不发新输入。
- 全场`rewards`复核必须先排除原领奖／腾位pending，以及当前来源明确仍有球／待选奖励。单球`completed`、单球消失均不能覆盖它们。

## 验收入口与来源

```powershell
python -B -X utf8 tools/replay_currency_wars_reward_hud.py --output reward-hud-windows.json
```

默认固定8项：4项原像素／有限OCR协议、1项实际本地RapidOCR读取两份原裁片、3项原Worker消费者。只运行本批选择；没有GUI／Rust或旧冻结类。缺包、模型缺失、原裁片未读出可靠4，均真实error／failure，输出JSON和同名`.tests.txt`，退出非0。

`reward-hud/acceptance.json`保存完整测前后源码／素材map、实际加载源码闭包、全部错误、原像素与消费者端点。两份金额相同且数字ROI字节相同，是同字形的时间复核，不是两个独立金额正例。消费者整帧、OCR/HUD值和移除后图是明确声明的协议，不是拼接历史输入资格。

本环境冻结运行：Linux Python3.12.14，8项／7项通过／0failure／1error／0skip，17.440596990秒；唯一error是缺少`rapidocr-onnxruntime`包元数据，原生OCR尚未运行。报告`ok=false`、`native_ocr_test_passed=false`，没有把整体标成通过。33项源码与17项素材测前后相同，实际加载闭包缺项为空；source-set为`7257e7f377d7d5ebf622e0c313b54a91d503bd0947a76af87dd307c72400f445`。测试当时checkout HEAD仍为父提交，候选字节由源码map和发布后回读绑定，不回填假HEAD。

两份生产源码SHA256：`currency_wars_perception.py`为`ae5daa4ca2e0bfdef4abfbc0dcee2df8c7da4c2a992657c1256c3ccde452fff9`；`currency_wars_runner.py`为`70e51eaae9e86c0b2f9cbf2836e49957d7d3b76d07b50e8944453fa14b6a1b3f`。

本批消费者覆盖：`reward_result`金币absent→原inspect（1个新capture，零物理输入）→新`preparation_strategy`→一球一次输入和原核效→其他球仍见拒绝领空→原native循环处理剩余球→真实生产`finish_manual_phase`／`explicit_resume`／新epoch／旧请求废弃→当前完整`Worker.execute_plan`复核→`startup_guide`。传输是惰性夹具，CLI分发／本机安装／真实游戏未执行。

原始来源完整map在`ROOT_PR27_HUD/diagnosis.json`。四份context原字节随候选复用公开Git blob，新增生产素材为0；原57私有资源没有导出。

## 窄范围读取的准确含义

PR27没有名为`narrow-rewards`的CLI、decide动作或专用reply。内部`Worker.observe(scope='rewards')`是只读能力，但公开`manual-step collect_rewards`调用它之后可真实领奖，整个命令不能称只读。

该scope仍用一遍全页主OCR识别页面／覆盖，继续读取金币和球；跳过玩家等级／XP、人口、HP、商店卡槽、阵容、库存与刷新控件。它不是只OCR金币小框，也不会把旧.797置信抬高。

可以用现有Python接口独立读取当前请求保存的原PNG。先从本机当前`runner-state.json.decision_request`取`$q`，使用本机已审Python：

```powershell
& $py -B -X utf8 -c "import sys,json,hashlib; from pathlib import Path; sys.path.insert(0,'tools'); from currency_wars_perception import Perception; p=Path(sys.argv[1]); assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[2]; r=Perception().read(p,force=True,scope='rewards'); print(json.dumps({'snapshot_id':r['snapshot_id'],'page':r['page'],'stage':r['fields'].get('stage'),'coins':r['semantic'].get('coins'),'coins_read':r['semantic'].get('coins_read'),'read_contract':r['read_contract']},ensure_ascii=False))" $q.original_png $q.snapshot_id
```

这是同一保存图的独立只读诊断，不创建新的游戏capture或输入，不进入Worker history，不可回填旧observation、`context_update.coins`或修改旧qkind。即使读出4，真正输入仍由Worker自己取得并读取当前新帧。

`manual-step inspect`是原有纯只读运行入口，使用full范围；它会发新capture并创建完整准备请求。不要称为窄范围。原ask将窄读升级为同帧full时，可复用主OCR但仍有其他字段读取；本批未改变此合同。

## 当前1-1的最短合法调用

ROOT当前两个native目标都明确，优先不带标注的原循环。以下只使用已认证本机变量，不上传owner／token：

```powershell
$auth = @('--run-dir', $runDir, '--chat-id', $chatId, '--run-token', $runToken)
$runner = 'tools/currency_wars_runner.py'
```

1. 新候选通过独立验收／原绑定后，以原start和business_resume取得当前run／epoch。没有恢复旧请求。需要手操时用原takeover取`$manualId`。
2. 如同一broker仍paused，先完成原Entry显式交接并核原ID成功：

```powershell
$handoffId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 tools/currency_wars_broker_entry.py resume @auth --handoff --request-id $handoffId
```

这只用现有`kind=resume`和实际pause-id CAS，不是raw动作、不清Worker manual intent。已交接时不再重复。

3. 使用当前节点pending rewards checkpoint；没有时创建：

```powershell
$cp = (& $py -B -X utf8 $runner manual-checkpoint @auth --manual-id $manualId --phase rewards | ConvertFrom-Json)
$checkpointId = $cp.checkpoint_id
```

4. 允许脚本领奖时，一次提交原循环：

```powershell
$stepId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation collect_rewards --checkpoint-id $checkpointId --request-id $stepId
```

它先取当前新图窄读，币值与目标成立才逐球一次输入、一次核效；最多8笔，任何未决停止。金币仍unknown时零领奖输入回`reward_result`。同ID查询原结果，不为queued／pending再造新ID。

若只想看新原生币值，或确需主管限定一球，先调用：

```powershell
$inspectId = [guid]::NewGuid().ToString('N')
& $py -B -X utf8 $runner manual-step @auth --manual-id $manualId --operation inspect --request-id $inspectId
```

从新`preparation_strategy`取完整15字段单ROI标注，原样沿`DYNAMIC_REWARD_ROI.md`协议提交。当前旧`reward_result`不能直接用于ROI，也不能只改它的kind。新请求金币未知时仍不能点；ROOT独立实读4不写成native成功，本批没有另开主管金币绕过。

单球必需字段：当前preparation页面、真实stage、绑定HUD金币、当前目标／锚点、snapshot／capture／frame、match／epoch／checkpoint／deadline、原收据及后图身份。**不要求等级、XP、满人口或完整阵容。**

## 实际结果出口与全场关闭

读取原`result.reward_step`的`status/outcome/request_id/after_snapshot_id/verification_reads/pending/record_file`，以及`input_receipt_ids/receipt_states/receipt_delivery_verified`。顶层returned／原receipt completed均不是全场完成。每笔记录和不覆盖的前后PNG仍在原journal的`reward-step-<id>.json`及相邻文件。

单球verified仍返回`all_rewards_cleared=null`、`automatic_phase_completion=false`。临时后金币unknown最多2次只读；持续unknown保留原pending。不得用all_claimed绕过原未决或当前明确剩余球。

全场确实领空后，manual-result只存原checkpoint真实trace，不把旧clear继承到新epoch。沿原resume后，ROOT必须查看新当前完整请求，再通过原`decide --reply-file`提交无输入复核：

```json
{
  "request_id": "<当前请求>",
  "snapshot_id": "<当前完整原图摘要>",
  "resume_epoch": "<当前代次>",
  "actions": [{"type": "finish_preparation_review", "reason": "已核当前全场领奖结束"}],
  "context_update": {
    "preparation_review": {
      "proof": {
        "source": "observed_screen",
        "snapshot_id": "<当前完整原图摘要>",
        "evidence_file": "<当前请求evidence_file>",
        "resume_epoch": "<当前代次>"
      },
      "value": {
        "reviewer": "supervising_agent", "phase": "rewards", "stage": "<当前节点>",
        "completed": true, "all_claimed": true, "rescanned_after_claim": true,
        "findings": "<ROOT当前关闭商店后全场实读，确无剩余球和待选奖励>"
      }
    }
  }
}
```

仅实际满足才写上述true。manual状态不能decide；`manual-step reviewed_plan`禁止context_update，不能用它提交该复核。准备顺序仍为领空→创业指南→清库→经济→布阵装备→ROOT出战验收。

保留限制：非business_resume的纯`finish_preparation_review`仍可能被原全屏指纹变化>.10拒绝，本批没有给它新增动画例外。拒绝为零输入、旧完成不生效，需当前新请求；不能由这条稳定协议声称所有自然动画已通过。

## GUI和部署纠正

PR27变更`currency_wars_runtime_sources.json`，GUI通过`source_guard.rs`的`include_str!`嵌入清单，`gui_fingerprint`也绑定它。因此ROOT此前必须Build-GUI重建，并非“GUI不用重建”；旧交接已在本候选更正。

本批只改已列入清单的两份Python，清单／GUI来源／依赖／生产素材字节没有变化。若ROOT保持已验PR27构建，不因这两份Python再编译GUI；仍须独立审查并重新绑定24源码＋本机实际64资源/provider，新READY不继承旧源码hash。随后原`check_install --check-launch`须通过。没有要求热改生产，也不要求先安装PR24。

本批完整自然端点仍由ROOT确认：新帧实际金币读数→一笔真实领奖→原收据和稳定后图→全场复核。原native等级unknown保留。没有整局速度改善结论。
