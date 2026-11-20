"""契约命令：schema list / schema check / package-hash。

三个都是「不写作品内容」的命令——schema 只读，package-hash 只算。
真正写盘的是 allocate-id（独立的 id_cmd）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import canonical
from .. import config as cfg_mod
from ..errors import CanonicalError, SchemaError, UsageError
from ..schema import SchemaStore
from ._common import fail, heading, info, ok, warn


def cmd_schema(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    try:
        store = SchemaStore(Path(args.schema_dir).expanduser() if args.schema_dir else None)
    except SchemaError as exc:
        print(str(exc))
        return 1

    if args.schema_command == "list":
        heading(f"可校验类型（{len(store.names())} 类，来自 {store.root}）")
        for name in store.names():
            doc = store.load(name)
            title = str(doc.get("title") or "")
            required = doc.get("required") or []
            print(f"  {name:22s} {title}")
            if required:
                info(f"必填：{'、'.join(required)}")
        return 0

    # check
    name = args.type
    if not name:
        raise UsageError("用法：novel schema check <类型> <文件…>，类型见 novel schema list")
    try:
        store.path_of(name)
    except SchemaError as exc:
        raise UsageError(str(exc)) from exc

    paths: list[Path] = []
    for raw in args.files:
        if raw == "-":
            payload = sys.stdin.read()
            return _report_one(store, name, "<stdin>", _load_json_text(payload, "<stdin>"))
        path = Path(raw).expanduser()
        if not path.exists():
            raise UsageError(f"文件不存在：{path}")
        paths.append(path)

    if not paths:
        raise UsageError("没有要校验的文件")

    bad = 0
    for path in paths:
        # 读不懂 JSON 属于用法错（退出 2），契约不符才是 1——两者含义不同，别混。
        data = _load_json_text(path.read_text(encoding="utf-8"), str(path))
        errors = store.check(name, data)
        if errors:
            bad += 1
            print(f"  [FAIL] {path}")
            for line in errors:
                info(line)
        else:
            ok(f"{path}")
    if bad:
        print(f"\n{bad} 个文件不满足 {name} 契约")
    return 1 if bad else 0


def _load_json_text(text: str, label: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(f"{label} 不是合法 JSON：{exc}") from exc


def _report_one(store: SchemaStore, name: str, label: str, data) -> int:
    errors = store.check(name, data)
    if errors:
        print(f"  [FAIL] {label}")
        for line in errors:
            info(line)
        return 1
    ok(f"{label}")
    return 0


def cmd_package_hash(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """算规范化 JSON 的哈希。带 --check 就比对给定的期望值。"""
    if args.path == "-":
        text = sys.stdin.read()
        label = "<stdin>"
    else:
        path = Path(args.path).expanduser()
        if not path.exists():
            raise UsageError(f"文件不存在：{path}")
        text = path.read_text(encoding="utf-8")
        label = str(path)

    data = _load_json_text(text, label)
    try:
        digest = canonical.package_hash(data)
    except CanonicalError as exc:
        fail(str(exc))
        return 2

    if args.check:
        want = args.check.strip().lower()
        if want == digest:
            ok(f"{label} 哈希一致：{digest}")
            return 0
        fail(f"{label} 哈希不一致")
        info(f"期望 {want}")
        info(f"实际 {digest}")
        return 1

    if args.canonical:
        print(canonical.canonical_json(data))
    print(digest)
    return 0
