"""原始字节保全。

PRD §12.4 第一条：原始文件保存 **100% 字节一致**。

做法就一句话：写下去，再读回来比 sha256。不比对「看起来一样」——
`write_new` 走的是临时文件 + `os.replace`，在某些受限环境里会退化成直接写，
退化的路径上「写成功」并不等于「写对了」。所以这里必须回读。

放在 Vault 的归档区（`99_Archive/Imports/<IMP>/raw/`）而不是 runtime：
原始字节是**不可重建的事实**，runtime 只放可重建物（ADR-001 的边界表）。
归档区不在正式区，所以写它不需要发布审批，也不会进索引。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel_management.domain.errors import ConflictError
from novel_management.infrastructure.filesystem.storage import sha256_file
from novel_management.application.importing.model import ImportFile


@dataclass
class Preserved:
    path: str
    sha256: str
    ok: bool
    reason: str = ""


def preserve_inputs(
    raw_dir: Path, rows: list[tuple[ImportFile, bytes]]
) -> list[Preserved]:
    """把接收到的原始字节逐份写进 `raw_dir`，回读校验。

    被拒的文件不写（没有字节可写，也不该在归档里留一份半截文件）。
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    out: list[Preserved] = []
    for record, data in rows:
        if not record.accepted:
            continue
        target = raw_dir / record.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = sha256_file(target)
            if existing != record.sha256:
                out.append(
                    Preserved(
                        path=record.path,
                        sha256=record.sha256,
                        ok=False,
                        reason=(
                            f"归档里已有一份同名但内容不同的原始文件（{existing[:12]}…）；"
                            "同一 import 目录不允许放两份不同的原文"
                        ),
                    )
                )
                continue
            out.append(Preserved(path=record.path, sha256=existing, ok=True))
            continue
        target.write_bytes(data)
        actual = sha256_file(target)
        if actual != record.sha256:
            raise ConflictError(
                f"原始字节保全失败：{record.path} 写入后哈希 {actual[:12]}… ≠ 读入时 {record.sha256[:12]}…。"
                "这条不成立，整个导入的可信度就不成立，因此中止而不是继续记账"
            )
        out.append(Preserved(path=record.path, sha256=actual, ok=True))
    return out
