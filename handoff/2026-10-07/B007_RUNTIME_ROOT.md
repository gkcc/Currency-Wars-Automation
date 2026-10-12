# B-007：启动运行根目录一致性

当前执行边界已由本机 [`9c3329c8` 离线交接](https://github.com/gkcc/Currency-Wars-Automation/blob/9c3329c8eaedf4f2b32a64ad3727b23e14b6c3fa/handoff/2026-10-07/OFFLINE_PERFORMANCE_BRIEF.md) 更新：仅离线工程优化，原 worker/broker 已核退出；以下 Windows 实机门槛须等用户重新授权，不能自动启动游戏。首批源码及13项验证仍对应 PR #7 / `dc4b26440e0cf95851493937628f9bb7f1691f76`。
基准：`6e7d3eaceb3625cbd8a095a61273642426229fd0`。这是 B 类通用启动缺陷的代码修复；不代表本局已经结束、整局自动化通过或提速实测通过。

## 原有能力与缺口

已有 `artifacts` 会校验绝对直属运行目录、无链接路径、marker 归属、真实进程创建身份和未知子进程保护；安装的 Agent Workflow helper 可以替代默认目录/生命周期实现。`entry.authenticate` 已明确使用 `run.parent`。固定 bridge task 只接受 `D:\Codex\Temp\codex-agent-workflow` 下的所属 run，安装配置、SID、ACL、清单哈希、驱动/控制器版本也已有校验。

缺口是 runner 在创建 run、读取 marker、登记/结束子进程时重新使用 `artifacts.default_root()`。该默认值取决于进程自己的临时目录设置和 Python 缓存；默认落 C 而固定组件落 D 时，在派发输入前即会拒绝。仅为一次子进程设置 TEMP/TMP 没有形成各入口共用的运行根目录绑定。GUI 另有默认 TEMP 前缀检查，必须随本批 GUI 补丁一起验收。

## 本批 Python 行为

`input_bridge.runtime_location(expected_broker_hash, inherited=None)` 在创建 run 前选择位置，返回严格字段：

```json
{
  "schema": 1,
  "source": "installed_bridge",
  "runtime_root": "D:\\Codex\\Temp\\codex-agent-workflow",
  "installation_id": "11111111111111111111111111111111"
}
```

上例安装 ID 仅为格式示例；实际绑定来自已验证安装配置。

- 安装目录明确不存在时，`source` 为 `standalone`，`installation_id` 为 `null`，保持已有 `artifacts.default_root()`。父进程选定后，子进程沿用显式位置，不重新依赖另一个 TEMP 缓存。
- 安装目录存在时，完整执行已有 `configuration` 的安装清单、有效访问权限、SID 和源码哈希校验，并复用**未修改的** `bridge_task.validate_config` 检查固定 root/inbox。存在但不完整、不可读、路径有链接、配置非法均拒绝，不能悄悄回退到 C 或任意其他根目录。
- 公开 `start` 先保持已有旧 worker/未知退出保护，再为新的所属启动选择位置；接收 GUI 提供的 `--runtime-location-json` 并重验，继续传给 `_worker`。worker 创建 run 前再次核验已安装配置与安装身份；期间安装消失、安装 ID 或根目录改变均拒绝。GUI 启动后才发生的安装变化也会在 `start` 的 Popen 前被发现。
- 创建 scratch 与读取 marker 使用同一显式根目录；`register`、`startup`、`shutdown` 的保护调用均使用 `self.run.parent`；`runner.load` 的重复 marker 校验也使用已经鉴权的 `run.parent`，覆盖 status/pause/resume 共用入口。既有 scratch lease 在该根目录中清理；未知子进程仍保留目录。`runner-owner.json` 和状态记录发布相同 `runtime_location`，供 GUI 校验和现场审查。
- 没有修改机器或当前进程的 TEMP/TMP、`tempfile.tempdir`，没有修改固定驱动/控制器、PIN、TaskScheduler 权限或增加第二控制器。下述 GUI 配套与 Python 同一提交交付，位置声明不能自行授权任意 GUI 路径。
- `tools/build_input_bridge.py` 也移除对调用者默认 TEMP 的依赖：打包配置直接使用原 `bridge_task.RUNTIME_ROOT/INBOX`，写盘前调用原 `validate_config`。CPython 3.12、游戏签名/哈希、SID、既存安装归属、修复清单和独立包审查/安装入口保持原逻辑；本环境未构建或安装 Windows 输入组件。

## GUI 的同一位置与父启动绑定

`gui/launch.py` 用共用 helper 选根，所有 scratch/child 保护调用均显式传入该根。Popen 后将 GUI 实际 PID/创建身份登记到 source lease 和标准 marker，复核父子身份，然后原子发布 `runtime-location.json`。Rust 从 `--runtime-dir` 的原 marker、位置参数和这份登记核验父启动链，再把结果保存在 `Config` 的 `RuntimeAuthority`。

若 GUI 抢先于 Python 登记运行，只对登记文件尚不存在这一状态最多等待 3 秒；登记已存在但内容矛盾、身份不符或不能解析时立即拒绝。此等待只在初始化发生，正常点击没有新增固定延迟。后续四处生产 Binding 读取、状态 header 与终态验证复用该绑定，不在轮询中重复扫描安装组件清单。

首次遇到完全没有位置字段的旧 owner，只在 GUI 启动时冻结的原系统 TEMP 边界内兼容；显式 `null` 或格式错误不能进入旧协议。已经绑定新位置后，owner/state 再删除或改变位置字段会拒绝。旧 TEMP 不存在或不可读时只关闭旧协议兼容，不影响已经验证的新位置。无链接、实际直属父根、marker/chat/token/run/PID 创建身份检查继续生效。

## 聚焦验证

复跑 Python 无输入检查：

```console
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python -m unittest test_local_runtime_compatibility.RuntimeRootTests -v
```

9 项覆盖：已安装根优先与子进程重验；未安装保留 standalone；无效/不可读配置在创建 run 前拒绝；沿用固定 task 路径合同；安装变化/消失与非法启动绑定拒绝；公开 start 显式传递位置且无效配置不 Popen；未知旧 worker 不选择新位置或再启动；真实 scratch/marker、实际 `Worker.register`/`Worker.shutdown` 代码在选定根目录登记、保护未知子进程和清理；`runner.load` 的普通与应急鉴权均用真实 marker/owner 验证选定根目录，错误 token 仍拒绝。

测试用两个独立临时路径模拟 C 默认与 D 配置差异，并使用受控进程身份和安装访问 fixture；固定 Windows 路径合同使用原验证函数。它们不证明 Windows 真实 SID/ACL、计划任务、提权桥接或游戏输入通过。未运行整套依赖 Windows 原生能力的回归，也没有用 Linux 结果替代实机结果。

额外复用 `test_currency_wars_bridge_task.ProtocolTests`：6 项通过；其 `test_json_duplicate_keys_constants_size_and_non_objects_are_rejected` 在 Linux 尝试将硬编码 `D:\Codex\Temp\codex-agent-workflow` 用作实际文件系统根，得到 `ArtifactError: Invalid runtime root or purpose`。该既有 Windows 文件系统用例没有通过，留待本机 Windows 原样复跑；没有改测试掩盖环境差异。Python 编译和 `git diff --check` 通过。

最终集成验证见 [B007_RUNTIME_VERIFICATION.json](B007_RUNTIME_VERIFICATION.json)：根在同一份代码运行上述 9 项、GUI 实际登记路径 2 项、原固定配置合同 2 项，**13/13 PASS、0 skip、0.106419 秒**。其中公开 start 用例也覆盖 GUI 的安装身份在启动前改变时零 Popen。GUI 两项另由独立审查复跑通过；重叠选择不相加。5 个修改的 Python 文件编译检查、完整差异空白检查通过，固定 driver/broker 与基准逐字节一致。

新增 6 项 `runtime_location_` Rust 用例已做源码审查，尚未执行。`cargo --version` 实际退出码为 127，当前环境没有 Cargo；不能把 Python 通过或源码审查写成 Rust/Windows 构建通过。

## 局后 Windows 门槛

当前局完成前继续冻结核心安装。局后按同一源码 SHA 与本批 GUI 补丁验证：保持系统默认 C TEMP/TMP，启动后核对 GUI/worker marker、`runtime_location`、bridge request 和 broker 的 run 根均为已安装固定 D 根；真实 SID/ACL/清单/哈希检查通过，所属 PID+creation 与唯一 broker 不变。验证一次无输入启动/观察/停止及退出清理，再逐次实机出战验收。配置异常必须在创建新的游戏 run/发布输入前明确失败；不要编辑真实已安装受保护配置来做破坏性测试，异常路径使用隔离 fixture。

公开仓库中的实际 Rust 验证与构建入口：

```powershell
cargo test --locked --manifest-path gui/Cargo.toml runtime_location_
.\gui\Build-GUI.ps1
```

新增 Rust 用例自带有限 fixture，不依赖旧测试错误消息提到、但公开仓库中不存在的 `build.py --test-filter`。输入组件若确需重新打包，仍通过原 `build_input_bridge.py`、独立包审查与 `Install-InputBridge.ps1` 流程；本批未绕过或执行该安装流程。

如果组件实际未安装，仍需确认 standalone 默认根与 GUI/worker 显式传递一致；若游戏需要提升权限而组件缺失，原有安全拒绝继续生效。目录修复不延长租期，不创建续局身份，也不把恢复界面当作自动运行成功。
