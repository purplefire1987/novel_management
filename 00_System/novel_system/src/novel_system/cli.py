"""CLI 入口：novel <command>（PRD 14.1）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .config import Config, ConfigError
from .gitops import GitError
from .orchestrator import Orchestrator
from .transaction import TransactionError
from .validators.permissions import PermissionDenied
from .vault import VaultError
from .workflow.state_machine import WorkflowError

APPROVE_CHOICES = ("none", "auto", "all")


# --------------------------------------------------------------------- 输出辅助


def head(text: str) -> None:
    print(f"\n{text}")
    print("-" * max(len(text), 24))


def kv_block(pairs: list[tuple[str, Any]]) -> None:
    width = max((len(str(k)) for k, _ in pairs), default=0)
    for key, value in pairs:
        print(f"  {str(key).ljust(width)}  {value}")


def print_issues(issues: list[Any]) -> None:
    if not issues:
        print("  无问题")
        return
    for issue in issues:
        severity = getattr(issue, "severity", "?")
        detail = getattr(issue, "detail", str(issue))
        refs = getattr(issue, "refs", [])
        flag = " ⛔" if getattr(issue, "blocks_write", False) else ""
        print(f"  [{severity}]{flag} {detail}" + (f"  ({', '.join(refs)})" if refs else ""))


def table(headers: list[str], rows: list[list[Any]]) -> None:
    if not rows:
        print("  （空）")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    print("  " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in rows:
        print("  " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


def parse_kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set 需要 key=value 形式，收到：{pair}")
        key, _, raw = pair.partition("=")
        try:
            out[key.strip()] = yaml.safe_load(raw)
        except yaml.YAMLError:
            out[key.strip()] = raw
    return out


# --------------------------------------------------------------------- 命令


def cmd_doctor(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.doctor()
        head("系统体检")
        for check in result["checks"]:
            mark = "OK  " if check["ok"] else "FAIL"
            print(f"  [{mark}] {check['check']}：{check['detail']}")
        head("索引统计")
        kv_block(list(result["stats"].items()))
        return 0 if result["all_ok"] else 1


def cmd_rebuild(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.rebuild_index()
        head("重建索引")
        kv_block(
            [
                ("数据库", result["db"]),
                ("扫描文件", result["files"]),
                ("错误", len(result["errors"])),
                ("清理临时文件", result.get("cleaned_temp", 0)),
            ]
        )
        for key, value in result["indexed"].items():
            print(f"    {key}: {value}")
        for error in result["errors"]:
            print(f"  [WARN] {error}")
        return 0


def cmd_status(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        data = orch.status(args.chapter)
        head("索引统计")
        kv_block(list(data["stats"].items()))
        head("章节状态")
        table(
            ["章节", "标题", "状态", "字数", "草稿", "Pack"],
            [
                [
                    c["chapter_id"],
                    (c.get("title") or "")[:14],
                    c.get("status") or "-",
                    c.get("word_count") or 0,
                    "有" if c["has_draft"] else "-",
                    "有" if c["pack"] else "-",
                ]
                for c in data["chapters"]
            ],
        )
        if data.get("chapter"):
            head(f"{args.chapter} 详情")
            kv_block(sorted(data["chapter"].items()))
            if data["events"]:
                table(
                    ["事件", "名称", "序号"],
                    [[e["event_id"], e.get("name"), e.get("sequence")] for e in data["events"]],
                )
        return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.preflight(args.chapter)
        head(f"Preflight｜{args.chapter}")
        print(f"  结论：{result.verdict}")
        print(f"  运行模式：{result.modes}")
        print(f"  资料包：{result.pack_path}")
        print(f"  报告：{result.report_path}")
        if result.status_changed:
            print(f"  状态已推进（操作 {result.status_changed}）")
        head("问题清单")
        print_issues(result.issues)
        if result.author_decisions:
            head("待作者确认")
            for item in result.author_decisions:
                print(f"  - {item}")
        return 0


def cmd_write(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.write(args.chapter, force=args.force)
        head("草稿生成")
        kv_block(
            [
                ("草稿", result.draft_path),
                ("字数", result.word_count),
                ("模式", result.mode),
                ("骨架草稿", "是" if result.skeleton else "否"),
            ]
        )
        if result.note:
            print(f"\n  {result.note}")
        return 0


def cmd_review(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.review(args.chapter)
        head(f"章节审核｜{args.chapter}")
        print(f"  结论：{result.verdict}（{result.mode}）")
        print(f"  字数：{result.word_count}")
        print(f"  报告：{result.report_path}")
        print(f"  当前状态：{result.status_after}")
        head("问题清单")
        print_issues(result.issues)
        return 0


def cmd_publish(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.publish(args.chapter, confirmed=args.yes)
        head("正文发布")
        kv_block(
            [
                ("文件", result["path"]),
                ("字数", result.get("word_count")),
                ("操作", result["operation_id"]),
                ("Git", result.get("git")),
            ]
        )
        return 0


def _print_proposals(proposals: list[dict[str, Any]]) -> None:
    table(
        ["提案", "类型", "目标", "审批", "证据"],
        [
            [
                p.get("proposal_id"),
                p.get("kind"),
                p.get("target_id") or "-",
                p.get("approval_class"),
                ((p.get("evidence") or ["-"])[0] or "-")[:24],
            ]
            for p in proposals
        ],
    )


def cmd_commit_memory(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.commit_memory(args.chapter)
        head(f"记忆提案｜{args.chapter}")
        print(f"  模式：{result.mode}")
        print(f"  提案文件：{result.bundle_path}")
        print(f"  报告：{result.report_path}")
        _print_proposals(result.proposals)
        if result.issues:
            head("校验问题")
            print_issues(result.issues)

        if args.approve == "none":
            print("\n  下一步：novel apply-memory %s --approve auto   # 或 --approve all / --ids ..." % args.chapter)
            return 0

        applied = orch.apply_memory(
            args.chapter,
            approve=args.approve,
            ids=[i for i in (args.ids or "").split(",") if i],
        )
        head("记忆提交结果")
        kv_block(
            [
                ("operation_id", applied.operation_id),
                ("已应用", len(applied.applied)),
                ("被阻断", len(applied.dropped)),
                ("待审批", len(applied.pending)),
                ("Git", applied.git),
                ("回归审计", applied.audit_report or "-"),
            ]
        )
        if applied.pending:
            print("  待审批：" + ", ".join(applied.pending))
        if applied.operation_id:
            print(f"  回滚：novel rollback {applied.operation_id}")
        return 0


def cmd_apply_memory(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.apply_memory(
            args.chapter,
            approve=args.approve,
            ids=[i for i in (args.ids or "").split(",") if i],
        )
        head("记忆提交结果")
        kv_block(
            [
                ("operation_id", result.operation_id),
                ("已应用", len(result.applied)),
                ("被阻断", len(result.dropped)),
                ("待审批", len(result.pending)),
                ("Git", result.git),
                ("回归审计", result.audit_report or "-"),
            ]
        )
        if result.dropped:
            print("  被阻断：" + ", ".join(result.dropped))
        if result.pending:
            print("  待审批：" + ", ".join(result.pending))
        return 0


def cmd_audit(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.audit_chapter(args.chapter)
        head(f"章节审计｜{args.chapter}")
        print(f"  结论：{result['verdict']}")
        print(f"  报告：{result['report']}")
        head("问题清单")
        print_issues(result["issues"])
        return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.rollback(args.operation_id, force=args.force)
        head("回滚完成")
        kv_block(
            [
                ("原操作", result["operation_id"]),
                ("回滚操作", result["rollback_id"]),
                ("还原文件", len(result["restored"])),
                ("删除新建", len(result["removed"])),
                ("记录", result["log"]),
            ]
        )
        for path in result["restored"]:
            print(f"    restored  {path}")
        for path in result["removed"]:
            print(f"    removed   {path}")
        return 0


def cmd_search(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        results = orch.search(args.query, args.limit)
        head(f"检索：{args.query}")
        table(
            ["章节", "标题", "评分"],
            [[r["chapter_id"], r.get("title"), r.get("score")] for r in results],
        )
        return 0


def cmd_show(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        data = orch.show(args.doc_id)
        if not data:
            print(f"未找到实体：{args.doc_id}")
            return 1
        head(f"{args.doc_id}")
        kv_block([k for k in data["entity"].items() if k[0] != "summary"])
        if args.body:
            print("\n" + data["body"])
        return 0


def cmd_new(args: argparse.Namespace) -> int:
    extra = parse_kv(args.set or [])
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.new_entity(args.type, args.name, extra)
        head("新建实体")
        kv_block(result.items())
        return 0


def cmd_log(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        rows = orch.audit_log(args.limit)
        head("操作日志")
        table(
            ["时间", "操作", "目标", "状态", "operation_id"],
            [
                [r["timestamp"][:19], r["operation"], r["target"], r["status"], r["operation_id"]]
                for r in rows
            ],
        )
        return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.ingest(
            args.source,
            volume_id=args.volume,
            mode=args.split,
            title=args.title or "",
            dry_run=args.dry_run,
            tag=args.tag,
            force=args.force,
        )
        head(f"录入{'（预演）' if args.dry_run else ''}｜{Path(args.source).name}")
        kv_block(
            [
                ("批次", result.batch_id),
                ("编码", result.encoding),
                ("切分模式", result.mode),
                ("目标卷", result.volume_id),
                ("章节数", len(result.chapters)),
                ("总字数", result.total_words),
                ("跳过文件头", f"{result.preamble_chars} 字" if result.preamble_chars else "-"),
            ]
        )
        head("章节清单")
        table(
            ["章节", "标题", "字数", "源文件行"],
            [[c["chapter_id"], c["title"][:20], c["word_count"], c["source_line"]] for c in result.chapters],
        )
        if args.dry_run:
            print("\n  预演模式：未写入任何文件。去掉 --dry-run 即可正式录入。")
            return 0
        head("落盘结果")
        kv_block(
            [
                ("操作", result.operation_id),
                ("批次日志", result.log_path),
                ("Git", result.git.get("status") or "-"),
                ("快照标签", result.tag or "-"),
            ]
        )
        print(f"\n  回滚：novel rollback {result.operation_id}")
        if result.warnings:
            head("提示")
            for warning in result.warnings:
                print(f"  - {warning}")
        return 0


def cmd_annotate(args: argparse.Namespace) -> int:
    lines: list[str] = list(args.line or [])
    if args.from_file:
        source = Path(args.from_file).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"找不到标注文件：{source}")
        lines.extend(source.read_text(encoding="utf-8").splitlines())
    if not lines:
        raise ValueError("请用 --line 或 --from 提供标注内容（格式：`- 事件: 名称 | 键:值`）")

    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.annotate(args.chapter, lines, replace=args.replace)
        head(f"记忆标注｜{args.chapter}")
        kv_block(
            [
                ("文件", result.path),
                ("新增", result.added),
                ("标注总数", result.total),
                ("覆盖原标注", "是" if result.replaced else "否"),
                ("操作", result.operation_id),
                ("Git", result.git.get("status") or "-"),
            ]
        )
        print(f"\n  下一步：novel commit-memory {args.chapter} --approve auto")
        return 0


def _print_import(result: Any, *, title: str, dry_run: bool) -> None:
    head(f"{title}{'（预演）' if dry_run else ''}｜{result.profile}")
    kv_block(
        [
            ("批次", result.batch_id),
            ("作品", result.world or "-"),
            ("来源", "、".join(source["name"] for source in result.sources) or "-"),
            ("分类条目", result.total()),
            ("文件数", result.files),
        ]
    )
    if result.counts:
        head("分类结果")
        table(
            ["类别", "数量"],
            [[label, count] for label, count in result.counts.items()],
        )
    if dry_run and result.samples:
        head("样例（前 5 份）")
        for sample in result.samples:
            print(f"\n  · {sample['path']}")
            for line in sample["head"].splitlines()[:8]:
                print(f"    {line}")
    if not dry_run:
        head("落盘结果")
        kv_block(
            [
                ("操作", result.operation_id),
                ("批次日志", result.log_path),
                ("归档原件", "；".join(s.get("archived_as", "-") for s in result.sources)),
                ("Git", result.git.get("status") or "-"),
                ("快照标签", result.tag or "-"),
            ]
        )
        print(f"\n  回滚：novel rollback {result.operation_id}")
    if result.skipped:
        head("跳过项")
        for item in result.skipped:
            print(f"  - {item}")
    if result.warnings:
        head("提示")
        for item in result.warnings:
            print(f"  - {item}")
    if dry_run:
        print("\n  预演模式：未写入任何文件。去掉 --dry-run 即可正式录入。")


def cmd_import_assets(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.import_assets(
            args.profile,
            source=args.source,
            dry_run=args.dry_run,
            force=args.force,
            archive=not args.no_archive,
            tag=args.tag,
        )
        _print_import(result, title="资产录入", dry_run=args.dry_run)
        if not args.dry_run:
            print("\n  下一步：novel rebuild-index && novel status")
        return 0


def cmd_import_lore(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.import_lore(
            args.profile,
            root=args.root,
            dry_run=args.dry_run,
            force=args.force,
            archive=not args.no_archive,
            tag=args.tag,
        )
        _print_import(result, title="底层记忆录入", dry_run=args.dry_run)
        if not args.dry_run:
            print("\n  下一步：novel rebuild-index && novel show WORLD-0002")
        return 0


def cmd_history(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        state = orch.git_state()
        head("仓库状态")
        kv_block(
            [
                ("分支", state["branch"]),
                ("HEAD", state["head"]),
                ("未提交", state["dirty"]),
                ("上游", state["upstream"] or "（未配置）"),
                ("领先/落后", f"{state['ahead']} / {state['behind']}"),
            ]
        )
        head(f"提交历史（最近 {args.limit} 条）")
        table(
            ["提交", "时间", "作者", "说明"],
            [[r["hash"], r["time"], r["author"], r["subject"][:44]] for r in orch.git_history(args.limit)],
        )
        tags = orch.git_tags()
        if tags:
            head("快照标签")
            table(["标签", "日期", "说明"], [[t["tag"], t["date"], (t["subject"] or "")[:34]] for t in tags[:args.limit]])
        return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    with Orchestrator(Config.load(args.vault)) as orch:
        result = orch.snapshot(args.name, args.message or "")
        head("快照已创建")
        kv_block([("标签", result["tag"]), ("提交", result["head"])])
        print(f"\n  回到此快照：git -C {args.vault or '.'} checkout {result['tag']}")
        return 0


def cmd_export_schemas(args: argparse.Namespace) -> int:
    from .validators.schema import MODEL_BY_TYPE

    cfg = Config.load(args.vault)
    out_dir = cfg.vault / "00_System" / "Schemas"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, model in MODEL_BY_TYPE.items():
        schema = model.model_json_schema()
        (out_dir / f"{name}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    head("导出 JSON Schema")
    print(f"  {out_dir}（{len(MODEL_BY_TYPE)} 个）")
    return 0


# --------------------------------------------------------------------- 骰子娘


def _print_dice_result(result: dict[str, Any]) -> None:
    head(f"掷骰 · {result['label']}")
    mode_note = {"normal": "普通", "advantage": "优势", "disadvantage": "劣势"}.get(result["mode"], result["mode"])
    kv_block(
        [
            ("编号", result["roll_id"]),
            ("表达式", f"{result['expression']}（{mode_note}）"),
            ("见证", "、".join(result["witnesses"]) or "无（仅系统熵）"),
            ("承诺", result["commitment"]),
            ("骰值", " ".join(str(d) for d in result["draws"]) + f" → {result['raw']}"),
            ("合计", f"{result['total']}（点数 {result['raw']}，加值 {result['bonus']:+d}）"),
            ("DC", result["dc"] if result["dc"] is not None else "—"),
            ("判定", result["text"]),
        ]
    )
    if result["refs"]:
        print("\n  依据：")
        for ref in result["refs"]:
            print(f"    · {ref}")
    print(f"\n  种子已落账，任何人可复算：novel dice verify（server_seed={result['server_seed'][:16]}…）")


def cmd_dice_roll(args: argparse.Namespace) -> int:
    from . import dice

    cfg = Config.load(args.vault)
    result = dice.roll(
        cfg,
        args.expression,
        bonus=args.bonus,
        dc=args.dc,
        witnesses=args.witness or [],
        label=args.label or "",
        mode=args.mode,
        character=args.character,
        note=args.note or "",
    )
    _print_dice_result(result)
    return 0


def cmd_dice_check(args: argparse.Namespace) -> int:
    from . import dice

    cfg = Config.load(args.vault)
    result = dice.roll(
        cfg,
        "1d100",
        bonus=args.bonus,
        dc=args.dc,
        witnesses=args.witness or [],
        label=args.label or "",
        mode=args.mode,
        character=args.character,
        note=args.note or "",
    )
    _print_dice_result(result)
    return 0


def cmd_dice_duel(args: argparse.Namespace) -> int:
    from . import dice

    cfg = Config.load(args.vault)
    result = dice.duel(
        cfg,
        bonus_a=args.a,
        bonus_b=args.b,
        name_a=args.name_a,
        name_b=args.name_b,
        label=args.label or "",
        witnesses=args.witness or [],
        note=args.note or "",
    )
    head(f"对抗检定 · {args.label or '未命名对抗'}")
    kv_block(
        [
            ("甲方", f"{args.name_a} d100{args.a:+d} → {result['a']['draws']} + {args.a} = {result['a']['total']}（{result['a']['band']}）"),
            ("乙方", f"{args.name_b} d100{args.b:+d} → {result['b']['draws']} + {args.b} = {result['b']['total']}（{result['b']['band']}）"),
            ("胜者", result["winner"]),
            ("结论", result["verdict"]),
            ("碾压", "触发（差 > 20，可追加暴击/终结效果）" if result["crush"] else "未触发"),
        ]
    )
    print("\n  依据：")
    for ref in result["refs"]:
        print(f"    · {ref}")
    return 0


def cmd_dice_verify(args: argparse.Namespace) -> int:
    from . import dice

    cfg = Config.load(args.vault)
    report = dice.verify(cfg)
    head("骰子台账校验")
    kv_block([("台账", dice.LEDGER_FILE), ("条数", report["checked"]), ("结论", "通过" if report["ok"] else "存在异常")])
    for problem in report["problems"]:
        print(f"  [!] {problem}")
    return 0 if report["ok"] else 1


def cmd_dice_log(args: argparse.Namespace) -> int:
    from . import dice

    cfg = Config.load(args.vault)
    rows = dice.recent(cfg, args.limit)
    head("最近掷骰")
    if not rows:
        print("  台账为空（还没有人要求掷骰）")
        return 0
    table(
        ["编号", "时间", "检定", "骰值", "合计", "判定"],
        [
            [
                row.get("roll_id", ""),
                str(row.get("at", ""))[5:19],
                row.get("label", ""),
                " ".join(str(d) for d in row.get("draws") or []),
                row.get("total", ""),
                row.get("band", ""),
            ]
            for row in rows
        ],
    )
    return 0


# --------------------------------------------------------------------- 解析器


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novel",
        description="超长篇小说个人知识库与受控 AI 写作工作流（MVP）",
    )
    parser.add_argument("--vault", help="NovelVault 路径（默认自动查找或读 NOVEL_VAULT）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="环境与配置体检").set_defaults(func=cmd_doctor)
    sub.add_parser("rebuild-index", help="从 Markdown 重建 SQLite 索引").set_defaults(func=cmd_rebuild)

    p_status = sub.add_parser("status", help="查看章节与索引状态")
    p_status.add_argument("chapter", nargs="?", help="章节 ID，如 CH-0002")
    p_status.set_defaults(func=cmd_status)

    p_pre = sub.add_parser("preflight", help="写前资料包与冲突检查")
    p_pre.add_argument("chapter")
    p_pre.set_defaults(func=cmd_preflight)

    p_write = sub.add_parser("write", help="主笔生成章节草稿（写入 staging）")
    p_write.add_argument("chapter")
    p_write.add_argument("--force", action="store_true", help="覆盖已有草稿（保留 .bak）")
    p_write.set_defaults(func=cmd_write)

    p_review = sub.add_parser("review", help="总编审核草稿")
    p_review.add_argument("chapter")
    p_review.set_defaults(func=cmd_review)

    p_pub = sub.add_parser("publish", help="作者确认并锁定正式正文")
    p_pub.add_argument("chapter")
    p_pub.add_argument("--yes", action="store_true", help="作者明确确认")
    p_pub.set_defaults(func=cmd_publish)

    p_commit = sub.add_parser("commit-memory", help="从正式正文提取记忆提案（可选立即应用）")
    p_commit.add_argument("chapter")
    p_commit.add_argument("--approve", choices=APPROVE_CHOICES, default="none")
    p_commit.add_argument("--ids", help="指定批准的提案 ID，逗号分隔")
    p_commit.set_defaults(func=cmd_commit_memory)

    p_apply = sub.add_parser("apply-memory", help="审批并应用已生成的记忆提案")
    p_apply.add_argument("chapter")
    p_apply.add_argument("--approve", choices=APPROVE_CHOICES, default="auto")
    p_apply.add_argument("--ids", help="指定批准的提案 ID，逗号分隔")
    p_apply.set_defaults(func=cmd_apply_memory)

    p_audit = sub.add_parser("audit", help="写后连续性回归审计")
    p_audit.add_argument("chapter")
    p_audit.set_defaults(func=cmd_audit)

    p_rb = sub.add_parser("rollback", help="按 operation_id 回滚正式写入")
    p_rb.add_argument("operation_id")
    p_rb.add_argument("--force", action="store_true")
    p_rb.set_defaults(func=cmd_rollback)

    p_search = sub.add_parser("search", help="全文检索章节")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.set_defaults(func=cmd_search)

    p_show = sub.add_parser("show", help="查看实体详情")
    p_show.add_argument("doc_id")
    p_show.add_argument("--body", action="store_true", help="同时打印正文")
    p_show.set_defaults(func=cmd_show)

    p_new = sub.add_parser("new", help="新建实体（ID 由系统生成）")
    p_new.add_argument("type", help="character/location/faction/item/enemy/world/mission/"
                                   "chapter_outline/foreshadowing/relationship/event/scene_beat/character_state")
    p_new.add_argument("name")
    p_new.add_argument("--set", action="append", help="附加字段，如 --set volume_id=VOL-0001")
    p_new.set_defaults(func=cmd_new)

    p_ingest = sub.add_parser("ingest", help="录入原始 txt/md 稿件（切章 + 建大纲 + 建正文 + 批次提交）")
    p_ingest.add_argument("source", help="原始文件路径")
    p_ingest.add_argument("--volume", default="VOL-0001", help="目标卷 ID，默认 VOL-0001")
    p_ingest.add_argument(
        "--split",
        choices=("auto", "chapter", "heading", "number", "single"),
        default="auto",
        help="切章方式：auto(默认) / chapter(第X章) / heading(Markdown 标题) / number(1、) / single(整篇一章)",
    )
    p_ingest.add_argument("--title", help="整篇一章时的标题（single 模式）")
    p_ingest.add_argument("--dry-run", action="store_true", help="只预演切章结果，不写文件")
    p_ingest.add_argument("--tag", action="store_true", help="录入成功后打 ingest/<批次> 快照标签")
    p_ingest.add_argument("--force", action="store_true", help="忽略「内容已录入过」的重复检测")
    p_ingest.set_defaults(func=cmd_ingest)

    p_ann = sub.add_parser("annotate", help="给已发布章节补写「作者备注」结构化标注")
    p_ann.add_argument("chapter")
    p_ann.add_argument("--line", action="append", help="一条标注，可重复，如 --line \"- 摘要: ...\"")
    p_ann.add_argument("--from", dest="from_file", help="从文本文件读取标注行（每行一条）")
    p_ann.add_argument("--replace", action="store_true", help="覆盖原有标注而不是追加")
    p_ann.set_defaults(func=cmd_annotate)

    p_import = sub.add_parser(
        "import-assets",
        help="分类录入 Excel 资产库（人物/势力/物品/地点/生物 + 总表）",
    )
    p_import.add_argument("profile", help="00_System/Config/import_profiles.yaml 中的 profile 名，如 jiuzhou-assets")
    p_import.add_argument("--source", help="资产文件路径；默认取 profile 里配置的 source")
    p_import.add_argument("--dry-run", action="store_true", help="只预演分类与 ID，不写文件")
    p_import.add_argument("--no-archive", action="store_true", help="不把原件复制到 99_Archive/Raw_Sources")
    p_import.add_argument("--tag", action="store_true", help="录入成功后打 import/<批次> 快照标签")
    p_import.add_argument("--force", action="store_true", help="忽略「同一文件已录入过」的重复检测")
    p_import.set_defaults(func=cmd_import_assets)

    p_lore = sub.add_parser(
        "import-lore",
        help="录入 Markdown 设定稿为底层记忆（按标题区间切分）",
    )
    p_lore.add_argument("profile", help="profile 名，如 jiuzhou-lore")
    p_lore.add_argument("--root", help="设定稿所在目录；默认取 profile 里的 root")
    p_lore.add_argument("--dry-run", action="store_true", help="只预演切分与 ID，不写文件")
    p_lore.add_argument("--no-archive", action="store_true", help="不把原件复制到 99_Archive/Raw_Sources")
    p_lore.add_argument("--tag", action="store_true", help="录入成功后打 import/<批次> 快照标签")
    p_lore.add_argument("--force", action="store_true", help="忽略「同一文件已录入过」的重复检测")
    p_lore.set_defaults(func=cmd_import_lore)

    p_hist = sub.add_parser("history", help="查看仓库状态、提交历史与快照标签")
    p_hist.add_argument("--limit", type=int, default=15)
    p_hist.set_defaults(func=cmd_history)

    p_snap = sub.add_parser("snapshot", help="给当前提交打快照标签")
    p_snap.add_argument("name", help="标签名，如 vol01-complete")
    p_snap.add_argument("--message", help="标签说明")
    p_snap.set_defaults(func=cmd_snapshot)

    p_log = sub.add_parser("log", help="查看审计日志")
    p_log.add_argument("--limit", type=int, default=15)
    p_log.set_defaults(func=cmd_log)

    sub.add_parser("export-schemas", help="把 Pydantic 模型导出为 JSON Schema").set_defaults(
        func=cmd_export_schemas
    )

    # ---------------------------------------------------------------- 骰子娘
    p_dice = sub.add_parser("dice", help="骰子娘：玩家可验证的掷骰与规则裁决")
    dsub = p_dice.add_subparsers(dest="dice_command", required=True)

    def _dice_common(p: argparse.ArgumentParser, *, with_bonus: bool = True) -> None:
        if with_bonus:
            p.add_argument("--bonus", type=int, default=0, help="加值（ADJ+LB+SKB+神通×2+情境）")
        p.add_argument("--dc", type=int, help="难度（5/30/55/80/100/105）")
        p.add_argument("--label", default="", help="这次检定在做什么")
        p.add_argument("--witness", action="append", default=[], help="玩家见证值，如 甲=37（可多次）")
        p.add_argument("--char", dest="character", help="相关人物 ID，仅用于追溯")
        p.add_argument("--note", default="", help="备注")
        p.add_argument(
            "--mode",
            choices=("normal", "advantage", "disadvantage"),
            default="normal",
            help="普通 / 优势（取高）/ 劣势（取低）",
        )

    p_droll = dsub.add_parser("roll", help="掷任意骰子并裁决（如 1d100+11、3d6）")
    p_droll.add_argument("expression", nargs="?", default="1d100", help="骰子表达式")
    _dice_common(p_droll)
    p_droll.set_defaults(func=cmd_dice_roll)

    p_dcheck = dsub.add_parser("check", help="d100 检定（等价于 roll 1d100）")
    _dice_common(p_dcheck)
    p_dcheck.set_defaults(func=cmd_dice_check)

    p_dduel = dsub.add_parser("duel", help="对抗检定：双方各掷 d100+加值，高者胜")
    p_dduel.add_argument("--a", type=int, required=True, help="甲方加值")
    p_dduel.add_argument("--b", type=int, required=True, help="乙方加值")
    p_dduel.add_argument("--name-a", default="甲方")
    p_dduel.add_argument("--name-b", default="乙方")
    p_dduel.add_argument("--label", default="")
    p_dduel.add_argument("--witness", action="append", default=[])
    p_dduel.add_argument("--note", default="")
    p_dduel.set_defaults(func=cmd_dice_duel)

    dsub.add_parser("verify", help="校验台账哈希链并复算全部骰值").set_defaults(func=cmd_dice_verify)

    p_dlog = dsub.add_parser("log", help="查看最近掷骰")
    p_dlog.add_argument("--limit", type=int, default=10)
    p_dlog.set_defaults(func=cmd_dice_log)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except WorkflowError as exc:
        print(f"\n[工作流拒绝] {exc}", file=sys.stderr)
        return 3
    except PermissionDenied as exc:
        print(f"\n[权限拒绝] {exc}", file=sys.stderr)
        return 4
    except TransactionError as exc:
        print(f"\n[事务失败] {exc}", file=sys.stderr)
        return 5
    except GitError as exc:
        print(f"\n[版本控制错误] {exc}", file=sys.stderr)
        return 6
    except (ConfigError, VaultError, FileNotFoundError, ValueError) as exc:
        print(f"\n[错误] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
