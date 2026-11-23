"""staging：Agent 产出的唯一落点。正式区在 publish 之前一个字都不动。

目录约定：`<staging_root>/<task_id>/<area>/<目标正式相对路径>`，
`<area>` 由任务类型决定，保证工作流语义在目录上看得见；
`manifest.json` 记录「这份产物拟替换哪个正式文件、基于哪个版本」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..audit import now_iso
from ..domain.errors import ConflictError, SecurityError, UsageError
from ..infrastructure.filesystem.paths import ProjectPaths
from ..infrastructure.filesystem.storage import sha256_bytes, sha256_file, write_atomic, write_new
from ..tasks import Task

MANIFEST_NAME = "manifest.json"

# 任务类型 → staging 分栏。与 workflows/*.yaml 里的 output 路径对齐。
AREA_BY_TASK = {
    "outline": "plans",
    "preflight": "preflight",
    "draft": "drafts",
    "revision": "drafts",
    "review": "reviews",
    "memory_update": "memory",
    "ingest": "ingest",
    "simulation": "simulation",
    "release": "release",
}


@dataclass
class StagedFile:
    target: str            # 项目内正式相对路径
    staged: str            # Vault 内相对路径
    sha256: str
    base_sha256: str       # stage 时正式文件的哈希；空表示当时不存在
    bytes: int = 0
    staged_at: str = ""
    actor: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "staged": self.staged,
            "sha256": self.sha256,
            "base_sha256": self.base_sha256,
            "bytes": self.bytes,
            "staged_at": self.staged_at,
            "actor": self.actor,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StagedFile":
        return cls(
            target=str(data.get("target") or ""),
            staged=str(data.get("staged") or ""),
            sha256=str(data.get("sha256") or ""),
            base_sha256=str(data.get("base_sha256") or ""),
            bytes=int(data.get("bytes") or 0),
            staged_at=str(data.get("staged_at") or ""),
            actor=str(data.get("actor") or ""),
            note=str(data.get("note") or ""),
        )


class StagingArea:
    def __init__(self, pp: ProjectPaths, task: Task):
        self.pp = pp
        self.task = task
        self.area = AREA_BY_TASK.get(task.task_type, "proposals")

    @property
    def root(self) -> Path:
        return self.pp.staging / self.task.task_id

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    # ---- manifest ----
    def load_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {
                "task_id": self.task.task_id,
                "project_id": self.task.project_id,
                "task_type": self.task.task_type,
                "area": self.area,
                "updated_at": "",
                "files": [],
            }
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ConflictError(f"manifest 损坏，拒绝继续：{self.manifest_path}（{exc}）") from exc
        data.setdefault("files", [])
        return data

    def save_manifest(self, data: dict) -> None:
        data["updated_at"] = now_iso()
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        write_atomic(self.manifest_path, payload.encode("utf-8"))

    def files(self) -> list[StagedFile]:
        return [StagedFile.from_dict(item) for item in self.load_manifest().get("files", []) if isinstance(item, dict)]

    # ---- 写入 ----
    def stage(
        self,
        *,
        data: bytes,
        target_rel: str,
        actor: str,
        note: str = "",
        replace: bool = False,
    ) -> StagedFile:
        if self.task.write_mode == "read_only":
            raise SecurityError(f"任务 {self.task.task_id} 是 read_only，不允许写 staging")

        target_rel = target_rel.strip().lstrip("/")
        if not target_rel:
            raise UsageError("缺少 --to（目标正式文件的项目内相对路径）")
        formal = self.pp.at(target_rel)
        if not self.pp.is_control_path(formal):
            allowed = ", ".join(p.name for p in self.pp.control_roots())
            raise SecurityError(f"目标不在正式区（{allowed}）：{target_rel}")

        staged_path = self.root / self.area / target_rel
        digest = sha256_bytes(data)

        manifest = self.load_manifest()
        existing = {item.get("target"): item for item in manifest.get("files", [])}
        if target_rel in existing:
            if not replace:
                old = existing[target_rel].get("sha256", "")
                hint = "内容相同，无需重复 stage" if old == digest else "确认替换请加 --replace"
                raise ConflictError(f"{target_rel} 已在本任务 staging 中（{hint}）")
        elif staged_path.exists() and not replace:
            raise ConflictError(f"staging 已存在同名产物：{staged_path}（确认覆盖请加 --replace）")

        base = sha256_file(formal) if formal.exists() else ""
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        if staged_path.exists():
            write_atomic(staged_path, data)
        else:
            write_new(staged_path, data)

        record = StagedFile(
            target=target_rel,
            staged=self.pp.rel(staged_path),
            sha256=digest,
            base_sha256=base,
            bytes=len(data),
            staged_at=now_iso(),
            actor=actor,
            note=note,
        )
        manifest["files"] = [
            item for item in manifest.get("files", []) if item.get("target") != target_rel
        ] + [record.as_dict()]
        manifest["files"].sort(key=lambda item: str(item.get("target")))
        manifest["task_type"] = self.task.task_type
        manifest["area"] = self.area
        self.save_manifest(manifest)
        return record

    # ---- 比对 ----
    def diff(self, target: str | None = None) -> list[dict]:
        """逐条比对 staged 与正式文件。kind: added / modified / identical / conflict。"""
        rows: list[dict] = []
        for item in self.files():
            if target and item.target != target:
                continue
            formal = self.pp.at(item.target)
            staged_path = self.pp.vault / item.staged
            kind = "added"
            detail = "正式文件不存在，属新增"
            formal_sha = ""
            if formal.exists():
                formal_sha = sha256_file(formal)
                if formal_sha == item.sha256:
                    kind, detail = "identical", "与正式文件一致"
                elif item.base_sha256 and formal_sha != item.base_sha256:
                    kind = "conflict"
                    detail = "staging 生成后正式文件被改过（可能是作者手改），直接发布将覆盖它"
                else:
                    kind, detail = "modified", "将替换正式文件"
            rows.append(
                {
                    "target": item.target,
                    "staged": staged_path,
                    "staged_sha256": item.sha256,
                    "formal_sha256": formal_sha,
                    "formal": formal,
                    "kind": kind,
                    "detail": detail,
                }
            )
        return rows


def artifact_id_of(target: str) -> str:
    """从 `01_Canon/Characters/CHAR-0001_某人.md` 里取 `CHAR-0001`。

    文件名带标题后缀（真实库的约定是 `{id}_{标题}.md`），所以不能直接拿 stem 当 ID。
    取不到合法前缀时原样返回 stem——宁可多拦一格，也不要静默变成空串。
    """
    name = target.rsplit("/", 1)[-1]
    stem = name[:-3] if name.endswith(".md") else name
    return stem.split("_", 1)[0]
