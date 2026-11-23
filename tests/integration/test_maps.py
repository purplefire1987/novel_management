"""地图生成器的回归测试。

这些用例只证明四件事，不试图复现真实地图：

1. 路径必须来自配置，缺版式文件时**报错**而不是画一张空图；
2. 地点档案按 `type: location` 识别，**不是按 ID 前缀**——0.14.0 之后 `ent_` 是公共号段，
   同目录里还有世界表、行政区划表这些同前缀但不同类型的档案；
3. 默认预演不写盘——生成器整段替换档案正文，误跑一次的代价是几十份 Canon；
4. 预演之后加 `--confirm` 能落盘，且第二次跑是 0 改动（幂等）。

真实 Vault 上的完整重跑另在交付报告里记录，不进测试：测试不该读作者的作品。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management.cli import main

STATE = "试验州"
WORLD_TABLE = """\
---
id: ent_000029
type: world
---
## 试验州

- **主要地貌**：平原与水网
- **简介**：测试用的虚构大区
- **特色口语**：喂
"""

ADMIN_TABLE = """\
---
id: ent_000040
type: world
---
## 试验州

### 云溪府

| 郡 | 治所 | 简介 | 备注 | 关联 |
|---|---|---|---|---|
| 石桥郡 | 灰石渡 | 虚构 | — | — |
"""

LAYOUT = """\
version: 1
order:
  - 试验州
regions:
  试验州:
    direction: 中部
    dx: 0.0
    dy: 0.0
    mainland: true
no_fu: （未分府）
center_region: 试验州
naming:
  world: 测试世界
  upper_inside: 测试世界
  upper_outside: 测试世界之外
  map_page: 测试地图
  tree_page: 测试层级
  world_table: ent_000029_九州表
  admin_table: ent_000040_行政区划表
  admin_alias: 测试区划表
  admin_aliases:
    - 测试区划
    - 测试别名
  office_table: ent_000009_测试官职
  office_note: 测试品级见该表
  xlsx: 资产表.xlsx
  xlsx_path: 90_Archive/Raw_Sources/资产表.xlsx

state_cities:
  试验州: 灰石渡

page_notes:
  special_note: 界外条目不入行政序列。
  same_city_example: |-
    - 测试同城说明（例：[[ent_000001_灰石渡|灰石渡]]），不要当成两个地方。
  links:
    - "- **测试关联**：[[ent_000001_灰石渡|灰石渡]]"
"""


def _project(vault: Path) -> Path:
    """项目目录：按实际布局发现，不写死层级名。

    历史教训：这里曾写死 `vault / "10_Projects"`（V4.0 目录名），而 `init-author`
    写出的 Vault_Config 是 V1.0 的 `01_小说项目`——四个用例因此全部挂在夹具上，
    等于 maps 一行回归都没有。
    """
    matches = sorted(p for d in vault.iterdir() if d.is_dir() for p in d.glob("novel-001*"))
    assert len(matches) == 1, f"项目目录应唯一，实际：{matches}"
    return matches[0]


def _seed(project: Path, *, layout: bool = True) -> None:
    """造一份最小但格式正确的地名数据（现行 ent_ 形态）。"""
    totals = project / "02_世界知识库" / "世界与宇宙"
    totals.mkdir(parents=True, exist_ok=True)
    (totals / "ent_000029_九州表.md").write_text(WORLD_TABLE, encoding="utf-8")
    (totals / "ent_000040_行政区划表.md").write_text(ADMIN_TABLE, encoding="utf-8")

    locs = project / "02_世界知识库" / "地理与地点"
    locs.mkdir(parents=True, exist_ok=True)
    (locs / "ent_000073_试验州.md").write_text(
        "---\nid: ent_000073\ntype: location\nsubtype: 州域\nname: 试验州\n---\n"
        "\n# 试验州\n\n## 档案\n\n- **层级**：测试世界 › 试验州\n",
        encoding="utf-8",
    )
    (locs / "ent_000001_灰石渡.md").write_text(
        "---\nid: ent_000001\ntype: location\nsubtype: 地点\nname: 灰石渡\n---\n"
        "\n# 灰石渡\n\n## 档案\n\n- **层级**：测试世界 › 试验州\n",
        encoding="utf-8",
    )
    # 干扰项：同前缀、同目录，但不是地点。旧实现按 `LOC-` 前缀扫，天然不会碰到它；
    # 改成 `ent_*.md` 之后若忘了 `type` 过滤，它会被当成第 3 个地点混进图里。
    (locs / "ent_000099_不是地点.md").write_text(
        "---\nid: ent_000099\ntype: item\nname: 不是地点\n---\n", encoding="utf-8"
    )
    if layout:
        meta = project / "10_项目配置"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "jiuzhou_layout.yaml").write_text(LAYOUT, encoding="utf-8")


def _maps_dir(vault: Path) -> Path:
    return vault / "00_作者工作台" / "全局仪表盘" / "Maps"


def test_maps_paths_requires_layout(vault_env, capsys: pytest.CaptureFixture[str]) -> None:
    """版式文件缺失时必须报错。

    旧版生成器把大区顺序写死在代码里，所以「没有版式」这件事根本不会发生；
    现在版式跟着项目走，缺了就是真缺，不能退化成默认值画出一张排错序的图。
    """
    vault, _runtime = vault_env
    project = _project(vault)
    _seed(project, layout=False)

    code = main(["maps", "paths", "--vault", str(vault), "--project", "novel-001"])
    assert code != 0
    assert "jiuzhou_layout.yaml" in capsys.readouterr().out


def test_maps_paths_resolve_inside_project(vault_env, capsys: pytest.CaptureFixture[str]) -> None:
    vault, _runtime = vault_env
    project = _project(vault)
    _seed(project)

    assert main(["maps", "paths", "--vault", str(vault), "--project", "novel-001"]) == 0
    out = capsys.readouterr().out
    assert str(project / "02_世界知识库" / "地理与地点") in out
    assert str(_maps_dir(vault)) in out
    assert str(project / "10_项目配置" / "jiuzhou_layout.yaml") in out


def test_maps_build_defaults_to_dry_run(vault_env, capsys: pytest.CaptureFixture[str]) -> None:
    """不加 --confirm 时，一个文件都不许写。"""
    vault, _runtime = vault_env
    project = _project(vault)
    _seed(project)
    maps_dir = _maps_dir(vault)

    assert main(["maps", "build", "--vault", str(vault), "--project", "novel-001", "--maps-only"]) == 0
    out = capsys.readouterr().out
    assert "预演" in out
    assert not (maps_dir / "测试地图.canvas").exists()
    assert "将改 0 个文件" not in out, "有内容可写却报 0，说明写盘点没接上"


def test_maps_build_confirm_then_idempotent(vault_env, capsys: pytest.CaptureFixture[str]) -> None:
    """--confirm 落盘；再跑一次必须是 0 改动。"""
    vault, _runtime = vault_env
    project = _project(vault)
    _seed(project)
    maps_dir = _maps_dir(vault)
    base = ["maps", "build", "--vault", str(vault), "--project", "novel-001", "--maps-only", "--confirm"]

    assert main(base) == 0
    capsys.readouterr()
    canvas = maps_dir / "测试地图.canvas"
    assert canvas.exists() and (maps_dir / "测试层级.canvas").exists()

    assert main(base) == 0
    second = capsys.readouterr().out
    assert "写入 0 个文件" in second, second


def test_maps_ignores_non_location_archives(vault_env, capsys: pytest.CaptureFixture[str]) -> None:
    """`ent_` 是公共号段：同目录里 `type: item` 的档案不得进图，也不得被判「待建档」。"""
    vault, _runtime = vault_env
    project = _project(vault)
    _seed(project)

    assert main(["maps", "build", "--vault", str(vault), "--project", "novel-001", "--maps-only"]) == 0
    out = capsys.readouterr().out
    assert "不是地点" not in out, "非地点档案被当作地理实体"
    assert "待建档治所" not in out, out
