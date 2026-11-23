"""JSON Schema 校验（draft 2020-12 的实用子集）。

为什么要自己写：本项目 `dependencies = []`，装 `jsonschema` 就破坏了零依赖；
而 `docs/architecture` 里已经把「零依赖 + 可降级」定为架构约束。契约本身放在
`schemas/*.schema.json`，是唯一真源；这个模块只是执行它。

**支持的关键字**（够覆盖本项目全部 schema，超出的一律当注解忽略）：

    $ref $defs type enum const pattern format
    minLength maxLength minimum maximum exclusiveMinimum exclusiveMaximum
    minItems maxItems uniqueItems items
    required properties additionalProperties
    oneOf anyOf allOf not

**不支持**：`patternProperties`、`dependentRequired`、`if/then/else`、
`propertyNames`、`contains`、`unevaluatedProperties`。项目 schema 不用它们；
真要用得先扩这里，不要指望「写了就生效」。

`additionalProperties: false` 是本模块的重点——PRD §8.4「未知字段默认拒绝」，
拼错的字段名不该静默通过。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from novel_management.domain.canonical import canonical_json, parse_iso
from .domain.errors import SchemaError

SCHEMA_SUFFIX = ".schema.json"
DEFAULT_ROOTS = (
    Path(__file__).resolve().parent / "schemas",
    Path(__file__).resolve().parents[2] / "schemas",
)


def _find_root(explicit: Path | None = None) -> Path:
    import os

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    env = os.environ.get("NOVEL_SCHEMAS")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.extend(DEFAULT_ROOTS)
    candidates.append(Path.cwd() / "schemas")
    for path in candidates:
        if path.is_dir() and any(path.glob("*" + SCHEMA_SUFFIX)):
            return path
    raise SchemaError(
        "找不到 schema 目录。依次找过："
        + "、".join(str(p) for p in candidates)
        + "。可用 NOVEL_SCHEMAS 指定。"
    )


class SchemaStore:
    """按名字取 schema 并校验。名字是文件名去掉 `.schema.json`。"""

    def __init__(self, root: Path | None = None):
        self.root = _find_root(root)
        self._cache: dict[Path, dict] = {}

    # ---- 加载 --------------------------------------------------------
    def files(self) -> list[Path]:
        """可供校验的类型 schema。下划线开头是共享定义（_common），不是一种类型。"""
        return [p for p in sorted(self.root.glob("*" + SCHEMA_SUFFIX)) if not p.name.startswith("_")]

    def names(self) -> list[str]:
        return [p.name[: -len(SCHEMA_SUFFIX)] for p in self.files()]

    def path_of(self, name: str) -> Path:
        candidate = self.root / f"{name}{SCHEMA_SUFFIX}"
        if candidate.exists():
            return candidate
        # 容错：允许传完整文件名
        if name.endswith(SCHEMA_SUFFIX):
            alt = self.root / name
            if alt.exists():
                return alt
        known = "、".join(self.names())
        raise SchemaError(f"没有名为 {name!r} 的 schema。可用：{known}")

    def load(self, name: str) -> dict:
        return self._load_file(self.path_of(name))

    def _load_file(self, path: Path) -> dict:
        path = path.resolve()
        if path in self._cache:
            return self._cache[path]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SchemaError(f"schema 不是合法 JSON：{path}：{exc}") from exc
        if not isinstance(data, dict):
            raise SchemaError(f"schema 顶层必须是对象：{path}")
        self._cache[path] = data
        return data

    def schema_version(self, name: str) -> str:
        doc = self.load(name)
        return str(doc.get("x-schema-version") or doc.get("$schema") or "")

    # ---- 校验 --------------------------------------------------------
    def check(self, name: str, data: Any) -> list[str]:
        """返回全部错误。空列表＝通过。不抛异常，便于一次报全。"""
        doc = self.load(name)
        validator = _Validator(self, doc, self.path_of(name))
        validator.validate(doc, data, "$")
        return validator.errors

    def check_or_raise(self, name: str, data: Any) -> None:
        errors = self.check(name, data)
        if errors:
            head = errors[0]
            more = f"（另有 {len(errors) - 1} 处）" if len(errors) > 1 else ""
            raise SchemaError(f"{name} 校验失败：{head}{more}")


# --------------------------------------------------------------------------
# 校验器
# --------------------------------------------------------------------------

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if expected == "number":
        return not isinstance(value, bool) and isinstance(value, (int, float))
    if expected == "string":
        return isinstance(value, str)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True  # 未知类型名不拦，避免 schema 写错就全红


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _resolve_pointer(doc: dict, pointer: str) -> Any:
    if pointer in ("", "/"):
        return doc
    if not pointer.startswith("/"):
        raise SchemaError(f"$ref 指针必须以 / 开头：{pointer!r}")
    node: Any = doc
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(token)]
        else:
            node = node[token]
    return node


class _Validator:
    def __init__(self, store: SchemaStore, doc: dict, doc_path: Path):
        self.store = store
        self.doc = doc
        self.doc_path = doc_path
        self.errors: list[str] = []

    def _error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    # ---- $ref --------------------------------------------------------
    def _deref(self, ref: str) -> tuple[dict, dict, Path]:
        """返回 (schema, 所在文档, 文档路径)。支持同文件与跨文件。"""
        base, _, pointer = ref.partition("#")
        if base:
            target = (self.doc_path.parent / base).resolve()
            doc = self.store._load_file(target)
            return _resolve_pointer(doc, pointer), doc, target
        return _resolve_pointer(self.doc, pointer), self.doc, self.doc_path

    # ---- 主入口 ------------------------------------------------------
    def validate(self, schema: Any, data: Any, path: str) -> None:
        if schema is True or schema is None:
            return
        if schema is False:
            self._error(path, "此处不允许出现任何值")
            return
        if not isinstance(schema, dict):
            return

        if "$ref" in schema:
            target, target_doc, target_path = self._deref(str(schema["$ref"]))
            # 余下关键字与 $ref 同级时 draft 2020-12 允许，一并生效。
            saved = (self.doc, self.doc_path)
            self.doc, self.doc_path = target_doc, target_path
            try:
                self.validate(target, data, path)
            finally:
                self.doc, self.doc_path = saved
            rest = {k: v for k, v in schema.items() if k != "$ref"}
            if rest:
                self.validate(rest, data, path)
            return

        if "type" in schema:
            expected = schema["type"]
            if isinstance(expected, str):
                if not _type_matches(expected, data):
                    self._error(path, f"类型应为 {expected}，实际是 {_type_name(data)}")
                    return
            elif isinstance(expected, list):
                if not any(_type_matches(t, data) for t in expected if isinstance(t, str)):
                    self._error(path, f"类型应为 {'/'.join(map(str, expected))}，实际是 {_type_name(data)}")
                    return

        if "const" in schema and data != schema["const"]:
            self._error(path, f"必须是 {schema['const']!r}，实际是 {data!r}")

        if "enum" in schema:
            allowed = schema["enum"]
            if not any(data == item and _type_name(data) == _type_name(item) for item in allowed):
                shown = "、".join(repr(v) for v in allowed)
                self._error(path, f"取值必须是 {shown} 之一，实际是 {data!r}")

        if isinstance(data, str):
            self._check_string(schema, data, path)
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            self._check_number(schema, data, path)
        if isinstance(data, list):
            self._check_array(schema, data, path)
        if isinstance(data, dict):
            self._check_object(schema, data, path)

        self._check_combinators(schema, data, path)

    # ---- 按类型 ------------------------------------------------------
    def _check_string(self, schema: dict, data: str, path: str) -> None:
        if "minLength" in schema and len(data) < schema["minLength"]:
            self._error(path, f"长度 {len(data)} 小于最小长度 {schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            self._error(path, f"长度 {len(data)} 超过最大长度 {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(str(schema["pattern"]), data) is None:
                    self._error(path, f"{data!r} 不匹配 {schema['pattern']}")
            except re.error as exc:
                self._error(path, f"schema 的 pattern 写错了：{exc}")
        fmt = schema.get("format")
        if fmt == "date-time":
            try:
                parse_iso(data)
            except Exception:
                self._error(path, f"{data!r} 不是带时区的 ISO 8601 时间")
        elif fmt == "date":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
                self._error(path, f"{data!r} 不是 YYYY-MM-DD")
        elif fmt == "uri":
            if "://" not in data:
                self._error(path, f"{data!r} 不是 URI")

    def _check_number(self, schema: dict, data: float, path: str) -> None:
        if "minimum" in schema and data < schema["minimum"]:
            self._error(path, f"{data} 小于最小值 {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            self._error(path, f"{data} 大于最大值 {schema['maximum']}")
        if "exclusiveMinimum" in schema and data <= schema["exclusiveMinimum"]:
            self._error(path, f"{data} 必须大于 {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and data >= schema["exclusiveMaximum"]:
            self._error(path, f"{data} 必须小于 {schema['exclusiveMaximum']}")

    def _check_array(self, schema: dict, data: list, path: str) -> None:
        if "minItems" in schema and len(data) < schema["minItems"]:
            self._error(path, f"元素 {len(data)} 个，少于 {schema['minItems']} 个")
        if "maxItems" in schema and len(data) > schema["maxItems"]:
            self._error(path, f"元素 {len(data)} 个，多于 {schema['maxItems']} 个")
        if schema.get("uniqueItems") and len(data) > 1:
            seen: set[str] = set()
            for index, item in enumerate(data):
                key = canonical_json(item) if isinstance(item, (dict, list)) else repr(item)
                if key in seen:
                    self._error(f"{path}[{index}]", f"元素重复：{item!r}")
                seen.add(key)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(data):
                self.validate(item_schema, item, f"{path}[{index}]")

    def _check_object(self, schema: dict, data: dict, path: str) -> None:
        required = schema.get("required") or []
        for key in required:
            if key not in data:
                self._error(path, f"缺少必填字段 {key!r}")

        properties = schema.get("properties") or {}
        for key, value in data.items():
            if key in properties:
                self.validate(properties[key], value, f"{path}.{key}")
                continue
            extra = schema.get("additionalProperties", True)
            if extra is False:
                allowed = "、".join(sorted(properties)) or "（无）"
                self._error(f"{path}.{key}", f"未知字段（允许的字段：{allowed}）")
            elif isinstance(extra, dict):
                self.validate(extra, value, f"{path}.{key}")

    def _check_combinators(self, schema: dict, data: Any, path: str) -> None:
        if "allOf" in schema:
            for index, sub in enumerate(schema["allOf"]):
                self.validate(sub, data, path)
        if "anyOf" in schema:
            if not any(self._matches(sub, data) for sub in schema["anyOf"]):
                self._error(path, "不满足 anyOf 中的任何一个分支")
        if "oneOf" in schema:
            hits = sum(1 for sub in schema["oneOf"] if self._matches(sub, data))
            if hits != 1:
                self._error(path, f"oneOf 要求恰好命中一个分支，实际命中 {hits} 个")
        if "not" in schema and self._matches(schema["not"], data):
            self._error(path, "命中了 not 禁止的分支")

    def _matches(self, schema: Any, data: Any) -> bool:
        """试探性校验：只看有没有错，不留痕。"""
        snapshot = self.errors
        self.errors = []
        try:
            self.validate(schema, data, "$")
            return not self.errors
        finally:
            self.errors = snapshot


# --------------------------------------------------------------------------
# 便捷函数
# --------------------------------------------------------------------------

def check(name: str, data: Any, root: Path | None = None) -> list[str]:
    return SchemaStore(root).check(name, data)


def check_many(name: str, items: Iterable[Any], root: Path | None = None) -> dict[int, list[str]]:
    """批量校验，返回 {序号: 错误}，只含有错的。"""
    store = SchemaStore(root)
    bad: dict[int, list[str]] = {}
    for index, item in enumerate(items):
        errors = store.check(name, item)
        if errors:
            bad[index] = errors
    return bad


# frontmatter 的 `type` 值 → 该用哪份契约判。
#
# 存在的理由：知识库里的 .md 都长一个样（frontmatter + 正文），只有 `type` 能说明
# 它是哪类对象。以前这里是一串 if-elif 写在 `entity validate` 里，漏掉一类就静默
# 按 entity 判——`rel_*.md` 就是这么被拿去比 `^(ent|asset)_` 模式、在第 1 条错误上
# 就撞墙的，后面真正缺的字段一条都没报出来。
#
# 只列**已经有独立契约**的类型。刻意不收进来的：素材卡（asset）与各类普通设定
# 共用 entity 契约——V1.0 把世界知识收进单一 `ent_` 号段、类别由 type 承载，
# 契约同理，除非字段面确实不同（事件有时序、关系有两端），否则不该再分家。
SCHEMA_BY_TYPE: dict[str, str] = {
    "event": "event",
    "relationship": "relationship",
    "state_change": "state_change",
    "knowledge": "knowledge",
    "foreshadowing": "foreshadowing",
    "scene_beat": "artifact",
    "chapter_plan": "artifact",
    "chapter": "chapter",
}


def schema_name_for(type_name: str | None, default: str = "entity") -> str:
    """按 frontmatter 的 `type` 选契约；认不出就用 default。"""
    key = str(type_name or "").strip().lower()
    return SCHEMA_BY_TYPE.get(key, default)
