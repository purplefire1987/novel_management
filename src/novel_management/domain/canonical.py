"""字节规范化与包哈希（V4.0 §14.5 哈希规则、§17.3 发布包规范化）。

两件事，都在「比较之前」做，让同一份内容在任何机器上得到同一个哈希：

1. **字节规范化**：UTF-8、无 BOM、LF 换行。审批与发布绑定的是规范化之后的
   精确字节，所以规范化必须是确定性的、不做任何「看起来等价」的改写。
2. **包哈希**：JSON 键排序、无多余空白、不转义中文、time 一律 UTC 字符串、
   禁止 NaN / Infinity。Python 的 `json.dumps` 在给定
   `sort_keys / separators / ensure_ascii=False / allow_nan=False` 后是确定的，
   但浮点仍用 `repr` 输出（`1.0` 就是 `1.0`，不会变成 `1`）——这一点写进测试，
   免得以后换实现时悄悄漂移。

不做什么：
- 不判断「是不是实质修改」。MVP 明令禁止含义判断（§14.5 末段），改一个字节就是新版本。
- 不把时间转成 UTC。时间字符串由产生方写对，这里只校验与排序。
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import CanonicalError
from ..infrastructure.filesystem.storage import sha256_bytes

BOM_UTF8 = b"\xef\xbb\xbf"
BOM_CHAR = "\ufeff"


# --------------------------------------------------------------------------
# 字节规范化
# --------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """去 BOM、统一 LF。行的内容一字不改。"""
    if text.startswith(BOM_CHAR):
        text = text[len(BOM_CHAR) :]
    # 先 CRLF 再孤立 CR：顺序反了会把 CRLF 拆成两个 LF。
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_bytes(data: bytes) -> bytes:
    """字节层面的同一件事。不尝试猜测编码——非 UTF-8 直接报错。"""
    if data.startswith(BOM_UTF8):
        data = data[len(BOM_UTF8) :]
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalError(f"不是合法的 UTF-8，拒绝规范化：{exc}") from exc
    return data


def normalize_file(path: Path) -> tuple[bytes, bytes]:
    """返回 (磁盘原始字节, 规范化字节)。不写盘——写不写由命令层决定。"""
    raw = path.read_bytes()
    return raw, normalize_bytes(raw)


def is_normalized(data: bytes) -> bool:
    """已经是规范形式？用来判断「要不要重写」而不是「要不要报错」。"""
    try:
        return normalize_bytes(data) == data
    except CanonicalError:
        return False


# --------------------------------------------------------------------------
# 时间
# --------------------------------------------------------------------------

def utc_now() -> str:
    """带 Z 后缀的 UTC 时间。PRD §8.2 要求带时区的 ISO 8601 UTC。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    """按 ISO 8601 解析，拒绝无时区的时间。

    无时区的时间在跨机器比较时没有意义，宁可报错也不猜它是本地还是 UTC。
    """
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CanonicalError(f"不是合法的 ISO 8601 时间：{text!r}") from exc
    if value.tzinfo is None:
        raise CanonicalError(f"时间缺少时区，无法确定绝对时刻：{text!r}")
    return value


def to_utc_iso(text: str) -> str:
    """任意带时区的 ISO 时间 → 统一的 UTC 字符串。"""
    value = parse_iso(text).astimezone(timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# 包哈希
# --------------------------------------------------------------------------

def _reject_non_finite(value: Any, path: str = "$") -> None:
    """NaN / Infinity 不是 JSON，写进包会让哈希无法跨语言复现。"""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalError(f"{path} 是 {value}，JSON 不允许 NaN / Infinity")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"{path} 的键 {key!r} 不是字符串，JSON 对象键必须是字符串")
            _reject_non_finite(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_non_finite(item, f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    raise CanonicalError(f"{path} 的类型 {type(value).__name__} 不可序列化为 JSON")


def canonical_json(obj: Any) -> str:
    """规范形式：键排序、紧凑分隔符、中文不转义、无 NaN。

    同一个对象重复调用必须逐字节相同——这是「中文包哈希重复运行一致」的实现基础。
    """
    _reject_non_finite(obj)
    try:
        return json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:  # 理论上已被 _reject_non_finite 挡住
        raise CanonicalError(f"无法规范化 JSON：{exc}") from exc


def package_hash(obj: Any) -> str:
    """规范化 JSON 的 sha256。审批与发布绑定这个值。"""
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def package_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def hash_file_package(path: Path) -> str:
    """读一个 JSON 文件 → 规范化 → 哈希。注释与缩进不影响结果。"""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CanonicalError(f"{path} 不是合法 JSON：{exc}") from exc
    return package_hash(obj)


def diff_paths(left: Any, right: Any, path: str = "$") -> list[str]:
    """列出两个对象在规范化之后仍然不同的位置。

    只报第一层的键差异与叶子值差异，够审批时告诉作者「哪里变了」。
    """
    problems: list[str] = []
    if type(left) is not type(right):
        problems.append(f"{path}: 类型不同（{type(left).__name__} ≠ {type(right).__name__}）")
        return problems
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                problems.append(f"{path}.{key}: 只存在于右侧")
            elif key not in right:
                problems.append(f"{path}.{key}: 只存在于左侧")
            else:
                problems.extend(diff_paths(left[key], right[key], f"{path}.{key}"))
        return problems
    if isinstance(left, list):
        if len(left) != len(right):
            problems.append(f"{path}: 长度不同（{len(left)} ≠ {len(right)}）")
        for index in range(min(len(left), len(right))):
            problems.extend(diff_paths(left[index], right[index], f"{path}[{index}]"))
        return problems
    if left != right:
        problems.append(f"{path}: {left!r} ≠ {right!r}")
    return problems
