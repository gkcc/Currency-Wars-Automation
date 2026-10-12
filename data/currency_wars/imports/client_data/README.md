# 货币战争公开客户端静态数据

当前归一化母版是 `normalized_current.json`，对应公开发布数据仓库 [DimbreathBot/TurnBasedGameData](https://github.com/DimbreathBot/TurnBasedGameData) 的 [4.6.0 固定提交](https://github.com/DimbreathBot/TurnBasedGameData/commit/312b4597691e0a29cf9828a15b592757dba86949)。提交原文为 `OSPRODWin4.6.0_D16728363_A16704710_L16700845`。这是公开提取的数据仓库，并非发行商官方仓库；本离线工作没有检查游戏内面板或读取游戏进程。感谢 Dimbreath 维护发布数据，原始仓库 README 保留在各发布目录。

`release_4_4` 和 `normalized_baseline_4_4.json` 是 [4.4.0 历史基线](https://github.com/DimbreathBot/TurnBasedGameData/commit/b11066beacc4de454b625fafc7ea3dd540c5bbf3)，只用于差分。攻略的 4.4 标签不能代替当前客户端版本。未下载或激活 Nanoka 的 4.6.51 测试数据、4.7 或其他未来版本。

| 类别 | 4.6 配置条数 |
| --- | ---: |
| 角色／费用变体 | 77 |
| 羁绊 | 33 |
| 装备 | 148 |
| 投资环境 | 84（83 登记于赛季，1 未登记） |
| 投资策略 | 334 |
| 竞争对手阵营 | 25（20 允许随机生成） |
| 敌人单位 | 162 |
| 敌人词条 | 55 |

`availability=current` 只表示来自当前发布配置。它不保证自然商店能够刷出该条目。角色顾问属性、赛季／模块限制、初选排除、切换条件及商店来源另存。丹恒·饮月的 1 星买价是 2；银狼 LV.999 的三个角色 ID 保留各自 3／4／5 买价。费用来自 `GridFightShopPrice.BuyGoldStar1`，没有使用通用角色稀有度代替费用。

4.4 和 4.6 的全部 153 张 `GridFight*.json` 表、关联角色／敌人基础表与中文 TextMap 已保存。4.6 另保存 201 份表中引用且公开可下载的 Config。共 543 个源文件，218,906,550 字节；各文件的 Git blob SHA-1、SHA-256、源 URL、字段和记录数见 `manifest.json`。源树清单未截断；原始文件是重建证据，请保留。

`audit_report.json` 记录收集及关联检查。静态表集合已收齐，战斗模拟仍不受支持：819 个指定类型 Config 引用中，618 个不在固定公开树中；161 个中文文本哈希未发布，其中敌人技能名称／描述 147 个、羁绊 buff 描述 13 个、未纳入装备表的道具名称 1 个。对应源位置、哈希和影响见 `unresolved_text_report.json`。存在缺失文本的敌人技能保留实际 `HideInUI` 字段，不把全部缺失都解释为隐藏或无关。

`#参数` 的静态展开保留模板和原始参数，只在模板显式带百分号时乘 100。展示舍入、动态星魂修正、最终伤害、叠加和利息公式没有推测。`GridFightSkillDescMod` 以明确技能类型绑定来源：117 条后台描述、7 条侍从描述（逐等级展开成 85 条）；31 条未提供技能类型的描述保留无参数模板。前台和后台可能使用同一 SkillID，绝不能只按 ID 套用参数。属性 `Value` 字段省略时保留 `null` 和缺失标记，不猜默认值。

经济数据保留买卖价格、升级经验／人口、三套权重表、常量与来源。V2 常量明确含 `GridFight_CardPoolNumber=[30,25,18,10,9]` 和买卖权重变化／StolenPool 返还参数；其数组单位、每角色有限副本、持有扣减自然池、出售返还自然池及当前模式启用条件仍为 `unknown`。这些字段存在，不等于该运行规则已证明。权重表在部分等级不同，`shop_odds.effective_profile=unknown`，不可自动挑一张表作为当前概率。

`release_diff.json` 按精确稳定 ID 对比版本。4.6 新增两个敌人单位和四个侵蚀词条；原始表／字段差异和归一化可见效果变化分别列出，空字段增删等结构变化不直接称为数值平衡变化。

重建使用 Python 3 标准库。在本目录依次执行：

```powershell
python -B -X utf8 download_client_data.py
python -B -X utf8 download_client_configs.py
python -B -X utf8 normalize_client_data.py
python -B -X utf8 finalize_client_data.py
```

下载步骤需要公开 GitHub 网络访问；已有完整原始数据时只运行最后两步即可离线重建。规范化文件包含生成时间，因此重建后的文件 SHA 可以不同，原始来源 revision 和每个文件的校验依据保持固定。此目录没有游戏控制器、运行常驻进程或额外第三方依赖。
