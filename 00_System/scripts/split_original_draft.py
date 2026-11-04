#!/usr/bin/env python3
"""把作者原稿《九州.md》按「卷 → 节」整理成一份可录入的中间稿。

为什么需要这一步：`novel ingest --split heading` 只会按**最小标题层级**切章，
原稿的最大标题是 `# 卷一：…`，直接录进去就是 6 个上万字的大块，不是章。
所以先把卷标题降为注释、把 `## 节` 提为 `# 章`，章名前置卷名（`卷一·楔子`），
再交给 `novel ingest`，一章一节，章节号由系统排。

输出：`99_Archive/Raw_Sources/原文/九州_原稿_切章_20261105.md`（中间稿，可删）
原件：`99_Archive/Raw_Sources/原文/九州_原稿_20261104.md`（照录，不改一字）
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parents[2]
RAW_DIR = VAULT / "99_Archive/Raw_Sources/原文"
SOURCE = Path("/Users/Mr_Zhou/Downloads/九州.md")

# 卷标题（原稿写法）→ 章名前缀。第四卷出现两次，是作者原件里的编号重复，不改，
# 只在标题里保留原样，另在卷页「待裁」里点名。
VOLUME_PREFIX = {
    "前述": "引子",
}


def normalize_title(text: str) -> str:
    text = text.strip().strip("*").strip()
    text = text.replace("**", "").strip()
    return text


def main() -> int:
    if not SOURCE.exists():
        print(f"✗ 找不到原稿：{SOURCE}", file=sys.stderr)
        return 1

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    archived = RAW_DIR / "九州_原稿_20261104.md"
    if not archived.exists():
        shutil.copy2(SOURCE, archived)

    lines = SOURCE.read_text(encoding="utf-8-sig").splitlines()

    out: list[str] = [
        "<!-- 中间稿：由 00_System/scripts/split_original_draft.py 从作者原稿生成，仅供 novel ingest 切章 -->",
        "",
    ]
    volume = "前述"
    chapters: list[tuple[str, int]] = []
    buffer: list[str] = []
    title: str | None = None

    def flush() -> None:
        nonlocal title, buffer
        if title is None:
            return
        body = "\n".join(buffer).strip("\n")
        if not body.strip():
            title, buffer = None, []
            return
        prefix = VOLUME_PREFIX.get(volume, volume)
        out.append(f"# {prefix}·{title}")
        out.append("")
        out.extend(buffer)
        out.append("")
        chapters.append((f"{prefix}·{title}", len(body)))
        title, buffer = None, []

    for raw in lines:
        line = raw.rstrip()
        m1 = re.match(r"^#\s+(.+)$", line)
        m2 = re.match(r"^##\s*(.*)$", line)
        if m1:
            flush()
            volume = normalize_title(m1.group(1))
            continue
        if m2 is not None:
            name = normalize_title(m2.group(1))
            if not name:          # 原稿里有空的 `## `，当分隔符，不切章
                continue
            flush()
            title, buffer = name, []
            continue
        buffer.append(line)
    flush()

    target = RAW_DIR / "九州_原稿_切章_20261105.md"
    target.write_text("\n".join(out), encoding="utf-8")

    print(f"✓ 原件照录：{archived.relative_to(VAULT)}")
    print(f"✓ 中间稿：{target.relative_to(VAULT)}（{len(chapters)} 章）")
    for name, words in chapters:
        print(f"   {words:>6} 字  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
