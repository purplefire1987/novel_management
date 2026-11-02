---
type: dashboard
---

# 伏笔账本

```dataview
TABLE id, status, planted_in, target_reveal_window.start AS 最早, target_reveal_window.end AS 最晚, importance
FROM "06_Plot_Ledger/Foreshadowing"
WHERE type = "foreshadowing"
SORT target_reveal_window.start ASC
```

## 状态说明
`planned` → `planted` → `hinted` → `triggered` → `paid_off`，异常态：`abandoned` / `overdue`。

## 硬规则
- 当前章早于最早回收章：不得标记 `paid_off`。
- 当前章晚于最晚回收章且未回收：标记 `overdue`。
- 触发条件未满足：不允许自动进入 `triggered`。
- 章节比较按数值，不按字符串。
