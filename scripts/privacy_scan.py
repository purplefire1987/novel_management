#!/usr/bin/env python3
"""公共仓库隐私扫描。

判据只有一条：**这段内容能不能让陌生人还原出作品。**

能自动判的部分写在这里，判不了的部分（例如「这段设定像不像真设定」）不假装能判——
扫描器只报它确定的事，剩下的交给人工审查。漏报比噪音更危险，所以规则宁可偏严，
但每条规则都给出可解释的理由。

用法：
    python3 scripts/privacy_scan.py --repo .
    python3 scripts/privacy_scan.py --repo . --fail-on warn
    python3 scripts/privacy_scan.py --repo . --commits 50
    python3 scripts/privacy_scan.py --repo . --json

退出码：
    0  没有达到阈值的发现
    1  有达到阈值的发现
    2  用法错误（仓库不存在等）
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------

# 私人 Vault 的目录形态。出现在公仓里就是错位——除了 examples/ 下的虚构示例。
PRIVATE_DIR_NAMES = {
    "10_Projects",
    "00_Canon",
    "01_Canon",
    "02_Planning",
    "03_Manuscripts",
    "04_Story_Assets",
    "05_Ledgers",
    "06_Memory",
    "07_Staging",
    "08_Reviews",
    "90_Archive",
    "01_Hot_Memory",
    "02_Short_Term_Memory",
    "03_Outline",
    "05_Plot_Ledger",
    "06_Manuscripts",
    "07_Archive",
}

# 本项目真实档案的命名形状：<前缀>-<四位数字>_<中文名>.md
# 这个形状在旧系统里出现过 966 次，是泄露时最典型的一类文件。
PRIVATE_ARCHIVE_NAME = re.compile(
    r"^(?:CHAR|LOC|ORG|SYS|SKILL|DEST|CREATURE|WORLD|RULE|EVT|RELSHIP|ASSET|FACT|FACTION)"
    r"-\d{4}_[\u4e00-\u9fff]{2,}\.md$"
)

# 本机绝对路径：/Users/<name>/、/home/<name>/、C:\Users\<name>\
ABS_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\?Users\\\\?)([^/\s\"'<>|`()\[\]]+)")

# 只有「看起来像真实用户名」的片段才算泄露。
# 文档里写 `/Users/<name>/`、grep 模式里写 `/Users/\|/home/`、正则源码里的 `/home/[a-z]`
# 都不是路径；用字符集把它们排除掉，避免把排障文档本身判成泄露。
LOOKS_LIKE_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")

# 占位名不算泄露——文档里写「换成你的用户名」是正常的。
PLACEHOLDER_NAMES = {
    "username",
    "user",
    "yourname",
    "yourusername",
    "your-name",
    "you",
    "example",
    "someone",
    "name",
    "xxx",
    "abc",
    "你的用户名",
    "用户名",
    "作者名",
}

SECRET_PATTERNS = [
    ("私钥块", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("OpenAI 风格密钥", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{12,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("微信/企业微信密钥", re.compile(r"\b(?:corpsecret|corpid)\s*[:=]\s*[\"']?[A-Za-z0-9_-]{12,}")),
    (
        "明文口令或令牌",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}"
        ),
    ),
]

# 这些后缀不该出现在公仓：数据库、备份、日志、缓存、凭据文件。
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".bak",
    ".bundle",
    ".dump",
    ".log",
    ".pyc",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".tar",
    ".gz",
    ".tgz",
    ".zip",
    ".7z",
    ".rar",
    ".sql",
}

# 系统垃圾，不该提交。
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".tmp",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".idea",
}

TEXT_SUFFIXES = {
    "",  # .gitignore / LICENSE / pre-commit 配置这类无后缀文件
    ".py",
    ".md",
    ".markdown",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".txt",
    ".cfg",
    ".ini",
    ".sh",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".xml",
    ".csv",
}

MAX_TEXT_BYTES = 2_000_000
LARGE_FILE_BYTES = 512 * 1024

SEVERITY_ORDER = {"info": 0, "warn": 1, "error": 2}


class Finding:
    __slots__ = ("rule", "severity", "path", "line", "message", "sample")

    def __init__(self, rule, severity, path, message, line=0, sample=""):
        self.rule = rule
        self.severity = severity
        self.path = path
        self.line = line
        self.message = message
        self.sample = sample

    def as_dict(self):
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "sample": self.sample,
        }


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if path.name in SKIP_DIRS:
            continue
        if path.is_file():
            yield path


def _is_example(rel: str) -> bool:
    return rel.startswith("examples/") or rel.startswith("tests/fixtures/")


def _mask(text: str, keep: int = 4) -> str:
    """示例只留头尾，避免扫描报告本身变成新的泄露面。"""
    text = text.strip()
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}…{text[-keep:]}"


def scan_file(path: Path, root: Path, findings: list[Finding]) -> None:
    rel = path.relative_to(root).as_posix()

    # R001 私人目录形态
    parts = Path(rel).parts
    for part in parts[:-1]:
        if part in PRIVATE_DIR_NAMES and not _is_example(rel):
            findings.append(
                Finding(
                    "R001",
                    "error",
                    rel,
                    f"路径包含私人 Vault 目录名 `{part}`；公仓只允许 examples/ 下出现同名结构",
                )
            )
            break

    # R002 私人档案命名
    if PRIVATE_ARCHIVE_NAME.match(path.name) and not _is_example(rel):
        findings.append(
            Finding("R002", "error", rel, "文件名是真实档案的命名形状（前缀-编号_中文名），疑似作品内容")
        )

    # R003 系统垃圾
    if path.name in JUNK_NAMES:
        findings.append(Finding("R003", "warn", rel, "系统垃圾文件不应提交"))
        return

    # R004 禁止的文件类型
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        findings.append(Finding("R004", "error", rel, f"`{suffix}` 类文件不得进入公仓（备份/数据库/日志/凭据）"))
        return

    size = path.stat().st_size

    # R005 大文件
    if size > LARGE_FILE_BYTES:
        findings.append(
            Finding("R005", "warn", rel, f"文件 {size / 1024:.0f} KB，超过 512 KB；确认不是备份或数据导出")
        )

    if suffix not in TEXT_SUFFIXES or size > MAX_TEXT_BYTES:
        return

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        findings.append(Finding("R006", "warn", rel, "含非 UTF-8 字节，可能是二进制或编码混杂，请人工确认"))
        return

    # 内容级豁免：只对 tests/ 下的文件生效，因为测试本来就要摆合成样本
    # （假的密钥、假的家目录路径）。仓库正文里留这个标记是无效的——
    # 否则「写一行注释就把扫描关掉」会成为新的绕过路径。
    if "privacy-scan: ignore-file" in text and rel.startswith("tests/"):
        return

    # R007 本机绝对路径
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "privacy-scan: ignore" in line:
            continue
        for match in ABS_PATH.finditer(line):
            name = match.group(1).strip()
            if name.lower() in PLACEHOLDER_NAMES or name.startswith("你") or name.startswith("<"):
                continue
            if not LOOKS_LIKE_USER.match(name):
                continue
            findings.append(
                Finding(
                    "R007",
                    "error",
                    rel,
                    f"出现本机绝对路径（用户名 `{name}`）；文档里应写占位名",
                    lineno,
                    _mask(match.group(0)),
                )
            )
            break

    # R008 凭据
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "privacy-scan: ignore" in line:
            continue
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(
                    Finding("R008", "error", rel, f"疑似 {label}", lineno, _mask(match.group(0)))
                )
                break


def scan_worktree(root: Path) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    count = 0
    for path in _iter_files(root):
        count += 1
        scan_file(path, root, findings)
    return findings, count


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def scan_commits(root: Path, limit: int, findings: list[Finding]) -> int:
    """扫最近的提交：提交信息和新增文件路径。

    只看「曾经加进来过什么」——历史里删掉的文件同样算泄露过，所以不按当前树过滤。
    """
    if not (root / ".git").exists():
        return 0
    count = 0
    log = _git(root, "log", f"-{limit}", "--pretty=format:@@COMMIT@@%h%x09%s")
    for line in log.splitlines():
        if line.startswith("@@COMMIT@@"):
            count += 1
            subject = line.split("\t", 1)[-1]
            for name in re.findall(r"/Users/[^/\s]+|/home/[^/\s]+", subject):
                findings.append(Finding("R009", "error", f"commit@{line[10:17]}", f"提交信息含绝对路径 `{_mask(name)}`"))

    names = _git(root, "log", f"-{limit}", "--name-only", "--pretty=format:")
    for raw in names.splitlines():
        rel = raw.strip()
        if not rel:
            continue
        base = Path(rel).name
        if base in JUNK_NAMES:
            findings.append(Finding("R010", "warn", f"history:{rel}", "历史中出现过系统垃圾文件"))
        if PRIVATE_ARCHIVE_NAME.match(base) and not _is_example(rel):
            findings.append(
                Finding("R011", "error", f"history:{rel}", "历史中出现过真实档案命名的文件——需按 SECURITY.md 流程处置")
            )
        for part in Path(rel).parts[:-1]:
            if part in PRIVATE_DIR_NAMES and not _is_example(rel):
                findings.append(Finding("R012", "error", f"history:{rel}", f"历史中出现过私人目录 `{part}`"))
                break
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="公共仓库隐私扫描")
    parser.add_argument("--repo", default=".", help="仓库根目录")
    parser.add_argument("--fail-on", choices=["info", "warn", "error", "none"], default="error")
    parser.add_argument("--commits", type=int, default=0, help="额外扫描最近 N 条提交（含历史路径）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = parser.parse_args(argv)

    root = Path(args.repo).expanduser().resolve()
    if not root.is_dir():
        print(f"不是目录：{root}")
        return 2

    findings, scanned = scan_worktree(root)
    commits = scan_commits(root, args.commits, findings) if args.commits > 0 else 0

    errors = sum(1 for f in findings if f.severity == "error")
    warns = sum(1 for f in findings if f.severity == "warn")
    infos = sum(1 for f in findings if f.severity == "info")

    if args.json:
        print(
            json.dumps(
                {
                    "repo": str(root),
                    "files_scanned": scanned,
                    "commits_scanned": commits,
                    "errors": errors,
                    "warnings": warns,
                    "findings": [f.as_dict() for f in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not args.quiet and findings:
            for f in sorted(findings, key=lambda x: (x.severity, x.path, x.line)):
                loc = f"{f.path}:{f.line}" if f.line else f.path
                mark = {"error": "错误", "warn": "警告", "info": "提示"}[f.severity]
                sample = f"  [{f.sample}]" if f.sample else ""
                print(f"[{mark}] {f.rule} {loc}  {f.message}{sample}")
            print()
        scan_note = f"，另扫 {commits} 条提交" if commits else ""
        print(f"扫描 {scanned} 个文件{scan_note}：错误 {errors}，警告 {warns}，提示 {infos}")

    if args.fail_on == "none":
        return 0
    threshold = SEVERITY_ORDER[args.fail_on]
    worst = 2 if errors else (1 if warns else 0)
    return 1 if worst >= threshold and worst > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
