"""上下文构建：按预算组装 Preflight Pack（PRD 11.3 / 11.4）。

不追求把历史正文全塞进去，而是按相关性分层装配，并显式记录截断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import vault
from ..config import Config
from ..database.sqlite import Index, loads
from . import retrieval
from .entity_resolver import Resolved

SECTION_TITLES = {
    "task_and_outline": "一、章节任务与批准细纲",
    "canon_and_state": "二、设定与当前状态",
    "recent_text": "三、近期正文与摘要",
    "foreshadow_and_events": "四、伏笔与历史事件",
    "style_reference": "五、风格参考",
}


@dataclass
class Pack:
    chapter_id: str
    sections: dict[str, str] = field(default_factory=dict)
    stats: dict[str, dict[str, int]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"# Preflight Pack｜{self.chapter_id}", ""]
        for key, title in SECTION_TITLES.items():
            content = self.sections.get(key, "").strip()
            if not content:
                continue
            lines += [f"## {title}", "", content, ""]
        return "\n".join(lines).rstrip() + "\n"

    def as_context(self) -> str:
        return self.render()

    @property
    def total_chars(self) -> int:
        return sum(len(v) for v in self.sections.values())


def _truncate(text: str, budget: int, label: str) -> tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= budget:
        return text, False
    cut = text[:budget].rstrip()
    return f"{cut}\n\n……（{label} 已按上下文预算截断，原文 {len(text)} 字）", True


def _entity_block(doc_id: str, meta: dict[str, Any], body: str) -> str:
    doc_type = meta.get("type")
    name = meta.get("name") or meta.get("title") or ""
    keep: list[str] = []
    if doc_type == "character":
        for section in ("基本信息", "能力与限制", "人物关系", "重要事实"):
            text = vault.extract_section(body, section)
            if text:
                keep.append(f"【{section}】\n{text}")
    elif doc_type == "character_state":
        for section in ("客观状态", "认知边界（角色以为的世界，不等于事实）", "当前目标"):
            text = vault.extract_section(body, section)
            if text:
                keep.append(f"【{section}】\n{text}")
    elif doc_type == "world":
        for section in ("世界规则（硬约束）", "禁忌与不可违反项"):
            text = vault.extract_section(body, section)
            if text:
                keep.append(f"【{section}】\n{text}")
    else:
        keep.append(body.strip()[:900])
    header = f"### {doc_id} {name}（{doc_type}，canon={meta.get('canon_status', 'confirmed')}）"
    return header + "\n" + "\n".join(keep)


def build_pack(
    cfg: Config,
    index: Index,
    resolved: Resolved,
    hard_issues: list[Any] | None = None,
) -> Pack:
    pack = Pack(chapter_id=resolved.chapter_id, meta={"outline": str(resolved.outline.path)})
    total = cfg.budget_chars

    # ---------------- 一、章节任务与细纲 ----------------
    outline = resolved.outline
    task_parts = [
        f"- 章节：{resolved.chapter_id}｜{outline.meta.get('title') or outline.meta.get('name')}",
        f"- 卷：{outline.meta.get('volume_id')}",
        f"- 章节状态：{outline.meta.get('status')}",
    ]
    for heading in ("章节目标", "POV", "时间与地点", "核心冲突", "必须发生的剧情事实", "禁止发生的内容", "章节结尾钩子"):
        text = vault.extract_section(outline.body, heading)
        if text:
            task_parts.append(f"\n**{heading}**\n{text}")
    if resolved.scenes:
        task_parts.append("\n**场景细纲**")
        for scene in resolved.scenes:
            task_parts.append(f"\n#### {scene['id']}\n{scene['body'].strip()[:900]}")
    task_text, _ = _truncate("\n".join(task_parts), int(total * cfg.budget_ratio("task_and_outline")), "章节任务")
    pack.sections["task_and_outline"] = task_text

    # ---------------- 二、设定与当前状态 ----------------
    canon_parts: list[str] = []
    rules = retrieval.world_rules(cfg)
    if rules:
        canon_parts.append("**世界硬规则（不可违反）**\n" + "\n".join(f"- {r}" for r in rules))
    for item in retrieval.canon_docs(resolved):
        canon_parts.append(_entity_block(item["id"], item["meta"], item["body"]))
    for char_id, state in sorted(resolved.states_by_character.items()):
        if char_id not in resolved.cast_ids and char_id not in resolved.mentioned_ids:
            continue
        doc = resolved.docs.get(state.get("id", ""))
        state_body = doc.body if doc else ""
        canon_parts.append(
            _entity_block(str(state.get("id") or f"STATE-{char_id}"), state, state_body)
        )
    hot = retrieval.hot_memory(cfg)
    for key in ("current_volume", "current_story_state", "active_context"):
        if key in hot:
            canon_parts.append(f"**热记忆：{key}**\n{hot[key][:1200]}")
    canon_text, _ = _truncate(
        "\n\n".join(canon_parts), int(total * cfg.budget_ratio("canon_and_state")), "设定与状态"
    )
    pack.sections["canon_and_state"] = canon_text

    # ---------------- 三、近期正文与摘要 ----------------
    recent_parts: list[str] = []
    if "rolling_summary" in hot:
        recent_parts.append(f"**滚动摘要**\n{hot['rolling_summary'][:1500]}")
    recent = retrieval.recent_chapters(cfg, index, resolved.chapter_id, cfg.recent_chapter_count)
    for item in reversed(recent):
        body = item["body"].strip()
        tail = body[-900:] if len(body) > 900 else body
        recent_parts.append(f"**{item['chapter_id']}｜{item['title']}**（节选结尾）\n{tail}")
    recent_text, _ = _truncate(
        "\n\n".join(recent_parts), int(total * cfg.budget_ratio("recent_text")), "近期正文"
    )
    pack.sections["recent_text"] = recent_text

    # ---------------- 四、伏笔与历史事件 ----------------
    fs_parts: list[str] = []
    for row in retrieval.foreshadow_entries(index, resolved.foreshadow_ids):
        fs_parts.append(
            "\n".join(
                [
                    f"### {row['id']}｜{row.get('name') or ''}",
                    f"- 状态：{row.get('status')}｜埋设：{row.get('planted_in')}"
                    f"｜回收窗口：{row.get('target_start')}～{row.get('target_end')}"
                    f"｜重要度：{row.get('importance') or '未标注'}",
                    f"- 触发条件：{', '.join(loads(row.get('trigger_conditions'), [])) or '未指定'}",
                ]
            )
        )
    # 未在大纲登记的伏笔：只提示，不展开
    extra_fs = retrieval.chapters_with_foreshadowing(
        index, resolved.cast_ids + resolved.location_ids, resolved.chapter_id
    )
    if extra_fs:
        fs_parts.append(
            "**未在大纲登记但可能相关（请编辑确认）**\n"
            + "\n".join(f"- {r['id']}｜{r.get('name')}｜状态 {r.get('status')}" for r in extra_fs)
        )
    for item in retrieval.related_events(
        cfg, index, resolved.cast_ids + resolved.location_ids, resolved.chapter_id
    ):
        event = item["event"]
        fs_parts.append(
            f"### {event['event_id']}｜{event.get('name')}（{event.get('chapter_id')}）\n"
            f"{item['body'].strip()[:600]}"
        )
    fs_text, _ = _truncate(
        "\n\n".join(fs_parts), int(total * cfg.budget_ratio("foreshadow_and_events")), "伏笔与事件"
    )
    pack.sections["foreshadow_and_events"] = fs_text

    # ---------------- 五、风格参考 ----------------
    style_text, _ = _truncate(
        retrieval.style_reference(cfg), int(total * cfg.budget_ratio("style_reference")), "风格参考"
    )
    pack.sections["style_reference"] = style_text

    for key, content in pack.sections.items():
        pack.stats[key] = {
            "chars": len(content),
            "budget": int(total * cfg.budget_ratio(key)),
            "ratio": cfg.budget_ratio(key),
        }
    return pack


def save_pack(cfg: Config, pack: Pack) -> str:
    """写入 staging/preflight，返回相对路径。"""
    cfg.ensure_dirs()
    rel = f".novel_system/staging/preflight/PREFLIGHT-{pack.chapter_id}.md"
    meta = {
        "id": f"PREFLIGHT-{pack.chapter_id}",
        "type": "preflight_pack",
        "chapter_id": pack.chapter_id,
        "generated_at": vault_and_time(),
        "total_chars": pack.total_chars,
    }
    vault.write_atomic(cfg.abs(rel), vault.render_doc(meta, pack.render()))
    return rel


def vault_and_time() -> str:
    from ..database.sqlite import now_iso

    return now_iso()


def load_pack(cfg: Config, chapter_id: str) -> str | None:
    path = cfg.staging_dir / "preflight" / f"PREFLIGHT-{chapter_id}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None
