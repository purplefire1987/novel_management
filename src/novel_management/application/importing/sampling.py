"""固定种子抽样，以及「来源定位是否可反查」的自动核对。

PRD §12.4 要抽样、要 95% 准确率。这里必须说清一件事：

**本系统能自动测的是「定位准确率」，不是「语义提取准确率」。**
前者问「这条记录指向的原文，真的是这段话吗」，可以机器判；后者问「这段话里是不是
真的有个叫某某的人物」，需要人看。把两者混成一个数字，就会拿抽样合格去冒充全库完整
——PRD 末句专门禁止这件事（「抽样准确率达标不等于全库完整」）。

所以 `accuracy` 记的是定位准确率，`note` 里写明定义，`known_limits` 里再写一次。
"""

from __future__ import annotations

import random
from pathlib import Path

from novel_management.application.importing.model import read_locator

DEFAULT_SEED = "novel-import-v1"
DEFAULT_SAMPLE_SIZE = 100


def sample_indices(population: int, *, size: int, seed: str) -> list[int]:
    """从 [0, population) 里抽 size 个下标。**确定性**：同种子同规模出同结果。

    用 `random.Random(seed)` 而不是全局 `random`：全局状态会被别的调用改掉，
    同一个种子的两次运行就会给出不同的样本，「可复现」就成了空话。
    """
    if population <= 0 or size <= 0:
        return []
    count = min(size, population)
    rng = random.Random(seed)
    return sorted(rng.sample(range(population), count))


def normalise(text: str) -> str:
    """比较用的归一化：只管空白。不改字、不转全半角——那会掩盖真实差异。"""
    return " ".join(text.split())


def verify_sample(
    raw_dir: Path,
    items: list[tuple[str, str]],
    *,
    size: int = DEFAULT_SAMPLE_SIZE,
    seed: str = DEFAULT_SEED,
) -> dict:
    """抽样反查：`items` 是 (locator, 记录下来的原文片段)。

    返回 schema 里 `sample_result` 那一块。**一条都抽不到时 accuracy 记 0 并说明**，
    不写 1.0——没有样本的通过率不是通过率。
    """
    picked = sample_indices(len(items), size=size, seed=seed)
    checked = 0
    matched = 0
    failures: list[str] = []
    for index in picked:
        locator, expected = items[index]
        checked += 1
        try:
            actual = read_locator(raw_dir, locator)
        except Exception as exc:  # noqa: BLE001 - 定位符坏掉有很多种，统统算不通过
            failures.append(f"{locator}：{exc}")
            continue
        if normalise(expected) in normalise(actual):
            matched += 1
        else:
            failures.append(f"{locator}：原文片段与记录不符")

    if checked == 0:
        return {
            "sample_size": 0,
            "seed": seed,
            "accuracy": 0.0,
            "note": "没有可抽样的来源引用（没有段落或没有提取物），准确率不适用",
        }

    accuracy = matched / checked
    note = (
        f"抽样 {checked}/{len(items)} 条，逐条按定位符回读原始字节比对。"
        "这是**来源定位准确率**，不是语义提取准确率——后者需要人工判定，系统不代劳"
    )
    if failures:
        note += f"；不通过 {len(failures)} 条：" + "；".join(failures[:5])
    return {
        "sample_size": checked,
        "seed": seed,
        "accuracy": round(accuracy, 4),
        "note": note,
    }
