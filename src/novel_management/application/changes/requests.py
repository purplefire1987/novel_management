"""变更请求（CR）：五类变更的登记、影响报告与独立审批。

## 为什么 RETCON 要单独一条审批路径

其余四类变更（`PLAN_CHANGE` / `STATE_CHANGE` / `CORRECTION` / `EDITORIAL`）
改的是「接下来怎么写」或「录错了改回来」。RETCON 改的是**此前已经成立的事实**——
它会让已经发表出去的正文变成错的。作者点一次「批准这份档案」不能替代这个决定，
所以：

1. 必须先有影响报告（`impact`），报告内容是审批对象；
2. 批准绑的是**影响报告的哈希**，不是文件哈希——改一个字节报告就失效；
3. 报告里列出受影响正文而作者既没承诺改写、也没明确缩小范围时，**拒绝批准**。
   PRD §19.3：「不能宣称 RETCON 完成」。

## 状态机

    DRAFT ──impact──> IMPACTED ──approve──> APPROVED
                          │
                          └──reject──> REJECTED

没有影响报告就批准，等于闭着眼签字，`approve()` 直接拒。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from novel_management.audit import now_iso
from novel_management.domain.canonical import package_hash
from novel_management.domain.errors import ApprovalError, UsageError
from novel_management.application.staging import artifact_id_of
from novel_management.infrastructure.filesystem.storage import write_atomic

PLAN_CHANGE = "PLAN_CHANGE"
STATE_CHANGE = "STATE_CHANGE"
CORRECTION = "CORRECTION"
RETCON = "RETCON"
EDITORIAL = "EDITORIAL"
TYPES = (PLAN_CHANGE, STATE_CHANGE, CORRECTION, RETCON, EDITORIAL)

DRAFT = "DRAFT"
IMPACTED = "IMPACTED"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

# 默认风险：死亡、复活、核心规则变更、重大身份揭露、RETCON 默认 HIGH（PRD §14.2）。
# 这里只落「哪些类型必须走独立审批」，具体风险评分留给 T09 的风险路由。
INDEPENDENT_APPROVAL = frozenset({RETCON})


@dataclass
class ChangeRequest:
    change_id: str
    type: str
    reason: str
    project_id: str = ""
    targets: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)
    old_record: str = ""
    new_record: str = ""
    migration_plan: str = ""
    release_strategy: str = ""
    required_approvals: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    status: str = DRAFT
    impact: dict = field(default_factory=dict)
    impact_hash: str = ""
    scope_narrowed: bool = False
    approved_at: str = ""
    approved_by: str = ""
    approval_id: str = ""
    created_at: str = ""
    created_by: str = ""

    @property
    def independent(self) -> bool:
        return self.type in INDEPENDENT_APPROVAL

    def scope_hits(self, targets: list[str]) -> bool:
        """这笔变更是否覆盖给定路径。

        两种写法都要认：`scope` 里写路径前缀（`01_Canon/Characters`），
        `targets` 里写对象 ID（`CHAR-0001`）。作者两种都会用，只认一种就会漏。
        """
        for raw in self.scope:
            scope = str(raw).strip().rstrip("/")
            if not scope:
                continue
            for target in targets:
                if target == scope or target.startswith(scope + "/"):
                    return True
        wanted = {str(x) for x in self.targets if str(x).strip()}
        if wanted:
            for target in targets:
                if artifact_id_of(target) in wanted:
                    return True
        return False

    def as_dict(self) -> dict:
        return {
            "change_id": self.change_id,
            "type": self.type,
            "reason": self.reason,
            "project_id": self.project_id,
            "targets": list(self.targets),
            "scope": list(self.scope),
            "old_record": self.old_record,
            "new_record": self.new_record,
            "migration_plan": self.migration_plan,
            "release_strategy": self.release_strategy,
            "required_approvals": list(self.required_approvals),
            "dependencies": list(self.dependencies),
            "status": self.status,
            "impact": self.impact,
            "impact_hash": self.impact_hash,
            "scope_narrowed": self.scope_narrowed,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "approval_id": self.approval_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChangeRequest":
        return cls(
            change_id=str(data.get("change_id") or ""),
            type=str(data.get("type") or ""),
            reason=str(data.get("reason") or ""),
            project_id=str(data.get("project_id") or ""),
            targets=[str(x) for x in (data.get("targets") or [])],
            scope=[str(x) for x in (data.get("scope") or [])],
            old_record=str(data.get("old_record") or ""),
            new_record=str(data.get("new_record") or ""),
            migration_plan=str(data.get("migration_plan") or ""),
            release_strategy=str(data.get("release_strategy") or ""),
            required_approvals=[str(x) for x in (data.get("required_approvals") or [])],
            dependencies=[str(x) for x in (data.get("dependencies") or [])],
            status=str(data.get("status") or DRAFT),
            impact=dict(data.get("impact") or {}),
            impact_hash=str(data.get("impact_hash") or ""),
            scope_narrowed=bool(data.get("scope_narrowed")),
            approved_at=str(data.get("approved_at") or ""),
            approved_by=str(data.get("approved_by") or ""),
            approval_id=str(data.get("approval_id") or ""),
            created_at=str(data.get("created_at") or ""),
            created_by=str(data.get("created_by") or ""),
        )


class ChangeStore:
    def __init__(self, runtime: Path):
        self.dir = Path(runtime) / "changes"

    def _path(self, change_id: str) -> Path:
        return self.dir / f"{change_id}.json"

    def next_id(self) -> str:
        highest = 0
        if self.dir.is_dir():
            for path in self.dir.glob("CR-*.json"):
                try:
                    highest = max(highest, int(path.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
        return f"CR-{highest + 1:06d}"

    def save(self, change: ChangeRequest) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(change.as_dict(), ensure_ascii=False, indent=2)
        write_atomic(self._path(change.change_id), payload.encode("utf-8"))

    def load(self, change_id: str) -> ChangeRequest:
        path = self._path(change_id)
        if not path.exists():
            raise UsageError(f"变更请求不存在：{change_id}")
        return ChangeRequest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def all(self, *, project_id: str = "") -> list[ChangeRequest]:
        if not self.dir.is_dir():
            return []
        out: list[ChangeRequest] = []
        for path in sorted(self.dir.glob("CR-*.json")):
            try:
                change = ChangeRequest.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
            if project_id and change.project_id and change.project_id != project_id:
                continue
            out.append(change)
        return out

    # ---- 生命周期 ----
    def create(
        self,
        *,
        type: str,
        reason: str,
        project_id: str = "",
        targets: list[str] | None = None,
        scope: list[str] | None = None,
        old_record: str = "",
        new_record: str = "",
        migration_plan: str = "",
        release_strategy: str = "",
        actor: str = "",
    ) -> ChangeRequest:
        type = type.upper()
        if type not in TYPES:
            raise UsageError(f"变更类型只能是 {'/'.join(TYPES)}，收到：{type}")
        if not reason.strip():
            raise UsageError("变更必须有原因（--reason）——没有原因的变更无法判断该不该批")
        if not targets and not scope:
            raise UsageError("变更必须说明影响对象（--target 或 --scope），否则影响分析无从下手")

        change = ChangeRequest(
            change_id=self.next_id(),
            type=type,
            reason=reason.strip(),
            project_id=project_id,
            targets=list(targets or []),
            scope=list(scope or []),
            old_record=old_record,
            new_record=new_record,
            migration_plan=migration_plan,
            release_strategy=release_strategy,
            required_approvals=["AUTHOR_INDEPENDENT"] if type in INDEPENDENT_APPROVAL else ["AUTHOR"],
            created_at=now_iso(),
            created_by=actor,
        )
        self.save(change)
        return change

    def attach_impact(self, change: ChangeRequest, report: dict) -> ChangeRequest:
        """把影响报告挂到变更上，并记下报告哈希——审的就是这份报告。"""
        change.impact = report
        change.impact_hash = package_hash(report)
        change.status = IMPACTED
        self.save(change)
        return change

    def approve(
        self,
        change: ChangeRequest,
        *,
        impact_hash: str,
        actor: str,
        note: str = "",
        allow_rewrite: bool = False,
        narrow_scope: bool = False,
    ) -> ChangeRequest:
        if change.status not in (IMPACTED,):
            raise ApprovalError(
                f"{change.change_id} 当前状态是 {change.status}，必须先跑 `novel impact {change.change_id}`"
                "生成影响报告再批准"
            )
        if not impact_hash:
            raise ApprovalError("批准变更必须显式给出 --hash（影响报告的哈希），不接受「大致同意」")
        if impact_hash != change.impact_hash:
            raise ApprovalError(
                f"哈希不符：手里的 {impact_hash[:12]}… ≠ 当前报告 {change.impact_hash[:12]}…。"
                "影响报告已被重新生成，请重新查看后再批准"
            )
        chapters = list(change.impact.get("affected_chapters") or [])
        if change.independent and chapters and not (allow_rewrite or narrow_scope):
            raise ApprovalError(
                "影响报告里有 "
                f"{len(chapters)} 章正文会因此变成错的：{', '.join(chapters[:5])}"
                f"{' 等' if len(chapters) > 5 else ''}。"
                "只改档案不处理正文时不能宣称 RETCON 完成（PRD §19.3）。"
                "要么承诺改写（--rewrite-chapters），要么明确缩小变更范围（--narrow-scope）"
            )
        change.status = APPROVED
        change.approved_at = now_iso()
        change.approved_by = actor
        change.scope_narrowed = bool(narrow_scope)
        if narrow_scope:
            change.release_strategy = change.release_strategy or "缩小范围：仅改档案，受影响正文挂起"
        self.save(change)
        return change

    def reject(self, change: ChangeRequest, *, actor: str, note: str = "") -> ChangeRequest:
        change.status = REJECTED
        change.approved_at = now_iso()
        change.approved_by = actor
        change.release_strategy = note or change.release_strategy
        self.save(change)
        return change

    def blocking_scope(self, targets: list[str], *, project_id: str = "") -> list[ChangeRequest]:
        """这批目标上还没批准的 RETCON。

        用于拦「先改档案、后补变更」：只要有一笔未批准的 RETCON 覆盖这个路径，
        普通文件审批就得停下——否则 RETCON 的独立审批就成为可选装饰。

        **scope 按前缀匹配**。用集合精确相交看着更严谨，实际是空的：
        `--scope 01_Canon/Characters` 是作者写目录的常态，永远不等于
        `01_Canon/Characters/CHAR-0001.md`，于是这道闸一次都不会触发——
        「有检查」和「检查生效」是两件事。
        """
        blocking: list[ChangeRequest] = []
        for change in self.all(project_id=project_id):
            if not change.independent or change.status == APPROVED:
                continue
            if change.status == REJECTED:
                continue
            if change.scope_hits(targets):
                blocking.append(change)
        return blocking
