"""地图命令：把 Canon 里的地名数据落成地图页与两张 canvas。

两个生成器原先住在 Vault 的 `00_System/scripts/` 里，路径写死成
`private/00_Canon/...`。库按 V4.0 重排后它们全部失效。这里不重写渲染逻辑，
只把「路径从哪来」换掉：模块保留原本的函数体，由 `configure()` 注入路径。

约定：
- 默认预演，只报「会改几个文件」；加 `--confirm` 才落盘。
- 幂等的判据是「0 个文件改动」，不是「命令没报错」。
"""

from __future__ import annotations

import argparse

from .. import config as cfg_mod
from novel_management.adapters import maps
from ..domain.errors import UsageError
from ._common import info, ok, project, warn


def _configured(cfg: cfg_mod.Config, args: argparse.Namespace) -> maps.MapPaths:
    pp = project(cfg, args)
    return maps.resolve(
        cfg,
        pp,
        world_subdir=getattr(args, "world_subdir", None),
        xlsx=getattr(args, "xlsx", None),
    )


def cmd_maps(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = getattr(args, "maps_command", None)

    if action == "paths":
        for line in maps.describe(_configured(cfg, args)):
            info(line)
        return 0

    # 延迟导入：这两个模块带模块级可变状态，导入即要求先 configure。
    from novel_management.adapters.maps import admin, jiuzhou

    paths = _configured(cfg, args)
    dry = not getattr(args, "confirm", False)
    if dry:
        warn("预演模式：只统计将改动的文件，不写盘（加 --confirm 执行）")

    if action == "import-admin":
        admin.configure(paths)
        rc = admin.main(["--confirm"] if not dry else [])
        if rc == 0 and not dry:
            ok("行政区划表已重排")
        return rc

    if action == "build":
        jiuzhou.configure(paths)
        argv = ["--maps-only"] if getattr(args, "maps_only", False) else []
        if not dry:
            argv.append("--confirm")
        return jiuzhou.main(argv)

    raise UsageError(f"未知的 maps 子命令：{action}")
