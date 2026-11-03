"""总编 Agent：流程裁决与问题汇总，但不能替作者批准 Canon（PRD 9.3）。"""

from __future__ import annotations

from typing import Any

from ..validators.schema import Issue
from .base import AgentResult, BaseAgent, offline_note

VERDICTS = ("ACCEPTED", "REVISION_REQUIRED", "AUTHOR_APPROVAL_REQUIRED", "BLOCKED")


def verdict_from_issues(issues: list[Issue], author_decisions: list[str]) -> str:
    if any(i.severity == "BLOCKER" or i.blocks_write for i in issues):
        if any(i.severity == "BLOCKER" for i in issues):
            return "BLOCKED"
        return "REVISION_REQUIRED"
    if author_decisions:
        return "AUTHOR_APPROVAL_REQUIRED"
    if any(i.severity == "MAJOR" for i in issues):
        return "REVISION_REQUIRED"
    return "ACCEPTED"


class ChiefEditorAgent(BaseAgent):
    key = "chief_editor"
    display = "总编 Agent"

    def review(
        self,
        *,
        stage: str,
        chapter_id: str,
        material: str,
        hard_issues: list[Issue],
        author_decisions: list[str] | None = None,
    ) -> AgentResult:
        author_decisions = author_decisions or []
        if self.llm.available:
            prompt = (
                f"审核阶段：{stage}\n章节：{chapter_id}\n\n"
                "以下是待审核材料与系统硬规则检查结果。请按你的职责输出审核结论，"
                "并在末尾给出约定的 JSON 块。\n\n"
                f"{material}\n\n"
                "## 系统硬规则检查结果\n"
                + ("\n".join(f"- [{i.severity}] {i.category}: {i.detail}" for i in hard_issues) or "- 无")
            )
            try:
                content = self.run_llm(prompt, temperature=0.2)
                data = self.extract_json(content) or {}
                verdict = str(data.get("verdict") or "").upper()
                if verdict not in VERDICTS:
                    verdict = verdict_from_issues(hard_issues, author_decisions)
                return self.make_result(
                    status=verdict,
                    content=content,
                    mode="llm",
                    data={**data, "verdict": verdict},
                )
            except Exception as exc:  # noqa: BLE001
                return self._offline(stage, chapter_id, hard_issues, author_decisions, f"LLM 调用失败：{exc}")
        return self._offline(stage, chapter_id, hard_issues, author_decisions, "未配置 LLM")

    # ------------------------------------------------------------------ 离线
    def _offline(
        self,
        stage: str,
        chapter_id: str,
        hard_issues: list[Issue],
        author_decisions: list[str],
        reason: str,
    ) -> AgentResult:
        verdict = verdict_from_issues(hard_issues, author_decisions)
        issues_json: list[dict[str, Any]] = [i.to_dict() for i in hard_issues]

        lines = [
            f"# 总编审核｜{chapter_id}（{stage}）",
            "",
            "## 1. 当前任务",
            f"{stage} 阶段审核：{chapter_id}",
            "",
            "## 2. 已完成阶段",
            "由 Python 执行器按固定工作流推进，本次仅做本阶段裁决。",
            "",
            "## 3. 审核结论",
            f"**{verdict}**",
            "",
            "## 4. 问题清单（按严重度）",
        ]
        if hard_issues:
            lines += [
                "| 严重度 | 类别 | 描述 | 引用 | 阻断 |",
                "|---|---|---|---|---|",
            ]
            for issue in sorted(
                hard_issues,
                key=lambda i: ({"BLOCKER": 0, "MAJOR": 1, "MINOR": 2}.get(i.severity, 9), i.category),
            ):
                lines.append(
                    f"| {issue.severity} | {issue.category} | {issue.detail.replace('|', '/')} | "
                    f"{', '.join(issue.refs) or '-'} | {'是' if issue.blocks_write else '否'} |"
                )
        else:
            lines.append("无")

        lines += ["", "## 5. 待作者确认事项"]
        lines += [f"- {item}" for item in author_decisions] or ["- 无"]
        lines += ["", "## 6. 下一步建议"]
        lines.append(
            {
                "ACCEPTED": "可以进入下一阶段。",
                "REVISION_REQUIRED": "先修改问题清单中标记为阻断的项，再重新提交审核。",
                "AUTHOR_APPROVAL_REQUIRED": "需要作者对上述事项做出决定后才能继续。",
                "BLOCKED": "存在阻断级冲突，必须由作者先解决设定冲突。",
            }[verdict]
        )
        lines += ["", "## 7. 引用来源", "- 硬规则检查（Python Rules Engine）", "- 本章大纲与资料包"]

        data = {
            "verdict": verdict,
            "issues": issues_json,
            "author_decisions": author_decisions,
            "next_step": "见报告第 6 节",
        }
        return self.make_result(
            status=verdict,
            content="\n".join(lines),
            mode="offline",
            data=data,
            sources=["Python Rules Engine"],
            note=offline_note(self.cfg, reason),
        )
