# 阶段读取实测：减少了无关调用，尚未证明总读图提速

## 结论与本批边界

同环境、同十张历史 PNG、同顺序的两组独立进程实测中，阶段读取确实跳过了不需要的调用，但**没有证明总读图时间改善**。预热十图的外层实读时间由 full 的 **12.3426 秒**变为 scoped 的 **13.1078 秒**；第一遍含引擎冷起的十图则由 12.2149 秒变为 11.9990 秒。两遍方向不一致，不以其中较好的一遍宣称提速，也不增加循环寻找较好数字。

预热十图中，主 OCR 的互斥区间由 10.5398 秒增至 11.8607 秒，其余读取由 1.8024 秒降至 1.2469 秒。调用记录确认主 OCR 的模型入口、完整图片尺寸和次数没有变化，StateReader、领奖无关的 ShopReader 和数字补读确实减少；本次单次顺序测量不能把主 OCR 区间的变化因果归于某一个宿主因素。

本批生产继续保留全帧主 OCR。基线中它占 85.83%，后续 q01/q00 拆分又显示文字识别是主 OCR 的主要部分，但当前页面判定依赖全局文字及优先级。直接漏掉中央弹窗文字，可能仍保留备战锚点而无法进入 unknown 回退。局部主 OCR 尚没有足够证据覆盖这类状态，不在这批以删掉该证据换取较小数字。基线原数据与内部热点见 [BASELINE_HOTSPOTS.md](BASELINE_HOTSPOTS.md)。

## 可复核来源

完整读取数据是 [candidate-full.json](candidate-full.json)，阶段读取及逐项对比是 [candidate-scoped.json](candidate-scoped.json)，派生核对摘要是 [VERIFICATION.json](VERIFICATION.json)。测量脚本是 [replay_currency_wars_perception_scope.py](../../../tools/replay_currency_wars_perception_scope.py)。

测量时 checkout 的提交基点为完整 PR12 `5e46351372ff26980a92a9806909c732607f50ea`，另含尚未提交的候选读取改动；不能将 JSON 中该 HEAD 当作本次候选代码已经提交的 SHA。实际执行的读取源码 SHA256 为：

| 读取文件 | SHA256 |
| --- | --- |
| `currency_wars_perception.py` | `ee33649258691ab3fea3f0f25c0e21ff0d7bb8ea1e0f012838264de2e789a735` |
| `currency_wars_shop_reader.py` | `e6a069766c010021875d3f002db935af741eb5692af1266abccfa8b8f6338bc3` |
| `currency_wars_state_reader.py` | `69acca4d20625fc23f41fa9872b9855fd9f3f526a1f29b949e432dd9422ed8a2` |
| `currency_wars_rewards.py` | `7afc4216ed9f72c1c857e99350871b436e90dc7b9e9b58198df4b86e68cfbc25` |

上述四文件在两组之间相同，且每组运行前后均相同。脚本没有实例化 Worker 或 Entry，没有调用 Runner；同期 Runner 的其他修改不属于这次读图计时，也没有被包含在“读取源稳定”的表述中。

环境为同一 Linux x86_64 宿主、Python 3.12.14，已安装 ONNX Runtime 1.30.0、RapidOCR ONNX Runtime 1.4.4、NumPy 2.3.5、Pillow 11.3.0、OpenCV 4.13.0.92。ONNX 遥测在导入前通过环境变量关闭，导入后再调用关闭 API；没有下载依赖或资源。

输入严格为 [reward-sequence/manifest.json](../reward-sequence/manifest.json) 的 q00、q01、q02，随后 [refresh-sequence/manifest.json](../refresh-sequence/manifest.json) 的 r00 至 r06。每张完整 1920×1080 PNG 都先核对导出 SHA256。主 OCR 使用生产原有的 1280×720 缩放，数值补读也来自生产原有区域；没有合成空槽、改 OCR 文本、填入 confidence，或用裁剪图替代真实帧。

每个模式一个独立进程，按上述顺序执行第一遍及第二遍，每张均 `force=True` 真读，随后 `force=False` 读同 PNG 同 scope 缓存。每模式各 20 次实读与 20 次缓存。只有第一遍 q00 构造冷引擎，不能把第一遍其余九图称为九次冷起。full 模式每张显式请求 `full`；scoped 模式对 q 图请求 `rewards`、对 r 图请求 `economy`。本批共用既有历史输入，无游戏操作、新截图、控制器、等待或实际执行阶段。

## 实读与缓存分开

单位为秒；“变化”是 scoped 减 full。表中总实读采用调用者外层时钟，包含该次完整函数调用及计时包装成本。

| 实读组 | full | scoped | 变化 | 观察比例 |
| --- | ---: | ---: | ---: | ---: |
| 第一遍 q 三图，含首次引擎构造 | 3.8777 | 3.8685 | -0.0092 | -0.24% |
| 第一遍 r 七图 | 8.3372 | 8.1305 | -0.2067 | -2.48% |
| 第一遍十图合计 | **12.2149** | **11.9990** | **-0.2159** | **-1.77%** |
| 预热 q 三图 | 3.0681 | 3.6814 | +0.6133 | +19.99% |
| 预热 r 七图 | 9.2745 | 9.4264 | +0.1519 | +1.64% |
| 预热十图合计 | **12.3426** | **13.1078** | **+0.7653** | **+6.20%** |

这些是本次原样观测值，不是统计置信区间或整局速度倍率。两遍 20 张实读总计 full 24.5575 秒、scoped 25.1069 秒。

| 独立初始化与缓存区间 | full | scoped |
| --- | ---: | ---: |
| ONNX Runtime/RapidOCR 模块导入 | 0.1303 | 0.1245 |
| 生产模块导入 | 0.0015 | 0.0014 |
| 首次 q00 内 OCR 引擎构造 | 0.1459 | 0.1622 |
| 首次 q00 总实读，已包含该构造 | 1.7000 | 1.8540 |
| 第一遍十次同 PNG 缓存合计 | 0.0191 | 0.0164 |
| 预热十次同 PNG 缓存合计 | 0.0241 | 0.0167 |

模块导入在帧外单列，引擎构造在首帧内，不能再次加到首帧。缓存单列，不加入实读表。候选所有缓存返回 `read_timing.cache_hit=true`，`elapsed_ms` 是当前缓存调用实耗；所有强制读取均为 `cache_hit=false`。返回对象与原实读对象不同，选定语义一致。旧 PR12 保存结果的缓存 `elapsed_ms` 仍是首次计算值，已在旧证据中独立注明，未改写成新契约。

## 预热互斥组件与方法包含关系

方法开始、结束及父调用 ID 全部保留；从每个方法包含时间中扣除直接子调用，得到互斥区间，再按实际调用栈归类。下列各行可相加；十图数值与 Perception 方法总区间的浮点残差不超过 2.3×10⁻¹⁶ 秒。外层总实读额外含少量函数计时包装时间，所以不与内部总区间强求逐位相等。

| 互斥组件 | full 秒 | scoped 秒 | 变化秒 |
| --- | ---: | ---: | ---: |
| 主 OCR，全帧检测与识别 | 10.5398 | 11.8607 | +1.3209 |
| 主 OCR 之外的数字补读 | 0.1546 | 0.1092 | -0.0454 |
| ShopReader 内部 OCR | 0 | 0 | 0 |
| ShopReader 其余处理 | 0.3524 | 0.3226 | -0.0298 |
| StateReader | 0.4273 | 0 | -0.4273 |
| 奖励检测与规则 | 0.0325 | 0.0448 | +0.0124 |
| 其他图像解码、字段、哈希和读取处理 | 0.8356 | 0.7703 | -0.0653 |
| Perception 方法总区间 | **12.3422** | **13.1076** | **+0.7654** |

`RapidOCR.__call__` 的方法包含时间既可来自主 OCR，也可来自数字补读，ShopReader 完整资源下还可能从其内部调用它。因此该方法总时间不可与上表的 OCR 各行再次相加。`TextDetector`/`TextRecognizer` 的成员时间是 OCR 的子区间，也不可再加一次。`ShopReader.read` 的包含时间包含其内部 OCR 与加载；上表只把扣除内部 OCR 后的剩余时间放入“其余处理”。当前公开环境实际没有触发商店内部 OCR。

| 每遍十图实际调用次数 | full | scoped |
| --- | ---: | ---: |
| 主 OCR，输入 `(720,1280,3)` | 10 | 10 |
| 主 OCR 之外的数字补读 | 10 | 7 |
| ShopReader.read | 8 | 7 |
| StateReader.read | 10 | 0 |
| native_player_hud | 10 | 7 |
| 奖励 detect | 10 | 10 |
| 方向分类器 | 0 | 0 |

各模式第一遍、预热遍均符合上述调用数。方向分类一直 `use_cls=False`，不算本次优化。奖励模式减少 q 三图的额外 HUD/人口等读取，经济模式保留七张的玩家 HUD 与商店读取。该调用削减是已观测到的代码效果；用户关心的总实读提速尚未成立。

## 逐图预热实读

单位为秒。主 OCR 列已经包含于前面的总实读；保留这一列可看到本次主要时间变化发生在哪里。

| 图片 | full 总实读 | scoped 总实读 | 变化 | 主 OCR，full → scoped |
| --- | ---: | ---: | ---: | ---: |
| q00 | 1.1872 | 1.5419 | +0.3547 | 1.0272 → 1.4651 |
| q01 | 0.8279 | 1.0303 | +0.2024 | 0.6698 → 0.9280 |
| q02 | 1.0529 | 1.1092 | +0.0562 | 0.8942 → 1.0106 |
| r00 | 1.0712 | 1.2872 | +0.2160 | 0.8964 → 1.1529 |
| r01 | 1.1219 | 1.4875 | +0.3657 | 0.9464 → 1.3391 |
| r02 | 1.1494 | 1.3557 | +0.2062 | 0.9702 → 1.2168 |
| r03 | 1.1515 | 1.3739 | +0.2224 | 0.9705 → 1.2345 |
| r04 | 1.5426 | 1.2961 | -0.2466 | 1.3363 → 1.1528 |
| r05 | 1.8584 | 1.3664 | -0.4920 | 1.6459 → 1.2365 |
| r06 | 1.3795 | 1.2597 | -0.1198 | 1.1829 → 1.1243 |

## 阶段语义与资源缺口

所有实际帧的页面、阶段、金币、奖励控件及奖励状态在 full/scoped 间一致。刷新七图都保留实际玩家等级 8，金币仍为 70→68→66→64→62→60→58。领奖图的等级、人口输出被显式设为 `None` 并列入 `read_contract.unread`，不是将未知强行判定为空，也不用于完成阵容验收。

| 图片或组 | 本次两种读取共同结果 | 阶段省略 |
| --- | --- | --- |
| q00 | 商店展开；奖励区被遮挡；不允许领取输入 | 商店与阵容 `not_read`，玩家 HUD/人口/HP 不作完成证据 |
| q01 | 备战，两个蓝球目标，坐标与匹配分数一致 | 同上；`all_rewards_cleared=null` |
| q02 | 备战，原生侧边提示需主管复核，`input_allowed=false` | 同上；没有推断全部已领取 |
| r00…r06 | 商店页，玩家 8 与金币正确；商店缺公开资源仍未知 | 阵容、库存 `not_read`；商店与玩家 HUD 继续实读 |

本公开检出缺 `tools/shop_reader_resources/SOURCES.json`，full 的八张商店帧与 economy 的七张商店帧均实际返回 `status=error, ok=false`，所以商店内部 OCR 为零。full 的场上角色读取也未完成，已知场上数量为零不表示场上为空。窄读是 `not_read`，与已经尝试读取但缺资源而未知区分开。

ROOT 的 [ROOT_LOCAL_RESOURCE_REPLAY.json](../ROOT_LOCAL_RESOURCE_REPLAY.json) 已另外记录本机 57 个资源、250477 字节及七商店均 `shop_ok=true`；[ROOT_REWARD_RESOURCE_REPLAY.json](../ROOT_REWARD_RESOURCE_REPLAY.json) 则保留其领奖图资源重放结果。本公开环境的耗时不代表 ROOT 本机完整资源下的 ShopReader/StateReader 耗时；不能据此说 ROOT 缺同类资源。下一份最小本机补证是用本脚本和原有资源顺序运行 full/scoped，回传两份计时 JSON 即可，不需要公开私人素材、缓存正文或采新游戏图。

这十张真图全是 shop/preparation，未实证其它页面的 full 回退成本。未执行领取或刷新，也未覆盖两次历史蓝球点击之间缺失的中间图，不重命名为完整领奖或完整备战验证。当前数字不能解释历史工具外 87.58 分钟，也不能把它归为 OCR 或主管思考。

## 复现命令

依赖与本地模型已安装时，在候选仓库根目录顺序运行，不并行跑另一组 OCR：

```bash
ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_perception_scope.py \
  --code-root . --mode full --output offline-results/perception-full.json

ORT_DISABLE_TELEMETRY=1 PYTHONPATH=/path/to/test-deps python -B -X utf8 tools/replay_currency_wars_perception_scope.py \
  --code-root . --mode scoped --compare-to offline-results/perception-full.json \
  --output offline-results/perception-scoped.json
```

Windows 使用现有已验证 Python 环境执行同脚本；例如在 PowerShell 设置 `$env:ORT_DISABLE_TELEMETRY='1'`，再顺序执行两条 `python -B -X utf8` 命令。`PYTHONPATH` 仅在依赖安装于独立目录时设置，不为本脚本重新安装私有资源。

**已修的范围**是按业务阶段省略不需要的结构化读取，并明确未读取字段与本次缓存耗时；**离线已验的范围**是这些调用确实减少、十张现有真图的必要阶段事实保留、缓存不冒充实读；**仍缺的证据**是 ROOT 完整资源同机对照的稳定总收益、其它页面与遮挡回退的真图范围，以及完整业务阶段/整局耗时改善。没有将本批有限调用削减称为用户速度问题已经解决。
