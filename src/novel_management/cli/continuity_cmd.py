"""连续性检查：continuity check（V1.0 §11 时间／信息／伏笔三项、WF-17）。

**这个命令能判什么、不能判什么**（写在这里而不是藏起来）：

能判的，是「账面上有没有缺口」——
- 事件没写世界时间：它在时间线上必然 `UNORDERED`；
- 伏笔的回收窗口已经过去，状态还不是已回收；
- 状态变化记录指向的实体不存在；
- 档案缺 `source_refs`，出了问题追不回去。

不能判的，是语义：「这一章让角色提前知道了秘密」「这段行程耗时不合物理」。
那种活要读正文，机器读了也只是在猜。

所以输出分两段：**已经确定的缺口** 与 **这次没查的项**。第二段不是免责声明，
是必需品——`novel continuity check` 打一屏「通过」然后让人以为全书一致，
比它报十个假警报更糟。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config as cfg_mod
from ..infrastructure.filesystem import frontmatter as fm
from ..domain.errors import UsageError
from ..infrastructure.filesystem.paths import ProjectPaths
from ._common import fail, heading, info, ok, project, read_meta, warn

# 时间精度取值。`unknown` 是合法的，但它意味着这条事件排不进时间线。
PRECISIONS = ("exact", "day", "month", "year", "era", "unknown")


def cmd_continuity(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    if args.continuity_command != "check":
        fail(f"未知子命令 {args.continuity_command}")
        return 2

    pp = project(cfg, args)
    scope = args.scope

    problems: list[tuple[str, str, str]] = []  # (级别, 对象, 说明)
    checked: list[str] = []
    skipped: list[tuple[str, str]] = []  # (项目, 为什么没查)

    # ---- 1. 事件时间 ----
    events = _docs(pp, pp.canon_dir("事件与历史") if pp.canon.is_dir() else None)
    checked.append(f"事件时间（{len(events)} 份）")
    for path, meta in events:
        when = str(meta.get("world_time") or "").strip()
        precision = str(meta.get("time_precision") or "").strip()
        if not when:
            problems.append(("WARN", pp.rel(path), "没有世界时间：这条事件在时间线上只能是 UNORDERED"))
        elif precision and precision not in PRECISIONS:
            problems.append(("WARN", pp.rel(path), f"时间精度 {precision!r} 不在 {('/'.join(PRECISIONS))} 里"))
        elif not precision:
            problems.append(("WARN", pp.rel(path), "有世界时间但没写精度：读者与查询都会把它当精确时刻"))

    # ---- 2. 伏笔回收 ----
    fs_dir = pp.planning / "伏笔与悬念" if pp.planning.is_dir() else None
    foreshadows = _docs(pp, fs_dir)
    checked.append(f"伏笔回收（{len(foreshadows)} 份）")
    for path, meta in foreshadows:
        status = str(meta.get("status") or "").strip().lower()
        window = str(meta.get("target_reveal_window") or meta.get("reveal_window") or "").strip()
        if status in ("resolved", "closed", "已回收", "已失效"):
            continue
        if not window:
            problems.append(("WARN", pp.rel(path), "未回收且没写回收窗口：它会被忘掉"))
        else:
            problems.append(
                ("INFO", pp.rel(path), f"未回收，窗口 {window}（窗口是否已过要靠正文判断，机器不知道进度）")
            )

    # ---- 3. 关系两端是否存在 ----
    rels = _docs(pp, pp.canon_dir("关系与状态") if pp.canon.is_dir() else None)
    checked.append(f"关系两端（{len(rels)} 份）")
    known = _all_ids(pp)
    for path, meta in rels:
        for key in ("from_entity_id", "to_entity_id"):
            raw = str(meta.get(key) or "").strip()
            if raw and raw not in known:
                problems.append(("BLOCKER", pp.rel(path), f"{key}={raw} 指向不存在的实体"))

    # ---- 4. 来源可回溯 ----
    canon_docs = _docs(pp, pp.canon if pp.canon.is_dir() else None)
    checked.append(f"来源可回溯（{len(canon_docs)} 份）")
    no_ref = [path for path, meta in canon_docs if not (meta.get("source_refs") or [])]
    for path in no_ref:
        problems.append(("INFO", pp.rel(path), "没有 source_refs：出问题时追不回依据"))

    # ---- 系统性未查项 ----
    skipped.append(
        (
            "时间线全序（事件之间的 BEFORE/AFTER 边）",
            "event_edges 表当前是空的；没有边就没有可判的顺序，任意两事件都是 UNORDERED",
        )
    )
    skipped.append(
        (
            "角色认知边界（是否提前知道秘密）",
            "要读正文语义。`novel knowledge` 只能查已登记的认知记录，查不到不等于没越界",
        )
    )
    skipped.append(
        (
            "旅行耗时／年龄／冷却时间是否合理",
            "需要世界时间与历法换算，当前库里世界时间本身就没记全（见上面的事件时间项）",
        )
    )
    if scope != "all":
        skipped.append((f"范围外（{scope}）", "本次只查了知识库与伏笔，正文语义检查未做"))

    # ---- 输出 ----
    if args.json:
        print(
            json.dumps(
                {
                    "scope": scope,
                    "checked": checked,
                    "problems": [
                        {"level": level, "target": target, "message": message}
                        for level, target, message in problems
                    ],
                    "not_checked": [{"item": item, "why": why} for item, why in skipped],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if any(level == "BLOCKER" for level, _, _ in problems) else 0

    heading(f"连续性检查 · {pp.project_id} · 范围 {scope}")

    print("查过的：")
    for item in checked:
        info(item)

    blockers = [p for p in problems if p[0] == "BLOCKER"]
    warns = [p for p in problems if p[0] == "WARN"]
    infos = [p for p in problems if p[0] == "INFO"]

    print()
    if blockers:
        heading(f"阻断（{len(blockers)}）")
        for _, target, message in blockers:
            fail(f"{target}：{message}")
    if warns:
        heading(f"缺口（{len(warns)}）")
        for _, target, message in warns[:40]:
            warn(f"{target}：{message}")
        if len(warns) > 40:
            info(f"另有 {len(warns) - 40} 条同类，用 --json 取全量")
    if infos:
        heading(f"提示（{len(infos)}）")
        for _, target, message in infos[:20]:
            info(f"{target}：{message}")
        if len(infos) > 20:
            info(f"另有 {len(infos) - 20} 条")

    print()
    heading(f"这次**没有**查的（{len(skipped)}）")
    for item, why in skipped:
        info(f"{item}：{why}")

    print()
    if blockers:
        fail("有阻断项：先修，再谈一致性。")
        return 1
    if warns:
        warn("账面上有缺口。缺口的数量是可信的——但「没有缺口」不等于「内容一致」，上面第二段说清了为什么。")
        return 0
    ok("账面上没有缺口。注意这只覆盖上面「查过的」那几项。")
    return 0


def _docs(pp: ProjectPaths, root: Path | None) -> list[tuple[Path, dict]]:
    if root is None or not root.is_dir():
        return []
    out: list[tuple[Path, dict]] = []
    for path in sorted(root.rglob("*.md")):
        # 用完整 YAML 解析：要判 `participants` / `source_refs` 这种嵌套块，
        # 轻量解析读出来是空字符串，会把「字段在、但读不到」变成满屏假缺口。
        meta = read_meta(path)
        if meta:
            out.append((path, meta))
    return out


def _all_ids(pp: ProjectPaths) -> set[str]:
    ids: set[str] = set()
    for root in (pp.canon, pp.assets, pp.planning):
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            value = str(fm.read(path).get("id") or "").strip()
            if value:
                ids.add(value)
    return ids
