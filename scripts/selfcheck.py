#!/usr/bin/env python3
"""端到端冒烟自检：不依赖 pytest / tempfile，只用一个普通目录。

用途：
- 真机上一键确认主链（含 T06 的 prepare → approve release → publish）仍然通；
- 受限环境（临时目录不可用）里替代 pytest 做基本验证。

用法：
    python3 scripts/selfcheck.py [工作目录]
    python3 scripts/selfcheck.py --keep      # 保留现场供排查

退出码：0 全通，1 有失败。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_system.cli import main  # noqa: E402
from novel_system.storage import sha256_file  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def _verdict(out: str) -> str:
    """从 `release status --json` 的输出里取判词。读不出返回空串。"""
    try:
        return str(json.loads(out).get("verdict") or "")
    except (ValueError, AttributeError):
        return ""


def run(label: str, argv: list[str], expect: int | None = 0, check=None) -> None:
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
    except Exception as exc:  # noqa: BLE001 - 自检要把异常变成一行失败
        RESULTS.append((label, False, f"异常 {type(exc).__name__}: {exc}"))
        return
    out = buffer.getvalue()
    if expect is not None and code != expect:
        RESULTS.append((label, False, f"退出码 {code}，期望 {expect}\n{out.strip()[-400:]}"))
        return
    if check is not None:
        ok, detail = check(out)
        RESULTS.append((label, ok, detail))
        return
    RESULTS.append((label, True, ""))


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="novel CLI 端到端冒烟自检")
    parser.add_argument("workdir", nargs="?", default="")
    parser.add_argument("--keep", action="store_true", help="保留现场")
    args = parser.parse_args()

    work = (Path(args.workdir).expanduser() if args.workdir else ROOT / ".tmp" / "selfcheck").resolve()
    if work.exists() and any(work.iterdir()):
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    vault, runtime = work / "vault", work / "runtime"

    common = ["--vault", str(vault), "--runtime", str(runtime)]
    project = vault / "10_Projects" / "novel-001_smoke"
    draft = work / "draft.md"
    draft.write_text("---\nid: CH-0001\ntype: chapter\n---\n\n第一章。\n", encoding="utf-8")
    target = "03_Manuscripts/Drafts/CH-0001.md"

    run("init-author", ["init-author", *common, "--confirm"])
    run("init-project", ["init-project", *common, "--id", "novel-001", "--name", "Smoke", "--confirm"])
    # 临时 Vault 建在仓库内，doctor 的隔离检查必然报警——只验它能跑完并给出结论
    run("doctor", ["doctor", *common], expect=None, check=lambda out: ("doctor:" in out, "缺少结论行"))
    run("validate", ["validate", *common])
    run("task create", ["task", "create", *common, "--type", "draft", "--assignee", "writer", "--as", "writer"])
    run("task inspect", ["task", "inspect", *common, "TASK-000001"])
    run(
        "stage",
        ["stage", *common, "--task", "TASK-000001", "--from", str(draft), "--to", target, "--as", "writer"],
    )
    run("diff", ["diff", *common, "--task", "TASK-000001"])
    # T06 之后 publish 只执行「已准备且已批准」的计划：两道门都要先关上才算验过
    run(
        "release prepare 未批准必须拒绝",
        ["release", "prepare", *common, "--task", "TASK-000001", "--as", "author"],
        expect=4,
    )
    run("approve 非作者必须拒绝", ["approve", *common, "--task", "TASK-000001", "--as", "writer"], expect=3)
    run("approve", ["approve", *common, "--task", "TASK-000001", "--as", "author"])
    run("publish 无计划必须拒绝", ["publish", *common, "--task", "TASK-000001", "--as", "author", "--confirm"], expect=2)
    run("release prepare", ["release", "prepare", *common, "--task", "TASK-000001", "--as", "author"])

    release_dir = runtime / "releases" / "REL-000001"
    plan_hash = sha256_file(release_dir / "plan.json")
    run(
        "publish 计划未批准必须拒绝",
        ["publish", *common, "--task", "TASK-000001", "--as", "author", "--confirm"],
        expect=4,
    )
    run(
        "approve release",
        ["approve", "release", "REL-000001", "--hash", plan_hash, "--as", "author", *common],
    )
    run("publish 缺确认必须拒绝", ["publish", *common, "--task", "TASK-000001", "--as", "author"], expect=2)
    run("publish", ["publish", *common, "--task", "TASK-000001", "--as", "author", "--confirm"])

    def published(out: str):
        path = project / target
        if not path.exists():
            return False, "正式文件未生成"
        return path.read_text(encoding="utf-8").startswith("---"), ""

    RESULTS.append(("正式文件已写入", *published("")))
    run(
        "release status 判词 OK",
        ["release", "status", *common, "--json"],
        check=lambda out: (_verdict(out) == "OK", f"判词 {_verdict(out) or '（读不出）'}"),
    )

    run("publish 重复必须拒绝", ["publish", *common, "--task", "TASK-000001", "--as", "author", "--confirm"], expect=1)
    run("adopt", ["adopt", *common, "--as", "author"])
    run("backup", ["backup", *common, "--as", "author", "--label", "smoke"])

    backups = sorted(p.name for p in (runtime / "backups").glob("BK-*"))
    if not backups:
        RESULTS.append(("backup 产物存在", False, "未找到 BK-* 目录"))
    else:
        meta = json.loads((runtime / "backups" / backups[-1] / "meta.json").read_text(encoding="utf-8"))
        RESULTS.append(("backup 校验通过", bool(meta.get("verified")), f"files={meta.get('files')}"))
        run("restore 预演", ["restore", *common, "--backup-id", backups[-1], "--dry-run"])

    run("rebuild-index", ["rebuild-index", *common])
    run("status", ["status", *common])

    width = max(len(label) for label, _, _ in RESULTS)
    failed = 0
    for label, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label.ljust(width)}  {detail if not ok else ''}".rstrip())
        if not ok:
            failed += 1

    print()
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} 通过")
    print(f"现场：{work}")
    if not args.keep and failed == 0:
        shutil.rmtree(work, ignore_errors=True)
        print("（现场已清理，加 --keep 可保留）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
