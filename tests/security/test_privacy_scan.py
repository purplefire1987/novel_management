"""隐私扫描器自身的测试。

扫描器漏报比不扫更危险——一个永远返回 0 的扫描器会让所有人误以为仓库是干净的。
所以这里既有「该报的必须报」，也有「不该报的不能报」。

privacy-scan: ignore-file
本文件本身必须摆合成样本（假密钥、假家目录路径），已显式声明豁免。
这行只在 tests/ 下有效；仓库正文里写它不会关闭扫描。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("privacy_scan", ROOT / "scripts" / "privacy_scan.py")
privacy_scan = importlib.util.module_from_spec(_spec)
sys.modules["privacy_scan"] = privacy_scan
_spec.loader.exec_module(privacy_scan)


def scan(repo: Path) -> list:
    findings, _ = privacy_scan.scan_worktree(repo)
    return findings


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_clean_repo_passes(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n纯虚构示例，无私人数据。\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    assert scan(tmp_path) == []


def test_absolute_path_detected(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("数据在 /Users/zhangsan/Vault 里\n", encoding="utf-8")
    found = scan(tmp_path)
    assert "R007" in rules(found)
    assert any("zhangsan" in f.message for f in found)


def test_absolute_path_placeholder_allowed(tmp_path: Path) -> None:
    """文档里写「换成你的用户名」是正常的，不能被判成泄露。"""
    (tmp_path / "docs.md").write_text(
        "配置：/Users/你的用户名/MyVault\n示例：/Users/username/vault\n占位：/Users/<name>/v\n",
        encoding="utf-8",
    )
    assert scan(tmp_path) == []


def test_private_dir_detected(tmp_path: Path) -> None:
    canon = tmp_path / "10_Projects" / "novel-001" / "01_Canon"
    canon.mkdir(parents=True)
    (canon / "World.md").write_text("设定\n", encoding="utf-8")
    found = scan(tmp_path)
    assert "R001" in rules(found)


def test_private_dir_allowed_in_examples(tmp_path: Path) -> None:
    canon = tmp_path / "examples" / "sample" / "01_Canon"
    canon.mkdir(parents=True)
    (canon / "CHAR-0001.md").write_text("虚构人物\n", encoding="utf-8")
    assert scan(tmp_path) == []


def test_private_archive_name_detected(tmp_path: Path) -> None:
    (tmp_path / "CHAR-0001_示例名.md").write_text("人物档案\n", encoding="utf-8")
    assert "R002" in rules(scan(tmp_path))


def test_archive_name_allowed_in_examples(tmp_path: Path) -> None:
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "CHAR-0001_林砚.md").write_text("虚构人物\n", encoding="utf-8")
    assert scan(tmp_path) == []


@pytest.mark.parametrize(
    "content",
    [
        "key = sk-abcdefghijklmnopqrstuvwx\n",
        "token: ghp_abcdefghijklmnopqrstuvwxyz0123\n",
        "AKIAABCDEFGHIJKLMNOP\n",
        "-----BEGIN RSA PRIVATE KEY-----\n",
        'password = "hunter2hunter2hunter2"\n',
    ],
)
def test_secrets_detected(tmp_path: Path, content: str) -> None:
    (tmp_path / "cfg.md").write_text(content, encoding="utf-8")
    assert "R008" in rules(scan(tmp_path))


def test_secret_sample_is_masked(tmp_path: Path) -> None:
    """报告本身不能变成新的泄露面。"""
    (tmp_path / "cfg.md").write_text("key = sk-abcdefghijklmnopqrstuvwx\n", encoding="utf-8")
    found = [f for f in scan(tmp_path) if f.rule == "R008"]
    assert found
    assert "abcdefghijklmnopqrstuvwx" not in found[0].sample


def test_forbidden_suffix_detected(tmp_path: Path) -> None:
    (tmp_path / "backup.sqlite").write_bytes(b"SQLite format 3\x00")
    assert "R004" in rules(scan(tmp_path))


def test_junk_file_warns(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x01")
    found = scan(tmp_path)
    assert "R003" in rules(found)


def test_large_file_warns(tmp_path: Path) -> None:
    (tmp_path / "big.md").write_text("x" * (513 * 1024), encoding="utf-8")
    assert "R005" in rules(scan(tmp_path))


def test_skip_dirs_not_scanned(tmp_path: Path) -> None:
    """临时目录与虚拟环境里的私人路径不该被当成仓库内容。"""
    (tmp_path / ".tmp" / "scratch").mkdir(parents=True)
    (tmp_path / ".tmp" / "scratch" / "x.md").write_text("/Users/zhangsan/v\n", encoding="utf-8")
    assert scan(tmp_path) == []


def test_main_exit_code(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("/Users/zhangsan/Vault\n", encoding="utf-8")
    assert privacy_scan.main(["--repo", str(tmp_path)]) == 1
    (tmp_path / "notes.md").write_text("干净\n", encoding="utf-8")
    assert privacy_scan.main(["--repo", str(tmp_path)]) == 0


def test_main_fail_on_none_always_zero(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("/Users/zhangsan/Vault\n", encoding="utf-8")
    assert privacy_scan.main(["--repo", str(tmp_path), "--fail-on", "none"]) == 0


def test_line_level_ignore(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(
        "正常一行\n/Users/zhangsan/Vault  <!-- privacy-scan: ignore -->\n", encoding="utf-8"
    )
    assert scan(tmp_path) == []


def test_file_level_ignore_only_inside_tests(tmp_path: Path) -> None:
    """「写一行注释就把扫描关掉」不能成为绕过路径。"""
    body = "privacy-scan: ignore-file\n/Users/zhangsan/Vault\n"

    loose = tmp_path / "docs"
    loose.mkdir()
    (loose / "leak.md").write_text(body, encoding="utf-8")
    assert "R007" in rules(scan(tmp_path))

    allowed = tmp_path / "tests"
    allowed.mkdir()
    (allowed / "fixture.md").write_text(body, encoding="utf-8")
    found = [f for f in scan(tmp_path) if "fixture.md" in f.path]
    assert found == []


def test_repo_itself_is_clean() -> None:
    """本仓库自己必须随时是干净的——这条挂了说明有东西混进来了。"""
    findings, _ = privacy_scan.scan_worktree(ROOT)
    assert findings == [], "\n".join(f"{f.rule} {f.path}:{f.line} {f.message}" for f in findings)
