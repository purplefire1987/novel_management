"""索引查询。中文检索的三条路径照 PRD §16.2 分派。

| 输入 | 走哪条 | 为什么 |
|---|---|---|
| 像 ID / 别名 | 精确匹配 | `CHAR-0001` 用 FTS 去搜是错的，分词会把它拆碎 |
| ≥ 3 个字符 | FTS5 trigram | 中文没有空格，trigram 按三字滑窗建索引，能接住任意子串 |
| < 3 个字符 | 参数化 LIKE | trigram 最少要吃三个字；两个字的人名它检不出来 |

**LIKE 不是「兜底凑合」，是中文本来的必要路径**。两个字的人名、地名在库里
占绝大多数，只做 trigram 的话一半的查询会空手而归，而且看不出是「没有」
还是「检索不支持」。

FTS 不可用时（`fts_mode=none`）全部走 LIKE，并在结果里带 `note` 说明
「本次检索没有全文索引，性能受限」——不静默。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ....domain.errors import UsageError

TRIGRAM_MIN = 3
DEFAULT_LIMIT = 20
SNIPPET_RADIUS = 28

_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")

MODE_ID = "id"
MODE_FTS = "fts"
MODE_LIKE = "like"
MODE_NAME = "name"

# 实体名与别名只在 frontmatter 里，不进 blocks。实体名通常正文里也写着，
# 所以全文路径能命中；但有一分别名是描述性称呼（如「某家孙女」），正文里
# 一次都不出现，只搜正文永远捞不到那份档案。所以正文路径空手时，
# 再按名字/别名精确找一次，把该实体的正文块端出来。
_NAME_SQL = """
select e.id, e.path, e.project, e.name
from entities e
where e.name = ? collate nocase
   or e.id in (select entity_id from aliases where alias = ? collate nocase)
"""

_ROW_COLUMNS = "b.path, b.project, b.artifact_id, b.block_id, b.kind, b.text, b.line_start"

# 结果分层：作品内容 > 共享资产 > 系统文档与生成看板。
#
# 不加这个的话，`00_System/Dashboards/` 下由 `maps build` 生成的看板页会因为
# 路径字典序（`00_` < `10_` < `90_`）永远排在真正的档案前面。真实 Vault 上
# 搜一个两字人名，前三条全是 `工作台.md` 里的摘录，`10_Projects/` 下那份人物
# 档案被挤到看不见。
#
# 这不只是排序难看的问题：看板页是从档案**派生**出来的，让人先引用派生页，
# 就在索引里造了一个循环——查到的东西其实是别处的摘要。所以派生物永远排在源之后。
_TIER_SQL = """case
    when b.path like '10_Projects/%' then 0
    when b.path like '90_Shared_Assets/%' then 1
    else 2
end"""


@dataclass(frozen=True)
class Hit:
    path: str
    project: str
    artifact_id: str
    block_id: str
    kind: str
    snippet: str
    score: float = 0.0
    # 原文行号（1-based）。检索结果要能直接跳回 Markdown，光给块 ID 还得自己数。
    line_start: int = 0


@dataclass(frozen=True)
class SearchResult:
    query: str
    mode: str
    hits: tuple[Hit, ...] = field(default=())
    total: int = 0
    note: str = ""

    @property
    def found(self) -> bool:
        return bool(self.hits)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    if not db_path.exists():
        raise UsageError(f"索引不存在：{db_path}；先跑 `novel rebuild-index`")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def index_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("select key, value from index_metadata").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {row["key"]: row["value"] for row in rows}


def _like_pattern(query: str) -> str:
    """把用户输入转成 LIKE 模式。转义 `%` `_` `\\`，配 ESCAPE '\\'。"""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_query(query: str) -> str:
    """FTS5 短语查询：整体加引号，内部引号翻倍。"""
    return '"' + query.replace('"', '""') + '"'


def _snippet(text: str, query: str) -> str:
    lowered, needle = text.lower(), query.lower()
    at = lowered.find(needle)
    if at < 0:
        flat = " ".join(text.split())
        return flat[: SNIPPET_RADIUS * 2] + ("…" if len(flat) > SNIPPET_RADIUS * 2 else "")
    start = max(0, at - SNIPPET_RADIUS)
    end = min(len(text), at + len(query) + SNIPPET_RADIUS)
    piece = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def _rows_to_hits(rows, query: str) -> tuple[Hit, ...]:
    return tuple(
        Hit(
            path=row["path"],
            project=row["project"],
            artifact_id=row["artifact_id"],
            block_id=row["block_id"],
            kind=row["kind"],
            snippet=_snippet(row["text"], query),
            score=float(row["rank"]) if "rank" in row.keys() and row["rank"] is not None else 0.0,
            line_start=int(row["line_start"] or 0),
        )
        for row in rows
    )


def _project_clause(project: str | None) -> tuple[str, tuple]:
    """项目过滤条件。

    索引里的 `project` 是目录名（`p1_project`），而 CLI 给的 `--project` 是
    project_id（`p1`）。只做等值比较会让 `--project p1` 静默返回空结果——
    「查不到」和「过滤条件写错了」看起来一模一样，所以这里两种都认。
    """
    if not project:
        return "", ()
    return " and (b.project = ? or b.project like ? || '\\_%' escape '\\')", (project, project)


def _name_hits(
    conn: sqlite3.Connection, text: str, project: str | None, limit: int
) -> tuple[Hit, ...]:
    """按实体名/别名精确找，返回该实体所在产物的块。"""
    rows = conn.execute(_NAME_SQL, (text, text)).fetchall()
    hits: list[Hit] = []
    for row in rows:
        if project and row["project"] != project and not row["project"].startswith(project + "_"):
            continue
        blocks = conn.execute(
            f"select {_ROW_COLUMNS} from blocks b"
            " where b.project = ? and b.artifact_id = ? order by b.ordinal limit ?",
            (row["project"], row["id"], limit),
        ).fetchall()
        if blocks:
            hits.extend(_rows_to_hits(blocks, text))
        else:
            # 只有 frontmatter、没有正文的档案（多数设定档如此），
            # 也要给出可点的一行，而不是「没搜到」。
            hits.append(
                Hit(
                    path=row["path"],
                    project=row["project"],
                    artifact_id=row["id"],
                    block_id="",
                    kind="entity",
                    snippet=f"{row['name']}（名称命中，正文无此词）",
                )
            )
        if len(hits) >= limit:
            break
    return tuple(hits[:limit])


def _search_text(
    conn: sqlite3.Connection,
    text: str,
    *,
    limit: int,
    project: str | None,
) -> SearchResult:
    where_project, project_args = _project_clause(project)
    fts_mode = index_metadata(conn).get("fts_mode", "")

    # 1) 像 ID 的查询：直接对 artifacts / entities 精确命中
    if _ID_RE.match(text):
        rows = conn.execute(
            f"select {_ROW_COLUMNS} from blocks b"
            " where b.artifact_id = ? collate nocase"
            f"{where_project} order by b.ordinal limit ?",
            (text, *project_args, limit),
        ).fetchall()
        if rows:
            return SearchResult(query=text, mode=MODE_ID, hits=_rows_to_hits(rows, text), total=len(rows))
        # 只有 frontmatter、没有正文的档案（设定档多数如此）：ID 命中也要能返回
        art = conn.execute(
            "select id, project, path, title from artifacts"
            " where id = ? collate nocase"
            + (" and (project = ? or project like ? || '\\_%' escape '\\')" if project else "")
            + " limit 1",
            (text, *project_args),
        ).fetchone()
        if art:
            hit = Hit(
                path=art["path"],
                project=art["project"],
                artifact_id=art["id"],
                block_id="",
                kind="artifact",
                snippet=f"{art['title']}（ID 命中，该档案没有正文块）",
            )
            return SearchResult(query=text, mode=MODE_ID, hits=(hit,), total=1)

    # 2) 短查询：trigram 吃不下，走参数化 LIKE
    if len(text) < TRIGRAM_MIN or fts_mode != "trigram":
        pattern = _like_pattern(text)
        rows = conn.execute(
            f"select {_ROW_COLUMNS} from blocks b"
            " where b.text like ? escape '\\'"
            f"{where_project} order by {_TIER_SQL}, b.id limit ?",
            (pattern, *project_args, limit),
        ).fetchall()
        total = conn.execute(
            "select count(*) from blocks b where b.text like ? escape '\\'"
            f"{where_project}",
            (pattern, *project_args),
        ).fetchone()[0]
        note = ""
        if fts_mode != "trigram":
            note = "本次检索没有全文索引（FTS5 trigram 不可用），全部走子串扫描，大库上会慢"
        return SearchResult(
            query=text, mode=MODE_LIKE, hits=_rows_to_hits(rows, text), total=total, note=note
        )

    # 3) 其余：FTS5 trigram
    match = _fts_query(text)
    rows = conn.execute(
        f"select {_ROW_COLUMNS}, rank from fts_blocks f"
        " join blocks b on b.id = f.rowid"
        " where fts_blocks match ?"
        f"{where_project} order by {_TIER_SQL}, rank limit ?",
        (match, *project_args, limit),
    ).fetchall()
    total = conn.execute(
        "select count(*) from fts_blocks f join blocks b on b.id = f.rowid"
        " where fts_blocks match ?"
        f"{where_project}",
        (match, *project_args),
    ).fetchone()[0]
    return SearchResult(query=text, mode=MODE_FTS, hits=_rows_to_hits(rows, text), total=total)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    project: str | None = None,
) -> SearchResult:
    """先按正文查；正文空手时再按实体名/别名查。"""
    text = (query or "").strip()
    if not text:
        raise UsageError("查询不能为空")
    if limit < 1:
        raise UsageError(f"limit 必须为正数，收到 {limit}")

    result = _search_text(conn, text, limit=limit, project=project)
    if result.found:
        return result

    named = _name_hits(conn, text, project, limit)
    if named:
        return SearchResult(
            query=text,
            mode=MODE_NAME,
            hits=named,
            total=len(named),
            note="正文里没有这个词；命中的是实体名或别名",
        )
    return result



def open_and_search(db_path: Path, query: str, **kwargs) -> SearchResult:
    conn = connect(db_path)
    try:
        return search(conn, query, **kwargs)
    finally:
        conn.close()
