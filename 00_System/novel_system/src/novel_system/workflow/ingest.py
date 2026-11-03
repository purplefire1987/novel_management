"""录入流水线：原始 txt/md 稿件 → 规范化的章节大纲 + 正式正文。

定位：这是「已有稿件入库」的专用通道，不是写作通道。
- 正文视作作者已定稿 → manuscript.status = published，可直接进入 commit-memory 抽取实体。
- 大纲只做骨架占位（目标/冲突/POV 标为待补），由录入员或资料官补齐。
- 所有文件在**同一个事务**里落盘：全成功或全不写，批次可整体回滚。
- 每批次生成一份 `10_Change_Log/Ingest/{批次}.md`，记录来源哈希与章节清单，供追溯与查重。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .. import vault
from ..audit.logger import AuditLog
from ..config import Config
from ..database.sqlite import Index, now_iso
from ..gitops import GitError, create_tag
from ..transaction import Transaction, commit_artifacts
from .state_machine import WorkflowError

# 编码探测顺序：带 BOM 的 UTF-8 在前，其后覆盖中文常见编码
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "big5")

CN_NUM = "零〇一二三四五六七八九十百千两0-9"

CHAPTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^[\s　]*第[\s]*[{CN_NUM}]+[\s]*[章回节][\s]*(?P<title>.*)$"),
    re.compile(r"^[\s　]*[Cc]hapter[\s]+[0-9]+[\s]*[.:：]?[\s]*(?P<title>.*)$"),
)
HEADING_PATTERN = re.compile(r"^[\s　]{0,3}(?P<hashes>#{1,3})[\s]+(?P<title>.+?)[\s#]*$")
NUMBER_PATTERN = re.compile(r"^[\s　]*(?P<num>[0-9]{1,4})[\s]*[、.．][\s]*(?P<title>\S.*)$")

SPLIT_MODES = ("auto", "chapter", "heading", "number", "single")

INGEST_LOG_DIR = "10_Change_Log/Ingest"


# --------------------------------------------------------------------- 数据结构


@dataclass
class RawChapter:
    title: str
    text: str
    line_no: int = 0


@dataclass
class IngestResult:
    batch_id: str
    source: str
    source_sha256: str = ""
    encoding: str = ""
    volume_id: str = "VOL-0001"
    mode: str = "auto"
    chapters: list[dict[str, Any]] = field(default_factory=list)
    total_words: int = 0
    preamble_chars: int = 0
    preamble_excerpt: str = ""
    operation_id: str = ""
    log_path: str = ""
    git: dict[str, Any] = field(default_factory=dict)
    tag: str = ""
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "encoding": self.encoding,
            "volume_id": self.volume_id,
            "mode": self.mode,
            "chapters": self.chapters,
            "total_words": self.total_words,
            "preamble_chars": self.preamble_chars,
            "operation_id": self.operation_id,
            "log_path": self.log_path,
            "git": self.git,
            "tag": self.tag,
            "dry_run": self.dry_run,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------- 读与切章


def read_source(path: Path) -> tuple[str, str]:
    """按候选编码依次尝试解码，返回 (文本, 实际编码)。"""
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise WorkflowError(
        f"无法识别文件编码：{path}。请先转成 UTF-8 再录入（macOS 可用 `iconv -f GBK -t UTF-8`）。"
    )


def strip_frontmatter(text: str) -> tuple[str, dict[str, Any]]:
    """若原始稿本身是本系统的 Markdown 文档，剥掉它的 frontmatter，只取正文。"""
    if text.lstrip("\ufeff").startswith("---"):
        meta, body = vault.split_frontmatter(text.lstrip("\ufeff"))
        return body, meta
    return text, {}


def _clean_title(raw: str, fallback: str) -> str:
    text = (raw or "").strip().strip("　 \t")
    text = re.sub(r"^[、.．:：\-—_]+", "", text).strip()
    text = re.sub(r"[（(]\s*[)）]\s*$", "", text).strip()
    return text[:60] or fallback


def _hit_title(line: str, mode: str) -> str | None:
    """判断该行是否是章节标题行；是则返回标题文本（可能为空串）。"""
    if mode in ("chapter", "auto"):
        for pattern in CHAPTER_PATTERNS:
            match = pattern.match(line)
            if match:
                return match.group("title") or ""
    if mode in ("heading", "auto"):
        match = HEADING_PATTERN.match(line)
        if match:
            return match.group("title") or ""
    if mode == "number":
        match = NUMBER_PATTERN.match(line)
        if match:
            return match.group("title") or ""
    return None


def _resolve_mode(lines: list[str], mode: str) -> str:
    """auto 模式下探测最合适的切分方式。"""
    if mode != "auto":
        return mode
    for candidate in ("chapter", "heading", "number"):
        if any(_hit_title(line, candidate) is not None for line in lines):
            return candidate
    return "single"


def _heading_level(lines: list[str]) -> int:
    """Markdown 模式下取最小标题层级，避免把正文小标题误当章。"""
    levels = []
    for line in lines:
        match = HEADING_PATTERN.match(line)
        if match:
            levels.append(len(match.group("hashes")))
    return min(levels) if levels else 1


def split_chapters(
    text: str, mode: str = "auto", single_title: str = ""
) -> tuple[list[RawChapter], str, str]:
    """切章，返回 (章节列表, 实际模式, 首个标题前的前言文本)。

    首个章节标题之前的内容按「文件头」处理（书名、作者、卷名、简介），
    默认不并入第一章，由调用方决定丢弃或告知用户，避免污染正文。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    actual = _resolve_mode(lines, mode)

    if actual == "single":
        body = "\n".join(lines).strip("\n")
        title = single_title.strip() or _first_line_title(lines) or "未命名章节"
        return ([RawChapter(title=title, text=body, line_no=1)] if body.strip() else []), actual, ""

    head_level = _heading_level(lines) if actual == "heading" else 0
    chapters: list[RawChapter] = []
    title: str | None = None
    buffer: list[str] = []
    start_line = 0
    preamble: list[str] = []
    seen_heading = False

    def flush() -> None:
        nonlocal title, buffer
        if title is None and not any(l.strip() for l in buffer):
            return
        body = "\n".join(buffer).strip("\n")
        chapters.append(
            RawChapter(
                title=_clean_title(title or "", f"第 {len(chapters) + 1} 章"),
                text=body,
                line_no=start_line,
            )
        )
        title, buffer = None, []

    for index, line in enumerate(lines, start=1):
        hit: str | None = None
        if actual == "heading":
            match = HEADING_PATTERN.match(line)
            if match and len(match.group("hashes")) == head_level:
                hit = match.group("title") or ""
        else:
            hit = _hit_title(line, actual)

        if hit is not None:
            if not seen_heading:
                preamble = [l for l in buffer if l.strip()]
                seen_heading = True
                buffer = []
                title, start_line = hit, index
                continue
            flush()
            title = hit
            start_line = index
            continue
        buffer.append(line)

    flush()
    chapters = [c for c in chapters if c.text.strip() or c.title]
    return chapters, actual, "\n".join(preamble).strip()


def _first_line_title(lines: list[str]) -> str:
    for line in lines:
        text = line.strip().strip("#").strip()
        if text:
            return text[:60]
    return ""


# --------------------------------------------------------------------- 文档构建


def _volume_dir(volume_id: str) -> str:
    match = re.search(r"(\d+)", volume_id or "")
    number = int(match.group(1)) if match else 1
    return f"08_Manuscripts/Volume_{number:02d}"


def _next_chapter_serial(index: Index) -> int:
    serials: list[int] = []
    for doc_type in ("chapter_outline", "manuscript"):
        for row in index.entities_by_type(doc_type):
            match = re.search(r"CH-(\d{4})", str(row["id"]))
            if match:
                serials.append(int(match.group(1)))
    return (max(serials) if serials else 0) + 1


def _manuscript_body(text: str, batch_id: str, source_name: str) -> str:
    return "\n".join(
        [
            "## 正文",
            "",
            text.strip(),
            "",
            "---",
            "",
            "## 作者备注（不进入正文）",
            "",
            f"> 录入自 {source_name}（批次 {batch_id}）。",
            "> 补写 `- 事件: 名称 | 键:值` 形式的标注后，再跑 `novel commit-memory` 抽取实体。",
        ]
    )


def _outline_body(chapter_id: str, title: str, batch_id: str, word_count: int) -> str:
    return "\n".join(
        [
            f"# {chapter_id}｜{title}",
            "",
            f"> 由录入批次 `{batch_id}` 生成骨架，正文 {word_count} 字。目标/冲突/POV 待补。",
            "",
            "## 章节目标",
            "- 待补（录入员或资料官根据正文补写）",
            "",
            "## POV",
            "- 待确认",
            "",
            "## 时间与地点",
            "- 待确认",
            "",
            "## 核心冲突",
            "- 待确认",
            "",
            "## 必须发生的剧情事实",
            "- 待补",
            "",
            "## 禁止发生的内容",
            "- ",
            "",
            "## 角色出场",
            "- 待补（先跑 `novel commit-memory` 抽取，再回填实体 ID）",
            "",
            "## 关联伏笔",
            "- ",
            "",
            "## 场景列表",
            "1. 待拆分",
            "",
            "## 章节结尾钩子",
            "- 待补",
            "",
            "## 作者批准",
            "- [x] 已批准写作（既有稿件录入，视为作者已确认）",
        ]
    )


# --------------------------------------------------------------------- 查重


def _previous_sources(cfg: Config) -> dict[str, str]:
    """扫描历史录入日志，返回 {source_sha256: 批次号}。"""
    seen: dict[str, str] = {}
    directory = cfg.abs(INGEST_LOG_DIR)
    if not directory.is_dir():
        return seen
    for path in sorted(directory.glob("*.md")):
        try:
            doc = vault.read_doc(path)
        except Exception:  # noqa: BLE001 - 日志损坏不影响录入
            continue
        match = re.search(r"source_sha256:\s*([0-9a-f]{64})", doc.body)
        if match:
            seen[match.group(1)] = str(doc.meta.get("batch") or path.stem)
    return seen


# --------------------------------------------------------------------- 主流程


def run(
    cfg: Config,
    index: Index,
    source: str,
    *,
    volume_id: str = "VOL-0001",
    mode: str = "auto",
    title: str = "",
    dry_run: bool = False,
    tag: bool = False,
    force: bool = False,
) -> IngestResult:
    if mode not in SPLIT_MODES:
        raise WorkflowError(f"未知切分模式：{mode}（可选：{', '.join(SPLIT_MODES)}）")

    path = Path(source).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise WorkflowError(f"找不到原始文件：{path}")
    if not path.is_file():
        raise WorkflowError(f"不是文件：{path}")

    text, encoding = read_source(path)
    body, source_meta = strip_frontmatter(text)
    if source_meta.get("title") and not title:
        title = str(source_meta["title"])

    chapters, actual_mode, preamble = split_chapters(body, mode=mode, single_title=title)
    if not chapters:
        raise WorkflowError(f"{path.name} 没切出任何章节（文件可能是空的，或标题格式不匹配）。")

    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = _previous_sources(cfg)
    warnings: list[str] = []
    if sha in previous and not force:
        warnings.append(
            f"该文件内容已录入过（批次 {previous[sha]}）。继续会生成重复章节；确认要录请加 --force。"
        )
        raise WorkflowError(warnings[0])

    batch_id = f"INGEST-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    serial = _next_chapter_serial(index)
    today = date.today().isoformat()
    manuscript_dir = _volume_dir(volume_id)

    result = IngestResult(
        batch_id=batch_id,
        source=str(path),
        source_sha256=sha,
        encoding=encoding,
        volume_id=volume_id,
        mode=actual_mode,
        dry_run=dry_run,
        warnings=warnings,
    )
    if preamble:
        result.preamble_chars = vault.count_words(preamble)
        result.preamble_excerpt = preamble[:120]
        result.warnings.append(
            f"首个章节标题前有 {result.preamble_chars} 字文件头，已跳过不入正文：{result.preamble_excerpt}"
        )

    # 先算好全部变更，dry-run 与正式写入共用同一份结果
    staged: list[tuple[str, dict[str, Any], str]] = []
    for offset, chapter in enumerate(chapters):
        chapter_id = f"CH-{serial + offset:04d}"
        words = vault.count_words(chapter.text)
        chapter_title = chapter.title or f"{chapter_id}"
        outline_rel = f"04_Outline/Chapters/{chapter_id}.md"
        manuscript_rel = f"{manuscript_dir}/{chapter_id}.md"

        outline_meta = {
            "id": chapter_id,
            "type": "chapter_outline",
            "title": chapter_title,
            "name": chapter_title,
            "volume_id": volume_id,
            "status": "published",
            "canon_status": "confirmed",
            "created_at": today,
            "updated_at": today,
            "source_refs": [f"{INGEST_LOG_DIR}/{batch_id}"],
            "tags": ["ingested"],
        }
        manuscript_meta = {
            "id": chapter_id,
            "type": "manuscript",
            "title": chapter_title,
            "name": chapter_title,
            "volume_id": volume_id,
            "status": "published",
            "canon_status": "confirmed",
            "word_count": words,
            "outline_ref": chapter_id,
            "preflight_ref": "",
            "created_at": today,
            "updated_at": today,
            "source_refs": [f"{INGEST_LOG_DIR}/{batch_id}"],
            "tags": ["ingested"],
        }
        staged.append((outline_rel, outline_meta, _outline_body(chapter_id, chapter_title, batch_id, words)))
        staged.append(
            (manuscript_rel, manuscript_meta, _manuscript_body(chapter.text, batch_id, path.name))
        )
        result.chapters.append(
            {
                "chapter_id": chapter_id,
                "title": chapter_title,
                "word_count": words,
                "outline": outline_rel,
                "manuscript": manuscript_rel,
                "source_line": chapter.line_no,
            }
        )
        result.total_words += words

    if len(result.chapters) == 1 and result.total_words < 800:
        result.warnings.append(f"全文仅 {result.total_words} 字，确认切分是否成功（可能是标题格式不匹配）。")

    log_rel = f"{INGEST_LOG_DIR}/{batch_id}.md"
    if dry_run:
        return result

    tx = Transaction(
        cfg,
        index,
        actor="author",
        agent="intake_clerk",
        operation="ingest",
        target=batch_id,
    )
    for rel, meta, doc_body in staged:
        tx.stage_doc(rel, meta, doc_body)

    # 批次日志与正文同一事务落盘：rollback 时日志一起消失，不留假记录
    tx.stage_doc(log_rel, _log_meta(batch_id), _log_body(result, tx.op_id, path))

    commit = tx.commit(f"ingest {batch_id}（{len(result.chapters)} 章 / {result.total_words} 字）")
    result.operation_id = commit["operation_id"]
    result.git = commit.get("git") or {}
    result.log_path = log_rel

    if tag:
        try:
            tagged = create_tag(
                cfg,
                f"ingest/{batch_id}",
                f"录入批次 {batch_id}｜{len(result.chapters)} 章 / {result.total_words} 字｜{path.name}",
            )
            result.tag = tagged["tag"]
        except GitError as exc:
            result.warnings.append(f"打标签跳过：{exc}")

    AuditLog(cfg, index).record(
        operation="ingest",
        target=batch_id,
        agent="intake_clerk",
        actor="author",
        status="success",
        detail=(
            f"{path.name}｜{len(result.chapters)} 章｜{result.total_words} 字"
            f"｜mode={actual_mode}｜op={result.operation_id}"
        ),
        operation_id=result.operation_id,
    )
    commit_artifacts(cfg, f"ingest log {batch_id}")
    return result


def _log_meta(batch_id: str) -> dict[str, Any]:
    # 故意不带 id：这不是实体文档，只是变更日志，索引器会跳过它
    return {
        "batch": batch_id,
        "type": "ingest_log",
        "created_at": now_iso(),
    }


def _log_body(result: IngestResult, operation_id: str, path: Path) -> str:
    lines = [
        f"# 录入批次｜{result.batch_id}",
        "",
        "## 批次信息",
        f"- 来源文件：`{path.name}`",
        f"- 来源路径：`{path}`",
        f"- source_sha256: {result.source_sha256}",
        f"- 编码：{result.encoding}",
        f"- 切分模式：{result.mode}",
        f"- 目标卷：{result.volume_id}",
        f"- 章节数：{len(result.chapters)}",
        f"- 总字数：{result.total_words}",
        f"- 操作：`{operation_id}`",
        f"- 时间：{now_iso()}",
        "",
        "## 章节清单",
        "| 章节 | 标题 | 字数 | 正文 |",
        "|---|---|---|---|",
        *[
            f"| {c['chapter_id']} | {c['title']} | {c['word_count']} | {c['manuscript']} |"
            for c in result.chapters
        ],
        "",
        "## 回滚",
        f"`novel rollback {operation_id}` —— 会删除本批次创建的全部大纲、正文与本日志。",
        "",
        "## 后续动作",
        "1. `novel rebuild-index`（若未自动重建）",
        "2. 逐章 `novel commit-memory <章节>` 抽取事件与人物状态",
        "3. 资料官补齐章节大纲的目标 / 冲突 / POV",
    ]
    if result.tag:
        lines += ["", "## 版本快照", f"标签：`{result.tag}`"]
    if result.preamble_chars:
        lines += [
            "",
            "## 文件头（未录入正文）",
            f"首个章节标题前共 {result.preamble_chars} 字，内容摘录：",
            "",
            "> " + result.preamble_excerpt.replace("\n", "\n> "),
            "",
            "若这段是本卷简介或重要设定，请手工整理到 `04_Outline/` 或 `01_Core_Memory/`。",
        ]
    if result.warnings:
        lines += ["", "## 提示", *[f"- {w}" for w in result.warnings]]
    return "\n".join(lines)


# --------------------------------------------------------------------- 记忆标注


ANNOTATION_HEADING = "作者备注（不进入正文）"
# 与 agents/memory_manager.py 的 ANNOTATION_RE 保持一致，避免写了却抽不出
ANNOTATION_KINDS = ("事件", "状态", "关系", "伏笔", "摘要")


@dataclass
class AnnotateResult:
    chapter_id: str
    added: int
    total: int
    replaced: bool
    operation_id: str = ""
    git: dict[str, Any] = field(default_factory=dict)
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "added": self.added,
            "total": self.total,
            "replaced": self.replaced,
            "operation_id": self.operation_id,
            "git": self.git,
            "path": self.path,
        }


def _strip_annotation_heading(body: str) -> str:
    """去掉正文里的「作者备注」小节，返回干净的正文部分。"""
    marker = f"## {ANNOTATION_HEADING}"
    index = body.find(marker)
    head = body[:index] if index >= 0 else body
    head = head.rstrip()
    head = re.sub(r"\n-{3,}\s*$", "", head).rstrip()
    return head


def normalize_annotations(lines: list[str]) -> list[str]:
    """规整标注行：统一成 `- 类型: 内容`，并拒绝无法识别类型的行。"""
    out: list[str] = []
    for raw in lines:
        text = (raw or "").strip()
        if not text or text == "-":
            continue
        text = text.lstrip("-*").strip()
        head = re.split(r"[:：]", text, maxsplit=1)[0].strip()
        if head not in ANNOTATION_KINDS:
            raise WorkflowError(
                f"无法识别的标注类型「{head}」：{text[:40]}。"
                f"允许：{'/'.join(ANNOTATION_KINDS)}，格式 `- 事件: 名称 | 键:值 | ...`"
            )
        out.append(f"- {text}")
    return out


def annotate(
    cfg: Config,
    index: Index,
    chapter_id: str,
    lines: list[str],
    *,
    replace: bool = False,
    actor: str = "author",
) -> AnnotateResult:
    """给已发布的章节补写「作者备注」结构化标注，让记忆抽取可离线运行。

    这是录入员的核心动作：原始稿件没有标注，抽取不出来事实，
    必须先按系统格式补上事件/状态/伏笔/摘要，才能走 commit-memory。
    """
    from ..memory import retrieval

    doc = retrieval.manuscript_doc(cfg, index, chapter_id)
    if not doc:
        raise WorkflowError(f"找不到正式正文：{chapter_id}（先 publish 或 ingest）")
    if not doc.path.exists():
        raise WorkflowError(f"正文文件不存在：{doc.path}")

    incoming = normalize_annotations(lines)
    if not incoming:
        raise WorkflowError("没有可写入的标注行")

    existing_section = vault.extract_section(doc.body, ANNOTATION_HEADING)
    existing = [
        line.strip()
        for line in existing_section.splitlines()
        if line.strip().startswith("-") and line.strip() != "-"
    ]
    merged = incoming if replace else existing + [l for l in incoming if l not in existing]

    if not replace and merged == existing:
        raise WorkflowError("这些标注已存在，无需重复写入")

    body = _strip_annotation_heading(doc.body)
    new_body = f"{body}\n\n---\n\n## {ANNOTATION_HEADING}\n" + "\n".join(merged) + "\n"

    meta = dict(doc.meta)
    meta["updated_at"] = date.today().isoformat()
    meta["annotation_count"] = len(merged)

    tx = Transaction(
        cfg,
        index,
        actor=actor,
        agent="intake_clerk",
        operation="annotate",
        target=chapter_id,
    )
    tx.stage_doc(cfg.rel(doc.path), meta, new_body)
    commit = tx.commit(
        f"annotate {chapter_id}（+{len(merged) - len(existing) if not replace else len(merged)} 条）"
    )

    AuditLog(cfg, index).record(
        operation="annotate",
        target=chapter_id,
        agent="intake_clerk",
        actor=actor,
        status="success",
        detail=f"标注 {len(merged)} 条｜replaced={replace}｜op={commit['operation_id']}",
        operation_id=commit["operation_id"],
    )
    commit_artifacts(cfg, f"annotate {chapter_id}")

    return AnnotateResult(
        chapter_id=chapter_id,
        added=len(merged) - len(existing) if not replace else len(merged),
        total=len(merged),
        replaced=replace,
        operation_id=commit["operation_id"],
        git=commit.get("git") or {},
        path=cfg.rel(doc.path),
    )
