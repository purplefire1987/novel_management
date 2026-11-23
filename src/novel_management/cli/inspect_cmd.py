"""只读命令：doctor / status / validate / diff。

模块名原先叫 `read.py`，而里面没有 `read` 命令——找「读文件」实现的人会绕很久。
现在按「查看类命令」命名。

注意与 `novel inspect` 区分：那条命令在 `approval_cmd.py`，看的是**候选**
（staging 里待批的产物），不是这里的环境体检与结构校验。
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

from .. import config as cfg_mod
from .. import ids as ids_mod
from ..approvals import ApprovalStore
from ..audit import OperationLog
from ..baseline import AuthorBaseline
from ..infrastructure.backup import BackupStore
from ..domain.errors import UsageError
from ..infrastructure.filesystem.paths import ProjectPaths
from ..staging import StagingArea
from ..infrastructure.filesystem.storage import human_size, sha256_file
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
        warn(
            "PyYAML 未安装：只能解析平铺 frontmatter；遇到嵌套（source_refs、aliases）"
            '会直接报错并跳过该文件（pip install -e ".[yaml]" 可启用）'
        )

    heading("SQLite")
    try:
        import sqlite3

        from ..infrastructure.database.indexing import detect_fts

        conn = sqlite3.connect(":memory:")
        try:
            available, reason = detect_fts(conn)
        finally:
            conn.close()
        if available:
            ok(f"SQLite {sqlite3.sqlite_version}，FTS5 trigram 可用（中文子串检索正常）")
        else:
            warn(
                f"SQLite {sqlite3.sqlite_version}，FTS5 trigram 不可用：{reason}"
                "——索引会退化为 LIKE 子串扫描，结果对但大库上慢"
            )
    except Exception as exc:  # 探测本身失败也算问题
        warn(f"SQLite 能力探测失败：{exc}")

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
        from ..infrastructure.database.indexing import locate
        from ..domain.errors import UsageError

        try:
            db = locate.active_db(cfg.runtime)
        except UsageError:
            db = None
        pointer = locate.read_pointer(cfg.runtime)
        if db is not None and db.exists():
            # 指针里记的是相对 index/ 的路径（builds/<id>.sqlite），比裸文件名有用：
            # 库不止一份，作者要能直接找到是哪一份。
            shown = str(pointer.get("db") or db.name)
            print(f"    索引: {human_size(db.stat().st_size)}（{shown}）")
            revision = pointer.get("canon_revision") or "(工作树)"
            print(
                f"      {pointer.get('artifact_count', '?')} 份产物 / {pointer.get('block_count', '?')} 块，"
                f"fts={pointer.get('fts_mode', '?')}，依据 {str(revision)[:12]}"
            )
            builds = locate.index_dir(cfg.runtime) / "builds"
            if builds.is_dir():
                print(f"      历史 build {len(list(builds.glob('*.sqlite')))} 份")
        elif locate.manifest_path(cfg.runtime).exists():
            print("    索引: 只有旧版 index.json；建议重跑 rebuild-index")
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

# 代码里的 `[[...]]` 不是链接：Obsidian 只对正文渲染双链。
# 写作时举例说明「裸 ID 双链长什么样」必然会写出 `[[CH-0001]]`，
# 不排除掉就会给每份讲解文档制造一条假断链——假断链比漏报更误导，
# 因为它会训练人忽略这项检查。
_FENCED = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def _link_text(text: str) -> str:
    """把代码区替换成等长空白，保留位置关系，避免误判代码里的双链字面量。"""
    text = _FENCED.sub(lambda m: " " * len(m.group(0)), text)
    return _INLINE_CODE.sub(lambda m: " " * len(m.group(0)), text)


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
    for name in (cfg.layout.get("vault_system_dir", "00_作者工作台"), cfg.layout.get("projects_dir", "01_小说项目")):
        if (vault / name).is_dir():
            ok(f"{name}/ 存在")
        else:
            fail(f"{name}/ 缺失")
            problems += 1

    heading("项目元数据")
    projects_dir = vault / cfg.layout.get("projects_dir", "01_小说项目")
    seen: dict[str, Path] = {}
    if projects_dir.is_dir():
        for root in sorted(p for p in projects_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            meta_path = root / "10_项目配置" / "project.yaml"
            if not meta_path.exists():
                fail(f"{root.name}: 缺 10_项目配置/project.yaml")
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

        # 查重范围 = 整个项目，排除 07_Staging（Agent 提案区，同名 ID 是常态）。
        # 素材虽然按分类分目录存放，ID 却是「全库连续编号，不按分类分段」（素材卡模板原话），
        # 必须跨目录一起看。只扫 Canon 正是 ASSET-0001 双占用长期没人发现的原因。
        #
        # 但「归档目录里引用了同一个 ID」与「正式区里两份档案抢同一个 ID」不是一回事：
        # 前者是历史事实，后者会让「按 ID 取对象」产生歧义。所以分开报——
        # 正式区内的冲突算失败，牵连到归档的只算警告。
        from ..infrastructure.filesystem.storage import iter_files as _iter_files

        def _formal(path: Path) -> bool:
            try:
                return pp.is_control_path(path)
            except OSError:
                return False

        ids: dict[str, tuple[Path, bool]] = {}
        dup = 0
        soft_dup = 0
        misnamed: list[str] = []
        counted: dict[str, int] = {}
        for path in _iter_files(pp.root, skip_dirs=("_staging",)):
            if path.suffix.lower() != ".md":
                continue
            rel = path.relative_to(pp.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            meta = read_frontmatter(path)
            eid = str(meta.get("id") or "").strip()
            # 模板里的 `id: {{id}}` 是占位符，不是身份；判据与 rebuild-index 保持一致
            if not eid or "{{" in eid:
                continue
            formal = _formal(path)
            area = pp.define_area(path)
            area = Path(area).name if area else "（非正式区）"
            counted[area] = counted.get(area, 0) + 1
            if eid in ids:
                other, other_formal = ids[eid]
                if formal and other_formal:
                    fail(f"ID 重复：{eid}（{other.relative_to(pp.root)} 与 {rel}）")
                    dup += 1
                else:
                    warn(
                        f"ID 重复但含非正式区：{eid}"
                        f"（{other.relative_to(pp.root)} 与 {rel}）——历史引用，不计失败"
                    )
                    soft_dup += 1
            else:
                ids[eid] = (path, formal)
            # 目录名不可靠，只看文件名与 id 是否错位。
            # V1.0 的 ID 是 `ent_000339`（下划线 + 数字末段），所以取文件名前两段
            # 拼回去比对；名称里可能还有下划线（`ent_000339_浑天-地动仪`），不能只切第一段。
            head = path.stem.split("_")
            file_id = f"{head[0]}_{head[1]}" if len(head) >= 2 else ""
            if file_id and ids_mod.split_id(file_id) and file_id != eid:
                misnamed.append(f"{rel}: id={eid} 与文件名前缀 {file_id} 不符")

        problems += dup
        parts = " / ".join(f"{k} {v}" for k, v in sorted(counted.items()))
        summary = f"带 id 的档案 {len(ids)} 份（{parts}），重复 {dup} 份"
        if soft_dup:
            summary += f"（另 {soft_dup} 份含非正式区，只警告）"
        if misnamed:
            summary += f"，id 与文件名错位 {len(misnamed)} 份"
        (fail if dup else ok)(summary)
        for item in misnamed[:5]:
            warn(item)
        for line in misnamed[:5]:
            warn(line)
        if len(misnamed) > 5:
            info(f"……另 {len(misnamed) - 5} 份 id 与文件名错位")

        stems = _link_index(pp)
        broken: list[tuple[str, str]] = []
        # 历史区（08_Reviews / 90_Archive）的双链单独记：那里保留的是**当时**的编号，
        # 指向的旧名在重编号后必然断。这是设计，不是缺陷——但不能因此整目录不查，
        # 否则真断链跟着一起藏起来（2026-11-22 就被藏过 28 条）。
        broken_hist: list[tuple[str, str]] = []
        cross: list[tuple[str, str]] = []
        total_links = 0
        for path in pp.vault.rglob("*.md"):
            rel = path.relative_to(pp.vault).as_posix()
            # 旧系统归档是另一套文件（legacy_v1 / legacy_v1_git），双链指向它自己的内部；
            # 隐藏目录是工具状态（.obsidian 等），不是内容。
            if rel.startswith(("99_Archive/legacy_v1/", "99_Archive/legacy_v1_git/")):
                continue
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            frozen = "/08_Reviews/" in f"/{rel}" or "/90_Archive/" in f"/{rel}"
            for match in WIKILINK.finditer(_link_text(text)):
                total_links += 1
                target = match.group(1).strip()
                if target.startswith("10_Projects/"):
                    if not target.startswith(f"10_Projects/{pp.root.name}/"):
                        cross.append((rel, target))
                        continue
                if not _resolve_link(target, pp, stems):
                    (broken_hist if frozen else broken).append((rel, target))

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
            ok(f"双链 {total_links} 条，活文档无断链")
        if broken_hist:
            info(
                f"历史区另有 {len(broken_hist)} 条断链"
                "（08_Reviews/90_Archive 保留当时编号，属设计，不计失败）"
            )
            for rel, target in broken_hist[:5]:
                info(f"  {rel} → [[{target}]]")
            if len(broken_hist) > 5:
                info(f"  ……另 {len(broken_hist) - 5} 条")

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
