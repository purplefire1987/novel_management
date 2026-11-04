"""旧稿素材录入：Excel 资产库 → 分类实体文档；Markdown 设定稿 → 底层记忆文档。

设计原则（与 `workflow/ingest.py` 的稿件录入并列，互不替代）：

- **搬运而非创作**：原文照录，不改写、不润色、不补设定。
- **分类靠配置**：工作表 → 目录 / 类型 / ID 前缀的映射写在
  `00_System/Config/import_profiles.yaml`，代码里不写死任何一部作品的字段。
- **全批次一个事务**：校验 → 备份 → 原子替换 → 索引 → 审计 → Git；
  任一步失败整批回滚，不留半套资产。
- **可对账**：批次日志落在 `10_Change_Log/Ingest/`，记录来源哈希、映射规则、
  跳过行与同名冲突，回滚后日志一起消失，不留假记录。
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .. import vault
from ..audit.logger import AuditLog
from ..config import Config
from ..database.sqlite import Index
from ..gitops import GitError, create_tag
from ..transaction import Transaction, commit_artifacts
from .state_machine import WorkflowError

INGEST_LOG_DIR = "10_Change_Log/Ingest"
PROFILE_FILE = "import_profiles.yaml"

# 索引器的排除目录，这里也一并跳过：它们不是事实源
SKIP_TOP_DIRS = {"00_System", "99_Archive", "11_Templates", ".novel_system", ".obsidian", ".git"}

# 标题归一化：去掉空白、井号、间隔号与中英文引号，只用于边界匹配，不用于改写原文
_NORM_RE = re.compile(r"[\s\u3000#·“”「」『』\"'`]+")

LINK_SPLIT_RE = re.compile(r"[,，、;；/]+")


# --------------------------------------------------------------------- 结果结构


@dataclass
class ImportResult:
    batch_id: str
    profile: str
    kind: str
    world: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    files: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    samples: list[dict[str, str]] = field(default_factory=list)
    operation_id: str = ""
    log_path: str = ""
    git: dict[str, Any] = field(default_factory=dict)
    tag: str = ""
    dry_run: bool = False

    def total(self) -> int:
        return sum(self.counts.values())


# --------------------------------------------------------------------- 配置读取


def load_profiles(cfg: Config) -> dict[str, Any]:
    path = cfg.vault / "00_System" / "Config" / PROFILE_FILE
    if not path.exists():
        raise WorkflowError(f"缺少录入配置：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("profiles"), dict) or not data["profiles"]:
        raise WorkflowError(f"{path} 中没有定义任何 profile")
    return data["profiles"]


def get_profile(cfg: Config, name: str) -> dict[str, Any]:
    profiles = load_profiles(cfg)
    if name not in profiles:
        raise WorkflowError(
            f"未知 profile：{name}（可用：{', '.join(sorted(profiles))}）"
        )
    return profiles[name]


# --------------------------------------------------------------------- 既有资产


class NameIndex:
    """名称 → 文档引用。用于把「关联：角色」这类列渲染成 Obsidian 双链。"""

    def __init__(self) -> None:
        self._exact: dict[tuple[str, str], dict[str, str]] = {}
        self._loose: dict[str, dict[str, str]] = {}
        self._ambiguous: set[str] = set()

    def add(self, name: str, doc_type: str, doc_id: str, stem: str) -> None:
        key = name.strip()
        if not key:
            return
        entry = {"id": doc_id, "stem": stem, "name": key}
        self._exact[(doc_type, key)] = entry
        if key in self._loose and self._loose[key]["id"] != doc_id:
            self._ambiguous.add(key)
        else:
            self._loose.setdefault(key, entry)

    def lookup(self, name: str, prefer_type: str | None = None) -> dict[str, str] | None:
        key = name.strip()
        if not key:
            return None
        if prefer_type and (prefer_type, key) in self._exact:
            return self._exact[(prefer_type, key)]
        if key in self._ambiguous:
            return None
        return self._loose.get(key)

    def render(self, value: str, prefer_type: str | None = None) -> str:
        """把「帝室,项家」渲染成「[[FAC-0003_帝室|帝室]]、项家」。"""
        parts: list[str] = []
        for raw in LINK_SPLIT_RE.split(value):
            token = raw.strip()
            if not token:
                continue
            hit = self.lookup(token, prefer_type)
            parts.append(f"[[{hit['stem']}|{hit['name']}]]" if hit else token)
        return "、".join(parts)


def scan_existing(cfg: Config) -> tuple[dict[str, int], NameIndex]:
    """扫描 Vault 现有 Markdown，返回 (各前缀最大序号, 名称索引)。"""
    max_serial: dict[str, int] = {}
    names = NameIndex()
    for path in vault.iter_markdown(cfg.vault):
        rel = cfg.rel(path)
        if rel.split("/")[0] in SKIP_TOP_DIRS:
            continue
        try:
            meta, _ = vault.split_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 单个坏文件不影响录入
            continue
        doc_id = str(meta.get("id") or "")
        match = re.fullmatch(r"([A-Z]+(?:-[A-Z]+)?)-(\d{3,4})", doc_id)
        if not match:
            continue
        prefix, serial = match.group(1), int(match.group(2))
        max_serial[prefix] = max(max_serial.get(prefix, 0), serial)
        name = str(meta.get("name") or meta.get("title") or "")
        if name:
            names.add(name, str(meta.get("type") or ""), doc_id, path.stem)
    return max_serial, names


class IdAllocator:
    """按前缀顺序发号；与 `ids.py` 同规则，但支持一次性批量分配（不依赖索引）。"""

    def __init__(self, cfg: Config, max_serial: dict[str, int]):
        self.prefixes: dict[str, str] = dict(cfg.id_rules.get("prefixes") or {})
        self.width = int(cfg.id_rules.get("serial_width", 4))
        self._max = dict(max_serial)

    def next(self, doc_type: str) -> str:
        prefix = self.prefixes.get(doc_type)
        if not prefix:
            raise WorkflowError(f"id_rules.yaml 未定义前缀：{doc_type}")
        self._max[prefix] = self._max.get(prefix, 0) + 1
        return f"{prefix}-{self._max[prefix]:0{self.width}d}"


# --------------------------------------------------------------------- 文本工具


def safe_filename(name: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|#\[\]\r\n\t]+', "_", name.strip())
    text = re.sub(r"\s+", "_", text).strip("_.")
    return text[:limit] or "untitled"


def _cell(row: Sequence[Any], index: dict[str, int], column: str | None) -> str:
    if not column:
        return ""
    if column not in index:
        return ""
    pos = index[column]
    if pos >= len(row):
        return ""
    value = row[pos]
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _inline(text: str) -> str:
    """单行字段：折叠换行与多余空白，避免破坏列表/表格结构。"""
    return re.sub(r"\s+", " ", text).strip()


def _prose(text: str) -> str:
    """长文本字段：保留分段，但压缩多余空行与行首尾空白。"""
    lines = [line.strip() for line in str(text).replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(line)
    return "\n".join(out).strip()


def _md_cell(text: str) -> str:
    return _inline(text).replace("|", "｜")


def _today() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------- 元数据


def _meta(
    doc_id: str,
    doc_type: str,
    name: str,
    *,
    profile: dict[str, Any],
    batch_id: str,
    log_rel: str,
    subtype: str = "",
    tags: Iterable[str] = (),
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tag_list = list(dict.fromkeys([*profile.get("tags", []), *tags]))
    meta: dict[str, Any] = {
        "id": doc_id,
        "type": doc_type,
        "name": name,
        "title": name,
        "aliases": [],
        "subtype": subtype or doc_type,
        "world": profile.get("world", ""),
        "canon_status": "confirmed",
        "created_at": _today(),
        "updated_at": _today(),
        "source_refs": [log_rel],
        "import_batch": batch_id,
        "tags": tag_list,
    }
    if extra:
        meta.update(extra)
    return meta


# --------------------------------------------------------------------- 实体文档


def _entity_body(
    name: str,
    rule: dict[str, Any],
    row: Sequence[Any],
    headers: list[str],
    names: NameIndex,
    source_label: str,
    batch_id: str,
) -> str:
    index = {h: i for i, h in enumerate(headers)}
    links: dict[str, str] = dict(rule.get("links") or {})
    lines = [
        f"# {name}",
        "",
        (
            f"> 来源：{source_label} · 工作表「{rule['sheet']}」｜录入批次 `{batch_id}`｜"
            "旧稿资产，如与新正文冲突，以正文为准。"
        ),
        "",
    ]

    summary = _prose(_cell(row, index, rule.get("summary_column")))
    if summary:
        lines += ["## 概要", "", summary, ""]

    fields = [f for f in rule.get("fields", []) if f not in links]
    details = [
        f"- **{column}**：{_inline(_cell(row, index, column))}"
        for column in fields
        if _cell(row, index, column)
    ]
    if details:
        lines += ["## 档案", "", *details, ""]

    relations = []
    for column, entity_type in links.items():
        value = _cell(row, index, column)
        if not value:
            continue
        rendered = names.render(value, entity_type)
        if rendered:
            relations.append(f"- **{column}**：{rendered}")
    if relations:
        lines += ["## 关联", "", *relations, ""]

    return "\n".join(lines)


def _table_body(
    title: str,
    rule: dict[str, Any],
    rows: list[Sequence[Any]],
    headers: list[str],
    source_label: str,
    batch_id: str,
) -> str:
    index = {h: i for i, h in enumerate(headers)}
    columns: list[str] = list(rule.get("columns") or [])
    layout = str(rule.get("layout") or "table")
    lines = [
        f"# {title}",
        "",
        (
            f"> 来源：{source_label} · 工作表「{rule['sheet']}」｜录入批次 `{batch_id}`｜"
            "旧稿资产，如原文与规则书表述冲突，以规则书为准。"
        ),
        "",
    ]
    lead = _prose(str(rule.get("lead") or ""))
    if lead:
        lines += ["## 用途", "", lead, ""]

    if layout == "sections":
        name_column = str(rule.get("name_column") or "名称")
        for row in rows:
            row_name = _cell(row, index, name_column)
            if not row_name:
                continue
            lines += [f"## {row_name}", ""]
            for column in columns or [h for h in headers if h != name_column]:
                value = _prose(_cell(row, index, column))
                if value:
                    lines.append(f"- **{column}**：{_inline(value)}")
            lines.append("")
    else:
        shown = columns or headers
        lines.append("| " + " | ".join(_md_cell(c) for c in shown) + " |")
        lines.append("| " + " | ".join("---" for _ in shown) + " |")
        for row in rows:
            lines.append("| " + " | ".join(_md_cell(_cell(row, index, c)) for c in shown) + " |")
        lines.append("")

    lines.append(f"> 共 {len(rows)} 条记录。")
    return "\n".join(lines)


# --------------------------------------------------------------------- 批次日志


def _previous_sources(cfg: Config) -> dict[str, str]:
    """读取历史批次日志，返回 {(profile, sha256): 批次号}，用于重复录入拦截。"""
    seen: dict[str, str] = {}
    directory = cfg.abs(INGEST_LOG_DIR)
    if not directory.is_dir():
        return seen
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        batch = path.stem
        profile = ""
        for line in text.splitlines():
            if line.startswith("profile:"):
                profile = line.split(":", 1)[1].strip()
                continue
            match = re.match(r"source_sha256:\s*([0-9a-f]{64})", line.strip())
            if match and profile:
                seen[f"{profile}|{match.group(1)}"] = batch
    return seen


def _log_body(result: ImportResult, operation_id: str) -> str:
    lines = [
        f"# 素材录入批次 {result.batch_id}",
        "",
        f"- profile：`{result.profile}`（{result.kind}）",
        f"- 作品：{result.world}",
        f"- 操作号：`{operation_id}`",
        f"- 写入文件：{result.files}",
        "",
        "## 来源文件",
        "",
        "| 文件 | 大小 | sha256 |",
        "| --- | --- | --- |",
    ]
    for source in result.sources:
        lines.append(f"| {source['name']} | {source['bytes']} B | `{source['sha256']}` |")
    lines += ["", "## 分类结果", "", "| 类别 | 数量 |", "| --- | --- |"]
    for label, count in result.counts.items():
        lines.append(f"| {label} | {count} |")
    lines.append(f"| **合计** | **{result.total()}** |")
    if result.skipped:
        lines += ["", "## 跳过项", "", *[f"- {item}" for item in result.skipped]]
    if result.warnings:
        lines += ["", "## 提示", "", *[f"- {item}" for item in result.warnings]]
    lines += [
        "",
        "## 机器可读",
        "",
        "```",
        f"profile: {result.profile}",
        *[f"source_sha256: {source['sha256']}" for source in result.sources],
        "```",
        "",
        "## 回滚",
        "",
        "```bash",
        f"novel rollback {operation_id}",
        "```",
        "",
        "> 本批次为旧稿搬运：只做分类、编号与建链，未改写任何原文。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------- 归档原件


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_sources(cfg: Config, profile: dict[str, Any], paths: list[Path]) -> list[str]:
    """把原件复制到归档目录（二进制安全，按内容哈希判断是否已归档）。"""
    archive_dir = str(profile.get("archive_dir") or "99_Archive/Raw_Sources")
    saved: list[str] = []
    for path in paths:
        target = cfg.abs(archive_dir) / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and _sha256_file(target) == _sha256_file(path):
            saved.append(cfg.rel(target))
            continue
        shutil.copy2(path, target)
        saved.append(cfg.rel(target))
    return saved


# --------------------------------------------------------------------- 主流程


def _stage_and_commit(
    cfg: Config,
    index: Index,
    result: ImportResult,
    staged: list[tuple[str, dict[str, Any], str]],
    *,
    operation: str,
    tag: bool,
    message: str,
) -> None:
    result.files = len(staged)
    result.log_path = f"{INGEST_LOG_DIR}/{result.batch_id}.md"
    if result.dry_run:
        return

    tx = Transaction(
        cfg,
        index,
        actor="author",
        agent="intake_clerk",
        operation=operation,
        target=result.batch_id,
    )
    for rel, meta, body in staged:
        tx.stage_doc(rel, meta, body)
    tx.stage_doc(result.log_path, {"batch": result.batch_id, "profile": result.profile}, _log_body(result, tx.op_id))

    commit = tx.commit(message)
    result.operation_id = commit["operation_id"]
    result.git = commit.get("git") or {}

    if tag:
        try:
            tagged = create_tag(cfg, f"import/{result.batch_id}", message)
            result.tag = tagged["tag"]
        except GitError as exc:  # 标签失败不影响已落盘内容
            result.warnings.append(f"打标签跳过：{exc}")

    AuditLog(cfg, index).record(
        operation=operation,
        target=result.batch_id,
        agent="intake_clerk",
        actor="author",
        status="success",
        detail=(
            f"profile={result.profile}｜{result.total()} 条｜{result.files} 文件"
            f"｜op={commit['operation_id']}"
        ),
        operation_id=commit["operation_id"],
    )
    commit_artifacts(cfg, f"import log {result.batch_id}")


def _check_repeat(cfg: Config, result: ImportResult, force: bool) -> None:
    seen = _previous_sources(cfg)
    for source in result.sources:
        key = f"{result.profile}|{source['sha256']}"
        if key in seen and not force:
            raise WorkflowError(
                f"{source['name']} 已用同一 profile 录入过（批次 {seen[key]}）。"
                "确认要重录请加 --force。"
            )


def import_workbook(
    cfg: Config,
    index: Index,
    profile_name: str,
    *,
    source: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    archive: bool = True,
    tag: bool = False,
) -> ImportResult:
    """Excel 资产库 → 分类实体文档 + 总表文档。"""
    profile = get_profile(cfg, profile_name)
    if str(profile.get("kind")) != "workbook":
        raise WorkflowError(f"profile {profile_name} 不是 workbook 类型")

    path = Path(source or profile.get("source") or "").expanduser()
    if not path.is_absolute():
        path = (cfg.vault / path).resolve()
    if not path.exists():
        raise WorkflowError(f"找不到资产文件：{path}")

    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时给可执行的指引
        raise WorkflowError(
            "缺少 openpyxl：请先执行 `pip install openpyxl`（或 `pip install -e 00_System/novel_system[xlsx]`）"
        ) from exc

    batch_id = f"ASSETS-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    result = ImportResult(
        batch_id=batch_id,
        profile=profile_name,
        kind="workbook",
        world=str(profile.get("world") or ""),
        dry_run=dry_run,
    )
    result.sources = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    ]
    _check_repeat(cfg, result, force)

    workbook = load_workbook(path, data_only=True)
    max_serial, names = scan_existing(cfg)
    allocator = IdAllocator(cfg, max_serial)
    source_label = f"`{path.name}`"

    staged: list[tuple[str, dict[str, Any], str]] = []

    # 先登记实体表的全部行，保证同一批次内的「关联」列能互相建链
    plan: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}
    name_counts: dict[tuple[str, str], int] = {}
    for rule in profile.get("entities") or []:
        sheet = str(rule["sheet"])
        if sheet not in workbook.sheetnames:
            result.skipped.append(f"工作表「{sheet}」不存在，已跳过")
            continue
        headers, rows = _read_sheet(workbook[sheet])
        identity = {h: i for i, h in enumerate(headers)}
        label = str(rule.get("subtype") or rule.get("type"))
        count = 0
        for row in rows:
            name = _cell(row, identity, rule.get("name_column"))
            if not name:
                result.skipped.append(f"{sheet}：有 1 行缺少「{rule.get('name_column')}」，已跳过")
                continue
            doc_type = str(rule["type"])
            previous = seen.get((doc_type, name))
            doc_id = allocator.next(doc_type)
            stem = f"{doc_id}_{safe_filename(name)}"
            if previous:
                name_counts[(doc_type, name)] = name_counts.get((doc_type, name), 1) + 1
                stem = f"{stem}_{name_counts[(doc_type, name)]}"
                result.warnings.append(
                    f"{sheet}：「{name}」与 {previous} 同名，已按 `{stem}` 单独建档，请作者确认是否合并"
                )
            rel = f"{rule['dir']}/{stem}.md"
            seen.setdefault((doc_type, name), doc_id)
            names.add(name, doc_type, doc_id, stem)
            plan.append({"rule": rule, "row": row, "headers": headers, "id": doc_id, "rel": rel, "name": name})
            count += 1
        result.counts[label] = result.counts.get(label, 0) + count

    for item in plan:
        rule = item["rule"]
        meta = _meta(
            item["id"],
            str(rule["type"]),
            item["name"],
            profile=profile,
            batch_id=batch_id,
            log_rel=f"{INGEST_LOG_DIR}/{batch_id}",
            subtype=str(rule.get("subtype") or ""),
            tags=[str(rule["sheet"])],
        )
        body = _entity_body(
            item["name"], rule, item["row"], item["headers"], names, source_label, batch_id
        )
        staged.append((item["rel"], meta, body))
        if len(result.samples) < 5:
            result.samples.append({"path": item["rel"], "head": body[:400]})

    # 总表：整表一份文档，或按 group_by 拆成多份
    for rule in profile.get("tables") or []:
        sheet = str(rule["sheet"])
        if sheet not in workbook.sheetnames:
            result.skipped.append(f"工作表「{sheet}」不存在，已跳过")
            continue
        headers, rows = _read_sheet(workbook[sheet])
        groups: list[tuple[str, list[Sequence[Any]]]] = []
        group_by = rule.get("group_by")
        if group_by:
            identity = {h: i for i, h in enumerate(headers)}
            bucket: dict[str, list[Sequence[Any]]] = {}
            for row in rows:
                key = _cell(row, identity, str(group_by)) or "未分类"
                bucket.setdefault(key, []).append(row)
            groups = list(bucket.items())
        else:
            groups = [("", rows)]

        label = str(rule.get("title") or sheet)
        for group, group_rows in groups:
            doc_id = allocator.next("world")
            title = f"{rule['title']}·{group}" if group else str(rule["title"])
            stem = f"{doc_id}_{safe_filename(str(rule['filename']))}"
            if group:
                stem = f"{stem}_{safe_filename(group)}"
            rel = f"{rule['dir']}/{stem}.md"
            meta = _meta(
                doc_id,
                "world",
                title,
                profile=profile,
                batch_id=batch_id,
                log_rel=f"{INGEST_LOG_DIR}/{batch_id}",
                subtype=str(group or sheet),
                tags=["总表", sheet],
            )
            body = _table_body(title, rule, list(group_rows), headers, source_label, batch_id)
            staged.append((rel, meta, body))
            key = f"{label}" if not group else f"{label}·{group}"
            result.counts[key] = len(group_rows)
            if len(result.samples) < 5:
                result.samples.append({"path": rel, "head": body[:400]})

    if not staged:
        raise WorkflowError("没有解析出任何可录入的内容，请检查 profile 的工作表名与列名")

    if archive and not dry_run:
        result.sources[0]["archived_as"] = ",".join(archive_sources(cfg, profile, [path]))

    _stage_and_commit(
        cfg,
        index,
        result,
        staged,
        operation="import_assets",
        tag=tag,
        message=f"import assets {batch_id}（{profile_name} / {result.total()} 条）",
    )
    return result


def _read_sheet(sheet: Any) -> tuple[list[str], list[tuple[Any, ...]]]:
    """读工作表：返回 (表头, 数据行)。跳过全空行与空表头列。"""
    raw = list(sheet.iter_rows(values_only=True))
    header_at: int | None = None
    for pos, row in enumerate(raw):
        if row and any(value is not None and str(value).strip() for value in row):
            header_at = pos
            break
    if header_at is None:
        return [], []
    headers = [str(value).strip() if value is not None else "" for value in raw[header_at]]
    keep = [i for i, head in enumerate(headers) if head]
    headers = [headers[i] for i in keep]
    rows: list[tuple[Any, ...]] = []
    for row in raw[header_at + 1 :]:
        if not row or not any(value is not None and str(value).strip() for value in row):
            continue
        rows.append(tuple(row[i] if i < len(row) else None for i in keep))
    return headers, rows


# --------------------------------------------------------------------- 底层记忆


def _norm_heading(text: str) -> str:
    return _NORM_RE.sub("", text.strip())


_ANCHOR_NORM_RE = re.compile(r"[\s\u3000#*·“”「」『』\"'`]+")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*$")


def _anchor_text(text: str) -> str:
    """锚点归一：去掉 #、*、空白与引号，让 `# **政治**` 与 `**政治**` 都能对齐。"""
    return _ANCHOR_NORM_RE.sub("", text.strip())


def _anchor_key(line: str) -> str | None:
    """一行的锚点文本；不是锚点返回 None。

    锚点有两种：Markdown 标题行，以及独占一行的加粗小标题（`**帝国的科技**`）。
    后者在设定稿里承担二级标题的作用，但语法上不是标题，只按 `#` 切会切不开。
    """
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("#"):
        return _anchor_text(stripped)
    match = _BOLD_LINE_RE.match(stripped)
    if match:
        return _anchor_text(match.group(1))
    return None


def _find_anchor(lines: list[str], target: str, begin: int = 0) -> int | None:
    """先找完全相同的锚点，再退回前缀匹配（锚点只给前半截时用）。"""
    for i in range(begin, len(lines)):
        key = _anchor_key(lines[i])
        if key is not None and key == target:
            return i
    for i in range(begin, len(lines)):
        key = _anchor_key(lines[i])
        if key is not None and key.startswith(target):
            return i
    return None


def _slice(text: str, start: str, end: str | None) -> tuple[str, int, int]:
    lines = text.splitlines()
    start_idx = _find_anchor(lines, _anchor_text(start))
    if start_idx is None:
        raise WorkflowError(f"在原文中找不到起始标题：{start}")

    end_idx = len(lines)
    if end:
        found = _find_anchor(lines, _anchor_text(end), begin=start_idx + 1)
        if found is None:
            raise WorkflowError(f"在原文中找不到结束标题：{end}")
        end_idx = found

    return "\n".join(lines[start_idx + 1 : end_idx]).strip("\n"), start_idx + 1, end_idx


def _toc(section: str) -> list[str]:
    items: list[str] = []
    for line in section.splitlines():
        match = re.match(r"^(#{2,3})\s+(.+?)\s*#*$", line)
        if not match:
            continue
        depth = len(match.group(1)) - 2
        title = re.sub(r"[*`]", "", match.group(2)).strip()
        items.append(("  " * depth) + f"- {title}")
    return items


def _lore_body(
    title: str,
    section: str,
    *,
    lead: str,
    source_label: str,
    batch_id: str,
    start: str,
    end: str | None,
) -> str:
    lines = [
        f"# {title}",
        "",
        (
            f"> 来源：{source_label} ｜区间「{start}」→「{end or '文末'}」｜"
            f"录入批次 `{batch_id}`｜原文照录，未改写、未补写。"
        ),
        "",
    ]
    if lead:
        lines += ["## 一句话定位", "", lead, ""]
    toc = _toc(section)
    if toc:
        lines += ["## 本页目录", "", *toc, ""]
    lines += ["---", "", section, ""]
    return "\n".join(lines)


def import_lore(
    cfg: Config,
    index: Index,
    profile_name: str,
    *,
    root: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    archive: bool = True,
    tag: bool = False,
) -> ImportResult:
    """Markdown 设定稿 → 底层记忆（world）文档。"""
    profile = get_profile(cfg, profile_name)
    if str(profile.get("kind")) != "lore":
        raise WorkflowError(f"profile {profile_name} 不是 lore 类型")

    base = Path(root or profile.get("root") or ".").expanduser()
    if not base.is_absolute():
        # 相对路径一律以 Vault 根为基准：profile 是 Vault 内的配置，不该依赖调用者的 cwd
        base = (cfg.vault / base).resolve()

    batch_id = f"LORE-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    result = ImportResult(
        batch_id=batch_id,
        profile=profile_name,
        kind="lore",
        world=str(profile.get("world") or ""),
        dry_run=dry_run,
    )

    entries = profile.get("documents") or []
    if not entries:
        raise WorkflowError(f"profile {profile_name} 没有配置 documents")

    sources: list[Path] = []
    for entry in entries:
        path = (base / str(entry["source"])).resolve()
        if not path.exists():
            raise WorkflowError(f"找不到设定稿：{path}")
        sources.append(path)
        result.sources.append(
            {"name": str(entry["source"]), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        )
    _check_repeat(cfg, result, force)

    max_serial, _ = scan_existing(cfg)
    allocator = IdAllocator(cfg, max_serial)
    staged: list[tuple[str, dict[str, Any], str]] = []

    for entry, path in zip(entries, sources):
        text = path.read_text(encoding="utf-8")
        target_dir = str(entry.get("dir") or profile.get("dir") or "01_Core_Memory/World")
        for spec in entry.get("docs") or []:
            title = str(spec["title"])
            section, line_from, line_to = _slice(text, str(spec["start"]), spec.get("end"))
            if not section.strip():
                result.skipped.append(f"{title}：切出的区间为空，已跳过")
                continue
            doc_id = allocator.next("world")
            stem = f"{doc_id}_{safe_filename(str(spec.get('filename') or title))}"
            rel = f"{target_dir}/{stem}.md"
            meta = _meta(
                doc_id,
                "world",
                title,
                profile=profile,
                batch_id=batch_id,
                log_rel=f"{INGEST_LOG_DIR}/{batch_id}",
                subtype="底层记忆",
                tags=["底层记忆", "世界规则"],
            )
            body = _lore_body(
                title,
                section,
                lead=str(spec.get("lead") or ""),
                source_label=f"`{entry['source']}`",
                batch_id=batch_id,
                start=str(spec["start"]),
                end=spec.get("end"),
            )
            staged.append((rel, meta, body))
            result.counts["底层记忆"] = result.counts.get("底层记忆", 0) + 1
            if len(result.samples) < 5:
                result.samples.append({"path": rel, "head": body[:400]})

    if not staged:
        raise WorkflowError("没有切出任何底层记忆文档，请检查标题区间配置")

    if archive and not dry_run:
        archived = archive_sources(cfg, profile, sources)
        for source, saved in zip(result.sources, archived):
            source["archived_as"] = saved

    _stage_and_commit(
        cfg,
        index,
        result,
        staged,
        operation="import_lore",
        tag=tag,
        message=f"import lore {batch_id}（{profile_name} / {len(staged)} 篇）",
    )
    return result
