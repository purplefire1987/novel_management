"""Schema 层：Pydantic 模型 + 文档校验（PRD 7.1 / 10.3 第 5 步）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..config import Config

CANON_STATUSES = {"confirmed", "proposed", "deprecated"}
SEVERITIES = ("BLOCKER", "MAJOR", "MINOR")

FORESHADOW_STATUSES = {
    "planned",
    "planted",
    "hinted",
    "triggered",
    "paid_off",
    "abandoned",
    "overdue",
}

PROPOSAL_KINDS = {
    "event",
    "character_state",
    "relationship",
    "foreshadowing",
    "summary",
    "new_entity",
    "canon_change",
}

APPROVAL_CLASSES = {"auto", "author", "blocking"}
CONFIDENCE_LEVELS = {"CONFIRMED", "PROPOSED", "INFERRED", "UNKNOWN", "CONFLICT"}

CHAPTER_ID_RE = re.compile(r"^CH-\d{4}$")


@dataclass
class Issue:
    """一条检查结果。blocks_write=True 的项会阻止自动推进。"""

    severity: str
    category: str
    detail: str
    refs: list[str] = field(default_factory=list)
    blocks_write: bool = False

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            self.severity = "MAJOR"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "detail": self.detail,
            "refs": self.refs,
            "blocks_write": self.blocks_write,
        }

    def __str__(self) -> str:  # pragma: no cover - 仅日志用
        refs = f" [{','.join(self.refs)}]" if self.refs else ""
        return f"{self.severity}/{self.category}: {self.detail}{refs}"


# --------------------------------------------------------------------------- 模型


class BaseMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    name: str | None = None
    title: str | None = None
    aliases: list[str] = Field(default_factory=list)
    canon_status: str = "confirmed"
    created_at: Any = None
    updated_at: Any = None
    source_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("aliases", "source_refs", "tags", mode="before")
    @classmethod
    def _as_list(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    @field_validator("canon_status", mode="before")
    @classmethod
    def _canon(cls, value: Any) -> str:
        return str(value or "confirmed")


class RevealWindow(BaseModel):
    model_config = ConfigDict(extra="allow")

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _chapter(cls, value: str) -> str:
        if not CHAPTER_ID_RE.match(str(value)):
            raise ValueError(f"回收窗口必须使用章节 ID（CH-0004），收到：{value}")
        return str(value)


class CharacterMeta(BaseMeta):
    pass


class CharacterStateMeta(BaseMeta):
    character_id: str
    as_of_event: str | None = None


class LocationMeta(BaseMeta):
    pass


class FactionMeta(BaseMeta):
    pass


class ItemMeta(BaseMeta):
    pass


class EnemyMeta(BaseMeta):
    pass


class WorldMeta(BaseMeta):
    pass


class ForeshadowingMeta(BaseMeta):
    status: str
    planted_in: str
    target_reveal_window: RevealWindow
    trigger_conditions: list[str] = Field(default_factory=list)
    importance: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in FORESHADOW_STATUSES:
            raise ValueError(f"非法伏笔状态：{value}")
        return value

    @field_validator("planted_in")
    @classmethod
    def _planted(cls, value: str) -> str:
        if not CHAPTER_ID_RE.match(str(value)):
            raise ValueError(f"planted_in 必须是章节 ID：{value}")
        return str(value)

    @field_validator("trigger_conditions", mode="before")
    @classmethod
    def _triggers(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)


class EventMeta(BaseMeta):
    chapter_id: str
    sequence: int = 1
    participants: list[str] = Field(default_factory=list)
    location_id: str | None = None

    @field_validator("participants", mode="before")
    @classmethod
    def _participants(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    @field_validator("chapter_id")
    @classmethod
    def _chapter(cls, value: str) -> str:
        if not CHAPTER_ID_RE.match(str(value)):
            raise ValueError(f"chapter_id 必须是章节 ID：{value}")
        return str(value)


class ChapterOutlineMeta(BaseMeta):
    volume_id: str | None = None
    status: str = "outline"


class ManuscriptMeta(BaseMeta):
    volume_id: str | None = None
    status: str = "draft"
    word_count: int = 0
    outline_ref: str | None = None
    preflight_ref: str | None = None


class SceneBeatMeta(BaseMeta):
    chapter_id: str
    sequence: int = 1
    status: str = "proposed"


class RelationshipMeta(BaseMeta):
    source_id: str
    target_id: str
    relation_type: str = "unknown"
    valid_from: str | None = None
    valid_to: str | None = None
    source_event: str | None = None


class MissionMeta(BaseMeta):
    status: str = "active"


class StoryAssetMeta(BaseMeta):
    category: str = "dialogue"


class VolumeMeta(BaseMeta):
    pass


MODEL_BY_TYPE: dict[str, type[BaseMeta]] = {
    "character": CharacterMeta,
    "character_state": CharacterStateMeta,
    "location": LocationMeta,
    "faction": FactionMeta,
    "item": ItemMeta,
    "enemy": EnemyMeta,
    "world": WorldMeta,
    "foreshadowing": ForeshadowingMeta,
    "event": EventMeta,
    "chapter_outline": ChapterOutlineMeta,
    "manuscript": ManuscriptMeta,
    "scene_beat": SceneBeatMeta,
    "relationship": RelationshipMeta,
    "mission": MissionMeta,
    "story_asset": StoryAssetMeta,
    "volume": VolumeMeta,
}

# 这些类型必须有人类可读主名称
NAME_REQUIRED = set(MODEL_BY_TYPE) - {"volume"}


# --------------------------------------------------------------------- 提案模型


class Proposal(BaseModel):
    """记忆更新提案。所有正式写入都必须由提案驱动。"""

    model_config = ConfigDict(extra="allow")

    proposal_id: str
    kind: str
    op: str = "create"
    target_id: str | None = None
    chapter_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    confidence: str = "CONFIRMED"
    approval_class: str = "author"
    status: str = "proposed"      # proposed / approved / rejected
    origin: str = "memory_manager"
    target_path: str | None = None
    note: str | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in PROPOSAL_KINDS:
            raise ValueError(f"非法提案类型：{value}")
        return value

    @field_validator("approval_class")
    @classmethod
    def _approval(cls, value: str) -> str:
        return value if value in APPROVAL_CLASSES else "author"

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, value: str) -> str:
        return value if value in CONFIDENCE_LEVELS else "PROPOSED"

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)


class ProposalBundle(BaseModel):
    model_config = ConfigDict(extra="allow")

    chapter_id: str
    operation_id: str
    generated_by: str = "memory_manager"
    mode: str = "offline"
    proposals: list[Proposal] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------- 校验入口


def validate_meta(meta: dict[str, Any], cfg: Config, rel_path: str | None = None) -> list[Issue]:
    """校验一份 frontmatter，返回问题列表（不抛异常）。"""
    issues: list[Issue] = []
    where = f"（{rel_path}）" if rel_path else ""

    if not isinstance(meta, dict) or not meta:
        return [Issue("MAJOR", "schema", f"缺少 frontmatter{where}")]

    doc_id = meta.get("id")
    doc_type = meta.get("type")

    if not doc_id:
        return [Issue("MAJOR", "schema", f"缺少 id 字段{where}")]
    if not doc_type:
        return [Issue("MAJOR", "schema", f"缺少 type 字段{where}", refs=[str(doc_id)])]

    pattern = cfg.id_pattern(str(doc_type))
    if pattern and not re.fullmatch(pattern, str(doc_id)):
        issues.append(
            Issue(
                "MAJOR",
                "id_format",
                f"ID 格式不符合规则 {doc_type}:{pattern}，实际 {doc_id}{where}",
                refs=[str(doc_id)],
            )
        )

    model_cls = MODEL_BY_TYPE.get(str(doc_type))
    if model_cls is None:
        issues.append(Issue("MINOR", "schema", f"未知文档类型 {doc_type}{where}", refs=[str(doc_id)]))
        return issues

    try:
        model = model_cls.model_validate(meta)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"])
            issues.append(
                Issue(
                    "MAJOR",
                    "schema",
                    f"{doc_type} 字段校验失败 {loc}：{err['msg']}{where}",
                    refs=[str(doc_id)],
                )
            )
        return issues

    if model.canon_status not in CANON_STATUSES:
        issues.append(
            Issue(
                "MAJOR",
                "canon",
                f"canon_status 必须是 confirmed/proposed/deprecated，实际 {model.canon_status}{where}",
                refs=[str(doc_id)],
            )
        )

    if doc_type in NAME_REQUIRED and not (model.name or model.title):
        issues.append(
            Issue("MINOR", "schema", f"{doc_type} 缺少 name/title{where}", refs=[str(doc_id)])
        )

    if doc_type == "manuscript" and isinstance(model, ManuscriptMeta):
        if model.status not in cfg.workflow.get("states", []):
            issues.append(
                Issue(
                    "MAJOR",
                    "state",
                    f"正文状态 {model.status} 不在状态机定义内{where}",
                    refs=[str(doc_id)],
                )
            )

    return issues
