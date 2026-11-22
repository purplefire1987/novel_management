"""T09／T10 的命令：plan / write / review / proposal / deps。

放在一个模块里，是因为它们共用同一套上下文（项目、runtime、任务、批准存储），
拆成五个文件只会让「同一件事的五个片段」分散在五处。

## 关于 `--mock-response`

真实模型适配器还没接（`adapters/` 只有 Mock）。所以 `write` 与
`proposal extract` 在没有 `--mock-response` 时**直接报错**，而不是偷偷用
Mock 生成一段占位正文写进 staging。

理由：一份看起来像稿子的占位内容进了 staging，会一路走到审核与批准，
最后变成「作者批了一段模板」。演练可以，但必须由人显式给文件。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..adapters.mock import MockLLM
from ..agents.metering import Meter
from ..audit import AUTHOR, ensure_actor
from ..errors import UsageError
from ..validation import closure as closure_mod
from ..validation import dependencies as dep_mod
from ..validation import proposals as prop_mod
from ..workflows import draft as draft_mod
from ..workflows import plan as plan_mod
from ..workflows import review as review_mod
from ..workflows import text_approval as text_mod
from ._common import (
    approval_store,
    heading,
    info,
    need_runtime,
    ok,
    oplog,
    project,
    task_store,
    warn,
)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def cmd_plan(cfg, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    action = args.plan_command

    if action == "show":
        check = plan_mod.check(pp, args.chapter)
        heading(f"{check.chapter_id} 规划：{check.rel}")
        info(f"可开写：{'是' if check.ready else '否'}")
        for hit in check.found:
            info(f"[有] {hit.key:<26} {hit.where}")
        for spec in check.missing:
            flag = "必需" if spec.key in plan_mod.REQUIRED_FOR_DRAFT else "建议"
            info(f"[缺/{flag}] {spec.key:<26} {spec.label}")
        if check.scope:
            heading("creative_scope")
            for key in ("allowed", "approval_required", "forbidden"):
                items = check.scope.get(key) or []
                info(f"{key}: {len(items)} 条")
                for item in items:
                    info(f"  - {item}")
        if not check.ready:
            print()
            warn("缺必需字段：preflight 会判 BLOCKED，write 会拒绝开写")
        return 0

    if action == "check":
        check = plan_mod.check(pp, args.chapter)
        if args.json:
            print(json.dumps(check.as_dict(), ensure_ascii=False, indent=2))
            return 0 if check.ready else 1
        if check.ready:
            ok(f"{check.chapter_id} 规划够格开写（{len(check.found)} 项有出处）")
            return 0
        warn(f"{check.chapter_id} 规划还缺 {len(check.missing_required)} 项必需字段")
        for spec in check.missing_required:
            suffix = "" if spec.key in spec.label else f"（{spec.key}）"
            info(f"  - {spec.label}{suffix}")
        return 1

    if action == "derive":
        result = plan_mod.derive(pp, args.chapter)
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            heading(f"{result.chapter_id} 可派生的结构化约束")
            info(f"必须发生：{len(result.must_happen)} 条")
            for item in result.must_happen:
                info(f"  - {item.text}    [{item.locator}]")
            info(f"禁止发生：{len(result.must_not_happen)} 条")
            for item in result.must_not_happen:
                info(f"  - {item.text}    [{item.locator}]")
            info(f"creative_scope.forbidden：{len(result.scope_forbidden)} 条（只收明确禁令语气的条目）")
            for note in result.skipped:
                warn(note)
            heading("只会派生确定性可读的两处")
            info("allowed / approval_required 不派生——散文里没有对应来源，编一个就等于替作者扩权")

        if args.write:
            actor = ensure_actor(getattr(args, "actor", None), "plan derive")
            if actor != AUTHOR:
                raise UsageError("写回章卡是作者的动作（--as author）")
            written = plan_mod.apply(pp, need_runtime(cfg), args.chapter, result)
            heading("写回")
            if written.get("written"):
                ok(f"已写入 {written['path']}")
                info(f"新增键：{'、'.join(written['added'])}")
                info(f"备份：{written['backup']}")
                info(f"正文哈希未变：{written['body_sha256'][:16]}…")
            else:
                warn(str(written.get("reason")))
        elif not args.json:
            heading("写回 frontmatter 的 YAML 片段（--write 才会真的写）")
            print(plan_mod.render_yaml(result))
        return 0

    raise UsageError(f"未知 plan 子命令：{action}")


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------
def cmd_write(cfg, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "write")
    pp = project(cfg, args)
    runtime = need_runtime(cfg)
    llm = _mock_llm(args, "write")
    meter = Meter(runtime)

    result = draft_mod.write(
        pp,
        runtime,
        task_id=args.task,
        chapter_id=args.chapter,
        llm=llm,
        base_release=args.base_release or "",
        meter=meter,
        actor=actor,
        note=args.note or "",
        model=args.model or "",
        replace=args.replace,
        preflight_id=args.preflight or "",
    )
    summary = meter.summary()
    meter.flush()

    heading("草稿已进 staging")
    ok(f"{result.chapter_id} → {result.target}")
    info(f"staging：{result.staged}")
    info(f"sha256：{result.sha256[:16]}…   {result.words} 字")
    info(f"资料包：{result.preflight_id}（{result.preflight_gate}）")
    info(f"模型调用：{summary['model_calls']} 次，提示 {summary['prompt_tokens']} / 生成 {summary['completion_tokens']}")
    for item in result.new_facts:
        info(f"  [新事实候选] {item.get('summary') or item}")
    for item in result.deviations:
        warn(f"偏离规划：{item}")
    for item in result.open_questions:
        warn(f"未决问题：{item}")
    for item in result.warnings:
        warn(item)
    print()
    print("正文没有、也不该进 01_Canon——它现在只在 staging 里。")
    print(f"下一步：novel review --task {result.task_id} --chapter {result.chapter_id} --as editor --verdict approve")
    return 0


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------
def cmd_review(cfg, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    runtime = need_runtime(cfg)

    if args.review_command == "dimensions":
        from ..workflows import dimensions as dim_mod

        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "dimensions": [dim.as_dict() for dim in dim_mod.REVIEW_DIMENSIONS],
                        "verdicts": list(dim_mod.VERDICT_IDS),
                        "severities": dict(dim_mod.SEVERITIES),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        heading("十维审核清单（V1.0 §11）")
        for index, dim in enumerate(dim_mod.REVIEW_DIMENSIONS, start=1):
            mark = "机器可判形状" if dim.machine_checkable else "机器判不了"
            print(f"  {index:2d}. {dim.id:<18s} {dim.name:<8s} {dim.primary_owner:<18s} {mark}")
            info(dim.question)
            info(f"  机器能做的：{dim.machine_scope}")
        info("取值：PASS / ISSUE / NOT_APPLICABLE / NOT_CHECKED")
        info("NOT_APPLICABLE（本章不涉及）与 NOT_CHECKED（没查）是两件事，报告里分开写。")
        return 0

    if args.review_command == "status":
        state = review_mod.status(runtime, task_id=args.task, chapter_id=args.chapter)
        if args.json:
            print(json.dumps(state.as_dict(), ensure_ascii=False, indent=2))
        else:
            heading(f"{args.chapter} 审核状态")
            info(f"结论：{state.verdict}   风险：{state.risk_level or '未定'}")
            info(f"返修轮次：{state.rounds_used}/{state.max_rounds}")
            info(f"已通过角色：{'、'.join(state.approved_by) or '（无）'}")
            done, total = state.dimension_coverage
            info(f"十维覆盖：{done}/{total}")
            if state.unchecked_dimensions:
                warn("还没查的维度：" + "、".join(state.unchecked_dimensions))
            if state.missing_reviewers:
                warn("还缺角色：" + "、".join(state.missing_reviewers))
            for item in state.blockers:
                warn(f"[BLOCKER/{item.category}] {item.message}")
        return 0 if state.passed else 1

    reviewer = ensure_actor(getattr(args, "actor", None), "review")
    role = args.role
    from ..agents.registry import ROLE_IDS

    if role not in ROLE_IDS:
        raise UsageError(f"角色必须是九角色之一：{'/'.join(ROLE_IDS)}")

    check = plan_mod.check(pp, args.chapter)
    must_not = _strings(check.meta.get("must_not_happen"))

    result = review_mod.submit(
        pp,
        runtime,
        task_id=args.task,
        chapter_id=args.chapter,
        reviewer=reviewer,
        reviewer_role=role,
        verdict=args.verdict,
        risk=args.risk,
        note=args.note or "",
        must_not=must_not,
        max_rounds=args.max_rounds,
        dimensions=_parse_dimensions(getattr(args, "dimension", None) or []),
        allow_unchecked=bool(getattr(args, "allow_unchecked", False)),
    )

    review = result.review
    heading(f"审核记录 {review.review_id}")
    info(f"{review.chapter_id} 第 {review.round} 轮   {review.reviewer_role} / {review.reviewer}")
    info(f"结论：{review.verdict}   风险：{review.risk_level}")
    for item in review.risk_basis:
        info(f"  风险依据：{item.get('label')}（关键词 {item.get('keyword')!r}）")
    heading("发现")
    for item in review.findings:
        line = f"[{item.severity}/{item.category}] {item.message}"
        (warn if item.severity == "BLOCKER" else info)(line)
    for item in result.warnings:
        warn(item)

    from ..workflows import dimensions as dim_mod

    print()
    print("十维清单（V1.0 §11）")
    print(dim_mod.render_markdown(review.dimensions))
    done, total = dim_mod.coverage(review.dimensions)
    info(f"覆盖 {done}/{total}")
    if done < total:
        warn("没查的项不会自动算通过——报告里写成「未审」，也不会凑成 APPROVE。")

    print()
    print(f"下一步：{result.next_action}")
    return 0 if review.verdict == "APPROVE" else 1


# ---------------------------------------------------------------------------
# proposal
# ---------------------------------------------------------------------------
def cmd_proposal(cfg, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    runtime = need_runtime(cfg)
    action = args.proposal_command

    if action == "extract":
        # 提取可以不给模型：确定性骨架仍然产出一份合法提案（只有摘要与实体清单）。
        # 这一点与 `write` 不同——写稿没有模型就没有正文，提取没有模型只是条目更薄。
        llm = _optional_mock_llm(args)
        meter = Meter(runtime)
        result = prop_mod.extract(
            pp,
            runtime,
            task_id=args.task,
            chapter_id=args.chapter,
            approvals=approval_store(cfg),
            llm=llm,
            meter=meter,
            base_release=args.base_release or "",
            note=args.note or "",
            model=args.model or "",
        )
        meter.flush()
        proposal = result.proposal
        heading(f"记忆提案 {proposal.proposal_id}")
        ok(f"{proposal.chapter_id} 提取出 {len(proposal.items)} 条候选")
        info(f"源：{proposal.source_target}  {proposal.source_sha256[:16]}…")
        info(f"绑定批准：{proposal.approval_id}")
        info(f"提取方式：{'模型' if proposal.model_used else '确定性骨架'}")
        for item in proposal.items[:12]:
            info(f"  [{item.kind}] {item.summary}")
        if len(proposal.items) > 12:
            info(f"  …… 另有 {len(proposal.items) - 12} 条")
        for item in result.warnings:
            warn(item)
        print()
        print(f"下一步：novel proposal approve {proposal.proposal_id} --as author")
        return 0

    if action == "list":
        items = prop_mod.ProposalStore(runtime).list(task_id=args.task or "", chapter_id=args.chapter or "")
        if not items:
            info("没有记忆提案")
            return 0
        heading("记忆提案")
        for proposal in items:
            mode = "不产记忆" if proposal.no_memory else f"{len(proposal.items)} 条"
            info(f"{proposal.proposal_id}  {proposal.chapter_id:<10} {proposal.status:<10} {mode}")
        return 0

    if action == "show":
        proposal = prop_mod.ProposalStore(runtime).load(args.proposal_id)
        if args.json:
            print(json.dumps(proposal.as_dict(), ensure_ascii=False, indent=2))
            return 0
        heading(f"{proposal.proposal_id}  {proposal.chapter_id}")
        info(f"状态：{proposal.status}   任务：{proposal.task_id}")
        info(f"源：{proposal.source_target}  {proposal.source_sha256[:16]}…")
        if proposal.no_memory:
            info(f"声明不产记忆：{proposal.no_memory_reason}")
        for item in proposal.items:
            info(f"  [{item.kind}] {item.summary}")
            if item.quote:
                info(f"        引用：{item.quote[:60]}")
        return 0

    if action == "verify":
        store = prop_mod.ProposalStore(runtime)
        proposal = store.load(args.proposal_id)
        verdict = prop_mod.verify(proposal, pp, runtime)
        if verdict.ok:
            ok(f"{proposal.proposal_id}：{verdict.status}（{verdict.reason}）")
            return 0
        warn(f"{proposal.proposal_id}：{verdict.status}——{verdict.reason}")
        return 1

    if action == "approve":
        actor = ensure_actor(getattr(args, "actor", None), "proposal approve")
        store = prop_mod.ProposalStore(runtime)
        proposal = store.load(args.proposal_id)
        verdict = prop_mod.verify(proposal, pp, runtime)
        if not verdict.ok:
            raise UsageError(f"{proposal.proposal_id} 已失效，不能批准：{verdict.reason}")
        prop_mod.approve(proposal, store, approval_store(cfg), actor=actor, note=args.note or "")
        ok(f"{proposal.proposal_id} 已批准（{len(proposal.items)} 条）")
        info("记忆提案与正文必须同批发布：release prepare 会把两者一起算进计划")
        return 0

    if action == "reject":
        actor = ensure_actor(getattr(args, "actor", None), "proposal reject")
        store = prop_mod.ProposalStore(runtime)
        proposal = store.load(args.proposal_id)
        prop_mod.reject(proposal, store, actor=actor, reason=args.reason or "")
        ok(f"{proposal.proposal_id} 已驳回")
        return 0

    if action == "declare-none":
        actor = ensure_actor(getattr(args, "actor", None), "proposal declare-none")
        if not args.chapter:
            raise UsageError("declare-none 需要 --chapter")
        path = prop_mod.declare_no_memory(
            runtime,
            project_id=pp.project_id,
            task_id=args.task or "",
            chapter_id=args.chapter,
            reason=args.reason or "",
            actor=actor,
        )
        ok(f"已声明 {args.chapter} 本章不产记忆")
        info(f"记录：{path.name}（发布闸门据此放行）")
        return 0

    raise UsageError(f"未知 proposal 子命令：{action}")


# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------
def cmd_deps(cfg, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    action = args.deps_command

    if action == "check":
        states = dep_mod.check(pp)
        if args.json:
            print(json.dumps([state.as_dict() for state in states], ensure_ascii=False, indent=2))
        else:
            heading("依赖状态")
            if not states:
                info("项目里没有任何档案声明 depends_on——没有可检查的依赖")
                return 0
            bad = 0
            for state in states:
                tag = state.status
                line = f"{state.artifact_id:<18} {tag:<13} {state.path}"
                if tag == dep_mod.VALID:
                    info(line)
                else:
                    bad += 1
                    warn(line)
                    for reason in state.reasons[:3]:
                        info(f"    {reason}")
            summary = f"{len(states)} 份产物声明了依赖，{bad} 份需要处理"
            (ok if bad == 0 else warn)(summary)
        return 0 if not [s for s in states if s.status == dep_mod.STALE] else 1

    if action == "show":
        state = dep_mod.dependencies_of(pp, args.artifact)
        if state is None:
            info(f"{args.artifact} 没有声明 depends_on")
            return 0
        heading(f"{state.artifact_id}  {state.status}")
        info(f"路径：{state.path}")
        for ref in state.depends_on:
            info(f"  → {ref['artifact_id']}  {ref['dependency_type']}  {str(ref['content_hash'])[:12]}…")
        for reason in state.reasons:
            warn(reason)
        return 0

    if action == "closure":
        result = closure_mod.check(
            pp,
            need_runtime(cfg),
            chapter_ids=list(args.chapter or []),
            task_id=args.task or "",
        )
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            heading("共同发布检查")
            info("正文：" + "、".join(result.chapter_ids))
            for item in result.checked:
                if item.get("mode") == "NO_MEMORY":
                    info(f"  {item['chapter_id']}：声明不产记忆（{item['reason']}）")
                else:
                    info(f"  {item['chapter_id']}：提案 {item['proposal_id']}，{item['items']} 条")
            for item in result.issues:
                (warn if item.blocking else info)(f"[{item.code}] {item.message}")
            (ok if result.ok else warn)("可以同批发布" if result.ok else f"{len(result.blocking)} 项阻断")
        return 0 if result.ok else 1

    raise UsageError(f"未知 deps 子命令：{action}")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _mock_llm(args: argparse.Namespace, command: str) -> MockLLM:
    """演练用：从文件读一段模型输出。没给就明确报错。"""
    path = getattr(args, "mock_response", "") or ""
    if not path:
        raise UsageError(
            f"{command} 需要模型输出，但本版还没有接入真实模型适配器。\n"
            "  演练：--mock-response <文件>（文件里放一段符合信封契约的 JSON）\n"
            "  真模型适配器属后续任务；不提供「不传也照样跑」的默认路径——"
            "那会把一段占位正文写进 staging，一路走到批准"
        )
    file = Path(path).expanduser()
    if not file.exists():
        raise UsageError(f"--mock-response 文件不存在：{file}")
    return MockLLM(script=[file.read_text(encoding="utf-8")])


def _optional_mock_llm(args: argparse.Namespace) -> MockLLM | None:
    path = getattr(args, "mock_response", "") or ""
    if not path:
        return None
    file = Path(path).expanduser()
    if not file.exists():
        raise UsageError(f"--mock-response 文件不存在：{file}")
    return MockLLM(script=[file.read_text(encoding="utf-8")])


def _strings(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _parse_dimensions(raw: list[str]) -> dict[str, dict]:
    """把 `--dimension 维度=结论[:说明]` 解析成十维表。

    只写 `structure=pass` 也行——说明留空。写 `structure=issue:开场没立住目标`
    就把说明一起记下来。**不给结论的维度不会被默认成通过**：
    没写就是没查，处理在 `dimensions.normalise()` 里。
    """
    from ..workflows import dimensions as dim_mod

    table: dict[str, dict] = {}
    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        key, _, rest = text.partition("=")
        rest = rest.strip()
        verdict, _, note = rest.partition(":")
        dim = dim_mod.get(key.strip())
        table[dim.id] = {"verdict": verdict.strip().upper() or "NOT_CHECKED", "note": note.strip()}
    return table


__all__ = ["cmd_deps", "cmd_plan", "cmd_proposal", "cmd_review", "cmd_write"]
