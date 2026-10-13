# 单条历史启动登记的无重启退役

此入口默认只读；ROOT 在真实 Windows 与独立审查通过后决定是否加 `--apply`。
不是删除/清理工具，不启动或恢复 runner、broker、游戏，不生成 READY，不修改原 marker。
旧的重启恢复仍保留，既有 prepare 收据不会被覆盖。

## 证明什么

退役的是一条明确选定的、已知父和包装器创建身份均死亡的 start 登记。
历史 wrapper→worker 父子关系仍为 unknown。新增收据的结论仅为：在冻结源对应的
受支持控制域内，当前没有可继续该项目控制的 actor；原登记按此显式协议迁移退役。
不声称整个 Windows 没有任意代码、不证明历史从未输入或没有其他未登记软件。

受支持宿主是标准 python/pythonw/py/venv、项目 GUI、Windows 脚本启动器。
改名解释器、任意嵌入、注入、远程任意启动服务不在协议内，不能借本入口认证。
因此必须明确传入 `--accept-supported-control-domain`；不能自动从普通启动调用。

## 只读实证与锁

- Toolhelp32 process-only 快照先取得完整有限 PID/PPID/镜像表，不对 lsass 等非宿主查询命令行。
  只有支持宿主读取 exe、argv、创建身份，前后核创建身份及 PPID；相关不可读/未知拒绝。
- 按 Python 选项后的实际绝对 `.py` 入口分类，不搜索整段命令。冻结 SHA 的项目
  currency_wars_mcp.py 可保留：它提供查询、绑定计划和暂停，不能启动/恢复或直接发游戏输入。
  它新产生的 runner 子进程仍独立核验，不因父进程是 MCP 而豁免。
- `-c`、`-m`、stdin、交互 Python、不可解释脚本宿主拒绝。普通交互 PowerShell/cmd 可保留。
  PowerShell 任意诊断程序不会被误称为 controller，但无法解释时仍报 scope unknown；
  应结束该临时诊断或用下述直接脚本入口，不能增加忽略 PID。
- operator 及实际 venv 父链只按当前 PPID、创建身份先后及完全对应 argv 放行。
  支持父 PowerShell 单条字面量 native 命令；不允许拼接另一条 Start-Process。
- 持有原 source mutation lock、原 bridge inbox_lock，以及旧游戏与当前游戏的两把
  原 `Local\CurrencyWarsControl-P…-T…-H…` mutex。与 claim_start_mutex 一致，已有 mutex
  即拒绝（包括 ERROR_ALREADY_EXISTS），不把空闲旧句柄视作不存在。
- 在 inbox_lock 内复用 configuration(entry.PINNED) 验安装/hash/固定任务定义，
  task_rpc(config, 'inspect') 必须 instances=[]；launch.json/claimed 请求存在则拒绝。
  不调用 run/cancel，不改安装、任务、安全软件或权限。
- 前后核对登记、源、相关宿主集合、安装任务、旧运行文件和旧游戏绑定；变化拒绝。
  其他 incomplete/active/unknown lease 和 CURRENT broker 原门禁完全保留。

## 原运行证据

传入真实原 run 目录和真实回放 owner 文件。marker 必须 complete、protected_children=[]、
run_id/worker 创建身份与 owner 一致，worker 已死。broker/bridge/request/result 文件不能出现。
这些证明原运行材料与早期失败阶段相容，不补造 parentage。
原文件字节全部保留，只记录哈希；不复制 token/命令行明文到输出。
旧游戏绑定文件也保留并按哈希绑定。新游戏 PID/创建时间/HWND 通过原生只读 API 核实。

## 操作

在普通交互 PowerShell，直接调用实际 Python/venv，不包一段 `python -c`：

```powershell
& 'D:\货币战争\.venv\Scripts\python.exe' -B 'D:\货币战争\tools\currency_wars_scoped_recovery.py' --project 'D:\货币战争' --lease '<原32位文件名>.json' --lease-sha256 '<原文件SHA256>' --parent-pid <原父PID> --parent-identity 'windows:<原父创建身份>' --run-dir '<原run目录>' --owner-file '<原回放owner.json>' --worker-pid <原workerPID> --worker-identity 'windows:<原worker创建身份>' --old-game-binding-file '<保留的原游戏binding.json>' --game-pid <当前PID> --game-creation-id <当前创建身份> --game-hwnd <当前HWND> --accept-supported-control-domain
```

所有占位符取本机真实记录，不按本说明猜值。只读结果 `checked_only=true` 后，
独立审查批准再用相同参数加 `--apply`。apply 会重新核实全部条件，不消费旧的检查结论。
输出 `retired=true` 仅指登记迁移；正常源码审批/发布/游戏启动仍走原入口。

仅新增 `docs/source-activity-recovery/<lease>.scoped-v1.json`，独占创建并读回；
重复调用返回已处理。部分/篡改收据 fail closed，不覆盖、清理或自动重试。
正常 guard 验证选定 lease、原 run 与旧 binding 保留字节，再检查其他登记/CURRENT。
今后如合法调整这些原保留证据，应另行审查，不由此工具处理。

## 验证边界

新增可移植测试覆盖协议事务、MCP/解释器分类、父 shell、双锁要求、桥接实例与队列、
并发变化拒绝、原字节保持、收据去重及篡改拒绝。模拟测试不等于真实 Windows 普查通过。
ROOT 需使用自身完整资源环境验证只读模式、再审查 apply；不要宣称已经解除阻断。

```text
python -B -m unittest test_currency_wars_scoped_recovery test_currency_wars_start_recovery -v
```

原生接口依据：
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot

## 收据完整性与人工暂停意图

消费收据时严格检查来源 SHA 映射、桥接 installed=true/配置哈希/空实例/固定任务名、
broker 与历史 control 源哈希绑定、每个 actor 的原生身份及合法角色、完整登记快照与
选定 lease 哈希、旧新游戏原生三键。整份证明另有规范 JSON 的 SHA256，检测字段损坏；
这不是签名或认证体系。验旧证据不要求当前源码等于旧版本，合法升级后仍可消费。

原 run 仅额外支持 manual-intents 这一明确目录：最多2000个、每个4096字节、
无链接/reparse/子目录；文件名必须对应32位 manual_id，JSON 恰有 manual_id、reason、
time、priority_ns 四字段，原因1–200字符、时间可解析且有时区、优先级正整数。
这是原 latch_manual 的人工暂停意图，归属由已核 run/marker/owner 和完整文件哈希绑定，
不新增原记录没有的动作或归属字段，不以中文原因匹配替代协议。目录内全部哈希消费时再核，
其他目录仍拒绝。此变更的 manifest/模块部署仍需原 Rust GUI 实际重建并记录新构建。

CMD 包装器按 /D /E:on /V:off /S /C 后的单条字面量绝对 exe/cmd/bat 入口分类，
项目、安装 bridge、bridge 工作目录内的入口拒绝；项目外直接入口按外部脚本记录哈希，
不证明外部脚本内部行为。变量展开、管道、重定向、命令拼接、相对入口仍拒绝。
孩子进程仍独立普查。它与项目外直接 Python 脚本遵循相同受支持控制域边界。
