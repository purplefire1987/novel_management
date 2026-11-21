"""索引构建：表、块、revision 绑定、原子替换。"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from novel_system.cli import main
from novel_system.indexing import GitRevisionSource, WorktreeSource, build_index
from novel_system.indexing import schema


def _vault(tmp_path: Path, project: str = "p1") -> tuple[Path, Path, Path]:
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
                project,
                "--name",
                "测试",
                "--confirm",
            ]
        )
        == 0
    )
    root = sorted((vault / "10_Projects").glob(f"{project}*"))[0]
    return vault, runtime, root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _init_repo(path: Path) -> None:
    """建一个临时 Git 仓库。建不了就跳过——这是环境限制，不是被测代码的问题。

    开发沙箱会拒绝对 `.git/` 的写入，跳过比伪装通过诚实：跳过在汇总里看得见，
    节点全绿却在真机炸掉才难查。
    """
    proc = subprocess.run(["git", "init", "-q", str(path)], capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:120]
        pytest.skip(f"当前环境不能创建 Git 仓库：{detail}")


def _commit_all(vault: Path, message: str = "init") -> str:
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], check=True, capture_output=True
    ).stdout.decode().strip()


CHAR = """\
---
id: CHAR-0001
type: character
name: 林砚
aliases:
  - 林先生
source_refs:
  - kind: CANON
    artifact_id: CH-0001
    block_id: p0002
---

雨落在灰石渡的石阶上。

他确信那个人已经死了。
"""


def _build(vault: Path, runtime: Path, revision: str = "worktree"):
    directory = runtime / "index"
    return build_index(
        WorktreeSource(vault, revision=revision),
        directory,
        manifest_path=directory / "index.json",
        pointer_path=directory / "active.json",
    )


def test_all_declared_tables_exist(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    result = _build(vault, runtime)

    conn = sqlite3.connect(result.db_path)
    names = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    conn.close()
    for table in schema.ALL_TABLES:
        assert table in names, f"缺表 {table}"


def test_populated_tables_have_rows(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    result = _build(vault, runtime)

    # Vault 骨架里还有 00_System 的几页文档，所以按表计数要按本用例的档案收窄
    conn = sqlite3.connect(result.db_path)
    assert conn.execute("select count(*) from entities where id='CHAR-0001'").fetchone()[0] == 1
    assert conn.execute("select count(*) from aliases where alias='林先生'").fetchone()[0] == 1
    assert conn.execute("select count(*) from source_refs where artifact_id='CHAR-0001'").fetchone()[0] == 1
    assert conn.execute("select count(*) from blocks where artifact_id='CHAR-0001'").fetchone()[0] == 2
    conn.close()


def test_empty_tables_are_really_empty(tmp_path: Path) -> None:
    """建空表是「查询不会崩」，不是「语义已支持」。"""
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    result = _build(vault, runtime)
    for table in schema.EMPTY_TABLES:
        conn = sqlite3.connect(result.db_path)
        count = conn.execute(f"select count(*) from {table}").fetchone()[0]
        conn.close()
        assert count == 0, f"{table} 本版不该有数据"


def test_metadata_records_build_and_revision(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    result = _build(vault, runtime, revision="工作树")

    conn = sqlite3.connect(result.db_path)
    meta = dict(conn.execute("select key, value from index_metadata").fetchall())
    conn.close()
    assert meta["canon_revision"] == "工作树"
    assert meta["build_id"] == result.build_id
    assert meta["fts_mode"] in (schema.FTS_MODE_TRIGRAM, schema.FTS_MODE_LIKE)
    # 伏笔表从 T08 起真的会填（Preflight 要查未回收承诺）；空表只剩依赖与问题，
    # 它们的权威副本在 runtime 里，进索引会让同一个 commit 建出两个不同的库。
    assert "foreshadowing" in meta["populated"]
    assert "dependencies" in meta["empty"]
    assert "issues" in meta["empty"]
    assert "foreshadowing" not in meta["empty"]


def test_blocks_keep_ids_and_line_numbers(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    result = _build(vault, runtime)

    conn = sqlite3.connect(result.db_path)
    rows = conn.execute(
        "select block_id, ordinal, kind, text, line_start from blocks"
        " where artifact_id = 'CHAR-0001' order by ordinal"
    ).fetchall()
    conn.close()
    assert [row[0] for row in rows] == ["p0001", "p0002"]
    assert [row[1] for row in rows] == [1, 2]
    assert rows[0][2] == "paragraph"
    assert "雨落在灰石渡的石阶上" in rows[0][3]
    assert rows[0][4] > 1  # frontmatter 占了前面若干行


def test_duplicate_ids_are_reported(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    _write(
        root / "01_Canon" / "Characters" / "CHAR-0001_副本.md",
        "---\nid: CHAR-0001\ntype: character\nname: 另一个\n---\n\n正文。\n",
    )
    result = _build(vault, runtime)
    assert "CHAR-0001" in result.duplicate_ids


def test_parse_failure_does_not_abort_build(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    _write(root / "01_Canon" / "Characters" / "坏文件.md", "---\nid: [坏\n---\n正文\n")
    result = _build(vault, runtime)
    assert result.artifact_count >= 1
    assert any(path.endswith("坏文件.md") for path, _ in result.failed)


def test_staging_and_archive_are_skipped(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    _write(root / "07_Staging" / "TASK-1" / "草稿.md", "---\nid: X\n---\n正文\n")
    _write(root / "90_Archive" / "旧.md", "---\nid: Y\n---\n正文\n")
    result = _build(vault, runtime)
    paths = [entry["path"] for entry in result.manifest["entries"]]
    assert not any("07_Staging" in path for path in paths)
    assert not any("90_Archive" in path for path in paths)
    assert result.skipped >= 2


def test_vault_level_archive_is_skipped(tmp_path: Path) -> None:
    """Vault 根的归档目录（V4.0 叫 `99_Archive`）也必须排除。

    回归用：代码曾把排除名单硬编码成 `90_Archive`，而真实 Vault 的顶层归档叫
    `99_Archive` ⇒ 整个归档（旧 PRD 2611 行、旧实体索引 75KB）被静默索引进库，
    注释上写着「跳过归档」，实际一份没跳。真实库上表现为产物多 6 份、块多 928 块。
    """
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    _write(vault / "99_Archive" / "legacy" / "旧PRD.md", "---\ntitle: 旧PRD\n---\n\n很长的旧文。\n")
    result = _build(vault, runtime)
    paths = [entry["path"] for entry in result.manifest["entries"]]
    assert not any("99_Archive" in path for path in paths), "Vault 归档不该进索引"
    assert result.skipped >= 1


def test_skip_dirs_come_from_layout_not_hardcoded() -> None:
    """归档目录名由配置决定。改名后代码要跟着走，不能靠改源码。"""
    from novel_system.indexing.source import skip_dirs_from_layout

    names = skip_dirs_from_layout({"vault_archive_dir": "ZZ_Old"})
    assert "ZZ_Old" in names
    assert "99_Archive" not in names
    # 项目内那三个是结构约定，不随 Vault 配置变
    assert {"07_Staging", "08_Reviews", "90_Archive"} <= set(names)
    # 配置缺这一项时有兜底，不能留个空洞
    assert "99_Archive" in skip_dirs_from_layout({})


def test_template_placeholder_id_is_not_counted_as_duplicate(tmp_path: Path) -> None:
    """模板里的 `id: {{id}}` 是占位符，不是身份。

    `validate` 早就这样判（`if not eid or "{{" in eid: continue`），
    builder 早期版本没跟上，于是真实库上多报一个重号 `{{id}}`。
    两处的判据必须一致，否则同一个库在两处给出不同结论。
    """
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    template = "---\nid: '{{id}}'\ntype: character\nname: '{{name}}'\n---\n\n模板正文\n"
    _write(vault / "90_Shared_Assets" / "Templates" / "Character.md", template)
    _write(vault / "90_Shared_Assets" / "Templates" / "Character2.md", template)

    result = _build(vault, runtime)
    assert "{{id}}" not in result.duplicate_ids
    conn = sqlite3.connect(result.db_path)
    ids = {row[0] for row in conn.execute(
        "select id from artifacts where path like '90_Shared_Assets/Templates/%'"
    )}
    conn.close()
    # 占位符退化成文件名，两份模板各自有身份，不再互相撞号
    assert ids == {"Character", "Character2"}



def test_rebuild_never_overwrites_the_live_index(tmp_path: Path) -> None:
    """不原地重建：每次都是新文件，旧 build 原样留着。"""
    import json

    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)
    first = _build(vault, runtime)
    _write(root / "01_Canon" / "Characters" / "CHAR-0002.md", "---\nid: CHAR-0002\nname: 周决\n---\n\n另一个。\n")
    second = _build(vault, runtime)

    assert second.build_id != first.build_id
    assert first.db_path != second.db_path
    assert first.db_path.exists(), "旧 build 被删掉了——它本该是不可变快照"
    assert second.db_path.exists()

    pointer = json.loads((runtime / "index" / "active.json").read_text(encoding="utf-8"))
    assert pointer["build_id"] == second.build_id
    assert pointer["db"] == f"builds/{second.db_path.name}"
    assert pointer["canon_revision"] == "worktree"


def test_git_revision_source_reads_that_commit_only(tmp_path: Path) -> None:
    """指定 commit 建索引：工作树里未提交的改动不该进索引。"""
    vault, runtime, root = _vault(tmp_path)
    _write(root / "01_Canon" / "Characters" / "CHAR-0001.md", CHAR)

    _init_repo(vault)
    committed = _commit_all(vault)

    # 提交后再加一份没提交的文件
    _write(root / "01_Canon" / "Characters" / "CHAR-0009.md", "---\nid: CHAR-0009\nname: 未提交\n---\n\n不该出现。\n")

    source = GitRevisionSource(vault, committed)
    assert source.revision == committed
    result = build_index(source, runtime / "index")
    ids = {entry["id"] for entry in result.manifest["entries"]}
    assert "CHAR-0001" in ids
    assert "CHAR-0009" not in ids, "按 commit 建索引却读到了工作树的未提交内容"


def test_worktree_source_does_not_confuse_outer_repo(tmp_path: Path) -> None:
    """Vault 若被摆在外层仓库里，不得把外层 HEAD 当成自己的 revision。

    这是真出过的错：临时库建在公仓的 `.tmp/` 下，`git -C` 一路向上找到公仓，
    索引就绑定了一个跟内容毫无关系的提交，而输出一切正常。
    """
    from novel_system.indexing import head_revision, is_dirty

    outer = tmp_path / "outer"
    nested = outer / "vault"
    nested.mkdir(parents=True)
    (nested / "a.md").write_text("---\nid: X\n---\n", encoding="utf-8")
    (outer / "keep.txt").write_text("x", encoding="utf-8")
    _init_repo(outer)
    _commit_all(outer)
    # 外层确实有 HEAD
    assert subprocess.run(
        ["git", "-C", str(outer), "rev-parse", "HEAD"], capture_output=True
    ).returncode == 0
    # 但 nested 不是仓库根，不允许继承
    assert head_revision(nested) == ""
    assert is_dirty(nested) is False


def test_revision_must_be_a_commit(tmp_path: Path) -> None:
    vault, runtime, root = _vault(tmp_path)
    _init_repo(vault)
    _commit_all(vault)
    from novel_system.errors import UsageError

    with pytest.raises(UsageError):
        GitRevisionSource(vault, "not-a-real-revision")
