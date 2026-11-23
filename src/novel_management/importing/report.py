"""导入的落盘：清单、解析结果、分批入 staging。

三条规矩：

1. **账分开记**。ImportManifest（PRD §12.3 的十一维度，受 schema 约束，可被外部复核）
   与 `detail.json`（逐章、逐提及的明细，体量大且结构会变）分成两个文件。
   塞一起会让 schema 变成一坨 `additionalProperties: true`，那等于没有契约。
2. **原始字节进归档区**，report 与解析结果进 runtime。前者不可重建，后者可重建。
3. **进 staging 才谈发布**。本模块只写 staging，一行正式区都不碰——
   发布走 T06 的 22 步，作者在中间那一步批准。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..canonical import utc_now
from ..errors import ConflictError, UsageError
from ..ids import IdAllocator
from ..paths import ProjectPaths
from ..staging import StagingArea
from ..storage import write_atomic, write_new
from ..tasks import TaskStore
from . import extract as extract_mod
from .model import Chapter, ImportFile, ImportManifest, make_locator, read_locator
from .parser import ParseResult, parse_document
from .preserve import Preserved, preserve_inputs
from .sampling import DEFAULT_SAMPLE_SIZE, DEFAULT_SEED, verify_sample
from .scanner import scan_inputs

MANIFEST_NAME = "manifest.json"
DETAIL_NAME = "detail.json"
ASSIGN_NAME = "assignments.json"
LEDGER_DIR = "Imports"
DRAFT_DIR = "初稿"  # V1.0 §8：04_正文/初稿（V4.0 的 `Drafts/`）
UNSAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


class ImportStore:
    """`<runtime>/imports/<IMP>/` 的读写。"""

    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.root = runtime / "imports"

    def next_id(self) -> str:
        highest = 0
        if self.root.is_dir():
            for path in self.root.glob("IMP-*"):
                try:
                    highest = max(highest, int(path.name.split("-")[1]))
                except (IndexError, ValueError):
                    continue
        return f"IMP-{highest + 1:06d}"

    def dir(self, import_id: str) -> Path:
        return self.root / import_id

    def manifest_path(self, import_id: str) -> Path:
        return self.dir(import_id) / MANIFEST_NAME

    def detail_path(self, import_id: str) -> Path:
        return self.dir(import_id) / DETAIL_NAME

    def exists(self, import_id: str) -> bool:
        return self.manifest_path(import_id).exists()

    def save(self, manifest: ImportManifest) -> Path:
        self.dir(manifest.import_id).mkdir(parents=True, exist_ok=True)
        path = self.manifest_path(manifest.import_id)
        payload = json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2).encode("utf-8")
        if path.exists():
            write_atomic(path, payload)
        else:
            write_new(path, payload)
        if manifest.detail:
            write_atomic(
                self.detail_path(manifest.import_id),
                json.dumps(manifest.detail, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        return path

    def load(self, import_id: str) -> ImportManifest:
        path = self.manifest_path(import_id)
        if not path.exists():
            raise UsageError(f"导入不存在：{import_id}（先 novel import scan）")
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = ImportManifest.from_dict(data)
        if self.detail_path(import_id).exists():
            manifest.detail = json.loads(self.detail_path(import_id).read_text(encoding="utf-8"))
        return manifest

    def list(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.glob("IMP-*") if p.is_dir())

    # 已发号记录。**必须落盘**，否则第二批渲染台账时只能看到自己那一批，
    # 第一批的章就从台账里消失了——那正是「伪报全量成功」的反面：
    # 报告看着完整，其实只描述了最后一次操作。
    def assignments_path(self, import_id: str) -> Path:
        return self.dir(import_id) / ASSIGN_NAME

    def load_assignments(self, import_id: str) -> dict:
        path = self.assignments_path(import_id)
        if not path.exists():
            return {"import_id": import_id, "batches": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("batches", {})
        data.setdefault("import_id", import_id)
        return data

    def save_assignments(self, import_id: str, data: dict) -> Path:
        path = self.assignments_path(import_id)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        if path.exists():
            write_atomic(path, payload)
        else:
            write_new(path, payload)
        return path

    # 原始字节在 Vault 的归档区，不在 runtime
    def raw_dir(self, pp: ProjectPaths, import_id: str) -> Path:
        return pp.archive / LEDGER_DIR / import_id / "raw"


# --------------------------------------------------------------------------
# scan：只读，产出各维度报告
# --------------------------------------------------------------------------

@dataclass
class ScanOutcome:
    manifest: ImportManifest
    parsed: list[ParseResult]


def run_scan(
    pp: ProjectPaths,
    runtime: Path,
    paths: list[Path],
    *,
    level: int = 0,
    seed: str = DEFAULT_SEED,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    actor: str = "",
) -> ScanOutcome:
    """扫一遍并把账记全。**不写正式区**，只写归档区（原始字节）与 runtime（报告）。"""
    store = ImportStore(runtime)
    import_id = store.next_id()

    rows = scan_inputs(paths)
    accepted = [(r, d) for r, d in rows if r.accepted]

    preserved = preserve_inputs(store.raw_dir(pp, import_id), rows)
    raw_ok = _raw_bytes_ok(rows, preserved)

    chapters_all: list[Chapter] = []
    unmapped_all = []
    errors: list[str] = []
    limits: list[str] = []
    parsed_list: list[ParseResult] = []

    for record, data in accepted:
        result = parse_document(record, data, level=level)
        parsed_list.append(result)
        chapters_all.extend(result.chapters)
        unmapped_all.extend(result.unmapped)
        errors.extend(result.errors)
        limits.extend(result.limits)

    # 章号在整次导入内重排，避免各自从 1 起撞号
    for ordinal, chapter in enumerate(chapters_all, start=1):
        chapter.index = ordinal

    dictionary = extract_mod.load_dictionary(pp)
    if not dictionary:
        limits.append(
            "项目 Canon 里没有可用的名称/别名词典，本次只按 ID 形态提取；"
            "人名类候选需要先有实体档案"
        )

    extractions = [extract_mod.extract_chapter(c, dictionary) for c in chapters_all]
    extracted_ids = [f"{c.chapter.index:04d} {c.chapter.title}" for c in extractions if c.mentions]
    pending_ids = [f"{c.chapter.index:04d} {c.chapter.title}" for c in extractions if not c.mentions]

    # 抽样对象：每个段落一条（定位符 + 原文）。逐条能反查才叫「来源定位有效」
    items: list[tuple[str, str]] = []
    for chapter in chapters_all:
        for paragraph in chapter.paragraphs:
            items.append((paragraph.locator, paragraph.text))
    sample = verify_sample(store.raw_dir(pp, import_id), items, size=sample_size, seed=seed)
    refs_valid = bool(sample.get("sample_size")) and float(sample.get("accuracy") or 0) >= 1.0

    conflicts, conflict_notes = _find_conflicts(import_id, chapters_all)
    limits.extend(conflict_notes)
    limits.append(
        "抽样准确率只衡量「来源定位能否反查回原文」，不衡量语义提取是否正确；"
        "后者需要人工核对，系统不代劳（PRD §12.4 末）"
    )

    manifest = ImportManifest(
        import_id=import_id,
        created_at=utc_now(),
        project_id=pp.project_id,
        files=[record.as_dict() for record, _ in rows],
        raw_bytes_preserved=raw_ok,
        chapters_parsed=len(chapters_all),
        paragraphs_mapped=sum(len(c.paragraphs) for c in chapters_all),
        paragraphs_unmapped=[p.locator for p in unmapped_all],
        chapters_extracted=extracted_ids,
        chapters_pending=pending_ids,
        refs_valid=refs_valid,
        sample_result=sample,
        blocking_conflicts=conflicts,
        known_limits=limits,
        parse_errors=errors,
    )
    manifest.detail = {
        "import_id": import_id,
        "raw_dir": pp.rel(store.raw_dir(pp, import_id)),
        "files": [record.as_dict() for record, _ in rows],
        "preserved": [p.__dict__ for p in preserved],
        "chapters": [c.as_dict() for c in chapters_all],
        "unmapped": [p.as_dict() for p in unmapped_all],
        "extractions": [e.as_dict() for e in extractions],
        "dictionary_size": len(dictionary),
    }
    store.save(manifest)
    return ScanOutcome(manifest=manifest, parsed=parsed_list)


def _raw_bytes_ok(rows: list[tuple[ImportFile, bytes]], preserved: list[Preserved]) -> bool:
    """只有「每一份被接收的文件都保全成功」才算 true。

    一份文件被拒不算保全失败——它没进来。写得含含糊糊，就等于把
    「有文件被拒」和「原文写坏了」混成同一件事。
    """
    expected = sum(1 for record, _ in rows if record.accepted)
    if expected == 0:
        return False
    return len(preserved) == expected and all(p.ok for p in preserved)


def _find_conflicts(import_id: str, chapters: list[Chapter]) -> tuple[int, list[str]]:
    """阻断冲突：会让「发布候选」自相矛盾的事。

    目前只认一种：两章会落到同一个目标文件名上。那是真冲突——
    后一章会覆盖前一章，而报告里两章都在，看着像都发了。
    """
    seen: dict[str, int] = {}
    notes: list[str] = []
    conflicts = 0
    for chapter in chapters:
        name = draft_name(import_id, chapter)
        if name in seen:
            conflicts += 1
            notes.append(
                f"第 {seen[name]} 章与第 {chapter.index} 章会落到同一个文件 {name}；"
                "标题重复或标题里只有标点，请先改标题再导入"
            )
        else:
            seen[name] = chapter.index
    return conflicts, notes


def draft_name(import_id: str, chapter: Chapter) -> str:
    """导入章的目标文件名。保留中文章名，去掉路径分隔符等危险字符。"""
    title = UNSAFE_NAME.sub("_", chapter.title).strip("._") or "untitled"
    title = title[:40]
    return f"{import_id}-C{chapter.index:03d}_{title}.md"


# --------------------------------------------------------------------------
# stage：分批入 staging（不碰正式区）
# --------------------------------------------------------------------------

@dataclass
class ChapterRef:
    """从 `detail.json` 读回来的章节摘要。正文不存这儿，按 locator 回原始文件取。"""

    index: int
    title: str
    source_file: str
    locator: str
    paragraphs: int = 0
    chars: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "ChapterRef":
        return cls(
            index=int(data.get("index") or 0),
            title=str(data.get("title") or ""),
            source_file=str(data.get("source_file") or ""),
            locator=str(data.get("locator") or ""),
            paragraphs=int(data.get("paragraphs") or 0),
            chars=int(data.get("chars") or 0),
        )


@dataclass
class StagedBatch:
    task_id: str
    import_id: str
    batch: int
    batches: int
    chapters: list[str]
    files: list[str]
    ledger: str
    skipped: list[str]
    reused: bool = False


def plan_batches(total: int, batch_size: int) -> list[tuple[int, int]]:
    """切批。返回 [(起, 止)) 0 起下标。`batch_size<=0` 表示不切。"""
    if total <= 0:
        return []
    size = total if batch_size <= 0 else max(1, batch_size)
    return [(start, min(start + size, total)) for start in range(0, total, size)]


def stage_batch(
    pp: ProjectPaths,
    runtime: Path,
    import_id: str,
    *,
    batch: int = 1,
    batch_size: int = 0,
    actor: str,
    task_store: TaskStore,
) -> StagedBatch:
    """把某一批章节写进 staging，并生成导入台账。"""
    store = ImportStore(runtime)
    manifest = store.load(import_id)
    problems = manifest.blocking()
    if problems:
        raise ConflictError(
            f"{import_id} 还不能进 staging：" + "；".join(problems) + "。先修掉或重新 scan"
        )

    detail = manifest.detail or {}
    chapters = [ChapterRef.from_dict(item) for item in detail.get("chapters", [])]
    # detail 里只存章的行区间，正文按 locator 回原始文件重取——
    # 这同时是「来源定位有效」的又一次实证：取不到就说明定位符坏了。
    raw_dir = store.raw_dir(pp, import_id)
    batches = plan_batches(len(chapters), batch_size)
    if not batches:
        raise UsageError(f"{import_id} 没有解析出任何章节，无法分批发布")
    if not 1 <= batch <= len(batches):
        raise UsageError(f"批次超出范围：共 {len(batches)} 批，收到第 {batch} 批")

    start, end = batches[batch - 1]
    window = chapters[start:end]
    # 只有标题、没有正文的章不生成提案：它进了 staging 就是一个空章，
    # 白占一个 CH 号，还会让「正文已发布」这句话变得不准确。
    chosen = [c for c in window if c.paragraphs > 0]
    skipped = [f"{c.index:04d} {c.title}" for c in window if c.paragraphs <= 0]
    if not chosen:
        raise UsageError(
            f"第 {batch} 批的 {len(window)} 章都没有正文（只有标题），没有可提案的内容"
        )

    assignments = store.load_assignments(import_id)
    ids, reused = _assign_batch_ids(
        store,
        import_id,
        assignments,
        batch=batch,
        batches=len(batches),
        pp=pp,
        runtime=runtime,
        chosen=chosen,
    )

    task = task_store.create(
        project_id=pp.project_id,
        task_type="ingest",
        assigned_to=actor,
        write_mode="staging",
        output_scope=f"{import_id} batch {batch}/{len(batches)}",
        input_files=[f["path"] for f in manifest.files if not f.get("rejected")],
        notes=f"原稿导入 {import_id} 第 {batch} 批（{len(chosen)} 章）",
    )
    staging = StagingArea(pp, task)

    written: list[str] = []
    # 目标路径从 ProjectPaths 取，不写字面量：V1.0 把 `03_Manuscripts/Drafts`
    # 改成了 `04_正文/初稿`，写死字面量的地方每改一次目录就得改一轮代码。
    ms_base = pp.manuscripts.relative_to(pp.root).as_posix()
    for entity_id, chapter in zip(ids, chosen):
        body = _read_chapter_body(raw_dir, chapter)
        target = f"{ms_base}/{DRAFT_DIR}/{entity_id}.md"
        data = _chapter_document(entity_id, pp.project_id, chapter, body).encode("utf-8")
        staging.stage(data=data, target_rel=target, actor=actor, note=f"{import_id} {chapter.locator}")
        written.append(target)

    # 导入台账是「研究记录」，不是世界知识：落在素材区（V1.0 解散了 05_Ledgers）。
    asset_base = pp.assets.relative_to(pp.root).as_posix()
    ledger_target = f"{asset_base}/{LEDGER_DIR}/{import_id}.md"
    ledger_body = _ledger_document(
        manifest,
        chapters,
        assignments,
        batches=batches,
        batch_size=batch_size,
        just_staged=batch,
    )
    staging.stage(
        data=ledger_body.encode("utf-8"),
        target_rel=ledger_target,
        actor=actor,
        note=f"{import_id} 导入台账",
        replace=True,
    )
    written.append(ledger_target)

    return StagedBatch(
        task_id=task.task_id,
        import_id=import_id,
        batch=batch,
        batches=len(batches),
        chapters=[f"{chapter.index:04d} {chapter.title}" for chapter in chosen],
        files=written,
        ledger=ledger_target,
        skipped=skipped,
        reused=reused,
    )


def _assign_batch_ids(
    store: ImportStore,
    import_id: str,
    assignments: dict,
    *,
    batch: int,
    batches: int,
    pp: ProjectPaths,
    runtime: Path,
    chosen: list[ChapterRef],
) -> tuple[list[str], bool]:
    """给这一批发号，并把结果记进 assignments。已发过就复用，不重发。

    重发号的后果不是「多占一个号」这么轻：同一章会有两个 CH 号，
    一个进了正文、一个躺在 discarded 的 staging 里，两边都能按 ID 取到对象。
    """
    plan: dict = assignments["batches"]
    key = str(batch)
    wanted = [c.index for c in chosen]

    prior = plan.get(key)
    if prior:
        had = [int(e.get("index") or 0) for e in prior.get("entries") or []]
        if had == wanted:
            return [str(e["id"]) for e in prior["entries"]], True
        raise ConflictError(
            f"{import_id} 第 {batch} 批此前按 {len(had)} 章切分（第 {_fmt_indices(had)} 章），"
            f"这次是 {len(wanted)} 章（第 {_fmt_indices(wanted)} 章）。\n"
            "换切分方式会让先前发出的号与章对不上。要重新分批请重新 import scan 起一个新的导入号。"
        )

    taken = {
        int(e.get("index") or 0): str(e["id"])
        for item in plan.values()
        for e in item.get("entries") or []
    }
    clash = [c.index for c in chosen if c.index in taken]
    if clash:
        raise ConflictError(
            f"第 {_fmt_indices(clash)} 章已在别的批次发过号（{taken[clash[0]]} 等），"
            "同一章不能进两个批次"
        )

    # areas 必须传：否则 staging 里这一批的副本会被当成「正式区里还有一个 CH-xxxx」，
    # 第二批开始就发不出号（T07 发现过的真实缺陷）。
    allocator = IdAllocator(
        pp.vault, runtime, actor=f"import:{import_id}", areas=pp.area_roots()
    )
    ids = allocator.allocate(
        "chapter", count=len(chosen), reason=f"{import_id} 第 {batch}/{batches} 批导入"
    )
    plan[key] = {
        "entries": [{"index": c.index, "id": eid, "title": c.title} for c, eid in zip(chosen, ids)],
        "size": len(chosen),
    }
    store.save_assignments(import_id, assignments)
    return ids, False


def _fmt_indices(indices: list[int]) -> str:
    return "、".join(f"{i:04d}" for i in indices)


def _read_chapter_body(raw_dir: Path, chapter: ChapterRef) -> str:
    """按章的行区间回原始文件取正文。**不重新解析**——取的就是当时那几行。"""
    text = read_locator(raw_dir, chapter.locator)
    # 去掉标题行本身（它就是 locator 的第一行）
    lines = text.splitlines()
    return "\n".join(lines[1:]).strip() if lines else ""


def _chapter_document(entity_id: str, project_id: str, chapter: ChapterRef, body: str) -> str:
    """章节 Markdown。frontmatter 只写 `chapter.schema.json` 允许的字段。

    **来源引用不写进正文 frontmatter**，写在导入台账里：
    正文要干净（正文只给人看），而且 schema 是 `additionalProperties: false`，
    往里塞 `source_refs` 只会让它变成一坨放行的空壳。
    """
    title = chapter.title.strip() or "未命名"
    word_count = len(re.sub(r"\s+", "", body))
    lines = [
        "---",
        f"chapter_id: {entity_id}",
        f"project_id: {project_id}",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        "stage: draft",
        "status: drafted",
        f"word_count: {word_count}",
        "---",
        "",
        f"# {title}",
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def _ledger_document(
    manifest: ImportManifest,
    chapters: list[ChapterRef],
    assignments: dict,
    *,
    batches: list[tuple[int, int]],
    batch_size: int,
    just_staged: int,
) -> str:
    """导入台账：**整个导入**的来源、覆盖、候选，不是「这一批」。

    为什么是整份而不是分批写：台账落在同一个目标文件上。若每批只写自己那份，
    第二批发布时会把第一批的章从台账里整段抹掉——报告于是「看着完整」，
    实际只描述了最后一次操作。整份渲染的代价是每次都要重算，收益是
    任何时刻读它，看到的都是这次导入的完整账。

    未发号的章明确写「尚未发号」，那正是「部分完成不伪报全量成功」要的形状。
    """
    detail = manifest.detail or {}
    by_index = {int(item["index"]): item for item in detail.get("extractions", [])}
    plan: dict = assignments.get("batches") or {}
    batch_of: dict[int, int] = {}
    id_of: dict[int, str] = {}
    for key, item in plan.items():
        for entry in item.get("entries") or []:
            index = int(entry.get("index") or 0)
            batch_of[index] = int(key)
            id_of[index] = str(entry["id"])
    staged = sorted(int(k) for k in plan) if plan else []

    size = "整篇一批" if batch_size <= 0 else f"每批 {batch_size} 章"
    lines = [
        "---",
        f"id: {manifest.import_id}",
        "type: import_ledger",
        f"import_id: {manifest.import_id}",
        f"batches: {len(batches)}",
        f"batch_size: {batch_size}",
        f"staged_batches: [{', '.join(str(b) for b in staged)}]",
        f"current_batch: {just_staged}",
        "---",
        "",
        f"# 原稿导入台账 {manifest.import_id}",
        "",
        f"生成时间：{manifest.created_at}　　分批：{len(batches)} 批（{size}）",
        "",
        f"本次更新：第 {just_staged}/{len(batches)} 批进入 staging。"
        "下表覆盖**全部批次**，未发号的章标注「尚未发号」。",
        "",
        "> 下面的实体提及**全部是候选**：系统只负责把它们找出来并指出位置，",
        "> 是否成为事实、要不要写成档案，由作者判断（PRD §12.2）。",
        "",
        "## 一、全部章节与发号情况",
        "",
        "| 批次 | 章 | 目标文件 | 原稿位置 | 段落 | 提及 |",
        "|---|---|---|---|---|---|",
    ]
    for chapter in chapters:
        extraction = by_index.get(chapter.index, {})
        mentions = len(extraction.get("mentions") or [])
        if chapter.paragraphs <= 0:
            # 空章不会发号，也不会「稍后补发」。写「尚未发号」会让人以为还欠它一个号。
            where = "不进正文"
        elif chapter.index in batch_of:
            where = f"第 {batch_of[chapter.index]} 批"
        else:
            where = "尚未发号"
        entity_id = id_of.get(chapter.index)
        label = (
            f"{entity_id} {chapter.title}"
            if entity_id
            else f"第{chapter.index:04d}章 {chapter.title}"
        )
        target = f"03_Manuscripts/{DRAFT_DIR}/{entity_id}.md" if entity_id else "—"
        note = "" if chapter.paragraphs > 0 else "（只有标题，无正文）"
        lines.append(
            f"| {where} | {label} | {target} | `{chapter.locator}` "
            f"| {chapter.paragraphs}{note} | {mentions} |"
        )

    lines += ["", "## 二、候选提及（未批准，不作为事实）", ""]
    any_mention = False
    for chapter in chapters:
        extraction = by_index.get(chapter.index, {})
        mentions = extraction.get("mentions") or []
        if not mentions:
            continue
        any_mention = True
        entity_id = id_of.get(chapter.index, f"第{chapter.index:04d}章")
        lines += [f"### {entity_id} {chapter.title}", ""]
        lines += ["| 类型 | 值 | 指向 | 原稿位置 | 片段 |", "|---|---|---|---|---|"]
        for mention in mentions:
            quote = str(mention.get("quote") or "").replace("|", "\\|")
            lines.append(
                f"| {mention.get('kind')} | {mention.get('value')} | {mention.get('entity_id') or '—'} "
                f"| `{mention.get('locator')}` | {quote} |"
            )
        lines.append("")
    if not any_mention:
        lines += ["这份原稿没有提取到任何候选提及。", ""]

    pending = [c for c in chapters if c.index not in id_of and c.paragraphs > 0]
    if pending:
        lines += ["## 三、尚未进 staging 的章", ""]
        lines += [f"- 第 {c.index:04d} 章 {c.title}（`{c.locator}`）" for c in pending]
        lines.append("")
        lines.append("上述章节还没发号。在这份台账里它们只是「计划」，不是「已完成」。")
        lines.append("")

    limits = manifest.known_limits or []
    if limits:
        lines += [f"## {'四' if pending else '三'}、已知限制", ""]
        lines += [f"- {item}" for item in limits]
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "ChapterRef",
    "ImportStore",
    "ScanOutcome",
    "StagedBatch",
    "build_manifest",
    "draft_name",
    "make_locator",
    "plan_batches",
    "run_scan",
    "stage_batch",
]


def build_manifest(**kwargs) -> ImportManifest:
    """给测试与外部调用方的一个薄封装：字段照 `ImportManifest` 传。"""
    return ImportManifest(**kwargs)
