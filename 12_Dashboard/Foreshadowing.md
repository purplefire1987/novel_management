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

## 状态与规则

状态机、四条硬规则、谁能进哪个状态——**一律看 [[伏笔规则]]**。
本页只管「按回收窗口排序看谁该收了」，不复述规则（复述必然与代码漂移）。
- 章节比较按数值，不按字符串。
