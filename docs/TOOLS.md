# Windows 极简操作工具（带教版）

本项目仅保留一个小型 Python CLI：`tools/game_io.py`。它**不理解**角色、商店、装备、经济与奖励，也**不负责出建议**；Codex 自己看图分析。没有 MCP、Worker、OCR 流水线或后台服务。

## 准备（一次）

Windows 10/11，在当前已安装 Python 的终端：

```powershell
python -m pip install -r requirements.txt
python tools/game_io.py status
python tools/game_io.py shot
```

必须让《崩坏：星穹铁道》的游戏客户端窗口（完整标题恰为“崩坏：星穹铁道”）处于前台，且仅有一个可见游戏窗口。不能控制游戏时不自动前台抢焦点，不要求提权，不直接启动游戏。

`shot` 返回 JSON 中的 `observation.image`（绝对 PNG 路径）和 `observation.frame`。**Codex 自己打开这张 PNG**，以 1920×1080 预览图的坐标下达下一步命令。

## 先查清事实，再提战略方案

下面 `<帧ID>` 必须替换为上一条 `shot` 或鼠标动作返回的 `observation.frame`；每次操作返回**新的图片、新的帧ID**，下一次必须使用新的帧ID。

```powershell
python tools/game_io.py hover 880 480 --frame <帧ID>
python tools/game_io.py inspect 880 480 --frame <帧ID>
python tools/game_io.py scroll 1200 560 -120 --frame <帧ID>
```

- `hover`：移动鼠标，等待详情浮层出现，然后自动截图。
- `inspect`：单击一次，等待打开人物/装备/投资/羁绊详情，然后自动截图。
- `scroll`：滚动详情区域，然后自动截图。
- 截图、移动、查看后，不要直接问用户“这是什么角色”；先用新截图补充名字、费用、星级、装备说明和任务目标。
- **`inspect` 是真实点击，并非程序保证的只读 API。** 只能点已知不会直接购买/升级/领奖/合成/出战的详情入口。商店角色卡可能单击即购买；不得为了看详情使用 `inspect` 点商店卡。未知按钮不要试错。
- 以上“查看信息”是用户允许 Codex 为提方案自行进行的操作，但不能暗中把奖励选择、金币支出或阵容改变放到审核前。

## 用户确认回合方案后

```powershell
python tools/game_io.py click 800 500 --frame <帧ID>
python tools/game_io.py key D --frame <帧ID>
python tools/game_io.py key F --frame <帧ID>
python tools/game_io.py drag 670 870 1080 515 --frame <帧ID>
```

- `click`：一次实际点击，可用于已批准的购买、领取、出售、开战等（必须先确认目标）。
- `key`：当前支持 ESC、D、F、SPACE、R；历史 D 刷新、F 经验，但必须确认当前商店页面与实际花费，不能盲连按。
- `drag`：单次拖动，适用于穿装/角色位置调整。每一步都返回新 PNG，**输入发出不代表游戏内确实生效**。
- **不要在 Codex 仍操控时由真人同时点击。** 如果用户自己操作完，就让 Codex 重新 `shot` 并根据新事实继续运营。

## 拒绝条件和边界

- `--frame` 必须是当前最新截图的帧ID，且截图字节、游戏 hwnd/PID/进程创建时间须保持一致；这不意味着代码证明了目标按钮的视觉语义。
- 必须保持游戏前台；发现人工移鼠、焦点改变、Windows 状态未知等就停止动作，不自动重发。
- 本工具使用旧版已经验证过的 Windows SendInput/1920×1080映射做法，但**新独立工具未经过 Windows/实机测试**，不能继承旧版验收结论。
- 若发现旧版 Worker/broker 的 Windows 具名互斥量还存在，拒绝新的输入。旧版程序运行到一半时**不能同时启用新工具**；远端合并不会杀死旧进程。
- `python tools/game_io.py stop` 写入持久 STOP 标记；`resume` 只能在用户明确允许之后使用。F8 仅在本脚本正在执行时检测**按住状态**，不是后台全局热键监听器，也不能代替停止其他程序。需要全时段紧急停止应使用 Windows 系统级人工接管/结束输入进程，不可宣称本工具有旧版等价防护。
- 键鼠操作可能发出但后置截图失败，JSON 会记录 `input_attempted` 和 `effect_verified=false`。**失败/未知时先只读重新观察，绝不按原指令盲重试**。
- 没有游戏语义级防误买、防误卖和完整审计 broker；Codex 必须遵守带教计划和视觉确认。若风险无法判定，应停下而不是放宽限制。

`captures/` 是本地临时图片及 STOP 存储目录，已被 `.gitignore` 排除；不要提交含私人游戏界面的截图。
