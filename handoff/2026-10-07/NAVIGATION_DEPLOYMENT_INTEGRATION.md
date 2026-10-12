# 导航新鲜度与一次整合升级

## 结论和来源

本批从完整 PR17 `290b9b9006309c02f2542931d97eb5f8b0a045b1` 开发，实际再次 fetch 的 main 为 `4cf23830e7832ee68b4ef40ba688b006ee3064fc`。独立分支为 `integrate/navigation-source-runtime-20261007`，草稿目标直接是 main，包含此前完整修复栈；不从 main 的旧核心重做，不要求 ROOT 依次合并旧草稿。本交接不表示已合并、安装或 Windows 验收通过。

ROOT 报告的活动卡与竞争对手页“下一步”动画误拒，在 PR17 也没有相应局部导航分支。因此只安装 PR17 不能宣称解决这两个入口。另一处部署缺口也成立：旧 GUI 的静态 CORE_FILES 漏了刷新模块，源码更新互斥锁本身不能证明遗漏文件的字节。

本批代码解决上述两个契约；真实入口命中仍缺原双帧。PR9–16 冻结验收复用，PR17 的 Windows 独立验收尚未进行。真实导航、Windows Rust 编译和本机运行根验证分别保留，不把 Python 协议通过写成生产通过。

## 导航改动及准确边界

`currency_wars_visual_guards.py` 新增两类单个文字导航资格，`runner.execute_plan` 在全图差异判断前强制调用。即使 dHash 差异小于等于 0.10，也不能省略这两类目标的局部检查。全局阈值没有放宽；商店购买、D/F、领奖与出战入口没有改成这条导航路径。

| 目标 | 原请求前提 | 两帧须实际读到的锚点 | 本批布局范围 |
| --- | --- | --- | --- |
| 下一步 | opponents / opponents_strategy | 竞争对手 | 标题在上部、按钮在右下部 |
| 货币战争 | unknown / unknown_page | 星际和平指南＋宇宙纷争，或星际和平指南＋逐光捡金，或旅情事记＋常驻活动 | 标题、菜单、内容卡各有明确范围 |

这些范围是**生成图协议的支持域，不是留存原图标定结果**。尤其 unknown_page 只说明尚未分类，不能单独证明是活动卡。没有所列原生锚点、目标落在范围外或其他未知布局，继续拒绝并回传。支持范围不是对所有同名说明页面的完整排除证明。

目标与锚点必须在两帧唯一、置信度至少 0.90；OCR 框最多两像素定位抖动，实际文字在固定坐标比较，不用平移模板搜索掩盖位移。黑字和白字均比较字形及原 RGB，不能只凭灰度归一化相似度通过变灰或遮挡。目标还比较整个文字框扩两像素的暴露状态，以及实际点击中心的 9×9 区域，避免空白点击中心被覆盖而字形未变的漏口。

复用两帧已有 OCR 行检查页面可见语义：0.72 及以上行参与变化否决，不用于降低目标授权置信度；除既有底部 UID/延迟区外，增加、删除、改字或移动的可见语义会拒绝。两帧还经现有 classify 重判。没有新增全图 OCR、模型服务、轮询等待或输入者。

强制识别阶段也捕获能匹配保留控件的非精确子串，批准仍限精确匹配。`validate_plan` 补齐文字动作最小字段约束：目标必须为非空字符串，exact 必须为布尔值或省略为 True，拒绝空子串、0/None 等隐式匹配旁路。

通过视觉资格后，仍走当前 `click_text → command → Entry`：当前定位、原请求/帧、epoch、请求身份、前台、pending、CAS、原 receipt 及 ROOT 出战审批均保留。它仅处理 ROOT 已选定的一个导航动作，不代选难度、阵容、攻略或出战。

### 覆盖与缺口

新增导航测试使用生成 PNG、声明 OCR 和惰性 publisher，实际经过生产 observe/ask/execute_plan/click_text/command/Entry。大背景动画正例只接到一个 click＋wait，结果仍是 outcome_confirmed=False。负例区分目标/锚点位移、字变、页面变化、新模态文字、局部遮挡、变灰、重复或低置信、错误布局、子串/坏类型、缺帧和多动作；发布前另核 epoch、原领奖 pending、前台和请求变化。原未知效果不会因这条导航资格变成已完成。

公开留存没有上述两个导航入口的原始连续配对。`boss_level_90_intro.png` 是“强敌来袭／点击空白处继续”，不能充作“竞争对手／下一步”正例。完整按钮边界、其他区域无字模态和不可观察的透明覆盖尚未证明；不能把局部文字框覆盖测试说成通用遮挡检测。

最小后续实证是**每个入口各一组既有配对**：原 decision-request、原 reply、原 PNG，以及没有发布动作时的当前 PNG/原生 observation；保留实际 capture/frame 身份，并由 ROOT 核实际目标、点击中心和使用的锚点。无需新游戏输入、完整流程截图、全阵容或私有素材。已有文件若缺失，保持未验，不拼接别的页面或补造身份。

冻结 driver 支持仅本机文件的视觉回放：

```json
{
  "schema": 1,
  "request_json": "decision-request.json",
  "reply_json": "reply.json",
  "current_observation_json": "current-observation.json",
  "original_png": "request.png",
  "current_png": "current.png"
}
```

```powershell
.\.venv\Scripts\python.exe -B -X utf8 tools/replay_currency_wars_navigation.py `
  --pair-spec <本机配对描述文件> --output <本机紧凑结果文件>
```

driver 只导出摘要和资格，不导出 rows、路径、owner/token。它验证 PNG 字节/指纹并调用生产视觉守卫，原身份字段缺失仍缺失；`native_capture_authentication_replayed`、`current_epoch_pending_foreground_verified`、`navigation_outcome_verified` 始终 false。`byte_bound_visual_eligibility=true` 不是原生 capture 认证、有效 receipt 或动作成功。两张现有图也不构成整段导航或交易链。

## 部署来源清单

唯一清单是 `tools/currency_wars_runtime_sources.json`，覆盖当前 22 个生产本地 Python 源文件／依赖声明，包括刷新模块、徽标消费者、奖励读取、launcher、进程辅助和来源/更新模块。AST 检查入口的本地导入闭包，包含函数内惰性导入；出现未列入的本地依赖会拒绝。`source_guard.verify_runtime_sources` 供正常 runner 入口使用，Rust 使用同份清单和同样的绑定结构。

| 来源 | 实际绑定 | 变化后的行为 |
| --- | --- | --- |
| 本地生产模块与清单 | 每文件 SHA256，清单自身也在集合中 | 缺文件、少摘要、集合或字节变化拒绝 |
| GUI | 清单编译内嵌值＋既有 GUI 构建来源/二进制摘要 | 旧构建或盘上清单不一致须重新构建 |
| shop/state 本机素材 | SOURCES、names、manifest 声明资产及实际目录成员 | 缺声明文件、新增/删除、字节变化拒绝 |
| 公开刷新/奖励素材 | manifest 及被引用图字节 | 缺失或变化拒绝 |
| 可选上场/战利品素材 | 当前存在/缺失及实际成员 | 新增/删除改变原绑定；缺失不代表功能已就绪 |
| 实际 artifacts provider | 本进程选中的来源、路径、SHA256 | 与原审查或当前字节不符拒绝 |

provider 来源由 GUI 同一次认证的启动登记传递，不能在认证后另读一份登记来取值。broker 与外部 provider 执行刚校验的同一份源字节，避免再由 loader 换读或使用旧 pyc。

本机 57 素材及 provider 私有路径只进入**本机 READY**，本批没有导出。公开缺素材不推断 ROOT 缺失。此清单核的是当前项目声明闭包与选中资源，第三方 Python 包继续沿既有 requirements 固定版本和安装记录验证；没有宣称对全部第三方 DLL、模型文件或已载入模块内存逐字节鉴证。

普通 Setup/check_install 可以报告来源未就绪而完成依赖/构建步骤；`check_install --check-launch` 要求来源绑定有效。GUI 可打开显示开始/恢复的阻塞原因，并保留暂停、接管、停止。正常 GUI 开始/恢复与公开 runner start/worker 检查完整来源及归属审查；紧急 CLI 命令没有新增 READY 门槛。

## 已验证运行根的传递

沿用原输入组件的批准根；本批没有安装或改写固定任务，不改全局环境。launcher 必要时仅用验证后的 TEMP/TMP/TMPDIR 重执行一次新 helper，使 provider 在正确环境初始化；正常 start 派生 worker，以及同权限 broker 派生进程都复用同一已验证根和安装身份，派生前再核变化。

这里修的是 GUI／正常 start／派生 broker 的入口链。直接从已缓存 C 盘 TEMP 的旧 Python 进程调用 CLI 控制 D 盘运行，仍可能被 installed provider 的原根规则拒绝；隐藏 _worker 也不应手工从这种旧环境调用。ROOT 原有“只给新子进程配置兼容目录”的方式继续适用，不把任何 C 环境调用都写成已修。

## 聚焦验收与一次升级路径

冻结选择和全部源码摘要在 [acceptance.json](navigation-integration/acceptance.json)，完整文本在 [acceptance.tests.txt](navigation-integration/acceptance.tests.txt)。最终 Linux **16项通过、0失败/错误/skip，30.442522722秒**，源码测前后相同。选择为新导航 5 项、新来源/运行根 8 项，以及受共享 runner/env 入口影响的原 3 项；没有重新跑 PR16 的 20 项或所有旧套。

第一次聚合的旧 root-routing fixture 只声明了 D-fixed-root，不带实际 provider 协议的 codex-agent-workflow 末级，因此被新子进程环境守卫正确挡在 Popen 前。该失败及处理记录公开在 [development-check.json](navigation-integration/development-check.json)。只把该受影响 fixture 改为完整声明根并断言子进程三个环境变量，没有放松生产根验证。最终结果以冻结 acceptance 为准，不比较失败短停点与最终完整终点的速度。

本环境没有 cargo/rustc。Rust 仅完成源码审查，**没有编译通过结论**。Windows SID/ACL、真实固定任务、D/C 环境以及导航真实双帧仍由 ROOT 验；PR17 Windows 也仍待验，不把 PR9–16 历史通过外推到它。

### ROOT 的最小操作顺序

1. 本轮打完后，按原身份确认旧 worker、broker 及所属子进程退出；保留业务检查点、原 receipt 与未决台账。未知效果先对账，不能为升级删 pending。现有 source-activity 更新守卫不变。
2. 独立检出本批完整候选，用原素材做本机复核，不复制 pyc。就地切换生产前，只清该仓库 tools/gui 下的应用 `__pycache__`，保留 `.venv`、素材、docs/debug 与固定任务；随后使用新进程。`-B` 只禁止写 pyc，不能把磁盘新源码摘要冒称所有旧内存代码已经更新。
3. 执行下面本批聚焦选择，另补 PR17 交接所列尚未完成的 Windows 检查。已有 PR9–16 证据复用。Rust 两项通过后用 `gui/Build-GUI.ps1` 构建，保留现有依赖；requirements 与生产 main 逐字节相同，只有本机安装记录或实际版本不匹配才需要 Setup 重装依赖。**另按下面的固定组件摘要表核实际安装，不能漏掉早期 B-001 的一次组件升级。**
4. ROOT 完成真实独立审查后，将本机 source binding 片段写入真正的 RUNNER_READY，并保留真实 owner/chat/审查者与时间。绑定函数不生成 PASS，不能复制旧 ready=true 作为新批准。用 `check_install --check-launch` 核部署前提。随后把**这一份直接针对 main 的整合候选**按 ROOT 的切换安排推进到生产；无需逐个安装旧草稿。
5. 从正确子进程环境或更新 GUI 使用唯一原 broker，ROOT 自行核首个真实导航及原 receipt，再继续常规玩法。出战仍由 ROOT 每次鲜帧验收。本批没有代做这些实操。

### 从旧生产版本升级，固定组件不能只看本批 diff

本批 control/bridge-task 相对 PR17 没改，但它们与生产 main 不同。这是完整栈早期 B-001 已提出的要求，见 [FIRST_BATCH.md](FIRST_BATCH.md)。[升级摘要绑定](navigation-integration/upgrade-contract.json) 来自实际 git blob/本地字节，**没有读取 ROOT 当前已安装组件，不能预先说它匹配或不匹配**。

| 文件 | 生产 main SHA256 | 完整候选 SHA256 |
| --- | --- | --- |
| currency_wars_control.py | 2b93583c57ee7593ca17cc951f078fa9cd4238285ca84aa83325646f45d86b54 | 187f826feb6e29beae8175ce9854845d19354f8344723c84663f6fe79ec0dca5 |
| currency_wars_bridge_task.py | b0f7616f478ccde4b1e519b60078a60daece9f6acdf6b6a796db41634a054d9d | aff9cb2bccb5047ac9f160fee17b5eced0a61f4688f4a6c98b0a459f86819978 |

ROOT 只需在本机核现有 install.json 的 broker_sha256/driver_sha256及原配置校验，勿导出整个配置、SID或安装身份。若已匹配候选则复用；若仍为旧 pin，必须在局后通过**原** `build_input_bridge.py --game-path <实际路径>` 生成保留原安装身份的修复包，经原独立包审查，再走 `Install-InputBridge.ps1`。不能只拉源码、复制单个控制文件或改 install.json 的 pin。安装器脚本本身与 main 字节相同，本批没有新造安装器、输入者或运行任务。

正常推广路径是 ROOT 完成审查后将这一份以 main 为目标的整合 PR 推进到 main，再在退出旧进程的生产检出运行原 `Update.ps1`，清应用 pyc、重建 GUI、完成必要的原组件修复与本机 READY 更新。这样一次取得完整栈，不逐个部署旧草稿。若 ROOT 先在独立候选检出做真实验证，仍须执行同一组件/来源门槛，不能把分支切换本身称为已部署成功。

```powershell
# 在已检出的候选根目录；同一冻结 driver，不启动游戏、GUI 或 broker。
$env:ORT_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = 'tools'
.\.venv\Scripts\python.exe -B -X utf8 tools/replay_currency_wars_navigation.py `
  --protocol --output <本机验收输出目录>/navigation-integration.json

cargo test --locked --manifest-path gui/Cargo.toml `
  source_guard::tests::reviewed_runtime_sources_require_full_current_inventory
cargo test --locked --manifest-path gui/Cargo.toml `
  protocol::tests::runtime_location_registration_requires_exact_live_parent_and_child

# 上述独立验收完成后的构建步骤，本身不启动 GUI。
.\gui\Build-GUI.ps1
```

仅本机生成来源片段的例子（不生成授权，不把文件上传）：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -c "import json,pathlib,sys; sys.path.insert(0,'tools'); from currency_wars_source_guard import runtime_source_binding; p=pathlib.Path.cwd(); (p/'docs/RUNNER_SOURCE_BINDING.json').write_text(json.dumps(runtime_source_binding(p),ensure_ascii=False,indent=2),encoding='utf8')"
# 由 ROOT 将已审片段纳入本机真实 RUNNER_READY 后：
.\.venv\Scripts\python.exe -B -X utf8 tools/check_install.py --check-launch
```

最小发布只含代码、清单、冻结 driver、聚焦验收/失败记录和本交接。已有 PNG/blob 与 PR9–17 报告直接复用，不重复上传完整 Worker 大对象。本批继续用 GitHub 接口发布必要新增 blob，不再重试已知缺凭据的 CLI push；最终 PR、SHA、父与树以实际远端回读为准。

## 全奖励的合并规划

唯一目标是总通关次数与总耗时最少。按 ROOT 当前 UI，紫金1难30不推进当前紫金7职级，因此低难局后还需晋升局的成本必须计入；普通紫金7难36也不能自动当作晋升赛难44。不能由敌难差推算胜率、耗时倍率或剩余局数。

默认把必须打的晋升赛当作承载未领羁绊/章节任务的主线：每局从 7追击、7群攻、7能量、7仙舟中选一个与当局真实攻略、环境和资源最适配的主目标，顺带推进特权赋予卡升级进阶装备 0/2。四类缺口不等于四局，也不能假设一局同时覆盖多种7羁绊。已领5追击、5群攻不再专门开局或重选攻略。

| 路线 | 何时有价值 | 必须计入的成本 |
| --- | --- | --- |
| 晋升赛44＋未领羁绊＋章节机会 | 当前真实打法胜算可靠，UI确认胜利推进尚缺段位奖励 | 失败/重试耗时；避免为任务牺牲过多战力 |
| 普通紫金7难36 | 有明确额外点数/奖励或成功率收益 | 未经UI确认不计入职级晋升收益 |
| 紫金1难30补缺 | 晋升赛风险显著更高，或缺口明显更容易在低难完成 | 之后仍必需的晋升局，不能只报这局较短 |

对当前已经进行的紫金1局，按剩余收益判断，不因这份工程交付要求中断或重开；结算后领取并重算。若当局能拿未领7羁绊、对应紫金奖励或任务卡，它有独立价值，但不抵扣尚未完成的晋升局。

5/7能量、5/7仙舟是否能在7层通关时同局领取、紫金奖励是否限定标准模式/终局保持条件，由 ROOT 在自然看到当前目标奖励条目时核对；确认后优先合并5→7，不为了未确认的叠领规则多开专场。两次装备升级同样核实际进度0→1→2，不假定必须同局，也不将机会候选或用卡意图记成完成。源码原 progression_plan 仍只是候选计划，没有因此新增自动用卡执行器。

晋升等级81、预期收益14/91、羁绊360/1320各自保留原单位与显示；没有足够信息把它们换算成还缺多少局。先做能覆盖多个未领奖励的局，让晋升点自然累积，最后才按实际“点数/分钟”补纯点数。当前官方 [4.6更新说明](https://hsr.hoyoverse.com/zh-cn/news/166468) 检索摘要明确提到双倍晋升点，但正文打开仅返回加载页；ROOT 顺手核当前UI是否生效，不能套旧攻略估算81级后的刷取次数。未完成的标准/紫金限定奖励不能被超频更短这一单项理由替代。

实用比较是：奖励覆盖相同的情况下，合并局的实际平均尝试成本，与“低难补缺＋后来仍需晋升”的总尝试成本比较。胜率来自历史而不是敌难数字；失败局若已经推进章节/收集，也要保留那份收益。最终全奖励还需按当前UI确认预期收益未完成条目类别和晋升奖励终点，不能凭聚合数字宣布全部完成。

## 借鉴范围

参考 [Airtest 官方源码](https://airtest.readthedocs.io/en/latest/_modules/airtest/core/cv.html) 在当前屏幕找具体目标、可在预测区域内匹配，以及 [pywinauto 官方等待说明](https://pywinauto.readthedocs.io/en/latest/wait_long_operations.html) 将目标存在/可见/可用作为动作条件。本批只借鉴目标和语义前提分开的方式，复用项目现有不可变帧、视觉守卫与唯一 Entry；没有引入 Airtest/pywinauto、新控制器、在线模型或额外每帧 OCR。
