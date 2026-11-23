#!/usr/bin/env python3
"""把旧形态 ID（`ent_000001` / `CHAR-0001`）换成 V1.3 的 `{TYPE}-{ULID}`。

**迁移是确定性的**：同一个旧 ID 永远换成同一个新 ID，与文件顺序、运行次数无关。
这条不是洁癖——迁移要能重跑。一旦中途失败，重跑不能把已经换过的号再换一次，
否则正文里的交叉引用会指向一个不存在的对象。确定性让「重跑 = 幂等」成立。

## 新 ID 怎么拼

`ULID(timestamp=固定迁移时间, payload=代数基数 + 旧序号)`

两个刻意的选择：

- **payload 放旧序号**，让字典序 == 旧序号序。章节顺序不能因为换 ID 形态就丢。
  `ch_000001` < `ch_000002` 换成 `CH-…01` < `CH-…02` 依旧成立。
- **代数基数**（V1.0 = 0，V4.0 = 100000）让两代旧号不会撞在同一个新号上：
  `rel_000001` 与 `REL-000001` 语义上是两个不同的历史对象，不能迁成同一个 ID。

## 用法

    python3 scripts/migrate_ids_v13.py --root tests/fixtures --root tests/unit
    python3 scripts/migrate_ids_v13.py --root <vault> --rename-files --apply

不带 `--apply` 只预演并打印映射表。撞号、同前缀多对一，一律报错退出，
**不做「自动挑一个」的补救**——那种补救会把两个历史对象合并成一个。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_management.domain import ulid as ulid_mod  # noqa: E402

# 迁移时间：2026-11-24T00:00:00Z。**固定常量，不许改成 now()**，
# 否则重跑会生成不同的号，迁移就不再幂等。
MIGRATION_TS_MS = 1795478400000

# 代数基数：两代旧号各占一段负载，避免跨代撞号。
GEN_BASE = {"v1_seq": 0, "v4_seq": 100_000}

V1_TO_V13 = {
    "ent": "ENT", "asset": "AST", "ch": "CH", "plan": "PLAN", "scene": "SCN",
    "evt": "EVT", "rel": "REL", "sc": "STC", "fs": "FSP", "task": "TSK",
    "prop": "PRP", "ver": "VER", "src": "SRC", "bkp": "BKP", "idea": "IDE",
    "novel": "NOV", "wf": "WFR",
}

V4_TO_V13 = {
    "CHAR": "ENT", "LOC": "ENT", "FAC": "ENT", "ITEM": "ENT", "CREATURE": "ENT",
    "RULE": "ENT", "WORLD": "ENT", "ENT": "ENT",
    "SCENE": "SCN", "FORE": "FSP", "FORESHADOW": "FSP",
    "VOL": "VOL", "PLAN-VOL": "PLAN", "PLAN-CH": "PLAN",
    "EVT": "EVT", "EVENT": "EVT", "REL": "REL",
    "TASK": "TSK", "PROP": "PRP", "CHANGE": "PRP", "ISSUE": "PRP",
    "APR": "APR", "VER": "VER", "AST": "AST", "ASSET": "AST",
    "SRC": "SRC", "IMP": "SRC",
}

# `ADR-0001`（架构决策记录）、`SHA-256` 之类不是本系统的对象 ID，
# 靠「前缀不在白名单」自然排除——白名单只有上面两张表。
#
# 单遍扫描用这一条合并正则，而不是先用 V1 再扫 V4：两遍扫会把第一遍写进去的
# 新 ID 当输入，见 `rewrite()` 的说明。
V1_RE = re.compile(r"(?<![A-Za-z0-9])([a-z]{2,8})_(\d{6})(?![0-9])")
V4_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,7})-(\d{3,6})(?![0-9])")
# **不能用 `\b`**：Python 的 `\w` 在 str 上是 Unicode 感知的，汉字算 word 字符，
# 于是「的`ent_000086`」「`ent_000086`的」两边都不存在词边界，一半引用扫不到。
# 这个坑第一次跑就踩了：正式库 6254 处只匹配到 1467 处，剩下 4787 处
# 全都紧贴汉字。用显式的「前不是字母数字、后不是数字」才准。
COMBINED_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[a-z]{2,8}_\d{6}|[A-Z][A-Z0-9]{1,7}-\d{3,6})(?![0-9])"
)

TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md", ".base", ".txt", ".toml"}


def build_map(names: set[tuple[str, str, int]]) -> dict[tuple[str, str], str]:
    """`{(代际, 旧ID): 新ID}`。撞号直接抛错。

    `names` 是 `(代际, 旧 ID 原文, 旧序号)` 的集合。

    撞号只有两种来由：同一代里两个不同前缀落到同一个新前缀（`CHAR-0001` 与
    `LOC-0001` 都要变 `ENT-…`），或同一份旧 ID 出现在两处却带了不同序号。
    两种都必须人工决定，**不能自动挑一个**——自动挑等于把两个历史对象
    合并成一个，而「合并对象」是不可逆的语义变更，不是迁移该做的事。
    """
    by_target: dict[str, list[str]] = {}
    out: dict[tuple[str, str], str] = {}
    for gen, old, ordinal in sorted(names):
        table = V1_TO_V13 if gen == "v1_seq" else V4_TO_V13
        head = old.split("_")[0] if gen == "v1_seq" else old.rsplit("-", 1)[0]
        new = f"{table[head]}-{ulid_mod.compose(MIGRATION_TS_MS, GEN_BASE[gen] + ordinal)}"
        by_target.setdefault(new, []).append(old)
        out[(gen, old)] = new
    clashes = {k: v for k, v in sorted(by_target.items()) if len(v) > 1}
    if clashes:
        detail = "\n".join(f"  {k} ← {' / '.join(v)}" for k, v in clashes.items())
        raise SystemExit(
            "撞号：多个旧 ID 会迁成同一个新 ID，必须人工决定怎么拆。\n" + detail
        )
    return out


def scan_text(text: str, gens: set[str]) -> set[tuple[str, str, int]]:
    found: set[tuple[str, str, int]] = set()
    if "v1_seq" in gens:
        for m in V1_RE.finditer(text):
            if m.group(1) in V1_TO_V13:
                found.add(("v1_seq", m.group(0), int(m.group(2))))
    if "v4_seq" in gens:
        for m in V4_RE.finditer(text):
            if m.group(1) in V4_TO_V13:
                found.add(("v4_seq", m.group(0), int(m.group(2))))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True, help="扫描根目录，可重复")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只预演）")
    ap.add_argument("--rename-files", action="store_true", help="文件名以旧 ID 开头的，连文件名一起改")
    ap.add_argument("--map-out", default=None, help="把映射表写成 JSON，便于审计与回滚")
    ap.add_argument(
        "--only",
        choices=("all", "v1", "v4"),
        default="all",
        help=(
            "只迁某一代旧形态。**真实 Vault 必须用 v1**："
            "规划文档里的 `CH-0001–CH-0045` 是章号范围不是 ID，历史记录里的 "
            "`CHAR-0001` 是当时形态的存档，两者都不该被换掉"
        ),
    )
    args = ap.parse_args()
    gens = {"v1_seq", "v4_seq"} if args.only == "all" else {f"{args.only}_seq"}

    files: list[Path] = []
    for root in args.root:
        base = Path(root)
        if base.is_file():
            files.append(base)
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
                continue
            if any(part in {".git", ".tmp", "__pycache__", ".pytest_cache"} for part in p.parts):
                continue
            files.append(p)

    texts = {p: p.read_text(encoding="utf-8", errors="surrogateescape") for p in files}
    names: set[tuple[str, str, int]] = set()
    for t in texts.values():
        names |= scan_text(t, gens)

    if not names:
        print("没有发现可迁移的旧形态 ID")
        return 0

    mapping = build_map(names)

    def rewrite(text: str, counter: list[int]) -> str:
        """单遍替换。**不逐条 `str.replace`**：

        逐条替换会让「已经换好的新 ID」再被后面的规则扫一遍。新 ID 的负载段
        是一长串 0，形态上很像旧号的一部分，一旦被二次命中就会换成一个错号——
        而且错得很隐蔽：ID 仍然「长得对」，只是指向了别的对象。
        """

        def repl(m: re.Match[str]) -> str:
            token = m.group(0)
            gen = "v1_seq" if "_" in token else "v4_seq"
            new = mapping.get((gen, token))
            if new is None:
                return token
            counter[0] += 1
            return new

        return COMBINED_RE.sub(repl, text)

    rewrites = 0
    touched: list[Path] = []
    for path, text in texts.items():
        counter = [0]
        new_text = rewrite(text, counter)
        if counter[0]:
            rewrites += counter[0]
            touched.append(path)
            if args.apply:
                path.write_text(new_text, encoding="utf-8", errors="surrogateescape")

    renames = 0
    if args.rename_files:
        for path in files:
            stem = path.stem
            hit = next(
                ((gen, old) for (gen, old) in mapping if stem == old or stem.startswith(old + "_")),
                None,
            )
            if not hit:
                continue
            new_id = mapping[hit]
            new_name = new_id + stem[len(hit[1]):] + path.suffix
            target = path.with_name(new_name)
            renames += 1
            if args.apply:
                if target.exists():
                    print(f"跳过（目标已存在）：{path.name} → {new_name}")
                    continue
                path.rename(target)

    if args.map_out:
        rows = [
            {"old": old, "new": new, "generation": gen}
            for (gen, old), new in sorted(mapping.items())
        ]
        Path(args.map_out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    for (gen, old), new in sorted(mapping.items()):
        print(f"{gen:7s} {old:16s} → {new}")
    print(
        f"\n映射 {len(mapping)} 条，替换 {rewrites} 处，涉及 {len(touched)} 份文件"
        + (f"，改名 {renames} 份" if args.rename_files else "")
    )
    if not args.apply:
        print("（未写盘。加 --apply 落盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
