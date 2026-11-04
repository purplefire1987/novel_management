"""把 SQLite 实体索引导出成 Obsidian 里可以直接翻看的全量索引页。

为什么需要它：`07_Entity_Graph/Entity_Index.md` 之前只是几行指路文字，实体一多就
等于没有索引；Obsidian 的图谱视图是散点，Dataview 又要装插件。这一页是纯静态
Markdown 表格，不依赖任何插件，同时给每个实体建一条反向链接——点开任一档案都能
在「反向链接」里回到索引。

事实源永远是 Markdown frontmatter；本页是派生产物，可随时丢弃重建。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..database.sqlite import Index, now_iso

INDEX_REL = "07_Entity_Graph/Entity_Index.md"

# 索引页的章节顺序：先读者最常翻的，再是设定与结构
TYPE_ORDER: list[tuple[str, str]] = [
    ("character", "人物"),
    ("faction", "势力"),
    ("location", "地点"),
    ("item", "物品"),
    ("enemy", "敌人"),
    ("world", "世界设定"),
    ("volume", "卷"),
    ("chapter_outline", "章节大纲"),
    ("story_asset", "故事素材"),
    ("event", "事件"),
    ("foreshadowing", "伏笔"),
    ("relationship", "关系"),
]


def _cell(text: Any) -> str:
    """表格单元格转义：竖线会把列切断，换行会把行切断。"""
    value = "" if text is None else str(text)
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _alias_text(raw: Any) -> str:
    aliases = raw
    if isinstance(raw, str):
        import json

        try:
            aliases = json.loads(raw or "[]")
        except json.JSONDecodeError:
            aliases = [raw]
    if not aliases:
        return "—"
    return "、".join(_cell(a) for a in aliases if str(a).strip()) or "—"


def _link(row: dict[str, Any]) -> str:
    file_path = str(row.get("file_path") or "")
    stem = Path(file_path).stem if file_path else str(row.get("id") or "")
    stem = _cell(stem)
    name = _cell(row.get("name") or stem)
    # 表格里必须写成 [[档案\|显示名]]，否则竖线会被当成列分隔符
    return f"[[{stem}\\|{name}]]"


def render(index: Index) -> tuple[str, dict[str, int]]:
    rows = index.all_entities()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("type") or ""), []).append(row)

    for items in buckets.values():
        items.sort(key=lambda r: str(r.get("id") or ""))

    known = [t for t, _ in TYPE_ORDER]
    extra = [t for t in buckets if t not in known]
    order = list(TYPE_ORDER) + [(t, t) for t in sorted(extra)]

    counts = {t: len(buckets.get(t, [])) for t, _ in order}

    lines: list[str] = [
        "---",
        "type: index",
        f"generated_at: {now_iso()}",
        f"entity_count: {len(rows)}",
        "---",
        "",
        "# 实体总索引",
        "",
        "> 本页由 `novel index-md` 从索引库生成，**请勿手改**。改档案请改 Markdown",
        "> frontmatter，然后跑 `novel rebuild-index`（会自动刷新本页）。",
        "",
        f"共 **{len(rows)}** 条实体。每一条都是双链，点开即档案；档案页的「反向链接」",
        "里也能回到本页。",
        "",
        "## 目录",
        "",
    ]

    for doc_type, title in order:
        if not buckets.get(doc_type):
            continue
        lines.append(f"- [[#{title}]]（{counts[doc_type]}）")

    lines.append("")

    for doc_type, title in order:
        items = buckets.get(doc_type)
        if not items:
            continue
        lines.extend(
            [
                f"## {title}",
                "",
                f"共 {len(items)} 条。",
                "",
                "| 编号 | 名称 | 别名 | 状态 | 更新 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in items:
            lines.append(
                "| {id} | {name} | {alias} | {status} | {updated} |".format(
                    id=_cell(row.get("id")),
                    name=_link(row),
                    alias=_alias_text(row.get("aliases")),
                    status=_cell(row.get("canon_status")) or "—",
                    updated=_cell(row.get("updated_at")) or "—",
                )
            )
        lines.append("")

    lines.extend(
        [
            "## 维护",
            "",
            "```bash",
            "novel rebuild-index   # 重扫 Markdown → 重建索引库 → 自动刷新本页",
            "novel index-md        # 只重画本页（索引库不变时用）",
            "novel find 环教        # 按名称/别名/概要查单条",
            "```",
            "",
        ]
    )
    return "\n".join(lines), {t: c for t, c in counts.items() if c}


def write_entity_index(cfg: Any, index: Index) -> dict[str, Any]:
    text, counts = render(index)
    target = cfg.vault / INDEX_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    from ..vault import write_atomic

    write_atomic(target, text)
    return {
        "path": INDEX_REL,
        "entities": sum(counts.values()),
        "by_type": counts,
    }
