"""正文口径的守卫：3,000–4,000 汉字、「不是A，是B」每千字不超过 1 处。

这两条是作者 11-06 定的规矩，写在 `00_System/Prompts/writer.md` 第一节与第二节；
这里只验证「规矩能不能被机器读到」——读不到就等于没定。
"""

from __future__ import annotations

from pathlib import Path

from novel_system.config import Config
from novel_system.validators import integrity as I


def _cfg(tmp_path: Path) -> Config:
    return Config(vault=tmp_path, system={}, id_rules={}, permissions={}, workflow={}, agents={})


def _chapter(tmp_path: Path, cid: str, body: str, *, word_count: int | None = None) -> None:
    directory = tmp_path / "08_Manuscripts" / "Volume_01"
    directory.mkdir(parents=True, exist_ok=True)
    head = f"---\nid: {cid}\ntype: manuscript\n"
    if word_count is not None:
        head += f"word_count: {word_count}\n"
    head += "---\n\n## 正文\n\n"
    (directory / f"{cid}.md").write_text(head + body + "\n", encoding="utf-8")


def _run(tmp_path: Path) -> list:
    report = I.Report()
    I._check_manuscript(_cfg(tmp_path), report)
    return report.issues


def test_overlong_chapter_is_reported(tmp_path: Path) -> None:
    _chapter(tmp_path, "CH-0001", "雨落在瓦上。" * 900)  # 4,500 汉字，越上限
    assert any(i.category == "篇幅越界" for i in _run(tmp_path))


def test_in_range_chapter_passes(tmp_path: Path) -> None:
    body = "雨落在瓦上，敲出一层白雾。" * 300  # 3,300 汉字
    _chapter(tmp_path, "CH-0001", body, word_count=len(I.HAN.findall(body)))
    assert _run(tmp_path) == []


def test_declared_word_count_drift_is_reported(tmp_path: Path) -> None:
    body = "雨落在瓦上，敲出一层白雾。" * 260
    _chapter(tmp_path, "CH-0001", body, word_count=100)
    assert any(i.category == "字数漂移" for i in _run(tmp_path))


def test_not_but_density_is_capped(tmp_path: Path) -> None:
    filler = "雨落在瓦上，敲出一层白雾。" * 100  # 约 1,200 汉字
    repeated = "先看的不是脸，是脚。" * 12  # 12 处 / 千字，远超上限
    _chapter(tmp_path, "CH-0001", filler + repeated)
    issues = _run(tmp_path)
    assert any(i.category == "句式密度" for i in issues)
