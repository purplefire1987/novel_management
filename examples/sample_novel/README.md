# 示例项目（完全虚构）

本目录的数据全部虚构，不来自任何真实作品。作用是**一份能过契约校验的最小项目**：
新人照它认目录、照它写 frontmatter，脚本拿它当夹具。

## 里面有什么

| 文件 | 展示什么 |
|---|---|
| `10_项目配置/project.yaml` | 项目元数据（由 `templates/project/project.yaml` 渲染而来） |
| `02_世界知识库/人物与角色/ent_000001_林砚.md` | 人物实体：`id` / `type` / `name` 是契约必填 |
| `02_世界知识库/地理与地点/ent_000002_灰石渡.md` | 地理实体：同一 `type: location`，分类靠 `subtype` |
| `02_世界知识库/事件与历史/evt_000001_雨夜抄书.md` | 事件：`chapter_id` 绑正文；`sequence` **故意不填**（ADR-013） |
| `04_正文/初稿/ch_000001.md` | 章节：`chapter_id` / `project_id` / `stage` / `status` 是契约必填 |

## 三条约定，照抄就行

1. **ID 是六位小写 + 下划线**：`ent_000001`、`evt_000001`、`ch_000001`。
   单前缀 `ent_` 收所有世界知识实体，**类型只看 `type` 字段**，不从号段猜。
2. **`canon_status` 只用六值**：`idea` / `proposal` / `pending_review` / `canon` /
   `deprecated` / `rejected`。示例用的是 `canon`（已落在正文里）。
3. **双链写 `[[ID_名称|显示名]]`**。`ID_名称` 是文件名主干，缺了 ID 的那一半会断。

## 怎么验

```bash
# 逐份校验 frontmatter 是否符合契约
# 注意：frontmatter.split 负责切 `---`，markdown.load_meta 只收「纯 YAML 块」，
# 直接把整篇文件丢给 load_meta 会报「found another document」。
python3 - <<'PY'
from pathlib import Path
from novel_management import frontmatter, markdown
from novel_management.schema import SchemaStore
store = SchemaStore(Path("schemas"))
cases = [
    ("entity", "examples/sample_novel/02_世界知识库/人物与角色/ent_000001_林砚.md"),
    ("entity", "examples/sample_novel/02_世界知识库/地理与地点/ent_000002_灰石渡.md"),
    ("event",  "examples/sample_novel/02_世界知识库/事件与历史/evt_000001_雨夜抄书.md"),
    ("chapter","examples/sample_novel/04_正文/初稿/ch_000001.md"),
]
for kind, path in cases:
    block, _body = frontmatter.split(Path(path).read_text(encoding="utf-8"))
    meta = markdown.load_meta(block, where=path)
    errs = store.check(kind, meta)
    print(f"{kind:8s} {Path(path).name:32s} {'OK' if not errs else errs}")
PY
```

四行应全部输出 `OK`。

## 不要做的事

- 不要把本目录当真实项目初始化（它没有 `07_审核与报告`、`_staging` 等运行目录）。
  要真实开工请 `novel init-project`。
- 不要在这里放真实作品内容。公仓按 GPL-3.0 公开，而 ADR-005 定了私人数据不进公仓。
