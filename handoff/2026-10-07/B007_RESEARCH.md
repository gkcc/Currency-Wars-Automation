# B-007：执行租期、持久业务身份与统一运行目录

调研基准：6e7d3eaceb3625cbd8a095a61273642426229fd0。访问日期：2026-10-06 UTC；沿用项目既有 handoff/2026-10-07 目录。本文件记录源码盘点、官方资料与最小设计建议，不是实现或实机验收结果。

保留当前有界 worker/broker、唯一输入控制器与暂停优先规则。在现有持久 records 上补业务检查点，让启动、GUI、worker、输入桥与清理共用一次已验证的运行根目录。租期到期终止本次执行授权，不能把未结束对局标成完成，也不能自动重发旧输入。

## 已有能力与真实缺口

以下路径与方法均已实际读取[固定基准树](https://github.com/gkcc/Currency-Wars-Automation/tree/6e7d3eaceb3625cbd8a095a61273642426229fd0)。

| 路径/方法 | 已有能力 | 本批缺口 |
| --- | --- | --- |
| tools/currency_wars_runner.py：_start_cli、Worker.__init__、run_loop | worker 60–7200 秒上限；chat 启动互斥；PID+creation 精确核验；旧身份未知不启动第二个 | 新 Worker 总是新建 active_match_id，缺跨运行的同局延续 |
| 同文件：account_manual_wait | 手动/策略等待不消耗节点活动预算 | worker 硬截止仍有效；不是自动续租 |
| tools/currency_wars_control.py：serve | 游戏进程/窗口互斥；唯一 broker；7200 秒总时限、600 秒空闲限制；优先处理暂停 | 延续业务应走原入口建立新有界执行，不新增后台保活或控制器 |
| runner：execute_plan 的 new_match/confirm_match_result | 真实 setup 页才新建局身份；当前完整结算证据才计数，防重复 | 同进程可进入下一局，业务记录不能只在初始化/关闭时更新 |
| runner：economy_ledger/save_economy_ledger | match+stage 台账已写持久 records；身份不符拒绝清零；商店读数不继承新鲜性 | verify_economy_fence 仍需旧 run/request-ledger。仅保留 pending ID/路径，父 run 删除后会断证 |
| runner：shutdown/_worker_cli/broker_activity | 所属 broker/子进程确切退出后再清理；CURRENT 与 records 留存 | broker_activity 只是统计，不是完整 request+result 归档。正常清理前需持久保存必要原收据 |
| tools/currency_wars_artifacts.py | PID+创建时间、marker 身份、无链接/联接、所属子进程退出检查；512 MiB/20000 项清理边界 | standalone 默认采用 Python tempfile；安装 helper 又可覆盖 default_root。显式根要贯穿两后端 |
| tools/currency_wars_broker_entry.py：authenticate | 已按声明的 run.parent 验 marker，处理提权进程旧 TEMP | 只覆盖认证，未统一上游创建 |
| tools/currency_wars_input_bridge.py、bridge_task、安装脚本 | 已有 SID/安装身份/ACL/文件哈希核验；固定任务批准 D:\Codex\Temp\codex-agent-workflow | 默认 C run 在 prepare_launch 父目录匹配处被拒。组件存在但损坏不能静默退回 standalone |
| gui/launch.py、gui/src/protocol.rs：Binding::load | GUI owned runtime 与子进程清理；worker marker/chat/token/PID+creation 守卫 | launcher 未传统一根；Rust 另按系统 temp_dir 前缀拒绝 D run。只改 Python 创建仍不够 |

RECOVERY_REVIEW.md 已指出这两项缺口；COACHING_BACKLOG.md 包含“默认 C、固定桥 D、启动被拒”的实操记录。本轮没有独立读取其私有游戏截图。

## 官方资料与真实访问

只采用原作者或项目官方资料。普通检索曾返回不相关结果，未作为依据。下列正文均实际读取；本地建议是项目内推导，不是引入这些平台。

| 来源 | 实际访问 | 可采用的原则 |
| --- | --- | --- |
| [AWS Leader election 官方 PDF](https://d1.awsstatic.com/builderslibrary/pdfs/leader-election-in-distributed-systems.pdf) | 成功，5 页正文。原 [HTML](https://aws.amazon.com/builders-library/leader-election-in-distributed-systems/) 重定向后工具返回 0 行，未假称读到 HTML | 租期采用经过时间；检查锁后到执行副作用前仍可能暂停；先持久化工作再宣告完成；保留所有者变更证据 |
| [AWS 安全重试与幂等 API](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/) | 正文成功，读到请求身份、迟到请求、同 ID 参数变化章节 | 请求 ID 表达意图，保留原参数与结果；参数相同不等于同一请求；迟到请求需要历史留存 |
| [Temporal Workflow Id/Run Id](https://docs.temporal.io/workflow-execution/workflowid-runid) | 正文成功 | 稳定业务身份与一次执行身份分开；只借鉴身份分层，不引入 Temporal/scheduler |
| [etcd 并发 API](https://etcd.io/docs/v3.6/dev-guide/api_concurrency_reference_v3/) | Lock/Election/LeaderKey 正文成功 | 写入时用持有 key/创建版本作条件检查；有租期不代替当前写入身份校验。对应既有 CAS/epoch，不引入 etcd |
| [Microsoft 进程句柄与标识](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers) | 页头有授权提示，但完整说明正文可读，无登录 | PID 身份有效期截至该进程结束；不能用裸 PID 代表跨进程业务 |
| [Python tempfile](https://docs.python.org/3/library/tempfile.html) | gettempdir/tempdir 正文成功 | 搜索先 TMPDIR、TEMP、TMP，再平台候选；结果缓存。建议显式 dir，不全局改 tempfile.tempdir |
| [Microsoft GetTempPathW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-gettemppathw) | 页头有授权提示，但 Remarks 正文成功 | 查 TMP、TEMP、USERPROFILE、Windows 目录；不保证返回目录存在或有权限，保留符号链接 |
| [Microsoft GetTempPath2W](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-gettemppath2w) | 同上，正文成功 | 普通进程同上述顺序；SYSTEM 有专门 SystemTemp 规则。普通 UAC 管理员不能假定为 SYSTEM |
| [Rust std::env::temp_dir](https://doc.rust-lang.org/std/env/fn.temp_dir.html) | 官方正文成功 | Windows 使用 GetTempPath2/GetTempPath；不能假定与 Python 的顺序、环境快照和缓存相同 |

游戏点击没有 AWS 服务端幂等事务保障。原输入可能已发生而结果/后帧未落盘时，只能保持 unknown 并回读，不能借“恢复检查点”重放整段点击。当前唯一 broker 已承担输入串行，本项目无需重做通用 leader election。

## 最小业务延续建议

1. 为本场业务保存稳定 business_id/match_id；每次租期仍有新 run_id/launch_id、精确进程身份与新 epoch。父 run 仅用于追溯，旧输入授权不继承。
2. 沿用持久 records 保存目标、最后实证节点、结算、已证实累计经济账与未决请求。支出/购买数不能因重启清零；旧 ROI、商店、准备完成标记、出战批准不能当新事实。
3. publication_guard 可以先持久写意图，实际收据返回后保存原结果。意图写后发布前崩溃、已发布无结果等窗口应区分或保持 unknown。清理前先成功归档必要完整 request-ledger；启动时归档只是兜底，因为旧目录可能已删除。
4. 到期仍停 worker/broker 并核释放/退出，业务标待续接。只有真实整局结算闭合该局；停止当前授权不等于游戏结束。新租期走原入口，不新增无限保活线程。
5. 在既有启动互斥内核旧 worker/broker 确切退出，对当前 owner/revision 作条件归属变更。旧 lease 的晚到 publish/shutdown 不得覆盖新 owner；每次持久更新仍核当前身份。
6. 新租期先读当前帧，发起同局复核。原任务意图、当前资源与主管当前证明共同绑定新请求/epoch；同游戏 PID、同 stage 都不能单独证明是同一局。
7. 旧 pending 不重发，也不能从新账中静默消失。可经当前完整人工整理对账标 superseded，但旧交易仍可保持 unknown；在对账前不能把未定支出/购买数当零来解封预算。
8. 同进程 new_match 到真实 setup 页时也切换业务身份。已 closed 业务不能被下一局 publish 续写，上一局账目不带到新局。

磁盘检查点不能与游戏输入组成原子事务；中间状态仍需原意图/收据和当前观察对账。

## 统一运行根目录合同

两实现线约定四字段：schema=1；source 为 installed_bridge 或 standalone；runtime_root 为已选绝对目录；installation_id 为已装组件的 32 位小写十六进制身份，standalone 为 null。字段只是声明，不能单独授权任意根。

- 有固定组件时通过现有完整配置/SID/ACL/清单/哈希验证，采用原 bridge_task 批准的 D 根。只有组件明确不存在时才 standalone；损坏、缺盘、权限错误、安装 ID 改变或显式配置不符都报错，不暗退 C 盘。
- 父入口选好后显式传 GUI launcher/worker；创建、read_marker、protect_children、关闭均使用该选择或实际 run.parent，不在清理时重新查询 TEMP。
- GUI 根需绑定实际父启动链：--runtime-dir 的 owned marker、启动器 PID+creation、登记的本 GUI 子进程、chat 与位置记录。不能只相信 worker owner 自报路径。installed 仍只准固定 D 根，standalone 只采用父入口已选择根。
- Popen 后子 GUI 可能先于 Python 登记 protected_children 启动；须有界启动握手，未登记时不假称已验证。只在启动做此等待，不新增逐点击固定延迟。
- GUI 初始化可核一次父链并冻结 root；每次 Binding 验证仍核 location/marker/owner 漂移，避免每轮组件全量 hash。旧合法系统 TEMP 协议可兼容；新协议字段缺失/篡改不能降级 legacy。
- 保留无链接/联接、绝对且直接子目录、marker root/path、run/PID+creation/chat/token 原守卫。路径前缀不能代替身份，D:\allowed-other 也不是 D:\allowed。
- 不改全局 TEMP 或 tempfile.tempdir，不移动活动 run，不放宽清理上限，不改固定组件批准路径来迁就错误选择。

## 聚焦反例与验收边界

复用现有 fixture 和真实入口即可：

| 反例 | 要求 |
| --- | --- |
| 租期中途到期再启动 | 旧授权确切结束，业务待续接；新 run 先回读，不自动新开局 |
| 同一进程开始下一局 | 新业务/match；旧 closed 状态和结果计数不复用 |
| PID 复用 | 旧精确身份已不在；不停止当前复用 PID |
| 父 run 已删仍有 pending | 可读原归档收据；缺失保持未决，不以清零/重发恢复 |
| 旧 lease 晚到写、并发 Start | 当前 owner/revision 才可写，旧状态不覆盖新归属 |
| 意图已存但发布/结果未知 | unknown，只查原收据/新观察 |
| 默认 C、已装 D | GUI、worker、桥与清理一致采用已选 D |
| 配置坏、安装身份变化、显式 root 错 | 明确拒绝，无 fallback 或第二启动通道 |
| owner 声称任意根、location 缺失/变化 | 新协议拒绝；旧协议仅原合法系统 TEMP 与完整身份可兼容 |
| GUI 抢先启动、父链未登记 | 有界等待实际登记，无输入；超时明确失败 |

本环境无 Windows 游戏/输入桥实机验证。根已实际确认 cargo --version 返回 127；Rust 改动只能记录源码审与留待本机执行的现有 crate 用例，不能标编译或 GUI 实机 PASS。本研究本身没有启动 worker、broker、游戏或后台调度器。
