#!/usr/bin/env python3
"""列出大纲区的层级，并指出哪一层是空的。

为什么要有它：2026-11-10 作者问「当前设计没有满足粗纲-细纲的划分？」——
问题不在于没有分层，而在于**分层只写在文件正文里，没人能一眼看出哪层缺**。
于是 S1 有粗有细、S2–S5 只有粗，看起来却像「结构完整」。

本脚本只做两件事：
  1. 按 `layer:` 把 `04_Outline/` 的文件排成层级树（总纲 → 粗纲 → 细纲 → 派生 → 归档）。
  2. 对着 `VOL-*_序列.yaml` 数细纲：卷里有几个序列，就该有几份细纲，缺的逐条报出来。

它不判断好坏，只报「有没有」。细纲写得对不对，`check_hooks.py` 与 `story_editor.md` 管。

用法：
    python3 00_System/scripts/outline_layers.py
    python3 00_System/scripts/outline_layers.py --quiet   # 只报缺层，exit 1 表示有缺
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTLINE = ROOT / "04_Outline"

# 层级顺序。数值只用于排序，不代表重要程度。
ORDER = [
    "总纲（全书）",
    "粗纲（卷）",
    "粗纲（章·一章一行）",
    "幕链",
    "细纲",
    "机读件（细纲落库）",
    "底稿（不是纲）",
    "派生物（改完即废）",
    "归档（deprecated）",
]
RANK = {name: i for i, name in enumerate(ORDER)}
UNLABELED = "（未标 layer）"


def frontmatter(path: Path) -> dict[str, str]:
    """只读第一个 `---` 块，够用就行，不引 yaml 依赖。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.lstrip().startswith("-"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip("'\"")
    return out


def collect() -> list[tuple[int, str, Path, dict[str, str]]]:
    rows = []
    for path in sorted(OUTLINE.rglob("*")):
        if path.is_dir() or path.suffix not in (".md", ".yaml", ".yml"):
            continue
        if path.name == "README.md":
            continue
        meta = frontmatter(path)
        layer = meta.get("layer")
        if not layer and path.suffix in (".yaml", ".yml") and "序列" in path.name:
            layer = "幕链"
        # 两个机读目录没有 layer 字段（脚本生成的），按所在目录认
        if not layer and path.parent.name in ("Chapters", "Scenes"):
            layer = "机读件（细纲落库）"
        layer = layer or UNLABELED
        rows.append((RANK.get(layer, len(ORDER)), layer, path.relative_to(OUTLINE), meta))
    rows.sort(key=lambda r: (r[0], str(r[2])))
    return rows


def sequences(vol_yaml: Path) -> list[tuple[str, str, str]]:
    """从序列文件里抠出 (id, 名, 章范围)。不解析 YAML，按行扫——格式是我们自己写的。"""
    out, cur = [], {}
    for line in vol_yaml.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("- id:"):
            if cur:
                out.append(cur)
            cur = {"id": s.split(":", 1)[1].strip()}
        elif s.startswith("name:") and cur is not None:
            cur["name"] = s.split(":", 1)[1].strip()
        elif s.startswith("chapters:") and cur is not None:
            cur["chapters"] = s.split(":", 1)[1].strip()
    if cur:
        out.append(cur)
    return [(d.get("id", "?"), d.get("name", "?"), d.get("chapters", "?")) for d in out]


def main() -> int:
    ap = argparse.ArgumentParser(description="列出大纲区层级，指出缺哪层")
    ap.add_argument("--quiet", action="store_true", help="只报缺层")
    args = ap.parse_args()

    if not OUTLINE.is_dir():
        print(f"[环境不成立] 找不到 {OUTLINE}", file=sys.stderr)
        return 2

    rows = collect()
    gaps: list[str] = []

    if not args.quiet:
        print(f"大纲区层级（{OUTLINE.relative_to(ROOT)}）\n")
        last = None
        for _, layer, rel, meta in rows:
            if layer != last:
                print(f"\n[{layer}]")
                last = layer
            bits = [str(rel)]
            if scope := meta.get("chapter_range"):
                bits.append(scope)
            if seq := meta.get("sequence_id"):
                bits.append(seq)
            if st := meta.get("status"):
                bits.append(f"status={st}")
            print("  " + "  ".join(bits))

    # 逐卷点细纲：序列有几个，细纲就该有几份
    if not args.quiet:
        print()
    for vol_yaml in sorted(OUTLINE.glob("Volumes/VOL-*_序列.yaml")):
        vol = vol_yaml.name.split("_")[0]
        seqs = sequences(vol_yaml)
        have = {}
        for path in OUTLINE.glob(f"Volumes/{vol}_S*.md"):
            meta = frontmatter(path)
            if meta.get("layer") == "细纲" and (sid := meta.get("sequence_id")):
                have[sid] = path.name
        missing = [(sid, nm, ch) for sid, nm, ch in seqs if sid not in have]
        if not args.quiet:
            print(f"{vol}：序列 {len(seqs)} 个，细纲 {len(have)} 份")
            for sid, nm, ch in seqs:
                mark = "有" if sid in have else "**缺**"
                print(f"  {mark}  {sid} {nm:<6} {ch}")
        for sid, nm, ch in missing:
            gaps.append(f"{vol} 缺细纲：{sid} {nm}（{ch}）——粗纲有、细纲空")

    if gaps:
        if not args.quiet:
            print()
        for g in gaps:
            print(f"[缺层] {g}")
        return 1

    if not args.quiet:
        print("\n各卷的序列都有细纲。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
