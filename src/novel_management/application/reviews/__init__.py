"""审阅记录：`08_Reviews` 的轻量写通道。

## 为什么另开一条通道，而不是复用 publish

`08_Reviews` 不在 `paths.PUBLISH_TARGETS` 六根里，所以 `publish` 会以
「目标不在正式区」直接拒绝；而把它加进正式区又是错的（2026-11-20 作者质询后撤回）：
审阅意见不是作品内容，进正式区就得过 `approve` + `publish`，还会被算进签字基线，
等于把「编辑的意见」和「作者的正文」绑死在同一份哈希上。

但**「不发布」不等于「可以手写文件」**。不设通道的话，`08_Reviews` 会成为全库
唯一一处没有契约的写入点——那里写错字段、写坏 YAML、覆盖别人的报告，都没有东西拦。

所以这条通道保留 publish 的三样、去掉它的两样：

- 保留：frontmatter schema 校验、原子写、审计留痕（oplog）
- 去掉：approve 批准链、加入签字基线

## 一条硬约束

`verdict` 只表达审阅结论，**不是作者批准**。审阅说 pass，正文照样要 `approve` +
`publish` 才能成为正式事实。这条不写清楚，很容易演变成「总编说行就行」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from novel_management.schemas import store as schema_mod
from novel_management.audit import now_iso
from novel_management.domain.errors import ConflictError, UsageError
from novel_management.infrastructure.filesystem.paths import ProjectPaths
from novel_management.infrastructure.filesystem.storage import write_new

# kind → 08_Reviews 下的子目录。目录名与库里现存的分类一致，不另造一套。
KIND_DIRS: dict[str, tuple[str, ...]] = {
    "review": ("Audit_Reports", "Chapter_Review"),
    "audit": ("Audit_Reports",),
    "change-log": ("Change_Log",),
}

# kind → frontmatter 的 type 取值。三者语义不同，不能都叫 audit_report：
# 「这一章的审阅」和「全库结构体检」不是同一类文件。
KIND_TYPES: dict[str, str] = {
    "review": "review_report",
    "audit": "audit_report",
    "change-log": "change_log",
}

SCHEMA_NAME = "review_note"

# 文件名里不能出现的字符。注意中文标点（：·｜）是合法的，只有路径分隔符与
# Windows 保留字符要换掉——把「卷一·序列一」这种标题改坏比留着更糟。
_UNSAFE = re.compile(r'[\\/:*?"<>|]+')
_SPACES = re.compile(r"\s+")


def safe_name(raw: str) -> str:
    """把标题变成能当文件名用的片段。不做拼音化、不截断语义。"""
    text = _UNSAFE.sub("-", (raw or "").strip())
    text = _SPACES.sub("-", text).strip("-.")
    if not text:
        raise UsageError("标题里没有可用作文件名的字符")
    return text[:80]


@dataclass
class Note:
    kind: str
    name: str
    body: str
    scope: str
    author: str = "agent"
    task_id: str = ""
    chapter_id: str = ""
    verdict: str = ""
    source_sha256: str = ""
    created_at: str = ""
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def frontmatter(self) -> dict:
        fm: dict = {
            "type": KIND_TYPES[self.kind],
            "name": self.name,
            "status": "active",
            "created_at": self.created_at or date.today().isoformat(),
            "author": self.author,
            "scope": self.scope,
        }
        if self.task_id:
            fm["task_id"] = self.task_id
        if self.chapter_id:
            fm["chapter_id"] = self.chapter_id
        if self.verdict:
            fm["verdict"] = self.verdict
        if self.source_sha256:
            fm["source_sha256"] = self.source_sha256
        return fm

    def render(self) -> str:
        header = yaml.safe_dump(
            self.frontmatter(), allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        body = self.body if self.body.endswith("\n") else self.body + "\n"
        return f"---\n{header}---\n\n{body}"


def kind_dir(pp: ProjectPaths, kind: str) -> Path:
    """`08_Reviews/<子目录>`。根目录从 layout 取，不写死 `08_Reviews`。"""
    if kind not in KIND_DIRS:
        raise UsageError(f"未知的 kind：{kind}（可选 {', '.join(KIND_DIRS)}）")
    return pp.reviews.joinpath(*KIND_DIRS[kind])


def target_path(pp: ProjectPaths, note: Note) -> Path:
    stem = safe_name(note.name)
    stamp = (note.created_at or date.today().isoformat())[:10].replace("-", "")
    return kind_dir(pp, note.kind) / f"{stem}_{stamp}.md"


def validate(note: Note) -> None:
    """按 schema 校验 frontmatter。**先校验再落盘**——落盘后才发现字段不对，
    就只能在 08_Reviews 里留一份坏文件，而那里正是最不该脏的地方。"""
    errors = schema_mod.check(SCHEMA_NAME, note.frontmatter())
    if errors:
        detail = "；".join(errors[:3])
        raise UsageError(f"审阅记录的 frontmatter 不合契约：{detail}")


def write_note(pp: ProjectPaths, note: Note) -> Path:
    """写入一份审阅记录。同名即拒，绝不覆盖。"""
    validate(note)
    path = target_path(pp, note)
    try:
        write_new(path, note.render().encode("utf-8"))
    except ConflictError as exc:
        raise ConflictError(
            f"{path.relative_to(pp.vault).as_posix()} 已存在。"
            "审阅记录不改写——要修正就换标题，或把旧的标成 superseded。"
        ) from exc
    note.path = path
    return path


def list_notes(pp: ProjectPaths, kind: str | None = None) -> list[dict]:
    """列出已有审阅记录。只读 frontmatter 前几行，不整份读入。"""
    kinds = [kind] if kind else list(KIND_DIRS)
    out: list[dict] = []
    for k in kinds:
        base = kind_dir(pp, k)
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            try:
                head = path.read_text(encoding="utf-8")[:1200]
                block = head.split("---", 2)
                fm = yaml.safe_load(block[1]) if len(block) >= 3 else {}
                fm = fm if isinstance(fm, dict) else {}
            except (OSError, yaml.YAMLError):
                fm = {}
            out.append(
                {
                    "kind": k,
                    "path": path.relative_to(pp.vault).as_posix(),
                    "name": fm.get("name", path.stem),
                    "type": fm.get("type", ""),
                    "status": fm.get("status", ""),
                    "created_at": fm.get("created_at", ""),
                    "verdict": fm.get("verdict", ""),
                }
            )
    return out


__all__ = [
    "KIND_DIRS",
    "KIND_TYPES",
    "Note",
    "kind_dir",
    "list_notes",
    "safe_name",
    "target_path",
    "validate",
    "write_note",
    "now_iso",
]
