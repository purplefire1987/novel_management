"""T06 发布与恢复内核：22 步协议与崩溃断点。

验收对照（PRD §23 T06）：

- 「所有发布关键断点的恢复测试通过」→ `test_fault_before_switch_keeps_old_release`、
  `test_fault_after_switch_is_detected_as_missing_receipt`、`test_resume_finishes_interrupted_release`、
  `test_pointer_corrupt_is_reported_not_guessed`
- 「不出现混合正式版本」→ `test_fault_before_switch_leaves_vault_untouched`
- 「切换后缺回执可补齐」→ `test_repair_writes_missing_receipt`

另外三条属于发布协议本身，不属于恢复：幂等（同一次重试不重复切换）、
锁（第二个发布者被挡在门外而不是覆盖）、回退（一次新发布，不是偷偷拨指针）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.cli import main
from novel_management.release.engine import InjectedFault
from novel_management.release.lock import PublishLock

CHAPTER = "03_Manuscripts/Drafts/CH-0001.md"
CANON = "01_Canon/Characters/CHAR-0001.md"


# --------------------------------------------------------------------------
# 夹具与工具
# --------------------------------------------------------------------------

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
                "novel-001",
                "--name",
                "Test Novel",
                "--confirm",
            ]
        )
        == 0
    )
    root = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    return vault, runtime, root


def _run(vault: Path, runtime: Path, *args: str) -> int:
    return main([*args, "--vault", str(vault), "--runtime", str(runtime)])


def _json(vault: Path, runtime: Path, capsys, *args: str) -> dict:
    capsys.readouterr()
    assert _run(vault, runtime, *args, "--json") == 0
    return json.loads(capsys.readouterr().out)


def _stage_release(
    vault: Path,
    runtime: Path,
    tmp_path: Path,
    capsys,
    *,
    text: str,
    target: str = CHAPTER,
    task_type: str = "draft",
) -> tuple[str, str]:
    """建任务 → 入 staging → 批产物 → 准备计划 → 批计划。返回 (task, release_id)。"""
    capsys.readouterr()
    assert (
        _run(vault, runtime, "task", "create", "--type", task_type, "--assignee", "writer", "--as", "writer")
        == 0
    )
    task = sorted((runtime / "tasks").glob("TASK-*.yaml"))[-1].stem

    draft = tmp_path / f"{task}.md"
    draft.write_text(text, encoding="utf-8")
    assert _run(vault, runtime, "stage", "--task", task, "--from", str(draft), "--to", target, "--as", "writer") == 0

    digest = _json(vault, runtime, capsys, "inspect", task)["hash"]
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 0

    capsys.readouterr()
    # --skip-closure：这个用例测的是 22 步发布协议本身，不是「正文与记忆同批」；
    # 共同发布闸有自己的用例（tests/integration/test_t10_closure.py）。
    assert (
        _run(vault, runtime, "release", "prepare", "--task", task, "--skip-closure", "--as", "author") == 0
    )
    release_id = sorted((runtime / "releases").glob("REL-*"))[-1].name
    plan_hash = _json(vault, runtime, capsys, "release", "show", release_id)["plan_hash"]
    assert _run(vault, runtime, "approve", "release", release_id, "--hash", plan_hash, "--as", "author") == 0
    return task, release_id


def _status(vault: Path, runtime: Path, capsys) -> dict:
    capsys.readouterr()
    _run(vault, runtime, "release", "status", "--json")  # 判词非 OK 时返回 1，这里只取内容
    return json.loads(capsys.readouterr().out)


def _pointer(runtime: Path) -> dict:
    return json.loads((runtime / "releases" / "active.json").read_text(encoding="utf-8"))


def _steps(report: dict, status: str = "done") -> list[int]:
    """事务日志里指定状态的步骤号。"""
    journal = report.get("journal") or {}
    return sorted(s["step"] for s in journal.get("steps") or [] if s.get("status") == status)


# --------------------------------------------------------------------------
# 正常路径
# --------------------------------------------------------------------------

def test_first_release_runs_all_22_steps(env, tmp_path, capsys):
    vault, runtime, root = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一章 正文\n")

    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    out = capsys.readouterr().out
    assert "22" in out

    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "OK"
    assert report["pointer"]["release_id"] == release_id
    assert report["manifest"]["release_id"] == release_id
    assert report["receipt"]["release_id"] == release_id
    assert report["journal"]["state"] == "ACTIVE"
    assert _steps(report) == list(range(1, 23))

    # 指针、清单、回执三者记的候选提交必须是同一个值——这是「不混搭」的最小证据
    assert (
        report["pointer"]["canon_revision"]
        == report["manifest"]["canon_revision"]
        == report["receipt"]["canon_revision"]
    )
    # 镜像到正式区
    assert (root / CHAPTER).read_text(encoding="utf-8") == "第一章 正文\n"


def test_manifest_pins_index_used_by_queries(env, tmp_path, capsys):
    """发布的索引被清单钉住：重建索引不改正式查询指向。"""
    vault, runtime, root = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一章 正文\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    build_id = _status(vault, runtime, capsys)["manifest"]["index_build_id"]
    db = runtime / "index" / "builds" / f"{build_id}.sqlite"
    assert db.exists()

    # 再重建一次索引：index/active.json 会指向新库，但正式查询仍认发布钉住的那一份
    assert _run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()
    assert _run(vault, runtime, "search", "第一章", "--json") == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits, "正式发布之后检索应当能查到刚发布的正文"
    assert (runtime / "index" / "builds" / f"{build_id}.sqlite").exists()


def test_snapshot_has_no_phantom_files(env, tmp_path, capsys):
    """快照里每个文件都必须落在 Vault 里真实存在的相对路径上。

    计划里的路径是项目相对，快照的根是 Vault。不做换算就会把
    `03_Manuscripts/...` 直接写到快照根，于是发布索引同时收录正式文件与
    一份「Vault 里不存在」的幽灵，搜索会命中一个打不开的路径。
    """
    vault, runtime, root = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一章 正文\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    root_rel = root.relative_to(vault).as_posix()
    snapshot = runtime / "releases" / release_id / "snapshot"
    started = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*.md")}
    assert f"{root_rel}/{CHAPTER}" in started, "正式文件必须在项目根之下"
    assert CHAPTER not in started, f"快照根不该出现 {CHAPTER} 这样的项目内路径"

    # 清单与快照逐一对得上，且清单里没有幻觉路径
    manifest = _status(vault, runtime, capsys)["manifest"]
    for item in manifest["files"]:
        assert (snapshot / item["path"]).exists(), item["path"]


# --------------------------------------------------------------------------
# 断点：切换前
# --------------------------------------------------------------------------

def test_fault_before_switch_keeps_old_release(env, tmp_path, capsys, monkeypatch):
    """PRD §18.2「索引完成、切换前」：完整候选存在，但旧发布仍 ACTIVE。"""
    vault, runtime, root = env
    _, first = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", first, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    _, second = _stage_release(vault, runtime, tmp_path, capsys, text="第二版\n")
    monkeypatch.setenv("NOVEL_RELEASE_FAULT", "before:18")
    capsys.readouterr()
    with pytest.raises(InjectedFault):
        _run(vault, runtime, "release", "execute", second, "--as", "author", "--confirm")
    monkeypatch.delenv("NOVEL_RELEASE_FAULT")

    # 正式版仍是第一个
    assert _pointer(runtime)["release_id"] == first
    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "OK"
    assert report["pointer"]["release_id"] == first
    # 候选没被丢掉：待续事务里能看到它停在哪一步
    assert [p["release_id"] for p in report["pending"]] == [second]
    assert report["pending"][0]["last_done"] >= 17


def test_fault_before_switch_leaves_vault_untouched(env, tmp_path, capsys, monkeypatch):
    """不出现混合正式版本：切换前中断，正式区一个字节都不该变。"""
    vault, runtime, root = env
    _, first = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", first, "--as", "author", "--confirm") == 0
    capsys.readouterr()
    before = (root / CHAPTER).read_bytes()

    _, second = _stage_release(vault, runtime, tmp_path, capsys, text="第二版\n")
    monkeypatch.setenv("NOVEL_RELEASE_FAULT", "before:18")
    capsys.readouterr()
    with pytest.raises(InjectedFault):
        _run(vault, runtime, "release", "execute", second, "--as", "author", "--confirm")
    monkeypatch.delenv("NOVEL_RELEASE_FAULT")

    assert (root / CHAPTER).read_bytes() == before, "正式区被候选内容污染了"


def test_resume_finishes_interrupted_release(env, tmp_path, capsys, monkeypatch):
    vault, runtime, root = env
    _, first = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", first, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    _, second = _stage_release(vault, runtime, tmp_path, capsys, text="第二版\n")
    monkeypatch.setenv("NOVEL_RELEASE_FAULT", "before:18")
    capsys.readouterr()
    with pytest.raises(InjectedFault):
        _run(vault, runtime, "release", "execute", second, "--as", "author", "--confirm")
    monkeypatch.delenv("NOVEL_RELEASE_FAULT")

    capsys.readouterr()
    assert _run(vault, runtime, "release", "resume", second, "--as", "author") == 0
    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "OK"
    assert report["pointer"]["release_id"] == second
    assert report["journal"]["state"] == "ACTIVE"
    assert (root / CHAPTER).read_text(encoding="utf-8") == "第二版\n"
    # 第一个版本必须留痕，不许被覆盖
    assert (runtime / "releases" / first / "manifest.json").exists()


def test_abort_refuses_after_switch(env, tmp_path, capsys):
    vault, runtime, _ = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    # 已切换 → abort 拒绝，并指出该用 rollback
    assert _run(vault, runtime, "release", "abort", release_id, "--as", "author") == 1
    assert "rollback" in capsys.readouterr().out
    assert _pointer(runtime)["release_id"] == release_id


def test_abort_marks_interrupted_release_and_keeps_pointer(env, tmp_path, capsys, monkeypatch):
    vault, runtime, _ = env
    _, first = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", first, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    _, second = _stage_release(vault, runtime, tmp_path, capsys, text="第二版\n")
    monkeypatch.setenv("NOVEL_RELEASE_FAULT", "before:18")
    capsys.readouterr()
    with pytest.raises(InjectedFault):
        _run(vault, runtime, "release", "execute", second, "--as", "author", "--confirm")
    monkeypatch.delenv("NOVEL_RELEASE_FAULT")

    capsys.readouterr()
    assert _run(vault, runtime, "release", "abort", second, "--as", "author", "--note", "写错了") == 0
    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "OK"
    assert report["pointer"]["release_id"] == first
    assert second not in [p["release_id"] for p in report["pending"]]


# --------------------------------------------------------------------------
# 断点：切换后
# --------------------------------------------------------------------------

def test_fault_after_switch_is_detected_as_missing_receipt(env, tmp_path, capsys, monkeypatch):
    """PRD §18.2「切换后、回执前」：指针已动，回执没写。"""
    vault, runtime, root = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")

    monkeypatch.setenv("NOVEL_RELEASE_FAULT", "after:18")
    capsys.readouterr()
    with pytest.raises(InjectedFault):
        _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm")
    monkeypatch.delenv("NOVEL_RELEASE_FAULT")

    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "RECEIPT_MISSING"
    assert report["pointer"]["release_id"] == release_id
    assert report["receipt"] is None


def test_repair_writes_missing_receipt(env, tmp_path, capsys, monkeypatch):
    vault, runtime, root = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")

    monkeypatch.setenv("NOVEL_RELEASE_FAULT", "after:18")
    capsys.readouterr()
    with pytest.raises(InjectedFault):
        _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm")
    monkeypatch.delenv("NOVEL_RELEASE_FAULT")

    capsys.readouterr()
    assert _run(vault, runtime, "release", "repair", release_id, "--as", "author") == 0
    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "OK"
    assert report["receipt"]["release_id"] == release_id
    assert report["receipt"]["repaired"] is True
    assert report["receipt"]["manifest_hash"] == report["pointer"]["manifest_sha256"]


def test_repair_refuses_when_pointer_points_elsewhere(env, tmp_path, capsys):
    """补一张对不上账的回执比没有回执更糟：指针不在这一版就拒绝。"""
    vault, runtime, _ = env
    _, first = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", first, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    _, second = _stage_release(vault, runtime, tmp_path, capsys, text="第二版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "repair", second, "--as", "author") == 1
    assert "不是当前正式版" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 指针损坏
# --------------------------------------------------------------------------

def test_pointer_corrupt_is_reported_not_guessed(env, tmp_path, capsys):
    vault, runtime, _ = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    (runtime / "releases" / "active.json").write_text("{ 这不是 JSON", encoding="utf-8")
    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "POINTER_CORRUPT"
    assert report["pointer"] is None
    assert any("不自动猜" in p for p in report["problems"])


def test_manifest_tampering_is_detected(env, tmp_path, capsys):
    vault, runtime, _ = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    manifest_path = runtime / "releases" / release_id / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["tool_version"] = "9.9.9"
    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "MANIFEST_HASH_MISMATCH"


# --------------------------------------------------------------------------
# 幂等 / 锁 / 回退
# --------------------------------------------------------------------------

def test_second_execute_is_idempotent(env, tmp_path, capsys):
    """同一次发布重试不重复切换，也不产出第二张回执。"""
    vault, runtime, root = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()
    pointer_before = _pointer(runtime)
    receipts_before = sorted(p.name for p in (runtime / "receipts").glob("RCPT-*.json"))

    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    assert "已经是正式版" in capsys.readouterr().out

    assert _pointer(runtime) == pointer_before, "重复执行改了指针"
    assert sorted(p.name for p in (runtime / "receipts").glob("RCPT-*.json")) == receipts_before
    assert (root / CHAPTER).read_text(encoding="utf-8") == "第一版\n"
    assert _status(vault, runtime, capsys)["verdict"] == "OK"


def test_written_records_satisfy_schemas(env, tmp_path, capsys):
    """落盘的四份记录必须满足 T02 定的契约（ADR-011：schema 是契约）。"""
    from novel_management.schema import SchemaStore

    vault, runtime, _ = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()

    release_dir = runtime / "releases" / release_id
    receipt_path = sorted((runtime / "receipts").glob("RCPT-*.json"))[-1]
    store = SchemaStore()
    for name, path in (
        ("release_plan", release_dir / "plan.json"),
        ("release_manifest", release_dir / "manifest.json"),
        ("transaction_journal", release_dir / "journal.json"),
        ("execution_receipt", receipt_path),
    ):
        assert path.exists(), f"{name} 没落盘：{path}"
        problems = store.check(name, json.loads(path.read_text(encoding="utf-8")))
        assert problems == [], f"{name} 不符合契约：{problems}"


def test_lock_blocks_second_publisher(env, tmp_path, capsys):
    """第二个发布者被挡在门外，而不是覆盖别人正在做的事。"""
    vault, runtime, _ = env
    _, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")

    path = runtime / "locks" / "publish.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = PublishLock(path, task="TASK-9999", release_id=release_id, actor="author")
    holder.acquire()
    try:
        capsys.readouterr()
        code = _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm")
        out = capsys.readouterr().out
        assert code == 1
        assert "锁" in out
        assert "不按文件年龄或 PID 猜测" in out
        assert "TASK-9999" in out, "锁里要能看出是谁持有，否则只能靠猜"
    finally:
        holder.release()

    # 锁放开后能正常跑完
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    assert _status(vault, runtime, capsys)["verdict"] == "OK"


def test_rollback_is_a_new_release_pointing_at_old_content(env, tmp_path, capsys):
    """回退是一次新发布：新 release_id、复用旧版索引、base 是当前正式版。"""
    vault, runtime, root = env
    _, first = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", first, "--as", "author", "--confirm") == 0
    capsys.readouterr()
    first_show = _json(vault, runtime, capsys, "release", "show", first)
    first_index = first_show["manifest"]["index_build_id"]
    first_files = {item["path"]: item["sha256"] for item in first_show["manifest"]["files"]}

    _, second = _stage_release(vault, runtime, tmp_path, capsys, text="第二版\n")
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", second, "--as", "author", "--confirm") == 0
    capsys.readouterr()
    assert (root / CHAPTER).read_text(encoding="utf-8") == "第二版\n"

    capsys.readouterr()
    assert (
        _run(vault, runtime, "release", "rollback", "--to", first, "--reason", "改坏了", "--as", "author") == 0
    )
    capsys.readouterr()
    rollback = sorted(p.name for p in (runtime / "releases").glob("REL-*"))[-1]
    assert rollback not in (first, second), "回退必须是新的 release_id"

    show = _json(vault, runtime, capsys, "release", "show", rollback)
    assert show["plan"]["base_release"] == second, "回退包的 base 应是当前正式版"
    assert _run(vault, runtime, "approve", "release", rollback, "--hash", show["plan_hash"], "--as", "author") == 0

    capsys.readouterr()
    assert (
        _run(
            vault,
            runtime,
            "release",
            "execute",
            rollback,
            "--as",
            "author",
            "--confirm",
            "--confirm-canon",
        )
        == 0
    )
    report = _status(vault, runtime, capsys)
    assert report["verdict"] == "OK"
    assert report["pointer"]["release_id"] == rollback
    assert report["manifest"]["index_build_id"] == first_index, "回退应复用旧版索引，不重建"
    # 内容复用是逐文件哈希一致，不是「看起来差不多」。
    # 回退不沿用旧版 commit sha：每次发布都在自己的快照里新提交一次，sha 天然不同，
    # 把旧 sha 写进新清单会指向一个在本版快照里不存在的提交。
    assert {item["path"]: item["sha256"] for item in report["manifest"]["files"]} == first_files
    assert (root / CHAPTER).read_text(encoding="utf-8") == "第一版\n"
    # 回执要把「这是一次新发布 + 对外平台需人工处理」写清楚（PRD §18.3 末段）
    assert any("回退" in w and "人工处理" in w for w in report["receipt"]["warnings"])
    # 导致回退的那次发布记录不许删
    assert (runtime / "releases" / second / "manifest.json").exists()


def test_plan_hash_changes_when_approved_bytes_change(env, tmp_path, capsys):
    """批准绑的是字节：staging 改一个字节，执行必须拒绝。"""
    vault, runtime, root = env
    task, release_id = _stage_release(vault, runtime, tmp_path, capsys, text="第一版\n")

    staged = root / "07_Staging" / task / "drafts" / CHAPTER
    staged.write_text("第二版\n", encoding="utf-8")

    capsys.readouterr()
    code = _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm")
    assert code == 4, "产物批准已失效时发布必须被拒（码 4 = 审批失败）"
    assert "失效" in capsys.readouterr().out
    assert _status(vault, runtime, capsys)["verdict"] == "NO_RELEASE"


def test_canon_write_needs_second_confirmation(env, tmp_path, capsys):
    vault, runtime, _ = env
    capsys.readouterr()
    assert (
        _run(vault, runtime, "task", "create", "--type", "draft", "--assignee", "writer", "--as", "writer") == 0
    )
    task = sorted((runtime / "tasks").glob("TASK-*.yaml"))[-1].stem
    draft = tmp_path / "canon.md"
    # frontmatter 要完整：第 6 步的契约校验会按 entity 契约验（name 是必填）。
    # 这里测的是发布协议本身，不该靠一个不合契约的样本去绕过闸门。
    draft.write_text("---\nid: CHAR-0001\ntype: character\nname: 甲\n---\n\n甲\n", encoding="utf-8")
    assert _run(vault, runtime, "stage", "--task", task, "--from", str(draft), "--to", CANON, "--as", "writer") == 0
    digest = _json(vault, runtime, capsys, "inspect", task)["hash"]
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 0
    capsys.readouterr()
    # --skip-closure：这个用例测的是 22 步发布协议本身，不是「正文与记忆同批」；
    # 共同发布闸有自己的用例（tests/integration/test_t10_closure.py）。
    assert (
        _run(vault, runtime, "release", "prepare", "--task", task, "--skip-closure", "--as", "author") == 0
    )
    release_id = sorted((runtime / "releases").glob("REL-*"))[-1].name
    plan_hash = _json(vault, runtime, capsys, "release", "show", release_id)["plan_hash"]
    assert _run(vault, runtime, "approve", "release", release_id, "--hash", plan_hash, "--as", "author") == 0

    capsys.readouterr()
    code = _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm")
    assert code == 3
    assert "confirm-canon" in capsys.readouterr().out

    capsys.readouterr()
    assert (
        _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm", "--confirm-canon")
        == 0
    )
    assert _status(vault, runtime, capsys)["verdict"] == "OK"
