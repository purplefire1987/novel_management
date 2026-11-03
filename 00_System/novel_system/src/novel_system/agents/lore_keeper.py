"""资料官 Agent：只读，生成可追溯的资料包（PRD 9.4）。"""

from __future__ import annotations

from typing import Any

from ..validators.schema import Issue
from .base import AgentResult, BaseAgent, offline_note


class LoreKeeperAgent(BaseAgent):
    key = "lore_keeper"
    display = "资料官 Agent"

    def build(
        self,
        *,
        chapter_id: str,
        pack_text: str,
        resolved: Any,
        hard_issues: list[Issue],
    ) -> AgentResult:
        if self.llm.available:
            prompt = (
                f"目标章节：{chapter_id}\n"
                "下面是系统按相关性检索出的资料（已按上下文预算裁剪）。"
                "请基于这些资料整理本章资料包，只使用资料中出现的事实；"
                "缺失的信息写入 unknowns，不要补造。\n\n"
                f"{pack_text}"
            )
            try:
                content = self.run_llm(prompt, temperature=0.2)
                data = self.extract_json(content) or {}
                return self.make_result(
                    status="OK",
                    content=content,
                    mode="llm",
                    data=data,
                    sources=list(data.get("sources") or []),
                )
            except Exception as exc:  # noqa: BLE001 - 降级必须留下痕迹
                return self._offline(chapter_id, pack_text, resolved, hard_issues, f"LLM 调用失败：{exc}")
        return self._offline(chapter_id, pack_text, resolved, hard_issues, "未配置 LLM")

    # ------------------------------------------------------------------ 离线
    def _offline(
        self,
        chapter_id: str,
        pack_text: str,
        resolved: Any,
        hard_issues: list[Issue],
        reason: str,
    ) -> AgentResult:
        cast = []
        for char_id in resolved.cast_ids:
            row = resolved.entities.get(char_id, {})
            state = resolved.states_by_character.get(char_id, {})
            cast.append(
                {
                    "id": char_id,
                    "name": row.get("name"),
                    "location": state.get("位置") or state.get("location") or "未记录",
                    "injury": state.get("伤势") or state.get("injury") or "未记录",
                    "objective_status": state.get("客观生死") or state.get("objective_status") or "未记录",
                    "knows": state.get("已知信息") or "",
                    "unknown": state.get("未知信息") or "",
                }
            )

        foreshadowing = []
        for fs_id in resolved.foreshadow_ids:
            row = resolved.entities.get(fs_id, {})
            foreshadowing.append(
                {
                    "id": fs_id,
                    "constraint": "见 Pack「伏笔与历史事件」；不得越窗回收",
                    "status": row.get("canon_status") or "unknown",
                }
            )

        risks = [
            f"{i.category}: {i.detail}" for i in hard_issues if i.severity in ("BLOCKER", "MAJOR")
        ]
        unknowns = [f"{doc_id} 未在知识库中建立档案" for doc_id in resolved.missing_ids]

        body = [
            f"# 资料官检索结果｜{chapter_id}",
            "",
            "## 本章相关实体",
            *[f"- {c['id']} {c['name'] or ''}｜状态：{c['objective_status']}｜位置：{c['location']}" for c in cast],
            "",
            "## 必须遵守的 Canon",
            "- 世界硬规则与人物当前状态见 Pack 第二节，不在本章新增或推翻任何设定。",
            "",
            "## 相关伏笔",
            *[f"- {f['id']}：{f['constraint']}" for f in foreshadowing],
            "",
            "## 角色已知 / 未知",
            *[f"- {c['id']}：已知 {c['knows'] or '未记录'}｜未知 {c['unknown'] or '未记录'}" for c in cast],
            "",
            "## 潜在连续性风险",
            *([f"- {r}" for r in risks] or ["- 未发现规则级风险"]),
            "",
            "## 未知项与冲突",
            *([f"- {u}" for u in unknowns] or ["- 无"]),
            "",
            "## 来源引用",
            *[f"- {path}" for path in [str(resolved.outline.path)] + [
                str(doc.path) for doc in resolved.docs.values()
            ]][:20],
        ]
        data = {
            "entities": cast,
            "foreshadowing": foreshadowing,
            "risks": risks,
            "unknowns": unknowns,
            "sources": [str(resolved.outline.path)],
        }
        return self.make_result(
            status="OK",
            content="\n".join(body),
            mode="offline",
            data=data,
            sources=data["sources"],
            note=offline_note(self.cfg, reason),
        )
