"""V4.0 §14 验收清单。

一条清单对应一组断言，命名带前缀（14.1-1 这种），便于对着规范逐条打勾。
全部在 tmp_path 的虚构环境里跑；唯一读仓库自身的地方是「公仓不得含私人内容」，
那一条读的是本仓库（它本来就该是干净的）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system import config as cfg_mod
from novel_system.cli import main

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): sha(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def run(*argv: str) -> int:
    return main(list(argv))


def publish_task(vault: Path, runtime: Path, task: str, *extra: str) -> int:
    """走完整发布：prepare → approve release → publish。

    T06 之后 `publish` 只能执行「已准备且作者已批准」的计划——它不再替作者
    临时生成一份再当成批过。这个 helper 就是那条正路。
    """
    assert (
        run(
            "release", "prepare", "--vault", str(vault), "--runtime", str(runtime),
            "--task", task, "--as", "author",
        )
        == 0
    )
    release_id = sorted(p.name for p in (runtime / "releases").glob("REL-*"))[-1]
    digest = sha(runtime / "releases" / release_id / "plan.json")
    assert (
        run(
            "approve", "release", release_id, "--hash", digest, "--as", "author",
            "--vault", str(vault), "--runtime", str(runtime),
        )
        == 0
    )
    return run(
        "publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--as", "author", "--confirm", *extra,
    )


# --------------------------------------------------------------------------
# 14.1 路径与隔离
# --------------------------------------------------------------------------


def test_14_1_public_repo_and_vault_are_separate(tmp_path: Path, vault_env) -> None:
    vault, runtime = vault_env
    cfg = cfg_mod.load(vault=vault, runtime=runtime)
    assert cfg.public_repo != vault
    assert not vault.is_relative_to(cfg.public_repo)
    assert not cfg.public_repo.is_relative_to(vault)
    assert not runtime.is_relative_to(vault), "Runtime 必须独立于 Vault"


def test_14_1_vault_inside_public_repo_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 Vault 建在公仓里，doctor 必须报错而不是放过。"""
    public = tmp_path / "repo"
    public.mkdir()
    monkeypatch.setenv("NOVEL_PUBLIC_REPO", str(public))
    inside = public / "vault"
    runtime = tmp_path / "runtime"
    assert run("init-author", "--vault", str(inside), "--runtime", str(runtime), "--confirm") == 0
    assert run("doctor", "--vault", str(inside), "--runtime", str(runtime)) != 0


def test_14_1_no_hardcoded_private_dirs() -> None:
    """代码里不许出现旧系统的 `private/` 形态，也不许写死本机路径。"""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ('"private/', "'private/", "/Users/"):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []


def test_14_1_does_not_infer_vault_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """当前目录下恰好有个 vault/，CLI 也不能自己认领它。"""
    (tmp_path / "vault" / "00_System").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    cfg = cfg_mod.load()
    assert cfg.author_vault is None, "不得从 cwd 推断 Vault"
    assert run("status") in (0, 2)  # 允许「报告未配置」，但不允许悄悄用 ./vault
    assert not (tmp_path / "vault" / "index").exists()


def test_14_1_path_escape_rejected(tmp_path: Path, vault_env, draft_md: Path, task_factory) -> None:
    vault, runtime = vault_env
    task = task_factory("--type", "draft", "--assignee", "writer")
    for bad in ("../外面.md", "/etc/passwd", "03_Manuscripts/../../escape.md"):
        code = run(
            "stage", "--vault", str(vault), "--runtime", str(runtime),
            "--task", task, "--from", str(draft_md), "--to", bad, "--as", "writer",
        )
        assert code == 3, f"{bad} 应被拒绝"


def test_14_1_write_without_project_rejected(tmp_path: Path, draft_md: Path) -> None:
    """没有项目可写时，写入类命令必须拒绝，不能凭空建一个。"""
    vault, runtime = tmp_path / "v", tmp_path / "r"
    assert run("init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm") == 0
    assert run(
        "task", "create", "--vault", str(vault), "--runtime", str(runtime),
        "--type", "draft", "--assignee", "writer",
    ) == 2


def test_14_1_cross_project_write_rejected(tmp_path: Path, vault_env, draft_md: Path, task_factory) -> None:
    vault, runtime = vault_env
    assert run(
        "init-project", "--vault", str(vault), "--runtime", str(runtime),
        "--id", "novel-002", "--name", "Other", "--confirm",
    ) == 0
    task = task_factory("--type", "draft", "--assignee", "writer")
    other = sorted((vault / "10_Projects").glob("novel-002*"))[0]
    code = run(
        "stage", "--vault", str(vault), "--runtime", str(runtime),
        "--task", task, "--from", str(draft_md),
        "--to", str(other / "03_Manuscripts" / "Drafts" / "CH-0001.md"), "--as", "writer",
    )
    assert code == 3


def test_14_1_runtime_can_be_detached(tmp_path: Path, vault_env) -> None:
    vault, runtime = vault_env
    assert run("rebuild-index", "--vault", str(vault), "--runtime", str(runtime)) == 0
    assert (runtime / "index" / "index.json").exists()
    assert not (vault / "index").exists()


# --------------------------------------------------------------------------
# 14.2 数据安全
# --------------------------------------------------------------------------


def test_14_2_public_repo_has_no_private_content() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("_ps", ROOT / "scripts" / "privacy_scan.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_ps"] = module
    spec.loader.exec_module(module)
    findings, scanned = module.scan_worktree(ROOT)
    assert scanned > 50
    assert findings == [], "\n".join(f"{f.rule} {f.path}:{f.line} {f.message}" for f in findings)


def test_14_2_private_vault_has_no_remote(vault_env) -> None:
    """Vault 可以有本地 .git，但不能有远端。"""
    vault, _ = vault_env
    cfg = vault / ".git" / "config"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        assert "[remote" not in text


def test_14_2_backup_is_verified(vault_env) -> None:
    vault, runtime = vault_env
    assert run("backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author") == 0
    backups = sorted(p for p in (runtime / "backups").iterdir() if p.is_dir())
    assert backups, "备份目录为空"
    latest = backups[-1]
    meta = json.loads((latest / "meta.json").read_text(encoding="utf-8"))
    assert meta["verified"] is True
    manifest = latest / "manifest.csv"
    assert manifest.exists()
    # 清单列序：path, sha256, bytes
    rows = [line.split(",") for line in manifest.read_text(encoding="utf-8").splitlines()[1:] if line]
    checked = 0
    for rel, digest in ((r[0], r[1]) for r in rows):
        copied = latest / "files" / rel
        if copied.exists():
            assert sha(copied) == digest, f"备份文件哈希不符：{rel}"
            checked += 1
    assert checked > 0


def test_14_2_restore_drill(vault_env, task_factory) -> None:
    vault, runtime = vault_env
    project = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    victim = project / "00_Project" / "project.yaml"
    original = victim.read_bytes()

    assert run("backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author") == 0
    backup_id = sorted(p.name for p in (runtime / "backups").iterdir() if p.is_dir())[-1]

    victim.write_text("被删改\n", encoding="utf-8")
    assert run(
        "restore", "--vault", str(vault), "--runtime", str(runtime),
        "--backup-id", backup_id, "--dry-run",
    ) == 0
    assert victim.read_text(encoding="utf-8") == "被删改\n", "预演不能写盘"

    assert run(
        "restore", "--vault", str(vault), "--runtime", str(runtime),
        "--backup-id", backup_id, "--confirm", "--overwrite", "--as", "author",
    ) == 0
    assert victim.read_bytes() == original


def test_14_2_logs_do_not_leak_body(vault_env, draft_md: Path, task_factory) -> None:
    vault, runtime = vault_env
    task = task_factory("--type", "draft", "--assignee", "writer")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--from", str(draft_md), "--to", "03_Manuscripts/Drafts/CH-0001.md",
        "--as", "writer",
    ) == 0
    assert run("approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author") == 0
    assert publish_task(vault, runtime, task) == 0

    log = runtime / "logs" / "ops.jsonl"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "灰石渡的雨" not in text, "日志不能记录正文"
    assert "抄书人把最后一页纸" not in text


def test_14_2_index_rebuild_does_not_touch_canon(vault_env) -> None:
    vault, runtime = vault_env
    project = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    canon = project / "01_Canon"
    if not canon.exists():
        canon.mkdir(parents=True)
    if not (canon / "Characters").exists():
        (canon / "Characters").mkdir()
    (canon / "Characters" / "CHAR-0001.md").write_text(
        "---\nid: CHAR-0001\ntype: character\nname: 林砚\n---\n\n虚构人物。\n", encoding="utf-8"
    )
    before = tree_digest(canon)
    assert run("rebuild-index", "--vault", str(vault), "--runtime", str(runtime)) == 0
    assert tree_digest(canon) == before

    index_path = runtime / "index" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert any(e.get("id") == "CHAR-0001" for e in index["entries"])

    # 把索引内容换成空的哨兵，再重建：能恢复全部条目说明索引纯属派生，不承担事实。
    index_path.write_text('{"entries": []}', encoding="utf-8")
    assert run("rebuild-index", "--vault", str(vault), "--runtime", str(runtime)) == 0
    rebuilt = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(rebuilt["entries"]) == len(index["entries"])


# --------------------------------------------------------------------------
# 14.3 Agent
# --------------------------------------------------------------------------

AGENTS = {
    "editor_in_chief",
    "world_architect",
    "editor",
    "dice_mistress",
    "researcher",
    "writer",
    "memory_keeper",
    "ingestor",
    "version_manager",
}


def test_14_3_nine_agents_defined() -> None:
    found = {p.parent.name for p in (ROOT / "agents").glob("*/agent.md")}
    assert found == AGENTS


def test_14_3_each_agent_states_boundaries() -> None:
    for name in sorted(AGENTS):
        text = (ROOT / "agents" / name / "agent.md").read_text(encoding="utf-8")
        assert "## 职责" in text, f"{name} 缺职责"
        assert "## 输入" in text, f"{name} 缺输入约定"
        assert "## 输出" in text, f"{name} 缺输出约定"
        assert "## 禁止" in text, f"{name} 缺禁止事项"
        assert "## 完成判据" in text, f"{name} 缺完成判据"


def test_14_3_agent_reporting_duty_in_common_rules() -> None:
    text = (ROOT / "agents" / "common" / "permissions.md").read_text(encoding="utf-8")
    assert "报告" in text
    assert "Canon 只读" in text


def test_14_3_task_carries_author_project_and_id(vault_env, task_factory) -> None:
    vault, runtime = vault_env
    task_id = task_factory("--type", "draft", "--assignee", "writer")
    data = cfg_mod.read_yaml(runtime / "tasks" / f"{task_id}.yaml")
    assert data["task_id"] == task_id
    assert data["project_id"] == "novel-001"
    assert data["author_id"], "任务必须记录作者"


def test_14_3_agent_output_lands_in_staging(vault_env, draft_md: Path, task_factory) -> None:
    vault, runtime = vault_env
    project = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    target = "03_Manuscripts/Drafts/CH-0001.md"
    task = task_factory("--type", "draft", "--assignee", "writer")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--from", str(draft_md), "--to", target, "--as", "writer",
    ) == 0
    assert (project / "07_Staging" / task).is_dir()
    assert not (project / target).exists(), "staging 阶段不得动正式文件"


def test_14_3_agent_cannot_approve_or_publish(vault_env, draft_md: Path, task_factory) -> None:
    vault, runtime = vault_env
    task = task_factory("--type", "draft", "--assignee", "writer")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--from", str(draft_md), "--to", "03_Manuscripts/Drafts/CH-0001.md", "--as", "writer",
    ) == 0
    assert run("approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "writer") == 3
    assert run(
        "publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--as", "writer", "--confirm",
    ) == 3


def test_14_3_author_edit_is_detected_on_reread(vault_env, draft_md: Path, task_factory) -> None:
    """作者改了正式文件之后，系统必须重新读、并把它当冲突，而不是覆盖。"""
    vault, runtime = vault_env
    project = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    target = project / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("作者原来的版本\n", encoding="utf-8")

    task = task_factory("--type", "draft", "--assignee", "writer")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--from", str(draft_md), "--to", "03_Manuscripts/Drafts/CH-0001.md", "--as", "writer",
    ) == 0

    target.write_text("作者在手改\n", encoding="utf-8")
    assert run("approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author") != 0
    assert run(
        "publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--as", "author", "--confirm",
    ) != 0
    assert target.read_text(encoding="utf-8") == "作者在手改\n"


# --------------------------------------------------------------------------
# 14.4 创作工作流
# --------------------------------------------------------------------------

WORKFLOWS = {
    "manuscript_production",
    "legacy_ingestion",
    "canon_change",
    "memory_update",
    "simulation",
    "release",
}
WORKFLOW_KEYS = {
    "start_conditions",
    "inputs",
    "steps",
    "quality_gates",
    "failure_handling",
    "author_approval_points",
}


def test_14_4_six_workflows_complete() -> None:
    dirs = {p.name for p in (ROOT / "workflows").iterdir() if p.is_dir()}
    assert dirs == WORKFLOWS
    for name in sorted(WORKFLOWS):
        data = cfg_mod.read_yaml(ROOT / "workflows" / name / "workflow.yaml")
        missing = WORKFLOW_KEYS - set(data)
        assert not missing, f"{name} 缺字段：{missing}"


def test_14_4_draft_and_revision_are_separate(vault_env) -> None:
    vault, _ = vault_env
    project = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    manuscripts = project / "03_Manuscripts"
    for name in ("Drafts", "Revisions", "Final"):
        assert (manuscripts / name).is_dir(), f"缺 {name}"


def test_14_4_publish_requires_author_approval(vault_env, draft_md: Path, task_factory) -> None:
    """两道门都要关着：产物没批过 → 准备不出计划；计划没批过 → 发布不了。"""
    vault, runtime = vault_env
    task = task_factory("--type", "draft", "--assignee", "writer")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--from", str(draft_md), "--to", "03_Manuscripts/Drafts/CH-0001.md", "--as", "writer",
    ) == 0

    # 产物未批准：计划准备即被拒
    assert run(
        "release", "prepare", "--vault", str(vault), "--runtime", str(runtime),
        "--task", task, "--as", "author",
    ) == 4

    # 产物批了，但计划没批：publish 仍被拒
    assert run("approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author") == 0
    assert run(
        "release", "prepare", "--vault", str(vault), "--runtime", str(runtime),
        "--task", task, "--as", "author",
    ) == 0
    assert run(
        "publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--as", "author", "--confirm",
    ) == 4


def test_14_4_memory_update_needs_author_confirmation() -> None:
    data = cfg_mod.read_yaml(ROOT / "workflows" / "memory_update" / "workflow.yaml")
    text = json.dumps(data, ensure_ascii=False)
    assert "批准" in text or "确认" in text


def test_14_4_version_manager_can_diff_and_rollback(vault_env, draft_md: Path, task_factory) -> None:
    vault, runtime = vault_env
    task = task_factory("--type", "draft", "--assignee", "writer")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime), "--task", task,
        "--from", str(draft_md), "--to", "03_Manuscripts/Drafts/CH-0001.md", "--as", "writer",
    ) == 0
    assert run("diff", "--vault", str(vault), "--runtime", str(runtime), "--task", task) == 0

    assert run("backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author") == 0
    backup_id = sorted(p.name for p in (runtime / "backups").iterdir() if p.is_dir())[-1]
    assert run(
        "restore", "--vault", str(vault), "--runtime", str(runtime),
        "--backup-id", backup_id, "--dry-run",
    ) == 0


# --------------------------------------------------------------------------
# 14.5 工程质量
# --------------------------------------------------------------------------


def test_14_5_readme_has_install_section() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("## 2. 安装", "pip install -e", "novel doctor"):
        assert needle in text


def test_14_5_doctor_and_validate_pass(vault_env) -> None:
    vault, runtime = vault_env
    assert run("doctor", "--vault", str(vault), "--runtime", str(runtime)) == 0
    assert run("validate", "--vault", str(vault), "--runtime", str(runtime)) == 0


def test_14_5_ci_runs_privacy_and_tests() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "privacy_scan.py" in text
    assert "pytest" in text
    assert "selfcheck.py" in text


def test_14_5_schemas_are_valid_json() -> None:
    for path in sorted((ROOT / "schemas").glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
    json.loads((ROOT / "config" / "config.schema.json").read_text(encoding="utf-8"))


def test_14_5_known_risks_documented() -> None:
    text = (ROOT / "docs" / "operations" / "known_risks.md").read_text(encoding="utf-8")
    assert "R-01" in text
    assert "未解决" in text
