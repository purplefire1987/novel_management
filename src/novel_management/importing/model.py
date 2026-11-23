"""导入的数据结构与定位符。

## 定位符（locator）

格式：`<导入内相对文件名>#L<起行>-L<止行>`，单行时 `#L12`。

它必须**能被反查回原始字节**——PRD §12.4 要求「发布候选的来源引用 100% 可解析」。
所以定位符里只出现文件名与行号，不出现解析后的偏移、块 ID 之类需要重算的东西：
行号是原始文件的坐标，哪怕解析逻辑将来改了，`#L12-L18` 依然指向同一段文字。

## 为什么定位符不带章号

章号是**解析结果**，不是原稿的属性。同一份 txt 换一次切分规则，章号就全变了，
而 `#L12-L18` 不会。来源引用一旦指向会变的东西，就不叫来源引用了。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.errors import UsageError

LOCATOR_RE = re.compile(r"^(?P<file>[^#\s][^#]*)#L(?P<start>\d+)(?:-L(?P<end>\d+))?$")

# 支持的扩展名。P1 的 docx/epub 明确拒绝而不是「试试看」（PRD §12.1）。
MARKDOWN_SUFFIXES = (".md", ".markdown")
TEXT_SUFFIXES = (".txt",)

REJECTED_SUFFIXES = {
    ".docx": "DOCX 属 PRD §12.1 的 P1，本版不解析。请先另存为 UTF-8 Markdown 或 TXT",
    ".doc": "旧版 Word 属 P1，本版不解析。请先另存为 UTF-8 Markdown 或 TXT",
    ".epub": "EPUB 属 PRD §12.1 的 P1，本版不解析。请先转成 Markdown 或 TXT",
    ".pdf": "PDF 不在 PRD §12.1 的格式清单内。请先转成 Markdown 或 TXT",
    ".rtf": "RTF 不在 PRD §12.1 的格式清单内。请先转成 Markdown 或 TXT",
}


def make_locator(file: str, start: int, end: int | None = None) -> str:
    """行号从 1 起。end 省略表示单行。"""
    if start < 1:
        raise UsageError(f"行号从 1 开始，收到 {start}")
    if end is None or end == start:
        return f"{file}#L{start}"
    if end < start:
        raise UsageError(f"定位符区间反了：L{start}-L{end}")
    return f"{file}#L{start}-L{end}"


def parse_locator(text: str) -> tuple[str, int, int]:
    """拆定位符。**不宽容**：认不出就抛，别让它变成一条解析不了的来源引用。"""
    match = LOCATOR_RE.match(text.strip())
    if not match:
        raise UsageError(
            f"定位符格式不对：{text!r}。应为「文件名#L起行」或「文件名#L起行-L止行」"
        )
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    # 行号从 1 起：`#L0` 语法上像定位符，指的位置却不存在。
    if start < 1 or end < 1:
        raise UsageError(f"行号从 1 开始：{text!r}")
    if end < start:
        raise UsageError(f"定位符区间反了：{text!r}")
    return match.group("file"), start, end


def read_locator(raw_dir: Path, locator: str, *, encoding: str = "utf-8") -> str:
    """按定位符把原文读回来。读不到就抛——「可解析」的意思就是这里不返回空串。"""
    file, start, end = parse_locator(locator)
    path = raw_dir / file
    if not path.exists():
        raise UsageError(f"定位符指向的原始文件不存在：{path}")
    text = path.read_text(encoding=encoding)
    lines = text.splitlines()
    if start > len(lines):
        raise UsageError(f"定位符 {locator} 越界：原文只有 {len(lines)} 行")
    return "\n".join(lines[start - 1 : min(end, len(lines))])


@dataclass
class ImportFile:
    """一份输入文件。`rejected` 非空表示没被接收，原因必须写清。"""

    path: str
    sha256: str = ""
    bytes: int = 0
    fmt: str = ""
    encoding: str = ""
    lines: int = 0
    rejected: str = ""

    @property
    def accepted(self) -> bool:
        return not self.rejected

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "format": self.fmt,
            "encoding": self.encoding,
            "lines": self.lines,
            "rejected": self.rejected,
        }


@dataclass
class Paragraph:
    locator: str
    text: str
    line_start: int
    line_end: int
    chapter_index: int = -1  # -1 表示没归到任何章
    mapped: bool = True
    reason: str = ""

    def as_dict(self) -> dict:
        payload = {
            "locator": self.locator,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "chapter_index": self.chapter_index,
            "bytes": len(self.text.encode("utf-8")),
        }
        if not self.mapped:
            payload["unmapped_reason"] = self.reason
            payload["preview"] = self.text[:40]
        return payload


@dataclass
class Chapter:
    index: int
    title: str
    source_file: str
    line_start: int
    line_end: int
    paragraphs: list[Paragraph] = field(default_factory=list)
    heading_level: int = 0

    @property
    def locator(self) -> str:
        return make_locator(self.source_file, self.line_start, self.line_end)

    @property
    def chars(self) -> int:
        return sum(len(p.text) for p in self.paragraphs)

    @property
    def empty(self) -> bool:
        return not self.paragraphs

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "source_file": self.source_file,
            "locator": self.locator,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "heading_level": self.heading_level,
            "paragraphs": len(self.paragraphs),
            "chars": self.chars,
        }


@dataclass
class ImportManifest:
    """PRD §12.3 的十一个维度。**没有 `success` 字段，这是故意的。**

    schema 里 `additionalProperties: false` 也挡住了「随手加个 ok: true」。
    """

    import_id: str
    created_at: str
    files: list[dict]
    raw_bytes_preserved: bool
    project_id: str = ""
    chapters_parsed: int = 0
    paragraphs_mapped: int = 0
    paragraphs_unmapped: list[str] = field(default_factory=list)
    chapters_extracted: list[str] = field(default_factory=list)
    chapters_pending: list[str] = field(default_factory=list)
    refs_valid: bool = False
    sample_result: dict = field(default_factory=dict)
    blocking_conflicts: int = 0
    known_limits: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    # ---- 不属于 schema 的附加信息，落盘时单独一个文件 ----
    detail: dict = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict:
        payload: dict = {
            "import_id": self.import_id,
            "created_at": self.created_at,
            "files": list(self.files),
            "raw_bytes_preserved": self.raw_bytes_preserved,
            "chapters_parsed": self.chapters_parsed,
            "paragraphs_mapped": self.paragraphs_mapped,
            "paragraphs_unmapped": list(self.paragraphs_unmapped),
            "chapters_extracted": list(self.chapters_extracted),
            "chapters_pending": list(self.chapters_pending),
            "refs_valid": self.refs_valid,
            "blocking_conflicts": self.blocking_conflicts,
            "known_limits": list(self.known_limits),
            "parse_errors": list(self.parse_errors),
        }
        if self.project_id:
            payload["project_id"] = self.project_id
        if self.sample_result:
            payload["sample_result"] = dict(self.sample_result)
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> "ImportManifest":
        return cls(
            import_id=str(data.get("import_id") or ""),
            created_at=str(data.get("created_at") or ""),
            files=[item for item in data.get("files") or [] if isinstance(item, dict)],
            raw_bytes_preserved=bool(data.get("raw_bytes_preserved")),
            project_id=str(data.get("project_id") or ""),
            chapters_parsed=int(data.get("chapters_parsed") or 0),
            paragraphs_mapped=int(data.get("paragraphs_mapped") or 0),
            paragraphs_unmapped=[str(x) for x in data.get("paragraphs_unmapped") or []],
            chapters_extracted=[str(x) for x in data.get("chapters_extracted") or []],
            chapters_pending=[str(x) for x in data.get("chapters_pending") or []],
            refs_valid=bool(data.get("refs_valid")),
            sample_result=dict(data.get("sample_result") or {}),
            blocking_conflicts=int(data.get("blocking_conflicts") or 0),
            known_limits=[str(x) for x in data.get("known_limits") or []],
            parse_errors=[str(x) for x in data.get("parse_errors") or []],
        )

    # ---- 判读 ----
    def verdicts(self) -> list[tuple[str, bool, str]]:
        """逐维度给结论。**不做总括**：每个维度自己说话。

        PRD §12.4 的默认验收配置，一条一条对照。
        """
        rows: list[tuple[str, bool, str]] = []
        accepted = [f for f in self.files if not f.get("rejected")]
        rejected = [f for f in self.files if f.get("rejected")]
        rows.append(
            (
                "接收文件",
                bool(accepted),
                f"{len(accepted)} 份接收，{len(rejected)} 份拒绝",
            )
        )
        rows.append(("原始字节 100% 保全", self.raw_bytes_preserved, "逐份回读 sha256 比对"))
        rows.append(("解析异常已全部报告", True, f"{len(self.parse_errors)} 条"))
        rows.append(
            (
                "非空段落全部有归属",
                not self.paragraphs_unmapped,
                f"{self.paragraphs_mapped} 段已映射"
                + (f"，{len(self.paragraphs_unmapped)} 段未映射" if self.paragraphs_unmapped else ""),
            )
        )
        rows.append(("来源引用可解析", self.refs_valid, "抽样反查原始字节"))
        rows.append(
            (
                "提取覆盖完整（每章都提到候选）",
                not self.chapters_pending,
                f"{len(self.chapters_extracted)} 章已提取，{len(self.chapters_pending)} 章待提取",
            )
        )
        sample = self.sample_result or {}
        if sample:
            accuracy = float(sample.get("accuracy") or 0)
            rows.append(
                (
                    "抽样准确率 ≥ 95%",
                    accuracy >= 0.95,
                    f"{sample.get('sample_size')} 条，准确率 {accuracy:.1%}，种子 {sample.get('seed')}",
                )
            )
        rows.append(("阻断冲突为 0", self.blocking_conflicts == 0, f"{self.blocking_conflicts} 条"))
        return rows

    def blocking(self) -> list[str]:
        """会让「发布候选」不成立的问题。空列表才允许进 stage。

        **未映射段落不在此列**：PRD §12.4 允许「映射到解析结果**或**显式未映射区」。
        列出来了就算合规。但它仍然算验收维度未达标（见 `verdicts`），
        两件事分开——一个是能不能发，一个是有没有做到满分。
        """
        problems: list[str] = []
        if not self.raw_bytes_preserved:
            problems.append("原始字节没有 100% 保全——发布候选缺少可比对的原文")
        if self.blocking_conflicts:
            problems.append(f"存在 {self.blocking_conflicts} 条阻断冲突")
        if not self.refs_valid:
            problems.append("来源引用无法全部反查回原文")
        return problems
