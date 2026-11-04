#!/usr/bin/env python3
"""修复「重复建档」与「号段空洞」——一次性维护脚本（默认 dry-run）。

背景
----
2026-11-03 的资产录入在受限环境中被中断：事务写完前 50 个人物后被环境拦下，
连清理动作也被拦，于是留下 50 个"孤儿"（批次 `ASSETS-…-224115`，没有批次日志）；
重试从 CHAR-0051 继续，成功写入 86 个人物（批次 `ASSETS-…-224145`）。
结果是同名人物各两份：CHAR-0001 与 CHAR-0051 都是陈默。

本脚本做两件事，幂等：

1. **去重**：同 `type` + 同 `name` 的多份文件，保留**能追溯到批次日志**的那份
   （「录入批次」指向 `10_Change_Log/Ingest/*.md` 且该文件存在），其余删除；
   删除前比对内容（忽略 id / 批次号差异），内容实质不同则不动，只报告。
2. **补洞**：每类实体按序号升序重排为 1..N，文件名与 `id` 一起改，
   并把 Vault 内所有 `CHAR-00xx` 之类的引用同步改写（归档与变更日志保持原貌）。

为什么单独做成脚本
------------------
删除/重命名是破坏性操作，不该藏在业务流程里：它需要人在终端里显式执行一次。
默认 dry-run 只打印计划，加 `--apply` 才动手。

用法
----
    cd "/Users/Mr_Zhou/WorkBuddy/超长篇小说管理系统"
    ../.venv/bin/python 00_System/novel_system/scripts/repair_duplicate_entities.py           # 看计划
    ../.venv/bin/python 00_System/novel_system/scripts/repair_duplicate_entities.py --apply   # 执行
    ../.venv/bin/novel --vault . rebuild-index                                               # 重建索引

执行后旧批次操作号（OP-…）不再可回滚（文件已改号），需要回退请用 Git。
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

ENTITY_DIRS = {
    "character": ("01_Core_Memory/Characters", "CHAR"),
    "faction": ("01_Core_Memory/Factions", "FAC"),
    "item": ("01_Core_Memory/Items", "ITEM"),
    "location": ("01_Core_Memory/Locations", "LOC"),
    "enemy": ("01_Core_Memory/Enemies", "ENEMY"),
}

#: 作者已裁定"同名但不是同一实体"的条目，不参与去重
#: 奉天造：朝廷系总部（山岚城）与民间系分部（武安都），见 FAC-0022 / FAC-0032 的编者注
DISTINCT_NAMES = {("faction", "奉天造")}

REWRITE_ROOTS = (
    "01_Core_Memory", "02_Hot_Memory", "03_Short_Term_Memory", "04_Outline",
    "05_Story_Assets", "06_Plot_Ledger", "07_Entity_Graph", "08_Manuscripts",
    "09_Audit_Reports", "12_Dashboard",
)
SKIP_PARTS = {"99_Archive", "10_Change_Log", ".git", ".obsidian", ".novel_system", "__pycache__"}

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
BATCH_RE = re.compile(r"(?:ASSETS|LORE)-\d{8}-\d{6}")


def read_frontmatter(path: Path) -> dict[str, str]:
    match = FM_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-", "#")):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def serial_of(path: Path, prefix: str) -> int | None:
    match = re.match(rf"^{prefix}-(\d{{4}})_", path.name)
    return int(match.group(1)) if match else None


def signature(path: Path) -> str:
    """内容指纹：抹掉 id、批次与标点空白差异，只留实质文字。

    两次录入的同一行数据常只差一个多余的顿号或逗号（如「淑妃项氏之女，。」
    与「淑妃项氏之女。」），这类差异不算「实质不同」，否则去重会漏。
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^id:.*$", "", text, flags=re.M)
    text = re.sub(r"^import_batch:.*$", "", text, flags=re.M)
    text = re.sub(r"^(source_refs:)\n(?:- .*\n)+", r"\1\n", text, flags=re.M)
    text = BATCH_RE.sub("<batch>", text)
    # 只比对「字」：中文、字母、数字；标点、空白、Markdown 符号一律忽略
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text))


def has_batch_log(path: Path, vault: Path) -> bool:
    batch = read_frontmatter(path).get("import_batch", "")
    return bool(batch) and (vault / "10_Change_Log" / "Ingest" / f"{batch}.md").is_file()


def collect(vault: Path) -> dict[str, dict[str, list[tuple[int, Path]]]]:
    out: dict[str, dict[str, list[tuple[int, Path]]]] = {}
    for doc_type, (rel_dir, prefix) in ENTITY_DIRS.items():
        directory = vault / rel_dir
        bucket: dict[str, list[tuple[int, Path]]] = defaultdict(list)
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                serial = serial_of(path, prefix)
                if serial is None:
                    continue
                meta = read_frontmatter(path)
                name = meta.get("name") or path.stem.split("_", 1)[-1]
                bucket[name].append((serial, path))
        out[doc_type] = dict(bucket)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="去重 + 补号（默认 dry-run）")
    parser.add_argument("--vault", default=".", help="Vault 根目录")
    parser.add_argument("--apply", action="store_true", help="真正执行（默认只打印计划）")
    args = parser.parse_args()
    vault = Path(args.vault).resolve()

    data = collect(vault)
    deletions: list[Path] = []
    renames: list[tuple[Path, Path, str, str]] = []
    dup_alias: dict[str, str] = {}     # 被删的 id → 保留的旧 id
    renum_map: dict[str, str] = {}     # 保留的旧 id → 新 id
    problems: list[str] = []
    kept_count: dict[str, int] = {}

    for doc_type, (rel_dir, prefix) in ENTITY_DIRS.items():
        keepers: list[tuple[int, Path]] = []
        for name, items in sorted(data.get(doc_type, {}).items()):
            if (doc_type, name) in DISTINCT_NAMES:
                keepers.extend(items)
                continue
            # 优先保留有批次日志的（可追溯），其次序号小的
            items.sort(key=lambda pair: (not has_batch_log(pair[1], vault), pair[0]))
            keeper_serial, keeper_path = items[0]
            keepers.append((keeper_serial, keeper_path))
            for serial, path in items[1:]:
                if signature(path) == signature(keeper_path):
                    deletions.append(path)
                    dup_alias[f"{prefix}-{serial:04d}"] = f"{prefix}-{keeper_serial:04d}"
                else:
                    keepers.append((serial, path))
                    problems.append(f"{path.name} 与 {keeper_path.name} 同名且内容实质不同，未处理")
        keepers.sort()
        kept_count[doc_type] = len(keepers)
        for index, (serial, path) in enumerate(keepers, 1):
            if serial != index:
                renames.append(
                    (
                        path,
                        path.with_name(re.sub(rf"^{prefix}-\d{{4}}_", f"{prefix}-{index:04d}_", path.name)),
                        f"{prefix}-{serial:04d}",
                        f"{prefix}-{index:04d}",
                    )
                )
                renum_map[f"{prefix}-{serial:04d}"] = f"{prefix}-{index:04d}"

    # 先删后改：被删 id → 保留旧 id → 新 id
    id_map: dict[str, str] = {}
    for old, keeper_old in dup_alias.items():
        id_map[old] = renum_map.get(keeper_old, keeper_old)
    id_map.update(renum_map)

    print(f"Vault：{vault}")
    print("保留数量：" + "、".join(f"{k}={v}" for k, v in kept_count.items()))
    print(f"\n删除重复文件：{len(deletions)}")
    for path in deletions[:8]:
        print(f"  - {path.relative_to(vault)}")
    if len(deletions) > 8:
        print(f"  …… 其余 {len(deletions) - 8} 个")

    print(f"\n改号文件：{len(renames)}")
    for old, new, old_id, new_id in renames[:8]:
        print(f"  - {old_id} → {new_id}　{old.name} → {new.name}")
    if len(renames) > 8:
        print(f"  …… 其余 {len(renames) - 8} 个")

    print(f"\n需要同步改写的 ID 映射：{len(id_map)} 条")
    if problems:
        print("\n需人工确认：")
        for item in problems:
            print(f"  [!] {item}")

    if not deletions and not renames:
        print("\n无需修复：没有重复建档，号段也连续。")
        return 0
    if not args.apply:
        print("\n[dry-run] 未改动任何文件。确认无误后加 --apply 执行。")
        return 0

    # 冲突预检：目标文件名若仍被占用则中止。
    # 注意必须排除两类「会被腾空」的目标：本次要删除的重复件、以及本次要改走的老文件，
    # 否则 0001 号孤儿未删就被当成占用，整批修复会被无谓中止。
    vacated = {path for path in deletions} | {old for old, _, _, _ in renames}
    clash = [new for _, new, _, _ in renames if new.exists() and new not in vacated]
    if clash:
        print("\n[中止] 目标文件已存在：" + ", ".join(p.name for p in clash[:5]))
        return 1

    for path in deletions:
        path.unlink()
    for old, new, old_id, new_id in renames:
        text = old.read_text(encoding="utf-8")
        text = re.sub(rf"^id:\s*{re.escape(old_id)}\s*$", f"id: {new_id}", text, count=1, flags=re.M)
        new.write_text(text, encoding="utf-8")
        old.unlink()

    pattern = re.compile("|".join(sorted((re.escape(k) for k in id_map), key=len, reverse=True)))
    touched = 0
    for root_name in REWRITE_ROOTS:
        root = vault / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if any(part in SKIP_PARTS for part in path.relative_to(vault).parts):
                continue
            original = path.read_text(encoding="utf-8")
            updated = pattern.sub(lambda m: id_map[m.group(0)], original)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                touched += 1

    print(f"\n完成：删除 {len(deletions)} 个重复文件，改号 {len(renames)} 个，更新引用 {touched} 个文件。")
    print("下一步：../.venv/bin/novel --vault . rebuild-index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
