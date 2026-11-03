"""验收测试夹具：每个测试在临时目录里复制一份 Vault，互不影响。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from novel_system import vault
from novel_system.config import Config
from novel_system.orchestrator import Orchestrator

def _find_vault_root(start: Path) -> Path:
    """向上查找 Vault 根目录，而不是硬编码目录层级。

    这样无论 novel_system 放在 Vault 外部、还是放进 00_System/ 内，测试都能自愈。
    """
    for candidate in (start, *start.parents):
        if (candidate / "00_System" / "Config" / "system_config.yaml").is_file():
            return candidate
    raise RuntimeError(
        f"未能在 {start} 的上级目录中找到 Vault 根（缺少 00_System/Config/system_config.yaml）"
    )


VAULT_SRC = _find_vault_root(Path(__file__).resolve())

# 复制 Vault 时必须排除的东西：运行产物 + 环境 + 本测试自己的临时目录。
# 尤其 .pytest_tmp：若 basetemp 落在 Vault 内（在代码目录跑测试时会这样），
# 不排除就会把临时目录复制进自身，导致无限递归。
COPY_IGNORE = (
    ".novel_system",
    ".git",
    ".obsidian",
    "__pycache__",
    ".pytest_tmp",
    ".pytest_cache",
    ".venv",
    "*.egg-info",
)


@pytest.fixture(scope="session")
def vault_template(tmp_path_factory) -> Path:
    dst = tmp_path_factory.mktemp("template") / "NovelVault"
    shutil.copytree(VAULT_SRC, dst, ignore=shutil.ignore_patterns(*COPY_IGNORE))
    return dst


@pytest.fixture
def vault_dir(tmp_path, vault_template) -> Path:
    dst = tmp_path / "NovelVault"
    shutil.copytree(vault_template, dst)
    return dst


@pytest.fixture
def cfg(vault_dir: Path) -> Config:
    return Config.load(vault_dir)


@pytest.fixture
def orch(cfg: Config):
    instance = Orchestrator(cfg)
    try:
        yield instance
    finally:
        instance.close()


# --------------------------------------------------------------------- 辅助


def write_file(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_chapter(root: Path, chapter_id: str = "CH-0003", title: str = "废祠") -> str:
    """在临时 Vault 里造一章可写的大纲 + 场景细纲。"""
    scene_id = f"SCENE-{chapter_id}-001"
    outline = f"""---
id: {chapter_id}
type: chapter_outline
title: {title}
name: {title}
volume_id: VOL-0001
status: outline
canon_status: confirmed
created_at: 2026-02-01
updated_at: 2026-02-01
---

# {chapter_id}｜{title}

## 章节目标
让沈砚上岸，第一次踏进西岸废祠。

## POV
沈砚（第三人称限知）

## 时间与地点
长夜第五日子时，LOC-0002 白骨渡西岸

## 核心冲突
废墟里有人来过，但沈砚不确定是不是敌人。

## 必须发生的剧情事实
- 沈砚与苏未晞登岸，进入废祠。
- 废祠里有没冷的灯油。

## 禁止发生的内容
- 不得确认邢咎的生死。

## 角色出场
- CHAR-0001
- CHAR-0002

## 关联伏笔

## 场景列表
1. {scene_id}

## 章节结尾钩子
灯油还是温的。

## 作者批准
- [x] 已批准写作
"""
    write_file(root, f"04_Outline/Chapters/{chapter_id}.md", outline)

    scene = f"""---
id: {scene_id}
type: scene_beat
name: 场景 1：废祠初见
chapter_id: {chapter_id}
sequence: 1
status: confirmed
canon_status: confirmed
---

# 场景 1：废祠初见

- 场景目标：让两人登岸并进入废祠。
- POV：沈砚
- 地点：LOC-0002 白骨渡西岸
- 时间：子时中
- 参与角色：CHAR-0001、CHAR-0002
- 场景开始状态：船搁浅在西岸滩头
- 冲突：祠内似有人迹
- 情绪变化：警惕 → 更警惕
- 关键动作：沈砚摸到灯台，灯油未冷
- 信息揭示：有人刚离开不久
- 场景结束状态：两人站在废祠中庭
- 与下一场景的连接：无（本章结束）
- 必须遵守的事实：不得出现第二件承载物
- 禁止擅自扩展：不得让邢咎出场
"""
    write_file(root, f"04_Outline/Scenes/{scene_id}.md", scene)
    return chapter_id


def compose_draft(
    root: Path,
    chapter_id: str,
    prose: str,
    annotations: list[str],
    title: str = "废祠",
) -> Path:
    """把 writer 的骨架草稿换成一段真正文（模拟主笔成稿 / 作者改写）。"""
    body = (
        "## 正文\n\n"
        + prose.strip()
        + "\n\n---\n\n## 作者备注（不进入正文）\n"
        + "\n".join(annotations)
        + "\n"
    )
    meta = {
        "id": chapter_id,
        "type": "manuscript",
        "title": title,
        "name": title,
        "volume_id": "VOL-0001",
        "status": "draft",
        "canon_status": "confirmed",
        "word_count": vault.count_words(prose),
        "outline_ref": chapter_id,
        "preflight_ref": f"PREFLIGHT-{chapter_id}",
        "created_at": "2026-02-01",
        "updated_at": "2026-02-01",
    }
    path = root / ".novel_system" / "staging" / "draft" / f"{chapter_id}.draft.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    vault.write_atomic(path, vault.render_doc(meta, body))
    return path
