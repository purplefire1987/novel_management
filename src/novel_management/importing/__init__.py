"""原稿导入：把外部稿件搬进来，逐维度记账，绝不用一个 `success` 概括。

为什么单独一个包而不是塞进 `staging/`：

- 导入面对的是**不受控输入**（别人的 .md/.txt，编码、标题层级、章节标记全不可信），
  与「Agent 产出」这条路径的失败模式完全不同；
- PRD §12.3 要求 ImportManifest 分开记录十一个维度，这份账必须能独立复核；
- 「不得自动把原稿叙述视为客观事实」（§12.2）——提取物一律是候选，不是断言。

```
scan（只读） → report（各维度） → stage（分批入 staging） → 作者批准 → release
```
"""

from __future__ import annotations

from .model import (
    Chapter,
    ImportFile,
    ImportManifest,
    Paragraph,
    make_locator,
    parse_locator,
    read_locator,
)
from .parser import parse_document, split_chapters
from .preserve import preserve_inputs
from .report import (
    ChapterRef,
    ImportStore,
    ScanOutcome,
    StagedBatch,
    build_manifest,
    draft_name,
    plan_batches,
    run_scan,
    stage_batch,
)
from .sampling import sample_indices, verify_sample
from .scanner import scan_inputs

__all__ = [
    "Chapter",
    "ChapterRef",
    "ImportFile",
    "ImportManifest",
    "ImportStore",
    "Paragraph",
    "ScanOutcome",
    "StagedBatch",
    "build_manifest",
    "draft_name",
    "make_locator",
    "parse_document",
    "parse_locator",
    "plan_batches",
    "preserve_inputs",
    "read_locator",
    "run_scan",
    "sample_indices",
    "scan_inputs",
    "split_chapters",
    "stage_batch",
    "verify_sample",
]
