"""稳定 ID 分配（V1.0 §9.2）。

规则来自 PRD，逐条落在这里：

- 分配后不变，一经分配不得复用（**删除留下的空号保留**，不从洞里补）。
- 不允许「扫描最大 ID 后无锁加一」——所以有锁文件，见 `_alloc_lock`。
- 分配结果写入任务日志（这里写 `<runtime>/id-allocations.jsonl`）。
- 失败产生的空号可以保留。
- 事件不因章节重排改 ID。本模块只发号，不承担「改号」职责。

**形态取 `<前缀>_<六位数字>`**（`ent_000001`），与 V1.0 §9.2 一致。
2026-11-22 之前是 V4.0 的 `CHAR-000001`（一类一前缀、连字符、4→6 位），
作者当日裁定按 V1.0 全库重排，1046 份已统一，旧形态只留在
`LEGACY_PREFIXES` / `LEGACY_ID_RE` 里做识别，不再发号。

前缀表分两类：`V1_PREFIXES`（V1.0 §9.2 的十二条 + 五条同风格补齐）是可发号的，
`LEGACY_PREFIXES`（V4.0 的 `CHAR` / `ITEM` / `CH` 等）只用于**认出旧号并报出迁移
去向**，本身不可发号。分开的理由：旧前缀若还能发号，同一个库里会同时长出
`ent_000967` 和 `CHAR-000205` 两种形态，而「一个对象一个 ID」是后者唯一的存在
理由被推翻的先兆。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter as fm
from .errors import ConflictError, UsageError
from .storage import iter_files

MIN_WIDTH = 6
MAX_WIDTH = 6

# V1.0 §9.2 的前缀。形态是 `<前缀>_<六位数字>`。
#
# **单前缀 `ent_` 收所有世界知识实体，类别靠 `type` 字段区分。**
# 这一点与 V4.0 的「一类一前缀」相反（CHAR/ITEM/LOC/FAC/RULE），
# 后果是：**不能再从 ID 号段推断类型**。`ent_000086` 是人物还是生物，
# 只能读 frontmatter。866 份实体在 2026-11-22 由五前缀合并重排而来，
# 映射表在 `~/WorkBuddy/novel-runtime/reports/v1_remap_mapping.json`。
V1_PREFIXES: dict[str, str] = {
    "novel": "小说项目",
    "idea": "灵感",
    "ent": "世界知识实体（类别由 type 字段承载）",
    "rel": "关系",
    "evt": "事件",
    "sc": "状态变化",
    "wf": "工作流实例",
    "task": "任务",
    "prop": "变更提案",
    "ver": "私库版本",
    "bkp": "备份",
    "src": "来源",
    # 下面五类 V1.0 §9.2 没给。原文写的是「**建议**前缀」，不是封闭枚举，
    # 所以按同风格补齐而不是硬塞进 ent_——塞进去会让「正文」与「世界设定」
    # 共用一个号段，正文写得多的人永远分不清 ent_000400 是物品还是第 400 章。
    "ch": "章节正文",
    "plan": "规划（总纲／卷纲／章纲）",
    "scene": "场景节拍",
    "fs": "伏笔",
    "asset": "素材卡",
}

# V4.0 旧前缀（`CHAR-000001`）。**只留名字用于拒绝，不再记每个前缀的去向。**
#
# 以前这里是一张「前缀 → 中文去向」的映射表（11 条）。删掉的是**内容**，不是
# 拒绝能力：名字集合还在，`resolve_prefix("FAC")` 照样报错，只是提示不再逐条写
# 「势力（已并入 ent）」。理由有两条：
#   1. 那 11 条描述是迁移期的脚手架。全库 0 份旧号之后，它只会在改号段时
#      变成两份要同时维护的真相（另一份是 `TYPE_TO_PREFIX`）。
#   2. 去向已经能从 `TYPE_TO_PREFIX` 推出来——能推的东西不该抄第二遍。
#
# **「拒绝发旧号」这一条不能丢**：数据面干净不等于守卫可以拆（见 ADR 里那条）。
LEGACY_PREFIXES: frozenset[str] = frozenset(
    {"CHAR", "LOC", "FAC", "ITEM", "RULE", "CH", "SCENE", "EVT", "FORESHADOW", "PLAN-VOL", "PLAN-CH"}
)

PREFIXES: dict[str, str] = dict(V1_PREFIXES)

# 号段上限：该前缀的**活文档段**到哪里为止。
#
# `ch` 是唯一需要它的前缀。旧稿 28 份（`90_Archive/Original_Draft/`）占
# 1001..1028，新正文只占 1..999。没有这条上限时，`allocate` 取「最大号 + 1」
# 会拿到 1029——新正文第一章写出来的 ID 就落进了存档段。
#
# 上限**不是**自动越过的阈值：到了 999 就报错，让人先决定扩段方案。
# 悄悄越过会同时破坏两件事：存档段的意义，以及「按 ID 猜年代」的可行性。
BAND_LIMITS: dict[str, int] = {
    "ch": 999,
}

# CLI 的类型名 → 前缀。大小写、单复数都收，少一次「凭记忆拼前缀」。
# V1.0 下绝大多数类型都落到 `ent`——区别只在 frontmatter 的 `type` 字段。
TYPE_TO_PREFIX: dict[str, str] = {
    # ---- 世界知识实体：一律 ent ----
    "CHARACTER": "ent",
    "CHAR": "ent",
    "PERSON": "ent",
    "LOCATION": "ent",
    "PLACE": "ent",
    "FACTION": "ent",
    "ORGANIZATION": "ent",
    "ORG": "ent",
    "ITEM": "ent",
    "OBJECT": "ent",
    "SKILL": "ent",
    "CREATURE": "ent",
    "WORLD": "ent",
    "FATE": "ent",
    "DESTINY": "ent",
    "RULE": "ent",
    "ENTITY": "ent",
    "ENT": "ent",
    # ---- 其余按 V1.0 前缀 ----
    "CHAPTER": "ch",
    "TEXT": "ch",
    "VOLUME": "plan",
    "PLAN": "plan",
    "SCENE": "scene",
    "FORESHADOW": "fs",
    "FORESHADOWING": "fs",
    "ASSET": "asset",
    "EVENT": "evt",
    "EVT": "evt",
    "RELATIONSHIP": "rel",
    "REL": "rel",
    "RELATION": "rel",
    "STATE_CHANGE": "sc",
    "STATECHANGE": "sc",
    "TASK": "task",
    "IDEA": "idea",
    "WORKFLOW": "wf",
    "PROPOSAL": "prop",
    "ISSUE": "prop",
    "CHANGE_REQUEST": "prop",
    "APPROVAL": "prop",
    "VERSION": "ver",
    "RELEASE": "ver",
    "BACKUP": "bkp",
    "SOURCE": "src",
    "IMPORT": "src",
    "PROJECT": "novel",
    "NOVEL": "novel",
}

# 合法 ID = 小写字母前缀 + 下划线 + 六位数字（V1.0 §9.2）。
# `ent_000086`、`ch_001001`、`plan_000003` 都成立；
# 旧形态 `CHAR-000086` 不再合法——它们落进 LEGACY_ID_RE，由调用方决定是报错还是提示迁移。
ID_RE = re.compile(r"^(?P<prefix>[a-z]+)_(?P<num>\d{6})$")
# V4.0 旧形态。用于「认出这是没迁移的旧号」，而不是判成「根本不是 ID」——
# 判成不是 ID 的后果是它在查重时完全隐身。
#
# 尾部那截 `(?:-[A-Z0-9]+)*` 是补的：`is_compound_id` 的 docstring 一直声明收
# 三类，第三类是「编号后面挂后缀」的 `VOL-0001-S1`，但原来的正则以 `\d{3,6}$`
# 收尾，这类根本匹配不上——声明与实现不符。库里旧号已经是 0 份（2026-11-22
# 重排），放宽只影响「认出来并报告」，不影响发号，所以补在这里而不是改口径。
LEGACY_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{3,6}(?:-[A-Z0-9]+)*$")

ID_ALLOCATION_LOG = "id-allocations.jsonl"


def resolve_prefix(kind: str) -> str:
    """`ent` / `ENT` / `entity` / `ENTITY` 都认。旧前缀明确报出迁移去向。

    V1.0 下 `CHARACTER` 不再得到 `CHAR` 而是 `ent`——这是**故意的**：
    类型信息归 `type` 字段，ID 只表达「这是一个世界知识实体」。
    """
    raw = (kind or "").strip()
    if not raw:
        raise UsageError("未指定 ID 类型")
    low = raw.lower()
    if low in PREFIXES:
        return low
    up = raw.upper()
    if up in TYPE_TO_PREFIX:
        return TYPE_TO_PREFIX[up]
    if up in LEGACY_PREFIXES:
        tgt = TYPE_TO_PREFIX.get(up)
        hint = f"。改用 --type {tgt}" if tgt else ""
        raise UsageError(f"前缀 {up} 是 V4.0 旧形态，已废弃{hint}")
    known = ", ".join(sorted(PREFIXES))
    raise UsageError(f"未知 ID 类型 {raw!r}。可用：{known}")


@dataclass(frozen=True)
class IdRecord:
    id: str
    prefix: str
    number: int
    source: str  # 项目内相对路径
    origin: str  # frontmatter / filename
    area: str = "formal"  # formal / pending / archive

    def __str__(self) -> str:
        return f"{self.id}@{self.source}[{self.area}]"


def split_id(raw: str) -> tuple[str, int] | None:
    match = ID_RE.match(raw.strip())
    if not match:
        return None
    return match.group("prefix"), int(match.group("num"))


def is_compound_id(raw: str) -> bool:
    """看着像 ID、但不是 V1.0 形态的旧号。三类都要收：

    - `EVT-CH-0001-001`：前缀里夹了数字段（V4.0 时代的复合号）
    - `PLAN-VOL-000001`：两段大写前缀（已并为 `plan_`）
    - `VOL-0001-S1`：编号后面挂后缀

    判定用 `LEGACY_ID_RE` 而不是「有没有下划线」——**它防的是「隐身」**：
    这些号既不计入号段（取最大号会取到脏值），也不该被当成「不是 ID」而忽略，
    忽略的结果是它们在查重时完全看不见。
    """
    text = raw.strip()
    if split_id(text):
        return False
    return bool(LEGACY_ID_RE.match(text)) and text.count("-") >= 2


class IdRegistry:
    """扫全 Vault 收集 ID。只读，不缓存跨调用结果。

    **查重是分级的**，因为「同一个 ID 出现在两处」有三种完全不同的来由：

    - 正式区里出现两次：真撞号。按 ID 取对象永远有歧义，必须拦住（FAIL）。
    - 正式区一份 + 待发布区（staging/审阅）一份：**同一个对象在途**。
      拷贝是发布流程的正常中间态，判成撞号等于「发布一次就再也发不出号」。
    - 正式区一份 + 归档区一份：多半是历史归档没删干净，只告警（WARN）。

    区划由 `areas` 传入（`ProjectPaths.area_roots()`），不在这里写死目录名。
    **不传 areas 时全库一律按正式区处理**——宁可从严，也不能因为调用方忘了
    传参就悄悄放松查重。
    """

    def __init__(self, vault: Path, include_hidden: bool = False, areas: dict | None = None):
        self.vault = vault
        self.include_hidden = include_hidden
        self._records: list[IdRecord] | None = None
        self._compounds: list[tuple[str, str]] | None = None
        self._mismatched: list[tuple[str, str, str]] | None = None
        self._areas = self._normalise_areas(areas)

    def _normalise_areas(self, areas: dict | None) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {"formal": [], "pending": [], "archive": []}
        for name, roots in (areas or {}).items():
            if name not in out:
                raise UsageError(f"未知 ID 区划：{name}（可用 formal / pending / archive）")
            out[name] = [Path(r).resolve() for r in roots]
        self._has_areas = any(out.values())
        if not self._has_areas:
            out["formal"] = [self.vault.resolve()]
        return out

    def area_of(self, path: Path) -> str:
        """formal / pending / archive / other。

        没给区划时全库都是 formal（从严）。给了区划时，落在三区之外的路径记 `other`
        ——它们不是正式区，不能算撞号；通常是 Vault 根的旧归档（如 `99_Archive/legacy_v1`），
        把那里的历史副本算成「正式区有两份」会让今天一个号都发不出去。
        """
        try:
            target = path.resolve()
        except OSError:
            return "formal"
        for name in ("formal", "pending", "archive"):
            for root in self._areas[name]:
                if target == root or root in target.parents:
                    return name
        return "other" if self._has_areas else "formal"

    def _collect(self) -> list[IdRecord]:
        records: list[IdRecord] = []
        compounds: list[tuple[str, str]] = []
        mismatched: list[tuple[str, str, str]] = []
        for path in iter_files(self.vault, skip_hidden=not self.include_hidden):
            if path.suffix.lower() != ".md":
                continue
            try:
                rel = path.relative_to(self.vault).as_posix()
            except ValueError:
                rel = str(path)
            area = self.area_of(path)
            # 文件名取 ID 要取**前两段**：V1.0 的文件名是 `ent_000339_浑天-地动仪.md`，
            # 只取第一段会得到 "ent"，那不是 ID。注意不能按 `-` 切——`浑天-地动仪`
            # 名字里本身就带连字符。
            parts = path.stem.split("_")
            head = "_".join(parts[:2]).strip() if len(parts) >= 2 else path.stem.strip()
            from_name = split_id(head)

            meta = fm.read(path)
            raw_id = str(meta.get("id") or "").strip()
            if "{{" in raw_id:
                raw_id = ""  # 模板占位符不是身份

            if raw_id:
                # frontmatter 是权威。文件名与它不符只记下来，不额外占一个号——
                # 无条件双记会制造假重复：VOL-0001_<细纲>.md 的 id 是
                # VOL-0001-S1-DETAIL，同一卷的五份文档各是不同对象，却被文件名判成同一个。
                from_meta = split_id(raw_id)
                if from_meta:
                    records.append(
                        IdRecord(raw_id, from_meta[0], from_meta[1], rel, "frontmatter", area)
                    )
                elif is_compound_id(raw_id):
                    compounds.append((raw_id, rel))
                if from_name and head != raw_id:
                    mismatched.append((rel, raw_id, head))
                continue

            # 没有 frontmatter id 才退回文件名。
            if from_name:
                records.append(IdRecord(head, from_name[0], from_name[1], rel, "filename", area))
            elif is_compound_id(head):
                compounds.append((head, rel))
        self._compounds = compounds
        self._mismatched = mismatched
        return records

    @property
    def records(self) -> list[IdRecord]:
        if self._records is None:
            self._records = self._collect()
        return self._records

    @property
    def compounds(self) -> list[tuple[str, str]]:
        """不规范的复合 ID：(id, 出处)。PRD 禁止，待迁移。"""
        if self._compounds is None:
            self._records = self._collect()
        return self._compounds or []

    @property
    def mismatched(self) -> list[tuple[str, str, str]]:
        """(出处, frontmatter id, 文件名 id)。文件名与身份不符，值得提示但不等于重号。"""
        if self._mismatched is None:
            self._records = self._collect()
        return self._mismatched or []

    def for_prefix(self, prefix: str) -> list[IdRecord]:
        return [r for r in self.records if r.prefix == prefix]

    def max_number(self, prefix: str, upper: int | None = None) -> int:
        """该前缀的最大号。

        `upper` 给定时只统计不超过它的号。理由是号段有分层：`CH` 的 1001 以上
        留给旧稿存档（`90_Archive/Original_Draft/`），新正文只占 1..999。
        不设上限的话，旧稿那 28 份会把新正文的发号游标推到 1029，
        下一章正文就发到存档段里去了。
        """
        numbers = [
            r.number
            for r in self.for_prefix(prefix)
            if upper is None or r.number <= upper
        ]
        return max(numbers) if numbers else 0

    def collisions(self) -> dict[str, list[IdRecord]]:
        """同一 ID 出现在多处（不分区域）。键是 ID，值是所有出处。"""
        buckets: dict[str, list[IdRecord]] = {}
        for record in self.records:
            buckets.setdefault(record.id, []).append(record)
        return {k: v for k, v in buckets.items() if len({r.source for r in v}) > 1}

    def conflicts(self) -> dict[str, list[IdRecord]]:
        """**正式区内**同一 ID 的两份以上。这是真撞号，发号必须停。"""
        return {
            eid: recs
            for eid, recs in self.collisions().items()
            if len({r.source for r in recs if r.area == "formal"}) > 1
        }

    def tainted(self) -> dict[str, list[IdRecord]]:
        """正式区一份 + 非正式区也有一份（归档或区划外）。只告警，不拦发号。

        归档区里躺着 `legacy_v1` 这种整库快照时，这里会一次报出很多条，
        所以它是「报告用」的口径，**不参与发号拦截**。
        """
        out: dict[str, list[IdRecord]] = {}
        for eid, recs in self.collisions().items():
            areas = {r.area for r in recs}
            if "formal" in areas and ("archive" in areas or "other" in areas):
                out[eid] = recs
        return out

    def pending_copies(self) -> dict[str, list[IdRecord]]:
        """正式区一份 + 待发布区也有一份：同一对象在途。按设计如此，不是问题。"""
        out: dict[str, list[IdRecord]] = {}
        for eid, recs in self.collisions().items():
            areas = {r.area for r in recs}
            if "formal" in areas and "pending" in areas and not ({"archive", "other"} & areas):
                out[eid] = recs
        return out

    def stray_compound(self, prefix: str) -> list[tuple[str, str]]:
        """`EVT-CH-0001-001` 这类复合型，按「首段前缀」归到 evt 名下。

        PRD 禁止这种形态，但库里有——发号时要提醒作者「这个前缀看着是空的，
        其实已经有一批旧号」。它们不参与取号，所以新号从 000001 开始不会撞。

        复合型都是 V4.0 遗留的大写形态（`EVT-`），而 `prefix` 是 V1.0 的小写
        （`evt`），所以比较**不区分大小写**；按大小写敏感比会永远返回空，
        那等于把「有一批旧号」这件事藏起来。
        """
        probe = prefix.upper() + "-"
        return [(raw, src) for raw, src in self.compounds if raw.upper().startswith(probe)]


class IdAllocator:
    """分配入口。所有正式 ID 都应经这里发出。"""

    def __init__(
        self,
        vault: Path,
        runtime: Path | None,
        actor: str = "",
        lock_timeout: float = 5.0,
        areas: dict | None = None,
    ):
        self.vault = vault
        self.runtime = runtime
        self.actor = actor or "unknown"
        self.lock_timeout = lock_timeout
        self.areas = areas

    # ---- 锁 ----------------------------------------------------------
    def _lock_path(self) -> Path:
        if self.runtime is None:
            raise UsageError(
                "未配置 Runtime：ID 分配必须留下审计记录，不能在没有 Runtime 的情况下发号"
            )
        return self.runtime / "locks" / "id-allocate.lock"

    def _holder_state(self, path: Path) -> tuple[str, bool | None]:
        """读出锁的持有者描述，并判断它是否还活着。

        返回 `(描述, 是否已死)`；`None` 表示**判断不了**（内容残缺、拿不到 pid、
        或者 pid 归别的用户）。判断不了时一律当活锁处理——把活的锁误判成死的
        会让两个进程同时发号，代价比多等 2 秒大得多。
        """
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            return "未知", None
        m = re.search(r"pid=(\d+)", raw)
        if not m:
            # 写锁的进程可能刚 O_CREAT 完还没写内容就被杀了，文件是空的。
            # 这种「空锁」是不是陈旧，静态判断不了，交给超时提示人工确认。
            return raw or "未知（锁文件为空）", None
        pid = int(m.group(1))
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return raw, True
        except PermissionError:
            return raw, False
        except OSError:
            return raw, None
        return raw, False

    def _acquire(self, timeout: float | None = None):
        path = self._lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + (self.lock_timeout if timeout is None else timeout)
        stale_cleared = False
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                holder, dead = self._holder_state(path)
                # 陈旧锁自动接管：进程已经不在，锁上的号是谁都发不出来。
                # 只接管一次，且必须确认 pid 已死——避免「两个进程互相判定对方陈旧」。
                if dead is True and not stale_cleared:
                    stale_cleared = True
                    try:
                        path.unlink()
                    except OSError:
                        raise ConflictError(
                            f"陈旧锁删不掉（锁 {path}，持有者 {holder} 已不在运行）。"
                            "请手工删除该文件后重试。"
                        ) from None
                    continue
                if time.monotonic() > deadline:
                    hint = (
                        "持有进程已不在运行，锁是陈旧残留——删掉该锁文件即可继续。"
                        if dead is True
                        else "确认没有别的 novel 在跑后可删除该锁文件。"
                    )
                    raise ConflictError(
                        f"另一个进程正在分配 ID（锁 {path}，持有者 {holder}）。{hint}"
                    ) from None
                time.sleep(0.05)
                continue
            os.write(fd, f"pid={os.getpid()} at={time.time():.0f}".encode("utf-8"))
            os.close(fd)
            return path

    def _release(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            # 锁删不掉不该让已成功的分配变成失败。留下的锁里记着自己的 pid，
            # 本进程退出后 `_holder_state` 会认出它已死，下次发号自动接管。
            pass

    # ---- 分配 --------------------------------------------------------
    def allocate(
        self,
        kind: str,
        count: int = 1,
        reason: str = "",
        dry_run: bool = False,
        allow_dirty: bool = False,
    ) -> list[str]:
        if count < 1:
            raise UsageError("--count 至少为 1")
        prefix = resolve_prefix(kind)
        registry = IdRegistry(self.vault, areas=self.areas)

        # 只拦本前缀的**正式区内**重号。理由分两层：
        # 1. 只拦本前缀：ASSET 撞号不该让 CHAR 也发不出号，全库重号由 validate 报。
        # 2. 只看正式区：staging 里那一份是同一对象的在途副本（T07 导入就是
        #    「先入 staging 再发布」），把它算成撞号等于发布一次就断掉后续发号。
        dirty = {
            eid: recs for eid, recs in registry.conflicts().items() if (split_id(eid) or (None,))[0] == prefix
        }
        if dirty and not allow_dirty:
            detail = "；".join(
                f"{eid}（{'、'.join(sorted({r.source for r in recs if r.area == 'formal'}))}）"
                for eid, recs in sorted(dirty.items())[:5]
            )
            raise ConflictError(
                f"{prefix} 前缀在正式区已有 {len(dirty)} 个重复 ID，先修完再发号：{detail}\n"
                "重复存在时继续发号，会让「按 ID 取对象」永远有歧义。"
                "确认要带病发号请加 --allow-dirty。"
            )

        lock = None if dry_run else self._acquire()
        try:
            # 取号要同时看两处：现存文件里的最大号，和审计里发过的最大号。
            # 只看前者会在删档后从洞里补号——那等于让旧引用指到另一个对象上。
            # 有号段上限时两处都只统计段内的号：段外是别的用途（旧稿存档），
            # 让它们参与取最大号等于把新正文的游标推到存档段里去。
            limit = BAND_LIMITS.get(prefix)
            base = max(
                registry.max_number(prefix, upper=limit),
                self._historical_max(prefix, upper=limit),
            )
            if limit is not None and base + count > limit:
                raise UsageError(
                    f"{prefix} 的活文档号段已用尽（1..{limit}，下一个要发 {base + count}）。\n"
                    f"{limit + 1:0{MIN_WIDTH}d} 起留给了存档段（旧稿等历史内容），"
                    "自动越过去会让新文档与存档混在同一段里。\n"
                    "要扩段请先定方案（比如把存档段整体上移），不要直接发号。"
                )
            issued = [f"{prefix}_{n:0{MIN_WIDTH}d}" for n in range(base + 1, base + count + 1)]
            # 锁内复查：别的进程可能刚发过号。这里查**全部记录**（含 staging/归档）——
            # 取号取最大号本来就要看全库，否则会把号发到别人盘里的对象上。
            taken = {r.id for r in IdRegistry(self.vault, areas=self.areas).records}
            clash = [i for i in issued if i in taken]
            if clash:
                raise ConflictError(f"分配结果与现有 ID 冲突：{clash}")
            if not dry_run:
                self._record(prefix, issued, base, reason)
            return issued
        finally:
            if lock is not None:
                self._release(lock)

    def _record(self, prefix: str, ids: list[str], base: int, reason: str) -> None:
        assert self.runtime is not None
        path = self.runtime / ID_ALLOCATION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "prefix": prefix,
                "ids": ids,
                "previous_max": base,
                "actor": self.actor,
                "reason": reason,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _historical_max(self, prefix: str, upper: int | None = None) -> int:
        """审计里该前缀发到过的最大号。文件被删了，号也不能回到前面的洞里。"""
        best = 0
        for row in self.history(limit=100_000):
            if row.get("prefix") != prefix:
                continue
            for raw in row.get("ids") or []:
                parsed = split_id(str(raw))
                if parsed and parsed[0] == prefix:
                    if upper is not None and parsed[1] > upper:
                        continue
                    best = max(best, parsed[1])
        return best

    def history(self, limit: int = 20) -> list[dict]:
        if self.runtime is None:
            return []
        path = self.runtime / ID_ALLOCATION_LOG
        if not path.exists():
            return []
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]
