"""T09／T10 的六条验收，对着 PRD §23 逐条落。

T09：

- 「草稿不写正式 00_Canon」→ `test_draft_never_touches_canon`
- 「高风险不能仅提案者自审」→ `test_high_risk_cannot_self_review`
- 「超过返修预算停止」→ `test_review_budget_stops`

T10：

- 「正文未发布也可合法提取」→ `test_extract_works_before_publish`
- 「正文修改后旧提案不能直接发布」→ `test_proposal_goes_stale_after_text_change`
- 「无新正文已生效而对应记忆仍旧版的中间状态」→ `test_closure_blocks_mixed_release`

另外钉住几条容易做成「看着过了其实没查」的地方：

- `test_plan_derive_never_invents_allowed`：allowed 没有散文来源，派生不能编；
- `test_plan_derive_keeps_body_bytes`：写回 frontmatter 后正文必须逐字节不变；
- `test_dependency_change_only_hits_dependents`：AC-44 的重点是「其他不受影响」；
- `test_approve_text_requires_review`：正文批准不是确认弹窗。

全部数据虚构。不读真实 Vault。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from novel_management.cli import main
from novel_management.storage import sha256_file

# ---------------------------------------------------------------------------
# 虚构素材
# ---------------------------------------------------------------------------
PLAN = """\
---
id: CH-0001
type: chapter_outline
title: 灰石渡的雨
volume_id: VOL-0001
timeline: 兴元十二年七月初八夜 亥时
pov: 抄书人
entities:
- CHAR-0001
- LOC-0001
core_conflict: 抄书人必须在天亮前离岸，而船家认出他手上那本书
creative_scope:
  allowed:
  - 符合人物动机的对话
  approval_required:
  - 新增命名角色
  forbidden:
  - 任何人认出抄书人的真名
must_happen:
- 抄书人在渡口交出书匣
- 雨停之前船必须离岸
must_not_happen:
- 任何人认出抄书人的真名
- 抄书人说起十五年前那场火
---

# CH-0001｜灰石渡的雨

## 必须发生的剧情事实

| 时间 | 事件 |
| --- | --- |
| 亥时初 | 抄书人把书匣交给船家 |
| 亥时末 | 船离岸 |

## 禁止发生的内容

- 不得让任何人认出抄书人的真名。
- 不得提到十五年前那场火。
"""

PLAN_NO_SCOPE = """\
---
id: CH-0002
type: chapter_outline
title: 无授权的章
volume_id: VOL-0001
timeline: 兴元十二年七月初九
pov: 抄书人
entities:
- CHAR-0001
core_conflict: 缺授权
---

# CH-0002

## 核心冲突

没有创作授权。

## 必须发生的剧情事实

| 时间 | 事件 |
| --- | --- |
| 次日 | 有人在渡口打听抄书人 |

## 禁止发生的内容

- 不得让任何人认出抄书人的真名。
- 不得提到十五年前那场火。
"""

CHAR = """\
---
id: CHAR-0001
type: character
name: 抄书人
aliases:
- 老周
---

抄书人常年在灰石渡替人抄经。
"""

LOC = """\
---
id: LOC-0001
type: location
name: 灰石渡
---

渡口只有三条石阶。
"""

CHAPTER_TEXT = (
    "雨落在灰石渡的石阶上，抄书人把最后一页纸压进书匣。\n\n"
    "船家接过匣子，指腹在盖沿上蹭了一下。他没问里面是什么，只问：「今夜就走？」\n\n"
    "抄书人点头。他没有回头。船在等，而书比船重。\n\n"
    "橹声起时，岸上的灯火一盏一盏沉进雨里。抄书人立在船尾，看着灰石渡退成一条黑线。\n"
    "他知道这一夜之后，替人抄经的日子就过完了。\n"
)

# 含「死亡」→ 确定性风险判 HIGH（PRD §14.2）。
HIGH_RISK_TEXT = CHAPTER_TEXT + "\n船家忽然低声道：「上个月死的那个人，也是这么走的。」\n"


def envelope(task_id: str, chapter_id: str = "CH-0001", text: str = CHAPTER_TEXT) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "role": "writer",
            "result": {
                "chapter_id": chapter_id,
                "title": "灰石渡的雨",
                "chapter_text": text,
                "new_fact_candidates": [{"summary": "船家认得那只书匣"}],
                "plan_deviations": [],
                "open_questions": ["船家是否该有名字"],
            },
        },
        ensure_ascii=False,
    )


MEMORY_ENVELOPE = json.dumps(
    {
        "task_id": "TASK-000001",
        "role": "memory_keeper",
        "result": {
            "items": [
                {"kind": "EVENT", "summary": "抄书人在渡口交出书匣", "quote": "把最后一页纸压进书匣"},
                {"kind": "STATE_CHANGE", "summary": "书匣离开抄书人之手", "quote": "船家接过匣子"},
            ]
        },
    },
    ensure_ascii=False,
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(env, *args: str) -> int:
    vault, runtime, _ = env
    return main([*args, "--vault", str(vault), "--runtime", str(runtime)])


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    assert (
        main(
            [
                "init-project", "--vault", str(vault), "--runtime", str(runtime),
                "--id", "novel-001", "--name", "Test Novel", "--confirm",
            ]
        )
        == 0
    )
    root = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    _write(root, "02_Planning/Chapter_Outlines/CH-0001_a.md", PLAN)
    _write(root, "02_Planning/Chapter_Outlines/CH-0002_b.md", PLAN_NO_SCOPE)
    _write(root, "01_Canon/Characters/CHAR-0001.md", CHAR)
    _write(root, "01_Canon/Locations/LOC-0001.md", LOC)
    assert main(["rebuild-index", "--vault", str(vault), "--runtime", str(runtime)]) == 0
    return vault, runtime, root


def _task(env, *, kind: str = "draft") -> str:
    _, runtime, _ = env
    assert _run(env, "task", "create", "--type", kind, "--assignee", "writer", "--as", "writer") == 0
    return sorted((runtime / "tasks").glob("TASK-*.yaml"))[-1].stem


def _write_draft(env, task: str, *, text: str = CHAPTER_TEXT, replace: bool = False, chapter: str = "CH-0001") -> int:
    vault, _, _ = env
    payload = vault / ".mock-writer.json"
    payload.write_text(envelope(task, chapter, text), encoding="utf-8")
    argv = [
        "write", "--task", task, "--chapter", chapter, "--mock-response", str(payload), "--as", "writer",
    ]
    if replace:
        argv.append("--replace")
    return _run(env, *argv)


def _review(env, task: str, *, role: str = "editor", verdict: str = "approve", risk: str = "", rounds: int = 3) -> int:
    argv = [
        "review", "submit", "--task", task, "--chapter", "CH-0001",
        "--role", role, "--verdict", verdict, "--as", role, "--max-rounds", str(rounds),
    ]
    if risk:
        argv += ["--risk", risk]
    return _run(env, *argv)


def _approve_text(env, task: str) -> int:
    return _run(env, "approve", "text", "--task", task, "--chapter", "CH-0001", "--as", "author")


def _canon_snapshot(root: Path) -> dict[str, str]:
    canon = root / "01_Canon"
    return {
        str(path.relative_to(canon)): sha256_file(path)
        for path in sorted(canon.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# T09-1：草稿不写 Canon
# ---------------------------------------------------------------------------
def test_draft_never_touches_canon(env, capsys) -> None:
    _, _, root = env
    before = _canon_snapshot(root)

    task = _task(env)
    capsys.readouterr()
    assert _write_draft(env, task) == 0
    out = capsys.readouterr().out

    assert _canon_snapshot(root) == before, "草稿流程改了 01_Canon——这是 T09 第一条验收的反面"
    assert "staging" in out
    staged = list((root / "07_Staging").rglob("CH-0001.md"))
    assert len(staged) == 1, staged
    assert "03_Manuscripts" in str(staged[0])
    # 正文里不许有系统注释（PRD §14.1）
    assert "<!--" not in staged[0].read_text(encoding="utf-8")


def test_write_refuses_when_plan_missing_scope(env, capsys) -> None:
    task = _task(env)
    payload = env[0] / ".mock-writer.json"
    payload.write_text(envelope(task, "CH-0002"), encoding="utf-8")
    capsys.readouterr()
    code = _run(
        env, "write", "--task", task, "--chapter", "CH-0002",
        "--mock-response", str(payload), "--as", "writer",
    )
    out = capsys.readouterr().out
    assert code != 0
    assert "creative_scope" in out
    assert "开写必需" in out


def test_write_refuses_without_model_response(env, capsys) -> None:
    """没有模型输出就报错，不许悄悄用 Mock 造一段占位正文。"""
    task = _task(env)
    capsys.readouterr()
    code = _run(env, "write", "--task", task, "--chapter", "CH-0001", "--as", "writer")
    out = capsys.readouterr().out
    assert code != 0
    assert "mock-response" in out


# ---------------------------------------------------------------------------
# T09-2：高风险不许自审
# ---------------------------------------------------------------------------
def test_high_risk_cannot_self_review(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task, text=HIGH_RISK_TEXT) == 0

    capsys.readouterr()
    code = _review(env, task, role="writer")
    out = capsys.readouterr().out
    assert code != 0
    assert "不能给自己写的稿" in out

    # 换个角色审同一稿：能过，但风险必须是 HIGH，且只过了一个角色 → 不算审全
    capsys.readouterr()
    assert _review(env, task, role="editor") == 0
    out = capsys.readouterr().out
    assert "HIGH" in out

    capsys.readouterr()
    code = _run(env, "review", "status", "--task", task, "--chapter", "CH-0001")
    out = capsys.readouterr().out
    assert code != 0
    assert "world_architect" in out or "editor_in_chief" in out

    capsys.readouterr()
    assert _approve_text(env, task) != 0
    out = capsys.readouterr().out
    assert "还缺角色" in out


def test_risk_can_only_be_raised(env, capsys) -> None:
    """模型／人只能往上抬风险，不能把 HIGH 降成 LOW。"""
    task = _task(env)
    assert _write_draft(env, task, text=HIGH_RISK_TEXT) == 0
    capsys.readouterr()
    assert _review(env, task, role="editor", risk="LOW") == 0
    out = capsys.readouterr().out
    assert "只能升不能降" in out
    assert "HIGH" in out


# ---------------------------------------------------------------------------
# T09-3：返修预算
# ---------------------------------------------------------------------------
def test_review_budget_stops(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0

    capsys.readouterr()
    assert _review(env, task, verdict="revise", rounds=1) != 0  # REVISE 返回非零（下一步是改稿）

    # 再改一稿：预算已用尽，不再接受审核
    assert _write_draft(env, task, replace=True) == 0
    capsys.readouterr()
    assert _review(env, task, verdict="revise", rounds=1) != 0
    out = capsys.readouterr().out
    assert "达到预算上限" in out


# ---------------------------------------------------------------------------
# T09-4：正文批准不是确认弹窗
# ---------------------------------------------------------------------------
def test_approve_text_requires_review(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0
    capsys.readouterr()
    code = _approve_text(env, task)
    out = capsys.readouterr().out
    assert code != 0
    assert "还没有审核记录" in out


def test_approve_text_binds_bytes(env, capsys) -> None:
    """批完之后草稿再被改，批准就失效——这是设计。"""
    from novel_management import config as cfg_mod
    from novel_management.approvals import ApprovalStore
    from novel_management.paths import ProjectPaths
    from novel_management.workflows import text_approval

    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    vault, runtime, _ = env
    cfg = cfg_mod.load(vault=str(vault), runtime=str(runtime), project="novel-001")
    pp = ProjectPaths.resolve(cfg, "novel-001")
    store = ApprovalStore(cfg.require_runtime())
    approval = text_approval.current(store, "CH-0001")
    assert approval is not None

    staged = list((pp.staging).rglob("CH-0001.md"))[0]
    before = sha256_file(staged)
    assert text_approval.is_valid(approval, before)
    staged.write_text(staged.read_text(encoding="utf-8") + "\n再添一句。\n", encoding="utf-8")
    assert not text_approval.is_valid(approval, sha256_file(staged))


# ---------------------------------------------------------------------------
# T10-1：未发布也能提取
# ---------------------------------------------------------------------------
def test_extract_works_before_publish(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    vault, runtime, _ = env
    payload = vault / ".mock-memory.json"
    payload.write_text(MEMORY_ENVELOPE, encoding="utf-8")

    capsys.readouterr()
    code = _run(
        env, "proposal", "extract", "--task", task, "--chapter", "CH-0001",
        "--mock-response", str(payload),
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "提取出 2 条候选" in out
    # 从未发布过
    assert not list((runtime / "releases").glob("REL-*"))
    assert list((runtime / "proposals").glob("PROP-*.yaml"))


def test_extract_requires_matching_approval(env, capsys) -> None:
    """正文改了但没重新批准 → 提取被拒（旧批准不能给新字节背书）。"""
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    # 直接改 staging 里的字节：批准哈希对不上了
    vault, _, root = env
    staged = list((root / "07_Staging").rglob("CH-0001.md"))[0]
    staged.write_text(staged.read_text(encoding="utf-8") + "\n补一句。\n", encoding="utf-8")

    capsys.readouterr()
    code = _run(env, "proposal", "extract", "--task", task, "--chapter", "CH-0001")
    out = capsys.readouterr().out
    assert code != 0
    assert "批准后被改过" in out


def test_extract_without_model_falls_back_to_skeleton(env, capsys) -> None:
    """没有模型也给得出对象：只有骨架，并明确说明缺语义条目。"""
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    capsys.readouterr()
    assert _run(env, "proposal", "extract", "--task", task, "--chapter", "CH-0001") == 0
    out = capsys.readouterr().out
    assert "确定性骨架" in out
    assert "人工" not in out.split("提取方式")[0]  # 别把警告混进摘要行
    assert "需记忆管理员补" in out


# ---------------------------------------------------------------------------
# T10-2：正文改了，旧提案不能直接发布
# ---------------------------------------------------------------------------
def test_proposal_goes_stale_after_text_change(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    capsys.readouterr()
    assert _run(env, "proposal", "extract", "--task", task, "--chapter", "CH-0001") == 0
    out = capsys.readouterr().out
    proposal_id = out.split("PROP-")[1].split()[0]
    proposal_id = "PROP-" + proposal_id.rstrip("，,。")

    capsys.readouterr()
    assert _run(env, "proposal", "verify", proposal_id) == 0

    # 改稿（返修）
    assert _write_draft(env, task, replace=True, text=CHAPTER_TEXT + "\n他在船头站了一整夜。\n") == 0

    capsys.readouterr()
    code = _run(env, "proposal", "verify", proposal_id)
    out = capsys.readouterr().out
    assert code != 0
    assert "STALE" in out

    capsys.readouterr()
    code = _run(env, "proposal", "approve", proposal_id, "--as", "author")
    out = capsys.readouterr().out
    assert code != 0
    assert "不能批准" in out


# ---------------------------------------------------------------------------
# T10-3：共同发布，不许半边生效
# ---------------------------------------------------------------------------
def test_closure_blocks_mixed_release(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    # 只批正文、不管记忆 → release prepare 必须拦
    capsys.readouterr()
    code = _run(env, "release", "prepare", "--task", task, "--as", "author")
    out = capsys.readouterr().out
    assert code != 0
    assert "正文与记忆必须同批发布" in out
    assert "NO_MEMORY_DECISION" in out

    # 声明「本章不产记忆」并写理由 → 放行
    capsys.readouterr()
    assert (
        _run(
            env, "proposal", "declare-none", "--task", task, "--chapter", "CH-0001",
            "--reason", "本章只有过场，没有新事实", "--as", "author",
        )
        == 0
    )
    capsys.readouterr()
    assert _run(env, "release", "prepare", "--task", task, "--as", "author") == 0


def test_closure_skip_is_recorded_in_plan(env, capsys) -> None:
    """`--skip-closure` 不是「什么都没发生」：计划里必须留下这条豁免。

    报错文案承诺了「在计划里留下这条豁免」。如果只拦不记，事后翻计划看不出
    这一批正文是在记忆没跟上的情况下发的——留痕就成了空话。
    """
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    # 不声明「本章不产记忆」，直接硬跳闸
    capsys.readouterr()
    assert (
        _run(env, "release", "prepare", "--task", task, "--as", "author", "--skip-closure") == 0
    )
    out = capsys.readouterr().out
    assert "记忆可能落后于正文" in out

    _, runtime, _ = env
    plans = sorted((runtime / "releases").glob("REL-*/plan.json"))
    assert plans, "prepare 应该落下计划"
    data = json.loads(plans[-1].read_text(encoding="utf-8"))
    assert data.get("closure_skipped") is True


def test_closure_flag_absent_for_old_plans() -> None:
    """0.11.0 写下的计划没有这个键，重新序列化后必须逐字段相同。

    无条件输出会让老计划算出新哈希，于是「已批准待执行」的发布突然变成
    「批准的是另一份计划」。
    """
    from novel_management.release.model import ReleasePlan

    old = {
        "release_id": "REL-000001",
        "base_release": None,
        "changes": [],
        "removals": [],
        "text_approvals": [],
        "memory_approvals": [],
        "change_requests": [],
        "nonblocking_issues": [],
        "schema_version": "0.11.0",
        "index_config_version": "3/0.11.0",
        "prepared_at": "2026-11-21T00:00:00+00:00",
    }
    plan = ReleasePlan.from_dict(old)
    assert plan.as_dict() == old
    assert "closure_skipped" not in plan.as_dict()


def test_closure_accepts_approved_proposal(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0

    capsys.readouterr()
    assert _run(env, "proposal", "extract", "--task", task, "--chapter", "CH-0001") == 0
    out = capsys.readouterr().out
    proposal_id = ("PROP-" + out.split("PROP-")[1].split()[0]).rstrip("，,。")

    capsys.readouterr()
    assert _run(env, "proposal", "approve", proposal_id, "--as", "author") == 0
    capsys.readouterr()
    assert _run(env, "release", "prepare", "--task", task, "--as", "author") == 0


def test_closure_rejects_unapproved_proposal(env, capsys) -> None:
    task = _task(env)
    assert _write_draft(env, task) == 0
    assert _review(env, task) == 0
    capsys.readouterr()
    assert _approve_text(env, task) == 0
    capsys.readouterr()
    assert _run(env, "proposal", "extract", "--task", task, "--chapter", "CH-0001") == 0

    capsys.readouterr()
    code = _run(env, "release", "prepare", "--task", task, "--as", "author")
    out = capsys.readouterr().out
    assert code != 0
    assert "PROPOSAL_NOT_APPROVED" in out


# ---------------------------------------------------------------------------
# 依赖失效（AC-44）
# ---------------------------------------------------------------------------
def _write_with_deps(root: Path, name: str, deps: list[dict], body: str = "正文。") -> Path:
    import yaml as _yaml

    path = root / "01_Canon" / "Rules" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": name.split("_")[0], "type": "rule", "title": name, "depends_on": deps}
    path.write_text("---\n" + _yaml.safe_dump(meta, allow_unicode=True) + "---\n\n" + body, encoding="utf-8")
    return path


def test_dependency_change_only_hits_dependents(env, capsys) -> None:
    """AC-44：依赖变化 → STALE；**其他不受影响**。"""
    _, runtime, root = env
    base = _write_with_deps(root, "RULE-0001_base.md", [])
    digest = sha256_file(base)
    _write_with_deps(root, "PLAN-CH-0001_a.md", [{"artifact_id": "RULE-0001", "content_hash": digest, "dependency_type": "HARD"}])
    _write_with_deps(root, "PLAN-CH-0002_b.md", [])

    capsys.readouterr()
    assert _run(env, "deps", "check") == 0
    out = capsys.readouterr().out
    assert "PLAN-CH-0001" in out
    assert "VALID" in out

    # 改被依赖的规则
    base.write_text(base.read_text(encoding="utf-8") + "\n补一条。\n", encoding="utf-8")

    capsys.readouterr()
    code = _run(env, "deps", "check")
    out = capsys.readouterr().out
    assert code == 1
    assert "STALE" in out
    stale_line = [line for line in out.splitlines() if "STALE" in line]
    assert any("PLAN-CH-0001" in line for line in stale_line)
    assert not any("PLAN-CH-0002" in line for line in stale_line), "无关产物被牵连了"


def test_dependency_without_hash_needs_review(env, capsys) -> None:
    """没记 content_hash：判不了，记 NEEDS_REVIEW，不许当成 VALID。"""
    _, _, root = env
    _write_with_deps(root, "RULE-0001_base.md", [])
    _write_with_deps(root, "PLAN-CH-0009_z.md", [{"artifact_id": "RULE-0001"}])

    capsys.readouterr()
    _run(env, "deps", "check")
    out = capsys.readouterr().out
    assert "NEEDS_REVIEW" in out


# ---------------------------------------------------------------------------
# 规划契约
# ---------------------------------------------------------------------------
def test_plan_check_reports_missing_fields(env, capsys) -> None:
    capsys.readouterr()
    code = _run(env, "plan", "check", "CH-0002")
    out = capsys.readouterr().out
    assert code == 1
    assert "creative_scope" in out


def test_plan_derive_never_invents_allowed(env, capsys) -> None:
    """allowed 没有散文来源，派生不能编。

    `PLAN` 里已经有 creative_scope，所以 derive 应当一条都不派生并说明原因；
    `PLAN_NO_SCOPE` 没有 creative_scope，派生只能给出 forbidden，allowed 必须为空。
    """
    capsys.readouterr()
    assert _run(env, "plan", "derive", "CH-0002", "--json") == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["creative_scope"]["forbidden"], "明确禁令没派生出来"
    assert "allowed" not in payload["creative_scope"], "allowed 被凭空造出来了"


def test_plan_derive_keeps_body_bytes(env, capsys) -> None:
    _, _, root = env
    path = root / "02_Planning" / "Chapter_Outlines" / "CH-0002_b.md"
    before = path.read_text(encoding="utf-8")

    capsys.readouterr()
    assert _run(env, "plan", "derive", "CH-0002", "--write", "--as", "author") == 0
    after = path.read_text(encoding="utf-8")

    assert after != before
    # 正文（第二个 --- 之后）逐字节不变
    before_body = before.split("---", 2)[2]
    after_body = after.split("---", 2)[2]
    assert after_body == before_body, "写回 frontmatter 动了正文"
    assert "must_not_happen" in after
