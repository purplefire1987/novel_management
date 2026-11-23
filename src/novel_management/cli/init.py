"""初始化与索引：init-author / init-project / rebuild-index。"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .. import config as cfg_mod
from ..audit import now_iso
from ..domain.errors import UsageError
from ..infrastructure.filesystem.storage import write_atomic
from ._common import fail, heading, info, ok, read_frontmatter, slugify, warn

VAULT_CONFIG = """\
# Vault 级配置（V1.0 §8）。路径均相对 Vault 根，禁止 `..`。
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

PROJECT_META_TEMPLATE = "project/project.yaml"


def template_root() -> Path:
    """模板目录：仓库根的 `templates/`，可用 `NOVEL_TEMPLATES_DIR` 覆盖。

    为什么不再把 `project.yaml` 写成模块里的字符串常量：常量形式的模板与
    `templates/project/project.yaml` 会各活一份，改哪个都只生效一半——
    这正是 `templates/` 长期「存在但没人读」的成因。
    """
    override = os.environ.get("NOVEL_TEMPLATES_DIR")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "templates"


def render_project_meta(**values: str) -> str:
    """读 `templates/project/project.yaml` 并填充占位符。

    找不到模板就报错，不回退到内嵌默认值：静默回退会让人以为「改了模板没生效」，
    而那种问题要到下一次新建项目才暴露。
    """
    path = template_root() / PROJECT_META_TEMPLATE
    if not path.is_file():
        raise UsageError(f"缺少项目模板：{path}（可用 NOVEL_TEMPLATES_DIR 指定模板目录）")
    try:
        return path.read_text(encoding="utf-8").format(**values)
    except KeyError as exc:
        raise UsageError(f"项目模板里有未提供的占位符 {exc}：{path}") from exc

STAGING_AREAS = ("proposals", "plans", "preflight", "reviews", "memory", "drafts", "ingest", "simulation", "release")
PLANNING_DIRS = ("故事总纲", "卷级粗纲", "章节细纲", "场景节拍", "故事线", "伏笔与悬念")
MANUSCRIPT_DIRS = ("初稿", "修订稿", "正式定稿")
MEMORY_DIRS = ("Hot", "Short_Term", "Long_Term")
RUNTIME_DIRS = ("cache", "index", "logs", "staging", "temp", "tasks", "approvals", "backups", "authorship")


def _layout(cfg: cfg_mod.Config) -> dict:
    return {
        "system_dir": cfg.layout.get("vault_system_dir", "00_作者工作台"),
        "projects_dir": cfg.layout.get("projects_dir", "01_小说项目"),
        "shared_dir": cfg.layout.get("shared_assets_dir", "02_作者级通用资料"),
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
    # 00_作者工作台 的四个子目录是 V1.0 §8 明写的，不是可选装饰：
    # 灵感收集 / 待处理事项 / 全局仪表盘 / 作者级公共资料。
    # Policies 与 Schemas 是 V1.0 §7／§19 需要的机器配置落点，规范没给位置，
    # 放在工作台下面（不放进 02_作者级通用资料——那里是「素材」，不是「制度」）。
    dirs = [
        system_dir / "灵感收集",
        system_dir / "待处理事项",
        system_dir / "全局仪表盘",
        system_dir / "作者级公共资料",
        system_dir / "Policies",
        system_dir / "Schemas",
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
    root = vault / cfg.layout.get("projects_dir", "01_小说项目") / f"{project_id}_{slug}"
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
        (root / "02_世界知识库" / sub).mkdir(parents=True, exist_ok=True)
    for sub in PLANNING_DIRS:
        (root / "03_故事规划" / sub).mkdir(parents=True, exist_ok=True)
    for sub in MANUSCRIPT_DIRS:
        (root / "04_正文" / sub).mkdir(parents=True, exist_ok=True)
    for sub in MEMORY_DIRS:
        (root / "05_记忆与连续性" / sub).mkdir(parents=True, exist_ok=True)
    for sub in STAGING_AREAS:
        (root / "_staging" / sub).mkdir(parents=True, exist_ok=True)
    (root / "07_审核与报告" / "editorial").mkdir(parents=True, exist_ok=True)
    (root / "07_审核与报告" / "author").mkdir(parents=True, exist_ok=True)

    write_atomic(
        root / "10_项目配置" / "project.yaml",
        render_project_meta(
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

    registry = vault / cfg.layout.get("vault_system_dir", "00_作者工作台") / "Project_Registry.md"
    if registry.exists():
        with registry.open("a", encoding="utf-8") as fh:
            fh.write(f"| {project_id} | {project_name} | draft | {root.name} |\n")

    seeded = _seed_active_project(vault, cfg.layout.get("vault_system_dir", "00_作者工作台"), project_id)
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

