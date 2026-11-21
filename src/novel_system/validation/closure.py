"""共同发布闸门：不许出现「正文生效了、记忆还是上一版」。

T10 第三条验收要消灭的正是这个中间态。它不会自己消失——只要允许「先发正文，
回头再补记忆」，就一定会有那么一段时间，索引里的状态是旧的、正文是新的，
而所有 `state` / `knowledge` 查询都基于旧记忆给出**看起来正确**的答案。

所以发布前问一句，每一条要发的正文都要答得上来：

- 有记忆提案，且已批准，且源哈希没变 → 过；
- 作者显式声明「本章不产记忆」并写了理由 → 过；
- 其余（没提案 / 提案未批 / 提案已失效）→ 阻断。

**「没有提案」与「声明不产记忆」是两件事。** 前者是漏做，后者是决定。
把两者混为一谈，闸门就会退化成「没做也能过」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..errors import UsageError
from ..paths import ProjectPaths
from ..staging import StagingArea
from ..tasks import TaskStore
from . import dependencies as dep_mod
from . import proposals as prop_mod

BLOCKER = "BLOCKER"
WARNING = "WARNING"


@dataclass
class ClosureIssue:
    chapter_id: str
    code: str
    message: str
    blocking: bool = True

    def as_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
        }


@dataclass
class ClosureResult:
    chapter_ids: list[str]
    issues: list[ClosureIssue] = field(default_factory=list)
    checked: list[dict] = field(default_factory=list)

    @property
    def blocking(self) -> list[ClosureIssue]:
        return [item for item in self.issues if item.blocking]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict:
        return {
            "chapter_ids": list(self.chapter_ids),
            "ok": self.ok,
            "issues": [item.as_dict() for item in self.issues],
            "checked": list(self.checked),
        }


def chapters_of_task(pp: ProjectPaths, runtime: Path, task_id: str) -> list[str]:
    """这个任务的 staging 里有哪些章节的正文。

    前缀从 layout 取，不写字面量：`release/engine.py` 判定「这一批含不含正文」
    用的是 `pp.manuscripts`，这里若写死 `03_Manuscripts/`，改过目录名的库会出现
    「engine 认为有正文、closure 说没有」的分裂。
    """
    task = TaskStore(runtime).load(task_id)
    staging = StagingArea(pp, task)
    prefix = pp.manuscripts.relative_to(pp.root).as_posix() + "/"
    found: set[str] = set()
    for item in staging.files():
        if not item.target.startswith(prefix):
            continue
        name = item.target.rsplit("/", 1)[-1]
        if name.endswith(".md"):
            found.add(name[:-3])
    return sorted(found)


def check(
    pp: ProjectPaths,
    runtime: Path,
    *,
    chapter_ids: list[str] | None = None,
    task_id: str = "",
    proposal_store: prop_mod.ProposalStore | None = None,
    check_dependencies: bool = True,
) -> ClosureResult:
    """发布前问：每一条正文的记忆都说清楚了吗。"""
    if not chapter_ids:
        if not task_id:
            raise UsageError("closure.check 需要 chapter_ids 或 task_id")
        chapter_ids = chapters_of_task(pp, runtime, task_id)
    if not chapter_ids:
        raise UsageError("这一批里没有正文（03_Manuscripts/ 下没有文件），没有可检查的共同发布")

    store = proposal_store or prop_mod.ProposalStore(runtime)
    result = ClosureResult(chapter_ids=list(chapter_ids))

    for chapter_id in chapter_ids:
        proposal = store.for_chapter(chapter_id)
        if proposal is None:
            result.issues.append(
                ClosureIssue(
                    chapter_id,
                    "NO_MEMORY_DECISION",
                    f"{chapter_id} 既没有记忆提案，也没有「本章不产记忆」的声明。"
                    f"允许不产记忆，但不允许没人说过要不要——"
                    f"`novel proposal extract --task {task_id or '<任务ID>'} "
                    f"--chapter {chapter_id}` 或 "
                    f"`novel proposal declare-none --chapter {chapter_id} --reason ...`",
                )
            )
            continue

        if proposal.no_memory:
            if not proposal.no_memory_reason:
                result.issues.append(
                    ClosureIssue(
                        chapter_id,
                        "NO_MEMORY_NO_REASON",
                        f"{chapter_id} 声明了不产记忆，但没写理由；" "没有理由的声明等于跳过检查",
                    )
                )
                continue
            result.checked.append(
                {
                    "chapter_id": chapter_id,
                    "proposal_id": proposal.proposal_id,
                    "mode": "NO_MEMORY",
                    "reason": proposal.no_memory_reason,
                }
            )
            continue

        verdict = prop_mod.verify(proposal, pp, runtime)
        if verdict.status == prop_mod.STATUS_STALE:
            result.issues.append(
                ClosureIssue(
                    chapter_id,
                    "PROPOSAL_STALE",
                    f"{chapter_id} 的记忆提案 {proposal.proposal_id} 已失效（{verdict.reason}）。"
                    "正文与提案必须同批：重新提取，或把正文改回去",
                )
            )
            continue
        if proposal.status != prop_mod.STATUS_APPROVED:
            result.issues.append(
                ClosureIssue(
                    chapter_id,
                    "PROPOSAL_NOT_APPROVED",
                    f"{chapter_id} 的记忆提案 {proposal.proposal_id} 还是 {proposal.status}。"
                    f"先 `novel proposal approve {proposal.proposal_id} --as author`",
                )
            )
            continue

        result.checked.append(
            {
                "chapter_id": chapter_id,
                "proposal_id": proposal.proposal_id,
                "mode": "PROPOSAL",
                "items": len(proposal.items),
                "source_sha256": proposal.source_sha256,
            }
        )

    # 依赖失效：不阻断发布（写新章本来就会让下游显 STALE），但必须报出来——
    # 否则「发完了，下游 20 份档案都该复核」这件事没人知道。
    if check_dependencies:
        for state in dep_mod.check(pp):
            if state.status == dep_mod.VALID:
                continue
            result.issues.append(
                ClosureIssue(
                    state.artifact_id,
                    "DEPENDENCY_" + state.status,
                    f"{state.path} 依赖已变化（{state.status}）：" + "；".join(state.reasons[:3]),
                    blocking=False,
                )
            )

    return result


__all__ = ["BLOCKER", "ClosureIssue", "ClosureResult", "WARNING", "chapters_of_task", "check"]
