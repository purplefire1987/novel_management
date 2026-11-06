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

- **场景目标**：{goal}
- **POV**：{pov}
- **地点**：{place}
- **时间**：{time}
- **承接上文**：{entry}
- **参与角色**：{cast}
- **场景开始状态**：{start}
- **冲突**：{conflict}
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
        row = {**sc, "created_at": sc.get("created_at", "2026-11-04"), "updated_at": today}
        (OUT / f"{sc['id']}.md").write_text(TPL.format(**row), encoding="utf-8")
    print(f"✓ 场景细纲 {len(scenes)} 个 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
