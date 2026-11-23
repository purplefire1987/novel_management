"""两份 defaults.yaml 必须逐字节一致。

`config/defaults.yaml` 是仓库内的权威副本，`src/novel_management/config/defaults.yaml`
是运行期随包分发的那份。两份漂移会导致"装了包跑出来的行为"和"读仓库看到的行为"
不一样，而且不会有任何报错——正是这类静默差异最难查。
"""

from __future__ import annotations

from pathlib import Path

from novel_management import config as cfg_mod

REPO_COPY = cfg_mod.PACKAGE_ROOT / "config" / "defaults.yaml"
PACKAGE_COPY = Path(cfg_mod.__file__).resolve().parent / "defaults.yaml"


def test_both_copies_exist() -> None:
    assert REPO_COPY.exists(), f"缺权威副本：{REPO_COPY}"
    assert PACKAGE_COPY.exists(), f"缺包内副本：{PACKAGE_COPY}"


def test_copies_are_identical() -> None:
    repo = REPO_COPY.read_bytes()
    package = PACKAGE_COPY.read_bytes()
    assert repo == package, "config/defaults.yaml 与包内副本不一致：改一处必须同步另一处"
