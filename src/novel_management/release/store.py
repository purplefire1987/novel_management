"""发布记录的落盘位置与读写。

目录约定（全部在 Runtime 内，Vault 只放作品内容）：

```
<runtime>/releases/
  active.json                 # 正式指针：唯一的权威
  REL-000001/
    plan.json                 # 作者批准的对象（规范 JSON）
    plan.meta.json            # 计划与任务/项目的关联；不参与计划哈希
    journal.json              # 事务日志（TXN 号与 REL 号同号）
    manifest.json             # 构建后的不可变事实
    files/<项目内相对路径>     # 待发布的精确字节
    snapshot/                 # 隔离工作区（含候选 Git 提交）
    snapshot.revision         # 候选提交 sha，或 local:<digest>
<runtime>/receipts/RCPT-000001.json
<runtime>/locks/publish.lock
```

**不可变靠写入方式保证，不靠自觉**：manifest 用 `write_new` 落盘，
已存在且内容不同就报冲突；内容相同则视为同一次重试（幂等），
这不是覆盖，是「同一份东西又写了一次」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.errors import ConflictError, UsageError
from ..infrastructure.filesystem.storage import sha256_file, write_atomic, write_new
from .model import (
    ActivePointer,
    Journal,
    Receipt,
    ReleaseManifest,
    ReleasePlan,
)

RELEASES_DIR = "releases"
RECEIPTS_DIR = "receipts"
LOCKS_DIR = "locks"
ACTIVE_NAME = "active.json"
PLAN_NAME = "plan.json"
PLAN_META_NAME = "plan.meta.json"
JOURNAL_NAME = "journal.json"
MANIFEST_NAME = "manifest.json"
FILES_DIR = "files"
SNAPSHOT_DIR = "snapshot"
REVISION_NAME = "snapshot.revision"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConflictError(f"记录损坏，拒绝猜测：{path}（{exc}）") from exc


@dataclass
class PlanMeta:
    """计划与上下文的关联。**不参与计划哈希**——计划哈希只覆盖 schema 里那些字段。"""

    release_id: str
    task_id: str
    project_id: str
    project_root: str
    prepared_by: str
    prepared_at: str
    artifact_approval: str = ""
    reuse_index_of: str = ""
    bytes_from: str = ""
    reason: str = ""
    confirm_canon: bool = False
    # 目标相对路径 → staging 产物在 Vault 内的相对路径。
    # 执行阶段不能依赖「任务记录还在」：任务 yaml 属于运行态，可能被清理；
    # 而计划一旦批准，字节从哪来就必须是计划自己带的信息。
    staged_paths: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "prepared_by": self.prepared_by,
            "prepared_at": self.prepared_at,
            "artifact_approval": self.artifact_approval,
            "reuse_index_of": self.reuse_index_of,
            "bytes_from": self.bytes_from,
            "reason": self.reason,
            "confirm_canon": self.confirm_canon,
            "staged_paths": dict(self.staged_paths or {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanMeta":
        return cls(
            release_id=str(data.get("release_id") or ""),
            task_id=str(data.get("task_id") or ""),
            project_id=str(data.get("project_id") or ""),
            project_root=str(data.get("project_root") or ""),
            prepared_by=str(data.get("prepared_by") or ""),
            prepared_at=str(data.get("prepared_at") or ""),
            artifact_approval=str(data.get("artifact_approval") or ""),
            reuse_index_of=str(data.get("reuse_index_of") or ""),
            bytes_from=str(data.get("bytes_from") or ""),
            reason=str(data.get("reason") or ""),
            confirm_canon=bool(data.get("confirm_canon")),
            staged_paths={str(k): str(v) for k, v in (data.get("staged_paths") or {}).items()},
        )


class ReleaseStore:
    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)
        self.root = self.runtime / RELEASES_DIR
        self.receipts = self.runtime / RECEIPTS_DIR
        self.locks = self.runtime / LOCKS_DIR

    # ---- 位置 ----
    def release_dir(self, release_id: str) -> Path:
        return self.root / release_id

    def plan_path(self, release_id: str) -> Path:
        return self.release_dir(release_id) / PLAN_NAME

    def meta_path(self, release_id: str) -> Path:
        return self.release_dir(release_id) / PLAN_META_NAME

    def journal_path(self, release_id: str) -> Path:
        return self.release_dir(release_id) / JOURNAL_NAME

    def manifest_path(self, release_id: str) -> Path:
        return self.release_dir(release_id) / MANIFEST_NAME

    def files_dir(self, release_id: str) -> Path:
        return self.release_dir(release_id) / FILES_DIR

    def snapshot_dir(self, release_id: str) -> Path:
        return self.release_dir(release_id) / SNAPSHOT_DIR

    def revision_path(self, release_id: str) -> Path:
        return self.release_dir(release_id) / REVISION_NAME

    def receipt_path(self, receipt_id: str) -> Path:
        return self.receipts / f"{receipt_id}.json"

    def active_path(self) -> Path:
        return self.root / ACTIVE_NAME

    def lock_path(self) -> Path:
        return self.locks / "publish.lock"

    # ---- 编号 ----
    def list_releases(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name.startswith("REL-"))

    def next_release_id(self) -> str:
        highest = 0
        for name in self.list_releases():
            try:
                highest = max(highest, int(name.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return f"REL-{highest + 1:06d}"

    def txn_id_for(self, release_id: str) -> str:
        """同一个 release 的重试沿用同一个事务号（PRD §18.1 幂等）。"""
        try:
            return f"TXN-{int(release_id.split('-', 1)[1]):06d}"
        except (IndexError, ValueError) as exc:
            raise UsageError(f"release_id 形态不对：{release_id}") from exc

    def index_id_for(self, release_id: str) -> str:
        """一次发布对应一份索引。回退复用旧索引，不另开号。"""
        try:
            return f"IDX-{int(release_id.split('-', 1)[1]):06d}"
        except (IndexError, ValueError) as exc:
            raise UsageError(f"release_id 形态不对：{release_id}") from exc

    def next_receipt_id(self) -> str:
        highest = 0
        if self.receipts.is_dir():
            for path in self.receipts.glob("RCPT-*.json"):
                try:
                    highest = max(highest, int(path.stem.split("-", 1)[1]))
                except (IndexError, ValueError):
                    continue
        return f"RCPT-{highest + 1:06d}"

    # ---- 计划 ----
    def write_plan(self, plan: ReleasePlan, meta: PlanMeta) -> Path:
        directory = self.release_dir(plan.release_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = plan.bytes()
        path = self.plan_path(plan.release_id)
        if path.exists():
            if path.read_bytes() != payload:
                raise ConflictError(
                    f"{plan.release_id} 已有另一份计划（内容不同）。"
                    "同一 release 只能对应一份计划；要改内容请新建发布"
                )
        else:
            write_new(path, payload)
        write_atomic(self.meta_path(plan.release_id), _dump(meta.as_dict()))
        return path

    def load_plan(self, release_id: str) -> ReleasePlan:
        path = self.plan_path(release_id)
        if not path.exists():
            raise UsageError(f"发布计划不存在：{release_id}（先 novel release prepare）")
        return ReleasePlan.from_dict(_read_json(path))

    def load_meta(self, release_id: str) -> PlanMeta:
        path = self.meta_path(release_id)
        if not path.exists():
            raise ConflictError(f"{release_id} 缺少 plan.meta.json，无法判定它属于哪个任务")
        return PlanMeta.from_dict(_read_json(path))

    def find_plan_for_task(self, task_id: str, project_id: str = "") -> list[str]:
        """某任务下已准备的发布（新 → 旧）。`publish --task` 靠它找回计划。"""
        out: list[str] = []
        for release_id in reversed(self.list_releases()):
            if not self.meta_path(release_id).exists():
                continue
            try:
                meta = self.load_meta(release_id)
            except ConflictError:
                continue
            if meta.task_id != task_id:
                continue
            if project_id and meta.project_id != project_id:
                continue
            out.append(release_id)
        return out

    # ---- 日志 ----
    def write_journal(self, journal: Journal) -> Path:
        path = self.journal_path(journal.release_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, journal.bytes())
        return path

    def load_journal(self, release_id: str) -> Journal | None:
        path = self.journal_path(release_id)
        if not path.exists():
            return None
        return Journal.from_dict(_read_json(path))

    # ---- 清单 ----
    def write_manifest(self, manifest: ReleaseManifest) -> Path:
        path = self.manifest_path(manifest.release_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = manifest.bytes()
        if path.exists():
            if path.read_bytes() != payload:
                raise ConflictError(
                    f"{manifest.release_id} 的 manifest 已存在且内容不同，拒绝覆盖——"
                    "清单是不可变事实，改了它就没法证明发的是什么"
                )
            return path
        write_new(path, payload)
        return path

    def load_manifest(self, release_id: str) -> ReleaseManifest:
        path = self.manifest_path(release_id)
        if not path.exists():
            raise ConflictError(f"{release_id} 还没有 manifest（构建未完成）")
        return ReleaseManifest.from_dict(_read_json(path))

    def manifest_hash(self, release_id: str) -> str:
        path = self.manifest_path(release_id)
        if not path.exists():
            raise ConflictError(f"{release_id} 还没有 manifest")
        return sha256_file(path)

    # ---- 指针 ----
    def read_active(self) -> ActivePointer | None:
        """读正式指针。文件不存在 = 从未发布；文件损坏 = 报错，不猜。"""
        path = self.active_path()
        if not path.exists():
            return None
        data = _read_json(path)  # 坏了这里就抛，恢复模式由命令层决定怎么呈现
        pointer = ActivePointer.from_dict(data)
        if not pointer.release_id or not pointer.manifest_sha256:
            raise ConflictError(
                f"正式指针缺字段（{path}）：进入恢复模式，不自动猜测当前正式版本"
            )
        return pointer

    def write_active(self, pointer: ActivePointer) -> Path:
        path = self.active_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # write_atomic 已经做了「同目录临时文件 + fsync + os.replace」。
        # 不自己另写一套：两条实现迟早会走偏，而这条路径只在切换时用一次。
        write_atomic(path, _dump(pointer.as_dict()))
        return path

    # ---- 回执 ----
    def write_receipt(self, receipt: Receipt) -> Path:
        path = self.receipt_path(receipt.receipt_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, _dump(receipt.as_dict()))
        return path

    def load_receipt(self, receipt_id: str) -> Receipt | None:
        path = self.receipt_path(receipt_id)
        if not path.exists():
            return None
        return Receipt.from_dict(_read_json(path))

    def receipt_for_release(self, release_id: str) -> Receipt | None:
        if not self.receipts.is_dir():
            return None
        for path in sorted(self.receipts.glob("RCPT-*.json")):
            try:
                receipt = Receipt.from_dict(_read_json(path))
            except ConflictError:
                continue
            if receipt.release_id == release_id:
                return receipt
        return None


def _dump(data: dict) -> bytes:
    """落盘用缩进 JSON：这些文件是给人查的，不是给哈希算的。

    需要参与哈希的对象（plan / manifest / journal）用各自的 `.bytes()`，
    那里走规范形式；只有关联信息与人读记录用这个。
    """
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
