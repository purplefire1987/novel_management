#!/usr/bin/env python
"""实体 ↔ 地图联动的手动入口。

正常情况下不需要单独跑：`novel rebuild-index` 与 `novel publish` 都会顺手调用。
只有在怀疑地图页过期、或想先看统计不做写盘时，才用这个脚本。

    python 00_System/scripts/link_entities_to_map.py          # 重算并写盘
    python 00_System/scripts/link_entities_to_map.py --dry-run # 只看统计
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "00_System" / "novel_system" / "src"))

from novel_system.config import Config  # noqa: E402
from novel_system.workflow import map_sync  # noqa: E402


def main() -> int:
    cfg = Config.load(ROOT)
    dry = "--dry-run" in sys.argv
    stats = map_sync.run(cfg, write=not dry)
    print("实体 ↔ 地图联动" + ("（预演，未写盘）" if dry else ""))
    print(f"  实体总数      {stats['entities']}")
    print(f"  已挂地点      {stats['located']}")
    print(f"    直接写出    {stats['direct']}")
    print(f"    依势力推断  {stats['inferred']}")
    print(f"  未挂地点      {stats['orphan']}")
    print(f"  地点档案      {stats['places']}")
    print(f"  已入正文章节  {stats['chapters']}")
    print(f"  改动文件      {stats['files_touched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
