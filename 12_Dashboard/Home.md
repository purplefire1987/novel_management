---
type: dashboard
---

# 九州·工作台

## 当前进度
- 当前卷：[[VOL-0001_工坊疑云|卷一：工坊疑云]]（大纲已录，待批准写作）
- 当前剧情状态：[[02_Hot_Memory/Current_Story_State|查看]]
- 总纲：[[04_Outline/Series_Outline|九州·总纲]]
- 章节大纲：[[CH-0001_雨夜的血案|CH-0001]] · [[CH-0002_石季崇的试探|CH-0002]]

## 世界底座
- 底层架构：[[WORLD-0002_三质_物质灵力魂魄|三质]] · [[WORLD-0003_三重循环_灵力总纲|三重循环]] · [[WORLD-0004_第一因_扩张与失衡|第一因]]
- 规则书：`01_Core_Memory/World/九州/规则书/`
- 总表：`01_Core_Memory/World/九州/总表/`

## 常用命令

```bash
novel status                 # 章节与索引状态
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

## 快速入口
- [[12_Dashboard/Characters|人物总览]]
- [[12_Dashboard/Foreshadowing|伏笔账本]]
- [[12_Dashboard/Chapters|章节进度]]

## 规则提醒
1. 正式事实源只有 Markdown/YAML；SQLite 随时可重建。
2. Agent 的所有产出都是提案，不自动成为 Canon。
3. 重大设定变更、人物死亡、伏笔回收必须由作者批准。
4. 角色「以为」的事，不等于客观事实。
5. 示例项目「白骨渡」已归档在 `99_Archive/Legacy_Example_白骨渡/`，不参与索引。
