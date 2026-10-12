# 单条历史 start 登记恢复，第二版

## 对第一版的纠正

d56bf1d 的全量进程普查不是可部署的恢复前置：ROOT 的真实 Windows 检查在 PID332
因访问不全被拒，普通 PowerShell/venv 父进程也会被判为 launcher。其测试结果是
17 通过、1 条符号链接 fixture 因 WinError1314 未执行，不能称为 18 项 Windows PASS。
不要为第一版重新启动电脑。第二版完全移除机器进程枚举，不增加 lsass/父 shell 白名单。

## 最小充分证据

1. 同一 Windows 安装、同一项目路径、人工明确选中的一份 start 登记；原父及所有已登记
   子进程均经原 PID + creation identity 检查为 dead，active/unknown 都拒绝。
2. --prepare 在重启之前封存原登记完整字节、SHA256、机器身份及 GetTickCount64。
   --prepare 不改变原登记、marker 或 READY，也不退休 lease。
3. 后续检查必须看到同机单调 uptime 严格小于封存值，才证明期间发生了内核启动边界。
   该边界使此前未知的旧后代也不可能继续存活，无需补造其历史父子关系。
4. 在原 mutation lock 内，原 activity_reason 排除且仅排除选中的这一份旧登记，独立核验
   所有其他登记及 CURRENT_RUNNER 的原 worker/broker 门禁。检查前后登记/CURRENT 字节
   哈希及源码集合须不变；其他 incomplete/active/unknown 或变化一律拒绝。
5. 只新增版本化、绑定原字节的退役凭据；原文件原地保留。没有通用清理、删除、移动、
   kill、恢复输入、第二执行器、force 或忽略其他守卫的选项。

[GetTickCount64 的官方定义](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64)
是系统启动以来的毫秒数，不用 UTC 调时、文件时间、PID 缺失或口头“已重启”代替。确认时
uptime 若已超过封存值，会保守拒绝，不猜测发生过重启。睡眠/休眠、只重启 Python、
VM 快照回滚、克隆 Windows 和复制工作树不是支持的恢复方法。

这里证明的是**旧启动后代已跨重启灭绝**，不是整台机器当前永远没有其他执行者。
未登记的独立新进程不会被当作旧后代，入口也不宣称拦截所有 Windows 进程创建。
原来的 CURRENT/broker 及注册门禁保持原语义；后续正式运行仍走原审查、publish-ready、
Start 流程。本入口不发布 READY，不验证或恢复游戏。

## 实际 operator 路径：普通 PowerShell + 仓库 Python/venv

无须退出父 PowerShell，无须绕过 venv，无须管理员全局权限、符号链接权限或关闭安全软件。
第二版不调用 psutil.pids/Process，也不读取 lsass 等无关进程命令行。它只查询原登记和
CURRENT 中的具体进程身份；如果这些身份自身不可读，仍拒绝。

先在 ROOT Windows 完成代码独审和测试，再从普通 PowerShell 在仓库根运行只读预检。
`python` 可替换为仓库实际 `.venv/Scripts/python.exe`；其包装器不参与恢复判定。
`<...>` 是待替换的原文件实际值，不猜文件名或 SHA，也不遍历全部旧登记。

```
python -B tools/currency_wars_source_guard.py --project "D:/货币战争" --lease <32位原文件名.json> --lease-sha256 <原文件实际SHA256> --parent-pid 110184 --parent-identity windows:134362871807656452 --preflight
```

--preflight 核验 API、机器身份、原锁、选中原身份、源码清单和其他登记，**不写文件、
不要求已经重启、不声称恢复成功**。先确认这个真实命令可执行，再决定是否采用重启恢复。

经用户/ROOT决定后，把 --preflight 替换为 --prepare 封存；自行保存工作并完成 Windows
“重新启动”（工具不会重启）。随后同一命令去掉模式选项执行只读检查，再加 --apply。
apply 全部重新核验，不把先前预检当作有效授权或本轮通过证明。重复 apply 只验证同一
字节与凭据，返回 already_retired；不会创建第二份退役凭据。

新文件在 docs/source-activity-recovery，使用 `<原文件stem>.retirement-v2.intent.json`
及 `<原文件stem>.retirement-v2.json`，不改写或删除第一版文件。本地机器/登记证据不应上传
GitHub。部分写入或冲突会保留并拒绝，不能手改成 PASS。

## 新启动登记修正可独立审查

start 在 Popen 前登记 chat_id/launch_id，传递原 lease 名；worker 在资源校验和构造前
登记关联。父进程发现早期登记后仍调用原 registered_worker_identity，验证实际 Popen
创建身份和直属 venv 跳转再转移责任，不用时间邻近推导关系。未完成握手仍保留 incomplete。
超时只声称本次 Popen 的退出，未宣称真实 worker/broker 都已退出，没有新增递归 kill。

## 检查

```
cd tools
python -B -m unittest test_currency_wars_start_recovery -v
python -B -m unittest test_currency_wars_source_runtime test_local_runtime_compatibility -v
```

第一模块是临时文件和 mock Windows 身份/uptime 下的离线契约测试，包含真正执行
runner 启动编排函数体的惰性平台测试。新增测试令机器进程枚举/保护进程访问直接抛错，
证明恢复不依赖它们；其他注册控制器和 broker 的 active/unknown 仍拒绝。
符号链接 fixture 在 Windows 缺少权限（1314）时明确 skipped，不设置权限、不算通过。
第二行及真实 --preflight/--prepare/重启后检查须由 ROOT 在完整 Windows 环境验证。
在本机与独立审查确认前，本机启动阻断仍未解除。
