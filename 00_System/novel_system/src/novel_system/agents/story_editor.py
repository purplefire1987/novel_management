"""编辑 Agent：大纲与节拍的结构检查（PRD 9.5）。"""

from __future__ import annotations

from typing import Any

from .. import vault
from ..validators.schema import Issue
from .base import AgentResult, BaseAgent, offline_note

REQUIRED_SECTIONS = (
    "章节目标",
    "POV",
    "时间与地点",
    "核心冲突",
    "必须发生的剧情事实",
    "禁止发生的内容",
    "章节结尾钩子",
)


class StoryEditorAgent(BaseAgent):
    key = "story_editor"
    display = "编辑 Agent"

    def review_outline(
        self, *, chapter_id: str, outline: vault.Doc, scenes: list[dict[str, Any]]
    ) -> AgentResult:
        empty = [name for name in REQUIRED_SECTIONS if not vault.extract_section(outline.body, name).strip()]
        if self.llm.available:
            prompt = (
                f"章节：{chapter_id}\n\n大纲全文：\n{outline.body}\n\n"
                "场景细纲：\n"
                + "\n\n".join(f"{s['id']}:\n{s['body'][:800]}" for s in scenes)
                + "\n\n请检查因果链、动机、节奏、伏笔布局与连续性风险，并按约定 JSON 输出。"
                "不要新增未经批准的世界规则。"
            )
            try:
                content = self.run_llm(prompt, temperature=0.4)
                data = self.extract_json(content) or {}
                return self.make_result(status="OK", content=content, mode="llm", data=data)
            except Exception as exc:  # noqa: BLE001
                return self._offline(chapter_id, empty, scenes, f"LLM 调用失败：{exc}")
        return self._offline(chapter_id, empty, scenes, "未配置 LLM")

    def _offline(
        self, chapter_id: str, empty: list[str], scenes: list[dict[str, Any]], reason: str
    ) -> AgentResult:
        issues = [
            Issue("MAJOR", "outline", f"大纲「{name}」为空，写作时必然要现编", refs=[chapter_id])
            for name in empty
        ]
        if not scenes:
            issues.append(
                Issue(
                    "MAJOR",
                    "outline",
                    "缺少场景细纲（SCENE-*），主笔只能自由发挥，容易跑偏",
                    refs=[chapter_id],
                )
            )
        lines = [
            f"# 结构检查｜{chapter_id}",
            "",
            "## 1. 章节 / 场景目标",
            "见大纲「章节目标」节。",
            "",
            "## 2. 结构方案",
            f"场景数：{len(scenes)}",
            "",
            "## 3. 缺失项",
            *([f"- {name}" for name in empty] or ["- 无"]),
            "",
            "## 4. 连续性风险",
            *([f"- {i.detail}" for i in issues] or ["- 未发现结构性问题"]),
            "",
            "## 5. 新增设定提案",
            "- 无（离线模式不做创作性提案）",
            "",
            "## 6. 作者待确认项",
            "- 无",
        ]
        return self.make_result(
            status="OK" if not issues else "REVISION_REQUIRED",
            content="\n".join(lines),
            mode="offline",
            data={"issues": [i.to_dict() for i in issues], "scene_count": len(scenes)},
            note=offline_note(self.cfg, reason),
        )
