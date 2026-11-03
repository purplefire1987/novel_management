"""连续性检查：实体会员、生死、状态回退、事件幂等（PRD 13.3 / 17.1）。"""

from __future__ import annotations

from typing import Any, Iterable

from ..config import Config
from .schema import Issue

DEAD_MARKERS = ("死亡", "已死", "身亡", "殒命")
ALIVE_MARKERS = ("存活", "健在", "未死", "生死未明")


def state_view(meta_or_kv: dict[str, Any]) -> dict[str, str]:
    """把状态文档的关键信息压平成字典，便于比较。"""
    return {str(k): str(v) for k, v in (meta_or_kv or {}).items() if v is not None}


def is_objectively_dead(state: dict[str, Any]) -> bool:
    text = str(state.get("objective_status") or state.get("客观生死") or "")
    if any(marker in text for marker in ALIVE_MARKERS):
        return False
    return any(marker in text for marker in DEAD_MARKERS)


def check_cast(
    cfg: Config,
    cast_ids: Iterable[str],
    states_by_character: dict[str, dict[str, Any]],
    exists_fn,
) -> list[Issue]:
    """出場人物检查：存在性、状态存在性、生死（AC-03）。"""
    issues: list[Issue] = []
    unknown_blocking = bool(cfg.guardrails.get("block_on_unknown_entity", False))
    dead_blocking = bool(cfg.guardrails.get("block_on_dead_character", True))

    for char_id in cast_ids:
        if not exists_fn(char_id):
            issues.append(
                Issue(
                    "BLOCKER" if unknown_blocking else "MAJOR",
                    "continuity",
                    f"出场人物 {char_id} 在知识库中不存在",
                    refs=[char_id],
                    blocks_write=unknown_blocking,
                )
            )
            continue

        state = states_by_character.get(char_id)
        if not state:
            issues.append(
                Issue(
                    "MINOR",
                    "continuity",
                    f"{char_id} 没有当前状态记录，本章无法校验伤势/位置/持物",
                    refs=[char_id],
                )
            )
            continue

        if is_objectively_dead(state):
            issues.append(
                Issue(
                    "BLOCKER" if dead_blocking else "MAJOR",
                    "continuity",
                    f"{char_id} 的客观状态为死亡，却出现在出场名单中；"
                    "需作者确认是误判、替身还是复活（世界规则不允许复活）",
                    refs=[char_id],
                    blocks_write=dead_blocking,
                )
            )

        awareness = " ".join(
            str(state.get(key) or "") for key in ("awareness", "认知边界", "已知信息", "未知信息")
        ).strip()
        if not awareness:
            issues.append(
                Issue(
                    "MINOR",
                    "continuity",
                    f"{char_id} 的状态缺少认知边界（已知/未知），存在信息越界风险",
                    refs=[char_id],
                )
            )
    return issues


def check_locations(location_ids: Iterable[str], exists_fn) -> list[Issue]:
    return [
        Issue("MAJOR", "continuity", f"地点 {loc} 不存在", refs=[loc])
        for loc in sorted({i for i in location_ids if i})
        if not exists_fn(loc)
    ]


def check_event_idempotency(event_ids: Iterable[str], exists_fn) -> list[Issue]:
    """AC-11：同一事件 ID 重复写入必须被识别。"""
    return [
        Issue(
            "MAJOR",
            "idempotency",
            f"事件 {eid} 已存在，重复执行会产生重复事件",
            refs=[eid],
            blocks_write=True,
        )
        for eid in sorted({i for i in event_ids if i})
        if exists_fn(eid)
    ]


def check_state_regression(
    old_state: dict[str, Any],
    new_values: dict[str, Any],
    evidence: list[str],
    chapter_id: str,
) -> list[Issue]:
    """新旧状态对比：伤势/位置变化必须有事件或正文依据（AC-02）。"""
    issues: list[Issue] = []
    char_id = str(old_state.get("character_id") or old_state.get("id") or "?")
    has_evidence = any(e and e.strip() for e in evidence)

    old_injury = str(old_state.get("injury") or old_state.get("伤势") or "").strip()
    new_injury = str(new_values.get("injury") or new_values.get("伤势") or "").strip()
    healed = new_injury in ("", "无", "已愈", "痊愈") or "已愈" in new_injury
    if old_injury and old_injury not in ("无", "无新伤") and healed and not has_evidence:
        issues.append(
            Issue(
                "MAJOR",
                "continuity",
                f"{char_id} 在 {chapter_id} 的伤势由「{old_injury}」变为无伤，但没有治疗过程或事件依据",
                refs=[char_id],
                blocks_write=True,
            )
        )

    old_loc = str(old_state.get("location") or old_state.get("位置") or "").strip()
    new_loc = str(new_values.get("location") or new_values.get("位置") or "").strip()
    if old_loc and new_loc and old_loc != new_loc and not has_evidence:
        issues.append(
            Issue(
                "MINOR",
                "continuity",
                f"{char_id} 位置由 {old_loc} 变为 {new_loc}，缺少移动事件依据",
                refs=[char_id],
            )
        )
    return issues


def check_item_holder(
    item_id: str,
    item_meta: dict[str, Any],
    cast_ids: Iterable[str],
    mentioned_in_outline: bool,
) -> list[Issue]:
    """物品持有者不在场却在本章被当成随手可用 → 提示。"""
    holder = str(item_meta.get("current_holder") or item_meta.get("当前持有者") or "")
    if not holder or not mentioned_in_outline:
        return []
    if any(cid in holder for cid in cast_ids):
        return []
    return [
        Issue(
            "MAJOR",
            "continuity",
            f"物品 {item_id} 当前持有者为 {holder}，但此人不在本章出场名单中",
            refs=[item_id, holder],
        )
    ]


def check_world_rule_text(text: str, rules: list[str]) -> list[Issue]:
    """极轻量的世界规则关键词检查（真正判断交给 LLM 审核）。"""
    issues: list[Issue] = []
    if "复活" in text or "死而复生" in text:
        if any("不得出现死者复活" in rule or "复活" in rule for rule in rules):
            issues.append(
                Issue(
                    "MAJOR",
                    "continuity",
                    "草稿出现「复活」相关表述，与世界规则（死者不可复生）冲突，需人工确认",
                    blocks_write=True,
                )
            )
    return issues
