"""T03 命令行闭环与三条验收。

验收对照（PRD §23 T03）：
- 指定 commit 可独立构建索引 → `test_build_from_specified_commit_ignores_uncommitted`
- 中文短词与长词均有测试 → 单元测试 `test_index_search.py`，这里加一条端到端
- 不修改 00_Canon → `test_canon_is_not_modified`
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from novel_management.cli import main
from novel_management.infrastructure.filesystem.storage import sha256_file

CHAR = """\
---
id: CHAR-0001
type: character
name: 林砚
aliases:
  - 林先生
---

雨落在灰石渡的石阶上。

他确信那个人已经死了。
"""


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
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
    canon = root / "01_Canon" / "Characters"
    canon.mkdir(parents=True, exist_ok=True)
    (canon / "CHAR-0001.md").write_text(CHAR, encoding="utf-8")
    return vault, runtime, canon


def run(vault: Path, runtime: Path, *argv: str) -> int:
    return main([*argv, "--vault", str(vault), "--runtime", str(runtime)])


def digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _init_repo(path: Path) -> None:
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


# ---- 基本闭环 -------------------------------------------------------------

def test_rebuild_index_writes_sqlite_pointer_and_manifest(env, capsys) -> None:
    vault, runtime, _ = env
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()

    index = runtime / "index"
    assert (index / "active.json").exists()
    assert (index / "index.json").exists()
    pointer = json.loads((index / "active.json").read_text(encoding="utf-8"))
    assert (index / pointer["db"]).exists()
    assert pointer["build_id"]


def test_search_cli_finds_chinese_long_and_short(env, capsys) -> None:
    vault, runtime, _ = env
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()

    # 长词：走 trigram
    assert run(vault, runtime, "search", "已经死了") == 0
    out = capsys.readouterr().out
    assert "已经死了" in out and "CHAR-0001" in out

    # 短词：正文里没有，靠实体名命中
    assert run(vault, runtime, "search", "林砚") == 0
    out = capsys.readouterr().out
    assert "CHAR-0001" in out


def test_search_without_hits_exits_one(env, capsys) -> None:
    """跟 grep 一致：没命中退出 1，脚本可以据此分支。"""
    vault, runtime, _ = env
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()
    assert run(vault, runtime, "search", "完全不存在的词组") == 1


def test_search_json_output(env, capsys) -> None:
    vault, runtime, _ = env
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()
    assert run(vault, runtime, "search", "灰石渡", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "灰石渡"
    assert payload["mode"] == "fts"
    assert payload["hits"][0]["block_id"].startswith("p")


def test_search_before_index_is_usage_error(env, capsys) -> None:
    vault, runtime, _ = env
    assert run(vault, runtime, "search", "随便") == 2
    assert "rebuild-index" in capsys.readouterr().out


def test_status_reports_sqlite_index(env, capsys) -> None:
    vault, runtime, _ = env
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()
    assert run(vault, runtime, "status") == 0
    out = capsys.readouterr().out
    assert "索引" in out and "builds/" in out
    assert "fts=trigram" in out


def test_doctor_reports_fts_capability(env, capsys) -> None:
    vault, runtime, _ = env
    run(vault, runtime, "doctor")
    out = capsys.readouterr().out
    assert "SQLite" in out
    assert "FTS5" in out


# ---- 验收：不修改 00_Canon ------------------------------------------------

def test_canon_is_not_modified(env, capsys) -> None:
    vault, runtime, canon = env
    before = digest(canon)
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()
    assert digest(canon) == before, "重建索引改了 00_Canon"
    assert run(vault, runtime, "search", "灰石渡") == 0
    capsys.readouterr()
    assert digest(canon) == before, "检索改了 00_Canon"


# ---- 验收：指定 commit 可独立构建索引 --------------------------------------

def test_build_from_specified_commit_ignores_uncommitted(env, capsys) -> None:
    vault, runtime, canon = env
    _init_repo(vault)
    committed = _commit_all(vault)

    # 提交后才写的文件：按 commit 建索引时不该出现
    (canon / "CHAR-0009.md").write_text(
        "---\nid: CHAR-0009\ntype: character\nname: 没提交的角色\n---\n\n这段不该进索引。\n",
        encoding="utf-8",
    )

    assert run(vault, runtime, "rebuild-index", "--canon-revision", committed) == 0
    capsys.readouterr()

    pointer = json.loads((runtime / "index" / "active.json").read_text(encoding="utf-8"))
    assert pointer["canon_revision"] == committed
    assert pointer["source_kind"] == "git"

    # 未提交的内容搜不到
    assert run(vault, runtime, "search", "没提交的角色") == 1
    # 已提交的内容搜得到
    assert run(vault, runtime, "search", "已经死了") == 0


def test_rebuild_from_revision_also_leaves_canon_alone(env, capsys) -> None:
    vault, runtime, canon = env
    _init_repo(vault)
    committed = _commit_all(vault)
    before = digest(canon)
    assert run(vault, runtime, "rebuild-index", "--canon-revision", committed) == 0
    capsys.readouterr()
    assert digest(canon) == before


def test_bad_revision_is_usage_error(env, capsys) -> None:
    vault, runtime, _ = env
    _init_repo(vault)
    _commit_all(vault)
    assert run(vault, runtime, "rebuild-index", "--canon-revision", "deadbeef") == 2
    assert "不是有效的 Git 提交" in capsys.readouterr().out


def test_worktree_build_records_head(env, capsys) -> None:
    vault, runtime, _ = env
    _init_repo(vault)
    committed = _commit_all(vault)
    assert run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()
    pointer = json.loads((runtime / "index" / "active.json").read_text(encoding="utf-8"))
    assert pointer["canon_revision"] == committed
    assert pointer["source_kind"] == "worktree"
