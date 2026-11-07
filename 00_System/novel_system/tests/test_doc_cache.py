"""持久解析缓存：第二次跑不该再读盘，文件改了必须重读。

为什么值得单独测：这个缓存的全部价值是「跳过 20ms 一次的读盘」，而它唯一
的风险正好相反——**读到过期内容**。两个方向都得钉住，否则要么白做，要么
让人闭嘴不报错。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from novel_system import vault
from novel_system.config import Config
from novel_system.validators import integrity as I

DOC = """---
id: CHAR-0001
type: 人物
name: 甲
---

# 甲
"""


def _seed(root: Path, count: int = 3) -> list[Path]:
    paths = []
    for i in range(count):
        path = root / f"01_Core_Memory/Characters/CHAR-{i:04d}_角色{i}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DOC.replace("CHAR-0001", f"CHAR-{i:04d}").replace("甲", f"角色{i}"), encoding="utf-8")
        paths.append(path)
    return paths


def _read_all(paths: list[Path]) -> list[str]:
    return [vault.read_doc(p).body for p in paths]


def test_second_run_reads_nothing(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    store = tmp_path / vault.CACHE_FILE

    with vault.cached_docs(store) as cache:
        first = _read_all(paths)
        assert cache.misses == 3, "首次必须真的读盘"
        assert store.exists() is False, "读盘还没结束，不该提前落盘"

    with vault.cached_docs(store) as cache:
        second = _read_all(paths)
        assert cache.misses == 0, "指纹没变，不该再读盘"
        assert cache.hits == 3

    assert first == second, "缓存内容必须与直读一致"


def test_changed_file_is_reread(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    store = tmp_path / vault.CACHE_FILE
    with vault.cached_docs(store):
        _read_all(paths)

    # 改一份，并把 mtime 拨到下一秒（同一秒内写盘时 mtime_ns 也会变，
    # 但显式拨开一点，免得依赖文件系统的时间精度）
    paths[1].write_text(DOC.replace("CHAR-0001", "CHAR-0001").replace("甲", "改过"), encoding="utf-8")
    stat = paths[1].stat()
    os.utime(paths[1], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    with vault.cached_docs(store) as cache:
        bodies = _read_all(paths)
        assert cache.misses == 1, "只该重读被改的那一份"
        assert "改过" in bodies[1]


def test_file_hash_matches_disk_with_and_without_cache(tmp_path: Path) -> None:
    """审计用的 `file_hash(use_cache=True)` 不许比直读更旧。

    记忆落盘后的回归审计要对全库上千份实体各算一次哈希，所以它开了缓存；
    但缓存一旦给出过期内容，「索引与文件不一致」这条告警就会被吞掉——
    而那正是审计唯一的职责。冷缓存、命中、外部改动三种状态都得与直读对齐。
    """
    paths = _seed(tmp_path)
    store = tmp_path / vault.MIRROR_FILE
    with vault.cached_docs(store):
        direct = [vault.file_hash(p) for p in paths]
        assert [vault.file_hash(p, use_cache=True) for p in paths] == direct, "冷缓存应与直读一致"

        _read_all(paths)  # 填缓存
        assert [vault.file_hash(p, use_cache=True) for p in paths] == direct, "命中缓存应与直读一致"

        paths[0].write_text(DOC.replace("CHAR-0001", "CHAR-0000").replace("甲", "换过"), encoding="utf-8")
        stat = paths[0].stat()
        os.utime(paths[0], ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        current = vault.file_hash(paths[0], use_cache=True)
        assert current == vault.file_hash(paths[0]), "缓存失效后应与直读一致"
        assert current != direct[0], "外部改过的文件不能给出旧哈希"

    assert vault.file_hash(tmp_path / "缺.md", use_cache=True) == "", "缺文件仍返回空串"


def test_version_mismatch_is_ignored(tmp_path: Path) -> None:
    paths = _seed(tmp_path, count=1)
    store = tmp_path / vault.MIRROR_FILE
    with vault.cached_docs(store):
        _read_all(paths)

    # 把版本号改高一级：格式变了，旧镜像一律不认
    db = sqlite3.connect(str(store))
    try:
        db.execute("UPDATE meta SET v=? WHERE k='version'", (str(vault.CACHE_VERSION + 1),))
        db.commit()
    finally:
        db.close()

    with vault.cached_docs(store) as cache:
        _read_all(paths)
        assert cache.misses == 1, "版本对不上就当没缓存，必须直读"


def test_deleted_file_is_pruned(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    store = tmp_path / vault.MIRROR_FILE
    with vault.cached_docs(store):
        _read_all(paths)

    paths[2].unlink()
    with vault.cached_docs(store):
        _read_all(paths[:2])

    db = sqlite3.connect(str(store))
    try:
        rels = {row[0] for row in db.execute("SELECT rel FROM doc")}
    finally:
        db.close()
    assert paths[2].relative_to(tmp_path).as_posix() not in rels, "删掉的文件不该留在镜像里"
    assert paths[0].relative_to(tmp_path).as_posix() in rels


def test_mirror_stores_relative_paths(tmp_path: Path) -> None:
    """镜像按 Vault 根存相对路径——整棵树搬走以后还能继续用，绝对路径会全指空。"""
    paths = _seed(tmp_path)
    store = tmp_path / vault.MIRROR_FILE
    with vault.cached_docs(store):
        _read_all(paths)

    db = sqlite3.connect(str(store))
    try:
        rels = {row[0] for row in db.execute("SELECT rel FROM doc")}
    finally:
        db.close()
    assert rels == {p.relative_to(tmp_path).as_posix() for p in paths}
    assert not any(rel.startswith("/") for rel in rels), "不该出现绝对路径"


def test_mirror_roundtrip_keeps_parsed_fields(tmp_path: Path) -> None:
    """镜像存的是解析结果，读回来必须和读盘解析的一致——否则就是静默改语义。

    特意留一个不带引号的日期：YAML 会把它解析成 `date` 对象，JSON 编不进去。
    早年这里用 `default=str` 糊过去，结果同一个字段「第一次读是 date、第二次是 str」，
    类型悄悄变了却没人发现。日期必须原样还原。
    """
    paths = _seed(tmp_path, count=3)
    store = tmp_path / vault.MIRROR_FILE
    dated = tmp_path / "01_Core_Memory/Characters/CHAR-0009_带日期.md"
    dated.write_text(
        DOC.replace("id: CHAR-0001", "id: CHAR-0009").replace(
            "name: 甲", "name: 甲\ncreated_at: 2026-02-01"
        ),
        encoding="utf-8",
    )
    paths.append(dated)

    fresh = {p: vault._load_doc(p) for p in paths}
    assert isinstance(fresh[dated].meta["created_at"], date), "前置条件：YAML 认成 date"

    with vault.cached_docs(store):
        _read_all(paths)

    with vault.cached_docs(store) as cache:
        assert cache.misses == 0, "镜像齐全时不该再读盘"
        for p in paths:
            doc = vault.read_doc(p)
            assert doc.meta == fresh[p].meta
            assert doc.body == fresh[p].body
            assert doc.raw == fresh[p].raw


def test_corrupt_mirror_falls_back_to_disk(tmp_path: Path) -> None:
    """镜像写坏了只能退化直读，不能报错——它本来就是可丢弃的加速层。"""
    paths = _seed(tmp_path, count=2)
    store = tmp_path / vault.MIRROR_FILE
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"not a sqlite file at all")

    with vault.cached_docs(store) as cache:
        assert cache.misses == 0 and cache.hits == 0
        assert vault.read_text(paths[0]) == paths[0].read_text(encoding="utf-8")
        assert cache.misses == 1


def test_read_many_returns_identical_text(tmp_path: Path) -> None:
    """批量读盘必须与逐份读一字不差——它是性能通道，不该改变任何语义。"""
    paths = _seed(tmp_path, count=6)
    got = vault.read_many(paths, tmp_path / "scratch")
    assert set(got) == set(paths), "一个都不该漏"
    for path in paths:
        assert got[path] == path.read_text(encoding="utf-8")


def test_read_many_tolerates_missing_and_mixed_names(tmp_path: Path) -> None:
    """清单里混进不存在的文件、带空格的文件名，都不能让整批失败。"""
    paths = _seed(tmp_path, count=2)
    spaced = tmp_path / "01_Core_Memory/Characters/带 空格 的 档案.md"
    spaced.write_text(DOC, encoding="utf-8")
    ghost = tmp_path / "01_Core_Memory/Characters/不存在.md"

    got = vault.read_many([*paths, spaced, ghost], tmp_path / "scratch")
    assert spaced in got and got[spaced] == DOC
    assert ghost not in got, "读不到的路径不该出现在结果里，交给调用方退回直读"
    assert all(p in got for p in paths)


def test_prefetch_fills_cache_without_extra_reads(tmp_path: Path) -> None:
    """预读补齐之后，后续 get 全命中；指纹没变时第二次预读不再读盘。"""
    paths = _seed(tmp_path, count=4)
    with vault.cached_docs() as cache:
        assert cache.prefetch(paths, tmp_path / "scratch") == 4
        assert cache.misses == 4
        _read_all(paths)
        assert cache.hits == 4, "预读过的文件不该再读一遍"
        assert cache.prefetch(paths, tmp_path / "scratch") == 0
        assert cache.misses == 4, "第二批预读应当全部命中指纹"


def test_unknown_scope_is_rejected(tmp_path: Path) -> None:
    cfg = Config(
        vault=tmp_path, system={}, id_rules={}, permissions={}, workflow={}, agents={}
    )
    with pytest.raises(ValueError, match="未知的体检范围"):
        I.run(cfg, scope="turbo")


def test_fast_scope_skips_cross_file_checks(tmp_path: Path) -> None:
    """fast 不该产出「关系图」类结论——那是 full 才有的口径。"""
    cfg = Config(
        vault=tmp_path, system={}, id_rules={}, permissions={}, workflow={}, agents={}
    )
    fast = I.run(cfg, scope="fast")
    full = I.run(cfg, scope="full")
    assert fast.stats["检查项数"] < full.stats["检查项数"]
    assert "关系边" not in fast.stats
