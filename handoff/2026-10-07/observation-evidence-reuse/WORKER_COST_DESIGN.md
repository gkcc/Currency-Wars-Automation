# 同一个 Worker 消费者终点的离线成本审计

脚本：[replay_currency_wars_worker_read_cost.py](../../../tools/replay_currency_wars_worker_read_cost.py)。本文件记录先于补丁确定的窗口设计；现已按原脚本完成基线和候选执行，实测见 [RESULTS.md](RESULTS.md)。下面的“预期”保留为运行前假设，不能替代 JSON 中的实际调用。

测量边界是**真实 OCR、Worker 业务方法和惰性 Entry 的本地窗口**。复用的 `ready_fixture` 将 `worker.publish/log` 保留为内存替身；`decision-request.json`、原始证据 PNG、展示图和惰性回执确实写盘，但生产状态文件／journal 的发布、ROOT 模型、ROOT 响应与游戏等待未测。因此“请求发布终点”指本地请求已写出、替身进入等待状态，不是生产端到端延迟。

## 固定窗口

| case | 输入与调用 | 确切终点 | 边界 |
| --- | --- | --- | --- |
| `q02_selection_request` | q02 真 PNG，生产 Perception；`observe(rewards)` → `tick` → `ask` 同请求 full 升级 | `reward_selection` 请求已构造、原图已保存、JSON 已写出，内存发布替身进入等待状态 | 两次 Perception 调用；包含窄读、full 升级、规则和本地交接。没有回放两球中间动作 |
| `q00_q01_original_completed` | q00 `observe(full)` → `tick` → 重定位 `observe(rewards)` → 惰性导航后图 q01 `rewards` → 原 completed 栅栏拒绝 → `ask` full | `reward_result` 异常请求，原 reward pending 保留 | 原坐标和原 wait 均原样保留；不将失败单因果归为等待差异。q01 是历史映射，并非一次新的单球成功后图 |
| `six_d_protocol` | 既有生成数字/空商店 fixture 语义，显式 scope；初始 full/预算请求 → 回复重观 → 六个 economy 后图 → 最后 full 升级 → 本地经济完成 → 再 `tick` | `lineup_equipment` 阶段的 `shop_strategy` 请求，pending false，协议台账刷新 12 | 无真实 OCR；预算、前三阶段、攻略、商店事实和 matched 回执均为独立协议前提 |
| `three_f_protocol` | 同类 fixture，初始 full/请求 → 回复重观 → 三个 economy 后图 → full → 完成 → 再 `tick` | 同一布阵交接终点，pending false，协议台账经验 12 | 没有可替换成真实三 F 的连续旧图；不报告为游戏执行或真图成本 |

从第一次 Worker.observe 进入开始，到终端请求发布返回结束；不把初始 ask 放在计时外。六 D 的静态预期为 9 次 Perception.read，三 F 为 6 次；它们是待运行核对的预期，不是本文件宣称已经观察到的结果。完整窗口的正常 ROOT 请求预期为 2（初始预算、末次布阵交接），旧 replay 的 1 使用了较短终点，不是产品回传次数退化。

真实 q02 当前基线预计两次主 OCR；q00/q01 当前基线预计四次。新增底层证据共享是否减少其中升级调用，必须看真实 RapidOCR 调用记录，不用 scope、force 名称或测试数量代替证据。

## 为什么不能直接重跑旧 replay 的总毫秒

旧经济与奖励 protocol reader 没有 `read_contract`，会被当前兼容函数当成旧 full 观察。因此旧 fixture 不触发真正的窄读→full 成本。本脚本为协议观察显式声明 scope/未读字段；查表仍归为 `protocol_fixture_lookup`，绝不归为 OCR。生产真图观察不注入 HUD、confidence、完整商店或阵容字段。

除 Perception 外，`Worker.economy_observation` 还会调用独立数字 ROI/hash/OCR；其缓存键含读取 scope，末帧升级可能使它再读。该层及策略、守卫、原回执核验、证据写出和交接均须进入完整窗口。

PR13 基线的 `ensure_full_observation` 在升级时强制 full；`ask` 先升级再检查旧请求是否去重。已有 full 对象通常可直接返回，但把同身份的旧 narrow 对象再次交消费者，不能从 ROOT 请求数推断实际只 full 读过一次。脚本逐次保留 caller、request/frame/SHA、scope、force、reuse_primary、返回契约/缓存元数据及真实主 OCR 调用数。

## 计时与冷热口径

每个 case 只跑冷、暖各一轮。暖真图用新 Perception 和新 Worker，因此结果缓存与底层主 OCR 缓存均从空开始；仅继承该 case 冷轮已经初始化的 engine、shop_reader、state_reader。协议两遍的 `inference_temperature=null`，不能称作模型冷暖推理。

直接复用已存在的 Trace 父子区间方法。互斥组件为引擎初始化、主 OCR、字段 OCR、ShopReader 内部 OCR、商店其余处理、StateReader、奖励检测、Worker 规则/策略、惰性发布、其余读取/Entry/IO，以及协议查表。完整窗口另保留外层墙钟时间。方法包含区间不可重复相加；规则/策略桶中残留的内联记账会如实说明，不把它包装为纯模型思考。

`wait:0.7` 仅是请求参数。惰性发布实际只写回执和帧文件，未等待游戏动画；游戏等待、主管等待与主管模型成本均为 null。重复 PNG 在不同 observe 请求下出现时另记，不据此假设真实游戏重观的像素永远不变。保留 cache_hit 与真实主 OCR 次数；将 force=True 保留为真读时，显式 reuse_primary 升级的调用亦单独标明。

终点比较包括页面、阶段、必要字段、金币、奖励状态、pending、台账、请求种类/次数与角色读取覆盖。输出使用 occupied_board_slots，并分别记录实名、星级及实名与星级同时读到的数量，不能把占用槽数称为角色身份已识别数。不同终点不给净时间差。

## 真七 D 仍缺的完整成本证据

七张 PNG 与六笔实际付费 completed 支持真实刷新子链。当前公共检出缺完整 ShopReader 素材，而且公开交接没有初始 `free_refreshes` / `refresh_cost` 的完整已核数值 ROI 绑定；原付费回执不能自动补成当前 `free_refreshes=0`。本批不新造输入框架、不用假空槽补事实，也不实现七图完整 Worker 真读闭环。

ROOT 原资源存在、此前七商店可读的报告继续独立有效。将来需要完整真七 D 净成本时，最小输入是现有本机资源加可追溯的初始经济字段/预算绑定，在原守卫下顺序运行；遇到 unknown 按实际终点停止。原 wait(.4) 与当前 wait(.7) 的精确交付负例和 matched inert adapter 必须分开，不能将适配回执称为原生产成功。

## 顺序运行

使用已安装依赖和模型，在独立进程按顺序执行：

```bash
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_worker_read_cost.py \
  --code-root /path/to/pr13-baseline --output offline-results/worker-cost-before.json

ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_worker_read_cost.py \
  --code-root /path/to/candidate --compare-to offline-results/worker-cost-before.json \
  --output offline-results/worker-cost-after.json
```

需要先审真图层时可加 `--cases q02_selection_request q00_q01_original_completed`。不得并行计时。报告记录真实读取栈和 Runner/经济/Entry 源哈希；不导出宿主 hostname。不报告 P95、整局提速或历史工具外 87.58 分钟的因果归属。
