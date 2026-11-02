---
type: dashboard
---

# 章节进度

```dataview
TABLE id, title, status, word_count
FROM "04_Outline/Chapters"
WHERE type = "chapter_outline"
SORT number(split(id, "-")[1]) ASC
```

## 正文

```dataview
TABLE id, title, status, word_count, updated_at
FROM "08_Manuscripts"
WHERE type = "manuscript"
SORT number(split(id, "-")[1]) ASC
```

## 状态机
`outline` → `preflight_passed` → `draft` → `revision`(可循环) → `editor_approved` → `author_approved` → `published` → `memory_committed`
