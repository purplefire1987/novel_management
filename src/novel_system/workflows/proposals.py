"""记忆提案：从「已批准正文」提取，不要求正文已经 ACTIVE。

T10 第一条验收是「正文未发布也可合法提取」。PRD §15.1 说明白了为什么：
正文与记忆要**同一次发布**。若要求正文先 ACTIVE 才能提取，那必然出现
「正文已经生效、对应记忆还是上一版」的窗口——这正是第三条验收要消灭的状态。

所以提取的输入是**已批准候选**：

- staging 里那份草稿的字节；
- 与它哈希一致的那条正文批准记录（`Workflows.text_approval` 写的）。

## 两条硬线

1. **提取必须绑源哈希。** 提案里存 `source.sha256`。正文改一个字节，
   `verify()` 立刻判 `STALE`——旧提案不能直接发布（T10 第二条验收）。
2. **不得补写正文没建立的因果。** PRD §15.2 明写。机器路径只做骨架：
   章节、字数、段落、出现的已登记实体。语义级的九类条目交给记忆管理员
   （给 `llm` 时），且模型输出必须过信封校验；失败就退回骨架并留一条
   issue——不「尽力而为地提取」出一堆看着像事实的东西。

## 为什么提案存在 runtime 而不是 Vault

提案是运行态：它等的是作者点头。真正的记忆落在 `06_Memory/`，走发布通道。
把提案写进 Vault，等于在正式区留了一堆「还没人批准的事实」，索引会把它们
当成档案读。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import markdown as md
from ..agents import runner
from ..agents.metering import Meter
from ..adapters.mock import LLMClient
from ..approvals import ApprovalStore
from ..audit import AUTHOR, now_iso
from ..config import read_yaml
from ..errors import ApprovalError, ConflictError, SecurityError, UsageError
from ..paths import ProjectPaths
from ..storage import dump_yaml, sha256_file, write_text_atomic
from ..tasks import TaskStore
from ..indexing.schema import SCHEMA_VERSION
from . import review as review_mod

PROPOSAL_DIR = "proposals"
PROPOSAL_PREFIX = "PROP"

# PRD §15.2 的九类（外加摘要与问题）。名字照抄，不自己发明同义词。
KINDS = (
    "ENTITY",
    "EVENT",
    "STATE_CHANGE",
    "RELATION",
    "KNOWLEDGE",
    "REVELATION",
    "RULE",
    "FORESHADOW",
    "SUMMARY",
    "ISSUE",
)

STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_STALE = "STALE"

INSTRUCTIONS = """\
从给定正文里提取记忆候选。

只输出 JSON 信封。`result.items` 是候选数组，每项必须有：
- kind：ENTITY / EVENT / STATE_CHANGE / RELATION / KNOWLEDGE / REVELATION / RULE / FORESHADOW / SUMMARY / ISSUE 之一
- summary：一句话说清「正文里哪个事实」；
- quote：正文里支撑它的原句片段（必须逐字来自正文，不要改写）；
- locator：形如 `段落序号:p0031`，指向正文块。

硬要求：
1. 只提取正文**明确写出**的事实。正文没写的因果，一条都不许补。
2. 拿不准的放进 kind=ISSUE，summary 写清「哪一点没法判定」。
3. 不要输出你的推理过程。
"""


@dataclass
class Item:
    kind: str
    summary: str
    quote: str = ""
    locator: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "summary": self.summary, "quote": self.quote, "locator": self.locator}


@dataclass
class Proposal:
    proposal_id: str
    project_id: str
    task_id: str
    chapter_id: str
    source_sha256: str
    source_target: str
    approval_id: str
    source_staged: str = ""
    status: str = STATUS_DRAFT
    base_release: str = ""
    items: list[Item] = field(default_factory=list)
    model_used: bool = False
    notes: list[str] = field(default_factory=list)
    created_at: str = ""
    approved_at: str = ""
    approved_by: str = ""
    schema_version: str = ""
    # 「本章不产记忆」的显式声明。空表示「有提案」。
    no_memory: bool = False
    no_memory_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "chapter_id": self.chapter_id,
            "source": {
                "target": self.source_target,
                "staged": self.source_staged,
                "sha256": self.source_sha256,
                "approval_id": self.approval_id,
            },
            "base_release": self.base_release or None,
            "status": self.status,
            "items": [item.as_dict() for item in self.items],
            "model_used": self.model_used,
            "notes": list(self.notes),
            "no_memory": self.no_memory,
            "no_memory_reason": self.no_memory_reason,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Proposal":
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        items = [
            Item(
                kind=str(raw.get("kind") or "ISSUE"),
                summary=str(raw.get("summary") or ""),
                quote=str(raw.get("quote") or ""),
                locator=str(raw.get("locator") or ""),
            )
            for raw in data.get("items") or []
            if isinstance(raw, dict)
        ]
        return cls(
            proposal_id=str(data.get("proposal_id") or ""),
            project_id=str(data.get("project_id") or ""),
            task_id=str(data.get("task_id") or ""),
            chapter_id=str(data.get("chapter_id") or ""),
            source_sha256=str(source.get("sha256") or ""),
            source_target=str(source.get("target") or ""),
            approval_id=str(source.get("approval_id") or ""),
            source_staged=str(source.get("staged") or ""),
            status=str(data.get("status") or STATUS_DRAFT),
            base_release=str(data.get("base_release") or ""),
            items=items,
            model_used=bool(data.get("model_used")),
            notes=[str(item) for item in data.get("notes") or []],
            created_at=str(data.get("created_at") or ""),
            approved_at=str(data.get("approved_at") or ""),
            approved_by=str(data.get("approved_by") or ""),
            schema_version=str(data.get("schema_version") or ""),
            no_memory=bool(data.get("no_memory")),
            no_memory_reason=str(data.get("no_memory_reason") or ""),
        )


class ProposalStore:
    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)
        self.root = self.runtime / PROPOSAL_DIR

    def path_of(self, proposal_id: str) -> Path:
        return self.root / f"{proposal_id}.yaml"

    def next_id(self) -> str:
        best = 0
        if self.root.exists():
            for path in self.root.glob(f"{PROPOSAL_PREFIX}-*.yaml"):
                found = re.search(rf"{PROPOSAL_PREFIX}-([0-9]+)", path.stem)
                if found:
                    best = max(best, int(found.group(1)))
        return f"{PROPOSAL_PREFIX}-{best + 1:04d}"

    def save(self, proposal: Proposal) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if not proposal.proposal_id:
            proposal.proposal_id = self.next_id()
        if not proposal.created_at:
            proposal.created_at = now_iso()
        if not proposal.schema_version:
            proposal.schema_version = str(SCHEMA_VERSION)
        path = self.path_of(proposal.proposal_id)
        write_text_atomic(path, dump_yaml(proposal.as_dict()))
        return path

    def load(self, proposal_id: str) -> Proposal:
        path = self.path_of(proposal_id)
        if not path.exists():
            raise UsageError(f"没有这份记忆提案：{proposal_id}")
        return Proposal.from_dict(read_yaml(path))

    def list(self, *, task_id: str = "", chapter_id: str = "") -> list[Proposal]:
        if not self.root.exists():
            return []
        out: list[Proposal] = []
        for path in sorted(self.root.glob(f"{PROPOSAL_PREFIX}-*.yaml")):
            try:
                proposal = Proposal.from_dict(read_yaml(path))
            except Exception:
                continue
            if task_id and proposal.task_id != task_id:
                continue
            if chapter_id and proposal.chapter_id != chapter_id:
                continue
            out.append(proposal)
        return out

    def for_chapter(self, chapter_id: str) -> Proposal | None:
        found = [item for item in self.list(chapter_id=chapter_id) if item.status != STATUS_REJECTED]
        return found[-1] if found else None


# ---------------------------------------------------------------------------
# 提取
# ---------------------------------------------------------------------------
@dataclass
class ExtractResult:
    proposal: Proposal
    path: Path
    warnings: list[str] = field(default_factory=list)


def extract(
    pp: ProjectPaths,
    runtime: Path,
    *,
    task_id: str,
    chapter_id: str,
    approvals: ApprovalStore,
    llm: LLMClient | None = None,
    meter: Meter | None = None,
    base_release: str = "",
    note: str = "",
    model: str = "",
) -> ExtractResult:
    """从已批准正文提取记忆候选。"""
    task = TaskStore(runtime).load(task_id)
    if task.project_id != pp.project_id:
        raise SecurityError(f"任务 {task.task_id} 属于 {task.project_id}，不是 {pp.project_id}")

    path, target, _ = review_mod.draft_of(pp, runtime, task_id, chapter_id)
    digest = sha256_file(path)

    # ---- 正文批准必须对得上当前字节 ----
    records = [
        item
        for item in approvals.for_target("artifact", chapter_id)
        if "CHAPTER_TEXT" in item.approved_scope
    ]
    if not records:
        raise ApprovalError(
            f"{chapter_id} 没有正文批准记录，不能提取记忆。\n"
            f"  先 `novel approve text --task {task_id} --chapter {chapter_id} --as author`\n"
            "  正文没批准就提取，等于把还没人认过的内容当成事实源"
        )
    approval = records[-1]
    if approval.target_hash != digest:
        raise ConflictError(
            f"{chapter_id} 的正文在批准后被改过（批准 {approval.target_hash[:12]}… ≠ 当前 {digest[:12]}…）。"
            "旧批准失效，需要重新审、重新批准——不能拿旧批准给新正文背书"
        )

    text = path.read_text(encoding="utf-8")
    doc = md.parse(text, str(path))
    body = doc.body

    warnings: list[str] = []
    items, model_used = _items_from_model(llm, body, chapter_id, task_id, meter, model, warnings)
    if not items:
        items = _skeleton(pp, body, chapter_id)
        if llm is not None:
            warnings.append("模型提取不可用，已退回确定性骨架（只有章节与实体清单，没有语义条目）")

    proposal = Proposal(
        proposal_id="",
        project_id=pp.project_id,
        task_id=task_id,
        chapter_id=chapter_id,
        source_sha256=digest,
        source_target=target,
        approval_id=approval.approval_id,
        source_staged=_vault_rel(pp, path),
        status=STATUS_DRAFT,
        base_release=base_release,
        items=items,
        model_used=model_used,
        notes=[note] if note else [],
    )
    saved = ProposalStore(runtime).save(proposal)
    return ExtractResult(proposal=proposal, path=saved, warnings=warnings)


def _items_from_model(
    llm: LLMClient | None,
    body: str,
    chapter_id: str,
    task_id: str,
    meter: Meter | None,
    model: str,
    warnings: list[str],
) -> tuple[list[Item], bool]:
    """给模型时的加值路径。失败一律返回空，让调用方退回骨架。"""
    if llm is None:
        return [], False
    run = runner.run(
        llm,
        "memory_keeper",
        instructions=INSTRUCTIONS,
        task={"task_id": task_id, "chapter_id": chapter_id, "chapter_text": body},
        anchors={"chapter_id": chapter_id},
        meter=meter,
        model=model,
    )
    if not run.ok:
        warnings.append("记忆管理员输出不可用：" + run.describe_errors())
        return [], False
    raw_items = run.result().get("items")
    if not isinstance(raw_items, list):
        warnings.append("记忆管理员输出的 result.items 不是数组，已退回骨架")
        return [], False
    items: list[Item] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").upper()
        summary = str(raw.get("summary") or "").strip()
        if kind not in KINDS or not summary:
            continue
        items.append(
            Item(
                kind=kind,
                summary=summary,
                quote=str(raw.get("quote") or ""),
                locator=str(raw.get("locator") or ""),
            )
        )
    if not items:
        warnings.append("记忆管理员一条合格候选都没给出，已退回骨架")
        return [], False
    return items, True


def _skeleton(pp: ProjectPaths, body: str, chapter_id: str) -> list[Item]:
    """确定性骨架：只写能数出来的东西，不猜因果。"""
    blocks = [block for block in md.parse("---\n---\n" + body).blocks if block.kind == md.BLOCK_PARAGRAPH]
    words = len(re.sub(r"\s", "", body))
    items = [
        Item("SUMMARY", f"{chapter_id} 正文共 {words} 字、{len(blocks)} 段", locator=""),
    ]
    for name in _registered_names(pp, body):
        items.append(Item("ENTITY", f"正文出现已登记实体：{name}", quote=name))
    items.append(
        Item(
            "ISSUE",
            "本提案由确定性骨架生成：只含摘要与实体清单，语义级事件／状态／认知未提取，需记忆管理员补",
        )
    )
    return items


def _source_path(pp: ProjectPaths, proposal: Proposal) -> Path | None:
    """提案所指的当前字节在哪：优先 staging，其次正式区。"""
    if proposal.source_staged:
        staged = pp.vault / proposal.source_staged
        if staged.exists():
            return staged
    if proposal.source_target:
        try:
            formal = pp.at(proposal.source_target)
        except Exception:
            return None
        if formal.exists():
            return formal
    return None


def _vault_rel(pp: ProjectPaths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(pp.vault.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _registered_names(pp: ProjectPaths, body: str) -> list[str]:
    """正文里出现的已登记实体名。只认档案里登记过的名字，不猜新名字。"""
    names: set[str] = set()
    for root_key in ("canon", "assets"):
        root = getattr(pp, root_key)
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            try:
                meta = md.parse(path.read_text(encoding="utf-8"), str(path)).meta
            except Exception:
                continue
            for value in (meta.get("name"), meta.get("title")):
                text = str(value or "").strip()
                if len(text) >= 2 and text in body:
                    names.add(text)
    return sorted(names)


# ---------------------------------------------------------------------------
# 校验、批准、声明
# ---------------------------------------------------------------------------
@dataclass
class VerifyResult:
    proposal_id: str
    status: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status != STATUS_STALE


def verify(proposal: Proposal, pp: ProjectPaths, runtime: Path) -> VerifyResult:
    """提案还有效吗：源哈希是否变化，源文件是否还在。"""
    if proposal.status == STATUS_REJECTED:
        return VerifyResult(proposal.proposal_id, STATUS_REJECTED, "提案已被驳回")
    if proposal.status == STATUS_STALE:
        return VerifyResult(proposal.proposal_id, STATUS_STALE, "提案已标记为失效")

    path = _source_path(pp, proposal)
    if path is None:
        # 提交后 staging 会被清理。那时正文已在正式区，退回 target 找；
        # 两边都找不到就是源不可达——按 STALE 处理，不猜。
        return VerifyResult(
            proposal.proposal_id,
            STATUS_STALE,
            f"源文件已不存在：{proposal.source_staged or proposal.source_target}",
        )
    digest = sha256_file(path)
    if digest != proposal.source_sha256:
        return VerifyResult(
            proposal.proposal_id,
            STATUS_STALE,
            f"正文已变化（{proposal.source_sha256[:12]}… → {digest[:12]}…）：旧提案必须重新核验",
        )
    return VerifyResult(proposal.proposal_id, proposal.status, "源哈希一致")


def approve(
    proposal: Proposal,
    store: ProposalStore,
    approvals: ApprovalStore,
    *,
    actor: str = AUTHOR,
    note: str = "",
) -> Proposal:
    """作者批准一份记忆提案。"""
    if actor != AUTHOR:
        raise SecurityError(f"记忆提案只能由作者批准（收到 {actor!r}）")
    if proposal.status == STATUS_REJECTED:
        raise ConflictError(f"{proposal.proposal_id} 已被驳回，不能批准；如需采纳请重新提取")
    if proposal.status == STATUS_STALE:
        raise ConflictError(f"{proposal.proposal_id} 已失效，不能批准；正文变了要重新提取")
    if not proposal.items:
        raise UsageError(f"{proposal.proposal_id} 没有任何条目，批准一份空提案没有意义")

    approvals.record(
        task_id=proposal.task_id,
        project_id=proposal.project_id,
        files=[],
        note=note or f"{proposal.chapter_id} 记忆提案批准",
        approver=actor,
        target_type="proposal",
        target_id=proposal.proposal_id,
        target_hash=proposal.source_sha256,
        approved_scope=["MEMORY_PROPOSAL"],
    )
    proposal.status = STATUS_APPROVED
    proposal.approved_at = now_iso()
    proposal.approved_by = actor
    store.save(proposal)
    return proposal


def reject(proposal: Proposal, store: ProposalStore, *, actor: str = AUTHOR, reason: str = "") -> Proposal:
    if actor != AUTHOR:
        raise SecurityError(f"记忆提案只能由作者驳回（收到 {actor!r}）")
    proposal.status = STATUS_REJECTED
    proposal.notes.append(f"驳回：{reason}" if reason else "驳回")
    store.save(proposal)
    return proposal


def declare_no_memory(
    runtime: Path,
    *,
    project_id: str,
    task_id: str,
    chapter_id: str,
    reason: str,
    actor: str = AUTHOR,
) -> Path:
    """显式声明「本章不产记忆」。

    这是 T10 第三条验收的关键：允许「没有记忆」，但**不允许「没人说过要不要」**。
    发布闸门看到一份 `no_memory=True` 的提案才知道这是决定，而不是漏做。
    """
    if actor != AUTHOR:
        raise SecurityError(f"「本章不产记忆」是作者的决定（收到 {actor!r}）")
    if not reason.strip():
        raise UsageError("声明不产记忆必须写理由（例如「本章只有环境描写，没有新事实」）")
    proposal = Proposal(
        proposal_id="",
        project_id=project_id,
        task_id=task_id,
        chapter_id=chapter_id,
        source_sha256="",
        source_target="",
        approval_id="",
        status=STATUS_APPROVED,
        no_memory=True,
        no_memory_reason=reason.strip(),
        approved_at=now_iso(),
        approved_by=actor,
    )
    return ProposalStore(runtime).save(proposal)


__all__ = [
    "KINDS",
    "Item",
    "Proposal",
    "ProposalStore",
    "ExtractResult",
    "VerifyResult",
    "approve",
    "declare_no_memory",
    "extract",
    "reject",
    "verify",
]
