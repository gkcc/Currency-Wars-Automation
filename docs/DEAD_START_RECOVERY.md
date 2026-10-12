# 单条历史 start 登记的保守恢复

## 结论和限制

同一启动期的“两个进程快照为空”不等于未知后代已穷尽。当前 source mutation lock
序列化 GUI/runner 的登记，但不是整台 Windows 的进程创建屏障，独立 broker 入口也未
取得此锁。因此本入口**不支持仅凭普查退役 legacy children_incomplete 登记**，不提供
force、忽略 broker、手工塞入进程列表、改写 marker、删除/移动登记或清理临时目录的选项。

恢复分为两个阶段：在原机器封存这条登记的完整字节、哈希和单调运行时间；经操作者自行
决定并完成一次 Windows“重新启动”后，再复核同机、同路径、同字节、原父和已登记子进程
确实死亡、其他活动与原 CURRENT_RUNNER/broker 门禁、源码集合和当前进程普查。
只追加一份审计退役凭据，原登记仍原地保留，历史父子关联仍明确 unknown。

重启边界使用 [GetTickCount64](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-gettickcount64)
的下降作为必要证据，不以 UTC 日期、PID 不存在、wall clock 推算、附近的 session_begin、
完整的另一个 runner lease 或一条“已经重启”声明代替。确认时的 uptime 必须严格小于封存
时的 uptime；确认太迟超过这个窗口就拒绝，不猜测。这是保守的充分条件而非检测所有重启
的通用工具。睡眠/休眠、只重启 Python、VM 快照回滚、克隆系统或复制工作树不属于支持流程。

此版本不自动重启、不终止任何进程、不触碰游戏、不发布 READY，也不证明某一局成功。
在没有重启且没有额外可验证 OS 生命周期证据的情况下，此历史登记仍被拒绝退役。

## ROOT 本机操作（独立审查通过后）

先确认最终代码已是待审版本；仅选择这条明确获准处理的 start 登记。不要猜文件名或哈希。
下面的 `<...>` 须替换成原文件的实际值，不是自动遍历整个目录的修复命令。

```
python -B tools/currency_wars_source_guard.py --project "D:/货币战争" --lease <32位原文件名.json> --lease-sha256 <原文件实际SHA256> --parent-pid 110184 --parent-identity windows:134362871807656452 --prepare
```

成功只表示已封存；此时 activity_reason 仍阻断。原文件与
`docs/source-activity-recovery/<原文件名>.intent.json` 均须保留，不手改。
由用户/ROOT决定保存工作和重新启动 Windows；本工具没有执行重启的功能。

重启后，在封存的 uptime 窗口内先去掉 `--prepare` 执行相同命令，只读检查。
检查通过后才加 `--apply`，**apply 会重新完成所有检查，不沿用上次 PASS**。
成功后的重复 apply 只读验证同一原文件与凭据，返回 already_retired，不再写第二份凭据。

进程普查最多 4096 个 PID，除 Windows kernel-only PID 0/4 外都必须能读到创建身份、
可执行路径及有界命令行，前后 PID 集合及两次完整记录必须一致。任一访问失败、消失、
PID 重用、未知元数据或新进程均拒绝。所有 Python/常见脚本宿主、shell、项目路径或
Currency Wars 控制器均视为潜在执行者，仅豁免本次恢复 CLI 自身。ROOT 的交互 shell、
venv 包装器和无关 Python **没有豁免**；检查会返回相应 PID。不要为通过检查而关闭安全
软件或改进程权限，不要将 AccessDenied 改成“无关”。无法建立全量访问时，此路线仍阻断。

对于所有非相关进程，收据只保存可执行路径、创建身份、命令行哈希与分类，不保存其任意
命令行明文。这份本地审计含机器标识/进程元数据，不应上传 GitHub。

成功退役仅让 guard 不再把**这一份哈希完全不变、原已知进程仍为 dead**的旧 start 登记
算作阻断。任何其他 incomplete/active/unknown 登记，以及 CURRENT_RUNNER 的原 broker
证据，继续使用原来的拒绝逻辑。没有改写 receipt、完成动作、父子关系或 broker 退出结论。
源码变化仍需要完整审查、原 publish-ready 和原 Start 门禁，旧 READY 不因此恢复有效。

## 新启动生命周期的小修正

- start 在 Popen 前登记 chat_id/launch_id，将原 start lease 名传给同一 worker。
- worker 在资源核验/Worker 构造前将同一 launch 与 start lease 写进自己的登记。
- start 不再必须等待 CURRENT 发布才发现 worker；先发现早期登记，再调用原
  registered_worker_identity 检查确切 Popen 创建身份/直属 venv 跳转，最后转移登记责任。
- 这些字段是当前启动的新事实，绝不追写历史登记。未发现早期登记、关联矛盾或身份不可读
  仍保留 incomplete；不以新增字段本身证明原生父子关系。
- 超时仍只能声称已等待本次 Popen 退出，不能声称真实 worker/broker 全部退出。此补丁
  没有把这一限制藏起来，也没有加入未验证的递归 kill。早期已登记 worker 保持原守卫覆盖。

## 可执行检查与验收边界

```
cd tools
python -B -m unittest test_currency_wars_start_recovery -v
python -B -m unittest test_currency_wars_source_runtime test_local_runtime_compatibility -v
```

首个模块的离线测试以临时测试文件、mock Windows 身份/uptime/census 检查状态转换，另有
实际执行 runner orchestration 函数体的惰性平台测试；它们不调用真实 broker/GUI。
第二行须由 ROOT 在原 Windows 与完整依赖/资源环境执行，不能把第一行当作第二行通过。
还需 Windows 实测只读 `--prepare`/同启动期拒绝/重启后检查，确认未知访问仍拒绝、原字节
完全不变，独立审查通过后才考虑 `--apply`。没有完成这些验证前，不宣称本机阻断已解除。
