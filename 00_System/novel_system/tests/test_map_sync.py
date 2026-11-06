"""地图同步的两个易错点：受控块不能当输入、城要算进它所属的府与州。

两条原先都靠「上一轮的渲染结果」间接成立，看着能跑，其实是自反馈：

    GEO-APPEARANCE 写出「州 › 府 › 城」 → 下一轮把链里的 ID 读回来 → 州页就有了名单

只要有人把 ①剥块 或 ②补上级 当成多余代码删掉，症状都是**静默的**：地图页少几行，
或者正文里随手一个地名链接被永久钉成落点。这里各留一条守卫。
"""

from __future__ import annotations

from pathlib import Path

from novel_system.workflow import map_sync
from novel_system.workflow.map_sync import Entity, Place


def test_auto_block_is_not_read_back() -> None:
    body = (
        "正文只提到一次某个城，顺手写了双链。\n\n"
        "<!-- AUTO:GEO-APPEARANCE -->\n\n"
        "## 地理位置与出场（自动）\n\n"
        "- **所在地**：[[LOC-0001_甲州|甲州]] › [[LOC-0003_丙城|丙城]]\n\n"
        "<!-- /AUTO:GEO-APPEARANCE -->\n"
    )
    assert "LOC-0003" in body, "前提：块里确实写着地点"
    assert "LOC-0003" not in map_sync._strip_auto(body), "受控块必须剥掉，否则地点会自我固化"


def _place(pid: str, name: str, *, state: str = "", fu: str = "") -> Place:
    return Place(id=pid, name=name, path=Path(f"/tmp/{pid}.md"), state=state, fu=fu)


def test_place_hierarchy_is_filled_upwards() -> None:
    places = {
        "LOC-0001": _place("LOC-0001", "甲州"),
        "LOC-0002": _place("LOC-0002", "乙府", state="LOC-0001"),
        "LOC-0003": _place("LOC-0003", "丙城", state="LOC-0001", fu="LOC-0002"),
    }
    item = Entity(
        id="ITEM-0001",
        name="某物",
        kind="物品",
        path=Path("/tmp/ITEM-0001_某物.md"),
        places={"LOC-0003"},
    )
    map_sync.resolve_places([item], places)
    assert item.places == {"LOC-0001", "LOC-0002", "LOC-0003"}, "写在城里的东西，州页与府页也该看见"
