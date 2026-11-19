"""CLI 入口。只读命令随时可跑；写入命令必须显式确认。

设计约束：
- 不硬编码任何私人路径，一律走 config.load()。
- 写入前校验目标落在 Vault 内；越界直接拒绝。
- 已存在内容不覆盖；冲突即停。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import config as cfg_mod

TEMPLATES_PROJECT_META = """\
project_id: "{project_id}"
project_name: "{project_name}"
project_slug: "{project_slug}"
author_id: "{author_id}"
created_at: "{created_at}"
project_status: "draft"
canon_root: "01_Canon"
planning_root: "02_Planning"
manuscript_root: "03_Manuscripts"
staging_root: "07_Staging"
archive_root: "90_Archive"
timezone: "Asia/Shanghai"
schema_version: 1
"""

VAULT_CONFIG = """\
# Vault 级配置。路径均相对 Vault 根，禁止 `..`。
system:
  config_version: 1
author:
  author_id: "{author_id}"
active_project:
  project_id: ""
paths:
  public_repo: ""
  author_vault: "{vault_path}"
  runtime: ""
privacy:
  private_remote_allowed: false
  public_repo_may_read_vault: false
  cross_project_access: false
  agent_canon_write: false
"""


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slug(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_-]+", "-", name.strip().lower()).strip("-")
    return s or "project"


def _read_frontmatter(path: Path) -> dict:
    """只读 `---` 之间的简单 key: value，够索引使用。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")
    return meta


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def cmd_doctor(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    problems = 0
    print("配置")
    for line in cfg.describe():
        print(f"  {line}")

    print("\n运行时环境")
    if sys.version_info >= (3, 11):
        _ok(f"Python {sys.version.split()[0]}")
    else:
        _fail(f"Python {sys.version.split()[0]}，需要 >= 3.11")
        problems += 1
    try:
        import yaml  # noqa: F401

        _ok("PyYAML 可用")
    except ImportError:
        _warn("PyYAML 未安装，使用内置简易解析器（pip install -e \".[yaml]\" 可启用）")

    print("\n路径")
    vault = cfg.author_vault
    if vault is None:
        _warn("author_vault 未配置：只读命令仍可用，init/validate 需要它")
    elif not vault.exists():
        _fail(f"author_vault 不存在：{vault}")
        problems += 1
    else:
        _ok(f"author_vault 存在：{vault}")

    runtime = cfg.runtime
    if runtime is None:
        _warn("runtime 未配置：索引与日志无处落盘")
    else:
        if vault is not None:
            try:
                cfg_mod.ensure_inside(runtime, vault)
                _fail(f"runtime 位于 Vault 内：{runtime}（应独立于 Vault）")
                problems += 1
            except cfg_mod.ConfigError:
                _ok(f"runtime 在 Vault 之外：{runtime}")
        if runtime.exists():
            _ok(f"runtime 存在：{runtime}")
        else:
            _warn(f"runtime 尚未创建：{runtime}（init-author 或 rebuild-index 会创建）")

    print("\n隐私开关")
    expect = {
        "private_remote_allowed": False,
        "public_repo_may_read_vault": False,
        "cross_project_access": False,
        "agent_canon_write": False,
    }
    for key, want in expect.items():
        got = cfg.privacy.get(key, want)
        if got == want:
            _ok(f"{key} = {got}")
        else:
            _fail(f"{key} = {got}，应为 {want}")
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
        projects = cfg.projects_dir
        if projects.exists():
            names = sorted(p.name for p in projects.iterdir() if p.is_dir())
            print(f"  项目数: {len(names)}")
            for name in names:
                root = projects / name
                md = sum(1 for _ in root.rglob("*.md")) if root.exists() else 0
                print(f"    - {name}  ({md} 份 Markdown)")
        else:
            print("  项目目录尚未建立")

    print(f"  Runtime: {cfg.runtime or '(未配置)'}")
    if cfg.runtime:
        index = cfg.runtime / "index" / "index.json"
        if index.exists():
            size = index.stat().st_size
            print(f"    索引: 存在，{size} 字节")
        else:
            print("    索引: 尚未生成")
    return 0


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------

def cmd_validate(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    problems = 0
    vault = cfg.require_vault()

    print("Vault 结构")
    for name in (cfg.layout.get("vault_system_dir", "00_System"), cfg.layout.get("projects_dir", "10_Projects")):
        path = vault / name
        if path.is_dir():
            _ok(f"{name}/ 存在")
        else:
            _fail(f"{name}/ 缺失")
            problems += 1

    print("\n项目元数据")
    projects_dir = vault / cfg.layout.get("projects_dir", "10_Projects")
    if projects_dir.is_dir():
        project_dirs = sorted(p for p in projects_dir.iterdir() if p.is_dir())
        if not project_dirs:
            _warn("尚无项目")
        seen: dict[str, Path] = {}
        for root in project_dirs:
            meta_path = root / "00_Project" / "project.yaml"
            if not meta_path.exists():
                _fail(f"{root.name}: 缺 00_Project/project.yaml")
                problems += 1
                continue
            data = cfg_mod.read_yaml(meta_path)
            pid = str(data.get("project_id") or "")
            if not pid:
                _fail(f"{root.name}: project_id 为空")
                problems += 1
            elif pid in seen:
                _fail(f"project_id 重复：{pid}（{seen[pid].name} 与 {root.name}）")
                problems += 1
            else:
                seen[pid] = root
                _ok(f"{root.name}: project_id={pid}")
            for sub in cfg.layout.get("project_subdirs", []):
                if not (root / sub).exists():
                    _warn(f"{root.name}: 缺子目录 {sub}/")

    print("\n路径越界")
    for root in [vault] + ([projects_dir] if projects_dir.exists() else []):
        try:
            cfg_mod.ensure_inside(root, vault)
        except cfg_mod.ConfigError as exc:
            _fail(str(exc))
            problems += 1
    if problems == 0:
        _ok("所有路径均在 Vault 内")

    print()
    if problems:
        print(f"validate: {problems} 项未通过")
        return 1
    print("validate: 通过")
    return 0


# --------------------------------------------------------------------------
# init-author
# --------------------------------------------------------------------------

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

    layout = cfg.layout
    system_dir = layout.get("vault_system_dir", "00_System")
    dirs = [
        vault / system_dir / "Dashboards",
        vault / system_dir / "Policies",
        vault / layout.get("projects_dir", "10_Projects"),
        vault / layout.get("shared_assets_dir", "90_Shared_Assets"),
        vault / layout.get("vault_archive_dir", "99_Archive"),
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    author_id = args.author_id or "author-local"
    (vault / system_dir / "Vault_Config.yaml").write_text(
        VAULT_CONFIG.format(author_id=author_id, vault_path=str(vault)), encoding="utf-8"
    )
    (vault / system_dir / "Author_Profile.md").write_text(
        f"# 作者\n\n- author_id: `{author_id}`\n- 建立时间: {_now()}\n", encoding="utf-8"
    )
    (vault / system_dir / "Project_Registry.md").write_text(
        "# 项目注册表\n\n| project_id | 名称 | 状态 | 路径 |\n|---|---|---|---|\n", encoding="utf-8"
    )

    if cfg.runtime:
        for sub in layout.get("runtime_subdirs", []):
            (cfg.runtime / sub).mkdir(parents=True, exist_ok=True)

    print(f"Vault 已建立：{vault}")
    for d in dirs:
        print(f"  + {d.relative_to(vault)}/")
    if cfg.runtime:
        print(f"Runtime 目录已就绪：{cfg.runtime}")
    return 0


# --------------------------------------------------------------------------
# init-project
# --------------------------------------------------------------------------

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
    slug = _slug(project_name)
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
    for sub in ("Story_Bible", "Series_Outline", "Volume_Outlines", "Chapter_Outlines", "Scene_Beats"):
        (root / "02_Planning" / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("Drafts", "Revisions", "Final"):
        (root / "03_Manuscripts" / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("Timeline", "Events", "Relationships", "Foreshadowing"):
        (root / "05_Ledgers" / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("Hot", "Short_Term", "Long_Term"):
        (root / "06_Memory" / sub).mkdir(parents=True, exist_ok=True)

    (root / "00_Project" / "project.yaml").write_text(
        TEMPLATES_PROJECT_META.format(
            project_id=project_id,
            project_name=project_name,
            project_slug=slug,
            author_id=args.author_id or "author-local",
            created_at=_now(),
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"# {project_name}\n\n- project_id: `{project_id}`\n- 建立时间: {_now()}\n",
        encoding="utf-8",
    )

    registry = vault / cfg.layout.get("vault_system_dir", "00_System") / "Project_Registry.md"
    if registry.exists():
        with registry.open("a", encoding="utf-8") as fh:
            fh.write(f"| {project_id} | {project_name} | draft | {root.name} |\n")

    print(f"项目已建立：{root}")
    return 0


# --------------------------------------------------------------------------
# rebuild-index
# --------------------------------------------------------------------------

def cmd_rebuild_index(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    runtime = cfg.require_runtime()
    out_dir = runtime / "index"
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    for path in sorted(vault.rglob("*.md")):
        # 隐藏目录按 **Vault 内相对路径** 判断：Vault 自身可能位于 .tmp 之类
        # 以点开头的目录里，用绝对路径会把整棵树误判为隐藏。
        rel = path.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue
        meta = _read_frontmatter(path)
        entries.append(
            {
                "path": str(rel),
                "id": meta.get("id", ""),
                "type": meta.get("type", ""),
                "status": meta.get("status", ""),
                "title": meta.get("title", path.stem),
            }
        )

    payload = {
        "built_at": _now(),
        "source_root": str(vault),
        "count": len(entries),
        "entries": entries,
    }
    out = out_dir / "index.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    typed = sum(1 for e in entries if e["id"])
    print(f"索引已重建：{out}")
    print(f"  文件 {len(entries)} 份，其中 {typed} 份带 id")
    return 0


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # 全局参数同时挂在主命令与子命令上，`novel --vault X status` 与
    # `novel status --vault X` 都成立——位置不同不该让命令失效。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="作者 Vault 路径（覆盖环境变量与配置文件）")
    common.add_argument("--runtime", help="Runtime 路径")
    common.add_argument("--project", help="项目 ID")

    parser = argparse.ArgumentParser(prog="novel", description="长篇小说管理系统 CLI", parents=[common])

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查配置、依赖、路径与隐私开关", parents=[common])
    sub.add_parser("status", help="查看 Vault、项目与索引状态", parents=[common])
    sub.add_parser("validate", help="校验 Vault 结构、项目元数据与路径越界", parents=[common])

    p_author = sub.add_parser("init-author", help="初始化作者 Vault", parents=[common])
    p_author.add_argument("--author-id", default="author-local")
    p_author.add_argument("--confirm", action="store_true", help="确认写入")

    p_proj = sub.add_parser("init-project", help="创建小说项目骨架", parents=[common])
    p_proj.add_argument("--id", help="项目 ID（小写字母、数字、连字符）")
    p_proj.add_argument("--name", help="项目显示名")
    p_proj.add_argument("--author-id", default="author-local")
    p_proj.add_argument("--confirm", action="store_true", help="确认写入")

    sub.add_parser("rebuild-index", help="从 Markdown 重建索引到 Runtime", parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = cfg_mod.load(
            vault=args.vault,
            runtime=args.runtime,
            project=args.project,
        )
    except cfg_mod.ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2

    handlers = {
        "doctor": cmd_doctor,
        "status": cmd_status,
        "validate": cmd_validate,
        "init-author": cmd_init_author,
        "init-project": cmd_init_project,
        "rebuild-index": cmd_rebuild_index,
    }
    try:
        return handlers[args.command](cfg, args)
    except cfg_mod.ConfigError as exc:
        print(f"错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
