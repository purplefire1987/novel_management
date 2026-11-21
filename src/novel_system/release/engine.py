"""发布引擎：PRD §17.4 的 22 步，一步一步留痕。

设计上有三条不可让步的：

1. **先建完，再切换。** 22 步里 1–13 全在候选区完成，第 18 步才动指针。
   任何一步失败，正式版仍然是上一个版本——不存在「改了一半」的正式区。
2. **每个断点都能续、能停、能查。** 步骤状态写进 `journal.json`，
   恢复程序结合指针、清单、日志、哈希**交叉验证**，而不是「取最新那个文件」。
3. **幂等靠编号，不靠运气。** `release_id` 与索引号在重试中不变；
   manifest 已存在且内容相同视为同一次重试，内容不同直接报冲突。

## 故障注入

`NOVEL_RELEASE_FAULT=before:18` 在进入第 18 步前抛异常，`after:18` 在第 18 步
做完后抛异常。它只用于测试断点恢复，不改变正常路径的行为；抛的是普通异常，
和真实崩溃在磁盘上留下的痕迹一致（步骤状态 + 指针 + 清单）。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import markdown as md
from ..canonical import utc_now
from ..changes import ChangeStore, IssueStore  # noqa: F401 - 类型提示用
from ..errors import ApprovalError, ConflictError, ParseError, SecurityError, UsageError
from ..indexing import schema as index_schema
from ..indexing.builder import build_index
from ..indexing.source import WorktreeSource, skip_dirs_from_layout
from ..paths import ProjectPaths
from ..staging import StagingArea
from ..storage import copy_verified, sha256_file, write_atomic
from ..version import __version__
from .gates import CANON_PREFIX, check_approval, check_gates
from .lock import PublishLock
from .model import (
    STEP_NAMES,
    ActivePointer,
    Change,
    Journal,
    ManifestFile,
    Receipt,
    ReleaseManifest,
    ReleasePlan,
)
from .store import ACTIVE_NAME, PlanMeta, ReleaseStore
from .workspace import (
    apply_changes,
    build_snapshot,
    copy_tree,
    project_rel,
    release_change_loader,
    scan_snapshot,
    skip_parts,
    source_change_loader,
    vault_rel,
    write_change_bytes,
)

FAULT_ENV = "NOVEL_RELEASE_FAULT"
TEXT_PREFIX = "03_Manuscripts"
MEMORY_PREFIXES = ("06_Memory", "01_Canon")


class InjectedFault(RuntimeError):
    """测试用的注入故障。生产路径不会产生它。"""


def _fault_point() -> str:
    return os.environ.get(FAULT_ENV, "").strip()


def _maybe_fault(where: str) -> None:
    if _fault_point() == where:
        raise InjectedFault(f"注入故障：{where}")


@dataclass
class ExecutionResult:
    release_id: str
    state: str = ""
    receipt_id: str = ""
    canon_revision: str = ""
    index_build_id: str = ""
    written: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resumed: bool = False
    already_active: bool = False
    steps_done: list[int] = field(default_factory=list)
    task_id: str = ""


# --------------------------------------------------------------------------
# 准备计划
# --------------------------------------------------------------------------

def _scope_of(path: str) -> str:
    for prefix in (TEXT_PREFIX, *MEMORY_PREFIXES):
        if path == prefix or path.startswith(prefix + "/"):
            return prefix
    return ""


def prepare_plan(
    pp: ProjectPaths,
    task,
    staging: StagingArea,
    approval,
    store: ReleaseStore,
    *,
    actor: str,
    issue_store=None,
    change_store=None,
    confirm_canon: bool = False,
    reuse_index_of: str = "",
    bytes_from: str = "",
    reason: str = "",
) -> tuple[ReleasePlan, PlanMeta]:
    """把「已批准的 staging」翻译成 ReleasePlan。

    计划里每条变更的 `content_hash` 都是**此刻磁盘上的字节**算出来的。
    staging 里那份 manifest 只是 stage 当时的记录；中间被人动过就以磁盘为准，
    这样「作者批的」和「要发的」指的是同一份字节。
    """
    files = staging.files()
    if not files:
        raise ConflictError(f"任务 {task.task_id} 的 staging 为空，没有可发布内容")

    changes: list[Change] = []
    for item in sorted(files, key=lambda f: f.target):
        staged_path = pp.vault / item.staged
        if not staged_path.exists():
            raise ConflictError(f"staging 文件丢失：{item.staged}")
        digest = sha256_file(staged_path)
        formal = pp.at(item.target)
        op = "modify" if formal.exists() else "add"
        changes.append(Change(path=item.target, op=op, content_hash=digest))

    targets = [c.path for c in changes]
    text_approvals = [approval.approval_id] if any(_scope_of(t) == TEXT_PREFIX for t in targets) else []
    memory_approvals = [
        approval.approval_id for _ in [0] if any(_scope_of(t) in MEMORY_PREFIXES for t in targets)
    ]

    change_requests: list[str] = []
    nonblocking: list[str] = []
    if change_store is not None:
        change_requests = sorted(
            c.change_id
            for c in change_store.all(project_id=pp.project_id)
            if c.status == "APPROVED" and c.scope_hits(targets)
        )
    if issue_store is not None:
        from ..staging import artifact_id_of

        upstream = {task.task_id} | {artifact_id_of(t) for t in targets}
        nonblocking = sorted(
            i.issue_id
            for i in issue_store.all(project_id=pp.project_id)
            if i.status == "OPEN" and i.severity != "BLOCKER" and i.target_id in upstream
        )

    release_id = store.next_release_id()
    active = store.read_active()
    plan = ReleasePlan(
        release_id=release_id,
        base_release=(active.release_id if active else None),
        changes=changes,
        removals=[],
        text_approvals=text_approvals,
        memory_approvals=memory_approvals,
        change_requests=change_requests,
        nonblocking_issues=nonblocking,
        schema_version=__version__,
        index_config_version=f"{index_schema.SCHEMA_VERSION}/{index_schema.BUILDER_VERSION}",
        prepared_at=utc_now(),
    )
    meta = PlanMeta(
        release_id=release_id,
        task_id=task.task_id,
        project_id=pp.project_id,
        project_root=str(pp.root),
        prepared_by=actor,
        prepared_at=plan.prepared_at,
        artifact_approval=approval.approval_id,
        reuse_index_of=reuse_index_of,
        bytes_from=bytes_from,
        reason=reason,
        confirm_canon=confirm_canon,
        staged_paths={item.target: item.staged for item in files},
    )
    store.write_plan(plan, meta)
    return plan, meta


def prepare_rollback(
    pp: ProjectPaths,
    store: ReleaseStore,
    target_release: str,
    *,
    actor: str,
    reason: str,
    confirm_canon: bool = False,
) -> tuple[ReleasePlan, PlanMeta]:
    """回退包（PRD §18.3）：新建 release_id，复用旧版内容与索引。

    **不删除导致回退的那次发布记录**——回退是新的受审计操作，不是时间倒流。
    """
    active = store.read_active()
    if active is None:
        raise ConflictError("当前没有任何正式发布，无从回退")
    if active.release_id == target_release:
        raise ConflictError(f"{target_release} 就是当前正式版，不需要回退")

    target_manifest = store.load_manifest(target_release)
    current_files = {
        item.path: item.sha256 for item in store.load_manifest(active.release_id).files
    }
    target_files = {item.path: item.sha256 for item in target_manifest.files}

    changes: list[Change] = []
    removals: list[str] = []
    # 清单是 Vault 相对（快照的坐标系），计划是项目相对——换算，不混用。
    for path in sorted(target_files):
        if current_files.get(path) != target_files[path]:
            op = "modify" if path in current_files else "add"
            changes.append(
                Change(path=project_rel(pp, path), op=op, content_hash=target_files[path])
            )
    for path in sorted(set(current_files) - set(target_files)):
        removals.append(project_rel(pp, path))
    if not changes and not removals:
        raise ConflictError(f"{target_release} 的内容与当前正式版完全相同，无需回退")

    release_id = store.next_release_id()
    plan = ReleasePlan(
        release_id=release_id,
        base_release=active.release_id,
        changes=changes,
        removals=removals,
        schema_version=__version__,
        index_config_version=f"{index_schema.SCHEMA_VERSION}/{index_schema.BUILDER_VERSION}",
        prepared_at=utc_now(),
    )
    meta = PlanMeta(
        release_id=release_id,
        task_id="",
        project_id=pp.project_id,
        project_root=str(pp.root),
        prepared_by=actor,
        prepared_at=plan.prepared_at,
        reuse_index_of=target_release,
        bytes_from=target_release,
        reason=reason or f"回退到 {target_release}",
        confirm_canon=confirm_canon,
    )
    store.write_plan(plan, meta)
    return plan, meta


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

def _receipt_warnings(meta: PlanMeta, plan: ReleasePlan) -> list[str]:
    """回执里必须留给作者看的东西。空回执不算合规。"""
    warnings: list[str] = []
    if meta.reuse_index_of:
        warnings.append(
            f"回退到 {meta.reuse_index_of}：内容与索引复用该版，但这是一次新的发布"
            f"（{plan.release_id}，base={plan.base_release or '空'}）。"
            f"原因：{meta.reason or '（未填写）'}。"
            "相关依赖标记为待复核；对外平台内容不在自动同步范围，需人工处理"
        )
    return warnings


def _mark(store: ReleaseStore, journal: Journal, step: int, status: str, detail: str = "") -> None:
    journal.set_step(step, status, at=utc_now(), detail=detail)
    journal.updated_at = utc_now()
    store.write_journal(journal)


def _verify_approval(pp: ProjectPaths, plan: ReleasePlan, meta: PlanMeta, approval_store) -> str:
    approvals = approval_store.for_target("release", plan.release_id)
    if not approvals:
        raise ApprovalError(
            f"发布计划 {plan.release_id} 还没有作者批准。"
            f"先看：novel release show {plan.release_id}；"
            f"再批：novel approve release {plan.release_id} --hash {plan.hash} --as author"
        )
    approval = approvals[-1]
    if approval.target_hash != plan.hash:
        raise ApprovalError(
            f"批准的是另一份计划（{approval.target_hash[:12]}…），当前计划是 {plan.hash[:12]}…。"
            "计划重新准备过就作废，需重新批准"
        )
    if approval.project_id and approval.project_id != pp.project_id:
        raise ApprovalError(f"批准属于项目 {approval.project_id}，不是 {pp.project_id}")

    # 文件级批准仍需逐字节复核：发布计划是从 staging 算出来的，
    # 但「文件被批过」和「这份计划里的字节被批过」必须同时成立。
    if meta.artifact_approval and not meta.bytes_from:
        artifact = approval_store.load(meta.artifact_approval)
        current = {}
        for record in artifact.files:
            path = pp.vault / record.staged
            current[record.target] = sha256_file(path) if path.exists() else ""
        status, stale = approval_store.verify(artifact, current)
        if status != "VALID":
            raise ApprovalError(
                "文件批准已失效（这些文件在批准后被改过）："
                + "、".join(stale)
                + "。需作者重新批准"
            )
    return approval.approval_id


def _validate_candidate(pp: ProjectPaths, snapshot: Path, plan: ReleasePlan, files_dir: Path) -> None:
    """第 6 步：路径、字节、解析、正式区归属。

    PRD 要求这里还校验 Schema、ID、引用、依赖。依赖表本版为空（T07 之后才有），
    如实只做能做的四样，并把「没做的」写在 known_risks 里，不假装做过。
    """
    for change in plan.changes:
        target = pp.at(change.path)
        if not pp.is_control_path(target):
            raise SecurityError(f"发布目标不在正式区：{change.path}")
        staged = files_dir / change.path
        if change.op != "delete" and not staged.exists():
            raise ConflictError(f"候选区缺少 {change.path} 的字节")
        if change.op != "delete" and sha256_file(staged) != change.content_hash:
            raise ConflictError(f"{change.path} 的字节与计划不符")
        if change.path.lower().endswith((".md", ".markdown")):
            try:
                md.parse_bytes(staged.read_bytes(), change.path)
            except ParseError as exc:
                raise ParseError(f"{change.path} 解析失败：{exc}") from exc
    for rel in plan.removals:
        pp.at(rel)


def _check_file_set(pp: ProjectPaths, snapshot: Path, plan: ReleasePlan) -> list[ManifestFile]:
    """第 7 步：最终文件集与计划一致。路径比的是 Vault 相对（快照的坐标系）。"""
    files = scan_snapshot(snapshot)
    present = {item.path: item for item in files}
    for change in plan.changes:
        rel = vault_rel(pp, change.path)
        if change.op == "delete":
            if rel in present:
                raise ConflictError(f"{change.path} 应删除但仍在候选区")
            continue
        item = present.get(rel)
        if item is None:
            raise ConflictError(f"候选区缺少 {change.path}（应在 {rel}）")
        if item.sha256 != change.content_hash:
            raise ConflictError(
                f"{change.path} 在候选区里的哈希与计划不符"
                f"（{item.sha256[:12]}… ≠ {change.content_hash[:12]}…）"
            )
    for rel in plan.removals:
        if vault_rel(pp, rel) in present:
            raise ConflictError(f"{rel} 应删除但仍在候选区")
    return files


def _build_candidate_index(
    snapshot: Path,
    revision: str,
    index_dir: Path,
    build_id: str,
    skip_dirs: tuple[str, ...],
    *,
    reuse: bool,
):
    db = index_dir / "builds" / f"{build_id}.sqlite"
    if db.exists():
        # 上一次尝试已经建好了这一份（发布号不变，索引号也不变）：
        # 复用，不重建。重建会得到不同字节，清单里的 index_bytes_hash 就对不上了。
        return db, True
    if reuse:
        raise ConflictError(
            f"回退要复用旧索引 {build_id}，但它在 {db} 不存在。"
            "旧索引被清理过，这次回退无法复用，请改为重新构建（去掉 reuse）"
        )
    source = WorktreeSource(snapshot, revision=revision, skip_dirs=skip_dirs)
    result = build_index(source, index_dir, build_id=build_id)
    return result.db_path, False


def _integrity_check(db: Path) -> None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("pragma integrity_check").fetchone()
    finally:
        conn.close()
    verdict = str(row[0]) if row else ""
    if verdict.lower() != "ok":
        raise ConflictError(f"索引完整性检查失败：{db}（{verdict}）")


def _assert_rollback_content(
    store: ReleaseStore, manifest: ReleaseManifest, target_release: str
) -> None:
    """回退包的内容必须逐字等于目标发布。

    PRD §18.3 写的是「复用已验证旧版 revision 与索引」。索引能整份复用——它只是内容的
    函数，同样的内容建两次得到同样的库。`revision` 复用不了：每次发布都在自己的快照里
    新提交一次，commit sha 天然不同；把旧版的 sha 写进新清单，等于指向一个在本版快照里
    根本不存在的提交，四点交叉验证会直接对不上。

    所以这里不假装复用 sha，而是直接核对**逐文件哈希**——内容确实一样，回退才是回退；
    不一样就说明这不是「以回退之名发布旧版」，而是发布了另一棵树。
    """
    target = store.load_manifest(target_release)
    mine = {item.path: item.sha256 for item in manifest.files}
    theirs = {item.path: item.sha256 for item in target.files}
    extra = sorted(set(mine) - set(theirs))
    missing = sorted(set(theirs) - set(mine))
    differ = sorted(p for p in set(mine) & set(theirs) if mine[p] != theirs[p])
    if not (extra or missing or differ):
        return

    parts = []
    if differ:
        parts.append(f"{len(differ)} 个文件内容不同（{'、'.join(differ[:5])}）")
    if missing:
        parts.append(f"缺 {len(missing)} 个（{'、'.join(missing[:5])}）")
    if extra:
        parts.append(f"多出 {len(extra)} 个目标版没有的（{'、'.join(extra[:5])}）")
    raise ConflictError(
        f"回退包与 {target_release} 的内容不一致：{'；'.join(parts)}。"
        "回退要发布的必须就是旧版那棵树。先处理这些差异（adopt 收编或另发一版），再来回退"
    )


def _write_pointer_atomic(store: ReleaseStore, pointer: ActivePointer, journal: Journal) -> Path:
    """第 16–18 步：同目录临时文件 → fsync → 原子替换。

    不直接用 `storage.write_atomic` 是为了让日志里的三步都是真的：
    它内部把「写临时文件」和「替换」做在一起，日志会写成 16、18 两步都完成，
    而 17（持久化）根本没发生过。
    """
    path = store.active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{ACTIVE_NAME}.novel-tmp"
    payload = json.dumps(pointer.as_dict(), ensure_ascii=False, indent=1).encode("utf-8")

    _maybe_fault("before:16")
    with tmp.open("wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    _mark(store, journal, 16, "done", f"临时指针 {tmp.name}")

    _maybe_fault("before:17")
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        _mark(store, journal, 17, "done", "目录已 fsync")
    except OSError as exc:  # 某些文件系统不支持对目录 fsync
        _mark(store, journal, 17, "done", f"目录 fsync 不支持，已跳过：{exc}")

    _maybe_fault("before:18")
    os.replace(tmp, path)
    return path


def _mirror_to_vault(
    pp: ProjectPaths,
    plan: ReleasePlan,
    files_dir: Path,
    *,
    label: str,
    stamp: str,
) -> tuple[list[str], list[str], list[str]]:
    """第 21 步：把已切换的内容镜像回 Vault（供 Obsidian 阅读）。

    镜像失败**不撤销发布**（PRD §17.4）：正式查询读的是 active_release 与它的索引，
    不读这个镜像。失败只写进回执告警，让人知道 Obsidian 里可能还是旧的。
    """
    written: list[str] = []
    archived: list[str] = []
    warnings: list[str] = []

    def archive(target: Path, rel: str) -> None:
        if not target.exists():
            return
        dest = pp.archive / "publish_archive" / label / stamp / rel
        copy_verified(target, dest)
        archived.append(pp.rel_to_project(dest))

    for change in plan.changes:
        target = pp.at(change.path)
        try:
            if change.op == "delete":
                archive(target, change.path)
                if target.exists():
                    target.unlink()
                continue
            source = files_dir / change.path
            # 已经是目标内容就跳过：崩溃后重跑镜像不该把新内容再「归档」一遍。
            if target.exists() and sha256_file(target) == change.content_hash:
                continue
            archive(target, change.path)
            write_atomic(target, source.read_bytes())
            written.append(change.path)
        except OSError as exc:
            warnings.append(f"镜像 {change.path} 失败：{exc}")
            break

    for rel in plan.removals:
        target = pp.at(rel)
        try:
            archive(target, rel)
            if target.exists():
                target.unlink()
        except OSError as exc:
            warnings.append(f"删除 {rel} 失败：{exc}")

    return written, archived, warnings


def _write_index_pointer(store: ReleaseStore, build_id: str, manifest: ReleaseManifest) -> None:
    """把索引指针指到本次发布的索引。

    这一步**不是**正式切换。正式版本的权威只有 `releases/active.json`；
    索引指针是给 `search` 用的加速约定。顺序上先切正式指针再切它，
    中途崩了也只是搜索慢一拍，不会让谁读到「新索引 + 旧发布」的正式结论。
    """
    pointer = store.runtime / "index" / "active.json"
    payload = {
        "build_id": build_id,
        "db": f"builds/{build_id}.sqlite",
        "canon_revision": manifest.canon_revision,
        "source_kind": "release",
        "release_id": manifest.release_id,
        "artifact_count": len(manifest.files),
    }
    write_atomic(pointer, json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))


def execute(
    pp: ProjectPaths,
    store: ReleaseStore,
    release_id: str,
    *,
    actor: str,
    approval_store,
    issue_store=None,
    change_store=None,
    confirm_canon: bool = False,
    skip_dirs: tuple[str, ...] | None = None,
    index_dir: Path | None = None,
    task=None,
) -> ExecutionResult:
    plan = store.load_plan(release_id)
    meta = store.load_meta(release_id)
    journal = store.load_journal(release_id)
    resumed = journal is not None

    if journal is not None and journal.state in ("ABORTED", "SUPERSEDED"):
        raise ConflictError(f"{release_id} 的事务已经是 {journal.state}，不能再执行")
    if journal is not None and journal.release_plan_hash and journal.release_plan_hash != plan.hash:
        raise ConflictError(
            f"{release_id} 的计划在事务开始后被替换过（计划哈希变了），拒绝按旧日志续做"
        )

    active = store.read_active()
    if active is not None and active.release_id == release_id:
        # 已经切过去了。可能是崩在「切换后、回执前」，也可能只是重复执行。
        return _finish_already_active(pp, store, plan, meta, actor, index_dir=index_dir)

    if journal is None:
        journal = Journal(
            txn_id=store.txn_id_for(release_id),
            release_id=release_id,
            base_release=plan.base_release,
            release_plan_hash=plan.hash,
            state="PREPARED",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        _maybe_fault("before:1")
        _mark(store, journal, 1, "done", f"计划 {plan.hash[:12]}…，变更 {len(plan.changes)} 条")
    else:
        _maybe_fault(f"before:{max(journal.last_done() + 1, 2)}")

    done = set(journal.done_steps)

    # ---- 2 基线 ----
    if 2 not in done:
        current_base = active.release_id if active else None
        if (plan.base_release or None) != current_base:
            raise ConflictError(
                f"基线不符：计划基于 {plan.base_release or '空'}，当前正式版是 {current_base or '空'}。"
                "有人在准备之后发布过，请重新准备计划"
            )
        _mark(store, journal, 2, "done", f"base={plan.base_release or '（首次发布）'}")

    # ---- 3 计划与批准 ----
    approval_id = ""
    if 3 not in done:
        approval_id = _verify_approval(pp, plan, meta, approval_store)
        if task is not None:
            check_gates(pp, task, StagingArea(pp, task), issue_store=issue_store, change_store=change_store)
            check_approval(pp, task, approval_store.load(meta.artifact_approval), StagingArea(pp, task))
        canon_targets = [c.path for c in plan.changes if c.path.startswith(CANON_PREFIX + "/")]
        if canon_targets and not (confirm_canon or meta.confirm_canon):
            raise SecurityError(
                f"本次发布包含 Canon 写入：{canon_targets}；需要作者额外确认 --confirm-canon"
            )
        _mark(
            store,
            journal,
            3,
            "done",
            f"批准 {approval_id}" + (f"，Canon {len(canon_targets)} 个" if canon_targets else ""),
        )
        journal.state = "VALIDATED"
        store.write_journal(journal)
    else:
        records = approval_store.for_target("release", release_id)
        approval_id = records[-1].approval_id if records else ""

    snapshot = store.snapshot_dir(release_id)
    files_dir = store.files_dir(release_id)

    # ---- 4 隔离工作区 ----
    if 4 not in done:
        _maybe_fault("before:4")
        files_dir.mkdir(parents=True, exist_ok=True)
        if meta.bytes_from:
            loader = release_change_loader(pp, store.snapshot_dir(meta.bytes_from))
        else:
            # staging 路径来自计划自身（meta），不依赖任务记录还在不在。
            loader = source_change_loader(pp, dict(meta.staged_paths or {}))
        write_change_bytes(pp, plan, files_dir, loader=loader)
        if not snapshot.exists() or not any(snapshot.iterdir()):
            copy_tree(pp.vault, snapshot, skip_parts(pp))
        _mark(store, journal, 4, "done", f"快照 {snapshot.name}")

    # ---- 5 应用精确字节 ----
    if 5 not in done:
        _maybe_fault("before:5")
        written = apply_changes(pp, snapshot, plan, files_dir)
        _mark(store, journal, 5, "done", f"写入 {len(written)} 个文件")

    # ---- 6 校验 ----
    if 6 not in done:
        _maybe_fault("before:6")
        _validate_candidate(pp, snapshot, plan, files_dir)
        _mark(store, journal, 6, "done", "路径/字节/解析/正式区")

    # ---- 7 文件集一致 ----
    if 7 not in done:
        _maybe_fault("before:7")
        files = _check_file_set(pp, snapshot, plan)
        _mark(store, journal, 7, "done", f"{len(files)} 个文件")
    else:
        files = scan_snapshot(snapshot)

    # ---- 8 候选提交 ----
    revision_path = store.revision_path(release_id)
    if revision_path.exists():
        revision = revision_path.read_text(encoding="utf-8").strip()
        revision_kind = "git" if not revision.startswith("local:") else "digest"
    else:
        revision, revision_kind = "", ""
    if 8 not in done or not revision:
        _maybe_fault("before:8")
        built = build_snapshot(
            pp,
            snapshot,
            plan=plan,
            files_dir=files_dir,
            release_id=release_id,
            plan_hash=plan.hash,
        )
        revision, revision_kind = built.revision, built.revision_kind
        write_atomic(revision_path, revision.encode("utf-8"))
        detail = f"{revision_kind}:{revision[:12]}"
        if built.warnings:
            detail += "；" + "；".join(built.warnings)
        _mark(store, journal, 8, "done", detail)

    resolved_index_dir = Path(index_dir) if index_dir is not None else store.runtime / "index"
    index_build_id = (
        store.index_id_for(meta.reuse_index_of) if meta.reuse_index_of else store.index_id_for(release_id)
    )
    db_path = resolved_index_dir / "builds" / f"{index_build_id}.sqlite"

    # ---- 9 建索引 ----
    if 9 not in done:
        _maybe_fault("before:9")
        resolved_skip = (
            tuple(skip_dirs)
            if skip_dirs is not None
            else skip_dirs_from_layout({"vault_archive_dir": pp.dir_name("archive_root")})
        )
        db_path, reused = _build_candidate_index(
            snapshot,
            revision,
            resolved_index_dir,
            index_build_id,
            resolved_skip,
            reuse=bool(meta.reuse_index_of),
        )
        _mark(store, journal, 9, "done", f"{index_build_id}{'（复用）' if reused else ''}")

    # ---- 10 完整性 ----
    if 10 not in done:
        _maybe_fault("before:10")
        _integrity_check(db_path)
        _mark(store, journal, 10, "done", "integrity_check ok")

    # ---- 11 索引哈希 ----
    if 11 not in done:
        _maybe_fault("before:11")
        index_hash = sha256_file(db_path)
        _mark(store, journal, 11, "done", index_hash[:16] + "…")
    else:
        index_hash = sha256_file(db_path)

    # ---- 12 清单 ----
    manifest_path = store.manifest_path(release_id)
    if manifest_path.exists():
        manifest = store.load_manifest(release_id)
        if 12 not in done:
            journal.state = "READY_TO_ACTIVATE"
            _mark(store, journal, 12, "done", manifest.hash[:16] + "…（复用已写入的清单）")
    else:
        _maybe_fault("before:12")
        manifest = ReleaseManifest(
            release_id=release_id,
            base_release=plan.base_release,
            release_plan_hash=plan.hash,
            canon_revision=revision,
            index_build_id=index_build_id,
            index_bytes_hash=index_hash,
            files=files,
            approval_refs=[approval_id],
            tool_version=f"novel-system/{__version__}",
            schema_version=__version__,
            created_at=utc_now(),
        )
        store.write_manifest(manifest)
        journal.state = "READY_TO_ACTIVATE"
        _mark(store, journal, 12, "done", manifest.hash[:16] + "…")

    # ---- 13 切换前校验 ----
    if meta.reuse_index_of:
        # 每一次都核对（哪怕第 12 步是复用磁盘清单）：回退是唯一一类
        # 「内容不由本次计划单独决定」的发布，多核一次不亏。
        _assert_rollback_content(store, manifest, meta.reuse_index_of)

    if 13 not in done:
        _maybe_fault("before:13")
        if store.manifest_hash(release_id) != manifest.hash:
            raise ConflictError("磁盘上的 manifest 与内存中的哈希不一致，拒绝切换")
        for item in manifest.files:
            path = snapshot / item.path
            if not path.exists() or sha256_file(path) != item.sha256:
                raise ConflictError(f"候选区 {item.path} 与清单不符，拒绝切换")
        _mark(store, journal, 13, "done", f"{len(manifest.files)} 个文件核对通过")

    # ---- 14–20 切换 ----
    _maybe_fault("before:14")
    lock = PublishLock(store.lock_path(), task=meta.task_id, release_id=release_id, actor=actor)
    lock.acquire()
    _mark(store, journal, 14, "done", f"pid={os.getpid()}")
    try:
        _maybe_fault("before:15")
        current = store.read_active()
        if (current.release_id if current else None) != (plan.base_release or None):
            raise ConflictError(
                "拿到锁后重新比对，正式版已经变了"
                f"（{current.release_id if current else '空'} ≠ {plan.base_release or '空'}）。"
                "发布已中止，正式版未被修改"
            )
        _mark(store, journal, 15, "done", f"base={plan.base_release or '（首次）'}")

        pointer = ActivePointer(
            release_id=release_id,
            canon_revision=revision,
            index_build_id=index_build_id,
            manifest_sha256=manifest.hash,
            activated_at=utc_now(),
        )
        _write_pointer_atomic(store, pointer, journal)
        journal.state = "ACTIVE"
        _mark(
            store,
            journal,
            18,
            "done",
            f"active={release_id}（{revision_kind}:{revision[:12]}）",
        )
        _maybe_fault("after:18")

        _maybe_fault("before:19")
        receipt = Receipt(
            receipt_id=store.next_receipt_id(),
            release_id=release_id,
            manifest_hash=manifest.hash,
            canon_revision=revision,
            index_build_id=index_build_id,
            created_at=utc_now(),
            base_release=plan.base_release,
            active_since=pointer.activated_at,
            repaired=False,
            warnings=_receipt_warnings(meta, plan),
        )
        store.write_receipt(receipt)
        _mark(store, journal, 19, "done", receipt.receipt_id)
    finally:
        lock.release()
        _mark(store, journal, 20, "done", "已释放")

    # ---- 21 镜像 ----
    _maybe_fault("before:21")
    label = meta.task_id or release_id
    stamp = utc_now().replace(":", "").replace("-", "")[:15]
    written, archived, warnings = _mirror_to_vault(pp, plan, files_dir, label=label, stamp=stamp)
    detail = f"写入 {len(written)}，归档 {len(archived)}"
    if warnings:
        detail += "；" + "；".join(warnings)
    _mark(store, journal, 21, "done", detail)
    if warnings:
        receipt.warnings.extend(warnings)
        store.write_receipt(receipt)

    # ---- 22 备份与依赖 ----
    _maybe_fault("before:22")
    _mark(store, journal, 22, "done", "建议：novel backup；依赖表待 T07/T10 生成")

    try:
        _write_index_pointer(store, index_build_id, manifest)
    except OSError as exc:
        journal.set_step(22, "done", at=utc_now(), detail=f"索引指针写入失败：{exc}")
        store.write_journal(journal)

    return ExecutionResult(
        release_id=release_id,
        state=journal.state,
        receipt_id=receipt.receipt_id,
        canon_revision=revision,
        index_build_id=index_build_id,
        written=written,
        archived=archived,
        warnings=warnings,
        resumed=resumed,
        steps_done=journal.done_steps,
        task_id=meta.task_id,
    )


def _finish_already_active(
    pp: ProjectPaths,
    store: ReleaseStore,
    plan: ReleasePlan,
    meta: PlanMeta,
    actor: str,
    *,
    index_dir: Path | None,
) -> ExecutionResult:
    """已经切到这一版，只补回执与镜像。**不写指针、不动清单。**"""
    manifest = store.load_manifest(plan.release_id)
    journal = store.load_journal(plan.release_id)
    if journal is None:
        journal = Journal(
            txn_id=store.txn_id_for(plan.release_id),
            release_id=plan.release_id,
            base_release=plan.base_release,
            release_plan_hash=plan.hash,
            state="ACTIVE",
            created_at=utc_now(),
        )
    journal.state = "ACTIVE"
    for step in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18):
        if step not in journal.done_steps:
            journal.set_step(step, "skipped", at=utc_now(), detail="指针已指向本版，按已完成处理")

    receipt = store.receipt_for_release(plan.release_id)
    if receipt is None:
        receipt = Receipt(
            receipt_id=store.next_receipt_id(),
            release_id=plan.release_id,
            manifest_hash=manifest.hash,
            canon_revision=manifest.canon_revision,
            index_build_id=manifest.index_build_id,
            created_at=utc_now(),
            base_release=plan.base_release,
            active_since=store.read_active().activated_at if store.read_active() else "",
            repaired=True,
            warnings=["切换后未写回执即中断，本回执由恢复程序补写"],
        )
        store.write_receipt(receipt)
    _mark(store, journal, 19, "done", f"{receipt.receipt_id}（补写）")

    files_dir = store.files_dir(plan.release_id)
    written, archived, warnings = _mirror_to_vault(
        pp,
        plan,
        files_dir,
        label=meta.task_id or plan.release_id,
        stamp=utc_now().replace(":", "").replace("-", "")[:15],
    )
    _mark(store, journal, 21, "done", f"镜像 {len(written)}，归档 {len(archived)}")
    if warnings:
        receipt.warnings.extend(warnings)
        store.write_receipt(receipt)
    _mark(store, journal, 20, "done", "无需持锁")
    _mark(store, journal, 22, "done", "恢复模式：未触发备份")

    return ExecutionResult(
        release_id=plan.release_id,
        state="ACTIVE",
        receipt_id=receipt.receipt_id,
        canon_revision=manifest.canon_revision,
        index_build_id=manifest.index_build_id,
        written=written,
        archived=archived,
        warnings=warnings + receipt.warnings,
        resumed=True,
        already_active=True,
        steps_done=journal.done_steps,
        task_id=meta.task_id,
    )


def resume(
    pp: ProjectPaths,
    store: ReleaseStore,
    release_id: str,
    *,
    actor: str,
    approval_store,
    issue_store=None,
    change_store=None,
    confirm_canon: bool = False,
    skip_dirs: tuple[str, ...] | None = None,
    index_dir: Path | None = None,
) -> ExecutionResult:
    """从中断处继续。**不重新准备计划**——计划与批准都还是原来那份。"""
    meta = store.load_meta(release_id)
    task = None
    if meta.task_id:
        from ..tasks import TaskStore

        try:
            task = TaskStore(store.runtime).load(meta.task_id)
        except Exception:  # 任务记录不在也要能把发布续完
            task = None
    return execute(
        pp,
        store,
        release_id,
        actor=actor,
        approval_store=approval_store,
        issue_store=issue_store,
        change_store=change_store,
        confirm_canon=confirm_canon,
        skip_dirs=skip_dirs,
        index_dir=index_dir,
        task=task,
    )


def abort(store: ReleaseStore, release_id: str, *, actor: str, note: str = "") -> Journal:
    """中止一次未切换的发布。已切换的必须走 rollback，不能靠 abort 假装撤销。"""
    journal = store.load_journal(release_id)
    if journal is None:
        raise UsageError(f"{release_id} 还没有开始执行，无需中止")
    active = store.read_active()
    if active is not None and active.release_id == release_id:
        raise ConflictError(
            f"{release_id} 已经是当前正式版。要撤回请用 `novel release rollback --to <上一版>`，"
            "它会生成一次新的受审计发布，而不是把指针偷偷拨回去"
        )
    if journal.state == "ABORTED":
        raise ConflictError(f"{release_id} 已经中止过")
    journal.state = "ABORTED"
    journal.updated_at = utc_now()
    journal.set_step(
        journal.last_done() + 1 if journal.last_done() < 22 else 22,
        "skipped",
        at=utc_now(),
        detail=f"中止（{actor}）：{note or '无说明'}",
    )
    store.write_journal(journal)
    return journal


# --------------------------------------------------------------------------
# 状态与修复（PRD §18.2：交叉验证，不「取最新文件」）
# --------------------------------------------------------------------------

def status(store: ReleaseStore, *, deep: bool = False) -> dict:
    """交叉验证指针 / 清单 / 索引 / 日志 / 回执。

    `verdict` 是给人和脚本共用的判词：

    | 判词 | 含义 | 该怎么办 |
    |---|---|---|
    | `NO_RELEASE` | 从未发布过 | 正常；也可能有待续做的事务 |
    | `OK` | 五项互相吻合 | —— |
    | `RECEIPT_MISSING` | 指针与清单对得上，但没回执 | `release repair` 补写 |
    | `MANIFEST_MISSING` | 指针指着一份不存在的清单 | 恢复模式，人工判断 |
    | `MANIFEST_HASH_MISMATCH` | 清单被改过 | 恢复模式，人工判断 |
    | `INDEX_MISSING` | 索引不在或哈希不符 | 重建该 release 的索引或回退 |
    | `POINTER_CORRUPT` | 指针读不出来 | 恢复模式，**不自动猜** |
    """
    report: dict = {
        "verdict": "NO_RELEASE",
        "detail": "",
        "pointer": None,
        "manifest": None,
        "receipt": None,
        "journal": None,
        "pending": [],
        "problems": [],
        "checked_files": 0,
    }
    try:
        active = store.read_active()
    except ConflictError as exc:
        report["verdict"] = "POINTER_CORRUPT"
        report["detail"] = str(exc)
        report["problems"].append("正式指针损坏；恢复模式，不自动猜测当前正式版本")
        return report

    pending = []
    for release_id in store.list_releases():
        journal = store.load_journal(release_id)
        if journal is None:
            continue
        if journal.state in ("ACTIVE", "ABORTED", "SUPERSEDED"):
            continue
        pending.append({"release_id": release_id, "state": journal.state, "last_done": journal.last_done()})
    report["pending"] = pending

    if active is None:
        report["detail"] = "尚未发布任何版本"
        if pending:
            report["detail"] += f"；有 {len(pending)} 个未完成的事务（release status --all 查看）"
        return report

    report["pointer"] = active.as_dict()
    manifest_path = store.manifest_path(active.release_id)
    if not manifest_path.exists():
        report["verdict"] = "MANIFEST_MISSING"
        report["detail"] = f"指针指向 {active.release_id}，但清单不存在"
        return report

    manifest = store.load_manifest(active.release_id)
    report["manifest"] = manifest.as_dict()

    actual_hash = store.manifest_hash(active.release_id)
    if actual_hash != active.manifest_sha256:
        report["verdict"] = "MANIFEST_HASH_MISMATCH"
        report["detail"] = (
            f"指针记的清单哈希 {active.manifest_sha256[:12]}…，磁盘上是 {actual_hash[:12]}…"
        )
        report["problems"].append("清单与指针不符：清单被改过，或指针被改过。两者都不能猜")
        return report
    if manifest.release_id != active.release_id:
        report["verdict"] = "MANIFEST_HASH_MISMATCH"
        report["detail"] = f"清单属于 {manifest.release_id}，指针写着 {active.release_id}"
        return report

    db = store.runtime / "index" / "builds" / f"{manifest.index_build_id}.sqlite"
    if not db.exists():
        report["verdict"] = "INDEX_MISSING"
        report["detail"] = f"索引 {manifest.index_build_id} 不存在：{db}"
        report["problems"].append("正式索引缺失：依赖它的查询应被阻断，重建或回退完整发布")
        return report
    if sha256_file(db) != manifest.index_bytes_hash:
        report["verdict"] = "INDEX_MISSING"
        report["detail"] = f"索引 {manifest.index_build_id} 的字节哈希与清单不符"
        report["problems"].append("正式索引损坏：依赖它的查询应被阻断")
        return report

    receipt = store.receipt_for_release(active.release_id)
    report["receipt"] = receipt.as_dict() if receipt else None
    journal = store.load_journal(active.release_id)
    report["journal"] = journal.as_dict() if journal else None

    if receipt is None:
        report["verdict"] = "RECEIPT_MISSING"
        report["detail"] = "切换已完成但缺回执；可用 release repair 补写"
        return report

    if deep:
        snapshot = store.snapshot_dir(active.release_id)
        missing = []
        for item in manifest.files:
            path = snapshot / item.path
            if not path.exists() or sha256_file(path) != item.sha256:
                missing.append(item.path)
            report["checked_files"] += 1
        if missing:
            report["verdict"] = "INDEX_MISSING"
            report["detail"] = f"候选区有 {len(missing)} 个文件与清单不符"
            report["problems"].extend(missing[:20])
            return report

    report["verdict"] = "OK"
    report["detail"] = f"{active.release_id} 生效中（{manifest.canon_revision[:12]}）"
    return report


def repair_receipt(store: ReleaseStore, release_id: str, *, actor: str) -> Receipt:
    """补写回执（PRD §18.2「切换后、回执前」）。

    只在**指针确实指向这一版、清单哈希对得上**时补。其余情况一律拒绝：
    补一张对不上账的回执，比没有回执更糟——它会让恢复程序以为一切正常。
    """
    active = store.read_active()
    if active is None or active.release_id != release_id:
        raise ConflictError(
            f"{release_id} 不是当前正式版（当前：{active.release_id if active else '无'}），不能补回执"
        )
    manifest = store.load_manifest(release_id)
    if store.manifest_hash(release_id) != active.manifest_sha256:
        raise ConflictError("清单哈希与指针不符，先查清是谁改的，再来补回执")
    existing = store.receipt_for_release(release_id)
    if existing is not None:
        return existing
    receipt = Receipt(
        receipt_id=store.next_receipt_id(),
        release_id=release_id,
        manifest_hash=manifest.hash,
        canon_revision=manifest.canon_revision,
        index_build_id=manifest.index_build_id,
        created_at=utc_now(),
        base_release=manifest.base_release,
        active_since=active.activated_at,
        repaired=True,
        warnings=[f"回执由 {actor} 于恢复流程补写（切换成功但当时未写回执）"],
    )
    store.write_receipt(receipt)
    journal = store.load_journal(release_id)
    if journal is not None:
        _mark(store, journal, 19, "done", f"{receipt.receipt_id}（补写）")
    return receipt
