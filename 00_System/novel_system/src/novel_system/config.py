"""配置加载：Vault 定位 + 00_System/Config 下 5 份 YAML。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import vault

CONFIG_FILES = {
    "system": "system_config.yaml",
    "id_rules": "id_rules.yaml",
    "permissions": "permissions.yaml",
    "workflow": "workflow.yaml",
    "agents": "agent_registry.yaml",
}


class ConfigError(RuntimeError):
    pass


# 进程内已解析的配置。CLI 一次命令一个进程，不存在中途热更的场景。
_LOADED: dict[Path, "Config"] = {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"缺少配置文件：{path}")
    # 走 vault 的读取入口：命令入口挂了内容镜像时，这 5 份配置就是 0 次读盘
    data = yaml.safe_load(vault.read_text(path)) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式错误（顶层应为映射）：{path}")
    return data


def find_vault(start: Path | None = None) -> Path:
    """按 环境变量 -> 当前目录/NovelVault -> 向上查找含 00_System 的目录。"""
    env = os.environ.get("NOVEL_VAULT")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "00_System").is_dir():
            return p
        raise ConfigError(f"NOVEL_VAULT={env} 不是有效 Vault（缺少 00_System）")

    cur = (start or Path.cwd()).resolve()
    candidate = cur / "NovelVault"
    if (candidate / "00_System").is_dir():
        return candidate
    if (cur / "00_System").is_dir():
        return cur
    for parent in [cur, *cur.parents]:
        if (parent / "NovelVault" / "00_System").is_dir():
            return parent / "NovelVault"
        if (parent / "00_System").is_dir():
            return parent
    raise ConfigError(
        "未找到 NovelVault。请用 --vault 指定，或设置环境变量 NOVEL_VAULT。"
    )


@dataclass
class Config:
    vault: Path
    system: dict[str, Any]
    id_rules: dict[str, Any]
    permissions: dict[str, Any]
    workflow: dict[str, Any]
    agents: dict[str, Any]

    # ---------- 加载 ----------
    @classmethod
    def load(cls, vault: Path | str | None = None) -> "Config":
        """加载配置。同一个 Vault 在一个进程里只解析一次。

        命令入口先加载一次用来定位镜像，各子命令再各自 `load()` 一次；
        没有这层缓存就是白读两遍 5 份 YAML（约 100ms）。CLI 一次命令一个进程，
        进程内配置不会中途变，缓存不会带来陈旧问题。
        """
        root = find_vault(Path(vault) if vault else None)
        hit = _LOADED.get(root)
        if hit is not None:
            return hit
        cfg_dir = root / "00_System" / "Config"
        data = {key: _read_yaml(cfg_dir / name) for key, name in CONFIG_FILES.items()}
        cfg = cls(
            vault=root,
            system=data["system"],
            id_rules=data["id_rules"],
            permissions=data["permissions"],
            workflow=data["workflow"],
            agents=data["agents"],
        )
        _LOADED[root] = cfg
        return cfg

    # ---------- 路径 ----------
    @property
    def paths(self) -> dict[str, Any]:
        return self.system.get("paths", {})

    def dir(self, key: str) -> Path:
        """取 system_config.paths 中配置的目录（绝对路径）。"""
        rel = self.paths.get(key, key)
        return (self.vault / rel).resolve()

    @property
    def system_dir(self) -> Path:
        return self.vault / self.paths.get("system_dir", ".novel_system")

    @property
    def index_db(self) -> Path:
        return self.system_dir / "index.sqlite"

    @property
    def staging_dir(self) -> Path:
        return self.vault / self.system.get("transactions", {}).get(
            "staging_dir", ".novel_system/staging"
        )

    @property
    def backup_dir(self) -> Path:
        return self.vault / self.system.get("transactions", {}).get(
            "backup_dir", ".novel_system/backups"
        )

    @property
    def logs_dir(self) -> Path:
        return self.system_dir / "logs"

    @property
    def scratch_dir(self) -> Path:
        """批量读盘的收容目录。

        宿主的中转层会为子进程读到的每个文件在 TMPDIR 里留一份副本；指到这里，
        垃圾留在 Vault 内部（已被 .gitignore 排除），不往系统临时目录倒。
        """
        return self.system_dir / "tmp"

    def ensure_dirs(self) -> None:
        """补齐运行目录。

        每个目录先 `is_dir()` 再决定要不要 `mkdir`：已经存在的目录再调一次 mkdir，
        在受限宿主里要过一次 IPC 中转（实测 ~5ms/次），而 Config 在一个命令里会被
        构造十几次、每次补 9 个目录，白花掉的半秒足够跑完整个用例。`stat` 不走中转。
        """
        for d in (self.system_dir, self.staging_dir, self.backup_dir, self.logs_dir, self.scratch_dir):
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)
        for sub in ("preflight", "draft", "memory", "outline"):
            d = self.staging_dir / sub
            if not d.is_dir():
                d.mkdir(parents=True, exist_ok=True)

    # ---------- 取值辅助 ----------
    def rel(self, path: Path) -> str:
        """转成相对 Vault 的 POSIX 路径。"""
        try:
            return path.resolve().relative_to(self.vault).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def abs(self, rel_path: str) -> Path:
        return (self.vault / rel_path).resolve()

    @property
    def budget_chars(self) -> int:
        return int(self.system.get("context_budget", {}).get("total_chars", 24000))

    def budget_ratio(self, section: str) -> float:
        return float(
            self.system.get("context_budget", {}).get("ratios", {}).get(section, 0.0)
        )

    @property
    def recent_chapter_count(self) -> int:
        return int(self.system.get("context_budget", {}).get("recent_chapter_count", 3))

    @property
    def guardrails(self) -> dict[str, Any]:
        return self.system.get("guardrails", {})

    @property
    def llm(self) -> dict[str, Any]:
        return self.system.get("llm", {})

    def id_pattern(self, doc_type: str) -> str | None:
        return (self.id_rules.get("id_patterns") or {}).get(doc_type)

    @property
    def allowed_shared_id_types(self) -> list[list[str]]:
        return [list(x) for x in (self.id_rules.get("allow_shared_id_types") or [])]

    def approval_class(self, kind: str) -> str:
        table = self.permissions.get("approval_classes") or {}
        return table.get(kind, "author")

    def agent_def(self, agent: str) -> dict[str, Any]:
        return (self.agents.get("agents") or {}).get(agent, {})
