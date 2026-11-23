#!/usr/bin/env python3
"""把 schemas/ 里的 ID 正则从 V1.0 形态改成 V1.3 形态（`{TYPE}-{ULID}`）。

只做**字面替换**，不 round-trip JSON：round-trip 会重排缩进、改排序，
把 27 份 schema 变成一大片无意义 diff，真正改的那几个字符就看不见了。

替换表按「长的先换」排。`^(ch|plan)_...` 与 `^ch_...` 有子串关系，
顺序颠倒会把前者换成一个半成品。

用法：python3 scripts/migrate_schema_id_patterns.py [--apply]
不带 --apply 只报告，不写文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ULID = r"[0-9A-HJKMNP-TV-Z]{26}"

# (旧字面量, 新字面量, 说明)
RULES: list[tuple[str, str, str]] = [
    # ---- 复合／带分支的放最前 ----
    (
        r'"^((ch|plan)_[0-9]{6}|CH-[0-9]{3,4})$"',
        f'"^(CH|VOL|OUT|PLAN)-{ULID}$"',
        "章节范围（含规划占位）",
    ),
    (
        r'"^(ch_[0-9]{6}|CH-[0-9]{3,4})$"',
        f'"^CH-{ULID}$"',
        "章节引用",
    ),
    (r'"^(ch|plan)_[0-9]{6}$"', f'"^(CH|VOL|OUT|PLAN)-{ULID}$"', "章节列表"),
    (r'"^(ent|asset)_[0-9]{6}$"', f'"^(ENT|AST)-{ULID}$"', "实体或素材"),
    # ---- 单前缀 ----
    (r'"^task_[0-9]{6}$"', f'"^TSK-{ULID}$"', "任务"),
    (r'"^prop_[0-9]{6}$"', f'"^PRP-{ULID}$"', "提案"),
    (r'"^ver_[0-9]{6}$"', f'"^VER-{ULID}$"', "发布"),
    (r'"^src_[0-9]{6}$"', f'"^SRC-{ULID}$"', "来源"),
    (r'"^fs_[0-9]{6}$"', f'"^FSP-{ULID}$"', "伏笔"),
    (r'"^evt_[0-9]{6}$"', f'"^EVT-{ULID}$"', "事件"),
    (r'"^scene_[0-9]{6}$"', f'"^SCN-{ULID}$"', "场景"),
    (r'"^rel_[0-9]{6}$"', f'"^REL-{ULID}$"', "关系"),
    (r'"^plan_[0-9]{6}$"', f'"^(VOL|OUT|PLAN)-{ULID}$"', "规划文档"),
    (r'"^ch_[0-9]{6}$"', f'"^CH-{ULID}$"', "章节"),
    (r'"^ent_[0-9]{6}$"', f'"^ENT-{ULID}$"', "实体"),
    # ---- 通配 ----
    (r'"^[a-z]+_[0-9]{6}$"', f'"^[A-Z][A-Z0-9]{{1,7}}-{ULID}$"', "anyId 通配"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    root = Path(__file__).resolve().parent.parent / "schemas"
    files = sorted(root.glob("*.json"))
    total = 0
    touched: list[tuple[str, str, int]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        before = text
        for old, new, note in RULES:
            n = text.count(old)
            if n:
                text = text.replace(old, new)
                touched.append((path.name, note, n))
                total += n
        if text != before:
            if apply:
                path.write_text(text, encoding="utf-8")

    for name, note, n in touched:
        print(f"{name:34s} {note:16s} ×{n}")
    print(f"\n共 {total} 处，涉及 {len({t[0] for t in touched})} 份 schema")
    if not apply:
        print("（未写盘。加 --apply 落盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
