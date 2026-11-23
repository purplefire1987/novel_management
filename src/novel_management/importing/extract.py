"""候选提取：从正文里找出「可能已经在 Canon 里」的东西。

PRD §12.2 最后一句是这一整个模块的约束：**不得自动将全部原稿叙述视为客观事实**。

所以这里产出的每一条都叫 `candidate`，都带 `approved: false`，都只进「导入台账」
（在 staging 里，等作者批），不进 Canon。台账标题写的是「候选（未经批准，不作为事实）」。

提取只做两件机器能负责的事：

1. **ID 提及**——正文里出现 `CHAR-0001` 这种形态，几乎必然是引用；
2. **名称/别名提及**——拿 Canon 现有的 name/aliases 当词典去撞。

词典撞不到的人名**不猜**。原因：中文没有可靠的首字母大写信号，
靠「连续两三字 + 姓氏表」去猜人名，误报率会在长文里堆成噪音，作者看两页就不看了。
宁可漏，也不能让作者觉得「这个清单没有价值」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..infrastructure.filesystem import markdown as md
from ..domain.errors import ParseError
from ..infrastructure.filesystem.paths import ProjectPaths
from .model import Chapter

ID_RE = re.compile(r"\b(?P<id>[A-Z]{2,10}-[0-9]{3,6})\b")

# 词典里名字太短会命中一切（「李」「王」）。两字起，且要求整词匹配。
MIN_NAME_LEN = 2
MAX_DICTIONARY = 5000


@dataclass
class Mention:
    kind: str  # id / name / alias
    value: str
    entity_id: str
    chapter_index: int
    locator: str
    quote: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "entity_id": self.entity_id,
            "chapter_index": self.chapter_index,
            "locator": self.locator,
            "quote": self.quote,
            "approved": False,
        }


@dataclass
class ChapterExtraction:
    chapter: Chapter
    mentions: list[Mention] = field(default_factory=list)

    @property
    def entity_ids(self) -> list[str]:
        return sorted({m.entity_id for m in self.mentions if m.entity_id})

    def as_dict(self) -> dict:
        return {
            "index": self.chapter.index,
            "title": self.chapter.title,
            "locator": self.chapter.locator,
            "mentions": [m.as_dict() for m in self.mentions],
            "entity_ids": self.entity_ids,
        }


def load_dictionary(pp: ProjectPaths) -> dict[str, str]:
    """从项目 Canon 读 `名称/别名 → ID`。读不出（缺 PyYAML、文件坏）的跳过，不抛。

    跳过而不是报错的理由：词典只是「帮作者省点眼力」的加速器，
    缺它照样能靠 ID 提及工作。为了词典而让整次导入失败，是把辅助当成了前置条件。
    """
    table: dict[str, str] = {}
    if not pp.canon.is_dir():
        return table
    for path in sorted(pp.canon.rglob("*.md")):
        try:
            doc = md.parse_bytes(path.read_bytes(), str(path))
        except (ParseError, OSError):
            continue
        entity_id = str(doc.meta.get("id") or "").strip()
        if not entity_id or "{{" in entity_id:
            continue
        name = str(doc.meta.get("name") or "").strip()
        if len(name) >= MIN_NAME_LEN:
            table.setdefault(name, entity_id)
        for alias in doc.meta.get("aliases") or []:
            text = str(alias).strip()
            if len(text) >= MIN_NAME_LEN:
                table.setdefault(text, entity_id)
        if len(table) >= MAX_DICTIONARY:
            return table
    return table


def extract_chapter(chapter: Chapter, dictionary: dict[str, str]) -> ChapterExtraction:
    """在一章里找提及。同一段落里同一个对象只记一次。"""
    result = ChapterExtraction(chapter=chapter)
    if not dictionary and not chapter.paragraphs:
        return result

    # 长名字优先，避免「李青莲」被「李青」先吃掉
    names = sorted(dictionary, key=len, reverse=True)
    for paragraph in chapter.paragraphs:
        text = paragraph.text
        seen: set[tuple[str, str]] = set()

        for match in ID_RE.finditer(text):
            entity_id = match.group("id")
            key = ("id", entity_id)
            if key in seen:
                continue
            seen.add(key)
            result.mentions.append(
                Mention(
                    kind="id",
                    value=entity_id,
                    entity_id=entity_id,
                    chapter_index=chapter.index,
                    locator=paragraph.locator,
                    quote=_quote(text, match.start(), match.end()),
                )
            )

        for name in names:
            start = text.find(name)
            if start < 0:
                continue
            entity_id = dictionary[name]
            kind = "name" if name == _primary_name(dictionary, entity_id, name) else "alias"
            key = (kind, entity_id)
            if key in seen:
                continue
            seen.add(key)
            result.mentions.append(
                Mention(
                    kind=kind,
                    value=name,
                    entity_id=entity_id,
                    chapter_index=chapter.index,
                    locator=paragraph.locator,
                    quote=_quote(text, start, start + len(name)),
                )
            )
    return result


def _primary_name(dictionary: dict[str, str], entity_id: str, name: str) -> str:
    """词典里第一个映射到该 ID 的名字当成主名，其余算别名。只为分类，不保证正确。"""
    for candidate, mapped in dictionary.items():
        if mapped == entity_id:
            return candidate
    return name


def _quote(text: str, start: int, end: int, width: int = 24) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    return " ".join(text[left:right].split())
