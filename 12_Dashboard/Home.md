---
type: dashboard
---

# 九州·工作台

## 当前进度
- 当前卷：[[VOL-0001_工坊疑云|卷一：工坊疑云]]（大纲已录，两章已批准写作）
- 当前剧情状态：[[02_Hot_Memory/Current_Story_State|查看]]
- 总纲：[[04_Outline/Series_Outline|九州·总纲]]
- 章节大纲：[[CH-0001_雨夜的血案|CH-0001]] · [[CH-0002_石季崇的试探|CH-0002]]

## 索引
- 地图：[[01_Core_Memory/Maps/九州地图|九州地图]]（11 大区 / 72 城邑，州→城层级）· [[01_Core_Memory/Maps/九州地图.canvas|Canvas 方位图]]
- 全量实体索引：[[07_Entity_Graph/Entity_Index|实体总索引]]（708 条，按类型分组，`novel rebuild-index` 自动刷新）
- 动态表格视图：[[12_Dashboard/九州视图.base|九州视图]]（人物 / 势力 / 地点 / 物品 / 敌人 / 设定 / 大纲）
- 待裁定事项：[[12_Dashboard/待裁定事项|待裁定事项]]（镜像自 WorkBuddy 项目任务）

## 世界底座
- 底层架构：[[WORLD-0002_三质_物质灵力魂魄|三质]] · [[WORLD-0003_三重循环_灵力总纲|三重循环]] · [[WORLD-0004_第一因_扩张与失衡|第一因]]
- 大盛帝国：[[WORLD-0030_国史与货币|国史与货币]] · [[WORLD-0031_军制与武装力量|军制]] · [[WORLD-0032_通用机兵力士|力士机兵]] · [[WORLD-0033_科技体系|科技]] · [[WORLD-0034_政治与七人议会|政治]] · [[WORLD-0035_礼俗节庆与历法|礼俗历法]] · [[WORLD-0036_语言与现状|语言与现状]] · [[WORLD-0037_修士管理|修士管理]] · [[WORLD-0038_鸿钧分析机|鸿钧分析机]] · [[WORLD-0039_货币与物价|货币与物价]]
- 规则书：`01_Core_Memory/World/九州/规则书/`
- 总表：`01_Core_Memory/World/九州/总表/`

## 常用命令

```bash
novel status                 # 章节与索引状态
novel find 环教               # 按名称/别名/概要查实体档案（录入前必查）
novel rebuild-index          # 重扫 Markdown → 重建索引库 → 刷新实体索引页
novel index-md               # 只重画实体索引页
novel preflight CH-0001      # 写前资料包 + 冲突检查
novel write CH-0001          # 主笔生成草稿（进 staging）
novel review CH-0001         # 总编审核草稿
novel publish CH-0001        # 作者确认并锁定正式正文
novel commit-memory CH-0001  # 提取记忆提案
novel apply-memory CH-0001 --approve-all   # 审批并正式写入
novel audit CH-0001          # 写后连续性回归审计
novel dice check --dc 55     # 骰子娘：按规则书裁决一次 d100
novel history                # 版本状态与提交历史
```

## 地图与地理
- 总图：[[01_Core_Memory/Maps/九州地图|九州地图]]（州 → 城 层级，含地貌与口语）
- 方位图：[[01_Core_Memory/Maps/九州地图.canvas|九州地图.canvas]]（中州居中，八州环列）
- 卷一舞台：[[LOC-0057_武安都|武安都]]（湖州·江陵府·江陵郡；四区：武、光、楚、汉）
- 重生成：改 [[WORLD-0029_九州表|九州表]] → `python 00_System/scripts/build_jiuzhou_map.py`

## 快速入口
- [[12_Dashboard/Characters|人物总览]]
- [[12_Dashboard/Foreshadowing|伏笔账本]]
- [[12_Dashboard/Chapters|章节进度]]

## 规则提醒
1. 正式事实源只有 Markdown/YAML；SQLite 随时可重建。
2. Agent 的所有产出都是提案，不自动成为 Canon。
3. 重大设定变更、人物死亡、伏笔回收必须由作者批准。
4. 角色「以为」的事，不等于客观事实。
5. 示例项目「白骨渡」已移出 Vault（`../.trash_20261104/`），不再出现在视图与索引中。
