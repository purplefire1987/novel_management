"""影响分析（基础能力）：一次变更会碰到哪些已存在的东西。

做法是**反查索引**，不是猜。五类查询对应 PRD §19.2 要求填的几个「影响 X」：

| 报告字段 | 数据来源 | 回答什么 |
|---|---|---|
| `affected_assertions` / `state_changes` | 语义表 | 哪条事实要改 |
| `affected_knowledge` | `knowledge` | 谁的认知要跟着改 |
| `affected_revelations` | `revelations` | 读者的认知在第几章哪一块被建立 |
| `affected_chapters` | `blocks` + `artifacts` | 哪几章正文会因此变成错的 |
| `affected_planning` | `artifacts` 的 `02_Planning` 段 | 哪些规划与新事实冲突 |

`dependencies` 表本版是空的（T07 才填），如实报 0 并说明，不假装做过依赖分析。
报告里所有条数都是**现查的**，不是从小结里抄的。

## 正文匹配按「名字」而不是只按 ID

正文里写的是「张三走进驿馆」，不会写 `CHAR-0001`。只扫 ID 的话
`affected_chapters` 在真库上永远是空集，RETCON 那道「有受影响正文就必须承诺改写」
的闸门就等于常开放行——**闸门看起来关着，实际从来没关过**。

所以扫描词表 = 目标 ID + 该实体的 `name` + 全部 `aliases`。名字短于 2 字的不收
（单字名会命中一切），词表上限 32 个（防止有人把整段别名表塞进来拖慢全表扫描）。
每条 mention 记下命中的是哪个词、是 ID 还是名字，便于人工核对是不是误命中。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from novel_management.domain.errors import UsageError

PLANNING_PREFIX = "03_故事规划"
MANUSCRIPT_PREFIX = "04_正文"
SAMPLE_LIMIT = 20


@dataclass
class Impact:
    change_id: str
    targets: tuple[str, ...] = ()
    counts: dict = field(default_factory=dict)
    affected_chapters: tuple[str, ...] = ()
    affected_planning: tuple[str, ...] = ()
    affected_assertions: tuple[str, ...] = ()
    affected_state_changes: tuple[str, ...] = ()
    affected_knowledge: tuple[str, ...] = ()
    affected_revelations: tuple[str, ...] = ()
    affected_blocks: tuple[str, ...] = ()
    mentions: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()

    def as_report(self) -> dict:
        """给变更请求存的那份报告。字段名稳定，哈希才对得上。"""
        return {
            "change_id": self.change_id,
            "targets": list(self.targets),
            "counts": dict(self.counts),
            "affected_chapters": list(self.affected_chapters),
            "affected_planning": list(self.affected_planning),
            "affected_assertions": list(self.affected_assertions),
            "affected_state_changes": list(self.affected_state_changes),
            "affected_knowledge": list(self.affected_knowledge),
            "affected_revelations": list(self.affected_revelations),
            "affected_blocks": list(self.affected_blocks),
            "mentions": [dict(item) for item in self.mentions],
            "notes": list(self.notes),
        }


MIN_TERM_LEN = 2
MAX_TERMS = 32


def _like_escape(term: str) -> str:
    """`LIKE` 的元字符转义。名字里带 `%` 或 `_` 时不转义会变成通配。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _match_terms(conn, ids: list[str]) -> list[tuple[str, str, str]]:
    """返回 [(term, kind, target_id)]：ID 本身，加上实体的 name 与别名。

    `kind` 取 `id` / `name` / `alias`，写进 mention，便于判断这次命中是不是误伤。
    """
    terms: list[tuple[str, str, str]] = [(t, "id", t) for t in ids]
    placeholders = ",".join("?" for _ in ids)
    args = tuple(ids)
    rows = conn.execute(
        f"select id, name from entities where id in ({placeholders})", args
    ).fetchall()
    for entity_id, name in rows:
        name = str(name or "").strip()
        if len(name) >= MIN_TERM_LEN:
            terms.append((name, "name", str(entity_id)))
    rows = conn.execute(
        f"select entity_id, alias from aliases where entity_id in ({placeholders})", args
    ).fetchall()
    for entity_id, alias in rows:
        alias = str(alias or "").strip()
        if len(alias) >= MIN_TERM_LEN:
            terms.append((alias, "alias", str(entity_id)))

    # 去重（同一实体的 name 可能又被写成别名），并守住词表上限。
    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for term in terms:
        if term[0] in seen:
            continue
        seen.add(term[0])
        unique.append(term)
    return unique[:MAX_TERMS]


def _ids(conn, sql: str, args: tuple) -> list[str]:
    return sorted({str(row[0]) for row in conn.execute(sql, args).fetchall() if row[0]})


def analyse(conn, *, change_id: str, targets: list[str]) -> Impact:
    """按目标 ID 反查索引，生成影响报告。

    索引不存在或为空时不编数字：查不到就是查不到，并在 notes 里说明。
    """
    ids = [t for t in dict.fromkeys(targets) if t]
    if not ids:
        raise UsageError("变更没有目标 ID，无法做影响分析")

    report = Impact(change_id=change_id, targets=tuple(ids))
    placeholders = ",".join("?" for _ in ids)
    args = tuple(ids)

    assertions = _ids(
        conn, f"select id from assertions where subject_id in ({placeholders})", args
    )
    changes = _ids(
        conn, f"select id from state_changes where entity_id in ({placeholders})", args
    )
    knowledge = _ids(
        conn,
        f"select id from knowledge where subject_id in ({placeholders}) or holder_id in ({placeholders})",
        args + args,
    )
    if assertions:
        ph = ",".join("?" for _ in assertions)
        revelations = _ids(
            conn,
            f"select id from revelations where proposition_ref in ({ph})",
            tuple(assertions),
        )
    else:
        revelations = []
    events = _ids(conn, f"select id from events where id in ({placeholders})", args)

    # 正文与规划里的「提及」：块文本里出现目标 ID，或出现该实体的名字/别名。
    # 只扫 ID 会漏掉全部真实正文（正文写人名不写 ID），见模块头的说明。
    terms = _match_terms(conn, ids)
    conditions = " or ".join("b.text like ? escape '\\'" for _ in terms)
    blocks = conn.execute(
        "select b.id, b.block_id, b.artifact_id, a.path, a.artifact_type, b.text"
        " from blocks b left join artifacts a"
        "   on a.id = b.artifact_id and a.project = b.project"
        f" where {conditions}",
        tuple(f"%{_like_escape(term)}%" for term, _kind, _tid in terms),
    ).fetchall()

    chapters: list[str] = []
    planning: list[str] = []
    block_ids: list[str] = []
    mentions: list[dict] = []
    matched_terms: set[str] = set()
    seen_rows: set[int] = set()
    for row_id, block_id, artifact_id, path, artifact_type, text in blocks:
        # 同一个块可能同时命中 name 与 alias：`where` 会把它返回两次，只记一次。
        # 去重键必须是**行主键**：`block_id`（`p0001`）只在单份产物内唯一，
        # 两章都有 `p0001`，按它去重会把后一章整章吞掉。
        if row_id in seen_rows:
            continue
        seen_rows.add(row_id)
        block_ids.append(str(block_id))
        path = str(path or "")
        text = str(text or "")
        hits = [
            {"term": term, "via": kind, "target": tid}
            for term, kind, tid in terms
            if term in text
        ]
        matched_terms.update(hit["term"] for hit in hits)
        # 先看目录再看 frontmatter：规划目录里也可能有 `type: chapter` 的文档
        # （分卷章卡就是这样），按 artifact_type 分类会把规划算成正文，
        # 于是 RETCON 的「受影响正文」里混进还没写的章节。
        is_planning = path.startswith(PLANNING_PREFIX)
        is_manuscript = path.startswith(MANUSCRIPT_PREFIX) or (
            not is_planning and artifact_type in ("chapter", "manuscript")
        )
        if is_manuscript:
            if artifact_id and str(artifact_id) not in chapters:
                chapters.append(str(artifact_id))
        elif is_planning:
            if artifact_id and str(artifact_id) not in planning:
                planning.append(str(artifact_id))
        if len(mentions) < SAMPLE_LIMIT:
            mentions.append(
                {
                    "block_id": block_id,
                    "artifact_id": artifact_id,
                    "path": path,
                    "matched": hits[:3],
                }
            )

    notes: list[str] = []
    deps = 0
    try:
        deps = conn.execute("select count(*) from dependencies").fetchone()[0]
    except Exception:  # pragma: no cover - 表一定在，兜底只为不因统计失败中断报告
        deps = 0
    if deps == 0:
        notes.append("dependencies 表本版为空（T07 之后才会生成依赖），本次未做依赖闭包分析")

    name_terms = [term for term, kind, _ in terms if kind != "id"]
    if not name_terms:
        notes.append(
            "目标在索引里没有实体档案（或名字不足 2 字），正文只按 ID 匹配——"
            "affected_chapters 可能偏少，请人工确认"
        )

    report.counts = {
        "assertions": len(assertions),
        "state_changes": len(changes),
        "knowledge": len(knowledge),
        "revelations": len(revelations),
        "events": len(events),
        "blocks": len(block_ids),
        "chapters": len(chapters),
        "planning": len(planning),
        "dependencies": deps,
    }
    report.affected_assertions = tuple(assertions)
    report.affected_state_changes = tuple(changes)
    report.affected_knowledge = tuple(knowledge)
    report.affected_revelations = tuple(revelations)
    report.affected_chapters = tuple(chapters)
    report.affected_planning = tuple(planning)
    report.affected_blocks = tuple(block_ids)
    report.mentions = tuple(mentions)
    report.counts["matched_terms"] = len(matched_terms)
    report.notes = tuple(notes)
    return report
