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

from novel_management.cli import main  # noqa: E402
from novel_management.storage import sha256_file  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def _verdict(out: str) -> str:
    """从 `release status --json` 的输出里取判词。读不出返回空串。"""
    try:
        return str(json.loads(out).get("verdict") or "")
    except (ValueError, AttributeError):
        return ""


def _all(*pairs) -> tuple[bool, str]:
    """把多个 (判据, 失败说明) 合成一条结论：全真才真，否则报第一条失败的说明。"""
    for ok, detail in pairs:
        if not ok:
            return False, detail
    return True, ""


def _write_t08_fixtures(project: Path) -> None:
    """给 preflight 造一份最小规划 + 一个实体。

    不加这一段，`preflight` 会因为「找不到规划」直接 BLOCKED——那也算跑通，
    但验不到「正常输入能 PASS」，而后者才是这条自检想知道的事。
    """
    plan = project / "02_Planning" / "Chapter_Outlines" / "CH-0001_smoke.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        "---\n"
        "id: CH-0001\n"
        "type: chapter_outline\n"
        "title: 自检章\n"
        "timeline: 兴元十二年七月初八\n"
        "pov: 抄书人\n"
        "participants:\n- CHAR-0001\n"
        "entities:\n- CHAR-0001\n"
        "core_conflict: 自检用冲突：书匣必须在雨停前离岸\n"
        "creative_scope:\n"
        "  allowed:\n  - 符合人物动机的对话\n"
        "  forbidden:\n  - 人物复活\n"
        "must_happen:\n- 抄书人在渡口交出书匣\n"
        "must_not_happen:\n- 任何人认出抄书人的真名\n"
        "---\n\n# CH-0001\n\n## 场景\n\n灰石渡的石阶被雨泡了三天。\n",
        encoding="utf-8",
    )
    char = project / "01_Canon" / "Characters" / "CHAR-0001.md"
    char.parent.mkdir(parents=True, exist_ok=True)
    char.write_text(
        "---\nid: CHAR-0001\ntype: character\nname: 抄书人\n---\n\n抄书人常年在灰石渡替人抄经。\n",
        encoding="utf-8",
    )


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
        ["release", "prepare", *common, "--task", "TASK-000001", "--skip-closure", "--as", "author"],
        expect=4,
    )
    run("approve 非作者必须拒绝", ["approve", *common, "--task", "TASK-000001", "--as", "writer"], expect=3)
    run("approve", ["approve", *common, "--task", "TASK-000001", "--as", "author"])
    run("publish 无计划必须拒绝", ["publish", *common, "--task", "TASK-000001", "--as", "author", "--confirm"], expect=2)
    # 这一段测发布协议本身；共同发布闸在后面 T09/T10 段单独验。
    run(
        "release prepare",
        ["release", "prepare", *common, "--task", "TASK-000001", "--skip-closure", "--as", "author"],
    )

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

    # ---- T07 原稿导入：scan（不写正式区）→ stage（只写 staging） ----
    old_src = work / "old"
    old_src.mkdir(parents=True, exist_ok=True)
    (old_src / "旧稿.md").write_text(
        "# 第一章 渡口\n\n雨落在石阶上。CHAR-0001 撑着伞。\n\n# 第二章 夜航\n\n船停了一夜。\n",
        encoding="utf-8",
    )
    # 非 UTF-8 必须被拒绝：不猜编码是硬约定，报告里要写明
    (old_src / "乱码.txt").write_bytes("第一章 乱码\n\n正文\n".encode("gbk"))

    def scan_said_no(out: str):
        if "IMP-000001" not in out:
            return False, "报告里没有导入号"
        if "不猜" not in out:
            return False, "GBK 文件未被拒绝，或拒绝理由没写清"
        return True, ""

    # 有维度未达标（GBK 被拒）时 scan 返回 1：部分失败不许报成全量成功
    run(
        "import scan 部分失败返回 1",
        ["import", "scan", *common, str(old_src), "--as", "ingestor", "--batch-size", "1"],
        expect=1,
        check=scan_said_no,
    )
    run("import list", ["import", "list", *common])
    run("import report", ["import", "report", *common, "IMP-000001", "--chapters"])
    run(
        "import where 反查原文",
        ["import", "where", *common, "IMP-000001", "旧稿.md#L3"],
        check=lambda out: ("石阶" in out, "定位符没取回正文"),
    )
    run(
        "import stage 第一批",
        ["import", "stage", *common, "IMP-000001", "--batch", "1", "--batch-size", "1", "--as", "writer"],
    )
    # 第二批发号曾经被 staging 里的副本判成撞号而卡死，这里钉住它
    run(
        "import stage 第二批（发号不被 staging 副本卡住）",
        ["import", "stage", *common, "IMP-000001", "--batch", "2", "--batch-size", "1", "--as", "writer"],
    )

    ledger = project / "05_Ledgers" / "Imports" / "IMP-000001.md"
    staging_ledger = sorted((project / "07_Staging").glob("*/ingest/05_Ledgers/Imports/IMP-000001.md"))
    if not staging_ledger:
        RESULTS.append(("台账覆盖全计划", False, "staging 里没有台账"))
    else:
        text = staging_ledger[-1].read_text(encoding="utf-8")
        ok = "第一章 渡口" in text and "第二章 夜航" in text and "staged_batches: [1, 2]" in text
        RESULTS.append(("台账覆盖全计划", ok, "台账丢了已发批次或没有批次记录"))

    run("backup", ["backup", *common, "--as", "author", "--label", "smoke"])

    backups = sorted(p.name for p in (runtime / "backups").glob("BK-*"))
    if not backups:
        RESULTS.append(("backup 产物存在", False, "未找到 BK-* 目录"))
    else:
        meta = json.loads((runtime / "backups" / backups[-1] / "meta.json").read_text(encoding="utf-8"))
        RESULTS.append(("backup 校验通过", bool(meta.get("verified")), f"files={meta.get('files')}"))
        run("restore 预演", ["restore", *common, "--backup-id", backups[-1], "--dry-run"])

    # ---- T08 写前资料包：规划 → 索引 → preflight → 九角色 ----
    _write_t08_fixtures(project)
    run("rebuild-index", ["rebuild-index", *common])
    run(
        "agent list 九角色",
        ["agent", "list", *common],
        check=lambda out: _all(
            ("九份角色卡都在位" in out, "角色卡缺失或权限表没读到"),
            ("ingestor" in out and "writer" in out, "角色名单不全"),
        ),
    )
    run(
        "agent prompt 九项公共约束",
        ["agent", "prompt", *common, "--role", "writer"],
        check=lambda out: ("九项公共约束齐全" in out, "公共前缀缺项"),
    )
    run(
        "preflight 出资料包",
        ["preflight", *common, "CH-0001", "--no-write"],
        check=lambda out: _all(
            ("gate_result : PASS" in out, "正常输入下 gate 不是 PASS"),
            ("没有阻断项" in out, "有意外阻断项"),
            ("调用计量" in out, "资料包没记调用计量"),
        ),
    )
    run(
        "preflight 预算不够必须 BLOCKED",
        [
            "preflight", *common, "CH-0001",
            "--budget", "1500", "--output-reserve", "200", "--hard-reserve", "100",
            "--no-write",
        ],
        expect=1,
        check=lambda out: _all(
            ("BLOCKED" in out, "硬约束装不下却没有阻断"),
            ("硬约束放不下预算" in out, "阻断理由没写清"),
        ),
    )
    run(
        "preflight 截断必须可见",
        [
            "preflight", *common, "CH-0001",
            "--budget", "2500", "--output-reserve", "3000", "--hard-reserve", "1000",
            "--no-write",
        ],
        expect=None,
        check=lambda out: _all(
            ("处结果被截断" in out, "截断了却没说"),
            ("别当成「就这么多」" in out, "截断提示没写清"),
        ),
    )
    run(
        "agent usage 台账有记录",
        ["agent", "usage", *common, "--json"],
        check=lambda out: _all(
            ('"tool_calls": 0' not in out, "台账里没有工具调用，计量没落盘"),
        ),
    )
    # ---- T09 创作与审核 / T10 记忆闭环 ----
    mock = work / "mock-writer.json"
    mock.write_text(
        json.dumps(
            {
                "task_id": "TASK-000001",
                "role": "writer",
                "result": {
                    "chapter_id": "CH-0001",
                    "title": "自检章",
                    "chapter_text": "雨落在灰石渡的石阶上，抄书人把最后一页纸压进书匣。\n\n"
                    "船家接过匣子，没有问里面是什么。\n",
                    "new_fact_candidates": [],
                    "plan_deviations": [],
                    "open_questions": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 用上一个任务号：selfcheck 前面已经建过若干任务
    tasks = sorted((runtime / "tasks").glob("TASK-*.yaml"))
    run("task create（写作任务）", ["task", "create", *common, "--type", "draft", "--assignee", "writer", "--as", "writer"])
    write_task = sorted((runtime / "tasks").glob("TASK-*.yaml"))[-1].stem

    run(
        "plan check 规划够格",
        ["plan", "check", *common, "CH-0001"],
        check=lambda out: ("够格开写" in out, "规划缺必需字段"),
    )
    canon_before = sorted(
        str(p.relative_to(project)) for p in (project / "01_Canon").rglob("*.md")
    )
    run(
        "write 草稿只进 staging",
        ["write", *common, "--task", write_task, "--chapter", "CH-0001", "--mock-response", str(mock), "--as", "writer"],
        check=lambda out: _all(
            ("草稿已进 staging" in out, "没有落到 staging"),
            ("03_Manuscripts" in out, "目标不是正文路径"),
        ),
    )
    canon_after = sorted(str(p.relative_to(project)) for p in (project / "01_Canon").rglob("*.md"))
    RESULTS.append(
        (
            "草稿没写进 01_Canon",
            canon_before == canon_after and not (project / "01_Canon" / "Chapters" / "CH-0001.md").exists(),
            "草稿流程动了正式区",
        )
    )

    run(
        "review 高风险不许自审",
        ["review", "submit", *common, "--task", write_task, "--chapter", "CH-0001",
         "--role", "writer", "--verdict", "approve", "--as", "writer"],
        expect=3,   # SecurityError：自审是安全拦截，不是普通用法错误
        check=lambda out: ("不能给自己写的稿" in out, "自审没被拦住"),
    )
    run(
        "review 编辑通过",
        ["review", "submit", *common, "--task", write_task, "--chapter", "CH-0001",
         "--role", "editor", "--verdict", "approve", "--as", "editor"],
    )
    run(
        "approve text 正文批准",
        ["approve", "text", *common, "--task", write_task, "--chapter", "CH-0001", "--as", "author"],
        check=lambda out: ("CHAPTER_TEXT" in out, "批准范围不对"),
    )
    run(
        "proposal extract 未发布也能提取",
        ["proposal", "extract", *common, "--task", write_task, "--chapter", "CH-0001"],
        check=lambda out: ("提取出" in out, "没产出提案"),
    )
    proposals = sorted((runtime / "proposals").glob("PROP-*.yaml"))
    if proposals:
        prop_id = proposals[-1].stem
        run("proposal approve", ["proposal", "approve", *common, prop_id, "--as", "author"])
        run(
            "deps closure 记忆说清楚了",
            ["deps", "closure", *common, "--task", write_task],
            check=lambda out: ("可以同批发布" in out, "共同发布检查没过"),
        )
        run(
            "release prepare 带上记忆同批",
            ["release", "prepare", *common, "--task", write_task, "--as", "author"],
            check=lambda out: ("已生成发布计划" in out, "计划没生成"),
        )
    else:
        RESULTS.append(("proposal approve", False, "没有生成提案文件"))

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
