"""中文检索：短词、长词、英文、混合、ID、别名。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from novel_management.cli import main
from novel_management.indexing import WorktreeSource, build_index, connect, search
from novel_management.indexing.search import MODE_FTS, MODE_ID, MODE_LIKE, MODE_NAME

CHAR1 = """\
---
id: CHAR-0001
type: character
name: 林砚
aliases:
  - 林先生
---

雨落在灰石渡的石阶上，抄书人把最后一页纸压进书匣。

他确信那个人已经死了。
"""

CHAR2 = """\
---
id: CHAR-0002
type: character
name: 周决
---

The river runs past 灰石渡 at dawn.
"""

LOC = """\
---
id: LOC-0001
type: location
name: 灰石渡
---
"""


@pytest.fixture()
def index(tmp_path: Path):
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    assert (
        main(
            [
                "init-project",
                "--vault",
                str(vault),
                "--runtime",
                str(runtime),
                "--id",
                "p1",
                "--name",
                "测试",
                "--confirm",
            ]
        )
        == 0
    )
    root = sorted((vault / "10_Projects").glob("p1*"))[0]
    canon = root / "01_Canon"
    for name, text in (("CHAR-0001.md", CHAR1), ("CHAR-0002.md", CHAR2)):
        (canon / "Characters").mkdir(parents=True, exist_ok=True)
        (canon / "Characters" / name).write_text(text, encoding="utf-8")
    (canon / "Locations").mkdir(parents=True, exist_ok=True)
    (canon / "Locations" / "LOC-0001.md").write_text(LOC, encoding="utf-8")

    directory = runtime / "index"
    result = build_index(
        WorktreeSource(vault, revision="worktree"),
        directory,
        manifest_path=directory / "index.json",
        pointer_path=directory / "active.json",
    )
    return result.db_path


def _search(db: Path, query: str, **kwargs):
    conn = connect(db)
    try:
        return search(conn, query, **kwargs)
    finally:
        conn.close()


def test_two_char_chinese_uses_like(index: Path) -> None:
    """trigram 最少吃三个字，两个字必须走子串，否则人名地名全检不出来。"""
    result = _search(index, "灰石")
    assert result.mode == MODE_LIKE
    assert result.found
    assert all("灰石" in hit.snippet for hit in result.hits)


def test_three_char_chinese_uses_fts(index: Path) -> None:
    result = _search(index, "灰石渡")
    assert result.mode == MODE_FTS
    assert result.total >= 2  # 林砚的正文一次、周决的正文一次


def test_four_char_chinese_uses_fts(index: Path) -> None:
    result = _search(index, "已经死了")
    assert result.mode == MODE_FTS
    assert result.hits[0].artifact_id == "CHAR-0001"
    assert result.hits[0].block_id == "p0002"


def test_english_word_uses_fts(index: Path) -> None:
    result = _search(index, "river")
    assert result.mode == MODE_FTS
    assert result.hits[0].artifact_id == "CHAR-0002"


def test_mixed_chinese_english(index: Path) -> None:
    result = _search(index, "灰石渡 at dawn")
    assert result.mode == MODE_FTS
    assert result.found


def test_id_is_exact_match(index: Path) -> None:
    result = _search(index, "CHAR-0002")
    assert result.mode == MODE_ID
    assert result.hits[0].artifact_id == "CHAR-0002"


def test_entity_without_body_still_returns_a_hit(index: Path) -> None:
    result = _search(index, "LOC-0001")
    assert result.mode == MODE_ID
    assert result.hits[0].block_id == ""
    assert "没有正文块" in result.hits[0].snippet


def test_name_is_found_even_though_it_is_not_in_the_body(index: Path) -> None:
    result = _search(index, "林砚")
    assert result.mode == MODE_NAME
    assert result.hits[0].artifact_id == "CHAR-0001"


def test_alias_is_found(index: Path) -> None:
    result = _search(index, "林先生")
    assert result.mode == MODE_NAME
    assert result.hits[0].artifact_id == "CHAR-0001"


def test_body_wins_over_name(index: Path) -> None:
    """正文里能搜到时，不要退回名称路径——用户要的是出现位置。"""
    result = _search(index, "灰石渡")
    assert result.mode == MODE_FTS


def test_no_hit_is_empty_not_error(index: Path) -> None:
    result = _search(index, "绝对不存在的词组")
    assert result.found is False
    assert result.total == 0


def test_limit_is_respected(index: Path) -> None:
    result = _search(index, "灰石渡", limit=1)
    assert len(result.hits) == 1
    assert result.total >= 2  # total 报真实总数，不受 limit 影响


def test_empty_query_is_usage_error(index: Path) -> None:
    from novel_management.errors import UsageError

    with pytest.raises(UsageError):
        _search(index, "   ")


def test_project_filter(index: Path) -> None:
    assert _search(index, "灰石渡", project="p1").found
    assert not _search(index, "灰石渡", project="不存在的项目").found


def test_without_fts_long_query_falls_back_to_like(index: Path) -> None:
    """FTS 不可用时要退到子串扫描，并明确说明，而不是「查不到」。"""
    conn = sqlite3.connect(index)
    # 沙箱会拦 .sqlite-journal 的创建，重开库时必须显式用 MEMORY
    conn.execute("pragma journal_mode = memory")
    conn.execute("update index_metadata set value='none' where key='fts_mode'")
    conn.commit()
    conn.close()

    result = _search(index, "已经死了")
    assert result.mode == MODE_LIKE
    assert result.found
    assert "全文索引" in result.note


def test_like_escapes_wildcards(index: Path) -> None:
    """`%` 是 LIKE 的通配符，用户输入里出现时不能被当通配符。"""
    result = _search(index, "%")
    assert result.total == 0, "`%` 被当成通配符了，匹配到了所有块"


# --------------------------------------------------------------------------
# 结果分层：派生物不能排在事实源前面
# --------------------------------------------------------------------------


def test_project_content_ranks_before_generated_dashboard(tmp_path: Path) -> None:
    """生成的看板页不能排在真正的档案前面。

    真实库上搜一个两字人名时，`00_System/Dashboards/工作台.md` 曾因为路径字典序
    （`00_` < `10_`）霸占前三条，`10_Projects/` 下那份人物档案被挤到看不见。
    看板是从档案派生的，让人先引用派生页等于在索引里造循环。
    """
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    assert (
        main(["init-project", "--vault", str(vault), "--runtime", str(runtime),
              "--id", "p1", "--name", "测试", "--confirm"]) == 0
    )
    root = sorted((vault / "10_Projects").glob("p1*"))[0]
    (root / "01_Canon" / "Characters").mkdir(parents=True, exist_ok=True)
    # 正文里要真的出现这个词，否则两字查询只会命中看板那一处，
    # 排序根本没机会参与——那样测的是「名字路径」而不是「分层」。
    (root / "01_Canon" / "Characters" / "CHAR-0001.md").write_text(
        "---\nid: CHAR-0001\ntype: character\nname: 林砚\n---\n\n林砚把刀横在膝上。\n",
        encoding="utf-8",
    )

    dash = vault / "00_System" / "Dashboards" / "工作台.md"
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text(
        "---\ntype: dashboard\ntitle: 工作台\n---\n\n# 工作台\n\n林砚 这一栏是看板摘要。\n",
        encoding="utf-8",
    )

    directory = runtime / "index"
    result = build_index(
        WorktreeSource(vault, revision="worktree"),
        directory,
        manifest_path=directory / "index.json",
        pointer_path=directory / "active.json",
    )

    hits = _search(result.db_path, "林砚").hits
    assert len(hits) >= 2, f"该同时命中档案与看板，实际 {[h.path for h in hits]}"
    assert hits[0].path.startswith("10_Projects/"), f"第一条是 {hits[0].path}，派生物排到了前面"
    paths = [hit.path for hit in hits]
    assert any(p.startswith("00_System/Dashboards/") for p in paths), "看板页仍应出现在结果里，只是靠后"


def test_hit_carries_line_number(index: Path) -> None:
    """检索结果要给原文行号。

    只给块 ID（`p0003`）的话，作者还得自己数到第三段；行号能直接跳。
    块 ID 与行号是两套坐标，缺一个都不好用。
    """
    result = _search(index, "灰石渡", limit=1)
    assert result.found
    hit = result.hits[0]
    assert hit.block_id.startswith("p")
    assert hit.line_start > 0, "行号没带上"

    # 与库里的记录对得上，才算「这个行号属于这个块」而不是随手填的数
    conn = sqlite3.connect(index)
    row = conn.execute(
        "select line_start from blocks where path = ? and block_id = ?",
        (hit.path, hit.block_id),
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == hit.line_start
