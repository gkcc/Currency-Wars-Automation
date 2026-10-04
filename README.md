# Currency Wars Automation

《崩坏：星穹铁道》货币战争的 Windows 本地助手：Rust/Tauri 界面、Python 执行器、OCR，以及带暂停、接管和停止保护的单窗口输入控制。

**当前发布是源码版。战略决策仍需要 Codex 在本机监督并回答请求，没有配置独立 AI 服务，也没有验证另一台电脑可无人值守完成整局。** 界面名称中的“一条龙”是目标，不是当前自动通关保证。

## 在另一台电脑安装

要自动同步，请安装 Git 并运行 `git clone https://github.com/gkcc/Currency-Wars-Automation.git`。也可下载 ZIP 解压，但 ZIP 安装只会提示改用 Git clone，不会替换你的目录。路径可自行选择，不要求特定盘符，也不要求安装 Codex Agent Workflow 技能。

准备 Windows 10/11 x64、Python 3.11–3.13、Microsoft Edge WebView2，以及用于编译界面的 Rust MSVC 工具链和 Visual Studio C++ 构建工具。在项目目录的 PowerShell 中运行：

```powershell
.\Setup.ps1 -BuildGui
.\Start.ps1
```

安装会下载 Python/Rust 依赖，创建项目独立 `.venv`，并构建 `gui/bin/currency-wars-gui.exe`。编译缓存保存在 `gui/target`。安装本身不打开游戏，不运行游戏输入。已有匹配的本机编译结果时可使用 `Setup.ps1`。

推荐使用 `Start.ps1`；它通过项目解释器传入实际项目路径、本次会话身份和所属临时运行目录。源码编译后的 EXE 也能找到同项目的 `.venv`。没有提供已验证的跨电脑二进制安装包。

## 自动检测和同步更新

正常启动时会自动检查规范仓库 `gkcc/Currency-Wars-Automation` 的 `main`，并为干净的 Git clone 执行安全的快进同步。它核对原始和 Git 实际解析的远端地址，拒绝重定向到其他仓库；只同步源码，不切换分支，不重置或清理本地文件。

本地有未提交修改、历史分叉、其他分支，或 GUI、执行器、broker 的活动/退出状态未知时，跳过同步。启动注册和更新共享互斥保护，避免控制器启动期间改写源码。远端改动若涉及 `docs`、运行目录、截图、`.venv`、商店模板、编译缓存或 `gui/bin`，也会拒绝同步。已有本机数据和修改保留原样。

网络查询有超时限制；更新失败会显示在启动输出和界面的更新提示中，已有有效本机版本仍可打开。Python 依赖变化需要重新运行 `Setup.ps1`；Rust 或界面资源变化需要 `Setup.ps1 -BuildGui`。安装/构建指纹必须来自本机实际操作，过期二进制会阻止启动，更新器不会静默运行旧 EXE，也不会自动重装系统工具链、重开界面、启动游戏或恢复游戏控制。

手动检查或同步：

```powershell
.\Update.ps1 -CheckOnly
.\Update.ps1
```

ZIP 用户请另建 Git clone，再按需迁移自己的本机数据；更新器不会接管或覆盖 ZIP 目录。

## 界面可打开，自动操作仍有前置条件

项目不自动安装、启动或登录游戏。当前识别布局基于 1920×1080 的中文游戏画面；其他语言、分辨率和界面布局需要重新校准。

公开仓库排除了截图、会话、账号配置、运行日志、既有验证记录和从个人游戏截图裁剪的商店模板。商店识别需要用户在本机自行提供并核验 `tools/shop_reader_resources/` 中的 `SOURCES.json`、`names.json`、价格/空槽/推荐标记模板；缺少模板时返回错误或未知，不能据此自动购买。这些资源尚没有自动校准入口。

开始和恢复还需要本机 Codex 审查当前核心源码，并写入有效的 `docs/RUNNER_READY.json`。该记录必须关联当前会话和源码 SHA256，包含真实独立审查的 PASS 身份和时间。安装脚本不会伪造或复用另一台机器的就绪记录；缺项时界面会显示未就绪，开始/恢复保持禁用。

需要 Codex 监督时，在该项目内开启 Codex 会话，并使用其实际会话身份启动，例如 `Start.ps1 -ChatId <当前会话ID>`。执行器会把投资、购买、装备和强化等判断转为有时限、与当前画面绑定的战略请求；监督代理通过固定 `decide` CLI 回答。GUI 留言只存入本地队列，没有内置模型连接或自动发送功能。

实际恢复前仍需要确认本机游戏窗口、输入权限、控制健康和手动交接。当前发布仅做离线源码与安装检查，没有在本次发布流程中启动界面、恢复游戏控制或验证实机通关。

## 安全控制和源码

暂停、接管、F8 和停止走独立控制通道；手动输入或手柄活动会触发暂停。成功停止需要所属进程的创建身份和退出证据，不凭单个 PID 判断。运行目录只清理本次所属数据，未知或仍活跃的子进程会阻止清理。

- `gui/src`、`gui/ui`：原生 Rust/Tauri 界面。
- `tools/currency_wars_runner.py`：有界节点、状态与战略请求。
- `tools/currency_wars_control.py`：固定窗口的输入保护；入口核验固定源码哈希。
- `tools/currency_wars_perception.py`、`currency_wars_shop_reader.py`：本地识别。
- `tools/currency_wars_artifacts.py`：可移植运行目录；已有 Codex Agent Workflow 时保留其生命周期实现。
- `tools/check_install.py`：只读依赖检查，不加载输入控制器。
- `tools/currency_wars_update.py`、`currency_wars_source_guard.py`：规范仓库更新、启动活动保护和本机安装/构建指纹。

离线安装检查：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 tools\check_install.py
.\.venv\Scripts\python.exe -B -X utf8 tools\test_public_packaging.py
.\.venv\Scripts\python.exe -B -X utf8 tools\test_currency_wars_update.py
```

本仓库未附项目开源许可证；公开可下载不代表授予额外再分发许可。第三方依赖保留各自许可，游戏资源不随仓库发布。
