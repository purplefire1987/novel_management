"""作者编辑基线。

作者在 Obsidian 里直接改文件是合法主路径（V4.0 §7.3）。系统不做的是「回滚作者」，
要做的是「登记作者」：算出哪些文件变了、推进基线、留下不可篡改的记录。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..audit import AUTHOR, now_iso
from ..errors import SecurityError
from ..paths import ProjectPaths
from ..storage import iter_files, sha256_file, write_atomic

SCAN_SUFFIXES = {".md", ".markdown", ".yaml", ".yml", ".json", ".txt", ".csv"}

# 运行态草稿与归档不进基线：前者本来就是流动的，后者是历史堆积——
# 纳进来会让 adopt 每次都报一堆「新增」，真正的手改反而被埋掉。
EXCLUDE_PREFIXES = ("07_Staging", "08_Reviews", "90_Archive")

HISTORY_LIMIT = 100


class AuthorBaseline:
    def __init__(self, runtime: Path, project_id: str):
        self.runtime = runtime
        self.project_id = project_id
        self.dir = runtime / "authorship"
        self.path = self.dir / f"{project_id}.json"

    # ---- 读写 ----
    def load(self) -> dict:
        if not self.path.exists():
            return {"project_id": self.project_id, "updated_at": "", "files": {}, "history": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        data.setdefault("project_id", self.project_id)
        data.setdefault("files", {})
        data.setdefault("history", [])
        return data

    def save(self, data: dict) -> None:
        data["updated_at"] = now_iso()
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    # ---- 扫描 ----
    def scan(self, pp: ProjectPaths) -> dict[str, dict]:
        """项目内受管文件的当前状态。键是项目内相对路径。"""
        out: dict[str, dict] = {}
        for path in iter_files(pp.root):
            rel = path.relative_to(pp.root).as_posix()
            if any(rel.startswith(prefix + "/") for prefix in EXCLUDE_PREFIXES):
                continue
            if path.suffix.lower() not in SCAN_SUFFIXES:
                continue
            stat = path.stat()
            out[rel] = {
                "sha256": sha256_file(path),
                "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            }
        return out

    def compare(self, pp: ProjectPaths) -> dict[str, list[str]]:
        baseline = self.load().get("files", {})
        current = self.scan(pp)
        added = sorted(set(current) - set(baseline))
        removed = sorted(set(baseline) - set(current))
        modified = sorted(
            rel for rel in set(current) & set(baseline) if current[rel]["sha256"] != baseline.get(rel, {}).get("sha256")
        )
        unchanged = len(set(current) & set(baseline)) - len(modified)
        return {
            "added": added,
            "modified": modified,
            "removed": removed,
            "unchanged": unchanged,
            "scanned": len(current),
        }

    # ---- 登记 ----
    def adopt(self, pp: ProjectPaths, *, actor: str = AUTHOR, note: str = "") -> dict:
        if actor != AUTHOR:
            raise SecurityError("adopt 登记的是作者的手改，主体只能是作者")
        report = self.compare(pp)
        data = self.load()
        first_time = not data.get("files")
        data["files"] = self.scan(pp)
        if not first_time:
            data["history"].append(
                {
                    "at": now_iso(),
                    "actor": actor,
                    "note": note,
                    "added": len(report["added"]),
                    "modified": len(report["modified"]),
                    "removed": len(report["removed"]),
                    "modified_paths": report["modified"][:50],
                    "added_paths": report["added"][:50],
                    "removed_paths": report["removed"][:50],
                }
            )
            data["history"] = data["history"][-HISTORY_LIMIT:]
        self.save(data)
        report["first_time"] = first_time
        return report

    def record_publish(self, *, task_id: str, paths: list[str], note: str = "") -> None:
        """发布记入历史。基线本身由 refresh_paths 推进。"""
        data = self.load()
        data["history"].append(
            {
                "at": now_iso(),
                "actor": "version_manager",
                "note": note or f"publish {task_id}",
                "added": 0,
                "modified": len(paths),
                "removed": 0,
                "modified_paths": paths[:50],
                "added_paths": [],
                "removed_paths": [],
            }
        )
        data["history"] = data["history"][-HISTORY_LIMIT:]
        self.save(data)

    def refresh_paths(self, pp: ProjectPaths, paths: list[str]) -> None:
        """把指定文件的当前状态写回基线，避免发布后误报作者手改。"""
        data = self.load()
        files = data.setdefault("files", {})
        for rel in paths:
            target = pp.at(rel)
            if not target.exists():
                files.pop(rel, None)
                continue
            stat = target.stat()
            files[rel] = {
                "sha256": sha256_file(target),
                "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            }
        self.save(data)
