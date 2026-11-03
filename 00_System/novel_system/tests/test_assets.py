"""AC-20 ~ AC-24：旧稿素材录入（Excel 资产库分类 / 设定稿切分为底层记忆）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from novel_system import vault
from novel_system.config import Config
from novel_system.database.rebuild import ensure_index
from novel_system.workflow import assets
from novel_system.workflow.state_machine import WorkflowError

openpyxl = pytest.importorskip("openpyxl", reason="需要 openpyxl 才能测 Excel 录入")


def max_serial(root: Path, rel_dir: str, prefix: str) -> int:
    """现有最大序号。测试不假设库里只有几号实体：换作品、加实体都不会挂。"""
    numbers = [
        int(match.group(1))
        for path in (root / rel_dir).rglob(f"{prefix}-*.md")
        if (match := re.match(rf"{prefix}-(\d{{4}})_", path.name))
    ]
    return max(numbers, default=0)

PROFILE = """
version: 1
profiles:
  test-assets:
    label: 测试资产库
    kind: workbook
    world: 测试世界
    source: assets.xlsx
    archive_dir: 99_Archive/Raw_Sources/test
    tags: [legacy, test]
    entities:
      - sheet: 角色表
        name_column: 人物
        type: character
        dir: 01_Core_Memory/Characters
        subtype: 人物
        summary_column: 简介
        fields: [境界, 备注]
        links:
          势力: faction
      - sheet: 势力表
        name_column: 名称
        type: faction
        dir: 01_Core_Memory/Factions
        subtype: 势力
        summary_column: 简介
        fields: [阵营]
    tables:
      - sheet: 技能表
        filename: 技能表
        title: 测试·技能表
        dir: 01_Core_Memory/World/测试/总表
        layout: table
        columns: [名称, 品级, 效果]
      - sheet: 境界表
        filename: 境界表
        title: 测试·境界表
        dir: 01_Core_Memory/World/测试/总表
        layout: sections
        name_column: 名称
        fields: [等级区间, 说明]
  test-lore:
    label: 测试底层记忆
    kind: lore
    world: 测试世界
    root: .
    archive_dir: 99_Archive/Raw_Sources/test
    tags: [legacy, test]
    dir: 01_Core_Memory/World/测试
    documents:
      - source: lore.md
        dir: 01_Core_Memory/World/测试/底层架构
        docs:
          - title: 测试·总纲
            filename: 总纲
            start: "# 设定稿"
            end: "## 第二条"
            lead: 一句话定位。
          - title: 测试·第二条
            filename: 第二条
            start: "## 第二条"
            end: null
"""


def _write_profile(vault_dir: Path) -> None:
    path = vault_dir / "00_System" / "Config" / "import_profiles.yaml"
    path.write_text(PROFILE, encoding="utf-8")


def _make_workbook(path: Path) -> Path:
    wb = openpyxl.Workbook()
    characters = wb.active
    characters.title = "角色表"
    characters.append(["人物", "简介", "境界", "备注", "势力"])
    characters.append(["甲一", "第一行简介", "启灵", "", "天家"])
    characters.append(["甲二", "第二行简介", "培元", "备注二", "天家,散人"])
    characters.append(["甲二", "同名重复行", "", "", ""])
    characters.append([None, "没有名字的行", "", "", ""])

    factions = wb.create_sheet("势力表")
    factions.append(["名称", "简介", "阵营"])
    factions.append(["天家", "皇族势力", "朝廷"])
    factions.append(["散人", "不属任何势力", "民间"])

    skills = wb.create_sheet("技能表")
    skills.append(["名称", "品级", "效果"])
    skills.append(["引火术", "下品", "点个火，用来照明"])

    realms = wb.create_sheet("境界表")
    realms.append(["名称", "等级区间", "说明"])
    realms.append(["启灵", "1-10", "初次感知灵能"])
    realms.append(["培元", "11-20", "凝结灵元"])

    wb.save(path)
    return path


def test_import_workbook_classifies_entities_and_tables(cfg: Config):
    _write_profile(cfg.vault)
    source = _make_workbook(cfg.vault / "assets.xlsx")
    index = ensure_index(cfg)

    char_base = max_serial(cfg.vault, "01_Core_Memory/Characters", "CHAR")
    fac_base = max_serial(cfg.vault, "01_Core_Memory/Factions", "FAC")

    result = assets.import_workbook(cfg, index, "test-assets", source=str(source))
    index.commit()

    # 续接既有最大号，而不是从 0001 重来
    char = cfg.vault / f"01_Core_Memory/Characters/CHAR-{char_base + 1:04d}_甲一.md"
    assert char.exists(), "人物应落到 Characters 目录并续接 CHAR 序号"
    meta, body = vault.split_frontmatter(char.read_text(encoding="utf-8"))
    assert meta["id"] == f"CHAR-{char_base + 1:04d}"
    assert meta["type"] == "character"
    assert meta["world"] == "测试世界"
    assert "legacy" in meta["tags"]
    assert "第一行简介" in body
    assert f"[[FAC-{fac_base + 1:04d}_天家|天家]]" in body, "关联列应渲染成 Obsidian 双链"

    # 多值关联逐项建链
    meta2, body2 = vault.split_frontmatter(
        (cfg.vault / f"01_Core_Memory/Characters/CHAR-{char_base + 2:04d}_甲二.md").read_text(encoding="utf-8")
    )
    assert f"[[FAC-{fac_base + 1:04d}_天家|天家]]" in body2
    assert f"[[FAC-{fac_base + 2:04d}_散人|散人]]" in body2

    # 同名重复行单独建档并提示
    dedup = cfg.vault / f"01_Core_Memory/Characters/CHAR-{char_base + 3:04d}_甲二_2.md"
    assert dedup.exists()
    assert any("同名" in warning for warning in result.warnings)

    # 无名行跳过并记录
    assert any("缺少「人物」" in item for item in result.skipped)

    # 总表：table 布局一张表、sections 布局每行一个二级标题
    skill_doc = next((cfg.vault / "01_Core_Memory/World/测试/总表").glob("WORLD-*_技能表.md"))
    skill_text = skill_doc.read_text(encoding="utf-8")
    assert "| 名称 | 品级 | 效果 |" in skill_text
    assert "引火术" in skill_text

    realm_doc = next((cfg.vault / "01_Core_Memory/World/测试/总表").glob("WORLD-*_境界表.md"))
    realm_text = realm_doc.read_text(encoding="utf-8")
    assert "## 启灵" in realm_text and "## 培元" in realm_text

    # 分类统计与原件归档
    assert result.counts["人物"] == 3
    assert result.counts["势力"] == 2
    archived = cfg.vault / "99_Archive/Raw_Sources/test/assets.xlsx"
    assert archived.exists(), "原件应复制到归档目录"

    # 批次日志记录了来源哈希与回滚方式
    log = (cfg.vault / result.log_path).read_text(encoding="utf-8")
    assert "profile: test-assets" in log
    assert result.operation_id in log


def test_import_workbook_is_idempotent_guarded(cfg: Config):
    _write_profile(cfg.vault)
    source = _make_workbook(cfg.vault / "assets.xlsx")
    index = ensure_index(cfg)
    assets.import_workbook(cfg, index, "test-assets", source=str(source))
    index.commit()

    with pytest.raises(WorkflowError, match="已用同一 profile 录入过"):
        assets.import_workbook(cfg, index, "test-assets", source=str(source))

    # --force 允许重录
    again = assets.import_workbook(cfg, index, "test-assets", source=str(source), force=True)
    assert again.total() > 0


def test_dry_run_writes_nothing(cfg: Config):
    _write_profile(cfg.vault)
    source = _make_workbook(cfg.vault / "assets.xlsx")
    index = ensure_index(cfg)
    char_base = max_serial(cfg.vault, "01_Core_Memory/Characters", "CHAR")

    result = assets.import_workbook(cfg, index, "test-assets", source=str(source), dry_run=True)

    assert result.files > 0
    assert not (cfg.vault / f"01_Core_Memory/Characters/CHAR-{char_base + 1:04d}_甲一.md").exists()
    assert not (cfg.vault / "99_Archive/Raw_Sources/test/assets.xlsx").exists()
    assert len(result.samples) == 5


def test_import_lore_slices_by_heading(cfg: Config):
    _write_profile(cfg.vault)
    lore = (
        "# 设定稿\n\n"
        "开头一句。\n\n"
        "## 第一条\n\n物质是载体。\n\n"
        "### 子条目\n\n细节。\n\n"
        "## 第二条\n\n灵力是介质。\n"
    )
    (cfg.vault / "lore.md").write_text(lore, encoding="utf-8")
    index = ensure_index(cfg)
    world_base = max_serial(cfg.vault, "01_Core_Memory/World", "WORLD")

    result = assets.import_lore(cfg, index, "test-lore")
    index.commit()

    assert result.counts["底层记忆"] == 2
    first = cfg.vault / f"01_Core_Memory/World/测试/底层架构/WORLD-{world_base + 1:04d}_总纲.md"
    second = cfg.vault / f"01_Core_Memory/World/测试/底层架构/WORLD-{world_base + 2:04d}_第二条.md"
    assert first.exists() and second.exists()

    meta, body = vault.split_frontmatter(first.read_text(encoding="utf-8"))
    assert meta["type"] == "world"
    assert meta["subtype"] == "底层记忆"
    assert "物质是载体" in body
    assert "灵力是介质" not in body, "下一段应被切到第二篇"
    assert "## 本页目录" in body, "长文档应自动生成目录"

    _, body2 = vault.split_frontmatter(second.read_text(encoding="utf-8"))
    assert "灵力是介质" in body2
    assert "物质是载体" not in body2


def test_import_lore_missing_heading_fails_loudly(cfg: Config):
    _write_profile(cfg.vault)
    (cfg.vault / "lore.md").write_text("# 别的标题\n\n内容\n", encoding="utf-8")
    index = ensure_index(cfg)

    with pytest.raises(WorkflowError, match="找不到起始标题"):
        assets.import_lore(cfg, index, "test-lore")
