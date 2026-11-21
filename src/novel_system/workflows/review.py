"""审核：风险路由、返修预算、以及「谁不能审谁」。

T09 后两条验收落在这里：

- 「高风险不能仅提案者自审」——`submit()` 会比对审核角色与草稿角色，
  高风险时同一个角色审自己的稿直接拒，没有 `--force`；
- 「超过返修预算停止」——每份 REVISE 都算一轮，轮次用完就不再接受审核，
  任务转阻塞，等作者裁定。

## 风险只有一个方向：只能升，不能降

PRD §14.2：确定性标签可以提高风险；模型只能建议提高，不得私自降低；
作者可以调整，但必须留档。

所以 `assess()` 分两层：

1. `deterministic_risk()`：死亡、复活、核心规则变更、身份揭露、RETCON
   这几类命中就至少 HIGH。关键词表在下面，命中位置一并报出来——
   作者要能看到系统凭什么判它 HIGH。
2. 模型建议只能往上抬：`raise_to` 只接受更高的等级，降级请求被忽略并留一条说明。

## 机器能判什么，判不了什么

规则检查只看**形状**：正文为空、残留系统注释、混入多份文档、出现禁止条目
里被引号括起来的专名。**语义级的检查（人物用了还没获得的信息）机器判不了**，
那种活归专项角色与作者。这里不假装能判——判不了就说判不了，
比给一个「看起来查过了」的绿灯危险得多。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..audit import now_iso
from ..config import read_yaml
from ..errors import ConflictError, SecurityError, UsageError
from ..paths import ProjectPaths
from ..staging import StagingArea
from ..storage import dump_yaml, sha256_text, write_text_atomic
from ..tasks import TaskStore

REVIEW_DIR = "reviews"
DEFAULT_MAX_ROUNDS = 3

RISK_ORDER = ("LOW", "MEDIUM", "HIGH")

# 确定性风险标签。命中即「至少 HIGH」——这些都是改不动口的设定级变更，
# 让模型或人「觉得还好」就把等级降下去，等于把安全阀交给被检查的人。
HIGH_TRIGGERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # 「死」单字最常见，也最容易误伤：「死路」「死寂」「死守」都不是死亡。
    # 单字命中后还要过 `_DEATH_EXCLUDE` 这一关，见 `_death_hits()`。
    ("DEATH", "有人死亡", ("死亡", "身亡", "丧命", "殒命", "断气", "咽气", "尸体", "暴毙", "死")),
    ("RESURRECTION", "复活", ("复活", "复生", "死而复生", "起死回生")),
    ("RULE_CHANGE", "核心规则变更", ("规则变更", "新增规则", "改写规则", "能力规则")),
    ("IDENTITY_REVEAL", "重大身份揭露", ("身份揭露", "暴露身份", "真实身份", "真正的身份")),
    ("RETCON", "既有事实改写", ("RETCON", "改写历史", "推翻既有", "推翻前文")),
)

# 各风险等级必须由哪些角色过一遍（PRD §14.2）。
# A5=编辑、A6=世界架构师、A9=总编。
REQUIRED_REVIEWERS = {
    "LOW": ("editor",),
    "MEDIUM": ("editor", "world_architect"),
    "HIGH": ("editor", "world_architect", "editor_in_chief"),
}

VERDICTS = ("APPROVE", "REVISE")

# 导出的正文里出现这些，说明系统注释混进了正文（PRD §14.1 明令禁止）。
_COMMENT_MARKERS = ("<!--", "-->", "TODO", "[待补]", "【待补】", "（系统注释）")


@dataclass
class Finding:
    severity: str
    category: str
    message: str
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "evidence": list(self.evidence),
        }


@dataclass
class Review:
    review_id: str
    task_id: str
    chapter_id: str
    round: int
    reviewer: str
    reviewer_role: str
    risk_level: str
    verdict: str
    source_sha256: str
    max_rounds: int = DEFAULT_MAX_ROUNDS
    findings: list[Finding] = field(default_factory=list)
    risk_basis: list[dict] = field(default_factory=list)
    note: str = ""
    created_at: str = ""

    def as_dict(self) -> dict:
        return {
            "review_id": self.review_id,
            "task_id": self.task_id,
            "chapter_id": self.chapter_id,
            "round": self.round,
            "reviewer": self.reviewer,
            "reviewer_role": self.reviewer_role,
            "risk_level": self.risk_level,
            "verdict": self.verdict,
            "source_sha256": self.source_sha256,
            "max_rounds": self.max_rounds,
            "findings": [item.as_dict() for item in self.findings],
            "risk_basis": list(self.risk_basis),
            "note": self.note,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Review":
        findings = [
            Finding(
                severity=str(item.get("severity") or "NON_BLOCKING"),
                category=str(item.get("category") or "OTHER"),
                message=str(item.get("message") or ""),
                evidence=[str(x) for x in item.get("evidence") or []],
            )
            for item in data.get("findings") or []
            if isinstance(item, dict)
        ]
        return cls(
            review_id=str(data.get("review_id") or ""),
            task_id=str(data.get("task_id") or ""),
            chapter_id=str(data.get("chapter_id") or ""),
            round=int(data.get("round") or 0),
            reviewer=str(data.get("reviewer") or ""),
            reviewer_role=str(data.get("reviewer_role") or ""),
            risk_level=str(data.get("risk_level") or "LOW"),
            verdict=str(data.get("verdict") or "REVISE"),
            source_sha256=str(data.get("source_sha256") or ""),
            max_rounds=int(data.get("max_rounds") or DEFAULT_MAX_ROUNDS),
            findings=findings,
            risk_basis=[item for item in data.get("risk_basis") or [] if isinstance(item, dict)],
            note=str(data.get("note") or ""),
            created_at=str(data.get("created_at") or ""),
        )


class ReviewStore:
    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)
        self.root = self.runtime / REVIEW_DIR

    def path_of(self, review_id: str) -> Path:
        return self.root / f"{review_id}.yaml"

    def next_id(self) -> str:
        best = 0
        if self.root.exists():
            for path in self.root.glob("REVIEW-*.yaml"):
                found = re.search(r"REVIEW-([0-9]+)", path.stem)
                if found:
                    best = max(best, int(found.group(1)))
        return f"REVIEW-{best + 1:04d}"

    def save(self, review: Review) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if not review.review_id:
            review.review_id = self.next_id()
        if not review.created_at:
            review.created_at = now_iso()
        path = self.path_of(review.review_id)
        write_text_atomic(path, dump_yaml(review.as_dict()))
        return path

    def load(self, review_id: str) -> Review:
        path = self.path_of(review_id)
        if not path.exists():
            raise NotFoundError(f"没有这份审核记录：{review_id}")
        return Review.from_dict(read_yaml(path))

    def list(self, *, task_id: str = "", chapter_id: str = "") -> list[Review]:
        if not self.root.exists():
            return []
        out: list[Review] = []
        for path in sorted(self.root.glob("REVIEW-*.yaml")):
            try:
                review = Review.from_dict(read_yaml(path))
            except Exception:
                continue
            if task_id and review.task_id != task_id:
                continue
            if chapter_id and review.chapter_id != chapter_id:
                continue
            out.append(review)
        return out


# ---------------------------------------------------------------------------
# 风险
# ---------------------------------------------------------------------------
# 「死」后面跟这些字时不是死亡：死路、死角、死寂、死守、死心、死板……
# 宁可漏判（人工 `--risk HIGH` 能补），也不要让「死寂的院子」把每一章都抬成 HIGH——
# 那样 HIGH 会多到没人看，等于没有风险路由。
_DEATH_EXCLUDE = ("路", "角", "寂", "守", "心", "板", "锁", "账", "扛", "磕", "缠", "党", "规矩")


def _death_hits(text: str) -> list[int]:
    """返回「死」按死亡语义出现的下标。"""
    hits: list[int] = []
    start = 0
    while True:
        index = text.find("死", start)
        if index < 0:
            break
        follow = text[index + 1 : index + 2]
        if follow not in _DEATH_EXCLUDE:
            hits.append(index)
        start = index + 1
    return hits


def deterministic_risk(text: str) -> tuple[str, list[dict]]:
    """按确定性标签判风险。命中一条就是 HIGH。"""
    basis: list[dict] = []
    for code, label, keywords in HIGH_TRIGGERS:
        for keyword in keywords:
            if keyword == "死":
                # 单字走语义过滤：文本里有没有「死得不平凡」的位置
                hits = _death_hits(text)
                if not hits:
                    continue
                index = hits[0]
            else:
                index = text.find(keyword)
                if index < 0:
                    continue
            basis.append(
                {
                    "code": code,
                    "label": label,
                    "keyword": keyword,
                    "context": text[max(0, index - 20) : index + len(keyword) + 20].replace("\n", " "),
                }
            )
            break
    return ("HIGH" if basis else "LOW"), basis


def _body_of(text: str, path: Path) -> str:
    """取正文。解析失败时退回整份文本，并在调用方看来仍是「可检查」的。"""
    from .. import markdown as md

    try:
        return md.parse(text, str(path)).body
    except Exception:
        return text


def raise_to(current: str, wanted: str) -> tuple[str, str]:
    """风险只能往上抬。返回 (最终等级, 说明)。"""
    current = current if current in RISK_ORDER else "LOW"
    wanted = (wanted or "").upper()
    if not wanted:
        return current, ""
    if wanted not in RISK_ORDER:
        raise UsageError(f"风险等级只能是 {'/'.join(RISK_ORDER)}，收到 {wanted!r}")
    if RISK_ORDER.index(wanted) < RISK_ORDER.index(current):
        return current, f"请求降到 {wanted} 被忽略：风险只能升不能降（当前 {current}）"
    return wanted, ""


def required_reviewers(risk: str) -> tuple[str, ...]:
    return REQUIRED_REVIEWERS.get(risk, REQUIRED_REVIEWERS["LOW"])


# ---------------------------------------------------------------------------
# 规则检查
# ---------------------------------------------------------------------------
def evaluate(text: str, *, chapter_id: str, must_not: list[str]) -> list[Finding]:
    """只看形状与可定位的字面。语义检查不在这里假装。"""
    findings: list[Finding] = []
    body = text.strip()

    if not body:
        findings.append(Finding("BLOCKER", "QUALITY", "正文为空：没有内容可审"))
        return findings

    words = len(re.sub(r"\s", "", body))
    if words < 200:
        findings.append(
            Finding(
                "NON_BLOCKING",
                "QUALITY",
                f"正文只有 {words} 字，短于常见章节；确认不是被截断的输出",
            )
        )

    for marker in _COMMENT_MARKERS:
        if marker in body:
            findings.append(
                Finding(
                    "BLOCKER",
                    "QUALITY",
                    f"正文里出现系统注释标记 {marker!r}：注释会随导出进入读者视野（PRD §14.1）",
                    [marker],
                )
            )

    # 正文里再出现一次 frontmatter 分隔，说明模型把两份文档拼在一起了。
    if body.count("\n---\n") >= 1:
        findings.append(
            Finding("BLOCKER", "QUALITY", "正文里出现 `---` 分隔线：疑似把两份文档拼在了一起")
        )

    for item in must_not:
        for term in _quoted_terms(item):
            if term and term in body:
                findings.append(
                    Finding(
                        "NON_BLOCKING",
                        "SCOPE",
                        f"禁止条目里的专名 {term!r} 出现在正文中，请人工确认是否越界",
                        [item],
                    )
                )

    if not findings:
        findings.append(
            Finding(
                "SUGGESTION",
                "OTHER",
                "机器只查了形状（空正文／系统注释／拼文档／禁令专名）。"
                "人物认知边界、时间线一致性这类语义问题机器判不了，需要专项角色与作者过目",
            )
        )
    return findings


_QUOTE_RE = re.compile(r"[《「『“\"]([^》」』”\"]{2,20})[》」』”\"]")


def _quoted_terms(text: str) -> list[str]:
    return [match.group(1).strip() for match in _QUOTE_RE.finditer(text or "") if match.group(1).strip()]


# ---------------------------------------------------------------------------
# 提交审核
# ---------------------------------------------------------------------------
@dataclass
class SubmitResult:
    review: Review
    path: Path
    next_action: str
    warnings: list[str] = field(default_factory=list)


def draft_of(pp: ProjectPaths, runtime: Path, task_id: str, chapter_id: str) -> tuple[Path, str, bytes]:
    """从 staging 取这一章的草稿。返回 (staging 文件路径, 目标相对路径, 字节)。"""
    task = TaskStore(runtime).load(task_id)
    staging = StagingArea(pp, task)
    matches = [
        item
        for item in staging.files()
        if item.target.endswith(f"{chapter_id}.md") and item.target.startswith("03_Manuscripts/")
    ]
    if not matches:
        raise UsageError(
            f"任务 {task_id} 的 staging 里没有 {chapter_id} 的草稿。先跑 `novel write --task {task_id} --chapter {chapter_id}`"
        )
    if len(matches) > 1:
        names = "、".join(item.target for item in matches)
        raise ConflictError(f"staging 里有 {len(matches)} 份 {chapter_id} 草稿：{names}；先确认哪一份是当前稿")
    item = matches[0]
    path = pp.vault / item.staged
    if not path.exists():
        raise ConflictError(f"staging 文件丢失：{item.staged}")
    return path, item.target, path.read_bytes()


def submit(
    pp: ProjectPaths,
    runtime: Path,
    *,
    task_id: str,
    chapter_id: str,
    reviewer: str,
    reviewer_role: str,
    verdict: str,
    risk: str = "",
    note: str = "",
    must_not: list[str] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    author_role: str = "writer",
    extra_reviewers: tuple[str, ...] = (),
) -> SubmitResult:
    """登记一次审核。

    顺序很重要：先判「还有没有返修预算」，再判「你有没有资格审」，
    最后才写记录。顺序反了的话，预算耗尽的稿子仍会被写进一条 REVISE 记录，
    看起来像「还有人在正常推进」。
    """
    verdict = (verdict or "").upper()
    if verdict not in VERDICTS:
        raise UsageError(f"结论只能是 {'/'.join(VERDICTS)}，收到 {verdict!r}")

    store = ReviewStore(runtime)
    history = store.list(task_id=task_id, chapter_id=chapter_id)

    # ---- 返修预算 ----
    rounds_used = len({item.round for item in history if item.verdict == "REVISE"})
    if rounds_used >= max_rounds:
        raise ConflictError(
            f"{chapter_id} 已经返修 {rounds_used} 轮，达到预算上限 {max_rounds}。"
            "不再接受新一轮审核——继续改下去只会让稿子离规划越来越远。\n"
            "  要么作者裁定接受现状（novel approve text），要么改规划重开任务"
        )

    # ---- 资格 ----
    if reviewer_role == author_role and verdict == "APPROVE":
        raise SecurityError(
            f"{reviewer_role} 不能给自己写的稿下通过结论（PRD §14.2：高风险提案不得仅由提案角色自审）。"
            "让另一个角色审，或把结论改成 REVISE"
        )

    path, target, data = draft_of(pp, runtime, task_id, chapter_id)
    text = data.decode("utf-8", errors="replace")
    # 哈希绑**整份字节**（批准也绑这个）；规则检查只看**正文**。
    # 两者混用会出两种错：拿正文哈希去对批准记录永远对不上；
    # 或者把 frontmatter 的 `---` 当成「正文里混了两份文档」。
    digest = sha256_text(text)
    body = _body_of(text, path)

    # ---- 风险 ----
    level, basis = deterministic_risk(body)
    wanted_level, warning = raise_to(level, risk)
    warnings = [warning] if warning else []
    if wanted_level == "HIGH" and not basis:
        basis = [{"code": "MANUAL", "label": "人工抬高", "keyword": "", "context": note or "审核人抬高"}]

    if wanted_level == "HIGH":
        required = required_reviewers("HIGH")
        done = {item.reviewer_role for item in history if item.verdict == "APPROVE"}
        done.add(reviewer_role)
        missing = [role for role in required if role not in done and role not in extra_reviewers]
        if verdict == "APPROVE" and missing:
            warnings.append(
                "高风险还缺这些角色过目：" + "、".join(missing)
                + "。这一次 APPROVE 不构成「已通过」，approve text 会继续拦"
            )

    # ---- 结论 ----
    findings = evaluate(body, chapter_id=chapter_id, must_not=list(must_not or []))
    round_no = max([item.round for item in history], default=0) + 1

    review = Review(
        review_id=store.next_id(),
        task_id=task_id,
        chapter_id=chapter_id,
        round=round_no,
        reviewer=reviewer,
        reviewer_role=reviewer_role,
        risk_level=wanted_level,
        verdict=verdict,
        source_sha256=digest,
        max_rounds=max_rounds,
        findings=findings,
        risk_basis=basis,
        note=note,
    )
    saved = store.save(review)

    blockers = [item for item in findings if item.severity == "BLOCKER"]
    if verdict == "REVISE":
        next_action = f"改稿后重跑 novel write --task {task_id} --chapter {chapter_id} --replace"
    elif blockers:
        next_action = "先处理 BLOCKER 再提交审核；有 BLOCKER 时正文批准会被拒"
    else:
        next_action = f"novel approve text --task {task_id} --chapter {chapter_id} --as author"

    return SubmitResult(review=review, path=saved, next_action=next_action, warnings=warnings)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
@dataclass
class ReviewStatus:
    chapter_id: str
    task_id: str
    latest: Review | None
    risk_level: str
    rounds_used: int
    max_rounds: int
    approved_by: list[str]
    missing_reviewers: list[str]
    blockers: list[Finding]
    verdict: str

    @property
    def passed(self) -> bool:
        return self.verdict == "APPROVE" and not self.blockers and not self.missing_reviewers

    def as_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "task_id": self.task_id,
            "verdict": self.verdict,
            "risk_level": self.risk_level,
            "rounds_used": self.rounds_used,
            "max_rounds": self.max_rounds,
            "approved_by": list(self.approved_by),
            "missing_reviewers": list(self.missing_reviewers),
            "blockers": [item.as_dict() for item in self.blockers],
        }


def status(runtime: Path, *, task_id: str, chapter_id: str) -> ReviewStatus:
    """这一章现在能不能过。只读，不改任何东西。"""
    store = ReviewStore(runtime)
    history = store.list(task_id=task_id, chapter_id=chapter_id)
    if not history:
        return ReviewStatus(
            chapter_id=chapter_id,
            task_id=task_id,
            latest=None,
            risk_level="",
            rounds_used=0,
            max_rounds=DEFAULT_MAX_ROUNDS,
            approved_by=[],
            missing_reviewers=list(required_reviewers("LOW")),
            blockers=[],
            verdict="NOT_REVIEWED",
        )

    latest = history[-1]
    risk = max((item.risk_level for item in history), key=lambda level: RISK_ORDER.index(level) if level in RISK_ORDER else 0)
    approved = sorted({item.reviewer_role for item in history if item.verdict == "APPROVE" and item.risk_level == risk})
    required = required_reviewers(risk)
    missing = [role for role in required if role not in approved]
    blockers = [item for item in latest.findings if item.severity == "BLOCKER"]
    rounds_used = len({item.round for item in history if item.verdict == "REVISE"})

    if latest.verdict != "APPROVE":
        verdict = "REVISE"
    elif blockers:
        verdict = "BLOCKED"
    elif missing:
        verdict = "PARTIAL"
    else:
        verdict = "APPROVE"

    return ReviewStatus(
        chapter_id=chapter_id,
        task_id=task_id,
        latest=latest,
        risk_level=risk,
        rounds_used=rounds_used,
        max_rounds=max(history[-1].max_rounds, DEFAULT_MAX_ROUNDS),
        approved_by=approved,
        missing_reviewers=missing,
        blockers=blockers,
        verdict=verdict,
    )


__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "Finding",
    "Review",
    "ReviewStatus",
    "ReviewStore",
    "SubmitResult",
    "deterministic_risk",
    "draft_of",
    "evaluate",
    "raise_to",
    "required_reviewers",
    "status",
    "submit",
]
