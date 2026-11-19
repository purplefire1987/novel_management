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
        return self.require_vault() / self.layout.get("projects_dir", "10_Projects")

    def project_root(self, project_id: str | None = None) -> Path:
        pid = project_id or self.require_project()
        vault = self.require_vault()
        base = vault / self.layout.get("projects_dir", "10_Projects")
        matches = sorted(p for p in base.glob(f"{pid}*") if p.is_dir()) if base.exists() else []
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ConfigError(f"项目 ID {pid} 命中多个目录：{[m.name for m in matches]}")
        return base / pid

    def runtime_dir(self, *parts: str) -> Path:
        return self.require_runtime().joinpath(*parts)

    def vault_system_dir(self) -> Path:
        return self.require_vault() / self.layout.get("vault_system_dir", "00_System")

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
        vault_cfg = vault_path / (data.get("layout") or {}).get("vault_system_dir", "00_System") / "Vault_Config.yaml"
        vault_data = read_yaml(vault_cfg)
        if vault_data:
            data = _merge(data, vault_data)
            sources.append(str(vault_cfg))

    paths = data.get("paths") or {}

    repo_raw = public_repo or os.environ.get(ENV_PUBLIC_REPO) or paths.get("public_repo") or ""
    repo = resolve_path(repo_raw) if repo_raw else PACKAGE_ROOT

    runtime_raw = runtime or os.environ.get(ENV_RUNTIME) or paths.get("runtime") or ""
    runtime_path = resolve_path(runtime_raw) if runtime_raw else None

    project_id = project or (data.get("active_project") or {}).get("project_id") or ""

    return Config(
        public_repo=repo,
        author_vault=vault_path,
        runtime=runtime_path,
        project_id=project_id,
        privacy=dict(data.get("privacy") or {}),
        layout=dict(data.get("layout") or {}),
        sources=sources,
    )
