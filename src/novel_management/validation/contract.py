"""发布前的契约校验：Schema / ID / 引用 / 依赖（AC-11／AC-12／AC-13／AC-49）。

`_validate_candidate` 原先只做四样——路径、字节、可解析、正式区归属。PRD 要的
另外四样一直没做，于是「发布」成了唯一一道只看形状、不看内容的闸。

## 这四条其实是同一件事

不是四个并列的检查，而是问同一个问题：**放进正式区的东西，能不能被解释**。

| 检查 | 它回答什么 | 对应 AC |
|---|---|---|
| Schema | 它的形状能不能被契约解释 | — |
| ID | 它的身份能不能被唯一解释 | AC-17 家族 |
| 引用 | 它指向的东西是否存在、引文是否还是那段字 | AC-12／AC-13 |
| 来源 | 正式断言有没有可定位的来源 | AC-11 |
| 依赖 | 它依赖的东西是否还成立 | AC-49 |

## 两条让步，各有理由

1. **只校验本次变更的产物，不扫全库。** 发布一次改一个字，不该等全库过一遍。
   全库的体检是 `validate` 与 `deps check` 的事。
2. **新增产物严格，既有产物对「历史形态」豁免。** 33 份素材库的 `source_refs`
   是裸路径，当时的 `import_id` / `file_sha256` 没落盘，事后补不出来（补就是编造）。
   这类形态只对 `op == "modify"` 放行；`op == "add"` 一律要求判别联合。
   规则写在这里而不是写在 schema 里，因为 schema 分不清「新写」和「旧有」。

## 没查的要说话

`NO_CONTRACT` 是 WARN 而不是静默跳过：库内 27 份产物的 type（`scene_beat`、
`chapter_plan`、`volume` 等）还没有契约。**没查不等于没问题**——静默跳过会让
「契约覆盖 998 份」变成一句看起来很好的谎。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..infrastructure.filesystem import markdown as md
from .. import schema as schema_mod
from novel_management.domain.ids import split_id
from ..infrastructure.filesystem.paths import ProjectPaths
from ..infrastructure.filesystem.storage import sha256_file

BLOCKER = "BLOCKER"
WARN = "WARN"

# type → 契约名。只映射「契约确实描述这类产物」的：
# chapter.schema.json 描述的是正文（stage: draft/final），不是章卡，故不映射 chapter_outline。
SCHEMA_BY_TYPE = {
    "character": "entity",
    "location": "entity",
    "faction": "entity",
    "organization": "entity",
    "item": "entity",
    "creature": "entity",
    "system": "entity",
    "rule": "entity",
    "world": "entity",
    "skill": "entity",
    "destiny": "entity",
    "story_asset": "entity",
    "event": "event",
    "foreshadowing": "foreshadowing",
    # 正文两个 type 值共存：`chapter` 是 novel write 的产出，`manuscript` 是
    # 2026-11-04 批量导入的 28 份旧稿。它们描述的是同一种产物（正文），
    # 所以共映射到 chapter.schema.json，而不是为旧稿另造一份契约——
    # 另造一份等于承认两套语义永久并存。
    "chapter": "chapter",
    "manuscript": "chapter",
}

# PRD §9 定义的「正式断言」类产物：它们必须有可定位来源（AC-11）。
ASSERTION_TYPES = {"assertion", "state_change", "knowledge", "revelation"}

# 模板不是产物，不参与契约校验。
TEMPLATE_MARKERS = ("Templates", "模板")

_WIKILINK = re.compile(r"\[\[([^\]\|]+?)(?:\\?\|[^\]]*)?\]\]")
_BLOCK_ANCHOR = re.compile(r"\[\[([^\]\|#]+)#(p[0-9]{3,5})\]\]")


@dataclass
class Issue:
    level: str
    code: str
    path: str
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "path": self.path, "message": self.message}


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    checked: int = 0
    no_contract: list[str] = field(default_factory=list)

    @property
    def blockers(self) -> list[Issue]:
        return [i for i in self.issues if i.level == BLOCKER]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARN]

    def raise_if_blocked(self) -> None:
        if not self.blockers:
            return
        from ..domain.errors import SecurityError

        detail = "；".join(f"[{i.code}] {i.message}" for i in self.blockers[:5])
        more = f"（共 {len(self.blockers)} 项）" if len(self.blockers) > 5 else ""
        raise SecurityError(f"发布前契约校验未通过：{detail}{more}")


def _is_template(rel: str) -> bool:
    return any(marker in rel for marker in TEMPLATE_MARKERS)


def _link_text(text: str) -> str:
    """排除代码区里的双链字面量——与 `validate` 同一口径。"""
    text = re.sub(r"```.*?```", lambda m: " " * len(m.group(0)), text, flags=re.S)
    return re.sub(r"`[^`\n]*`", lambda m: " " * len(m.group(0)), text)


def _iter_sources(meta: dict):
    """产出 (来源项, 是否裸路径)。裸路径是遗留形态，只有既有产物才豁免。"""
    refs = meta.get("source_refs")
    if not isinstance(refs, list):
        return
    for item in refs:
        if isinstance(item, str):
            yield item, True
        elif isinstance(item, dict):
            yield item, False


def check(
    pp: ProjectPaths,
    changes,
    files_dir: Path,
    known_ids: set[str] | None = None,
) -> Report:
    """对本次发布的变更做契约校验。

    `files_dir` 是候选区（`.../files/`），里面是「将要成为正式区」的字节。
    """
    report = Report()
    known_ids = set(known_ids or ())

    # 计划内 ID 索引：本次要落盘的每份产物。
    plan_ids: dict[str, str] = {}
    metas: dict[str, dict] = {}

    for change in changes:
        if change.op == "delete":
            continue
        rel = change.path
        staged = files_dir / rel
        if not staged.exists():
            continue  # 第 6 步已拦过，这里不重复报
        report.checked += 1

        if _is_template(rel):
            continue

        try:
            doc = md.parse_bytes(staged.read_bytes(), rel)
        except Exception:  # noqa: BLE001 - 解析失败由第 6 步负责
            continue
        meta = doc.meta or {}
        metas[rel] = meta
        typ = str(meta.get("type") or "").strip()
        eid = str(meta.get("id") or "").strip()

        # ---- ID 形态与计划内唯一 ----
        if eid:
            if not split_id(eid):
                report.issues.append(
                    Issue(BLOCKER, "ID_SHAPE", rel, f"ID 形态不合规（PRD §8.1）：{eid}")
                )
            elif eid in plan_ids:
                report.issues.append(
                    Issue(
                        BLOCKER,
                        "ID_DUP",
                        rel,
                        f"本次发布里 {eid} 出现两次（另一份是 {plan_ids[eid]}）",
                    )
                )
            else:
                plan_ids[eid] = rel

        # ---- Schema ----
        schema_name = SCHEMA_BY_TYPE.get(typ)
        if not schema_name:
            report.no_contract.append(f"{rel}（type={typ or '?'}）")
        else:
            errors = schema_mod.check(schema_name, meta)
            for err in errors:
                report.issues.append(Issue(BLOCKER, "SCHEMA_INVALID", rel, err))

        # ---- 来源（AC-11 / AC-13）----
        sources = list(_iter_sources(meta))
        if typ in ASSERTION_TYPES and not sources:
            report.issues.append(
                Issue(BLOCKER, "SOURCE_MISSING", rel, f"{typ} 必须带可定位来源（AC-11）")
            )
        for item, bare in sources:
            if bare:
                if change.op == "add":
                    report.issues.append(
                        Issue(
                            BLOCKER,
                            "SOURCE_BARE_PATH",
                            rel,
                            f"新增产物不得用裸路径来源：{item}",
                        )
                    )
                else:
                    report.issues.append(
                        Issue(WARN, "SOURCE_BARE_PATH", rel, f"遗留裸路径来源（既有产物豁免）：{item}")
                    )
                if not (pp.vault / str(item)).exists() and not (pp.root / str(item)).exists():
                    report.issues.append(
                        Issue(BLOCKER, "SOURCE_NOT_FOUND", rel, f"来源路径不存在（AC-12）：{item}")
                    )
                continue

            kind = str(item.get("kind") or "")
            if kind == "CANON":
                target = str(item.get("artifact_id") or "")
                if target and target not in known_ids and target not in plan_ids:
                    report.issues.append(
                        Issue(
                            BLOCKER,
                            "SOURCE_NOT_FOUND",
                            rel,
                            f"来源产物不存在（AC-12）：{target}",
                        )
                    )
            elif kind == "RAW":
                digest = str(item.get("file_sha256") or "")
                locator = str(item.get("locator") or "")
                if digest and locator.startswith("file:"):
                    raw_rel = locator[len("file:") :]
                    raw_path = pp.vault / raw_rel
                    if not raw_path.exists():
                        report.issues.append(
                            Issue(BLOCKER, "SOURCE_NOT_FOUND", rel, f"原始来源不存在（AC-12）：{raw_rel}")
                        )
                    elif sha256_file(raw_path) != digest:
                        report.issues.append(
                            Issue(
                                BLOCKER,
                                "SOURCE_HASH_MISMATCH",
                                rel,
                                f"原始来源字节已变，引文可能不再成立（AC-13）：{raw_rel}",
                            )
                        )

    # ---- 双链引用（AC-12）----
    stems = _stems(pp, files_dir)
    for rel, meta in metas.items():
        staged = files_dir / rel
        try:
            text = staged.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _WIKILINK.finditer(_link_text(text)):
            target = match.group(1).strip()
            if "#" in target:
                target = target.split("#", 1)[0].strip()
            if not target or target.startswith("^") or "..." in target:
                continue
            if target.startswith("10_Projects/"):
                if not target.startswith(f"10_Projects/{pp.root.name}/"):
                    report.issues.append(
                        Issue(BLOCKER, "REF_CROSS_PROJECT", rel, f"跨项目引用：{target}")
                    )
                    continue
            if not _resolves(target, pp, stems):
                report.issues.append(
                    Issue(BLOCKER, "REF_BROKEN", rel, f"双链目标不存在（AC-12）：{target}")
                )

    # ---- 删除的块仍被引用（AC-49）----
    removed = [c.path for c in changes if c.op == "delete"]
    if removed:
        report.issues.extend(_check_removed_blocks(pp, changes, files_dir, removed))

    return report


def _stems(pp: ProjectPaths, files_dir: Path) -> set[str]:
    out: set[str] = set()
    for base in (pp.root, files_dir):
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            rel = path.relative_to(base).as_posix()
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if path.is_file():
                out.add(path.stem)
    return out


def _resolves(target: str, pp: ProjectPaths, stems: set[str]) -> bool:
    raw = target.strip()
    candidates = [raw]
    if not PurePosixPath(raw).suffix:
        candidates.append(raw + ".md")
    for cand in candidates:
        for base in (pp.root, pp.vault):
            if (base / cand).exists():
                return True
    return PurePosixPath(raw).stem in stems


def _check_removed_blocks(pp: ProjectPaths, changes, files_dir: Path, removed: list[str]):
    """AC-49：被删文件里的块，别处还在引用吗。

    块引用只在同文件内稳定（`p0001` 是「这份文件当时的第 1 块」），跨文件引用
    必须带文件名：`[[某文件#p0001]]`。所以查法是「谁引用了被删文件 + 块锚点」。
    """
    issues: list[Issue] = []
    removed_stems = {PurePosixPath(r).stem for r in removed}

    anchors: dict[str, set[str]] = {}
    for rel in removed:
        src = pp.at(rel)
        if not src.exists():
            continue
        try:
            doc = md.parse_bytes(src.read_bytes(), rel)
        except Exception:  # noqa: BLE001
            continue
        anchors[rel] = {b.block_id for b in doc.blocks}

    if not anchors:
        return issues

    for path in files_dir.rglob("*.md"):
        rel = path.relative_to(files_dir).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _BLOCK_ANCHOR.finditer(_link_text(text)):
            target_file, block_id = match.group(1).strip(), match.group(2)
            if PurePosixPath(target_file).stem not in removed_stems:
                continue
            for removed_rel, ids in anchors.items():
                if PurePosixPath(removed_rel).stem == PurePosixPath(target_file).stem and block_id in ids:
                    issues.append(
                        Issue(
                            BLOCKER,
                            "BLOCK_REMOVED_BUT_REFERENCED",
                            rel,
                            f"被删的块仍被引用（AC-49）：{target_file}#{block_id}",
                        )
                    )
    return issues


__all__ = [
    "ASSERTION_TYPES",
    "BLOCKER",
    "WARN",
    "Issue",
    "Report",
    "SCHEMA_BY_TYPE",
    "check",
]
