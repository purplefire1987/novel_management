"""T07 原稿导入：三条验收对着 PRD §23 逐条落。

- 「部分失败不伪报全量成功」→ `test_partial_failure_is_not_reported_as_full_success`
  以及 `test_empty_chapter_is_skipped_visibly`
- 「未提取章节可查询」→ `test_unextracted_chapter_is_still_queryable`
- 「来源定位有效」→ `test_locator_reads_back_original_bytes`

另外几条钉住 T07 期间发现的真实缺陷：

- `test_second_batch_can_still_get_ids`：发布过一次之后 staging 里的副本
  被当成撞号，第二批就发不出号。
- `test_ledger_covers_all_batches`：第二批台账会把第一批整段覆盖掉。
- `test_restage_reuses_ids`：重跑同一批不该再吃一个新号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.cli import main

CHAPTER = "03_Manuscripts/Drafts"

# 两份虚构原稿。第一份四章（第三章只有标题），第二份一章且没有任何实体提及。
DRAFT_ONE = """\
# 第一章 渡口

雨落在灰石渡的石阶上。CHAR-0001 撑着伞站了很久。

他想起 LOC-0001 的灯。

# 第二章 夜航

船在河心停了一夜。

天亮时钟响了三次。

# 第三章 空白

# 第四章 归途

CHAR-0009 在城门口等了一夜。
"""

DRAFT_TWO = """\
# 第一章 无人

这一章里没有任何已登记的实体名字，只是一段风景。

风把纸吹得响，像有人在翻书。
"""


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Vault / runtime / 项目根 / 原稿目录。"""
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
    src = tmp_path / "src"
    src.mkdir()
    (src / "旧稿.md").write_text(DRAFT_ONE, encoding="utf-8")
    (src / "另一稿.md").write_text(DRAFT_TWO, encoding="utf-8")
    root = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    return vault, runtime, root, src


def _run(vault: Path, runtime: Path, *args: str) -> int:
    return main([*args, "--vault", str(vault), "--runtime", str(runtime)])


def _json(vault: Path, runtime: Path, capsys, *args: str) -> dict:
    capsys.readouterr()
    assert _run(vault, runtime, *args, "--json") == 0
    return json.loads(capsys.readouterr().out)


def _scan(vault: Path, runtime: Path, src: Path, capsys, *extra: str) -> dict:
    """跑一次 scan 并拿 JSON。**不断言退出码**：有维度未达标时它就该是非 0，
    那些用例单独断言（`test_partial_failure_...`）。
    """
    capsys.readouterr()
    _run(vault, runtime, "import", "scan", str(src), "--as", "ingestor", "--json", *extra)
    return json.loads(capsys.readouterr().out)


def _stage(vault: Path, runtime: Path, import_id: str, batch: int, size: int, capsys) -> str:
    capsys.readouterr()
    assert (
        _run(
            vault, runtime, "import", "stage", import_id,
            "--batch", str(batch), "--batch-size", str(size), "--as", "writer",
        )
        == 0
    )
    tasks = sorted((runtime / "tasks").glob("TASK-*.yaml"))
    return tasks[-1].stem


def _release(vault: Path, runtime: Path, task: str, capsys) -> str:
    """批准产物 → 准备计划 → 批准计划 → 执行。返回 release_id。"""
    digest = _json(vault, runtime, capsys, "inspect", task)["hash"]
    capsys.readouterr()
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 0
    capsys.readouterr()
    # 导入发布测的是分批与来源保全，不测共同发布；后者有专门用例。
    assert (
        _run(vault, runtime, "release", "prepare", "--task", task, "--skip-closure", "--as", "author") == 0
    )
    capsys.readouterr()

    release_id = sorted((runtime / "releases").glob("REL-*"))[-1].name
    plan_hash = _json(vault, runtime, capsys, "release", "show", release_id)["plan_hash"]
    capsys.readouterr()
    assert (
        _run(vault, runtime, "approve", "release", release_id, "--hash", plan_hash, "--as", "author")
        == 0
    )
    capsys.readouterr()
    assert _run(vault, runtime, "release", "execute", release_id, "--as", "author", "--confirm") == 0
    capsys.readouterr()
    return release_id


# --------------------------------------------------------------------------
# 验收一：部分失败不伪报全量成功
# --------------------------------------------------------------------------

def test_partial_failure_is_not_reported_as_full_success(env, capsys, tmp_path: Path) -> None:
    """有维度没达标就退出码非 0，且逐条说清哪一项没达标。

    GBK 文件被拒、空章没有正文——两件事都在报告里点名。只要有一项没做到，
    就不能让 `scan` 静默成功；否则「全量导入完成」就是一句谎话。
    """
    vault, runtime, _root, src = env
    (src / "乱码.txt").write_bytes("第一章 乱码\n\n正文\n".encode("gbk"))

    capsys.readouterr()
    code = _run(vault, runtime, "import", "scan", str(src), "--as", "ingestor", "--batch-size", "2")
    out = capsys.readouterr().out
    assert code == 1, "有维度未达标时 scan 必须返回非 0"

    manifest = _json(vault, runtime, capsys, "import", "report", "IMP-000001")["manifest"]

    assert any(f["rejected"] for f in manifest["files"]), "GBK 文件应被拒绝并写明原因"
    assert "不猜" in out and "GBK" in out
    assert manifest["raw_bytes_preserved"] is True, "被拒的没进来，不算保全失败"
    assert manifest["chapters_parsed"] == 5
    pending = "；".join(manifest["chapters_pending"])
    assert "第三章 空白" in pending, "空章没有候选提及，要如实列出"
    assert "第一章 无人" in pending, "只按 ID 形态提取时，纯描写章必然落进待提取"
    assert manifest["parse_errors"] == []


def test_empty_chapter_is_skipped_visibly(env, capsys) -> None:
    """只有标题的空章不发号，但要在输出里点名，不能悄悄消失。"""
    vault, runtime, _root, src = env
    _scan(vault, runtime, src, capsys, "--batch-size", "2")
    capsys.readouterr()
    assert (
        _run(
            vault, runtime, "import", "stage", "IMP-000001",
            "--batch", "2", "--batch-size", "2", "--as", "writer",
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "跳过" in out
    assert "第三章 空白" in out


# --------------------------------------------------------------------------
# 验收二：未提取章节可查询
# --------------------------------------------------------------------------

def test_unextracted_chapter_is_still_queryable(env, capsys) -> None:
    """没有提到任何实体的章，照样入正文、照样能检索到。

    「提取覆盖」是报告维度，不是准入门槛——用「没提到人名」当理由
    把一章挡在正文之外，等于让系统替作者删稿。
    """
    vault, runtime, root, src = env
    _scan(vault, runtime, src, capsys, "--batch-size", "0")
    capsys.readouterr()

    report = _json(vault, runtime, capsys, "import", "report", "IMP-000001")["manifest"]
    assert any("无人" in item for item in report["chapters_pending"]), "这一章应当算「待提取」"

    task = _stage(vault, runtime, "IMP-000001", 1, 0, capsys)
    _release(vault, runtime, task, capsys)

    drafts = sorted((root / CHAPTER).glob("*.md"))
    # 五章里有一章只有标题，不生成正文提案；其余四章都要在
    assert len(drafts) == 4, [p.name for p in drafts]
    body = "".join(p.read_text(encoding="utf-8") for p in drafts)
    assert "风把纸吹得响" in body

    capsys.readouterr()
    assert _run(vault, runtime, "search", "风把纸吹得响") == 0
    hits = capsys.readouterr().out
    assert "风把纸吹得响" in hits and "CH-00" in hits


# --------------------------------------------------------------------------
# 验收三：来源定位有效
# --------------------------------------------------------------------------

def test_locator_reads_back_original_bytes(env, capsys) -> None:
    """`import where` 按定位符回读到原文，逐字一致。"""
    vault, runtime, _root, src = env
    data = _scan(vault, runtime, src, capsys, "--batch-size", "2")
    chapters = data["detail"]["chapters"]
    first = next(c for c in chapters if c["title"].endswith("渡口"))

    capsys.readouterr()
    assert _run(vault, runtime, "import", "where", "IMP-000001", first["locator"]) == 0
    out = capsys.readouterr().out
    assert "第一章 渡口" in out
    assert "灰石渡的石阶" in out, "定位符必须能取回正文第一段"

    sample = data["manifest"]["sample_result"]
    assert sample["sample_size"] > 0
    assert sample["accuracy"] == 1.0


def test_locator_that_points_nowhere_fails_loudly(env, capsys) -> None:
    vault, runtime, _root, src = env
    _scan(vault, runtime, src, capsys)
    capsys.readouterr()
    assert _run(vault, runtime, "import", "where", "IMP-000001", "旧稿.md#L999") != 0
    assert "越界" in capsys.readouterr().out + capsys.readouterr().err


# --------------------------------------------------------------------------
# 分批：T07 期间发现的真实缺陷
# --------------------------------------------------------------------------

def test_second_batch_can_still_get_ids(env, capsys) -> None:
    """第一批发布后，staging 里那份副本不该把第二批的发号卡死。

    staging 是「同一对象在途」，不是第二个 CH-0001。判成撞号的结果是
    发布过一次就再也发不出新号。
    """
    vault, runtime, root, src = env
    _scan(vault, runtime, src, capsys, "--batch-size", "2")

    first = _stage(vault, runtime, "IMP-000001", 1, 2, capsys)
    _release(vault, runtime, first, capsys)
    assert (root / CHAPTER / "CH-0001.md").exists()
    # 正式区与 staging 此刻各有一份 CH-0001
    assert list((root / "07_Staging").rglob("CH-0001.md"))

    second = _stage(vault, runtime, "IMP-000001", 2, 2, capsys)
    _release(vault, runtime, second, capsys)
    assert (root / CHAPTER / "CH-0003.md").exists(), "第二批应当拿到 CH-0003，不是被拦下"


def test_ledger_covers_all_batches(env, capsys) -> None:
    """台账落在同一个文件上，第二批不能把第一批的章整段抹掉。"""
    vault, runtime, root, src = env
    _scan(vault, runtime, src, capsys, "--batch-size", "2")

    first = _stage(vault, runtime, "IMP-000001", 1, 2, capsys)
    _release(vault, runtime, first, capsys)
    second = _stage(vault, runtime, "IMP-000001", 2, 2, capsys)
    _release(vault, runtime, second, capsys)

    ledger = (root / "05_Ledgers" / "Imports" / "IMP-000001.md").read_text(encoding="utf-8")
    for name in ("第一章 渡口", "第二章 夜航", "第四章 归途", "第一章 无人"):
        assert name in ledger, f"台账漏了 {name}"
    assert "staged_batches: [1, 2]" in ledger
    assert "尚未发号" in ledger, "还没进 staging 的章要标明，不能省略"


def test_restage_reuses_ids(env, capsys) -> None:
    """重跑同一批（审阅后重做）复用原来的号，不新吃一个。"""
    vault, runtime, root, src = env
    _scan(vault, runtime, src, capsys, "--batch-size", "2")
    _stage(vault, runtime, "IMP-000001", 1, 2, capsys)

    again = _stage(vault, runtime, "IMP-000001", 1, 2, capsys)
    targets = sorted(
        p.relative_to(root / "07_Staging" / again).as_posix()
        for p in (root / "07_Staging" / again).rglob("*.md")
    )
    assert any(t.endswith("CH-0001.md") for t in targets)
    assert any(t.endswith("CH-0002.md") for t in targets)
    assert not any("CH-0003" in t for t in targets), "重跑不该吃新号"

    assignments = json.loads(
        (runtime / "imports" / "IMP-000001" / "assignments.json").read_text(encoding="utf-8")
    )
    assert assignments["batches"]["1"]["size"] == 2


def test_changing_batch_size_is_refused(env, capsys) -> None:
    """换切分方式会让已发的号与章对不上，宁可拒绝也不重发。"""
    vault, runtime, _root, src = env
    _scan(vault, runtime, src, capsys, "--batch-size", "2")
    _stage(vault, runtime, "IMP-000001", 1, 2, capsys)

    capsys.readouterr()
    code = _run(
        vault, runtime, "import", "stage", "IMP-000001",
        "--batch", "1", "--batch-size", "3", "--as", "writer",
    )
    out = capsys.readouterr().out
    assert code != 0
    assert "切分" in out and "重新" in out


def test_scan_writes_nothing_to_formal_area(env, capsys) -> None:
    """scan 只写归档区与 runtime，六个正式区目录一行不动。"""
    vault, runtime, root, src = env
    formal = ("01_Canon", "02_Planning", "03_Manuscripts", "04_Story_Assets", "05_Ledgers", "06_Memory")
    before = {p.as_posix() for key in formal for p in (root / key).rglob("*.md")}
    _scan(vault, runtime, src, capsys, "--batch-size", "2")
    after = {p.as_posix() for key in formal for p in (root / key).rglob("*.md")}
    assert before == after, "scan 不许碰正式区"

    raw = root / "90_Archive" / "Imports" / "IMP-000001" / "raw"
    assert (raw / "旧稿.md").read_bytes() == (src / "旧稿.md").read_bytes()
