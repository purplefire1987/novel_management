"""写作阶段：主笔只读 Pack，草稿只进 staging（PRD 16.2）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import vault
from ..agents.writer import WriterAgent
from ..audit.logger import AuditLog
from ..config import Config
from ..database.sqlite import Index, now_iso
from ..memory import context_builder, retrieval
from ..memory.entity_resolver import resolve_chapter
from .state_machine import WorkflowError, chapter_status


@dataclass
class WriteResult:
    chapter_id: str
    draft_path: str
    word_count: int
    mode: str
    skeleton: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "draft": self.draft_path,
            "word_count": self.word_count,
            "mode": self.mode,
            "skeleton": self.skeleton,
            "note": self.note,
        }


def draft_path(cfg: Config, chapter_id: str) -> Path:
    return cfg.staging_dir / "draft" / f"{chapter_id}.draft.md"


def load_draft(cfg: Config, chapter_id: str) -> vault.Doc | None:
    path = draft_path(cfg, chapter_id)
    return vault.read_doc(path) if path.exists() else None


def body_word_count(body: str) -> int:
    text = vault.extract_section(body, "正文")
    if not text:
        text = body
    text = text.split("## 写作备注")[0]
    return vault.count_words(text)


def run(cfg: Config, index: Index, chapter_id: str, force: bool = False) -> WriteResult:
    status = chapter_status(cfg, index, chapter_id)
    if status not in ("preflight_passed", "revision", "outline"):
        raise WorkflowError(f"{chapter_id} 当前状态 {status}，不能生成草稿（需先 preflight）")
    if status == "outline":
        raise WorkflowError(
            f"{chapter_id} 尚未通过 Preflight（状态 outline）。先运行 `novel preflight {chapter_id}`。"
        )

    pack_text = context_builder.load_pack(cfg, chapter_id)
    if not pack_text and cfg.workflow.get("require_preflight_before_write", True):
        raise WorkflowError(
            f"缺少 Preflight Pack：{chapter_id}。先运行 `novel preflight {chapter_id}`。"
        )

    outline = retrieval.outline_doc(cfg, index, chapter_id)
    if not outline:
        raise WorkflowError(f"找不到大纲：{chapter_id}")
    if not vault.checkbox_checked(outline.body, "已批准写作"):
        raise WorkflowError(f"{chapter_id} 大纲未获作者批准，主笔不能写作。")

    resolved = resolve_chapter(cfg, index, outline)
    title = str(outline.meta.get("title") or outline.meta.get("name") or chapter_id)

    writer = WriterAgent(cfg, index)
    result = writer.write(
        chapter_id=chapter_id,
        title=title,
        pack_text=pack_text or "",
        scenes=resolved.scenes,
    )

    body = result.content
    if "## 作者备注" not in body:
        body = body.rstrip() + "\n\n---\n\n## 作者备注（不进入正文）\n-\n"

    word_count = body_word_count(body)
    meta = {
        "id": chapter_id,
        "type": "manuscript",
        "title": title,
        "name": title,
        "volume_id": outline.meta.get("volume_id"),
        "status": "draft",
        "canon_status": "confirmed",
        "word_count": word_count,
        "outline_ref": chapter_id,
        "preflight_ref": f"PREFLIGHT-{chapter_id}",
        "created_at": now_iso()[:10],
        "updated_at": now_iso()[:10],
        "draft_mode": result.mode,
    }

    path = draft_path(cfg, chapter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        backup = path.with_suffix(".draft.bak.md")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    vault.write_atomic(path, vault.render_doc(meta, body))

    AuditLog(cfg, index).record(
        operation="write_draft",
        target=chapter_id,
        agent="writer",
        actor="system",
        status="draft",
        detail=f"{cfg.rel(path)}｜words={word_count}｜mode={result.mode}",
    )

    return WriteResult(
        chapter_id=chapter_id,
        draft_path=cfg.rel(path),
        word_count=word_count,
        mode=result.mode,
        skeleton=bool(result.data.get("skeleton")),
        note=result.note,
    )
