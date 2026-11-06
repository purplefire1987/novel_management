#!/usr/bin/env python3
"""提交前只跑「和这次改动有关」的测试。

为什么需要
----------
全量 `pytest` 约 5 分钟，其中绝大多数用例和当下的改动无关。改一行 `map_sync`
也要等五分钟，实际的后果是**根本不跑**——这比跑得慢更糟。

怎么做
------
不维护「哪个模块对应哪个测试」的手写表（那种表一定会过期），而是用 AST 现算：

1. 从 `git diff` / `git status` 取出本次改动的文件；
2. 把 `src/novel_system/**/*.py` 解析成模块图（谁 import 了谁）；
3. 每个测试文件也解析出它直接 import 的模块，再沿模块图做传递闭包；
4. 改动模块落在某个测试的闭包里 → 这个测试要跑。

传递闭包是关键：改了 `vault.py`，`test_scene_chain.py` 虽然没直接 import 它，
但通过 `integrity` 用到了，必须跑。

什么时候退回全量
----------------
以下改动无法安全地缩小范围，脚本会直接跑全部并说明原因：

- `tests/`、`conftest.py`、`pyproject.toml` 等测试基建；
- 任何 `.md` / `.yaml` / `.json` 数据文件——验收测试会读真实档案，改内容就可能翻结论；
- 改动落在包外（比如 `00_System/scripts/`）。

用法
----
    .venv/bin/python 00_System/scripts/test_changed.py          # 只跑相关的
    .venv/bin/python 00_System/scripts/test_changed.py --all    # 强制全量（合并前用）
    .venv/bin/python 00_System/scripts/test_changed.py --list   # 只列不跑
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG_ROOT = REPO / "00_System" / "novel_system" / "src"
TESTS_DIR = REPO / "00_System" / "novel_system" / "tests"
PKG = "novel_system"

# 这些一动就必须全量：测试基建本身，或数据文件（验收用例会读真实档案）
ALWAYS_ALL_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".jsonl", ".canvas"}
ALWAYS_ALL_PATHS = {"pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini"}


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True
    )
    return out.stdout if out.returncode == 0 else ""


def changed_files(base: str) -> list[Path]:
    """本次改动：与 base 的差异 + 未跟踪的新文件。"""
    names: set[str] = set()
    for line in git("diff", "--name-only", base).splitlines():
        if line.strip():
            names.add(line.strip())
    for line in git("status", "--porcelain").splitlines():
        if len(line) > 3:
            path = line[3:].strip()
            if " -> " in path:  # 改名：取新路径
                path = path.split(" -> ", 1)[1]
            names.add(path)
    return sorted(REPO / n for n in names if (REPO / n).exists())


def module_name(path: Path) -> str | None:
    """`src/novel_system/workflow/map_sync.py` → `novel_system.workflow.map_sync`。"""
    try:
        rel = path.resolve().relative_to(PKG_ROOT)
    except ValueError:
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports_of(path: Path) -> set[str]:
    """一个文件里出现的 `novel_system.*` 模块名（含 `from .x import y` 的相对导入）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()

    package_parts = []
    pkg_mod = module_name(path)
    if pkg_mod is None:
        # 测试文件：`tests/test_x.py`，相对导入按 `novel_system` 下的同名包算
        package_parts = [PKG]
    else:
        package_parts = pkg_mod.split(".")[:-1]

    found: set[str] = set()

    def add(dotted: str) -> None:
        text = dotted.strip(".")
        # 只保留顶级包名，后续按前缀匹配即可覆盖子模块
        if text and text.split(".")[0] == PKG:
            found.add(text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            if prefix:
                add(prefix)
                for alias in node.names:
                    add(f"{prefix}.{alias.name}")
    return found


def build_graph(files: list[Path]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in files:
        mod = module_name(path)
        if mod is None:
            continue
        graph[mod] = {m for m in imports_of(path) if m != mod}
    return graph


def closure(start: set[str], graph: dict[str, set[str]]) -> set[str]:
    """从 import 集合出发的传递闭包。

    注意**不要**把「导入一个包」当成「导入它的全部子模块」——`import novel_system`
    只执行 `__init__.py`，不会拉起 `workflow.map_sync`。按前缀展开的话，每个测试
    都会命中全库，这个脚本就没意义了。
    """
    seen = set(start)
    stack = list(start)
    while stack:
        current = stack.pop()
        for dep in graph.get(current, ()):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def affected_tests(changed: list[Path], graph: dict[str, set[str]]) -> tuple[list[Path], list[str]]:
    """返回 (要跑的测试文件, 退回全量的理由)。"""
    reasons: list[str] = []
    for path in changed:
        rel = path.relative_to(REPO)
        if path.suffix in ALWAYS_ALL_SUFFIXES:
            reasons.append(f"{rel} 是数据文件")
        elif path.name in ALWAYS_ALL_PATHS:
            reasons.append(f"{rel} 是测试/构建配置")
        elif TESTS_DIR in path.parents or path.parent == TESTS_DIR:
            reasons.append(f"{rel} 改的是测试本身")
    if reasons:
        return sorted(TESTS_DIR.glob("test_*.py")), reasons[:3]

    mods = [m for m in (module_name(p) for p in changed) if m]
    if not mods:
        return sorted(TESTS_DIR.glob("test_*.py")), ["改动不在包内（脚本或文档），保守跑全量"]

    changed_mods = set(mods)
    picked: list[Path] = []
    for test in sorted(TESTS_DIR.glob("test_*.py")):
        reach = closure(imports_of(test), graph)
        if reach & changed_mods:
            picked.append(test)
    return picked, []


def main() -> int:
    ap = argparse.ArgumentParser(description="提交前只跑相关测试")
    ap.add_argument("--all", action="store_true", help="强制跑全量")
    ap.add_argument("--list", action="store_true", help="只列出选中项，不执行")
    ap.add_argument("--base", default="HEAD", help="比较基准（默认 HEAD）")
    args = ap.parse_args()

    graph = build_graph(sorted(PKG_ROOT.rglob("*.py")))

    if args.all:
        selected, reasons = sorted(TESTS_DIR.glob("test_*.py")), ["--all 指定全量"]
    else:
        changed = changed_files(args.base)
        if not changed:
            print("本次没有任何改动，不跑测试。")
            return 0
        selected, reasons = affected_tests(changed, graph)
        print(f"改动 {len(changed)} 个文件：")
        for path in changed[:8]:
            print(f"  · {path.relative_to(REPO)}")
        if len(changed) > 8:
            print(f"  … 共 {len(changed)} 个")

    if reasons:
        print("\n退回全量的理由：" + "；".join(reasons))

    if not selected:
        print("\n没有测试覆盖到这些模块——建议跑一次全量确认。")
        return 0

    names = [t.name for t in selected]
    print(f"\n选中 {len(names)} 个测试文件：{', '.join(names)}")
    if args.list:
        return 0

    cmd = [sys.executable, "-m", "pytest", "-q", *[str(t) for t in selected]]
    return subprocess.run(cmd, cwd=str(TESTS_DIR.parent)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
