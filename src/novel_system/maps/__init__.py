"""地图生成：把地名数据落成地图页与两张 canvas。

历史：这两个脚本原先住在 Vault 里，用 `Path(__file__).resolve().parents[2]`
找库根，再用 `private/00_Canon/Entities/...` 拼路径。V4.0 把库重排成
`10_Projects/<PROJECT>/01_Canon/...` 之后，那条路径不存在了——脚本不会报
「路径错」，只会读到空表然后输出一张空地图，或把文件写到已废弃的目录里。

现在唯一的路径来源是 `resolve()` 返回的 `MapPaths`。两个模块保留原有函数体，
只把模块级常量改成由 `configure()` 注入，避免重写 1500 行渲染逻辑时引入新错。

产物位置：

- `00_作者工作台/全局仪表盘/Maps/<地图页>.md`（Vault 级看板，不是项目内）
- `00_作者工作台/全局仪表盘/Maps/<地图页>.canvas`
- `00_作者工作台/全局仪表盘/Maps/<层级树>.canvas`

版式与命名（大区顺序、方位、canvas 偏移、世界名、产物文件名）都跟着项目走：
`<project>/10_项目配置/<LAYOUT_NAME>`。代码里不留作品设定——
这个仓库按 GPL-3.0 公开，而 ADR-005 定了私人数据不进公仓。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config as cfg_mod
from ..config import ConfigError
from ..paths import ProjectPaths

LAYOUT_NAME = "jiuzhou_layout.yaml"
MAPS_SUBDIR = ("全局仪表盘", "Maps")  # V1.0 §8：00_作者工作台/全局仪表盘（V4.0 的 `Dashboards`）


@dataclass
class MapPaths:
    """一次地图生成所需的全部输入输出路径。"""

    vault: Path
    project_root: Path
    world_table: Path
    admin_table: Path
    loc_dir: Path
    map_dir: Path
    layout_file: Path
    xlsx: Path


def resolve(
    cfg: cfg_mod.Config,
    pp: ProjectPaths,
    *,
    world_subdir: str | None = None,
    xlsx: str | Path | None = None,
) -> MapPaths:
    """按 V1.0 §8 目录约定算出全部路径，不猜、不兜底。

    世界名与资产表位置都从版式配置的 `naming` 段取，代码里不留任何具体地名——
    公仓按 GPL-3.0 公开，而 ADR-005 定了「私人数据不落公仓」。
    """
    vault = pp.vault
    system_dir = vault / str(cfg.layout.get("vault_system_dir") or "00_作者工作台")
    map_dir = system_dir.joinpath(*MAPS_SUBDIR)

    layout_file = pp.project_meta / LAYOUT_NAME
    if not layout_file.exists():
        raise ConfigError(
            f"缺少版式配置：{layout_file}（大区顺序、命名与 canvas 方位都在这里，代码里不再写死）"
        )
    layout = cfg_mod.read_yaml(layout_file) or {}
    naming = layout.get("naming") or {}

    sub = (world_subdir or str(naming.get("world") or "")).strip()
    if not sub:
        raise ConfigError("版式配置缺少 naming.world，且命令行没给 --world-subdir")

    world_category = pp.canon_dir("世界与宇宙")
    # V1.0 重排把世界知识**平铺**到 `02_世界知识库/世界与宇宙/`，不再有
    # `.../九州/总表/` 这一层。两种形态都认：有子目录就用子目录，
    # 没有就用分类目录本身。**不能用「找不到子目录就报错」**——那会让
    # 每个新项目都必须复刻作者当年碰巧用过的嵌套，纯属把偶然当规范。
    world_root = world_category / sub
    if not world_root.is_dir():
        world_root = world_category
    if not world_root.is_dir():
        raise ConfigError(
            f"找不到世界知识目录：{world_category.relative_to(vault)}；"
            f"若项目的大区名不是「{sub}」，用 --world-subdir 指定"
        )

    # 「总表」在平铺结构里就是同目录。有 `总表/` 子目录时仍按子目录走。
    totals = world_root / "总表"
    if not totals.is_dir():
        totals = world_root
    world_name = str(naming.get("world_table") or "").strip()
    admin_name = str(naming.get("admin_table") or "").strip()
    if not world_name or not admin_name:
        raise ConfigError(
            "版式配置缺少 naming.world_table / naming.admin_table。"
            "这两张表是地图的**数据来源**，ID 属作品数据，代码里不给默认值——"
            "猜一个 WORLD-xxxx/Xxx 出来只会让人以为查过了。"
        )
    world_table = totals / f"{world_name}.md"
    admin_table = totals / f"{admin_name}.md"
    if not world_table.exists() or not admin_table.exists():
        raise ConfigError(
            f"总表不全：期望 {world_table.name} 与 {admin_table.name} 都在 {totals.relative_to(vault)}"
        )

    if xlsx:
        xlsx_path = Path(xlsx).expanduser()
        if not xlsx_path.is_absolute():
            xlsx_path = pp.root / xlsx_path
    else:
        rel = str(naming.get("xlsx_path") or "").strip()
        if not rel:
            raise ConfigError("版式配置缺少 naming.xlsx_path，且命令行没给 --xlsx")
        xlsx_path = pp.root / rel
    cfg_mod.ensure_inside(xlsx_path, vault)

    return MapPaths(
        vault=vault,
        project_root=pp.root,
        world_table=world_table,
        admin_table=admin_table,
        loc_dir=pp.canon_dir("地理与地点"),
        map_dir=map_dir,
        layout_file=layout_file,
        xlsx=xlsx_path,
    )


def describe(paths: MapPaths) -> list[str]:
    """给 CLI 打印用的一行一路径清单。"""
    return [
        f"州域志  : {paths.world_table}",
        f"区划表  : {paths.admin_table}",
        f"地点目录: {paths.loc_dir}",
        f"输出目录: {paths.map_dir}",
        f"版式    : {paths.layout_file}",
    ]
