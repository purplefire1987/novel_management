"""场景接缝的守卫：跳号、时间倒退、换地点不交代。

这三样在成稿阶段都贵：读者正跟着一个人淋雨，下一场她衣服干了、人在半座城外，
或者镜头跳回半小时前而没有任何提示。细纲是一行字，成稿是一整段——所以查在前面。

用最小 vault（临时目录里只放几份场景卡）跑，不复制整库：这几条检查只认
frontmatter 的 sequence/chapter_id 与正文里的四行字段。
"""

from __future__ import annotations

from pathlib import Path

from novel_system.config import Config
from novel_system.validators import integrity as I


def _cfg(tmp_path: Path) -> Config:
    return Config(
        vault=tmp_path,
        system={},
        id_rules={},
        permissions={},
        workflow={},
        agents={},
    )


def _scene(
    tmp_path: Path,
    sid: str,
    seq: int,
    *,
    chapter: str = "CH-0009",
    place: str = "某地·街面",
    time: str = "'21:00'",
    entry: str = "承接上一场",
    nxt: str = "",
) -> None:
    directory = tmp_path / "04_Outline" / "Scenes"
    directory.mkdir(parents=True, exist_ok=True)
    tail = f"接 {nxt}" if nxt else "无"
    (directory / f"{sid}.md").write_text(
        "---\n"
        f"id: {sid}\n"
        "type: scene_beat\n"
        f"chapter_id: {chapter}\n"
        f"sequence: {seq}\n"
        "---\n\n"
        f"# 场景 {seq}\n\n"
        "- **地点**：" + place + "\n"
        "- **时间**：" + time + "\n"
        "- **承接上文**：" + entry + "\n"
        "- **与下一场景的连接**：" + tail + "\n",
        encoding="utf-8",
    )


def _run(tmp_path: Path) -> list:
    report = I.Report()
    I._check_scene_chain(_cfg(tmp_path), report)
    return report.issues


def test_chinese_date_and_midnight_range_are_parsed() -> None:
    assert I._scene_time("七月初八夜 21:00–21:30") == ((7, 8), 21 * 60, 21 * 60 + 30)
    assert I._scene_time("'21:45'") == (None, 21 * 60 + 45, 21 * 60 + 45)
    assert I._scene_time("七月初九 00:10") == ((7, 9), 10, 10)
    # 跨夜区间要按次日算，否则 22:00–01:00 会被读成「结束早于开始」
    day, start, end = I._scene_time("22:00–01:00")
    assert day is None and end > start


def test_sequence_gap_is_major(tmp_path: Path) -> None:
    _scene(tmp_path, "SCENE-CH-0009-001", 1, nxt="SCENE-CH-0009-003")
    _scene(tmp_path, "SCENE-CH-0009-003", 3, time="'21:20'")
    issues = _run(tmp_path)
    assert any(i.category == "场景跳号" and i.severity == "MAJOR" for i in issues)


def test_time_going_back_is_major(tmp_path: Path) -> None:
    _scene(tmp_path, "SCENE-CH-0009-001", 1, time="七月初八夜 21:00–21:30", nxt="SCENE-CH-0009-002")
    _scene(tmp_path, "SCENE-CH-0009-002", 2, time="'21:10'")
    issues = _run(tmp_path)
    assert any(i.category == "场景时间倒退" for i in issues)


def test_cross_midnight_with_date_is_not_a_regression(tmp_path: Path) -> None:
    """过零点要放行——只要写了日期推进，就不是漏时。"""
    _scene(tmp_path, "SCENE-CH-0009-001", 1, time="七月初八夜 23:45", nxt="SCENE-CH-0009-002")
    _scene(tmp_path, "SCENE-CH-0009-002", 2, time="七月初九 00:10")
    assert not [i for i in _run(tmp_path) if i.category == "场景时间倒退"]


def test_next_must_point_at_sequence_plus_one(tmp_path: Path) -> None:
    _scene(tmp_path, "SCENE-CH-0009-001", 1, nxt="SCENE-CH-0009-003")
    _scene(tmp_path, "SCENE-CH-0009-002", 2, time="'21:10'", nxt="SCENE-CH-0009-003")
    _scene(tmp_path, "SCENE-CH-0009-003", 3, time="'21:20'")
    issues = _run(tmp_path)
    assert any(i.category == "场景跳号" for i in issues)
    assert any(i.category == "场景重入" for i in issues), "两场都指向 003，读者会来回跳"


def test_place_change_needs_an_entry(tmp_path: Path) -> None:
    _scene(tmp_path, "SCENE-CH-0009-001", 1, place="甲地·街面", nxt="SCENE-CH-0009-002")
    _scene(tmp_path, "SCENE-CH-0009-002", 2, place="乙地·水阁", time="'21:10'", entry="")
    issues = _run(tmp_path)
    assert any(i.category == "场景接缝缺失" and "承接上文" in i.detail for i in issues)


def test_clean_chain_is_silent(tmp_path: Path) -> None:
    _scene(tmp_path, "SCENE-CH-0009-001", 1, nxt="SCENE-CH-0009-002")
    _scene(tmp_path, "SCENE-CH-0009-002", 2, time="'21:10'")
    assert _run(tmp_path) == []
