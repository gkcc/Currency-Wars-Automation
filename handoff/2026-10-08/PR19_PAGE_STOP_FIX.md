# PR19 优势页分类与停止投影修复

## 范围与来源

独立候选以 `8bcff97b5a9287a6ffda405ee33a17473d1e32ac` 为唯一直接父；PR19 原候选保持冻结，不含 PR20/21。只改两个生产文件：`currency_wars_perception.py` 与 `currency_wars_runner.py`。

已实际 fetch ROOT 证据提交 `16b8b96b06ab2e6e8073ca32fa4a8268e2287069`。本候选复用其 `ROOT_PR19_ADVANTAGES_MISCLASSIFICATION.json` **原 blob** `0a1f2d85ff581f618602c721420c9d3e75d9b647`，没有重新制作 OCR 记录。证据为15行去 UID 的原生 OCR 结果，不是原完整 PNG。

- 原只读 receipt：`b71a7bf33f6840ebb7e5fea80f2519e3`。
- ROOT 本机原 PNG 摘要引用：`25632a83eebdee01c30cfe1b5ee3b75fd8e3f69e213c8b1de0895e7181910575`。本候选未取得或重读该 PNG。
- 修复前在原 PR19 生产 `classify` 实际复现：原生优势锚点 `true`、页面 `battle`。

## 1. 页面结构优先，正文不能认定战斗

四个已存在的原生锚点（货币战争、优势布局、常驻优势、赛季优势）原本均通过。误判来自先执行的全文“战斗中”分支。现在将 `_native_advantages_page` 提前至一般页面文字判断之前，保留真实更新模态、领奖弹层和正式结果页优先级；删除全文“战斗中”直接返回 `battle` 的分支。战斗继续使用已有 `_native_battle_stage` 的四个固定 HUD 锚点，无新模板、无阈值放宽、无额外 OCR。

原15行原样进入生产分类，结果 `advantages`；行、置信度、box 均不变。缺失/移位/低置信/重复任一优势锚点的16个协议变体返回 `unknown`。已有四固定战斗 HUD 协议仍返回 `battle`，其16个损坏变体不因正文补成战斗。

真实 Worker `tick` 的惰性消费者结果按原业务状态区分：

| 当前状态 | 修复后请求/结果 |
|---|---|
| 需要跨租期业务审查 | `business_resume`；保留原 unknown，不再因误分类只读等待 |
| 业务已承接，当前局后清单恰好为优势页 | `post_match_advantages`；不写面板已完成、不发布点击 |
| 清单顺序与当前优势页不同 | `unknown_page`，让现有审查接口处理，不擅自跳清单 |

这些是生产函数与惰性请求边界的协议回放，不是 ROOT 当前 run 的真实决策回执。没有把优势28/40或材料0写成通用完成条件，没有写 `remaining_settlements=0`，也没有制造备战节点。

## 2. stop 终态保留原业务归属

旧路径在 worker 退出并清理运行目录后，`current_state(emergency=True)` 退成展示壳；随后 `stop` 将薄状态覆盖 `CURRENT_RUNNER`，丢失原 `journal_file / match_id / business`。

新路径保留同一 owner/run/worker PID/creation 的最终 CURRENT，复用 `durable_records` 验原回放目录 owner，复用 `load_business` 验现存业务检查点与最新 lease。缺失字段只从已经匹配的现存来源取得；原来没有业务指针或来源时继续缺失，不拼路径、不扫描猜测、不新造 match。

原 broker PID/creation 同样保留并精确 probe。展示壳或旧终态写 `not_launched` 不能抹去已知原身份；原进程仍运行或退出未知继续拒绝。新鲜双进程退出证据和 `stopped` 最后覆盖旧显示状态，保留 worker 实际写出的清理结果。

两个 stop flag 仍先设置。坏/外来/冲突的持久记录不覆盖；可选记录校验错误暂存，仍完成沿已认证进程身份的退出核实，最后在写 CURRENT 前报告错误。没有撤锁或自动重启。

协议验收实际调用 `command_cli(stop)`，在夹具中删除真实临时运行目录，再调用 `_start_cli`，走过 `prepare_business_start` 与归档，最后在 `Popen` 边界拦截。没有启动任何 OS 子进程。原 broker running/unknown、原 owner/最新 lease/CURRENT 冲突及缺来源继续拒绝。

## 聚焦验收与源码绑定

冻结入口：`tools/replay_currency_wars_page_stop.py`。仅三个明确类、14个方法：页面结构5、Worker路由3、stop/start6；损坏锚点等 subTest 不充作新增测试数。

Linux / Python 3.12.14：**14 passed，0 failure/error/skip，0.049560280秒**。该数字只是离线测试执行时间。生产来源24项；加两个新测试、driver和公开证据，共28项摘要，测前测后相同。

`page-stop/acceptance.json` 为9,109字节紧凑报告，保留全部测试 ID、逐项结果、28项 SHA-256、环境与真实/协议边界。测时 checkout HEAD 如实为原父提交；修改字节由摘要绑定，未伪写测试 head。完整 tests.txt 由 driver 本机生成，未重复上传。

Source-set SHA-256：`e6d85664369c2f222677f693dbf38ae9c825707790dd73aab876de58b0ce48df`。

| 文件 | SHA-256 |
|---|---|
| tools/currency_wars_perception.py | 604b4b6d6912e3d8415f066bbb32307555b9564e2f2d5ebddc6d94f9fded4aba |
| tools/currency_wars_runner.py | 706472e3daaaee9647daa9851d2448c51f4e5412a19545dbde4125e8d0be866e |
| tools/replay_currency_wars_page_stop.py | edb94a4cb9f77d0b24adabe9ed36685aafb94b8d56b0ede47732e5152e8d0728 |
| tools/test_currency_wars_page_structure.py | 0085c6609dec114b263f200cc82e45cf28ae32e45e1d26e1fadcecac0146102b |
| tools/test_currency_wars_stop_projection.py | de553e8b63d5936da764ce441a327c4431190d76314a38ca641087b191e90a38 |

开发记录：首次 stop 测试导入因当前工具环境缺 `psutil` 失败，未隐去。之后在任务临时依赖目录安装仓库已固定的 psutil7.2.2/opencv4.13.0.92，未动生产安装或全局环境；分别8/6项自检与最终冻结14项通过。没有重跑旧39项、GUI/Rust旧类或 PR20/21。

### Windows 局部复核

ROOT 用已有本机 Python 和候选独立归档，在归档根执行：

```powershell
python -B -X utf8 tools/replay_currency_wars_page_stop.py --root . --output page-stop-windows.json
```

核14项、0 failure/error/skip、28项同一 source-set、测前后差异为空。无 `.git` 的归档报告 head 为 null，仍由不可变归档和28项摘要绑定。此选择不需要私有57素材或新截图。

另一个必要真实端点由 ROOT 本机复核：同一原 PNG 经当前生产 Perception 得到 `advantages`，保留实际源帧与原生 OCR；不能拿本报告的行级回放替代。新 run 的当前鲜帧再按实际业务状态生成 `business_resume` 或页面审查请求，旧只读 receipt 不能充当新 epoch 的动作授权。

## 最小升级与当前手动恢复

1. 当前继续保持手动安全。先在本机保留当前完整 CURRENT、真实 business/checkpoint/lease 与原收据，不公开 owner/token。此次停旧 PR19 仍可能触发原投影 bug；若发生，只按 ROOT 已执行过的同 owner/run/PID/creation 真实来源补回缺字段，保留 `stopped` 和实际退出，不能将本补丁当作追回无来源数据的工具。
2. 沿原 stop 退出旧所属 worker/broker/GUI及子进程并核真实身份退出后，才在 source activity 允许的窗口把生产 checkout 快进到本候选。不要热换正在运行的 Python 源码，不创建第二控制器，不清别人锁。原资源与本机记录按既有升级保护保留。
3. 本批 control、bridge task、broker pins、runtime manifest、requirements、Rust/UI 全部未变。已有794文件组件安装与GUI构建在原校验仍匹配时可复用，不必为了这两份 Python 重做整包 repair 或 Rust 编译。若原检查返回实际不匹配，仍走原修复流程，不能绕过。
4. ROOT 对新的24项源码和原本机61项资源/provider做实际独立审查，更新真实 READY 绑定。旧 READY 两个源码摘要必然过期，不能只改 ready=true 或复用旧 PASS。清除本应用对应旧 pyc 并使用全新 Python 进程；`-B` 本身不保证忽略已有旧 pyc。
5. 在本机运行 `python -B -X utf8 tools/check_install.py --check-launch`，须实际通过。继续沿已验证 runtime root 与原子进程环境启动，不改全局 TEMP，不另设安装根。
6. 沿同一正常 start 使用当前会话与原连续整局参数：`--profile --continue-matches --max-matches 20`（以及原授权期限）。start 从真实业务来源形成续接合同，ROOT 用当前请求的真实来源、新 snapshot/epoch 完成原 `business_resume`。之后处理实际页面请求，按原清单/大厅开局接口继续。局外页不调用需要 preparation_stage 的 `manual-step inspect`，不伪造1-1，不走raw逃出。

新 stop 的实机终点仍需 ROOT 验：最终 CURRENT 保留真实 journal/match/business/broker 身份和双进程退出事实；随后正常 start 不再因“业务记录不在本项目原始回放目录”拒绝，并正常请求当前来源续接。若前面的原身份、退出或当前帧核验失败，继续拒绝，不能为可开局清空业务历史。

## 明确未宣称

本候选只修两个已定位 B 类缺口。未执行 Windows 实机验收、未安装候选、未启动游戏/GUI/broker或在线模型。未取得/上传原57素材、UID或真实owner/token。没有自然完整备战节点、减少D回传、真实新局成功或整局提速结论；经济/GUI fixture/运行期限/PR20/21不纳入本批。
