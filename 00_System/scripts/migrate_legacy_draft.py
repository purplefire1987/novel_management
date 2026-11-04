#!/usr/bin/env python
"""把作者旧稿正文挪出主章节序列（一次性迁移）。

背景
----
`九州.md` 是作者上一版正文，2026-11-04 用 `novel ingest` 按标题切入库时，
系统按「下一可用章号」连续分配，占掉了 CH-0003..CH-0030、并挂在 VOL-0002 下。
但这一版正文不是新书的第二卷，而是**同一部书的旧版全稿**；新版从 CH-0001 重写，
若旧稿继续占着 0003 起的号，新书写到第三章就会撞号。

做法
----
- 编号：CH-0003..CH-0030 → CH-1001..CH-1028（1xxx 段留给旧稿，主序列 0xxx 只给新版）
- 归属：volume_id 改为 VOL-0009（旧稿存档），目录移到 99_Archive/Original_Draft/
- 定性：canon_status 由 confirmed 改为 deprecated（保留 status: published，历史事实不改写）
- 骨架：删掉 `04_Outline/Chapters/` 下自动生成的 28 个空骨架，改为一份对照表
- 正文一字不改，只改 frontmatter 的编号与归属
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

VAULT = Path(__file__).resolve().parents[2]
SRC_DIR = VAULT / "08_Manuscripts/Volume_02"
DST_DIR = VAULT / "99_Archive/Original_Draft"
OUTLINE_DIR = VAULT / "04_Outline/Chapters"

LEGACY_VOLUME = "VOL-0009"
SERIAL_OFFSET = 998  # CH-0003 → CH-1001


def safe(name: str) -> str:
    """文件名里能留全角冒号（Vault 里本来就这么用），只挡路径分隔符与半角非法字符。"""
    return re.sub(r'[/\\:*?"<>|]', "-", name).strip()


def split_front(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if line.startswith("- ") or not line.strip():
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, text[end + 5 :]


def main() -> int:
    DST_DIR.mkdir(parents=True, exist_ok=True)

    if SRC_DIR.exists():
        files = sorted(SRC_DIR.glob("CH-*.md"), key=lambda p: p.name)
        for path in files:
            text = path.read_text(encoding="utf-8")
            meta, body = split_front(text)
            old_id = meta.get("id") or path.stem.split("_")[0]
            m = re.fullmatch(r"CH-(\d{4})", old_id)
            if not m:
                print(f"跳过无法识别的编号：{path.name}")
                continue
            new_id = f"CH-{int(m.group(1)) + SERIAL_OFFSET:04d}"
            title = meta.get("title") or meta.get("name") or path.stem

            meta["id"] = new_id
            meta["volume_id"] = LEGACY_VOLUME
            if meta.get("canon_status") == "confirmed":
                meta["canon_status"] = "deprecated"

            # 重建 frontmatter：按原顺序，只替换值
            head = []
            for line in text[4 : text.find("\n---\n", 4)].splitlines():
                if not line.strip() or line.startswith("- "):
                    head.append(line)
                    continue
                key, sep, _ = line.partition(":")
                key = key.strip()
                if key in ("id", "volume_id", "canon_status"):
                    head.append(f"{key}: {meta[key]}")
                else:
                    head.append(line)
            new_text = "---\n" + "\n".join(head) + "\n---\n" + body

            dst = DST_DIR / f"{new_id}_{safe(title)}.md"
            dst.write_text(new_text, encoding="utf-8")
            path.unlink()

        if not any(SRC_DIR.iterdir()):
            shutil.rmtree(SRC_DIR)

    # 骨架一律清掉（旧稿的历史阶段记录在对照表里，不靠空骨架撑）
    for name in ("CH-0027", "CH-0028", "CH-0029", "CH-0030"):
        for skeleton in OUTLINE_DIR.glob(f"{name}*.md"):
            skeleton.unlink()

    # 对照表从落盘结果生成，可重复执行
    rows: list[tuple[str, str, int]] = []
    for path in DST_DIR.glob("CH-*.md"):
        meta, _ = split_front(path.read_text(encoding="utf-8"))
        new_id = meta.get("id", path.stem.split("_")[0])
        old_id = f"CH-{int(new_id.split('-')[1]) - SERIAL_OFFSET:04d}"
        title = meta.get("title") or meta.get("name") or path.stem
        words = int(meta.get("word_count") or 0)
        rows.append((new_id, title, words, old_id))
    rows.sort(key=lambda r: r[0])

    print(f"存档 {len(rows)} 章，总字数 {sum(r[2] for r in rows)}")

    table = "\n".join(
        f"| {n} | [[{n}_{safe(t)}|{t}]] | {w} | {o} |" for n, t, w, o in rows
    )
    doc = f"""---
id: ASSET-0002
type: story_asset
name: 旧稿章节对照表
title: 旧稿章节对照表（CH-1001..CH-10{len(rows):02d}）
subtype: 存档索引
status: published
canon_status: deprecated
created_at: '2026-11-05'
updated_at: '2026-11-05'
world: 九州
source_refs:
- 10_Change_Log/Ingest/INGEST-20261104-144536
tags:
- 旧稿
- 存档
- 对照
---

# 旧稿章节对照表

> 作者上一版正文（`九州.md`，约 5.5 万字，{len(rows)} 节）的存档索引。
> **这不是新版的章节序列**：旧稿统一编在 `CH-10xx` 段，新版主序列从 `CH-0001` 起，
> 两者不共用编号，改写时左右对照即可。

## 用法

- 写新版某一章时，先在本表找「同一情节」的旧稿段落，读它的语感、人物口吻、场景细节，
  再按新版细纲重写——**旧稿是材料，不是底稿**。
- 旧稿正文一律 `canon_status: deprecated`：可引用、可检索，但不作为事实判定依据；
  事实以 `01_Core_Memory` 的实体档案与新版细纲为准。
- 原稿原件：`99_Archive/Raw_Sources/原文/九州.md`

## 章节清单

| 旧稿编号 | 原节标题 | 字数 | 原编号 |
| --- | --- | --- | --- |
{table}

## 迁移记录

- 2026-11-04：`novel ingest` 按标题切分录入，落在 `08_Manuscripts/Volume_02/`，占用 CH-0003..CH-0030。
- 2026-11-05：新版从 CH-0001 重写，旧稿占号会让第三章起撞号；
  编号改到 CH-1001..CH-10{len(rows):02d}，移入 `99_Archive/Original_Draft/`，`canon_status` 降为 deprecated。
  正文一字未改，仅改 frontmatter 的编号与归属。
"""
    (DST_DIR / "旧稿章节对照表.md").write_text(doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
