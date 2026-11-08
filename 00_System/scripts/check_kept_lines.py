#!/usr/bin/env python3
"""核对细纲里的「必留原句」是否逐条能在正文里逐字查到。

为什么要有它：细纲里的引文是**给正文下的硬约束**，抄错一个字，
主笔就会照着错的去改正文，或者干脆绕开不写。人眼校对挡不住手误，
跑一遍能挡。同一套判据也用于场景卡的引文核实（`verify_cards.py`），
两者口径必须一致：**整串逐字命中才算命中，模糊匹配不算**。

重写期间的特殊情况：细纲覆盖 CH-0001–0009，但正文只有一份压缩稿 CH-0001.md。
此时用 `--from` 指定压缩稿，把所有引文都回到它里面查；等各章陆续落盘，
再去掉 `--from`，脚本会逐章改查同名正文。

用法：
    python3 00_System/scripts/check_kept_lines.py                 # 逐章查同名正文
    python3 00_System/scripts/check_kept_lines.py \
        --from 08_Manuscripts/Volume_01/CH-0001.md                # 全部回查压缩源稿
    python3 00_System/scripts/check_kept_lines.py <细纲.md>       # 查指定细纲

退出码：0 全命中｜1 有缺失｜2 环境/参数不对
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOC = ROOT / "04_Outline" / "Volumes" / "VOL-0001_S1细纲.md"
DEFAULT_MS = ROOT / "08_Manuscripts"

CH_HEAD = re.compile(r"^###\s*(CH-\d{4})｜", re.M)
KEEP_ROW = re.compile(r"^\|\s*必留原句\s*\|(.*)\|\s*$", re.M)
QUOTE = re.compile(r"`([^`]+)`")
BODY = re.compile(r"^##[ \t]*正文[ \t]*$(.*?)(?=^##[ \t]|\Z)", re.S | re.M)


def body_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = BODY.search(text)
    return m.group(1) if m else text


def find_chapter(chapter: str, ms_dir: Path) -> Path | None:
    hits = sorted(ms_dir.rglob(f"{chapter}.md"))
    return hits[0] if hits else None


def parse_doc(doc: Path) -> list[tuple[str, list[str]]]:
    text = doc.read_text(encoding="utf-8")
    marks = [(m.start(), m.group(1)) for m in CH_HEAD.finditer(text)]
    if not marks:
        raise SystemExit(f"没有找到任何 `### CH-00xx｜` 标题：{doc}")
    result: list[tuple[str, list[str]]] = []
    for i, (start, chapter) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        row = KEEP_ROW.search(text[start:end])
        result.append((chapter, QUOTE.findall(row.group(1)) if row else []))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("doc", nargs="?", default=str(DEFAULT_DOC))
    ap.add_argument("--ms-dir", default=str(DEFAULT_MS))
    ap.add_argument("--from", dest="source", default=None,
                    help="把所有引文都回到这一份正文里查（重写期用压缩源稿）")
    args = ap.parse_args()

    doc = Path(args.doc)
    ms_dir = Path(args.ms_dir)
    if not doc.is_file():
        print(f"[环境] 细纲不存在：{doc}", file=sys.stderr)
        return 2
    if args.source and not Path(args.source).is_file():
        print(f"[环境] 指定源稿不存在：{args.source}", file=sys.stderr)
        return 2
    if not ms_dir.is_dir():
        print(f"[环境] 正文目录不存在：{ms_dir}", file=sys.stderr)
        return 2

    shared = body_of(Path(args.source)) if args.source else None
    rows: list[tuple[str, int, int, str]] = []
    misses: list[tuple[str, str]] = []
    skipped: list[str] = []
    total = hit = chars = 0

    for chapter, quotes in parse_doc(doc):
        if not quotes:
            rows.append((chapter, 0, 0, "无必留句"))
            continue
        if shared is not None:
            body, note = shared, Path(args.source).name
        else:
            path = find_chapter(chapter, ms_dir)
            if path is None:
                skipped.append(chapter)
                total += len(quotes)
                chars += sum(len(q) for q in quotes)
                rows.append((chapter, len(quotes), 0, "正文未落盘"))
                continue
            body, note = body_of(path), path.name

        ok = 0
        for q in quotes:
            total += 1
            chars += len(q)
            if q in body:
                ok += 1
                hit += 1
            else:
                misses.append((chapter, q))
        rows.append((chapter, len(quotes), ok, note))

    width = max((len(r[0]) for r in rows), default=8)
    print(f"细纲：{doc.name}")
    if shared is not None:
        print(f"回查源稿：{Path(args.source).name}")
    print("-" * 58)
    for chapter, n, ok, note in rows:
        flag = "—" if (n == 0 or chapter in skipped) else ("✓" if ok == n else "✗")
        print(f"{chapter:<{width}}  {flag}  {ok}/{n}  {note}")
    print("-" * 58)
    print(f"合计：必留句 {total} 条｜命中 {hit} 条｜缺失 {len(misses)} 条｜引文 {chars} 字")
    if skipped:
        print(f"跳过（正文未落盘，且未指定 --from）：{', '.join(skipped)}")

    if misses:
        print("\n缺失明细（逐条把这句话回到正文里找一次）：")
        for chapter, q in misses:
            print(f"  [{chapter}] `{q}`")
        return 1
    print("\n判据：整串逐字命中才算命中；模糊匹配、分段拼接都不算。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
