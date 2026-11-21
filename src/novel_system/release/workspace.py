"""隔离工作区：先把候选内容完整建出来，再谈切换。

为什么不在 Vault 里就地改：

- 就地改写到一半崩掉，正式区就是**混合版本**——PRD §17.1 的底线正是不出现这种状态；
- 正式区里还有作者的未提交手改，任何「先回滚再应用」的做法都会把它们卷进去。

所以做法是：把 Vault 的当前内容（排除 staging / 审阅 / 归档 / 缓存）复制成一份
快照，在快照上按**批准的精确字节**落变更，再对快照做一次 Git 提交，
得到 `canon_revision`。之后索引、校验、清单全部基于这份快照。
只有切换成功后，才把改动镜像回 Vault（失败只告警，不撤销发布——PRD §17.4 步骤 21）。

复制整棵树的代价是磁盘占用随发布次数线性增长。这是已知代价，不是疏忽：
换来的是「正式版永远对应一份完整、可复核、可回退的内容」。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConflictError, UsageError
from ..paths import ProjectPaths
from ..storage import sha256_bytes, sha256_file
from .model import Change, ManifestFile, ReleasePlan

# 不进快照的目录名：运行态、提案区、审阅意见、废弃旧版、编辑器缓存。
BASE_SKIP_PARTS = frozenset(
    {
        ".git",
        ".obsidian",
        ".trash",
        ".tmp",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
    }
)

GIT_TIMEOUT = 180


@dataclass
class SnapshotResult:
    path: Path
    files: list[ManifestFile] = field(default_factory=list)
    revision: str = ""
    revision_kind: str = ""
    warnings: list[str] = field(default_factory=list)
    skipped_parts: tuple[str, ...] = ()
    written: list[str] = field(default_factory=list)


def skip_parts(pp: ProjectPaths) -> tuple[str, ...]:
    """不复制哪些目录。名字从项目配置取，不写死。

    写死过一次 `90_Archive` 而真实库叫 `99_Archive`，结果整份归档进了索引；
    同类错误在快照里同样致命——归档会被当成正式内容发布。
    """
    names = set(BASE_SKIP_PARTS)
    for key in ("staging_root", "review_root", "archive_root"):
        rel = pp.dir_name(key)
        names.add(rel.rsplit("/", 1)[-1])
    return tuple(sorted(names))


def _should_skip(rel_parts: tuple[str, ...], skip: frozenset[str]) -> bool:
    return any(part in skip for part in rel_parts)


def copy_tree(src: Path, dest: Path, skip: tuple[str, ...]) -> None:
    """复制 src 到 dest，跳过指定目录名。不保留软链接（跟进去会逃出快照）。"""
    skip_set = frozenset(skip)
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        try:
            rel = path.relative_to(src)
        except ValueError:  # pragma: no cover - rglob 不会给这个
            continue
        if _should_skip(rel.parts, skip_set):
            continue
        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file() and not path.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def write_change_bytes(
    pp: ProjectPaths,
    plan: ReleasePlan,
    dest: Path,
    *,
    loader,
) -> dict[str, str]:
    """把每条变更的**精确字节**写进 `files/`，并核对哈希。

    `loader(change)` 返回 bytes（来自 staging，或回退时来自旧发布的快照）。
    返回 {路径: sha256}。哈希与计划不符直接失败——那说明批准的不是这些字节。
    """
    out: dict[str, str] = {}
    for change in plan.changes:
        data = loader(change)
        digest = sha256_bytes(data)
        if digest != change.content_hash:
            raise ConflictError(
                f"{change.path} 的字节与计划不符（计划 {change.content_hash[:12]}…，"
                f"实际 {digest[:12]}…）。批准的是前者，不能拿后者去发布"
            )
        target = dest / change.path
        pp.at(change.path)  # 只做越界校验：绝对路径、`..` 在这里就拒
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out[change.path] = digest
    return out


def apply_changes(snapshot: Path, plan: ReleasePlan, files_dir: Path) -> list[str]:
    """把 `files/` 里的字节落进快照，并处理删除项。返回实际写入的路径。"""
    written: list[str] = []
    for change in plan.changes:
        if change.op == "delete":
            target = snapshot / change.path
            if target.exists():
                target.unlink()
            continue
        source = files_dir / change.path
        if not source.exists():
            raise ConflictError(f"隔离工作区缺少 {change.path} 的字节，拒绝继续")
        target = snapshot / change.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        written.append(change.path)

    for rel in plan.removals:
        target = snapshot / rel
        if target.exists():
            target.unlink()
    return written


def scan_snapshot(snapshot: Path) -> list[ManifestFile]:
    """列出快照里的全部文件与哈希。清单要能回答「这一次到底发了什么」。"""
    out: list[ManifestFile] = []
    for path in sorted(snapshot.rglob("*")):
        if path.is_dir() or path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(snapshot).as_posix()
        if rel.startswith(".git/"):
            continue
        out.append(ManifestFile(path=rel, sha256=sha256_file(path), bytes=path.stat().st_size))
    return out


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=novel-system",
            "-c",
            "user.email=novel-system@localhost",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        check=False,
        timeout=GIT_TIMEOUT,
    )


def commit_snapshot(snapshot: Path, release_id: str, plan_hash: str) -> tuple[str, str, list[str]]:
    """给快照做一个独立仓库的提交，返回 (revision, kind, warnings)。

    找不到 git 或提交失败时退回内容摘要 `local:<sha256>`。**这是降级，要写进告警**：
    `canon_revision` 变成摘要意味着别人无法用 `git show` 复核这次发布，
    只能拿清单逐个文件核对。
    """
    warnings: list[str] = []
    if shutil.which("git") is None:
        warnings.append("找不到 git：canon_revision 退化为内容摘要，本次发布无法用 commit 复核")
        return _digest_revision(snapshot), "digest", warnings

    try:
        init = _git(snapshot, "init", "-q")
        if init.returncode != 0:
            raise OSError(init.stderr.decode("utf-8", "replace")[:200])
        add = _git(snapshot, "add", "-A")
        if add.returncode != 0:
            raise OSError(add.stderr.decode("utf-8", "replace")[:200])
        message = f"{release_id}: plan {plan_hash[:12]}"
        commit = _git(snapshot, "commit", "-q", "--no-verify", "-m", message)
        if commit.returncode != 0:
            detail = commit.stderr.decode("utf-8", "replace").strip()[:200]
            # 空快照（没有任何文件）时 git 会说 nothing to commit，这不是错误。
            if "nothing to commit" in detail:
                return _digest_revision(snapshot), "digest", warnings
            raise OSError(detail)
        head = _git(snapshot, "rev-parse", "HEAD")
        if head.returncode != 0:
            raise OSError(head.stderr.decode("utf-8", "replace")[:200])
        return head.stdout.decode("utf-8").strip(), "git", warnings
    except (OSError, subprocess.SubprocessError) as exc:
        warnings.append(f"候选提交失败，canon_revision 退化为内容摘要：{exc}")
        return _digest_revision(snapshot), "digest", warnings


def _digest_revision(snapshot: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(snapshot.rglob("*")):
        if path.is_dir() or not path.is_file():
            continue
        rel = path.relative_to(snapshot).as_posix()
        if rel.startswith(".git/"):
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "local:" + digest.hexdigest()


def build_snapshot(
    pp: ProjectPaths,
    dest: Path,
    *,
    plan: ReleasePlan,
    files_dir: Path,
    release_id: str,
    plan_hash: str,
) -> SnapshotResult:
    """复制 → 落字节 → 提交 → 列清单。任一步失败都不留半个「候选」。"""
    skip = skip_parts(pp)
    if dest.exists():
        # 重试时复用同一个目录：上一次可能已经复制了一半。
        # 不复用会留下两份内容，谁也不敢删。
        shutil.rmtree(dest)
    copy_tree(pp.vault, dest, skip)
    written = apply_changes(dest, plan, files_dir)
    revision, kind, warnings = commit_snapshot(dest, release_id, plan_hash)
    files = scan_snapshot(dest)

    # 计划里的每一条都必须真的落在快照里：这是「文件集与计划一致」的第一次核对。
    present = {item.path for item in files}
    missing = [c.path for c in plan.changes if c.op != "delete" and c.path not in present]
    if missing:
        raise ConflictError(f"隔离工作区缺少计划中的文件：{missing}")
    for rel in plan.removals:
        if rel in present:
            raise ConflictError(f"隔离工作区里 {rel} 应已删除但仍在")

    return SnapshotResult(
        path=dest,
        files=files,
        revision=revision,
        revision_kind=kind,
        warnings=warnings,
        skipped_parts=skip,
        written=written,
    )


def source_change_loader(pp: ProjectPaths, staging_paths: dict[str, str]):
    """从 staging 读字节。`staging_paths` 是 {目标相对路径: Vault 内相对路径}。"""

    def load(change: Change) -> bytes:
        raw = staging_paths.get(change.path)
        if not raw:
            raise UsageError(f"计划里的 {change.path} 没有对应的 staging 产物")
        path = pp.vault / raw
        if not path.exists():
            raise ConflictError(f"staging 文件已丢失：{raw}（stage 之后被删了？）")
        return path.read_bytes()

    return load


def release_change_loader(snapshot: Path):
    """从旧发布的快照读字节（回退用）。"""

    def load(change: Change) -> bytes:
        path = snapshot / change.path
        if not path.exists():
            raise ConflictError(f"旧发布快照里没有 {change.path}，无法回退到它")
        return path.read_bytes()

    return load
