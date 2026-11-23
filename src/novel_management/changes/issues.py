"""问题（ISSUE）：审核阶段的产物，带等级与状态。

PRD §14.3 定了三个等级和三个状态，**只有一条硬规矩**：

    BLOCKER 不允许直接 WAIVED 后发布。

这条不是「加个判断」那么简单——豁免 BLOCKER 等于让一个已知的硬伤带着章号进正式版。
所以 `waive()` 在 BLOCKER 上直接抛错，给的是「怎么走」而不是「不行」：

- 修掉它 → `resolve`（需要写明核验依据）；
- 或者先批准对应的规划/设定变更，缩小变更范围，让这条不再成立。

问题存在 runtime 而不是 Vault：它们是审核过程的判词，不是作品内容，进正式区就要
被算进签字基线（签字绑正文 sha256，多一份判词就多一个失效源）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..audit import now_iso
from ..domain.errors import ApprovalError, UsageError
from ..infrastructure.filesystem.storage import write_atomic

BLOCKER = "BLOCKER"
NON_BLOCKING = "NON_BLOCKING"
SUGGESTION = "SUGGESTION"
SEVERITIES = (BLOCKER, NON_BLOCKING, SUGGESTION)

OPEN = "OPEN"
RESOLVED = "RESOLVED"
WAIVED = "WAIVED"
STATUSES = (OPEN, RESOLVED, WAIVED)

CATEGORIES = (
    "KNOWLEDGE_BOUNDARY",   # 人物用了还没获知的信息
    "CONTINUITY",           # 与既有事实冲突
    "TIMELINE",             # 时间线矛盾
    "RULES",                # 世界规则被违反
    "SCOPE",                # 超出创作授权
    "QUALITY",              # 文字/结构
    "OTHER",
)


@dataclass
class Issue:
    issue_id: str
    severity: str
    category: str
    target_id: str
    description: str
    project_id: str = ""
    block_id: str = ""
    status: str = OPEN
    evidence_refs: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    created_at: str = ""
    created_by: str = ""
    closed_at: str = ""
    closure_note: str = ""

    def as_dict(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity,
            "category": self.category,
            "target": {"artifact_id": self.target_id, "block_id": self.block_id},
            "description": self.description,
            "project_id": self.project_id,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
            "suggested_actions": list(self.suggested_actions),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "closed_at": self.closed_at,
            "closure_note": self.closure_note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Issue":
        target = data.get("target") or {}
        return cls(
            issue_id=str(data.get("issue_id") or ""),
            severity=str(data.get("severity") or NON_BLOCKING),
            category=str(data.get("category") or "OTHER"),
            target_id=str(target.get("artifact_id") or data.get("target_id") or ""),
            block_id=str(target.get("block_id") or ""),
            description=str(data.get("description") or ""),
            project_id=str(data.get("project_id") or ""),
            status=str(data.get("status") or OPEN),
            evidence_refs=[str(x) for x in (data.get("evidence_refs") or [])],
            suggested_actions=[str(x) for x in (data.get("suggested_actions") or [])],
            created_at=str(data.get("created_at") or ""),
            created_by=str(data.get("created_by") or ""),
            closed_at=str(data.get("closed_at") or ""),
            closure_note=str(data.get("closure_note") or ""),
        )


class IssueStore:
    def __init__(self, runtime: Path):
        self.dir = Path(runtime) / "issues"

    def _path(self, issue_id: str) -> Path:
        return self.dir / f"{issue_id}.json"

    def next_id(self) -> str:
        highest = 0
        if self.dir.is_dir():
            for path in self.dir.glob("ISSUE-*.json"):
                try:
                    highest = max(highest, int(path.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
        return f"ISSUE-{highest + 1:06d}"

    def create(
        self,
        *,
        severity: str,
        category: str,
        target_id: str,
        description: str,
        project_id: str = "",
        block_id: str = "",
        evidence_refs: list[str] | None = None,
        suggested_actions: list[str] | None = None,
        actor: str = "",
    ) -> Issue:
        severity = severity.upper()
        category = category.upper()
        if severity not in SEVERITIES:
            raise UsageError(f"severity 只能是 {'/'.join(SEVERITIES)}，收到：{severity}")
        if category not in CATEGORIES:
            raise UsageError(f"未知 category：{category}（可选 {', '.join(CATEGORIES)}）")
        if not target_id:
            raise UsageError("问题必须指向一个对象（--target）")
        if not description.strip():
            raise UsageError("问题必须有描述（--description）——空描述没法判断该不该阻断")

        issue = Issue(
            issue_id=self.next_id(),
            severity=severity,
            category=category,
            target_id=target_id,
            block_id=block_id,
            description=description.strip(),
            project_id=project_id,
            evidence_refs=list(evidence_refs or []),
            suggested_actions=list(suggested_actions or []),
            created_at=now_iso(),
            created_by=actor,
        )
        self._save(issue)
        return issue

    def _save(self, issue: Issue) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(issue.as_dict(), ensure_ascii=False, indent=2)
        write_atomic(self._path(issue.issue_id), payload.encode("utf-8"))

    def load(self, issue_id: str) -> Issue:
        path = self._path(issue_id)
        if not path.exists():
            raise UsageError(f"问题不存在：{issue_id}")
        return Issue.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def all(self, *, project_id: str = "") -> list[Issue]:
        if not self.dir.is_dir():
            return []
        out: list[Issue] = []
        for path in sorted(self.dir.glob("ISSUE-*.json")):
            try:
                issue = Issue.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
            if project_id and issue.project_id and issue.project_id != project_id:
                continue
            out.append(issue)
        return out

    def open_blockers(self, target_ids: set[str], *, project_id: str = "") -> list[Issue]:
        """指向这批对象的、仍 OPEN 的 BLOCKER。

        `target_ids` 同时收任务 ID 与产物 ID——同一份问题可能记在任务上（还没发布）
        也可能记在产物上（已发布后发现），只认一种就会漏。
        """
        return [
            issue
            for issue in self.all(project_id=project_id)
            if issue.status == OPEN
            and issue.severity == BLOCKER
            and (not project_id or not issue.project_id or issue.project_id == project_id)
            and issue.target_id in target_ids
        ]

    def resolve(self, issue_id: str, *, note: str, actor: str = "") -> Issue:
        issue = self.load(issue_id)
        if issue.status == RESOLVED:
            raise UsageError(f"问题 {issue_id} 已经是 RESOLVED")
        if issue.severity == BLOCKER and not note.strip():
            raise UsageError("关闭 BLOCKER 必须写明核验依据（--note）：凭什么说它不再构成阻断")
        issue.status = RESOLVED
        issue.closed_at = now_iso()
        issue.closure_note = f"{note.strip()}（{actor or '未知'}）"
        self._save(issue)
        return issue

    def waive(self, issue_id: str, *, note: str, actor: str = "") -> Issue:
        """豁免。**BLOCKER 一律拒绝**，这是 PRD §14.3 的硬线。"""
        issue = self.load(issue_id)
        if issue.severity == BLOCKER:
            raise ApprovalError(
                f"{issue_id} 是 BLOCKER，不能豁免。"
                "两条合法路径：① 修掉后 `issue resolve`（写明核验依据）；"
                "② 先批准对应的规划/设定变更、缩小变更范围，让它不再成立。"
            )
        if issue.status != OPEN:
            raise UsageError(f"问题 {issue_id} 当前是 {issue.status}，不能豁免")
        issue.status = WAIVED
        issue.closed_at = now_iso()
        issue.closure_note = f"豁免：{note.strip() or '未写理由'}（{actor or '未知'}）"
        self._save(issue)
        return issue
