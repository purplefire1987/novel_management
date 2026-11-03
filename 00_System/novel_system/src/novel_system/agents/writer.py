"""主笔 Agent：只依据批准的细纲与 Pack 写正文（PRD 9.6）。"""

from __future__ import annotations

from typing import Any

from .. import vault
from .base import AgentResult, BaseAgent, offline_note

BEAT_FIELDS = (
    "场景目标",
    "POV",
    "地点",
    "时间",
    "参与角色",
    "场景开始状态",
    "冲突",
    "关键动作",
    "信息揭示",
    "场景结束状态",
)


class WriterAgent(BaseAgent):
    key = "writer"
    display = "主笔 Agent"

    def write(
        self,
        *,
        chapter_id: str,
        title: str,
        pack_text: str,
        scenes: list[dict[str, Any]],
    ) -> AgentResult:
        if self.llm.available:
            prompt = (
                f"章节：{chapter_id}｜{title}\n\n"
                "以下是批准的大纲、场景细纲与 Preflight 资料包。请写本章正文。\n"
                "要求：只写正文；不要输出 YAML 或审稿意见；不得新增未经批准的事实。\n\n"
                f"{pack_text}"
            )
            try:
                content = self.run_llm(prompt, temperature=0.85)
                body = self._normalize(content)
                return self.make_result(
                    status="OK",
                    content=body,
                    mode="llm",
                    data={"word_count": vault.count_words(body)},
                )
            except Exception as exc:  # noqa: BLE001
                return self._offline(chapter_id, title, scenes, f"LLM 调用失败：{exc}")
        return self._offline(chapter_id, title, scenes, "未配置 LLM")

    # ------------------------------------------------------------------ 离线
    def _offline(
        self, chapter_id: str, title: str, scenes: list[dict[str, Any]], reason: str
    ) -> AgentResult:
        lines = [
            "## 正文",
            "",
            "> **【离线占位草稿】**当前未配置 LLM，Python 只按场景细纲拼出结构骨架，不生成文学正文。",
            "> 配置 `NOVEL_LLM_API_KEY` 后重跑 `novel write` 即为真实 AI 草稿；也可由作者直接改写本文件。",
            "",
        ]
        for index, scene in enumerate(scenes, start=1):
            kv = vault.key_values(scene["body"])
            lines.append(f"### 场景 {index}｜{kv.get('场景目标', scene['id'])}")
            lines.append("")
            for field in BEAT_FIELDS:
                value = kv.get(field, "").strip()
                lines.append(f"- {field}：{value or '（细纲未给，需补）'}")
            lines.append("")
        lines += [
            "## 写作备注",
            "- 本文件为流程联调用骨架，任何事实性内容都以细纲为准。",
            "",
            "## 未解决问题",
            f"- {reason}：需要模型或作者补全正文。",
        ]
        return self.make_result(
            status="OK",
            content="\n".join(lines),
            mode="offline",
            data={"word_count": 0, "skeleton": True},
            note=offline_note(self.cfg, reason),
        )

    @staticmethod
    def _normalize(content: str) -> str:
        """保证正文以 `## 正文` 开头，便于后续提取。"""
        text = (content or "").strip()
        if text.startswith("## 正文"):
            return text
        if text.startswith("# "):
            text = "\n".join(text.splitlines()[1:]).lstrip()
        return "## 正文\n\n" + text
