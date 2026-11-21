"""写稿：主笔产出草稿，落 staging，**永远不落 Canon**。

T09 第一条验收是「草稿不写正式 00_Canon」。本模块把它做成结构性的，不是提醒性的：

- 目标路径只允许 `03_Manuscripts/Drafts/`，拼出来再校验一次「不在 canon 根下」；
- 落盘只走 `StagingArea.stage()`，那是全系统唯一允许 Agent 写内容的地方；
- 想进正式区只有一条路：`approve` → `release prepare` → `publish`，都要作者点头。

写之前还有两道前置，缺一道就不写：

1. **规划够格**（`workflows.plan.check`）——不知道「必须发生什么、禁止发生什么」就动笔，
   写出来的东西没法判对错；
2. **资料包 PASS**（`agents.preflight`）——没有 PASS 的资料包就写，等于让主笔凭记忆编。

## 正文与元数据分离（PRD §14.1）

模型返回的是结构化信封：`chapter_text` 是正文，`new_fact_candidates` /
`plan_deviations` / `open_questions` 是元数据。落盘的 Markdown 里，正文部分
**不含任何系统注释**——注释混进正文，导出时就成了读者能看见的东西。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as cfg_mod
from ..agents import preflight as pf
from ..agents import runner
from ..agents.metering import Meter
from ..adapters.mock import LLMClient
from ..errors import ConflictError, SecurityError, UsageError
from ..paths import ProjectPaths
from ..staging import StagingArea
from ..tasks import TaskStore
from ..storage import dump_yaml, sha256_text
from . import plan as plan_mod

DRAFT_DIR = "03_Manuscripts/Drafts"
CANON_DIR = "01_Canon"

TASK_TYPES = ("draft", "revision", "outline")

RESULT_KEYS = ("chapter_id", "chapter_text")

INSTRUCTIONS = """\
写这一章的正文。

硬要求：
1. 只输出 JSON 信封，不要 Markdown 围栏之外的解释。
2. `result.chapter_text` 是正文本体，**不要**在里面写任何系统注释、提纲、自我说明。
3. `result.new_fact_candidates` 列出正文里新引入、但规划没写的事实（可为空）。
4. `result.plan_deviations` 列出与规划「必须发生」对不上的地方（可为空）。
5. `result.open_questions` 列出你不确定、需要作者裁定的点（可为空）。
6. 规划里「禁止发生」的条目一条都不许出现。拿不准就写进 open_questions，不要硬写。
"""


@dataclass
class DraftResult:
    task_id: str
    chapter_id: str
    target: str
    staged: str
    sha256: str
    words: int
    chapter_text: str
    title: str = ""
    new_facts: list[dict] = field(default_factory=list)
    deviations: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    preflight_id: str = ""
    preflight_gate: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "chapter_id": self.chapter_id,
            "target": self.target,
            "staged": self.staged,
            "sha256": self.sha256,
            "words": self.words,
            "title": self.title,
            "new_fact_candidates": list(self.new_facts),
            "plan_deviations": list(self.deviations),
            "open_questions": list(self.open_questions),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
            "preflight_id": self.preflight_id,
            "preflight_gate": self.preflight_gate,
            "warnings": list(self.warnings),
        }


def write(
    pp: ProjectPaths,
    runtime: Path,
    *,
    task_id: str,
    chapter_id: str,
    llm: LLMClient,
    base_release: str = "",
    meter: Meter | None = None,
    actor: str = "writer",
    note: str = "",
    model: str = "",
    replace: bool = False,
    preflight_id: str = "",
) -> DraftResult:
    """写一章草稿进 staging。"""
    task = TaskStore(runtime).load(task_id)
    if task.project_id != pp.project_id:
        raise SecurityError(f"任务 {task.task_id} 属于 {task.project_id}，不是 {pp.project_id}")
    if task.task_type not in TASK_TYPES:
        raise UsageError(
            f"任务 {task.task_id} 的类型是 {task.task_type}，不是写作任务"
            f"（允许：{'/'.join(TASK_TYPES)}）"
        )
    if task.write_mode == "read_only":
        raise SecurityError(f"任务 {task.task_id} 是 read_only，不能产出草稿")

    # ---- 前置一：规划够格 ----
    check = plan_mod.check(pp, chapter_id)
    if not check.ready:
        missing = "；".join(f"{spec.label}（{spec.key}）" for spec in check.missing_required)
        raise UsageError(
            f"{chapter_id} 的规划还缺开写必需的字段：{missing}。\n"
            f"  先补章卡：{check.rel}\n"
            f"  可先看 `novel plan derive {chapter_id}` 能派生出什么（只派生确定性可读的部分）"
        )

    # ---- 前置二：资料包 PASS ----
    gate_doc = _ensure_preflight(
        pp, runtime, chapter_id=chapter_id, base_release=base_release,
        meter=meter, preflight_id=preflight_id,
    )

    # ---- 装配与调用 ----
    envelope_task = {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "chapter_id": chapter_id,
        "must_happen": _strings(check.meta.get("must_happen")),
        "must_not_happen": _strings(check.meta.get("must_not_happen")),
        "creative_scope": check.scope,
        "preflight_id": str(gate_doc.get("preflight_id") or ""),
    }
    run = runner.run(
        llm,
        "writer",
        instructions=INSTRUCTIONS,
        task=envelope_task,
        release=base_release,
        anchors={"chapter_id": chapter_id},
        meter=meter,
        model=model,
    )
    if not run.ok:
        raise UsageError("主笔调用失败：" + run.describe_errors())

    result = run.result()
    missing = [key for key in RESULT_KEYS if not str(result.get(key) or "").strip()]
    if missing:
        raise UsageError(
            "主笔返回的信封缺字段：" + "、".join(missing)
            + "。缺正文就不往下写——不拿「result 里别的字段」凑一份草稿"
        )

    declared = str(result.get("chapter_id") or "").strip()
    if declared and declared != chapter_id:
        raise ConflictError(
            f"主笔写的是 {declared}，本次任务要的是 {chapter_id}："
            "章节号写错会让草稿落到别的章上"
        )

    text = str(result.get("chapter_text") or "")
    title = str(result.get("title") or "").strip() or _title_of(text) or chapter_id
    target = _target_rel(chapter_id)
    document = render_document(
        chapter_id=chapter_id,
        title=title,
        text=text,
        base_release=base_release,
        preflight_id=str(gate_doc.get("preflight_id") or ""),
        task_id=task.task_id,
    )

    record = StagingArea(pp, task).stage(
        data=document.encode("utf-8"),
        target_rel=target,
        actor=actor,
        note=note or f"{chapter_id} 草稿",
        replace=replace,
    )

    warnings: list[str] = []
    deviations = _strings(result.get("plan_deviations"))
    questions = _strings(result.get("open_questions"))
    if deviations:
        warnings.append(f"主笔自报 {len(deviations)} 处与规划偏离，审核时要逐条看")
    if questions:
        warnings.append(f"主笔留了 {len(questions)} 个未决问题")

    return DraftResult(
        task_id=task.task_id,
        chapter_id=chapter_id,
        target=record.target,
        staged=record.staged,
        sha256=record.sha256,
        words=count_words(text),
        chapter_text=text,
        title=title,
        new_facts=[item for item in result.get("new_fact_candidates") or [] if isinstance(item, dict)],
        deviations=deviations,
        open_questions=questions,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        model=run.model,
        preflight_id=str(gate_doc.get("preflight_id") or ""),
        preflight_gate=str(gate_doc.get("gate_result") or ""),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _ensure_preflight(
    pp: ProjectPaths,
    runtime: Path,
    *,
    chapter_id: str,
    base_release: str,
    meter: Meter | None,
    preflight_id: str,
) -> dict:
    """拿到一份 PASS 的资料包：优先复用，没有就现跑一次（不落盘）。"""
    if preflight_id:
        doc = pf.load(runtime, preflight_id)
        if str(doc.get("chapter_id") or "") != chapter_id:
            raise ConflictError(
                f"资料包 {preflight_id} 是给 {doc.get('chapter_id')} 的，不是 {chapter_id}"
            )
        if doc.get("gate_result") != "PASS":
            raise UsageError(
                f"资料包 {preflight_id} 的结论是 {doc.get('gate_result')}，不能据此开写：\n"
                + _blockers(doc)
            )
        return doc

    latest = latest_pass(runtime, chapter_id)
    if latest is not None:
        return latest

    built = pf.build(pp, runtime, chapter_id=chapter_id, base_release=base_release, meter=meter, write=False)
    if built.doc.get("gate_result") != "PASS":
        raise UsageError(
            f"{chapter_id} 的资料包没通过（{built.doc.get('gate_result')}），不能开写：\n"
            + _blockers(built.doc)
            + "\n  先处理上面的阻断项，再重跑 novel preflight "
            + chapter_id
        )
    return built.doc


def latest_pass(runtime: Path, chapter_id: str) -> dict | None:
    """最新的 PASS 资料包。

    只认 `gate_result == PASS`：BLOCKED 的资料包不能因为「它是新的」就覆盖掉
    一个旧的 PASS——那等于用一次更差的检查结果换掉一次更好的。
    """
    root = pf.preflight_dir(runtime)
    if not root.exists():
        return None
    best: tuple[int, dict] | None = None
    for path in sorted(root.glob("PREFLIGHT-*.yaml")):
        try:
            doc = cfg_mod.read_yaml(path)
        except Exception:
            continue
        if str(doc.get("chapter_id") or "") != chapter_id:
            continue
        if doc.get("gate_result") != "PASS":
            continue
        number = _number(doc.get("preflight_id"))
        if best is None or number > best[0]:
            best = (number, doc)
    return best[1] if best else None


def _number(value: object) -> int:
    found = re.search(r"([0-9]+)$", str(value or ""))
    return int(found.group(1)) if found else 0


def _blockers(doc: dict) -> str:
    lines = []
    for item in doc.get("issues") or []:
        if str(item.get("severity") or "") != "BLOCKER":
            continue
        lines.append(f"  [BLOCKER/{item.get('category', 'OTHER')}] {item.get('message', '')}")
    return "\n".join(lines) or "  （没有具体阻断项，但 gate 判了 BLOCKED——先看 coverage.unresolved_gaps）"


def _target_rel(chapter_id: str) -> str:
    target = f"{DRAFT_DIR}/{chapter_id}.md"
    # 结构性硬拦：拼出来的路径不许落在 Canon 下。这里不靠调用方自觉。
    if target.startswith(CANON_DIR + "/") or f"/{CANON_DIR}/" in target:
        raise SecurityError(f"草稿目标 {target} 落在正式区 {CANON_DIR}，拒绝写入")
    return target


def render_document(
    *,
    chapter_id: str,
    title: str,
    text: str,
    base_release: str,
    preflight_id: str,
    task_id: str,
) -> str:
    """正文 + frontmatter。正文里不掺系统注释（PRD §14.1）。"""
    meta = {
        "id": chapter_id,
        "type": "chapter",
        "title": title,
        "status": "draft",
        "word_count": count_words(text),
    }
    if base_release:
        meta["base_release"] = base_release
    if preflight_id:
        meta["preflight_id"] = preflight_id
    meta["created_by_task"] = task_id
    body = text.strip("\n")
    return "---\n" + dump_yaml(meta).rstrip("\n") + "\n---\n\n" + body + "\n"


def count_words(text: str) -> int:
    """字数：非空白字符。

    与预算层 `estimate_tokens` 同一口径（都按非空白字符数）。两处用不同口径时，
    「5000 字的一章」在两个报告里会是两个数，作者第一反应是不信。
    """
    return len(re.sub(r"\s", "", text or ""))


def _title_of(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return ""
    return ""


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("message") or "").strip()
                if text:
                    out.append(text)
        return out
    return []


__all__ = ["DraftResult", "count_words", "latest_pass", "render_document", "write"]
