"""只读命令：doctor / status / validate / diff。"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

from .. import config as cfg_mod
from ..approvals import ApprovalStore
from ..audit import OperationLog
from ..baseline import AuthorBaseline
from ..backup import BackupStore
from ..errors import UsageError
from ..paths import CANON_KINDS, ProjectPaths
from ..staging import StagingArea
from ..storage import human_size, sha256_file
from ..tasks import TASK_STATUSES, TaskStore
from ._common import fail, heading, info, ok, project, read_frontmatter, task_store, warn


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def cmd_doctor(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    problems = 0
    print("配置")
    for line in cfg.describe():
        print(f"  {line}")

    heading("运行时环境")
    if sys.version_info >= (3, 11):
        ok(f"Python {sys.version.split()[0]}")
    else:
        fail(f"Python {sys.version.split()[0]}，需要 >= 3.11")
        problems += 1
    try:
        import yaml  # noqa: F401

        ok("PyYAML 可用")
    except ImportError:
        warn("PyYAML 未安装，使用内置简易解析器（pip install -e \".[yaml]\" 可启用）")

    heading("路径")
    vault = cfg.author_vault
    if vault is None:
        warn("author_vault 未配置：只读命令仍可用，其余命令需要它")
    elif not vault.exists():
        fail(f"author_vault 不存在：{vault}")
        problems += 1
    else:
        ok(f"author_vault 存在：{vault}")

    runtime = cfg.runtime
    if runtime is None:
        warn("runtime 未配置：索引、任务、批准、日志、备份都无处落盘")
    else:
        if vault is not None:
            try:
                cfg_mod.ensure_inside(runtime, vault)
                fail(f"runtime 位于 Vault 内：{runtime}（应独立于 Vault）")
                problems += 1
            except cfg_mod.ConfigError:
                ok(f"runtime 在 Vault 之外：{runtime}")
        if runtime.exists():
            ok(f"runtime 存在：{runtime}")
            for sub in ("index", "logs", "tasks", "approvals", "backups"):
                (runtime / sub).mkdir(parents=True, exist_ok=True)
            ok("runtime 子目录就绪（index/logs/tasks/approvals/backups）")
        else:
            warn(f"runtime 尚未创建：{runtime}（init-author 会创建）")

    if vault is not None and runtime is not None:
        heading("隔离检查")
        repo = cfg.public_repo
        try:
            cfg_mod.ensure_inside(vault, repo)
            fail(f"Vault 位于公仓内：{vault}（V4.0 §0.2 禁止）")
            problems += 1
        except cfg_mod.ConfigError:
            pass
        try:
            cfg_mod.ensure_inside(repo, vault)
            fail(f"公仓位于 Vault 内：{repo}（V4.0 §0.2 禁止）")
            problems += 1
        except cfg_mod.ConfigError:
            pass
        if (vault / ".git").exists() and (repo / ".git").exists():
            ok("Vault 与公仓各有独立 .git")
        if problems == 0:
            ok("公仓与 Vault 互不包含")

    heading("隐私开关")
    expect = {
        "private_remote_allowed": False,
        "public_repo_may_read_vault": False,
        "cross_project_access": False,
        "agent_canon_write": False,
    }
    for key, want in expect.items():
        got = cfg.privacy.get(key, want)
        if got == want:
            ok(f"{key} = {got}")
        else:
            fail(f"{key} = {got}，应为 {want}")
            problems += 1

    print()
    if problems:
        print(f"doctor: {problems} 项未通过")
        return 1
    print("doctor: 全部通过")
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    print("系统状态")
    if cfg.author_vault is None:
        print("  Vault: (未配置)")
    else:
        print(f"  Vault: {cfg.author_vault}")
        projects = cfg.project_candidates()
        print(f"  项目数: {len(projects)}")
        for pid, root in projects:
            md = sum(1 for _ in root.rglob("*.md")) if root.exists() else 0
            print(f"    - {pid}  ({root.name}, {md} 份 Markdown)")

    print(f"  Runtime: {cfg.runtime or '(未配置)'}")
    if cfg.runtime:
        index = cfg.runtime / "index" / "index.json"
        if index.exists():
            print(f"    索引: 存在，{human_size(index.stat().st_size)}")
        else:
            print("    索引: 尚未生成")

    if cfg.runtime is None:
        return 0

    store = TaskStore(cfg.runtime)
    tasks = store.list()
    counting = {status: 0 for status in TASK_STATUSES}
    for task in tasks:
        counting[task.status] = counting.get(task.status, 0) + 1
    open_tasks = [t for t in tasks if t.status not in ("done", "cancelled")]

    heading("任务")
    print(f"  总数: {len(tasks)}，未结: {len(open_tasks)}")
    for task in open_tasks[:10]:
        print(f"    {task.task_id}  {task.task_type:<14} {task.status:<16} {task.assigned_to or '-'}")
    if len(open_tasks) > 10:
        print(f"    ……另 {len(open_tasks) - 10} 条")

    approvals = ApprovalStore(cfg.runtime)
    awaiting = [t for t in open_tasks if t.approval_state == "pending_author"]
    if awaiting:
        heading("等待作者批准")
        for task in awaiting:
            print(f"    {task.task_id}  {task.task_type}（运行 novel diff --task {task.task_id} 查看变更）")

    backups = BackupStore(cfg.runtime).list()
    if backups:
        latest = backups[-1]
        heading("备份")
        print(f"  共 {len(backups)} 份，最近: {latest.get('backup_id')}（{latest.get('files')} 文件，verified={latest.get('verified')}）")

    ops = OperationLog(cfg.runtime).tail(5)
    if ops:
        heading("最近操作")
        for op in reversed(ops):
            print(f"  {op.get('at', '')}  {op.get('actor', ''):<16} {op.get('command', ''):<12} {op.get('status', '')}")
    return 0


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

WIKILINK = re.compile(r"\[\[([^\]\|]+?)(?:\\?\|[^\]]*)?\]\]")


def _resolve_link(target: str, pp: ProjectPaths, stems: dict[str, list[Path]]) -> bool:
    raw = target.strip()
    # `[[文件#标题]]` 只校验文件部分；`[[#标题]]` 是页内跳转，不算断链
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    if not raw:
        return True
    if raw.startswith("^"):
        return True
    # 正文里常见的省略写法，不是链接意图
    if raw in ("...", "…") or "..." in raw:
        return True
    candidates = [raw]
    if not Path(raw).suffix:
        candidates.append(raw + ".md")
    for cand in candidates:
        for base in (pp.root, pp.vault):
            if (base / cand).exists():
                return True
    stem = Path(raw).stem
    return stem in stems


def _link_index(pp: ProjectPaths) -> dict[str, list[Path]]:
    """按文件名索引全 Vault 的文件。

    Obsidian 的 `[[名字]]` 是「全库按文件名唯一匹配」，不是「同目录」；
    只扫项目内会把 `[[世界地图]]`（在 00_System/Dashboards/Maps/）误判成断链。
    双链也可以指向 .canvas / .base，所以不按扩展名过滤。
    """
    index: dict[str, list[Path]] = {}
    for path in pp.vault.rglob("*"):
        rel = path.relative_to(pp.vault).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        if path.is_file():
            index.setdefault(path.stem, []).append(path)
    return index


def cmd_validate(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    strict = getattr(args, "strict", False)
    problems = 0
    warnings = 0
    vault = cfg.require_vault()

    heading("Vault 结构")
    for name in (cfg.layout.get("vault_system_dir", "00_System"), cfg.layout.get("projects_dir", "10_Projects")):
        if (vault / name).is_dir():
            ok(f"{name}/ 存在")
        else:
            fail(f"{name}/ 缺失")
            problems += 1

    heading("项目元数据")
    projects_dir = vault / cfg.layout.get("projects_dir", "10_Projects")
    seen: dict[str, Path] = {}
    if projects_dir.is_dir():
        for root in sorted(p for p in projects_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            meta_path = root / "00_Project" / "project.yaml"
            if not meta_path.exists():
                fail(f"{root.name}: 缺 00_Project/project.yaml")
                problems += 1
                continue
            data = cfg_mod.read_yaml(meta_path)
            pid = str(data.get("project_id") or "")
            if not pid:
                fail(f"{root.name}: project_id 为空")
                problems += 1
            elif pid in seen:
                fail(f"project_id 重复：{pid}（{seen[pid].name} 与 {root.name}）")
                problems += 1
            else:
                seen[pid] = root
                ok(f"{root.name}: project_id={pid}")
            for sub in cfg.layout.get("project_subdirs", []):
                if not (root / sub).exists():
                    warn(f"{root.name}: 缺子目录 {sub}/")
                    warnings += 1
    else:
        warn("尚无项目目录")

    heading("路径越界")
    boundary_bad = 0
    for root in [vault] + ([projects_dir] if projects_dir.exists() else []):
        try:
            cfg_mod.ensure_inside(root, vault)
        except cfg_mod.ConfigError as exc:
            fail(str(exc))
            boundary_bad += 1
    problems += boundary_bad
    if not boundary_bad:
        ok("所有路径均在 Vault 内")

    # 逐项目校验实体与链接
    for pid, root in seen.items():
        pp = ProjectPaths.resolve(cfg, pid)
        heading(f"实体与链接 · {pid}")

        # 查重范围 = Canon 六类 + 素材库。素材虽然按分类分目录存放，ID 却是
        # 「全库连续编号，不按分类分段」（素材卡模板原话），必须跨目录一起看。
        # 只扫 Canon 正是 ASSET-0001 双占用长期没人发现的原因。
        scan_roots: list[tuple[str, Path]] = [(kind, pp.canon / kind) for kind in CANON_KINDS]
        scan_roots.append(("Story_Assets", pp.assets))

        ids: dict[str, str] = {}
        dup = 0
        misnamed: list[str] = []
        counted: dict[str, int] = {}
        for label, base in scan_roots:
            if not base.is_dir():
                continue
            n = 0
            for path in sorted(base.rglob("*.md")):
                meta = read_frontmatter(path)
                eid = str(meta.get("id") or "").strip()
                # 模板里的 `id: {{id}}` 是占位符，不是身份；判据与 rebuild-index 保持一致
                if not eid or "{{" in eid:
                    continue
                n += 1
                if eid in ids:
                    fail(f"ID 重复：{eid}（{ids[eid]} 与 {path.relative_to(pp.root)}）")
                    dup += 1
                else:
                    ids[eid] = str(path.relative_to(pp.root))
                # 目录名不可靠（World/Creatures 是合法层级），只看文件名与 id 是否错位
                file_prefix = path.stem.split("_")[0].split("-")[0]
                if "-" in path.stem and file_prefix and not eid.startswith(file_prefix + "-"):
                    misnamed.append(f"{path.relative_to(pp.root)}: id={eid} 与文件名前缀 {file_prefix}- 不符")
            if n:
                counted[label] = n
        problems += dup
        canon_n = sum(v for k, v in counted.items() if k != "Story_Assets")
        assets_n = counted.get("Story_Assets", 0)
        summary = (
            f"实体 {len(ids)} 份（Canon {canon_n} / 素材 {assets_n}），重复 {dup} 份"
            + (f"，id 与文件名错位 {len(misnamed)} 份" if misnamed else "")
        )
        (fail if dup else ok)(summary)
        for line in misnamed[:5]:
            warn(line)
        if len(misnamed) > 5:
            info(f"……另 {len(misnamed) - 5} 份 id 与文件名错位")

        stems = _link_index(pp)
        broken: list[tuple[str, str]] = []
        cross: list[tuple[str, str]] = []
        total_links = 0
        for path in pp.root.rglob("*.md"):
            rel = path.relative_to(pp.root).as_posix()
            if rel.startswith("90_Archive/"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in WIKILINK.finditer(text):
                total_links += 1
                target = match.group(1).strip()
                if target.startswith("10_Projects/"):
                    if not target.startswith(f"10_Projects/{pp.root.name}/"):
                        cross.append((rel, target))
                        continue
                if not _resolve_link(target, pp, stems):
                    broken.append((rel, target))

        if cross:
            fail(f"跨项目引用 {len(cross)} 条")
            for rel, target in cross[:5]:
                info(f"{rel} → {target}")
            problems += len(cross)
        else:
            ok("无跨项目引用")

        if broken:
            label = "FAIL" if strict else "WARN"
            print(f"  [{label}] 断链 {len(broken)} 条（共 {total_links} 条双链）")
            for rel, target in broken[:10]:
                info(f"{rel} → [[{target}]]")
            if len(broken) > 10:
                info(f"……另 {len(broken) - 10} 条")
            if strict:
                problems += len(broken)
            else:
                warnings += len(broken)
        else:
            ok(f"双链 {total_links} 条，无断链")

    print()
    if problems:
        print(f"validate: {problems} 项未通过" + (f"，{warnings} 项警告" if warnings else ""))
        return 1
    if warnings:
        print(f"validate: 通过，{warnings} 项警告（加 --strict 可把警告计为失败）")
        return 0
    print("validate: 通过")
    return 0


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------

KIND_LABEL = {
    "added": "新增",
    "modified": "改动",
    "identical": "一致",
    "conflict": "冲突",
}


def cmd_diff(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    store = task_store(cfg)
    task = store.load(args.task)
    staging = StagingArea(pp, task)
    rows = staging.diff(getattr(args, "path", None))

    if not rows:
        if staging.files():
            print(f"任务 {task.task_id} 的 staging 中没有 {args.path}，用 novel diff --task {task.task_id} 看全部")
            return 0
        print(f"任务 {task.task_id} 的 staging 为空（先运行 novel stage）")
        return 0

    print(f"任务 {task.task_id} · {pp.project_id} · 类型 {task.task_type} · staging 分栏 {staging.area}")
    print(f"产物 {len(rows)} 个")
    conflicts = 0
    for row in rows:
        print(f"\n[{KIND_LABEL.get(row['kind'], row['kind'])}] {row['target']}")
        print(f"  {row['detail']}")
        if row["kind"] == "conflict":
            conflicts += 1
        if row["kind"] == "identical":
            continue
        staged_path: Path = row["staged"]
        formal_path: Path = row["formal"]
        if not staged_path.exists():
            print("  staging 文件已丢失")
            conflicts += 1
            continue
        old = formal_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if formal_path.exists() else []
        new = staged_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old,
                new,
                fromfile=f"正式/{row['target']}" if formal_path.exists() else "/dev/null",
                tofile=f"staging/{row['target']}",
                n=3,
            )
        )
        limit = len(diff_lines) if getattr(args, "full", False) else 120
        for line in diff_lines[:limit]:
            print("  " + line.rstrip("\n"))
        if len(diff_lines) > limit:
            print(f"  ……另有 {len(diff_lines) - limit} 行（加 --full 查看全部）")

    print()
    if conflicts:
        print(f"diff: {conflicts} 个文件需要先处理（作者手改或 staging 丢失）")
        return 1
    print("diff: 无冲突。确认无误后由作者运行 novel approve")
    return 0
