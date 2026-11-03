"""摘要：滚动摘要与卷摘要（PRD 12.1 第 12 步、19 风险表「自动摘要丢失细节」）。

摘要只用于召回；关键事实仍以事件文档为准。
"""

from __future__ import annotations

import re
from typing import Iterable

from .. import vault

CHAPTER_HEADING_RE = re.compile(r"^###\s+(CH-\d{4})\b.*$", re.M)


def split_summary_blocks(body: str) -> tuple[str, list[tuple[str, str]]]:
    """把滚动摘要拆成（头部, [(chapter_id, block)]）。"""
    matches = list(CHAPTER_HEADING_RE.finditer(body))
    if not matches:
        return body.strip(), []
    head = body[: matches[0].start()].strip()
    blocks: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        blocks.append((match.group(1), body[match.start() : end].strip()))
    return head, blocks


def render_rolling_summary(
    existing_body: str,
    chapter_id: str,
    title: str,
    one_line: str,
    facts: Iterable[str] = (),
    keep: int = 6,
) -> str:
    """重建滚动摘要：最近 keep 章，同一章重复执行只会覆盖不会追加（幂等）。"""
    _, blocks = split_summary_blocks(existing_body)
    blocks = [(cid, block) for cid, block in blocks if cid != chapter_id]

    block_lines = [f"### {chapter_id} {title}", one_line.strip()]
    fact_list = [f for f in facts if f and f.strip()]
    if fact_list:
        block_lines.append("")
        block_lines.append("**事实要点**")
        block_lines.extend(f"- {f}" for f in fact_list)
    blocks.append((chapter_id, "\n".join(block_lines)))

    blocks.sort(key=lambda item: vault.chapter_num(item[0]))
    blocks = blocks[-keep:]

    # 章块内部若带 `## ` 小节，降级为 `#### `，保持归属在该章之下
    normalized = [
        (cid, re.sub(r"(?m)^## ", "#### ", block)) for cid, block in blocks
    ]

    parts = [
        "# 滚动摘要",
        "",
        "## 一句话",
        one_line.strip(),
        "",
        "## 近章摘要",
        "",
        "\n\n".join(block for _, block in normalized),
    ]
    return "\n".join(parts).strip() + "\n"


def extractive_summary(text: str, max_chars: int = 240) -> str:
    """无 LLM 时的抽取式摘要：取首段 + 末段，够用即可。"""
    clean = re.sub(r"^#.*$", "", text, flags=re.M).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    if not paragraphs:
        return ""
    picked: list[str] = []
    for para in paragraphs:
        if sum(len(p) for p in picked) + len(para) > max_chars:
            break
        picked.append(para)
    if len(picked) < len(paragraphs) and len(picked) < 2:
        picked.append(paragraphs[-1])
    return "\n\n".join(picked)[:max_chars]


def render_volume_summary(volume_id: str, chapters: list[dict[str, str]]) -> str:
    lines = [f"# {volume_id} 卷摘要", ""]
    for chapter in chapters:
        lines.append(f"## {chapter['chapter_id']} {chapter.get('title', '')}")
        lines.append(chapter.get("summary", "").strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"
