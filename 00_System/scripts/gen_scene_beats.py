#!/usr/bin/env python3
"""从章节大纲的「必须发生的剧情事实」时间轴生成场景细纲（SCENE-*）。

细纲只做拆分，不新增情节：每条「关键动作 / 信息揭示」都能在上层大纲的时间轴里找到出处。
情绪线、镜头切分属编排建议，文末统一标注「以章节大纲为准」。

幂等：同 ID 已存在则覆盖（细纲是可重生成的派生物，不是手工维护的事实源）。

接缝三字段（entry / exit / carry）不是装饰：场景断裂几乎都发生在换场那一行，
写细纲时看得见「上一场把读者留在哪」，落笔时才不会让湿衣服自己干了、
让死了的人再喘一口气。缺字段直接报错，不渲染成空行——空的接缝比没有更危险。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

VAULT = Path(__file__).resolve().parents[2]
OUT = VAULT / "04_Outline/Scenes"

DATA = OUT / "scenes.yaml"

# 每一个场景都必须写全。少一个就不是「简洁」，是接缝缺了一边。
REQUIRED = (
    "id", "chapter", "seq", "name", "goal", "pov", "place", "time",
    "cast", "start", "entry", "conflict", "emotion", "action",
    "reveal", "end", "exit", "carry", "next", "rules", "forbid",
)

# 结构字段（麦基口径），**可选**。别把它们加进 REQUIRED：
# REQUIRED 是硬校验，缺一条 load_scenes() 就 SystemExit，
# 而 2026-11 之前录入的旧数据没有这些键——加进去等于当场把生成链掐断。
# 所以规则是「有就写、没有就跳过」，缺失一律按 legacy 放行。
# 判据全文见 00_System/Prompts/story_editor.md「场景拆解标准」。
STRUCT_KEYS = (
    ("value", "主控价值"),
    ("polarity_in", "开场极性"),
    ("polarity_out", "收场极性"),
    ("shift", "价值转变"),
    ("turn", "转折点落点"),
)
DRIVE_KEYS = (
    ("expect", "期望"),
    ("outcome", "结果"),
    ("gap", "缺口（gap）"),
    ("dilemma_a", "危机两难 A"),
    ("dilemma_b", "危机两难 B"),
    # 作者 2026-11-09 裁定：困境必须有效。写成 `谁 → 被迫做了什么 → 代价：…`。
    # 可选：旧数据没有这个键，加了也不会让渲染结果变一个字
    # （_optional 的过滤条件是「有值才输出」，缺键取到空串就被丢掉）。
    ("forced", "逼迫反应"),
)

# `dilemma_b` 永远要有值。本场没有真两难时写 `—（本场无真两难：<一句理由>）`。
# 为什么不留空：留空的行与「压根没查」的行在档案里长得一模一样——
# 「没有」和「没查」分不开，正是这套结构字段要治的病，不能自己先犯一遍。
# 判定标记一律落在 B 行，A 行只写选项或"为什么这条不成立"，两行合起来才是一次判定。
#
# ---- 返工期的「待重判」标记（同一个目的：让「没查」在档案里无处藏身）----
#
# 卡片记的是**已发布那一稿**的判定。修订进行中时，结论字段（`shift`）保持不动——
# 「不确定」是过程，`full/weak/none` 是结论，拿结论字段去表示过程，等于把两种失败并成一种。
# 返工状态一律落 B 行，接在原有理由**之后**，写成**第二个括号**，不覆盖原来的括号。
# 第二个括号前面**不加 `—`**——那条破折号属于第一个括号（「本场无真两难」那个），
# 加两遍会读成两条并列的判定。机器只认括号本身：
#
#     （待重判：<触发条件>；<清标记的条件>）
#
# 字面量必须逐字如此，起手是全角左括号。不许换写成「待定」「暂挂」「待作者确认」——
# 一旦换了写法，`grep -F '（待重判：'` 就数不全，三态（从未判 / 已判 / 待重判）又糊回一片。
# 括号里两头都要写死：什么触发、什么时候清。只写「待重判」三个字，等于给自己留一个永远清不掉的洞。
#
# 现有实例（SCENE-CH-0001-003，作者 2026-11-08 裁定「场3 暂不动正文」后落的标记）：
#     （待重判：作者授权给 CH-0001 场3 补一句代价台词、按加字配等量删字重走；新正文落盘并完成重判后删本标记）
# 触发条件写的是「作者授权……」而不是「作者已授权」：暂缓期里没人能保证那一天一定来，
# 所以清标记的条件也必须写死——新正文落盘并完成重判，才删。两头都不许省。
#
# 数待办时**必须限定在卡片目录**，别全库 grep：
#     grep -F '（待重判：' 04_Outline/Scenes/*.md
# 全库搜会把本文件的注释示例和 12_Dashboard/待裁定事项.md 的正文一起数进去，
# 那就不再是「在返工的卡有几张」，而是「提到过这四个字的地方有几处」。
#
# 删标记与改 `shift` 必须是**同一次编辑**。不许出现「新正文已落盘、`shift` 已改成新结论、
# 标记还挂着」的中间态——那跟「判了没记」是同一件事。返工那一步的验收标准只有一条：
# 标记消失 + `shift` 反映新结论，两者同时成立。


def _optional(scene: dict, keys: tuple) -> str:
    """把可选结构字段渲染成 `- **键**：值` 行；一条都没有就返回空串。

    返回的字符串自带前导换行，直接拼在上一行末尾——这样旧数据渲染出来
    与改造前逐字节相同（连空行都不多一个），legacy 路径不会漂移。
    """
    lines = [
        f"- **{label}**：{str(scene[key]).strip()}"
        for key, label in keys
        if str(scene.get(key, "")).strip()
    ]
    return ("\n" + "\n".join(lines)) if lines else ""


def load_scenes() -> list[dict]:
    """场景内容是**数据**，放在 `04_Outline/Scenes/scenes.yaml`；本脚本只负责渲染。

    内容写死在代码里的问题不是「不好看」，而是改一句台词要去改 .py：
    文本 diff 淹没逻辑 diff，非程序员改不了，测试也没法造样例。
    """
    if not DATA.exists():
        raise SystemExit(f"✗ 缺少场景数据：{DATA.relative_to(VAULT)}")
    scenes = yaml.safe_load(DATA.read_text(encoding="utf-8")) or []
    if not isinstance(scenes, list):
        raise SystemExit("✗ scenes.yaml 顶层必须是列表（每个元素一个场景）")
    missing = [
        f"{sc.get('id', '?')}:{k}"
        for sc in scenes
        for k in REQUIRED
        if not str(sc.get(k, "")).strip()
    ]
    if missing:
        raise SystemExit("✗ 场景缺字段：" + "、".join(missing))
    return scenes


TPL = """---
id: {id}
type: scene_beat
name: 场景 {seq}：{name}
chapter_id: {chapter}
sequence: {seq}
status: confirmed
canon_status: confirmed
created_at: '{created_at}'
updated_at: '{updated_at}'
source_refs:
- 04_Outline/Chapters/{chapter}.md
- 99_Archive/Raw_Sources/九州旧稿/卷一：工坊疑云.md
import_batch: SCENE-20261104
tags:
- 大纲
- 场景细纲
- 卷一
---

# 场景 {seq}：{name}

- **场景目标**：{goal}{struct}
- **POV**：{pov}
- **地点**：{place}
- **时间**：{time}
- **承接上文**：{entry}
- **参与角色**：{cast}
- **场景开始状态**：{start}
- **冲突**：{conflict}{drive}
- **情绪变化**：{emotion}
- **关键动作**：{action}
- **信息揭示**：{reveal}
- **场景结束状态**：{end}
- **收束画面**：{exit}
- **跨场状态**：{carry}
- **与下一场景的连接**：{next}
- **必须遵守的事实**：{rules}
- **禁止擅自扩展**：{forbid}

## 编排说明

> 本细纲由 [[{chapter}]] 的「必须发生的剧情事实」时间轴拆分而来，**不含新增情节**；
> 情绪线与镜头切分为编排建议，如与作者设想不符，以章节大纲为准。
>
> `sequence` 是**叙事顺序**，不是 ID 尾号：两者一致时才算一条通到底的场景链。
> 「与下一场景的连接」只能指向 `sequence + 1` 那一场——指错就是跳号，下一场起笔接不住。
"""


def main() -> int:
    if not OUT.exists():
        OUT.mkdir(parents=True)
    scenes = load_scenes()
    today = date.today().isoformat()
    for sc in scenes:
        # created_at 认数据里的首次建卡日期，缺省按导入批次；updated_at 永远是重生成那天。
        row = {
            **sc,
            "struct": _optional(sc, STRUCT_KEYS),
            "drive": _optional(sc, DRIVE_KEYS),
            "created_at": sc.get("created_at", "2026-11-04"),
            "updated_at": today,
        }
        (OUT / f"{sc['id']}.md").write_text(TPL.format(**row), encoding="utf-8")
    print(f"✓ 场景细纲 {len(scenes)} 个 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
