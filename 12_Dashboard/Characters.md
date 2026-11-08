---
type: dashboard
---

# 人物总览

```dataview
TABLE id, race, canon_status, aliases
FROM "01_Core_Memory/Characters"
WHERE type = "character"
SORT id ASC
```

## 当前状态

```dataview
TABLE character_id AS 人物, 位置, 伤势, 客观生死
FROM "02_Hot_Memory/Characters"
WHERE type = "character_state"
SORT character_id ASC
```
