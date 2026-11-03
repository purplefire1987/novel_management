"""记忆管理员 Agent：从正式正文提取更新提案（PRD 9.7 / 12）。

关键约束：角色认知与客观事实必须分开；没有来源的推断不得成为 Canon。
"""

from __future__ import annotations

import re
from typing import Any

from .. import vault
from ..validators.permissions import resolve_approval_class
from .base import AgentResult, BaseAgent, offline_note

ANNOTATION_RE = re.compile(r"^-\s*(事件|状态|关系|伏笔|摘要)\s*[:：]\s*(.+)$")


def next_event_sequence(existing: list[dict[str, Any]]) -> int:
    seqs = [int(e.get("sequence") or 0) for e in existing]
    return (max(seqs) if seqs else 0) + 1


def find_evidence(body: str, keywords: list[str], limit: int = 2) -> list[str]:
    """从正文里找包含关键词的句子，作为提案的引用句段。"""
    sentences = [s.strip() for s in re.split(r"(?<=[。！？\n])", body) if s.strip()]
    hits: list[str] = []
    for sentence in sentences:
        if any(k and k in sentence for k in keywords):
            if sentence not in hits:
                hits.append(sentence[:120])
        if len(hits) >= limit:
            break
    return hits


def parse_annotations(notes_body: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in notes_body.splitlines():
        match = ANNOTATION_RE.match(line.strip())
        if match:
            out.append((match.group(1), match.group(2).strip()))
    return out


def _split_pipes(text: str) -> list[str]:
    return [p.strip() for p in text.split("|") if p.strip()]


def _pairs(parts: list[str]) -> tuple[str, dict[str, str], list[str]]:
    """`名称 | 客观事实 | 键:值` -> (名称, {键: 值}, [其它位置字段])"""
    name = ""
    kv: dict[str, str] = {}
    positional: list[str] = []
    for part in parts:
        for sep in ("=", ":", "："):
            if sep in part:
                key, _, value = part.partition(sep)
                kv[key.strip()] = value.strip()
                break
        else:
            if not name:
                name = part
            else:
                positional.append(part)
    return name, kv, positional


def _ids(text: str) -> list[str]:
    from ..memory.entity_resolver import ids_in_text

    return ids_in_text(text)


class MemoryManagerAgent(BaseAgent):
    key = "memory_manager"
    display = "记忆管理员 Agent"

    def extract(
        self,
        *,
        chapter_id: str,
        manuscript: vault.Doc,
        canon_context: str,
        existing_events: list[dict[str, Any]],
        event_sequence: int | None = None,
    ) -> AgentResult:
        if self.llm.available:
            prompt = (
                f"章节：{chapter_id}\n\n正式正文：\n{manuscript.body}\n\n"
                f"现有 Canon 摘要：\n{canon_context}\n\n"
                "请提取事件、状态、关系、伏笔、摘要更新提案，并按约定 JSON 输出。"
                "角色认知写入 payload.awareness，不得混入客观状态。"
            )
            try:
                content = self.run_llm(prompt, temperature=0.1)
                data = self.extract_json(content) or {}
                proposals = self._normalize(data.get("proposals") or [], chapter_id)
                return self.make_result(
                    status="OK",
                    content=content,
                    mode="llm",
                    data={
                        "proposals": proposals,
                        "conflicts": data.get("conflicts") or [],
                        "unknowns": data.get("unknowns") or [],
                    },
                    sources=[f"{chapter_id}（正文）"],
                )
            except Exception as exc:  # noqa: BLE001
                seq = event_sequence or next_event_sequence(existing_events)
                return self._offline(chapter_id, manuscript, seq, f"LLM 调用失败：{exc}")
        seq = event_sequence or next_event_sequence(existing_events)
        return self._offline(chapter_id, manuscript, seq, "未配置 LLM")

    # ------------------------------------------------------------------ 规范化
    def _normalize(self, proposals: list[dict[str, Any]], chapter_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for index, raw in enumerate(proposals, start=1):
            payload = raw.get("payload") or {}
            kind = str(raw.get("kind") or "event")
            approval = str(raw.get("approval_class") or resolve_approval_class(self.cfg, kind, payload))
            out.append(
                {
                    "proposal_id": raw.get("proposal_id") or f"PROP-{chapter_id}-{index:02d}",
                    "kind": kind,
                    "op": raw.get("op") or "create",
                    "target_id": raw.get("target_id"),
                    "chapter_id": chapter_id,
                    "payload": payload,
                    "evidence": raw.get("evidence") or [],
                    "confidence": raw.get("confidence") or "PROPOSED",
                    "approval_class": approval,
                    "status": "proposed",
                    "origin": "memory_manager" if self.llm.available else "memory_manager_offline",
                }
            )
        return out

    # ------------------------------------------------------------------ 离线
    def _offline(
        self, chapter_id: str, manuscript: vault.Doc, event_sequence: int, reason: str
    ) -> AgentResult:
        notes = vault.extract_section(manuscript.body, "作者备注（不进入正文）") or vault.extract_section(
            manuscript.body, "作者备注"
        )
        # 证据只能来自正文，不能引用作者备注本身
        body_text = vault.extract_section(manuscript.body, "正文") or manuscript.body
        annotations = parse_annotations(notes)
        proposals: list[dict[str, Any]] = []
        conflicts: list[str] = []
        seq = event_sequence

        for index, (kind, value) in enumerate(annotations, start=1):
            parts = _split_pipes(value)
            name, kv, positional = _pairs(parts)
            # 证据关键词：涉及实体的名字 + 标注里的引号原句
            keywords: list[str] = []
            if self.index:
                for doc_id in _ids(value):
                    row = self.index.get_entity(doc_id)
                    if row and row.get("name"):
                        keywords.append(str(row["name"]))
            keywords += re.findall(r"[「“\"]([^」”\"]{2,20})[」”\"]", value)
            if name and len(name) <= 8:
                keywords.append(name)
            evidence = find_evidence(body_text, keywords) if keywords else []
            if not evidence:
                evidence = [f"{chapter_id}（作者备注标注，正文对应句段需人工确认）"]

            if kind == "事件":
                payload = {
                    "name": name,
                    "objective_fact": kv.get("客观事实") or kv.get("事实") or (positional[0] if positional else ""),
                    "participants": _ids(kv.get("参与", "")) or _ids(value),
                    "location_id": (_ids(kv.get("地点", "")) or [None])[0],
                    "results": kv.get("结果", ""),
                    "state_changes": [v for k, v in kv.items() if k in ("状态变化", "变化")],
                    "knowledge": {
                        "knows": _ids(kv.get("知道", "")),
                        "unaware": _ids(kv.get("不知道", "")),
                    },
                }
                proposals.append(
                    {
                        "kind": "event",
                        "op": "create",
                        "target_id": f"EVT-{chapter_id}-{seq:03d}",
                        "payload": payload,
                        "evidence": evidence,
                        "confidence": "CONFIRMED",
                        "approval_class": resolve_approval_class(self.cfg, "event", payload),
                        "note": "事件由作者备注标注后提取",
                    }
                )
                seq += 1

            elif kind == "状态":
                char_ids = _ids(value)
                if not char_ids:
                    conflicts.append(f"状态标注缺少人物 ID：{value}")
                    continue
                char_id = char_ids[0]
                payload = {
                    "location": kv.get("位置", ""),
                    "injury": kv.get("伤势", ""),
                    "objective_status": kv.get("生死", "存活"),
                    "mind": kv.get("精神", ""),
                    "ability": kv.get("能力", ""),
                    "holdings": kv.get("持有物", ""),
                    "awareness": [kv.get("认知", "")] if kv.get("认知") else [],
                    "unknown": [kv.get("未知", "")] if kv.get("未知") else [],
                    "goal": kv.get("目标", ""),
                    "as_of_event": f"EVT-{chapter_id}-{max(seq - 1, 1):03d}",
                }
                proposals.append(
                    {
                        "kind": "character_state",
                        "op": "update",
                        "target_id": f"STATE-{char_id}",
                        "payload": payload,
                        "evidence": evidence,
                        "confidence": "CONFIRMED",
                        "approval_class": resolve_approval_class(self.cfg, "character_state", payload),
                    }
                )

            elif kind == "关系":
                rel_ids = _ids(value)
                payload = {
                    "relation_type": kv.get("性质", ""),
                    "note": kv.get("说明", ""),
                    "strength": int(kv.get("强度", "0") or 0),
                }
                proposals.append(
                    {
                        "kind": "relationship",
                        "op": "update",
                        "target_id": rel_ids[0] if rel_ids else None,
                        "payload": payload,
                        "evidence": evidence,
                        "confidence": "CONFIRMED",
                        "approval_class": resolve_approval_class(self.cfg, "relationship", payload),
                    }
                )

            elif kind == "伏笔":
                fs_ids = _ids(value)
                if not fs_ids:
                    conflicts.append(f"伏笔标注缺少 ID：{value}")
                    continue
                payload = {"status": kv.get("状态", ""), "note": kv.get("说明", "")}
                proposals.append(
                    {
                        "kind": "foreshadowing",
                        "op": "update",
                        "target_id": fs_ids[0],
                        "payload": payload,
                        "evidence": evidence,
                        "confidence": "CONFIRMED",
                        "approval_class": resolve_approval_class(
                            self.cfg,
                            "foreshadow_payoff" if payload["status"] in ("paid_off", "triggered") else "foreshadowing_status",
                            payload,
                        ),
                    }
                )

            elif kind == "摘要":
                payload = {"one_line": name or kv.get("一句话", ""), "facts": [kv.get("要点", "")]}
                proposals.append(
                    {
                        "kind": "summary",
                        "op": "update",
                        "target_id": f"SUMMARY-{chapter_id}",
                        "payload": payload,
                        "evidence": evidence,
                        "confidence": "CONFIRMED",
                        "approval_class": resolve_approval_class(self.cfg, "summary", payload),
                    }
                )

        body = [
            f"# 记忆提取｜{chapter_id}",
            "",
            f"- 运行模式：offline（{reason}）",
            f"- 提案数：{len(proposals)}",
            "",
            "## 事件提案",
            *[
                f"- {p['target_id']}｜{p['payload'].get('name', '')}"
                for p in proposals
                if p["kind"] == "event"
            ],
            "",
            "## 状态变化提案",
            *[
                f"- {p['target_id']}｜{p['payload']}"
                for p in proposals
                if p["kind"] == "character_state"
            ],
            "",
            "## 冲突与待确认",
            *([f"- {c}" for c in conflicts] or ["- 无"]),
            "",
            "> 离线模式只读取正文「作者备注」中的结构化标注（`- 事件: ...` / `- 状态: ...` / "
            "`- 关系: ...` / `- 伏笔: ...` / `- 摘要: ...`），不做语义推断。",
        ]
        return self.make_result(
            status="OK",
            content="\n".join(body),
            mode="offline",
            data={"proposals": self._normalize(proposals, chapter_id), "conflicts": conflicts, "unknowns": []},
            sources=[f"{chapter_id}（作者备注）"],
            note=offline_note(self.cfg, reason),
        )
