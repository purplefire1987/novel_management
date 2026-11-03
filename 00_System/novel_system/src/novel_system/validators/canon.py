"""Canon 一致性：重复 ID、别名冲突、引用有效性（PRD 7.2 / 17.1 AC-01）。"""

from __future__ import annotations

from typing import Any, Iterable

from ..config import Config
from .schema import Issue


def collect_doc_indexes(docs: Iterable[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    """(id, type) -> [file_path...]"""
    table: dict[tuple[str, str], list[str]] = {}
    for doc in docs:
        meta = doc.get("meta") or {}
        doc_id = meta.get("id")
        doc_type = meta.get("type")
        if not doc_id or not doc_type:
            continue
        table.setdefault((str(doc_id), str(doc_type)), []).append(str(doc.get("path", "")))
    return table


def check_duplicate_ids(cfg: Config, docs: list[dict[str, Any]]) -> list[Issue]:
    """同一 ID 不得在同类文档中出现两次；跨类共享只允许配置中声明的组合。"""
    issues: list[Issue] = []
    table = collect_doc_indexes(docs)

    for (doc_id, doc_type), paths in table.items():
        if len(paths) > 1:
            issues.append(
                Issue(
                    "BLOCKER",
                    "canon",
                    f"重复实体 ID：{doc_id}（type={doc_type}）出现在 {len(paths)} 个文件",
                    refs=[doc_id],
                    blocks_write=True,
                )
            )

    by_id: dict[str, set[str]] = {}
    for (doc_id, doc_type) in table:
        by_id.setdefault(doc_id, set()).add(doc_type)

    allowed = {frozenset(pair) for pair in cfg.allowed_shared_id_types}
    for doc_id, types in by_id.items():
        if len(types) <= 1:
            continue
        if frozenset(types) in allowed:
            continue
        issues.append(
            Issue(
                "BLOCKER",
                "canon",
                f"ID {doc_id} 被多种类型共用：{sorted(types)}，且不在 allow_shared_id_types 中",
                refs=[doc_id],
                blocks_write=True,
            )
        )
    return issues


def check_alias_conflicts(docs: list[dict[str, Any]]) -> list[Issue]:
    """同一个名字/别名被两个实体同时占用 → 会让实体解析失真。"""
    issues: list[Issue] = []
    seen: dict[str, str] = {}
    entity_types = {"character", "location", "faction", "item", "enemy", "world"}

    for doc in docs:
        meta = doc.get("meta") or {}
        if meta.get("type") not in entity_types:
            continue
        doc_id = str(meta.get("id"))
        names = [str(meta.get("name") or "")] + [str(a) for a in (meta.get("aliases") or [])]
        for name in {n.strip() for n in names if n and n.strip()}:
            owner = seen.get(name)
            if owner and owner != doc_id:
                issues.append(
                    Issue(
                        "MAJOR",
                        "canon",
                        f"名称/别名「{name}」同时属于 {owner} 与 {doc_id}",
                        refs=[owner, doc_id],
                    )
                )
            else:
                seen[name] = doc_id
    return issues


def check_refs_exist(
    ids: Iterable[str],
    exists_fn,
    *,
    category: str = "reference",
    blocking: bool = False,
    context: str = "",
) -> list[Issue]:
    """引用有效性：引用的实体必须存在（AC-07）。"""
    issues: list[Issue] = []
    for doc_id in sorted({i for i in ids if i}):
        if not exists_fn(doc_id):
            detail = f"引用了不存在的实体 {doc_id}"
            if context:
                detail += f"（{context}）"
            issues.append(
                Issue(
                    "BLOCKER" if blocking else "MAJOR",
                    category,
                    detail,
                    refs=[doc_id],
                    blocks_write=blocking,
                )
            )
    return issues


def check_canon_status(meta: dict[str, Any]) -> list[Issue]:
    status = str(meta.get("canon_status") or "confirmed")
    doc_id = str(meta.get("id") or "?")
    if status == "deprecated":
        return [
            Issue(
                "MAJOR",
                "canon",
                f"{doc_id} 已标记为 deprecated，不得在新章节中继续使用",
                refs=[doc_id],
            )
        ]
    return []
