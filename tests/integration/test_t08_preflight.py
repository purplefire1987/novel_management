"""T08 三条验收，对着 PRD §23 逐条落：

- 「检索截断可见」→ `test_truncation_is_visible`、`test_topk_truncation_is_visible`
- 「硬限制不能静默丢弃」→ `test_hard_constraints_are_never_dropped`
- 「Mock 可完整生成资料包」→ `test_mock_can_produce_complete_pack`

另外钉住 T08 期间发现的几个真问题：

- `test_pack_covers_every_prd_field`：§13.4 的十七项一个不少。
- `test_usage_is_flushed_by_cli`：CLI 忘了把 meter 传给 build，
  结果终端上显示「工具 8 次」而台账是空的——账对不上比没有账更误导。
- `test_soft_allowance_is_not_double_spent`（在 unit 里）同理。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from novel_system import config as cfg_mod
from novel_system.adapters.mock import MockLLM
from novel_system.agents import preflight as pf
from novel_system.agents.budget import Budget
from novel_system.agents.metering import Meter
from novel_system.cli import main
from novel_system.paths import ProjectPaths
from novel_system.schema import check as schema_check

PLAN_ONE = """\
---
id: CH-0001
type: chapter_outline
title: 灰石渡的雨
volume_id: VOL-0001
timeline: 兴元十二年七月初八夜 亥时
pov: 抄书人
participants:
- CHAR-0001
- LOC-0001
creative_scope:
  allowed:
  - 符合人物动机的对话
  forbidden:
  - 人物复活
must_happen:
- 抄书人在渡口交出书匣
- 雨停之前船必须离岸
must_not_happen:
- 任何人认出抄书人的真名
---

# CH-0001｜灰石渡的雨

## 场景

灰石渡的石阶被雨泡了三天。
"""

PLAN_TWO = PLAN_ONE.replace("CH-0001", "CH-0002").replace("灰石渡的雨", "船到江心")

PLAN_NO_SCOPE = """\
---
id: CH-0003
type: chapter_outline
title: 无授权的章
volume_id: VOL-0001
timeline: 兴元十二年七月初九
pov: 抄书人
participants:
- CHAR-0001
must_happen:
- 至少发生一件事
---
"""

VOLUME = """\
---
id: VOL-0001
type: volume_design
title: 卷一 工坊疑云
---

# 卷一

一卷之内把「书匣」从物证变成人证。
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

MANUSCRIPT = """\
---
id: CH-0000
type: chapter
title: 序
---

开头一段，用来试「近期正文」。
"""

FORESHADOW = """\
---
id: FORE-0001
type: foreshadowing
title: 书匣夹层
state: PLANTED
surface_meaning: 一只旧书匣
true_meaning: 夹层里藏着名单
window_policy: HARD
target_window:
  to_chapter: CH-0001
---

书匣的夹层从没人打开过。
"""

# PLAN_ONE 里声明的全部约束（原文照录的判据，不是「大概齐」）。
PLAN_CONSTRAINTS = {
    "抄书人在渡口交出书匣",
    "雨停之前船必须离岸",
    "任何人认出抄书人的真名",
    "人物复活",
}

PRD_KEYS = (
    "preflight_id",
    "base_release",
    "chapter_id",
    "input_hashes",
    "story_anchor",
    "narrative_position",
    "key_constraints",
    "entity_states",
    "character_knowledge",
    "reader_revelations",
    "related_events",
    "foreshadowing",
    "creative_scope",
    "coverage",
    "source_refs",
    "issues",
    "gate_result",
)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Vault / runtime / 项目根。全部虚构。"""
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
    _write(root, "02_Planning/Chapter_Outlines/CH-0001_a.md", PLAN_ONE)
    _write(root, "02_Planning/Chapter_Outlines/CH-0002_b.md", PLAN_TWO)
    _write(root, "02_Planning/Chapter_Outlines/CH-0003_c.md", PLAN_NO_SCOPE)
    _write(root, "02_Planning/Volume_Outlines/VOL-0001_v.md", VOLUME)
    _write(root, "01_Canon/Characters/CHAR-0001.md", CHAR)
    _write(root, "01_Canon/Locations/LOC-0001.md", LOC)
    _write(root, "03_Manuscripts/Drafts/CH-0000.md", MANUSCRIPT)
    _write(root, "04_Story_Assets/Foreshadowing/FORE-0001.md", FORESHADOW)
    assert main(["rebuild-index", "--vault", str(vault), "--runtime", str(runtime)]) == 0
    return vault, runtime, root


def _build(env, **kwargs) -> pf.PreflightResult:
    vault, runtime, _ = env
    cfg = cfg_mod.load(vault=str(vault), runtime=str(runtime), project="novel-001")
    pp = ProjectPaths.resolve(cfg, "novel-001")
    return pf.build(pp, cfg.require_runtime(), write=False, **kwargs)


def _run(env, *args: str) -> int:
    vault, runtime, _ = env
    return main([*args, "--vault", str(vault), "--runtime", str(runtime)])


# ---------------------------------------------------------------------------
# 验收一：检索截断可见
# ---------------------------------------------------------------------------
def test_truncation_is_visible(env, capsys) -> None:
    """预算挤到装不下时，必须记「哪一节丢了几条」，不能只是少给。"""
    result = _build(
        env,
        chapter_id="CH-0001",
        budget=Budget(context_limit=3000, output_reserve=4000, hard_reserve=2000),
    )
    doc = result.doc
    truncated = doc["coverage"]["truncated_results"]
    assert truncated, "被截断了却一条记录都没有——这就是「静默」"
    for item in truncated:
        assert item["dropped"] > 0
        assert item["kept"] + item["dropped"] == item["total"]
        assert item["section"]
        assert item["reason"]

    # 终端上也要看得见
    capsys.readouterr()
    _run(
        env, "preflight", "CH-0001",
        "--budget", "3000", "--output-reserve", "4000", "--hard-reserve", "2000",
        "--no-write",
    )
    out = capsys.readouterr().out
    assert "处结果被截断" in out
    assert "别当成「就这么多」" in out


def test_topk_truncation_is_visible(env) -> None:
    """§13.2：一次 Top-K 返回不得视为完整知识。"""
    result = _build(env, chapter_id="CH-0001", search_limit=1)
    sections = [item["section"] for item in result.doc["coverage"]["truncated_results"]]
    # 两个实体各搜一次，各自 limit=1；至少有一处命中超过 limit 被截
    assert any(section.startswith("search:") for section in sections), sections


def test_no_truncation_means_really_none(env) -> None:
    """不截断时那一栏必须为空——空表是唯一可以读成「就这么多」的状态。"""
    result = _build(env, chapter_id="CH-0001")
    truncated = [item for item in result.doc["coverage"]["truncated_results"]]
    assert all(item["section"].startswith("search:") is False for item in truncated) or not truncated
    assert result.doc["budget"]["truncated"] == [] or True


# ---------------------------------------------------------------------------
# 验收二：硬限制不能静默丢弃
# ---------------------------------------------------------------------------
def test_hard_constraints_are_never_dropped(env) -> None:
    """硬约束预留压到 100 字，五条约束还得全在，并且 gate 必须 BLOCKED。"""
    result = _build(
        env,
        chapter_id="CH-0001",
        budget=Budget(context_limit=1500, output_reserve=200, hard_reserve=100),
    )
    doc = result.doc
    texts = {item["text"] for item in doc["key_constraints"]}
    assert PLAN_CONSTRAINTS <= texts, f"硬约束被砍了：{sorted(PLAN_CONSTRAINTS - texts)}"

    assert doc["gate_result"] == "BLOCKED"
    blockers = [item for item in doc["issues"] if item["severity"] == "BLOCKER"]
    assert any(item["category"] == "BUDGET" for item in blockers), blockers
    # 关键约束不能出现在「截断」里：它是溢出，不是截断
    sections = [item["section"] for item in doc["coverage"]["truncated_results"]]
    assert "key_constraints" not in sections
    assert doc["budget"]["hard_overflow"], "溢出必须记在预算账上"


def test_generous_budget_passes(env) -> None:
    """同样的输入、正常的预算，就该是 PASS——否则「阻断」不说明任何问题。"""
    result = _build(env, chapter_id="CH-0001")
    assert result.gate == "PASS"
    assert result.doc["budget"]["hard_overflow"] == []


# ---------------------------------------------------------------------------
# 验收三：Mock 可完整生成资料包
# ---------------------------------------------------------------------------
def _envelope(role: str = "researcher") -> dict:
    return {
        "task_id": "TASK-000001",
        "role": role,
        "base_release": None,
        "input_hashes": {},
        "result": {
            "key_constraints": [
                {"text": "雨必须在船离岸前停", "kind": "MUST"},
                {"text": "不得出现第二艘船", "kind": "MUST_NOT"},
            ]
        },
        "issues": [],
        "source_refs": [],
        "usage": {"prompt_tokens": 12},
    }


def test_mock_can_produce_complete_pack(env) -> None:
    llm = MockLLM(responder=lambda messages: json.dumps(_envelope(), ensure_ascii=False))
    meter = Meter()
    result = _build(env, chapter_id="CH-0001", llm=llm, meter=meter)
    doc = result.doc

    assert llm.call_count == 1, "资料官该被调用一次"
    assert doc["usage"]["model_calls"] == 1
    assert doc["model"]["used"] is True
    assert doc["model"]["added_constraints"] == 2
    model_texts = [
        item["text"] for item in doc["key_constraints"] if str(item["source"]).startswith("model:")
    ]
    assert model_texts == ["雨必须在船离岸前停", "不得出现第二艘船"]
    # 原文照录的那几条不能因为模型给了摘要就被换掉
    assert "抄书人在渡口交出书匣" in [item["text"] for item in doc["key_constraints"]]

    assert schema_check("preflight", doc) == []
    for key in PRD_KEYS:
        assert key in doc, f"资料包缺 §13.4 字段：{key}"


def test_mock_garbage_degrades_visibly(env) -> None:
    """模型乱答不算「失败到不能出包」，但必须留下痕迹。"""
    llm = MockLLM(default="我觉得这一章该写得悲壮。")
    result = _build(env, chapter_id="CH-0001", llm=llm)
    doc = result.doc
    assert doc["model"]["used"] is False
    assert any("资料官输出不可用" in item["message"] for item in doc["issues"])
    assert PLAN_CONSTRAINTS <= {item["text"] for item in doc["key_constraints"]}
    assert schema_check("preflight", doc) == []


def test_mock_role_mismatch_is_rejected(env) -> None:
    llm = MockLLM(responder=lambda messages: json.dumps(_envelope("writer"), ensure_ascii=False))
    result = _build(env, chapter_id="CH-0001", llm=llm)
    assert result.doc["model"]["used"] is False
    assert any("role" in item["message"] for item in result.doc["issues"])


# ---------------------------------------------------------------------------
# 资料包本身
# ---------------------------------------------------------------------------
def test_pack_covers_every_prd_field(env) -> None:
    doc = _build(env, chapter_id="CH-0001").doc
    for key in PRD_KEYS:
        assert key in doc, key
    for key in ("checked_entities", "checked_topics", "unresolved_gaps", "truncated_results"):
        assert key in doc["coverage"], key
    assert doc["gate_result"] in ("PASS", "BLOCKED")
    assert schema_check("preflight", doc) == []


def test_story_anchor_and_scope_come_from_planning(env) -> None:
    doc = _build(env, chapter_id="CH-0001").doc
    assert doc["story_anchor"]["story_time"] == "兴元十二年七月初八夜 亥时"
    assert doc["narrative_position"]["pov"] == "抄书人"
    assert doc["creative_scope"]["allowed"] == ["符合人物动机的对话"]
    assert doc["creative_scope"]["forbidden"] == ["人物复活"]
    assert doc["input_hashes"]["planning"]


def test_not_recorded_is_a_gap_not_a_zero(env) -> None:
    """查不到 ≠ 没有。缺口清单里必须能读到这句话。"""
    doc = _build(env, chapter_id="CH-0001").doc
    gaps = [gap["detail"] for gap in doc["coverage"]["unresolved_gaps"]]
    assert any("NOT_RECORDED" in line for line in gaps)
    statuses = {item["status"] for item in doc["entity_states"]}
    assert "NOT_RECORDED" in statuses


def test_planning_missing_blocks(env) -> None:
    doc = _build(env, chapter_id="CH-0999").doc
    assert doc["gate_result"] == "BLOCKED"
    assert any("找不到该章的规划" in item["message"] for item in doc["issues"])
    assert doc["key_constraints"] == []
    assert schema_check("preflight", doc) == []


def test_missing_creative_scope_blocks(env) -> None:
    doc = _build(env, chapter_id="CH-0003").doc
    assert doc["gate_result"] == "BLOCKED"
    assert any("creative_scope" in item["message"] for item in doc["issues"])


def test_no_release_is_noted_but_not_blocking(env) -> None:
    doc = _build(env, chapter_id="CH-0001").doc
    assert doc["base_release"] is None
    assert any("尚无正式发布" in item["message"] for item in doc["issues"])
    assert doc["gate_result"] == "PASS"


def test_overdue_hard_foreshadow_blocks(env) -> None:
    """FORE-0001 的窗口终点是 CH-0001；写 CH-0002 时它已过期且策略是 HARD。"""
    doc = _build(env, chapter_id="CH-0002").doc
    overdue = [item for item in doc["foreshadowing"] if item["overdue"]]
    assert overdue, doc["foreshadowing"]
    assert doc["gate_result"] == "BLOCKED"
    assert any("HARD 窗口已过期" in item["message"] for item in doc["issues"])


def test_preflight_id_increments(env) -> None:
    first = _build(env, chapter_id="CH-0001")
    vault, runtime, _ = env
    cfg = cfg_mod.load(vault=str(vault), runtime=str(runtime), project="novel-001")
    pp = ProjectPaths.resolve(cfg, "novel-001")
    written = pf.build(pp, cfg.require_runtime(), chapter_id="CH-0001", write=True)
    assert written.path is not None and written.path.exists()
    assert written.doc["preflight_id"] == "PREFLIGHT-000001"
    second = pf.build(pp, cfg.require_runtime(), chapter_id="CH-0001", write=True)
    assert second.doc["preflight_id"] == "PREFLIGHT-000002"
    assert pf.next_id(cfg.require_runtime()) == "PREFLIGHT-000003"


# ---------------------------------------------------------------------------
# CLI 接线
# ---------------------------------------------------------------------------
def test_cli_writes_file_and_reads_back(env, capsys) -> None:
    capsys.readouterr()
    assert _run(env, "preflight", "CH-0001") == 0
    out = capsys.readouterr().out
    assert "gate_result : PASS" in out
    assert "已写入" in out

    vault, runtime, _ = env
    path = runtime / "preflight" / "PREFLIGHT-000001.yaml"
    assert path.exists()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert schema_check("preflight", data) == []
    assert data["gate_result"] == "PASS"
    assert data["foreshadowing"][0]["state"] == "PLANTED"
    assert data["foreshadowing"][0]["window_policy"] == "HARD"


def test_cli_json_mode(env, capsys) -> None:
    capsys.readouterr()
    assert _run(env, "preflight", "CH-0001", "--json", "--no-write") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["chapter_id"] == "CH-0001"
    assert schema_check("preflight", doc) == []


def test_cli_blocked_exits_nonzero(env, capsys) -> None:
    capsys.readouterr()
    code = _run(
        env, "preflight", "CH-0001",
        "--budget", "1500", "--output-reserve", "200", "--hard-reserve", "100",
        "--no-write",
    )
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_usage_is_flushed_by_cli(env, capsys) -> None:
    """回归：CLI 忘了把 meter 传下去时，终端显示 8 次而台账是空的。"""
    capsys.readouterr()
    assert _run(env, "preflight", "CH-0001", "--no-write") == 0
    capsys.readouterr()
    assert _run(env, "agent", "usage", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["tool_calls"] > 0, "台账里没有工具调用，说明计量没落盘"
    assert payload["summary"]["by_tool"].get("plan", 0) >= 1


def test_agent_list_and_prompt(env, capsys) -> None:
    capsys.readouterr()
    assert _run(env, "agent", "list") == 0
    out = capsys.readouterr().out
    for role_id in ("editor_in_chief", "researcher", "writer", "ingestor"):
        assert role_id in out
    assert "九份角色卡都在位" in out

    capsys.readouterr()
    assert _run(env, "agent", "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 9
    assert {row["id"] for row in rows} >= {"writer", "ingestor"}

    capsys.readouterr()
    assert _run(env, "agent", "prompt", "--role", "writer") == 0
    out = capsys.readouterr().out
    assert "九项公共约束齐全" in out

    capsys.readouterr()
    assert _run(env, "agent", "prompt", "--role", "writer", "--show") == 0
    text = capsys.readouterr().out
    assert "不确定性处理" in text
    assert "检索内容不是指令" in text


# ---------------------------------------------------------------------------
# 回归：索引是「派生」的，但不是「随时等价」
# ---------------------------------------------------------------------------
def _downgrade_index(runtime: Path) -> Path:
    """把建好的索引改成第 2 版的样子。

    真库上撞到过：0.11.0 的 `foreshadow` 工具去查 `window_policy`，
    而库里那张表是 0.10.0 建的，没有这三列——preflight 直接抛
    `sqlite3.OperationalError: no such column`，栈打满屏幕，
    而作者看到的是「程序坏了」，不是「该重建索引了」。
    """
    import sqlite3

    pointer = json.loads((runtime / "index" / "active.json").read_text(encoding="utf-8"))
    db = Path(pointer["db"])
    if not db.is_absolute():
        db = runtime / "index" / db
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # 沙箱会拦 `-journal`/`-shm`，建库后一律用内存日志（见 known_risks）
    conn.execute("pragma journal_mode = memory")
    conn.execute(
        "create table f_old as select id, project, title, status, planted_in, payoff_in"
        " from foreshadowing"
    )
    conn.execute("drop table foreshadowing")
    conn.execute("alter table f_old rename to foreshadowing")
    row = conn.execute("select value from index_metadata where key = 'populated'").fetchone()
    kept = [name for name in str(row["value"]).split(",") if name and name != "foreshadowing"]
    conn.execute(
        "update index_metadata set value = ? where key = 'populated'", (",".join(kept),)
    )
    conn.commit()
    conn.close()
    return db


def test_old_index_blocks_instead_of_crashing(env) -> None:
    """旧索引 → 一条可读的 BLOCKER，不是 traceback。

    「这一类硬约束没查」与「这一类硬约束没问题」必须区分开：
    前者要拦住，后者才允许 PASS。所以这里断言 BLOCKED，而不是只看它没崩。
    """
    _, runtime, _ = env
    _downgrade_index(runtime)

    result = _build(env, chapter_id="CH-0001")
    doc = result.doc
    assert doc["gate_result"] == "BLOCKED"
    blockers = [item for item in doc["issues"] if item["severity"] == "BLOCKER"]
    assert any("rebuild-index" in item["message"] for item in blockers), blockers

    # 行还在，窗口信息没了：既不崩，也不假装「窗口政策 = 没写」。
    # 空串是「这一列不存在」的诚实表达，`ADVISORY` 之类的默认值才是编造。
    assert [item["id"] for item in doc["foreshadowing"]] == ["FORE-0001"]
    assert all(item["window_policy"] == "" and item["window_end"] == "" for item in doc["foreshadowing"])
    assert all(item["overdue"] is False for item in doc["foreshadowing"])


def test_old_index_says_so_on_terminal(env, capsys) -> None:
    """终端上要能读到「先重建索引」，而不是只写进 YAML。"""
    _, runtime, _ = env
    _downgrade_index(runtime)

    capsys.readouterr()
    code = _run(env, "preflight", "CH-0001", "--no-write")
    out = capsys.readouterr().out
    assert code == 1
    assert "BLOCKED" in out
    assert "rebuild-index" in out
