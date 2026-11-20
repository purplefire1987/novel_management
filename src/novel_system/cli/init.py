"""初始化与索引：init-author / init-project / rebuild-index。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .. import config as cfg_mod
from ..audit import now_iso
from ..errors import UsageError
from ..storage import write_atomic
from ._common import fail, heading, info, ok, read_frontmatter, slugify, warn

VAULT_CONFIG = """\
# Vault 级配置（V4.0 §4.2）。路径均相对 Vault 根，禁止 `..`。
schema_version: '4.0.0'
vault_name: "{vault_name}"
created_at: '{created_at}'
timezone: Asia/Shanghai

# 结构约定
layouts:
  system: {system_dir}
  projects: {projects_dir}
  shared_assets: {shared_dir}
  archive: {archive_dir}

# 作者身份：任务记录要带 author_id，才答得出「这条任务是给谁的作品做的」
author:
  author_id: "{author_id}"

# Runtime 必须在 Vault 之外（V4.0 §2.1）
runtime_root: {runtime}

# 公仓（系统代码，不含作品内容）
public_repo: {public_repo}

active_project:
  project_id: ""

# 隐私硬约束
privacy:
  vault_has_remote: false
  vault_in_public_repo: false
  canon_write_requires: author
  private_remote_allowed: false
  public_repo_may_read_vault: false
  cross_project_access: false
  agent_canon_write: false
"""

PROJECT_META = """\
project_id: "{project_id}"
project_name: "{project_name}"
project_slug: "{slug}"
author_id: "{author_id}"
created_at: '{created_at}'
project_status: "draft"
canon_root: "01_Canon"
planning_root: "02_Planning"
manuscript_root: "03_Manuscripts"
assets_root: "04_Story_Assets"
ledger_root: "05_Ledgers"
memory_root: "06_Memory"
staging_root: "07_Staging"
review_root: "08_Reviews"
archive_root: "90_Archive"
timezone: "Asia/Shanghai"
schema_version: 1
"""

STAGING_AREAS = ("proposals", "plans", "preflight", "reviews", "memory", "drafts", "ingest", "simulation", "release")
PLANNING_DIRS = ("Story_Bible", "Series_Outline", "Volume_Outlines", "Chapter_Outlines", "Scene_Beats")
MANUSCRIPT_DIRS = ("Drafts", "Revisions", "Final")
LEDGER_DIRS = ("Timeline", "Events", "Relationships", "Foreshadowing")
MEMORY_DIRS = ("Hot", "Short_Term", "Long_Term")
RUNTIME_DIRS = ("cache", "index", "logs", "staging", "temp", "tasks", "approvals", "backups", "authorship")


def _layout(cfg: cfg_mod.Config) -> dict:
    return {
        "system_dir": cfg.layout.get("vault_system_dir", "00_System"),
        "projects_dir": cfg.layout.get("projects_dir", "10_Projects"),
        "shared_dir": cfg.layout.get("shared_assets_dir", "90_Shared_Assets"),
        "archive_dir": cfg.layout.get("vault_archive_dir", "99_Archive"),
    }


def cmd_init_author(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    if vault.exists() and any(vault.iterdir()):
        print(f"目标目录非空，拒绝初始化：{vault}")
        print("请换一个空目录，或先自行备份并清空。本命令不覆盖既有内容。")
        return 2
    if not args.confirm:
        print(f"将创建 Vault 于：{vault}")
        print("确认请加 --confirm")
        return 2

    layout = _layout(cfg)
    system_dir = vault / layout["system_dir"]
    dirs = [
        system_dir / "Dashboards",
        system_dir / "Policies",
        vault / layout["projects_dir"],
        vault / layout["shared_dir"],
        vault / layout["archive_dir"],
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    author_id = args.author_id or "author-local"
    write_atomic(
        system_dir / "Vault_Config.yaml",
        VAULT_CONFIG.format(
            vault_name=vault.name,
            created_at=now_iso()[:10],
            author_id=author_id,
            system_dir=layout["system_dir"],
            projects_dir=layout["projects_dir"],
            shared_dir=layout["shared_dir"],
            archive_dir=layout["archive_dir"],
            runtime=cfg.runtime or "~/novel-runtime",
            public_repo=cfg.public_repo,
        ).encode("utf-8"),
    )
    write_atomic(
        system_dir / "Author_Profile.md",
        f"# 作者\n\n- author_id: `{author_id}`\n- 建立时间: {now_iso()}\n".encode("utf-8"),
    )
    write_atomic(
        system_dir / "Project_Registry.md",
        "# 项目注册表\n\n| project_id | 名称 | 状态 | 路径 |\n|---|---|---|---|\n".encode("utf-8"),
    )

    if cfg.runtime:
        for sub in RUNTIME_DIRS:
            (cfg.runtime / sub).mkdir(parents=True, exist_ok=True)

    print(f"Vault 已建立：{vault}")
    for d in dirs:
        print(f"  + {d.relative_to(vault)}/")
    if cfg.runtime:
        print(f"Runtime 目录已就绪：{cfg.runtime}")
        for sub in RUNTIME_DIRS:
            print(f"  + runtime/{sub}/")
    return 0


def cmd_init_project(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    project_id = args.id or cfg.project_id
    if not project_id:
        print("缺少项目 ID：用 --id 指定，或配置 active_project.project_id")
        return 2
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_id):
        print(f"project_id 只允许小写字母、数字、连字符：{project_id}")
        return 2

    project_name = args.name or project_id
    slug = slugify(project_name)
    root = vault / cfg.layout.get("projects_dir", "10_Projects") / f"{project_id}_{slug}"
    cfg_mod.ensure_inside(root, vault)

    if root.exists() and any(root.iterdir()):
        print(f"项目目录已存在且非空，拒绝初始化：{root}")
        return 2
    if not args.confirm:
        print(f"将创建项目于：{root}")
        print("确认请加 --confirm")
        return 2

    for sub in cfg.layout.get("project_subdirs", []):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for sub in cfg.layout.get("canon_subdirs", []):
        (root / "01_Canon" / sub).mkdir(parents=True, exist_ok=True)
    for sub in PLANNING_DIRS:
        (root / "02_Planning" / sub).mkdir(parents=True, exist_ok=True)
    for sub in MANUSCRIPT_DIRS:
        (root / "03_Manuscripts" / sub).mkdir(parents=True, exist_ok=True)
    for sub in LEDGER_DIRS:
        (root / "05_Ledgers" / sub).mkdir(parents=True, exist_ok=True)
    for sub in MEMORY_DIRS:
        (root / "06_Memory" / sub).mkdir(parents=True, exist_ok=True)
    for sub in STAGING_AREAS:
        (root / "07_Staging" / sub).mkdir(parents=True, exist_ok=True)
    (root / "08_Reviews" / "editorial").mkdir(parents=True, exist_ok=True)
    (root / "08_Reviews" / "author").mkdir(parents=True, exist_ok=True)

    write_atomic(
        root / "00_Project" / "project.yaml",
        PROJECT_META.format(
            project_id=project_id,
            project_name=project_name,
            slug=slug,
            author_id=args.author_id or "author-local",
            created_at=now_iso(),
        ).encode("utf-8"),
    )
    write_atomic(
        root / "README.md",
        f"# {project_name}\n\n- project_id: `{project_id}`\n- 建立时间: {now_iso()}\n".encode("utf-8"),
    )

    registry = vault / cfg.layout.get("vault_system_dir", "00_System") / "Project_Registry.md"
    if registry.exists():
        with registry.open("a", encoding="utf-8") as fh:
            fh.write(f"| {project_id} | {project_name} | draft | {root.name} |\n")

    seeded = _seed_active_project(vault, cfg.layout.get("vault_system_dir", "00_System"), project_id)
    if seeded:
        info(f"已把 active_project 设为 {project_id}（后续命令可省略 --project）")
    else:
        info("active_project 已有值，未改动；写第二个项目时请带 --project")

    print(f"项目已建立：{root}")
    return 0


def _seed_active_project(vault: Path, system_dir: str, project_id: str) -> bool:
    """第一个项目写进 active_project，日常命令就不用每次带 --project。

    只填空值，不覆盖已有配置——作者手动指过哪个项目是当前项目，就该一直听他的。
    """
    path = vault / system_dir / "Vault_Config.yaml"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"(active_project:[ \t]*\r?\n[ \t]*project_id:[ \t]*)([\"']?)([^\"'\r\n]*)([\"']?)")
    match = pattern.search(text)
    if not match or match.group(3).strip():
        return False
    updated = f'{match.group(1)}"{project_id}"'
    write_atomic(path, (text[: match.start()] + updated + text[match.end() :]).encode("utf-8"))
    return True


# 索引不收录运行态草稿与归档：它们不是事实源（V4.0 §10.2）。
INDEX_EXCLUDE_PREFIXES = ("07_Staging", "08_Reviews", "90_Archive")


def cmd_rebuild_index(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    runtime = cfg.require_runtime()
    out_dir = runtime / "index"
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    skipped = 0
    for path in sorted(vault.rglob("*.md")):
        # 隐藏目录按 **Vault 内相对路径** 判断：Vault 自身可能位于 .tmp 之类
        # 以点开头的目录里，用绝对路径会把整棵树误判为隐藏。
        rel = path.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(part in INDEX_EXCLUDE_PREFIXES for part in rel.parts):
            skipped += 1
            continue
        meta = read_frontmatter(path)
        raw_id = meta.get("id", "")
        # 模板里的 `id: {{id}}` 是占位符，不是身份；纳进来会污染重复检查。
        if "{{" in raw_id:
            raw_id = ""
        entries.append(
            {
                "path": rel.as_posix(),
                "project": _project_of(rel),
                "id": raw_id,
                "type": meta.get("type", ""),
                "status": meta.get("status", ""),
                "title": meta.get("title", path.stem),
            }
        )

    ids = [e["id"] for e in entries if e["id"]]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    payload = {
        "built_at": now_iso(),
        "schema_version": 2,
        "source_root": str(vault),
        "count": len(entries),
        "skipped_runtime": skipped,
        "ids": len(ids),
        "duplicate_ids": duplicates,
        "entries": entries,
    }
    write_atomic(out_dir / "index.json", json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8"))

    print(f"索引已重建：{out_dir / 'index.json'}")
    print(f"  文件 {len(entries)} 份，其中 {len(ids)} 份带 id；跳过运行态/归档 {skipped} 份")
    if duplicates:
        print(f"  [WARN] {len(duplicates)} 个 ID 重复：{duplicates[:5]}")
    return 0


def _project_of(rel: Path) -> str:
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "10_Projects":
        return parts[1]
    return ""
