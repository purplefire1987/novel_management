"""索引落在哪：目录约定与「当前生效的是哪一份」。

布局：

```
<runtime>/index/
  active.json          # 指针：{"build_id","db":"builds/xxx.sqlite",...}
  index.json           # 清单：全部条目摘要（人读、兼容旧工具）
  builds/
    IDX-20261120T101500Z-1234.sqlite
    IDX-20261120T104200Z-5678.sqlite
```

指针是唯一「当前生效」的判据。旧 build 不删——它们是不可变快照，
删不删不影响正确性，清理留给以后（known_risks R-18）。

`active_db` 还认旧的 `index/index.sqlite`（0.5.x 之前只有 index.json，
0.6.0 早期版本直接写 index.sqlite）：迁移期不要让老库变成「不存在」。

## 有正式发布时，以发布钉住的索引为准

`index/active.json` 与 `releases/active.json` 是**两个不同的东西**：

- 前者是「上一次重建索引的结果」，重建一次就变；
- 后者是「当前正式版」，只在发布切换时变。

如果两者不一致（发布切换成功但索引指针没写成、或者之后有人重跑 rebuild-index），
按 `index/active.json` 查就会得到「新发布 + 旧索引」或「旧发布 + 新索引」的混搭。
所以有正式发布时，一律用 ReleaseManifest 里钉住的那份索引；
它不存在就报错进恢复模式，**不退回另一份**——退回等于悄悄换版本。
"""

from __future__ import annotations

import json
from pathlib import Path

from ....domain.errors import UsageError

POINTER_NAME = "active.json"
MANIFEST_NAME = "index.json"
LEGACY_DB_NAME = "index.sqlite"


def index_dir(runtime: Path) -> Path:
    return Path(runtime) / "index"


def pointer_path(runtime: Path) -> Path:
    return index_dir(runtime) / POINTER_NAME


def manifest_path(runtime: Path) -> Path:
    return index_dir(runtime) / MANIFEST_NAME


def release_pointer(runtime: Path) -> dict:
    """正式发布指针。不存在返回空字典。损坏时**报错**，不猜。"""
    path = Path(runtime) / "releases" / POINTER_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(
            f"正式指针读不出来：{path}（{exc}）；用 `novel release status` 进入恢复流程"
        ) from exc


def active_db(runtime: Path) -> Path:
    """当前生效的索引库。没有就报错并指路，不返回一个不存在的路径。"""
    directory = index_dir(runtime)
    released = release_pointer(runtime)
    if released:
        build_id = str(released.get("index_build_id") or "")
        if not build_id:
            raise UsageError(
                "正式指针缺 index_build_id；用 `novel release status` 查，不要按目录里最新的库猜"
            )
        db = directory / "builds" / f"{build_id}.sqlite"
        if not db.exists():
            raise UsageError(
                f"正式版 {released.get('release_id')} 钉住的索引不存在：{db}。"
                "依赖它的查询应被阻断；重建该发布的索引或 `novel release rollback`"
            )
        return db

    pointer = directory / POINTER_NAME
    if pointer.exists():
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UsageError(f"索引指针读不出来：{pointer}（{exc}）；重跑 `novel rebuild-index`") from exc
        relative = str(data.get("db") or "")
        if not relative:
            raise UsageError(f"索引指针缺 db 字段：{pointer}；重跑 `novel rebuild-index`")
        return directory / relative

    legacy = directory / LEGACY_DB_NAME
    if legacy.exists():
        return legacy
    raise UsageError(f"索引不存在：{pointer}；先跑 `novel rebuild-index`")


def read_pointer(runtime: Path) -> dict:
    pointer = pointer_path(runtime)
    if not pointer.exists():
        return {}
    try:
        return json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
