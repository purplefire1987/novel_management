"""发布闸的契约校验：Schema / ID / 引用 / 来源（AC-11/12/13/49）。

为什么这些断言必须单独存在：`_validate_candidate` 只证明「文件是对的」，
不证明「内容能成立」。少了这一层，发布就是唯一一道只看形状、不看内容的闸。

每个阻断码各有一个用例——`raise_if_blocked` 会把它们合起来报，
但只有分别钉住，删掉其中一条时才会有测试变红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management import config as cfg_mod
from novel_management.domain.errors import SecurityError
from novel_management.infrastructure.filesystem.paths import ProjectPaths
from novel_management.application.release.model import Change
from novel_management.schemas.validation import contract

PROJECT = "novel-001"


def _pp(vault: Path, runtime: Path) -> ProjectPaths:
    cfg = cfg_mod.load(vault=vault, runtime=runtime, project=PROJECT)
    return ProjectPaths.resolve(cfg, PROJECT)


def _stage(files_dir: Path, rel: str, text: str) -> Change:
    path = files_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return Change(path=rel, op="add", content_hash="0" * 64)


def _entity(eid: str = "CHAR-9001", name: str = "灰石渡的抄书人", **extra) -> str:
    lines = [
        "---",
        f"id: {eid}",
        "type: character",
        f"name: {name}",
    ]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    lines += ["---", "", "# 一个人物", ""]
    return "\n".join(lines)


def test_clean_artifact_passes(vault_env, tmp_path: Path) -> None:
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    change = _stage(files_dir, "01_Canon/Characters/CHAR-9001_抄书人.md", _entity())

    report = contract.check(pp, [change], files_dir, known_ids={"CHAR-9001"})
    assert report.blockers == [], [i.message for i in report.blockers]
    assert report.checked == 1


def test_bad_id_shape_blocks(vault_env, tmp_path: Path) -> None:
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    change = _stage(
        files_dir,
        "01_Canon/Characters/CHAR-9002_带后缀.md",
        _entity("CHAR-9002-DRAFT"),
    )

    report = contract.check(pp, [change], files_dir, known_ids=set())
    codes = {i.code for i in report.blockers}
    assert "ID_SHAPE" in codes


def test_duplicate_id_within_one_release_blocks(vault_env, tmp_path: Path) -> None:
    """两份文件同一个 ID 一起发布：按 ID 取对象从此有歧义，必须拦住。"""
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    a = _stage(files_dir, "01_Canon/Characters/CHAR-9003_甲.md", _entity("CHAR-9003", "甲"))
    b = _stage(files_dir, "01_Canon/Characters/CHAR-9003_乙.md", _entity("CHAR-9003", "乙"))

    report = contract.check(pp, [a, b], files_dir, known_ids=set())
    assert "ID_DUP" in {i.code for i in report.blockers}


def test_schema_violation_blocks(vault_env, tmp_path: Path) -> None:
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    # 缺 name：entity 契约 required 里有它
    text = "---\nid: CHAR-9004\ntype: character\n---\n\n# 无名字\n"
    change = _stage(files_dir, "01_Canon/Characters/CHAR-9004_无名字.md", text)

    report = contract.check(pp, [change], files_dir, known_ids=set())
    assert "SCHEMA_INVALID" in {i.code for i in report.blockers}


def test_assertion_without_source_blocks(vault_env, tmp_path: Path) -> None:
    """AC-11：正式断言没有可定位来源，发布应被拒绝。"""
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    text = (
        "---\n"
        "id: AST-9001\n"
        "type: assertion\n"
        "name: 灰石渡那夜下过雨\n"
        "source_refs: []\n"
        "---\n\n正文\n"
    )
    change = _stage(files_dir, "05_Ledgers/Assertions/AST-9001_下雨.md", text)

    report = contract.check(pp, [change], files_dir, known_ids=set())
    assert "SOURCE_MISSING" in {i.code for i in report.blockers}


def test_broken_wikilink_blocks(vault_env, tmp_path: Path) -> None:
    """AC-12：链接指向不存在的东西，发布应被拒绝。"""
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    body = _entity() + "\n参见 [[某个根本不存在的档案]]。\n"
    change = _stage(files_dir, "01_Canon/Characters/CHAR-9001_抄书人.md", body)

    report = contract.check(pp, [change], files_dir, known_ids={"CHAR-9001"})
    assert "REF_BROKEN" in {i.code for i in report.blockers}


def test_bare_path_source_blocks_only_for_new_artifact(vault_env, tmp_path: Path) -> None:
    """33 份素材库的裸路径来源是历史形态（hash 当时没落盘，补不出来）。
    既有产物豁免、新增产物不放行——这条区别就是 AC-11 不被架空的关键。"""
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    text = (
        "---\n"
        "id: ASSET-9001\n"
        "type: story_asset\n"
        "name: 某段打斗\n"
        "source_refs:\n"
        "- 90_Archive/Original_Draft/旧稿.md\n"
        "---\n\n正文\n"
    )
    rel = "04_Story_Assets/Battles/ASSET-9001_打斗.md"
    (pp.root / "04_Story_Assets" / "Battles").mkdir(parents=True, exist_ok=True)
    (pp.root / "04_Story_Assets" / "Battles" / "ASSET-9001_打斗.md").write_text(text, encoding="utf-8")

    added = _stage(files_dir, rel, text)
    report_new = contract.check(pp, [added], files_dir, known_ids={"ASSET-9001"})
    assert "SOURCE_BARE_PATH" in {
        i.code for i in report_new.blockers
    }, "新增产物不得用裸路径来源"

    modified = Change(path=rel, op="modify", content_hash="0" * 64)
    report_old = contract.check(pp, [modified], files_dir, known_ids={"ASSET-9001"})
    assert "SOURCE_BARE_PATH" not in {i.code for i in report_old.blockers}
    assert "SOURCE_BARE_PATH" in {i.code for i in report_old.warnings}


def test_type_without_contract_is_reported_not_silently_passed(vault_env, tmp_path: Path) -> None:
    """scene_beat 还没有契约。**没查不等于没问题**——要出现在 no_contract 里，
    否则「契约覆盖了多少份」会变成一句看起来很好的谎。"""
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    text = "---\nid: SCENE-9001\ntype: scene_beat\nname: 某场\nchapter_id: CH-0001\n---\n\n正文\n"
    change = _stage(files_dir, "02_Planning/Scene_Beats/SCENE-9001.md", text)

    report = contract.check(pp, [change], files_dir, known_ids={"SCENE-9001"})
    assert report.blockers == []
    assert any("SCENE-9001" in item for item in report.no_contract)


def test_template_is_skipped(vault_env, tmp_path: Path) -> None:
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    text = "---\ntype: template\nname: 素材卡模板\nfor: 04_Story_Assets\n---\n\n示例\n"
    change = _stage(files_dir, "04_Story_Assets/Templates/素材卡模板.md", text)

    report = contract.check(pp, [change], files_dir, known_ids=set())
    assert report.blockers == []
    assert report.no_contract == []


def test_raise_if_blocked_lists_blockers(vault_env, tmp_path: Path) -> None:
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    change = _stage(
        files_dir,
        "01_Canon/Characters/CHAR-9005_坏 ID.md",
        _entity("CHAR-9005-XYZ"),
    )
    report = contract.check(pp, [change], files_dir, known_ids=set())

    with pytest.raises(SecurityError) as exc:
        report.raise_if_blocked()
    assert "契约校验未通过" in str(exc.value)


def test_code_block_wikilink_is_not_a_reference(vault_env, tmp_path: Path) -> None:
    """讲双链写法时必然写出 `[[不存在]]` 字面量。代码里的不是链接——
    与 `validate` 同一口径，否则每份讲解文档都会给自己制造一条假阻断。"""
    vault, runtime = vault_env
    pp = _pp(vault, runtime)
    files_dir = tmp_path / "files"
    body = _entity() + "\n示例：`[[某个不存在的档案]]` 就是断链。\n"
    change = _stage(files_dir, "01_Canon/Characters/CHAR-9001_抄书人.md", body)

    report = contract.check(pp, [change], files_dir, known_ids={"CHAR-9001"})
    assert "REF_BROKEN" not in {i.code for i in report.blockers}
