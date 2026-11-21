"""索引库 DDL 层：表齐全、FTS5 探测、退化路径、触发器同步。

这一层只管「建出来的库长什么样」，不碰 Markdown 解析与查询。
分出来是因为 builder 的测试要用真 Vault 骨架，跑得慢；这里的用例都是纯内存库。
"""

from __future__ import annotations

import sqlite3

import pytest

from novel_system.indexing import schema

# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


@pytest.fixture()
def conn():
    """内存库。沙箱里建库会撞 .sqlite-journal 拦截，内存库没有落盘文件。"""
    connection = sqlite3.connect(":memory:")
    connection.execute("pragma journal_mode = memory")
    yield connection
    connection.close()


@pytest.fixture()
def built(conn):
    """已按正常路径建好表的库。"""
    mode = schema.create_schema(conn)
    return conn, mode


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type in ('table','view')").fetchall()
    return {row[0] for row in rows}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'index'").fetchall()
    return {row[0] for row in rows}


# --------------------------------------------------------------------------
# 表清单
# --------------------------------------------------------------------------


def test_table_lists_cover_all_seventeen() -> None:
    """PRD §16.1 要 17 张表。两份清单必须正好拼满且不重叠。

    不写死「已填 8 / 空 9」：每过一个任务（T04 填语义表、T05 填问题与依赖）
    这个数字都会变，写死的结果是每次都要改测试，改着改着就把「清单漏表」
    这个真问题一起改掉了。这里只守结构：拼满 17、不重叠、不重复。
    """
    assert len(schema.POPULATED_TABLES) + len(schema.EMPTY_TABLES) == 17
    assert len(schema.ALL_TABLES) == 17
    assert len(set(schema.ALL_TABLES)) == 17, "两份清单有重叠或重复"
    assert set(schema.POPULATED_TABLES).isdisjoint(schema.EMPTY_TABLES)
    # 关键表必须在已填清单里：这几张是 T03/T04 的实际交付，掉了就是回归。
    # `issues` / `dependencies` 不在此列：问题的权威副本在 `<runtime>/issues/`，
    # 索引是「Vault + commit」的派生物，把 runtime 状态塞进来会让同一个 commit
    # 建出两个不同库（T03 验收「指定 commit 可独立构建」会变成假的）。
    for table in ("artifacts", "blocks", "events", "assertions", "knowledge", "revelations"):
        assert table in schema.POPULATED_TABLES, f"{table} 应已填"


def test_created_tables_match_declared_lists(built) -> None:
    """建出来的表必须与清单逐一对得上——多一张少一张都是清单在说谎。"""
    conn, _ = built
    actual = _tables(conn)
    missing = set(schema.ALL_TABLES) - actual
    assert not missing, f"清单里有但没建：{sorted(missing)}"
    # fts_blocks 是虚表，sqlite_master 里 type='table'，一定在
    assert "fts_blocks" in actual


def test_no_stray_schema_objects(built) -> None:
    """除了清单里的表与已知辅助表，不该有别的表。"""
    conn, _ = built
    allowed = set(schema.ALL_TABLES) | {"fts_blocks_data", "fts_blocks_idx", "fts_blocks_docsize",
                                        "fts_blocks_config", "sqlite_sequence"}
    stray = {name for name in _tables(conn) if name not in allowed and not name.startswith("sqlite_")}
    assert not stray, f"冒出了未声明的表：{sorted(stray)}"


def test_hot_path_indexes_exist(built) -> None:
    """查询热路径上的普通索引都得建出来，否则大库上会退化成全表扫。"""
    conn, _ = built
    names = _indexes(conn)
    for expected in (
        "idx_blocks_artifact",
        "idx_blocks_lookup",
        "idx_entities_name",
        "idx_aliases_alias",
        "idx_artifacts_path",
    ):
        assert expected in names, f"缺索引 {expected}"


# --------------------------------------------------------------------------
# FTS5 探测
# --------------------------------------------------------------------------


def test_detect_fts_reports_trigram_on_this_machine(conn) -> None:
    """本机 sqlite 支持 trigram，探测应报可用；不支持时这条会明确失败而不是静默跳过。"""
    available, reason = schema.detect_fts(conn)
    if not available:
        pytest.skip(f"本机 FTS5 trigram 不可用：{reason}")
    assert reason == ""


def test_detect_fts_leaves_no_probe_table(conn) -> None:
    """探测用的虚表必须清掉，否则每次建库都留一个垃圾表。"""
    schema.detect_fts(conn)
    assert "__fts_probe" not in _tables(conn)


def test_create_schema_returns_trigram_and_builds_fts(built) -> None:
    conn, mode = built
    if mode == schema.FTS_MODE_LIKE:
        pytest.skip("本机没有 FTS5 trigram，正常分支走不到")
    assert mode == schema.FTS_MODE_TRIGRAM
    assert "fts_blocks" in _tables(conn)
    # 退化时才建的那张表不该存在
    assert "fts_blocks_unavailable" not in _tables(conn)


def test_fts_uses_trigram_tokenizer(built) -> None:
    """不是 trigram 就等于对中文按整段切词——中文检索会静默失效。"""
    conn, mode = built
    if mode == schema.FTS_MODE_LIKE:
        pytest.skip("本机没有 FTS5 trigram")
    ddl = conn.execute(
        "select sql from sqlite_master where name = 'fts_blocks'"
    ).fetchone()[0]
    assert "trigram" in ddl.lower()
    assert "content='blocks'" in ddl or 'content="blocks"' in ddl


# --------------------------------------------------------------------------
# 退化路径
# --------------------------------------------------------------------------


def test_unavailable_fts_degrades_to_like(conn, monkeypatch) -> None:
    """探测说不可用时，建库不能装作 FTS 可用——要留下降级原因，且不建虚表。"""
    monkeypatch.setattr(schema, "detect_fts", lambda c: (False, "no such tokenizer: trigram"))
    mode = schema.create_schema(conn)

    assert mode == schema.FTS_MODE_LIKE
    tables = _tables(conn)
    assert "fts_blocks" not in tables, "FTS 不可用时不该建虚表"
    assert "fts_blocks_unavailable" in tables
    assert "trigram" in schema.fts_unavailable_reason(conn)


def test_fts_unavailable_reason_is_blank_when_fts_works(built) -> None:
    conn, mode = built
    if mode == schema.FTS_MODE_LIKE:
        pytest.skip("本机没有 FTS5 trigram")
    assert schema.fts_unavailable_reason(conn) == ""


def test_all_tables_created_even_without_fts(conn, monkeypatch) -> None:
    """降级只影响 fts_blocks，其余 16 张业务表必须照建，否则查询层会撞「表不存在」。"""
    monkeypatch.setattr(schema, "detect_fts", lambda c: (False, "boom"))
    schema.create_schema(conn)
    actual = _tables(conn)
    for name in schema.ALL_TABLES:
        if name == "fts_blocks":
            continue
        assert name in actual, f"降级时漏建了 {name}"


# --------------------------------------------------------------------------
# 触发器
# --------------------------------------------------------------------------


def _insert_block(conn: sqlite3.Connection, *, block_id: str, text: str, artifact: str = "CHAR-0001") -> None:
    conn.execute(
        "insert into blocks (project, artifact_id, path, block_id, ordinal, kind, text, line_start, line_end) "
        "values ('p1', ?, 'a.md', ?, 1, 'paragraph', ?, 1, 1)",
        (artifact, block_id, text),
    )


def test_trigger_feeds_fts_on_insert(built) -> None:
    """外部内容表靠触发器同步。没有它，全库能搜到 0 条，而且不会报错。"""
    conn, mode = built
    if mode == schema.FTS_MODE_LIKE:
        pytest.skip("本机没有 FTS5 trigram")

    _insert_block(conn, block_id="p0001", text="林砚握住了刀柄，指节发白。")
    conn.commit()

    total = conn.execute("select count(*) from fts_blocks").fetchone()[0]
    assert total == 1
    rows = conn.execute(
        "select rowid from fts_blocks where fts_blocks match ?", ('"握住了刀柄"',)
    ).fetchall()
    assert len(rows) == 1


def test_trigger_trigram_matches_two_char_chinese(built) -> None:
    """两字词能不能命中，取决于 tokenizer——trigram 下两字反而查不到，这是已知边界，
    查询层要用 LIKE 兜住。这里钉住 trigram 的行为，防止有人误以为两字也该走 FTS。
    """
    conn, mode = built
    if mode == schema.FTS_MODE_LIKE:
        pytest.skip("本机没有 FTS5 trigram")

    _insert_block(conn, block_id="p0001", text="林砚握住了刀柄。")
    conn.commit()

    long_hit = conn.execute(
        "select count(*) from fts_blocks where fts_blocks match ?", ('"握住了"',)
    ).fetchone()[0]
    assert long_hit == 1, "三字词在 trigram 下必须能命中"

    # 两字在 trigram 下切不出完整 token，命中数为 0 —— 正是查询层要绕开的情形
    short_hit = conn.execute(
        "select count(*) from fts_blocks where fts_blocks match ?", ('"林砚"',)
    ).fetchone()[0]
    assert short_hit == 0, "若这里变成 1，说明 tokenizer 不是 trigram，查询层的长短分流要重算"


def test_blocks_unique_key_rejects_duplicate_block_id(built) -> None:
    """同一产物内块 ID 唯一（PRD §9.1）。重号必须在这里就炸，不能带进库。"""
    conn, _ = built
    _insert_block(conn, block_id="p0001", text="甲")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_block(conn, block_id="p0001", text="乙")


def test_same_block_id_in_different_path_is_allowed(built) -> None:
    """块 ID 只在产物内唯一：两份档案都有 p0001 是正常的，不能被当成重号。"""
    conn, _ = built
    _insert_block(conn, block_id="p0001", text="甲")
    conn.execute(
        "insert into blocks (project, artifact_id, path, block_id, ordinal, kind, text) "
        "values ('p1', 'CHAR-0002', 'b.md', 'p0001', 1, 'paragraph', '乙')"
    )
    assert conn.execute("select count(*) from blocks").fetchone()[0] == 2


def test_metadata_is_key_value_store(built) -> None:
    conn, _ = built
    conn.execute("insert into index_metadata (key, value) values ('build_id', 'x')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("insert into index_metadata (key, value) values ('build_id', 'y')")


def test_schema_version_is_declared(built) -> None:
    """库里的 schema_version 必须能对上代码常量，否则将来迁移无从判断。"""
    conn, _ = built
    conn.execute("insert into index_metadata (key, value) values ('schema_version', ?)",
                 (str(schema.SCHEMA_VERSION),))
    row = conn.execute("select value from index_metadata where key = 'schema_version'").fetchone()
    assert int(row[0]) == schema.SCHEMA_VERSION >= 1
