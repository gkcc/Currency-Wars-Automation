# 同终点成本实测

基线为完整 PR13，候选包含本轮两处生产修改；两者在同一 Linux 执行环境中使用同一冻结脚本、同一顺序分别运行。每个 case 各一遍冷读和暖读，没有并行 OCR。原始报告列出运行前后源码摘要、模型版本、输入 SHA、逐次读取和嵌套事件。

**结论：两个真实领奖消费者窗口各少跑一次主 OCR，终点必要事实、回传和 pending 保持一致。本次两组暖轮总窗口分别省 0.8313 秒和 0.3699 秒；这些是单次本地窗口差值，不是稳定倍率或整局收益。**

## 计时边界

从第一次 Worker.observe 开始，到末次请求 JSON 和证据图写出、发布替身进入等待状态。包括窄读、full 升级、业务规则、经济 ROI、回执检查、图片／请求 IO 与惰性传输。`ready_fixture` 的 worker.publish/log 是内存替身，生产 status/journal 发布未测；也没有 ROOT 模型、ROOT 响应、游戏动画／玩家等待。`.7` 只是原请求参数。全部真实窗口的普通完整结果 cache_hit 均为 false。

每个 case 的冷轮用新 Perception；暖轮仍用新 Perception/Worker、空语义和原始证据缓存，仅继承该 case 已初始化的模型与 side reader。cold 指该读取器初始化，不表示操作系统页缓存或依赖导入也冷。引擎初始化已包含在冷轮总数中，不能再加一次；进程依赖导入另列在 JSON，基线 0.2106 秒、候选 0.1209 秒，不混进窗口。

环境：Linux x86_64；Python 3.12.14；onnxruntime 1.30.0；RapidOCR 1.4.4；NumPy 2.3.5；Pillow 11.3.0；OpenCV 4.13.0.92。公共检出没有 ROOT 的完整私有商店／阵容资源。本轮 q 图不要求虚构商店完整事实；此处 ShopReader 内部 OCR 为 0，不能外推 Windows 商店成本。

## 真实 PNG 的相同消费者窗口

| 窗口 | 冷／暖 | PR13 总秒 | 候选总秒 | 本次净省秒 | 主 OCR 次数 | 完整结果缓存命中 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| q02 → 奖励选择请求 | cold | 2.8255 | 1.4210 | 1.4045 | 2→1 | 0→0 |
| q02 → 奖励选择请求 | warm | 1.9329 | 1.1016 | 0.8313 | 2→1 | 0→0 |
| q00→q01 → 原回执异常请求 | cold | 4.8587 | 4.7666 | 0.0921 | 4→3 | 0→0 |
| q00→q01 → 原回执异常请求 | warm | 5.2715 | 4.9017 | 0.3699 | 4→3 | 0→0 |

q02 两次 Perception 调用：rewards → full。终点为 rewards 阶段的 reward_selection，正常 1／异常 0，pending false；没有输入发布，也没有证明奖励完成。q00/q01 四次调用：q00 full → 重定位 q00 rewards → 惰性后帧 q01 rewards → 同请求 q01 full。终点为 reward_result，正常 0／异常 1，pending true／outcome unknown。原 completed 保持原坐标和 wait；只模拟一次发布，未把 q01 当成新执行成功。

这两个窗口彼此独立。q02 不是 q00/q01 拒绝后的自动续跑，也没有两颗蓝球之间的独立截图／回执；不能串接为“完整领奖”。

### 暖轮的互斥组件

以下各桶只算自身区间一次，可相加得到相应 worker_method_seconds。完整父区间不能再次相加。单位秒，列为 PR13→候选。

| 互斥组件 | q02 | q00/q01 |
| --- | ---: | ---: |
| 引擎初始化 | 0.0000→0.0000 | 0.0000→0.0000 |
| 主 OCR | 1.5626→0.7743 | 4.4038→3.9925 |
| 字段 OCR | 0.0164→0.0132 | 0.0533→0.0422 |
| 商店内部 OCR | 0.0000→0.0000 | 0.0000→0.0000 |
| 商店其他 | 0.0000→0.0000 | 0.0467→0.0509 |
| StateReader | 0.0406→0.0410 | 0.0868→0.1006 |
| 奖励规则 | 0.0287→0.0326 | 0.0395→0.0430 |
| Worker 规则／策略及其内联记账 | 0.0027→0.0029 | 0.0075→0.0076 |
| 惰性帧／回执发布 | 0.0017→0.0017 | 0.0050→0.0050 |
| 其余读取、Entry、证据／请求 IO | 0.2802→0.2360 | 0.6288→0.6599 |
| 协议查表 | 0.0000→0.0000 | 0.0000→0.0000 |

主 OCR 次数下降是实际引擎调用轨迹；主 OCR 每次自身没有变快。q00/q01 候选其他三次 OCR 的波动吞掉了部分被删升级成本，因此总净差必须用完整墙钟比较，不能直接把升级的差值称作窗口净省。StateReader、HUD 与奖励重派生仍付成本，字段保持原 unknown。

### 同请求 full 升级本身

| 窗口 | 冷／暖 | PR13 升级 ms | 候选升级 ms | 升级主 OCR |
| --- | --- | ---: | ---: | --- |
| q02 → 奖励选择请求 | cold | 1016.51 | 112.81 | 1→0，原始证据复用 |
| q02 → 奖励选择请求 | warm | 1033.71 | 112.90 | 1→0，原始证据复用 |
| q00→q01 → 原回执异常请求 | cold | 789.89 | 156.64 | 1→0，原始证据复用 |
| q00→q01 → 原回执异常请求 | warm | 883.23 | 155.29 | 1→0，原始证据复用 |

该升级行包含在上面总窗口内，不能额外相加。候选每个升级均 cache_hit=false、primary_ocr_reused=true、primary_ocr_executed=false。这里只复用了原始文字证据，完整语义确实重新读取。force=True 的实际主调用保留由聚焦声明 OCR 测试核验；本轮没有另测候选 force 的真实模型耗时，也没有将其称为性能结果。

## 六 D／三 F 的协议终点

这两组使用生成数值帧、已核空商店／预算前提与 matched 惰性回执；不是原七张刷新 PNG 的真实 Worker 闭环。所有查表耗时单列，真实 OCR 调用为 0。各两遍的 cold/warm 仅为脚本顺序标签，inference_temperature=null。

| case | 读取轨迹 | 请求与终点 | 协议实记 | PR13 秒（第 1／2 遍） | 候选秒（第 1／2 遍） |
| --- | --- | --- | --- | ---: | ---: |
| six_d_protocol | full×2＋economy×6＋full升级 | 非异常 2／异常 0；lineup_equipment，pending false | 刷新 12 | 0.8383／0.8274 | 0.8714／0.8141 |
| three_f_protocol | full×2＋economy×3＋full升级 | 非异常 2／异常 0；lineup_equipment，pending false | 经验 12 | 0.5036／0.5362 | 0.4652／0.5118 |

两次请求均在窗口内：初始 economy_budget 与最后 lineup_equipment。后者请求类型 shop_strategy，原分类仍 unclassified；“非异常 2”不能写成两次已显式标注的 strategy。经济阶段完成，布阵尚未验收，battle_ready false。旧 replay 的 1 是较短终点，不是本补丁增加了接管。六笔原历史 D 回执只作关联，原 wait(.4) 与 matched 惰性 wait(.7) 分开保存；没有把它们改成生产成功。协议计时只核覆盖和边界，不能支撑 OCR 加速结论。

## 聚焦验收与最小 Windows 复核

本轮只执行新增 3 个 raw 证据测试及 4 个受影响原方法：同 PNG 的语义缓存隔离、ROOT 同请求完整升级与旧身份拒绝、D/F 后图与只读恢复及购牌完整消费者、奖励末次全场交接。合计 7，0 failure/error/skip；未扩跑历史 50/15/58 或 PR13 原全套。实际日志见 [focused-tests.log](focused-tests.log)，源摘要见 [FOCUSED_ACCEPTANCE.json](FOCUSED_ACCEPTANCE.json)。

Windows 在独立候选根目录、原已安装依赖下运行；selector 一律不加 tools. 前缀，明确 PYTHONPATH 指向该候选 tools。

```powershell
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'tools')
$selectors = @(
  'test_currency_wars_primary_reuse',
  'test_currency_wars_perception_scope.PerceptionScopeTests.test_same_png_scope_cache_and_returned_nested_objects_are_isolated',
  'test_currency_wars_perception_scope.WorkerReadScopeTests.test_root_request_upgrades_the_same_current_png_and_refuses_missing_or_old_identity',
  'test_currency_wars_perception_scope.WorkerReadScopeTests.test_free_d_and_f_keep_economy_scope_for_result_and_read_only_frame_recovery',
  'test_currency_wars_perception_scope.WorkerReadScopeTests.test_reward_results_keep_narrow_scope_until_root_full_field_review'
)
python -B -X utf8 -m unittest -v @selectors
```

同终点真图对比使用候选中同一份脚本，分别传入独立 PR13 基线和候选目录；两处都覆盖相同原 57 资源并保持生产源码不变。两个命令是独立进程，固定相同 case 顺序，每个 case 自动冷／暖各一遍。目录变量按本机实际填写，输出写入此次临时检出或独立结果目录。

```powershell
$baselineRoot = 'C:\offline-review\pr13'
$candidateRoot = (Get-Location).Path
$scriptPath = Join-Path $candidateRoot 'tools\replay_currency_wars_worker_read_cost.py'
python -B -X utf8 $scriptPath --code-root $baselineRoot --cases q02_selection_request q00_q01_original_completed --output worker-before.json
python -B -X utf8 $scriptPath --code-root $candidateRoot --cases q02_selection_request q00_q01_original_completed --compare-to worker-before.json --output worker-after.json
```

经济消费者契约如需独立核对，只把上述两个命令的 cases 改为 six_d_protocol three_f_protocol，另用 protocol-before.json / protocol-after.json；不要把声明数值 fixture 称为原七图模型回放。脚本含全部调用轨迹，不需要另造观测包装器。

## 剩余证据

- Windows 原资源下的同终点收益待 ROOT 独立实测；此处缺完整商店资源，ShopReader 内 OCR 为 0，不能迁移比例。原本机七商店 shop_ok=true 的报告继续有效。
- 原七图完整六 D Worker 回放仍缺可追溯的初始免费次数／刷新费用数值 ROI 和预算绑定；付费回执不能自动补 free_refreshes=0。ROOT 有原资源，当前不要求导出全部素材；若以后在公开环境复现，再按明确拒绝的 shop 资源最小交接。
- 发布前 main `19e997d` 已给出 [q01/q02 位置与星符人工标签](../ROOT_RETAINED_CARD_LABELS.json)，每帧可见八张卡牌，实名未标、其他槽位不补。已核两图 SHA；本轮测量未使用这份新标签，也没有重跑 OCR。无需重复提供这类标注，后续仍需可确认的实名及实际“哪个未知阻止哪个消费者终点”的请求／恢复轨迹，才能量化减少接管的收益。
- 两球间缺独立后图，奖励未证明全清；完整备战、整局、当前 4K 游戏坐标以及观察后异步弹窗均未验证。
- 模型／玩家／主管等待仍未知；Jev 和视觉模型均未接入或实测。

## 原始材料

- [PR13 真图窗口](pr13-real-worker.json)／[候选真图窗口及对比](candidate-real-worker.json)
- [PR13 协议窗口](pr13-protocol-worker.json)／[候选协议窗口及对比](candidate-protocol-worker.json)
- [源码绑定与验收汇总](NET_COST_ACCEPTANCE.json)／[先验窗口设计](WORKER_COST_DESIGN.md)

四份报告使用的脚本 SHA256：`2eb1517d13e4b0d8c7110c51f2798be44b36303485d3ce34d6e618ec27070d08`。运行时 HEAD 尚为 PR13，候选生产修改由各报告 source_hashes/source_hashes_after 绑定，发布后再核 Git tree。没有重写原始 JSON 的计时或用提交后的 HEAD 替换运行身份。
