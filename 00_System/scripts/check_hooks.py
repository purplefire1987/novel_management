#!/usr/bin/env python3
"""章末钩子与有效困境的机检。

作者 2026-11-09 裁定两条：
  ① 每章章末必须有悬念，五类之一：未完成的动作／新的威胁／刚被推翻的认知／倒计时／一句听不懂的话。
  ② 为角色设置的困境必须有效——能迫使角色作出反应（谁／做了什么／代价）。

## 射程

本脚本只验三件事，**别的都不验**：

  1. 章级有没有 `| 钩子类型 |`，取值是否落在五类里；
  2. 场级有没有 `| 逼迫反应 |`，且是否写了「代价」；
  3. 正文模式（`--ms-dir`）下，写作备注有没有 `章末钩子：<类型>｜<原文>`。

它验不出「钩子好不好」「反应是不是真的被迫」——那要人读。
**0 违规只说明没有漏填，不说明写得对。** 这是两种结论，汇报时不许合并。

## 判据要对实测，不对叙述

`--outline` 指哪份就读哪份，不猜；`--ms-dir` 里逐章读 frontmatter 的 `status`，
`published` 的章跳过（状态机没有 `published → revision` 这条边），除非加 `--strict`。
`--strict` 的用途只有一个：重写开工前给旧稿列缺陷，**不用于卡发布**。

## 退出码

  0  无违规（WARN 不改变退出码）
  1  有违规
  2  环境不成立（文件读不到、路径不对、字段全缺）——这时候的 0 不算数

用法：
  python3 00_System/scripts/check_hooks.py
  python3 00_System/scripts/check_hooks.py --outline 04_Outline/Volumes/VOL-0001_S1细纲.md
  python3 00_System/scripts/check_hooks.py --ms-dir 08_Manuscripts/Volume_01
  python3 00_System/scripts/check_hooks.py --ms-dir 08_Manuscripts/Volume_01 --strict
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HOOK_TYPES = ("未完成的动作", "新的威胁", "刚被推翻的认知", "倒计时", "一句听不懂的话")

DEFAULT_OUTLINE = "04_Outline/Volumes/VOL-0001_S1细纲.md"

CH_HEAD = re.compile(r"^### (CH-\d{4})｜(.+)$", re.M)
SCENE_HEAD = re.compile(r"^#### 场 (\d+)｜(.+?)\s*`(SCENE-[^`]+)`", re.M)

# 表行：| 键 | 值 |   （值里可能带 | ？不会——表内不许出现竖线）
ROW = r"^\|[ \t]*{key}[ \t]*\|[ \t]*(.*?)[ \t]*\|[ \t]*$"

SEP = re.compile(r"[＋+／/、,，]")
TYPE_SPLIT = re.compile(r"\s*[｜|]\s*", 0)


def row_value(block: str, key: str) -> str | None:
    m = re.search(ROW.format(key=re.escape(key)), block, re.M)
    return m.group(1).strip() if m else None


def split_types(raw: str) -> list[str]:
    parts = [p.strip() for p in SEP.split(raw) if p.strip()]
    return parts


def check_outline(path: str) -> tuple[list[str], list[str], dict]:
    text = open(path, encoding="utf-8").read()

    # 先切章，再在章内切场
    ch_marks = [(m.start(), m.group(1), m.group(2).strip()) for m in CH_HEAD.finditer(text)]
    if not ch_marks:
        raise SystemExit(f"[环境不成立] {path} 里一个 `### CH-xxxx` 都没找到")

    blocks: list[tuple[str, str, str]] = []
    for i, (pos, cid, title) in enumerate(ch_marks):
        end = ch_marks[i + 1][0] if i + 1 < len(ch_marks) else len(text)
        blocks.append((cid, title, text[pos:end]))

    viol: list[str] = []
    warn: list[str] = []
    stat = {"chapters": 0, "scenes": 0, "hook_ok": 0, "forced_ok": 0,
            "hook_missing": 0, "forced_missing": 0, "observe_scenes": 0}

    seen_types: list[tuple[str, str]] = []  # (chapter_id, primary_type)

    for cid, title, cbody in blocks:
        stat["chapters"] += 1

        hook_text = row_value(cbody, "结尾钩子")
        hook_type = row_value(cbody, "钩子类型")

        # —— ① 结尾钩子本身 ——
        if hook_text is None:
            viol.append(f"[BLOCK] {cid} 缺 `| 结尾钩子 |` 行")
        elif not hook_text.strip():
            viol.append(f"[BLOCK] {cid} `结尾钩子` 为空")

        # —— ② 钩子类型 ——
        if hook_type is None:
            stat["hook_missing"] += 1
            viol.append(f"[BLOCK] {cid} 缺 `| 钩子类型 |` 行——不标类型按未写钩子处理")
        else:
            toks = split_types(hook_type)
            bad = [t for t in toks if t not in HOOK_TYPES]
            if not toks:
                viol.append(f"[BLOCK] {cid} `钩子类型` 为空")
            elif bad:
                viol.append(
                    f"[BLOCK] {cid} 钩子类型不在五类内：{'、'.join(bad)}"
                    f"（只能是 {'／'.join(HOOK_TYPES)}）"
                )
            else:
                stat["hook_ok"] += 1
                seen_types.append((cid, toks[0]))

        # —— ③ 场级：逼迫反应 ——
        scenes = list(SCENE_HEAD.finditer(cbody))
        if not scenes:
            warn.append(f"[WARN] {cid} 章内没有场景块（`#### 场 N｜…` ），无法核逼迫反应")
            continue

        for j, sm in enumerate(scenes):
            end = scenes[j + 1].start() if j + 1 < len(scenes) else len(cbody)
            sbody = cbody[sm.start():end]
            sid = sm.group(3)
            stat["scenes"] += 1

            forced = row_value(sbody, "逼迫反应")
            dilemma = row_value(sbody, "两难") or ""
            no_dilemma = "无真两难" in dilemma

            if forced is None:
                stat["forced_missing"] += 1
                viol.append(f"[BLOCK] {sid} 缺 `| 逼迫反应 |` 行")
                continue

            if not forced.strip():
                stat["forced_missing"] += 1
                viol.append(f"[BLOCK] {sid} `逼迫反应` 为空")
                continue

            if "★不通过" in forced or "★未通过" in forced:
                stat["observe_scenes"] += 1
                viol.append(f"[BLOCK] {sid} `逼迫反应` 自标不通过——观察场不许单独占一场")
                continue

            if "★" in forced:
                warn.append(f"[WARN] {sid} `逼迫反应` 自标弱项：{forced[:52]}")

            if "代价" not in forced:
                warn.append(f"[WARN] {sid} `逼迫反应` 没写代价：{forced[:40]}")
            else:
                stat["forced_ok"] += 1

            # 写了「无真两难」的场，逼迫反应更不许空着——这是它唯一的戏
            if no_dilemma and len(forced) < 8:
                viol.append(f"[BLOCK] {sid} 标了无真两难，逼迫反应过短，等于没写：{forced}")

    # —— ④ 同类钩子连用 ——（作者：同类最多连两章）
    run_type, run_len = None, 0
    for cid, t in seen_types:
        if t == run_type:
            run_len += 1
            if run_len >= 3:
                warn.append(f"[WARN] {cid} 与前面两章同为「{t}」，三类连用读者会疲劳")
        else:
            run_type, run_len = t, 1

    return viol, warn, stat


def check_ms(ms_dir: str, strict: bool) -> tuple[list[str], list[str], dict]:
    viol: list[str] = []
    warn: list[str] = []
    stat = {"files": 0, "checked": 0, "skipped": 0, "hook_ok": 0, "no_notes": 0}

    names = sorted(f for f in os.listdir(ms_dir) if re.fullmatch(r"CH-\d{4}\.md", f))
    if not names:
        raise SystemExit(f"[环境不成立] {ms_dir} 里没有 CH-xxxx.md")

    for name in names:
        stat["files"] += 1
        p = os.path.join(ms_dir, name)
        text = open(p, encoding="utf-8").read()
        cid = name[:-3]

        head = text.split("---", 2)
        status = ""
        if len(head) >= 3:
            m = re.search(r"^status:\s*(\S+)", head[1], re.M)
            status = (m.group(1).strip().strip("'\"") if m else "")

        if status == "published" and not strict:
            stat["skipped"] += 1
            continue
        stat["checked"] += 1

        notes_at = text.find("## 写作备注")
        if notes_at < 0:
            stat["no_notes"] += 1
            viol.append(f"[BLOCK] {cid} 没有 `## 写作备注`，无法登记章末钩子")
            continue

        notes = text[notes_at:]
        m = re.search(r"章末钩子[：:]\s*([^\n｜|]+)[｜|]\s*(.+)$", notes, re.M)
        if not m:
            viol.append(f"[BLOCK] {cid} 写作备注缺 `章末钩子：<类型>｜<正文最后一句原文>`")
            continue

        t = m.group(1).strip()
        quote = m.group(2).strip()
        if t not in HOOK_TYPES:
            viol.append(f"[BLOCK] {cid} 钩子类型「{t}」不在五类内")
            continue

        # 最后一句必须真的在正文里，且是正文的最后一段
        body_at = text.find("## 正文")
        if body_at < 0:
            viol.append(f"[BLOCK] {cid} 没有 `## 正文`")
            continue
        body = text[body_at:notes_at].replace("## 正文", "", 1).strip()
        if body and quote not in body[-400:]:
            viol.append(f"[BLOCK] {cid} 钩子引文不在正文末尾：{quote[:30]}")
            continue
        stat["hook_ok"] += 1

    return viol, warn, stat


def main() -> int:
    ap = argparse.ArgumentParser(
        description="章末钩子(五类)与有效困境(逼迫反应)机检。只验漏填，不验好坏。",
    )
    ap.add_argument("--outline", default=DEFAULT_OUTLINE,
                    help=f"细纲路径，默认 {DEFAULT_OUTLINE}")
    ap.add_argument("--ms-dir", default=None,
                    help="正文目录；给了就改查写作备注里的章末钩子，不再查细纲")
    ap.add_argument("--strict", action="store_true",
                    help="连 published 的正文也查（只用于重写前给旧稿列缺陷）")
    ap.add_argument("--quiet", action="store_true", help="只打汇总行与违规行")
    a = ap.parse_args()

    try:
        if a.ms_dir:
            viol, warn, stat = check_ms(a.ms_dir, a.strict)
            summary = (f"正文 {stat['files']} 份｜实查 {stat['checked']}｜"
                       f"跳过(published) {stat['skipped']}｜"
                       f"钩子合格 {stat['hook_ok']}｜违规 {len(viol)} 条｜WARN {len(warn)} 条")
        else:
            if not os.path.isfile(a.outline):
                print(f"[环境不成立] 找不到细纲：{a.outline}", file=sys.stderr)
                return 2
            viol, warn, stat = check_outline(a.outline)
            summary = (f"章 {stat['chapters']}｜场 {stat['scenes']}｜"
                       f"钩子类型 {stat['hook_ok']}/{stat['chapters']}｜"
                       f"逼迫反应 {stat['forced_ok']}/{stat['scenes']}｜"
                       f"违规 {len(viol)} 条｜WARN {len(warn)} 条")
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2
    except OSError as e:
        print(f"[环境不成立] {e}", file=sys.stderr)
        return 2

    print(summary)
    for line in viol:
        print("  " + line)
    if not a.quiet:
        for line in warn:
            print("  " + line)
        print("  —— 0 违规只说明没有漏填，不说明写得对；钩子好不好、反应是不是真的被迫，要人读。")

    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
