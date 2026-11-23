"""发布协议的四类记录（PRD §17.2、§17.4、§18）。

四者职责不同，混在一起就会出「用回执证明内容、用清单证明批准」这类错：

| 记录 | 何时产生 | 可变？ | 证明什么 |
|---|---|---|---|
| ReleasePlan | 执行前 | 是（同一 release_id 下重新准备会换哈希，批准随之失效） | 作者批准的是**这份计划** |
| ReleaseManifest | 构建后 | **否** | 候选内容长这样，且只长这样 |
| 事务日志 | 执行中逐步写 | 是（只追加步骤） | 崩在哪一步、还能不能续 |
| 回执 | 切换成功后 | 可修复（补回执带 repaired=true） | 这次切换确实发生了 |

清单**不写入它自己引用的 canon_revision 的父提交**，也不写自己的哈希——
写进去就成了自引用，哈希永远算不出来（PRD §17.2 末段）。

所有 JSON 落盘用规范形式（`canonical.package_bytes`）：键排序、紧凑分隔符、
中文不转义。好处是**文件字节哈希 == package_hash(对象)**，回执里记的
`manifest_hash` 可以直接用 `sha256_file` 复核，不用再解析一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..canonical import package_bytes, package_hash
from ..domain.errors import UsageError

# PRD §17.4 的 22 步。名字写在这里而不是引擎里，是为了让日志和文档用同一套说法。
STEPS: tuple[tuple[int, str], ...] = (
    (1, "创建 release_id 与事务日志"),
    (2, "验证预期 base_release"),
    (3, "校验 ReleasePlan 及其批准"),
    (4, "创建隔离工作区"),
    (5, "按批准的精确字节应用变更"),
    (6, "校验路径、哈希、解析与审批"),
    (7, "核对最终文件集与 ReleasePlan 一致"),
    (8, "生成候选 Git 提交"),
    (9, "按该提交构建新 SQLite"),
    (10, "关闭连接并检查索引完整性"),
    (11, "计算索引字节哈希"),
    (12, "写入不可变 ReleaseManifest"),
    (13, "执行正式切换前校验"),
    (14, "获取发布锁"),
    (15, "再次比较 active_release 与 base_release"),
    (16, "把新指针写入同目录临时文件"),
    (17, "flush/fsync 持久化"),
    (18, "原子替换 active_release"),
    (19, "写入成功回执"),
    (20, "释放锁"),
    (21, "刷新正式区镜像"),
    (22, "触发备份与依赖治理提示"),
)

STEP_NAMES = dict(STEPS)

JOURNAL_STATES = (
    "PREPARED",
    "VALIDATED",
    "READY_TO_ACTIVATE",
    "ACTIVE",
    "SUPERSEDED",
    "ABORTED",
)
STEP_STATUSES = ("pending", "done", "failed", "skipped")

CHANGE_OPS = ("add", "modify", "delete", "replace")


@dataclass(frozen=True)
class Change:
    """一条变更。`content_hash` 是**批准时那一份字节**的 sha256。"""

    path: str
    op: str
    content_hash: str

    def as_dict(self) -> dict:
        return {"path": self.path, "op": self.op, "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, data: dict) -> "Change":
        return cls(
            path=str(data.get("path") or ""),
            op=str(data.get("op") or "modify"),
            content_hash=str(data.get("content_hash") or ""),
        )


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


@dataclass
class ReleasePlan:
    """作者审批的对象。规范 JSON 的哈希即批准依据（PRD §17.2）。"""

    release_id: str
    base_release: str | None
    changes: list[Change]
    removals: list[str] = field(default_factory=list)
    text_approvals: list[str] = field(default_factory=list)
    memory_approvals: list[str] = field(default_factory=list)
    change_requests: list[str] = field(default_factory=list)
    nonblocking_issues: list[str] = field(default_factory=list)
    schema_version: str = ""
    index_config_version: str = ""
    prepared_at: str = ""
    # 本批含正文却跳过共同发布闸时置 True。只在 True 时进 `as_dict`——
    # 0.11.0 及更早写下的计划没有这个键，无条件输出会让它们重新序列化后
    # 哈希变化，已批准待执行的计划会突然「批准的是另一份计划」。
    closure_skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        """只输出 schema 里有的字段，顺序固定；`None` 表示首次发布。"""
        out = {
            "release_id": self.release_id,
            "base_release": self.base_release,
            "changes": [c.as_dict() for c in self.changes],
            "removals": list(self.removals),
            "text_approvals": list(self.text_approvals),
            "memory_approvals": list(self.memory_approvals),
            "change_requests": list(self.change_requests),
            "nonblocking_issues": list(self.nonblocking_issues),
            "schema_version": self.schema_version,
            "index_config_version": self.index_config_version,
            "prepared_at": self.prepared_at,
        }
        if self.closure_skipped:
            out["closure_skipped"] = True
        return out

    @property
    def hash(self) -> str:
        return package_hash(self.as_dict())

    def bytes(self) -> bytes:
        return package_bytes(self.as_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "ReleasePlan":
        return cls(
            release_id=str(data.get("release_id") or ""),
            base_release=(None if data.get("base_release") in (None, "") else str(data["base_release"])),
            changes=[Change.from_dict(item) for item in data.get("changes") or [] if isinstance(item, dict)],
            removals=[str(x) for x in data.get("removals") or []],
            text_approvals=[str(x) for x in data.get("text_approvals") or []],
            memory_approvals=[str(x) for x in data.get("memory_approvals") or []],
            change_requests=[str(x) for x in data.get("change_requests") or []],
            nonblocking_issues=[str(x) for x in data.get("nonblocking_issues") or []],
            schema_version=str(data.get("schema_version") or ""),
            index_config_version=str(data.get("index_config_version") or ""),
            prepared_at=str(data.get("prepared_at") or ""),
            closure_skipped=bool(data.get("closure_skipped")),
        )


@dataclass(frozen=True)
class ManifestFile:
    path: str
    sha256: str
    bytes: int

    def as_dict(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}

    @classmethod
    def from_dict(cls, data: dict) -> "ManifestFile":
        return cls(
            path=str(data.get("path") or ""),
            sha256=str(data.get("sha256") or ""),
            bytes=int(data.get("bytes") or 0),
        )


@dataclass
class ReleaseManifest:
    """构建后的不可变事实。写一次，之后只读。"""

    release_id: str
    base_release: str | None
    release_plan_hash: str
    canon_revision: str
    index_build_id: str
    index_bytes_hash: str
    files: list[ManifestFile]
    approval_refs: list[str] = field(default_factory=list)
    tool_version: str = ""
    schema_version: str = ""
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "base_release": self.base_release,
            "release_plan_hash": self.release_plan_hash,
            "canon_revision": self.canon_revision,
            "index_build_id": self.index_build_id,
            "index_bytes_hash": self.index_bytes_hash,
            "files": [f.as_dict() for f in self.files],
            "approval_refs": list(self.approval_refs),
            "tool_version": self.tool_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
        }

    @property
    def hash(self) -> str:
        return package_hash(self.as_dict())

    def bytes(self) -> bytes:
        return package_bytes(self.as_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "ReleaseManifest":
        return cls(
            release_id=str(data.get("release_id") or ""),
            base_release=(None if data.get("base_release") in (None, "") else str(data["base_release"])),
            release_plan_hash=str(data.get("release_plan_hash") or ""),
            canon_revision=str(data.get("canon_revision") or ""),
            index_build_id=str(data.get("index_build_id") or ""),
            index_bytes_hash=str(data.get("index_bytes_hash") or ""),
            files=[ManifestFile.from_dict(item) for item in data.get("files") or [] if isinstance(item, dict)],
            approval_refs=[str(x) for x in data.get("approval_refs") or []],
            tool_version=str(data.get("tool_version") or ""),
            schema_version=str(data.get("schema_version") or ""),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass
class JournalStep:
    step: int
    name: str = ""
    status: str = "pending"
    at: str = ""
    detail: str = ""

    def as_dict(self) -> dict:
        payload = {"step": self.step, "status": self.status}
        if self.name:
            payload["name"] = self.name
        if self.at:
            payload["at"] = self.at
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class Journal:
    """事务日志。恢复程序的依据之一，**不是**唯一依据（PRD §18.2）。"""

    txn_id: str
    release_id: str
    base_release: str | None = None
    release_plan_hash: str = ""
    state: str = "PREPARED"
    steps: list[JournalStep] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "txn_id": self.txn_id,
            "release_id": self.release_id,
            "state": self.state,
            "steps": [s.as_dict() for s in self.steps],
            "created_at": self.created_at,
        }
        if self.base_release:
            payload["base_release"] = self.base_release
        if self.release_plan_hash:
            payload["release_plan_hash"] = self.release_plan_hash
        if self.updated_at:
            payload["updated_at"] = self.updated_at
        return payload

    def bytes(self) -> bytes:
        return package_bytes(self.as_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Journal":
        return cls(
            txn_id=str(data.get("txn_id") or ""),
            release_id=str(data.get("release_id") or ""),
            base_release=(None if data.get("base_release") in (None, "") else str(data["base_release"])),
            release_plan_hash=str(data.get("release_plan_hash") or ""),
            state=str(data.get("state") or "PREPARED"),
            steps=[
                JournalStep(
                    step=int(item.get("step") or 0),
                    name=str(item.get("name") or ""),
                    status=str(item.get("status") or "pending"),
                    at=str(item.get("at") or ""),
                    detail=str(item.get("detail") or ""),
                )
                for item in data.get("steps") or []
                if isinstance(item, dict)
            ],
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    # ---- 查询 ----
    @property
    def done_steps(self) -> list[int]:
        return [s.step for s in self.steps if s.status == "done"]

    @property
    def failed_steps(self) -> list[int]:
        return [s.step for s in self.steps if s.status == "failed"]

    def last_done(self) -> int:
        return max(self.done_steps or [0])

    def step(self, number: int) -> JournalStep | None:
        for item in self.steps:
            if item.step == number:
                return item
        return None

    def set_step(self, number: int, status: str, *, at: str = "", detail: str = "") -> None:
        if number not in STEP_NAMES:
            raise UsageError(f"未知步骤号：{number}（1–22）")
        if status not in STEP_STATUSES:
            raise UsageError(f"未知步骤状态：{status}")
        item = self.step(number)
        if item is None:
            item = JournalStep(step=number, name=STEP_NAMES[number])
            self.steps.append(item)
            self.steps.sort(key=lambda s: s.step)
        item.status = status
        if at:
            item.at = at
        if detail:
            item.detail = detail


@dataclass
class Receipt:
    """切换成功的对外凭证。`repaired=true` 表示这是恢复程序补写的。"""

    receipt_id: str
    release_id: str
    manifest_hash: str
    canon_revision: str
    index_build_id: str
    created_at: str
    base_release: str | None = None
    active_since: str = ""
    repaired: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "receipt_id": self.receipt_id,
            "release_id": self.release_id,
            "manifest_hash": self.manifest_hash,
            "canon_revision": self.canon_revision,
            "index_build_id": self.index_build_id,
            "created_at": self.created_at,
            "repaired": self.repaired,
            "warnings": list(self.warnings),
        }
        if self.base_release:
            payload["base_release"] = self.base_release
        if self.active_since:
            payload["active_since"] = self.active_since
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "Receipt":
        return cls(
            receipt_id=str(data.get("receipt_id") or ""),
            release_id=str(data.get("release_id") or ""),
            manifest_hash=str(data.get("manifest_hash") or ""),
            canon_revision=str(data.get("canon_revision") or ""),
            index_build_id=str(data.get("index_build_id") or ""),
            created_at=str(data.get("created_at") or ""),
            base_release=(None if data.get("base_release") in (None, "") else str(data["base_release"])),
            active_since=str(data.get("active_since") or ""),
            repaired=bool(data.get("repaired")),
            warnings=[str(x) for x in data.get("warnings") or []],
        )


@dataclass
class ActivePointer:
    """正式指针。**只有这一个文件是正式版的权威**（PRD §17.1）。"""

    release_id: str
    canon_revision: str
    index_build_id: str
    manifest_sha256: str
    activated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "release_id": self.release_id,
            "canon_revision": self.canon_revision,
            "index_build_id": self.index_build_id,
            "manifest_sha256": self.manifest_sha256,
        }
        if self.activated_at:
            payload["activated_at"] = self.activated_at
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "ActivePointer":
        return cls(
            release_id=str(data.get("release_id") or ""),
            canon_revision=str(data.get("canon_revision") or ""),
            index_build_id=str(data.get("index_build_id") or ""),
            manifest_sha256=str(data.get("manifest_sha256") or ""),
            activated_at=str(data.get("activated_at") or ""),
        )


def normalise_ids(values: list[str]) -> list[str]:
    """批准引用去重保序。计划哈希要稳定，顺序就不能随调用方抖动。"""
    return _dedup(list(values))
