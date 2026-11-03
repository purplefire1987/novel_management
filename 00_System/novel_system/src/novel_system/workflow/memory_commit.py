"""写后记忆提交：提案 → 校验 → 审批 → 事务写入 → 回归审计（PRD 12 / 15）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .. import vault
from ..agents.memory_manager import MemoryManagerAgent
from ..audit.logger import AuditLog
from ..audit.reports import issues_table, write_report
from ..config import Config
from ..database.sqlite import Index, now_iso
from ..memory import retrieval
from ..memory.summarizer import render_rolling_summary
from ..transaction import Transaction, TransactionError, commit_artifacts, new_operation_id
from ..validators import canon, continuity, foreshadowing
from ..validators.permissions import resolve_approval_class
from ..validators.schema import Issue, Proposal, ProposalBundle, validate_meta
from .state_machine import WorkflowError, chapter_status, ensure_transition, with_status


@dataclass
class MemoryResult:
    chapter_id: str
    operation_id: str
    bundle_path: str
    report_path: str
    proposals: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    git: dict[str, Any] = field(default_factory=dict)
    audit_report: str = ""
    mode: str = "offline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "operation_id": self.operation_id,
            "bundle": self.bundle_path,
            "report": self.report_path,
            "proposals": self.proposals,
            "issues": [i.to_dict() for i in self.issues],
            "dropped": self.dropped,
            "applied": self.applied,
            "pending": self.pending,
            "git": self.git,
            "audit_report": self.audit_report,
            "mode": self.mode,
        }


# --------------------------------------------------------------------- 路径


def bundle_path(cfg: Config, chapter_id: str) -> Path:
    return cfg.staging_dir / "memory" / f"{chapter_id}.proposals.yaml"


def _rel(cfg: Config, path: Path) -> str:
    return cfg.rel(path)


# ------------------------------------------------------------------ 提案生成


def _canon_context(cfg: Config, index: Index, chapter_id: str) -> str:
    hot = retrieval.hot_memory(cfg)
    parts = [hot.get("current_story_state", "")]
    for row in index.entities_by_type("character"):
        parts.append(f"- {row['id']} {row.get('name')}: canon={row.get('canon_status')}")
    for row in index.foreshadowing_all():
        parts.append(
            f"- {row['id']} {row.get('name')}: status={row.get('status')}"
            f" 窗口 {row.get('target_start')}～{row.get('target_end')}"
        )
    return "\n".join(p for p in parts if p)[:3000]


def generate(cfg: Config, index: Index, chapter_id: str) -> MemoryResult:
    manuscript = retrieval.manuscript_doc(cfg, index, chapter_id)
    if not manuscript:
        raise WorkflowError(f"找不到正式正文：{chapter_id}（需先 publish）")
    status = str(manuscript.meta.get("status") or "")
    if cfg.workflow.get("require_published_before_memory_commit", True) and status not in (
        "published",
        "memory_committed",
    ):
        raise WorkflowError(f"{chapter_id} 状态为 {status}，必须先由作者确认并发布（published）")

    existing = index.events_of_chapter(chapter_id)
    from ..agents.memory_manager import next_event_sequence

    agent = MemoryManagerAgent(cfg, index)
    result = agent.extract(
        chapter_id=chapter_id,
        manuscript=manuscript,
        canon_context=_canon_context(cfg, index, chapter_id),
        existing_events=existing,
        event_sequence=next_event_sequence(existing),
    )

    raw_proposals = result.data.get("proposals") or []
    proposals: list[dict[str, Any]] = []
    issues: list[Issue] = []
    existing_names = {str(e.get("name") or "") for e in existing}
    skipped_duplicates: list[str] = []

    for raw in raw_proposals:
        try:
            proposal = Proposal.model_validate(raw).model_dump()
        except Exception as exc:  # noqa: BLE001
            issues.append(Issue("MAJOR", "proposal_schema", f"提案结构非法：{exc}"))
            continue
        # AC-11：同一章同一事件名重复提取时，直接按幂等跳过，不生成第二个事件
        if (
            proposal["kind"] == "event"
            and str(proposal["payload"].get("name") or "") in existing_names
        ):
            skipped_duplicates.append(proposal["proposal_id"])
            continue
        # 审批级别以配置为准，模型只能收紧不能放宽
        configured = resolve_approval_class(cfg, proposal["kind"], proposal["payload"])
        if proposal["approval_class"] == "auto" and configured != "auto":
            proposal["approval_class"] = configured
        proposals.append(proposal)

    for pid in skipped_duplicates:
        issues.append(
            Issue(
                "MINOR",
                "idempotency",
                f"{pid} 的事件在本章已存在（同名），已按幂等跳过",
                refs=[pid],
            )
        )

    op_id = new_operation_id()
    bundle = ProposalBundle(
        chapter_id=chapter_id,
        operation_id=op_id,
        generated_by=f"memory_manager/{result.mode}",
        mode=result.mode,
        proposals=[Proposal.model_validate(p) for p in proposals],
        conflicts=result.data.get("conflicts") or [],
        unknowns=result.data.get("unknowns") or [],
        sources=result.sources,
    )
    path = bundle_path(cfg, chapter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    vault.write_atomic(
        path,
        yaml.safe_dump(bundle.model_dump(), allow_unicode=True, sort_keys=False),
    )

    issues.extend(validate_proposals(cfg, index, chapter_id, proposals))

    report_body = "\n".join(
        [
            f"# 记忆提取｜{chapter_id}",
            "",
            f"- 运行模式：{result.mode}",
            f"- 提案数：{len(proposals)}",
            f"- 提案文件：`{_rel(cfg, path)}`",
            "",
            "## 提案清单",
            "| 提案 | 类型 | 操作 | 目标 | 审批级别 | 证据 |",
            "|---|---|---|---|---|---|",
            *[
                f"| {p['proposal_id']} | {p['kind']} | {p['op']} | {p.get('target_id') or '-'} | "
                f"{p['approval_class']} | {(p['evidence'] or ['-'])[0][:40]} |"
                for p in proposals
            ],
            "",
            "## 校验问题",
            issues_table(issues),
            "",
            "## Agent 说明",
            result.content,
            "",
        ]
    )
    report_rel = write_report(
        cfg,
        "memory",
        f"MEMORY-{chapter_id}",
        report_type="Memory_Update",
        target=chapter_id,
        verdict="PROPOSED",
        body=report_body,
        generated_by=f"memory_manager/{result.mode}",
        extra_meta={"proposal_count": len(proposals)},
    )

    AuditLog(cfg, index).record(
        operation="memory_extract",
        target=chapter_id,
        agent="memory_manager",
        actor="system",
        status="proposed",
        detail=f"{len(proposals)} 条提案｜{report_rel}",
        operation_id=op_id,
    )

    return MemoryResult(
        chapter_id=chapter_id,
        operation_id=op_id,
        bundle_path=_rel(cfg, path),
        report_path=report_rel,
        proposals=proposals,
        issues=issues,
        mode=result.mode,
    )


# ------------------------------------------------------------------ 提案校验


def validate_proposals(
    cfg: Config, index: Index, chapter_id: str, proposals: list[dict[str, Any]]
) -> list[Issue]:
    issues: list[Issue] = []
    for proposal in proposals:
        pid = proposal["proposal_id"]
        kind = proposal["kind"]
        target = proposal.get("target_id")

        if not proposal.get("evidence"):
            issues.append(
                Issue(
                    "MAJOR",
                    "evidence",
                    f"{pid} 缺少来源引证，不能写入正式记忆",
                    refs=[pid],
                    blocks_write=True,
                )
            )

        if kind == "event":
            payload = proposal["payload"]
            if not payload.get("objective_fact"):
                issues.append(
                    Issue("MAJOR", "event", f"{pid} 缺少客观事实描述", refs=[pid], blocks_write=True)
                )
            issues.extend(
                canon.check_refs_exist(
                    payload.get("participants") or [],
                    index.exists,
                    category="event_participant",
                    blocking=True,
                    context=pid,
                )
            )
            location = payload.get("location_id")
            if location and not index.exists(location):
                issues.append(
                    Issue("MAJOR", "event", f"{pid} 的地点 {location} 不存在", refs=[pid], blocks_write=True)
                )
            if target and index.event_exists(str(target)):
                issues.append(
                    Issue(
                        "MINOR",
                        "idempotency",
                        f"{pid} 的事件 {target} 已存在，将按幂等跳过",
                        refs=[str(target)],
                    )
                )

        elif kind == "character_state":
            char_id = str(target or "").replace("STATE-", "")
            if not index.exists(char_id):
                issues.append(
                    Issue("MAJOR", "state", f"{pid} 指向的人物 {char_id} 不存在", refs=[char_id], blocks_write=True)
                )
            existing = _state_doc(cfg, index, char_id)
            if existing:
                issues.extend(
                    continuity.check_state_regression(
                        _state_view(existing), proposal["payload"], proposal.get("evidence") or [], chapter_id
                    )
                )
            payload_text = json.dumps(proposal["payload"], ensure_ascii=False)
            if "复活" in payload_text:
                issues.append(
                    Issue(
                        "BLOCKER",
                        "canon",
                        f"{pid} 涉及复活，违反世界规则（死者不可复生）",
                        refs=[char_id],
                        blocks_write=True,
                    )
                )

        elif kind == "foreshadowing":
            row = index.foreshadowing_get(str(target))
            if not row:
                issues.append(
                    Issue("MAJOR", "foreshadowing", f"{pid} 的伏笔 {target} 不存在", refs=[str(target)], blocks_write=True)
                )
                continue
            meta = {
                "id": row["id"],
                "status": row["status"],
                "planted_in": row.get("planted_in"),
                "target_reveal_window": {"start": row.get("target_start"), "end": row.get("target_end")},
                "trigger_conditions": [],
            }
            new_status = str(proposal["payload"].get("status") or "")
            issues.extend(
                foreshadowing.check_transition(meta, new_status, chapter_id, proposal.get("evidence") or [])
            )

        elif kind == "relationship":
            if not target:
                issues.append(Issue("MAJOR", "relationship", f"{pid} 缺少关系实体 ID", refs=[pid], blocks_write=True))
            elif not index.exists(str(target)):
                issues.append(
                    Issue("MAJOR", "relationship", f"{pid} 的关系 {target} 不存在", refs=[str(target)], blocks_write=True)
                )
    return issues


def _state_doc(cfg: Config, index: Index, char_id: str):
    row = index.get_entity(f"STATE-{char_id}", "character_state")
    if not row:
        return None
    path = cfg.abs(str(row["file_path"]))
    return vault.read_doc(path) if path.exists() else None


def _state_view(doc: vault.Doc) -> dict[str, Any]:
    kv = vault.key_values(doc.section("客观状态"))
    kv.update({"character_id": doc.meta.get("character_id"), "id": doc.meta.get("id")})
    return kv


# ------------------------------------------------------------------ 提案应用


def load_bundle(cfg: Config, chapter_id: str) -> dict[str, Any] | None:
    path = bundle_path(cfg, chapter_id)
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or None


def _select(
    bundle: dict[str, Any], approve: str, ids: Iterable[str] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    proposals = list(bundle.get("proposals") or [])
    explicit = {i.strip() for i in (ids or []) if i.strip()}

    selected: list[dict[str, Any]] = []
    pending: list[str] = []
    for proposal in proposals:
        cls = proposal.get("approval_class", "author")
        pid = proposal.get("proposal_id")
        if approve == "all":
            selected.append(proposal)
        elif explicit:
            if pid in explicit:
                selected.append(proposal)
            else:
                pending.append(pid)
        elif approve == "auto":
            if cls == "auto":
                selected.append(proposal)
            else:
                pending.append(pid)
        else:
            pending.append(pid)
    return selected, pending


def apply(
    cfg: Config,
    index: Index,
    chapter_id: str,
    *,
    approve: str = "auto",
    ids: Iterable[str] | None = None,
    actor: str = "author",
) -> MemoryResult:
    bundle = load_bundle(cfg, chapter_id)
    if not bundle:
        raise WorkflowError(f"没有待审批提案：{chapter_id}。先运行 `novel commit-memory {chapter_id}`。")

    manuscript = retrieval.manuscript_doc(cfg, index, chapter_id)
    if not manuscript:
        raise WorkflowError(f"找不到正式正文：{chapter_id}")

    proposals = list(bundle.get("proposals") or [])
    issues = validate_proposals(cfg, index, chapter_id, proposals)
    # 逐条判定阻断，避免「某条提案的问题把其它提案也带下水」
    blocking: set[str] = set()
    for proposal in proposals:
        pid = proposal["proposal_id"]
        if any(i.blocks_write for i in validate_proposals(cfg, index, chapter_id, [proposal])):
            blocking.add(pid)

    selected, pending = _select(bundle, approve, ids)
    approved = [p for p in selected if p.get("proposal_id") not in blocking]
    dropped = [p["proposal_id"] for p in selected if p.get("proposal_id") in blocking]

    before_states: dict[str, dict[str, Any]] = {}
    for proposal in approved:
        if proposal["kind"] == "character_state":
            char_id = str(proposal.get("target_id") or "").replace("STATE-", "")
            doc = _state_doc(cfg, index, char_id)
            if doc:
                before_states[char_id] = _state_view(doc)

    if not approved:
        return MemoryResult(
            chapter_id=chapter_id,
            operation_id=str(bundle.get("operation_id") or ""),
            bundle_path=_rel(cfg, bundle_path(cfg, chapter_id)),
            report_path="",
            proposals=proposals,
            issues=issues,
            dropped=dropped,
            pending=pending or [p["proposal_id"] for p in proposals],
            mode=str(bundle.get("mode") or "offline"),
        )

    tx = Transaction(
        cfg,
        index,
        actor=actor,
        agent="memory_manager",
        operation="memory_commit",
        target=chapter_id,
    )

    applied: list[str] = []
    canon_changes: list[str] = []
    summary_one_line = ""
    summary_facts: list[str] = []

    for proposal in approved:
        kind = proposal["kind"]
        payload = proposal["payload"] or {}
        target = proposal.get("target_id")
        pid = proposal["proposal_id"]

        if kind == "event":
            event_id = str(target)
            if index.event_exists(event_id):
                continue  # 幂等
            rel = f"07_Entity_Graph/Events/{event_id}.md"
            meta = {
                "id": event_id,
                "type": "event",
                "name": payload.get("name") or event_id,
                "chapter_id": chapter_id,
                "sequence": int(event_id.rsplit("-", 1)[-1]),
                "canon_status": "confirmed",
                "participants": payload.get("participants") or [],
                "location_id": payload.get("location_id") or "",
                "source_refs": [chapter_id],
                "created_at": now_iso()[:10],
                "updated_at": now_iso()[:10],
            }
            tx.stage_doc(rel, meta, _event_body(payload, chapter_id, proposal))
            applied.append(pid)

        elif kind == "character_state":
            char_id = str(target or "").replace("STATE-", "")
            doc = _state_doc(cfg, index, char_id)
            if doc:
                meta = dict(doc.meta)
                body = _kv_update(
                    doc.body,
                    "客观状态",
                    {
                        "位置": payload.get("location") or None,
                        "伤势": payload.get("injury") or None,
                        "身体状态": payload.get("mind") or None,
                        "能力状态": payload.get("ability") or None,
                        "持有物": payload.get("holdings") or None,
                        "客观生死": payload.get("objective_status") or None,
                    },
                )
                awareness = payload.get("awareness") or []
                unknown = payload.get("unknown") or []
                if awareness or unknown:
                    body = _kv_update(
                        body,
                        "认知边界（角色以为的世界，不等于事实）",
                        {
                            "已知信息": "；".join(a for a in awareness if a) or None,
                            "未知信息": "；".join(u for u in unknown if u) or None,
                        },
                    )
                if payload.get("goal"):
                    body = _kv_update(body, "当前目标", {"目标": payload["goal"]})
                meta["as_of_event"] = payload.get("as_of_event") or meta.get("as_of_event")
                meta["updated_at"] = now_iso()[:10]
                tx.stage_doc(_rel(cfg, doc.path), meta, body)
                applied.append(pid)
                canon_changes.append(f"{char_id} 状态更新｜{_summarize_payload(payload)}")
            else:
                issues.append(
                    Issue(
                        "MAJOR",
                        "state",
                        f"{pid} 找不到 {char_id} 的状态文件，跳过（需先建立状态档案）",
                        refs=[char_id],
                    )
                )

        elif kind == "relationship":
            row = index.get_entity(str(target), "relationship")
            if not row:
                issues.append(Issue("MINOR", "relationship", f"{pid} 关系 {target} 不存在，跳过", refs=[str(target)]))
                continue
            path = cfg.abs(str(row["file_path"]))
            doc = vault.read_doc(path)
            line = f"- {chapter_id}：{payload.get('note') or payload.get('relation_type') or ''}"
            body = doc.body.rstrip()
            if line not in body:
                body = body + "\n" + line + "\n"
            meta = dict(doc.meta)
            meta["updated_at"] = now_iso()[:10]
            tx.stage_doc(_rel(cfg, path), meta, body)
            applied.append(pid)
            canon_changes.append(f"{target} 关系变化：{payload}")

        elif kind == "foreshadowing":
            row = index.get_entity(str(target), "foreshadowing")
            if not row or not row.get("file_path"):
                issues.append(Issue("MINOR", "foreshadowing", f"{pid} 伏笔 {target} 缺失，跳过", refs=[str(target)]))
                continue
            path = cfg.abs(str(row["file_path"]))
            doc = vault.read_doc(path)
            new_status = str(payload.get("status") or doc.meta.get("status"))
            meta = dict(doc.meta)
            meta["status"] = new_status
            meta["updated_at"] = now_iso()[:10]
            history_line = f"- {new_status}: {chapter_id}（{payload.get('note') or '记忆提交'}）"
            body = _append_under_section(doc.body, "状态历史", history_line)
            tx.stage_doc(_rel(cfg, path), meta, body)
            applied.append(pid)
            canon_changes.append(f"{target} 伏笔状态 → {new_status}")
            if new_status in ("paid_off", "triggered"):
                issues.append(
                    Issue(
                        "MINOR",
                        "foreshadowing",
                        f"{target} 进入 {new_status}，属于重大 Canon 变化，请在报告中留档",
                        refs=[str(target)],
                    )
                )

        elif kind == "summary":
            summary_one_line = payload.get("one_line") or summary_one_line
            summary_facts.extend([f for f in (payload.get("facts") or []) if f])
            applied.append(pid)

        elif kind == "canon_change":
            canon_changes.append(f"{target or '未指定'}：{payload}")
            applied.append(pid)

    # 摘要更新
    if summary_one_line:
        rolling_path = cfg.dir("short_term_memory") / "Rolling_Summary.md"
        existing_body = vault.read_doc(rolling_path).body if rolling_path.exists() else "# 滚动摘要"
        new_body = render_rolling_summary(
            existing_body,
            chapter_id,
            str(manuscript.meta.get("title") or chapter_id),
            summary_one_line,
            summary_facts,
        )
        tx.stage_doc(
            cfg.rel(rolling_path),
            {"type": "short_term", "scope": "rolling_summary", "as_of_chapter": chapter_id, "updated_at": now_iso()[:10]},
            new_body,
        )

        state_path = cfg.dir("hot_memory") / "Current_Story_State.md"
        if state_path.exists():
            state_doc = vault.read_doc(state_path)
            state_meta = dict(state_doc.meta)
            state_meta["as_of_chapter"] = chapter_id
            state_meta["updated_at"] = now_iso()[:10]
            state_body = _replace_section(
                state_doc.body, f"本章变化（{chapter_id}）", f"- {summary_one_line}"
            )
            tx.stage_doc(cfg.rel(state_path), state_meta, state_body)

    # 正文状态 → memory_committed
    ensure_transition(cfg, str(manuscript.meta.get("status")), "memory_committed")
    new_manuscript_meta = with_status(manuscript.meta, "memory_committed")
    tx.stage_doc(cfg.rel(manuscript.path), new_manuscript_meta, manuscript.body)

    if not applied and not summary_one_line:
        raise WorkflowError("没有可应用的提案（全部被阻断或未批准）。")

    # 变更日志（纳入同一事务，回滚时一并还原）
    log_rel, log_meta, log_body = _canon_log_doc(
        cfg, tx.op_id, chapter_id, canon_changes, len(applied) + (1 if summary_one_line else 0)
    )
    tx.stage_doc(log_rel, log_meta, log_body)

    commit = tx.commit(f"memory commit {chapter_id}")
    op_id = commit["operation_id"]

    # 更新提案状态
    for proposal in proposals:
        if proposal["proposal_id"] in applied:
            proposal["status"] = "approved"
        elif proposal["proposal_id"] in dropped:
            proposal["status"] = "rejected"
    bundle["proposals"] = proposals
    vault.write_atomic(
        bundle_path(cfg, chapter_id),
        yaml.safe_dump(bundle, allow_unicode=True, sort_keys=False),
    )

    audit_rel = _regression_audit(cfg, index, chapter_id, before_states)

    report_body = "\n".join(
        [
            f"# 记忆提交｜{chapter_id}",
            "",
            f"- operation_id：`{op_id}`",
            f"- 已应用：{len(applied)} 条",
            f"- 被阻断：{len(dropped)} 条",
            f"- 待审批：{len(pending)} 条",
            f"- Git：{commit.get('git')}",
            "",
            "## 已应用提案",
            *([f"- {pid}" for pid in applied] or ["- 无"]),
            "",
            "## Canon 变化",
            *([f"- {c}" for c in canon_changes] or ["- 无"]),
            "",
            "## 校验问题",
            issues_table(issues),
            "",
            f"## 回滚",
            f"```bash\nnovel rollback {op_id}\n```",
            "",
        ]
    )
    report_rel = write_report(
        cfg,
        "memory",
        f"MEMORY-COMMIT-{chapter_id}",
        report_type="Memory_Commit",
        target=chapter_id,
        verdict="COMMITTED",
        body=report_body,
        generated_by=f"memory_manager/{bundle.get('mode', 'offline')}",
        extra_meta={"operation_id": op_id, "applied": len(applied)},
    )

    AuditLog(cfg, index).record(
        operation="memory_commit",
        target=chapter_id,
        agent="memory_manager",
        actor=actor,
        status="committed",
        detail=f"{len(applied)} 条｜{op_id}",
        operation_id=op_id,
    )

    commit_artifacts(cfg, f"memory reports {chapter_id}")
    return MemoryResult(
        chapter_id=chapter_id,
        operation_id=op_id,
        bundle_path=_rel(cfg, bundle_path(cfg, chapter_id)),
        report_path=report_rel,
        proposals=proposals,
        issues=issues,
        dropped=dropped,
        applied=applied,
        pending=pending,
        git=commit.get("git", {}),
        audit_report=audit_rel,
        mode=str(bundle.get("mode") or "offline"),
    )


# ------------------------------------------------------------------ 文本改写


def _event_body(payload: dict[str, Any], chapter_id: str, proposal: dict[str, Any]) -> str:
    knowledge = payload.get("knowledge") or {}
    lines = [
        f"# {payload.get('name') or '事件'}",
        "",
        f"- 事件时间：{payload.get('time') or f'{chapter_id} 内'}",
        f"- 客观事实：{payload.get('objective_fact') or ''}",
        f"- 参与者：{', '.join(payload.get('participants') or []) or '未记录'}",
        f"- 结果：{payload.get('results') or ''}",
        f"- 造成的状态变化：{'; '.join(payload.get('state_changes') or []) or '无'}",
        f"- 影响的关系：{payload.get('relationship_changes') or '无'}",
        f"- 影响的任务：{payload.get('mission_changes') or '无'}",
        f"- 影响的伏笔：{payload.get('foreshadow_changes') or '无'}",
        f"- 哪些角色知道此事：{', '.join(knowledge.get('knows') or []) or '未记录'}",
        f"- 哪些角色不知道此事：{', '.join(knowledge.get('unaware') or []) or '未记录'}",
        "",
        "## 来源",
        *[f"- {e}" for e in (proposal.get("evidence") or [f"{chapter_id}"])],
        "",
        "## 提案",
        f"- {proposal.get('proposal_id')}（{proposal.get('confidence')}）",
    ]
    return "\n".join(lines) + "\n"


def _summarize_payload(payload: dict[str, Any]) -> str:
    """把提案 payload 压成一行可读文本，空字段不写进报告。"""
    labels = {
        "location": "位置",
        "injury": "伤势",
        "objective_status": "生死",
        "mind": "精神",
        "ability": "能力",
        "holdings": "持有物",
        "awareness": "认知",
        "unknown": "未知",
        "goal": "目标",
    }
    parts: list[str] = []
    for key, label in labels.items():
        value = payload.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = "；".join(str(v) for v in value if v)
        if value:
            parts.append(f"{label}={value}")
    return "，".join(parts) or "（无字段变化）"


def _append_under_section(body: str, heading: str, line: str) -> str:
    """把一行记录插到指定小节末尾；已存在同一条则跳过（幂等）。"""
    lines = body.splitlines()
    start = None
    end = len(lines)
    for i, raw in enumerate(lines):
        if raw.strip().startswith("## ") and raw.strip()[3:].strip() == heading:
            start = i
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith("## "):
                    end = j
                    break
            break
    if start is None:
        return body.rstrip() + f"\n\n## {heading}\n{line}\n"
    if any(existing.strip() == line.strip() for existing in lines[start:end]):
        return body
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, line)
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")


def _replace_section(body: str, heading: str, content: str) -> str:
    """整段替换某小节内容；不存在则追加。"""
    lines = body.splitlines()
    for i, raw in enumerate(lines):
        if raw.strip().startswith("## ") and raw.strip()[3:].strip() == heading:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith("## "):
                    end = j
                    break
            new_lines = lines[: i + 1] + [content.strip(), ""] + lines[end:]
            return "\n".join(new_lines).rstrip() + "\n"
    return body.rstrip() + f"\n\n## {heading}\n{content.strip()}\n"


def _kv_update(body: str, heading: str, updates: dict[str, str | None]) -> str:
    """只改 `- 键：值` 行，保留其它内容与顺序。"""
    lines = body.splitlines()
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("## ") and line.strip()[3:].strip() == heading:
            start = i + 1
            for j in range(i + 1, len(lines)):
                if lines[j].strip().startswith("## "):
                    end = j
                    break
            break
    if start is None:
        extra = "\n".join(f"- {k}：{v}" for k, v in updates.items() if v)
        return body.rstrip() + f"\n\n## {heading}\n{extra}\n"

    for key, value in updates.items():
        if not value:
            continue
        pattern = re.compile(rf"^(\s*-\s*){re.escape(key)}\s*[：:]")
        found = False
        for i in range(start, end):
            if pattern.match(lines[i]):
                lines[i] = f"- {key}：{value}"
                found = True
                break
        if not found:
            lines.insert(end, f"- {key}：{value}")
            end += 1
    return "\n".join(lines) + ("\n" if not body.endswith("\n") else "")


def _canon_log_doc(
    cfg: Config, op_id: str, chapter_id: str, changes: list[str], count: int
) -> tuple[str, dict[str, Any], str]:
    rel = f"10_Change_Log/Canon_Changes/{op_id}.md"
    meta = {
        "id": op_id,
        "type": "canon_change_log",
        "chapter_id": chapter_id,
        "operation_id": op_id,
        "created_at": now_iso(),
    }
    body = "\n".join(
        [
            f"# Canon 变更｜{chapter_id}",
            "",
            f"- 操作：`{op_id}`",
            f"- 变更条数：{count}",
            "",
            "## 变更明细",
            *([f"- {c}" for c in changes] or ["- 仅摘要更新"]),
            "",
            "## 回滚",
            f"`novel rollback {op_id}`",
        ]
    )
    return rel, meta, body


# ------------------------------------------------------------------ 回归审计


def _regression_audit(
    cfg: Config, index: Index, chapter_id: str, before_states: dict[str, dict[str, Any]]
) -> str:
    index_after = index
    issues: list[Issue] = []

    # 1) 索引一致性：文件哈希必须与索引记录一致
    for row in index_after.all_entities():
        path = cfg.abs(str(row.get("file_path") or ""))
        if not path.exists():
            issues.append(
                Issue("MAJOR", "index", f"索引指向的文件不存在：{row.get('file_path')}", refs=[str(row["id"])])
            )
            continue
        current = vault.file_hash(path)
        if row.get("content_hash") and current != row.get("content_hash"):
            issues.append(
                Issue(
                    "MINOR",
                    "index",
                    f"{row['id']} 的文件哈希与索引不一致，建议 novel rebuild-index",
                    refs=[str(row["id"])],
                )
            )

    # 2) 状态回归：伤势/位置变化必须有事件依据
    for char_id, old_state in before_states.items():
        doc = _state_doc(cfg, index, char_id)
        if not doc:
            continue
        new_state = _state_view(doc)
        events = index.events_of_chapter(chapter_id)
        evidence = [f"{chapter_id}"] if events else []
        issues.extend(continuity.check_state_regression(old_state, new_state, evidence, chapter_id))

    # 3) 伏笔超期复查
    for row in index.foreshadowing_all():
        meta = {
            "id": row["id"],
            "status": row["status"],
            "planted_in": row.get("planted_in"),
            "target_reveal_window": {"start": row.get("target_start"), "end": row.get("target_end")},
        }
        issues.extend(foreshadowing.check_ledger_entry(meta, chapter_id))

    verdict = "BLOCKED" if any(i.severity == "BLOCKER" for i in issues) else (
        "NEEDS_ATTENTION" if issues else "CLEAN"
    )
    body = "\n".join(
        [
            f"# 连续性回归审计｜{chapter_id}",
            "",
            "## 结论",
            f"**{verdict}**",
            "",
            "## 检查项",
            "- 索引与文件一致性",
            "- 人物状态回退（伤势/位置）",
            "- 伏笔窗口与状态",
            "",
            "## 问题清单",
            issues_table(issues),
        ]
    )
    return write_report(
        cfg,
        "continuity",
        f"AUDIT-{chapter_id}",
        report_type="Continuity",
        target=chapter_id,
        verdict=verdict,
        body=body,
        generated_by="memory_commit/regression",
        extra_meta={"issue_count": len(issues)},
    )
