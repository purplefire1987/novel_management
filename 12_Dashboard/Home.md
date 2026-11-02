---
type: dashboard
---

# 白骨渡·工作台

## 当前进度
- 当前卷：[[02_Hot_Memory/Current_Volume|卷一·白骨渡]]
- 当前章节：CH-0002《渡口无灯》
- 当前剧情状态：[[02_Hot_Memory/Current_Story_State|查看]]

## 常用命令

```bash
novel preflight CH-0002      # 写前资料包 + 冲突检查
novel write CH-0002          # 主笔生成草稿（进 staging）
novel review CH-0002         # 总编审核草稿
novel publish CH-0002        # 作者确认并锁定正式正文
novel commit-memory CH-0002  # 提取记忆提案
novel apply-memory CH-0002 --approve-all   # 审批并正式写入
novel audit CH-0002          # 写后连续性回归审计
novel rollback <operation_id> # 回滚
novel rebuild-index          # 从 Markdown 重建 SQLite
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
