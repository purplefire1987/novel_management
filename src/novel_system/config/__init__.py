"""配置解析：所有路径在运行时解析，代码里不出现作者绝对路径。

优先级（高 → 低）：
1. 显式参数（CLI）
2. 环境变量
3. 用户级配置 ~/.config/novel-system/config.yaml
4. Vault 级配置 <vault>/00_System/Vault_Config.yaml
5. 本仓库 config/defaults.yaml
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ENV_VAULT = "NOVEL_VAULT"
ENV_RUNTIME = "NOVEL_RUNTIME"
ENV_PUBLIC_REPO = "NOVEL_PUBLIC_REPO"

def _detect_repo_root() -> Path:
    """向上找带 pyproject.toml 的目录；安装后找不到就退回包目录。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path(__file__).resolve().parents[2]


PACKAGE_ROOT = _detect_repo_root()
DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults.yaml"
# `config/defaults.yaml` 是仓库内的权威副本，包内这份是运行期可用的副本。
# 两份必须逐字节一致，由 tests/unit/test_config_defaults.py 守。


class ConfigError(RuntimeError):
    """配置缺失、格式错误或路径越界。"""


# --------------------------------------------------------------------------
# 极简 YAML 子集：嵌套映射、列表、标量、注释。够读本仓库的配置，不追求通用。
# 装了 PyYAML 就用它，行为更标准。
# --------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _scalar(token: str):
    token = token.strip()
    if not token:
        return ""
    if token in ("true", "True"):
        return True
    if token in ("false", "False"):
        return False
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    if token in ("[]", "{}"):
        return [] if token == "[]" else {}
    return token


def _parse_block(lines: list[tuple[int, str]], pos: int, indent: int):
    """返回 (值, 下一行位置)。缩进驱动，不处理多行标量与锚点。"""
    if pos >= len(lines):
        return {}, pos

    is_list = lines[pos][1].startswith("- ")
    result: dict | list = [] if is_list else {}

    while pos < len(lines):
        cur_indent, content = lines[pos]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise ConfigError(f"缩进异常：第 {cur_indent} 列出现意外层级")
        if is_list:
            if not content.startswith("- "):
                break
            item = content[2:].strip()
            if not item:
                value, pos = _parse_block(lines, pos + 1, indent + 2)
                result.append(value)
                continue
            result.append(_scalar(item))
            pos += 1
        else:
            if ":" not in content:
                raise ConfigError(f"无法解析的行：{content!r}")
            key, _, rest = content.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest:
                result[key] = _scalar(rest)
                pos += 1
            else:
                value, pos = _parse_block(lines, pos + 1, indent + 2)
                result[key] = value
    return result, pos


def parse_yaml(text: str) -> dict:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data or {}
    except ImportError:
        pass

    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line.strip()))
    if not lines:
        return {}
    if lines[0][0] != 0:
        raise ConfigError("顶层键必须顶格")
    data, _ = _parse_block(lines, 0, 0)
    return data if isinstance(data, dict) else {}


def read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return parse_yaml(path.read_text(encoding="utf-8"))
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


# --------------------------------------------------------------------------
# 路径校验
# --------------------------------------------------------------------------

def resolve_path(raw: str | Path, base: Path | None = None) -> Path:
    """展开 ~ 与环境变量，再规范化；拒绝含 `..` 的输入。"""
    text = str(raw).strip()
    if not text:
        raise ConfigError("路径为空")
    if ".." in Path(text).parts:
        raise ConfigError(f"路径不得含 '..'：{text}")
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        if base is None:
            raise ConfigError(f"相对路径缺少基准目录：{text}")
        path = base / path
    return path.resolve()


def ensure_inside(child: Path, parent: Path) -> Path:
    """子路径必须落在父目录内，符号链接逃逸同样拒绝。"""
    child_r, parent_r = child.resolve(), parent.resolve()
    if child_r != parent_r and parent_r not in child_r.parents:
        raise ConfigError(f"路径越界：{child_r} 不在 {parent_r} 之内")
    return child_r


# --------------------------------------------------------------------------
# 配置对象
# --------------------------------------------------------------------------

@dataclass
class Config:
    public_repo: Path
    author_vault: Path | None
    runtime: Path | None
    project_id: str
    privacy: dict = field(default_factory=dict)
    layout: dict = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    # 任务记录必须带作者，否则无法回答「这条任务是给谁的作品做的」。
    author_id: str = "author-local"

    # ---- 必需路径 ----
    def require_vault(self) -> Path:
        if self.author_vault is None:
            raise ConfigError(
                "未配置作者 Vault。请设置 --vault、环境变量 NOVEL_VAULT，"
                "或在 ~/.config/novel-system/config.yaml 写 paths.author_vault"
            )
        return self.author_vault

    def require_runtime(self) -> Path:
        if self.runtime is None:
            raise ConfigError(
                "未配置 Runtime。请设置 --runtime、环境变量 NOVEL_RUNTIME，"
                "或在 ~/.config/novel-system/config.yaml 写 paths.runtime"
            )
        return self.runtime

    def require_project(self) -> str:
        if not self.project_id:
            raise ConfigError("未指定项目。请用 --project 或配置 active_project.project_id")
        return self.project_id

    # ---- 派生路径 ----
    @property
    def projects_dir(self) -> Path:
        return self.require_vault() / self.layout.get("projects_dir", "01_小说项目")

    def project_candidates(self) -> list[tuple[str, Path]]:
        """列出 Vault 内的项目：(project_id, 目录)。以 project.yaml 为准，读不到才退回目录名。"""
        base = self.projects_dir
        out: list[tuple[str, Path]] = []
        if not base.is_dir():
            return out
        for path in sorted(base.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            meta = read_yaml(path / "10_项目配置" / "project.yaml")
            out.append((str(meta.get("project_id") or path.name), path))
        return out

    def resolve_project_id(self, project_id: str | None = None) -> str:
        """显式 > 配置的 active_project > 报错。

        这里**不做**「Vault 里只有一个项目，那就用它的」这种推断。
        单项目时推断看起来无害，但等到第二个项目建起来，同一条命令的语义就变了；
        写入类命令一旦选错项目，改的是另一部书的 Canon。

        要让日常命令不必带 --project，正确做法是在配置里写死 active_project
        （`novel init-project` 建第一个项目时会自动写入），而不是让程序去猜。
        """
        if project_id:
            return project_id
        if self.project_id:
            return self.project_id
        candidates = self.project_candidates()
        if not candidates:
            raise ConfigError("Vault 内没有项目：先运行 novel init-project")
        raise ConfigError(
            "未指定项目。请用 --project 指定，或在 Vault_Config.yaml 的 "
            f"active_project.project_id 写死（当前可选：{[c[0] for c in candidates]}）"
        )

    def project_root(self, project_id: str | None = None) -> Path:
        pid = self.resolve_project_id(project_id)
        for cid, path in self.project_candidates():
            if cid == pid:
                return path
        base = self.projects_dir
        matches = sorted(p for p in base.glob(f"{pid}*") if p.is_dir()) if base.exists() else []
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ConfigError(f"项目 ID {pid} 命中多个目录：{[m.name for m in matches]}")
        raise ConfigError(f"项目不存在：{pid}（{base} 下没有对应目录）")

    def runtime_dir(self, *parts: str) -> Path:
        return self.require_runtime().joinpath(*parts)

    def vault_system_dir(self) -> Path:
        return self.require_vault() / self.layout.get("vault_system_dir", "00_作者工作台")

    def describe(self) -> list[str]:
        lines = [f"public_repo : {self.public_repo}"]
        lines.append(f"author_vault: {self.author_vault or '(未配置)'}")
        lines.append(f"runtime     : {self.runtime or '(未配置)'}")
        lines.append(f"project     : {self.project_id or '(未配置)'}")
        lines.append(f"config 来源 : {', '.join(self.sources) or '(仅默认值)'}")
        return lines


def _merge(base: dict, override: dict) -> dict:
    """浅合并两层以内的映射；列表整体替换。"""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _user_config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "novel-system" / "config.yaml"


# Vault_Config.yaml 允许两套写法：本仓库内部的 layout.*/paths.runtime，以及
# V4.0 §4.2 交付物里实际使用的 layouts.*/runtime_root。后者必须能读，
# 否则真实 Vault 的 runtime 永远解析不到，所有写入命令都无处落盘。
_LAYOUT_ALIASES = {
    "system": "vault_system_dir",
    "projects": "projects_dir",
    "shared_assets": "shared_assets_dir",
    "archive": "vault_archive_dir",
}


def _normalize_vault_config(raw: dict) -> dict:
    """把 V4.0 写法折算成内部约定。不覆盖作者已显式写的键。"""
    out: dict = {}
    layouts = raw.get("layouts")
    if isinstance(layouts, dict):
        layout = {
            _LAYOUT_ALIASES[key]: str(value)
            for key, value in layouts.items()
            if key in _LAYOUT_ALIASES and value
        }
        if layout:
            out["layout"] = layout
    if raw.get("runtime_root"):
        out["paths"] = {"runtime": str(raw["runtime_root"])}
    return out


def load(
    vault: str | None = None,
    runtime: str | None = None,
    public_repo: str | None = None,
    project: str | None = None,
) -> Config:
    """按优先级合并配置。不创建任何目录。"""
    sources: list[str] = []
    data: dict = read_yaml(DEFAULTS_PATH)
    if DEFAULTS_PATH.exists():
        sources.append("defaults")

    user_cfg = _user_config_path()
    user_data = read_yaml(user_cfg)
    if user_data:
        data = _merge(data, user_data)
        sources.append(str(user_cfg))

    # Vault 级配置：先确定 vault 路径才能读
    raw_vault = vault or os.environ.get(ENV_VAULT) or (user_data.get("paths") or {}).get("author_vault")
    vault_path: Path | None = None
    if raw_vault:
        vault_path = resolve_path(raw_vault)
        vault_cfg = vault_path / (data.get("layout") or {}).get("vault_system_dir", "00_作者工作台") / "Vault_Config.yaml"
        vault_data = read_yaml(vault_cfg)
        if vault_data:
            data = _merge(data, _normalize_vault_config(vault_data))
            data = _merge(data, vault_data)
            sources.append(str(vault_cfg))

    paths = data.get("paths") or {}

    repo_raw = public_repo or os.environ.get(ENV_PUBLIC_REPO) or paths.get("public_repo") or ""
    repo = resolve_path(repo_raw) if repo_raw else PACKAGE_ROOT

    runtime_raw = runtime or os.environ.get(ENV_RUNTIME) or paths.get("runtime") or ""
    runtime_path = resolve_path(runtime_raw) if runtime_raw else None

    project_id = project or (data.get("active_project") or {}).get("project_id") or ""

    author_id = str((data.get("author") or {}).get("author_id") or "author-local")

    return Config(
        public_repo=repo,
        author_vault=vault_path,
        runtime=runtime_path,
        project_id=project_id,
        privacy=dict(data.get("privacy") or {}),
        layout=dict(data.get("layout") or {}),
        sources=sources,
        author_id=author_id,
    )
