"""Vault 文件层：frontmatter 读写、哈希、原子替换、备份。

原则：Markdown/YAML 是唯一事实源；本模块是唯一的「碰盘」入口。
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

FM_RE_BYTES = b"---"

CACHE_VERSION = 2

# 内容镜像：全库正文的 sqlite 副本。
#
# 为什么不是 json.gz：宿主的 `open()` 要过一次 IPC 中转，单次约 20ms，与文件大小
# 无关；但 sqlite 打开一个库文件只要一次 open，之后读 1000 多行是纯内存操作。
# 实测 1120 份档案：逐份读 21.8s、tar 批量 0.75s、sqlite 一次 open 取全库 0.004s。
#
# 为什么不是 WAL：受限环境里 `-wal`/`-shm` 两个附属文件会被拦（disk I/O error），
# 用 `journal_mode=MEMORY` 把回滚日志放内存里，单文件即可读写。
MIRROR_FILE = ".novel_system/doc_mirror.sqlite"
CACHE_FILE = MIRROR_FILE  # 旧名，兼容既有调用

MIRROR_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc(
    rel   TEXT PRIMARY KEY,
    mtime INTEGER NOT NULL,
    size  INTEGER NOT NULL,
    meta  TEXT NOT NULL,
    body  TEXT NOT NULL,
    raw   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def _is_mirror(path: Path) -> bool:
    return path.name.endswith((".sqlite", ".db"))


class VaultError(RuntimeError):
    pass


@dataclass
class Doc:
    """一份 Markdown 文档：frontmatter + 正文。"""

    path: Path
    meta: dict[str, Any]
    body: str
    raw: str

    @property
    def id(self) -> str | None:
        return self.meta.get("id")

    @property
    def type(self) -> str | None:
        return self.meta.get("type")

    def section(self, heading: str) -> str:
        """取 `## heading` 到下一个同级标题之间的内容。"""
        return extract_section(self.body, heading)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end = idx
            break
    if end is None:
        return {}, text
    block = "".join(lines[1:end])
    try:
        meta = yaml.safe_load(block) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 报错信息更友好
        raise VaultError(f"frontmatter YAML 解析失败：{exc}") from exc
    if not isinstance(meta, dict):
        meta = {}
    return meta, "".join(lines[end + 1 :])


def render_doc(meta: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()
    body = body.lstrip("\n")
    return f"---\n{yaml_text}\n---\n\n{body}"


def read_many(paths: Iterable[Path], scratch: Path | None = None) -> dict[Path, str]:
    """批量读一批文件的原文，返回 `{路径: 内容}`。

    读不到的路径不会出现在结果里，调用方对缺项退回 `read_text()` 即可。
    返回的字典只保证「读到的」正确。

    这里曾经走「一个 tar 进程批量吐出来」的路子：读 1120 份只要 0.75 秒，比逐份
    快 30 倍。但中转层会给子进程读到的**每个**文件在临时目录留一份副本，一次全库
    扫描就是 1000 多个；而这些副本的清理比读还贵（实测单次 unlink 约 140ms，
    且会被安全守卫逐项拦下），一次 `rebuild-index` 因此能挂十几分钟。
    所以退回逐份直读——慢，但只发生一次：读到的内容会进内容镜像，
    之后所有命令都是从镜像里取，不再碰硬盘。

    `scratch` 仅为兼容旧调用保留，当前实现不再产生中转副本。
    """
    out: dict[Path, str] = {}
    for path in paths:
        if path in out:
            continue
        try:
            out[path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # 交给调用方的单文件通路去抛它本来要抛的错
    return out


def doc_from_text(path: Path, text: str) -> Doc:
    """由已读到的原文构造 `Doc`，省掉一次读盘。"""
    meta, body = split_frontmatter(text)
    return Doc(path=path, meta=meta, body=body, raw=text)


class DocCache:
    """一次运行内的文档缓存：同一个文件只读一次。

    为什么需要：受限宿主里 `open()` 走 IPC 中转，单次读盘约 18ms（800 个档案
    ≈ 15 秒）。索引器、地图投影、关系投影各读一遍全库，就是三次 15 秒；
    一次 `rebuild-index` 因此能拖到一分钟以上。

    缓存用 `(mtime_ns, size)` 做指纹：文件被本进程写过就自动刷新，
    被外部改过（编辑器、git checkout）也认得出，不会给出陈旧内容。

    可选持久化：`load()` / `save()` 把「已解析的正文」落到
    `.novel_system/doc_cache.json.gz`。指纹不变的文件下次直接复用，跳过
    20ms 的读盘——体检从二十几秒降到一秒以内，代价只是多一个缓存文件。
    """

    __slots__ = ("_docs", "hits", "misses", "dirty", "persist")

    def __init__(self) -> None:
        self._docs: dict[Path, tuple[int, int, Doc]] = {}
        self.hits = 0
        self.misses = 0
        self.dirty = False
        self.persist: Path | None = None

    def get(self, path: Path) -> Doc:
        try:
            st = path.stat()
        except OSError:
            # 读不到就让上层照常报错，不要在这里吞掉
            self.misses += 1
            return _load_doc(path)
        hit = self._docs.get(path)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            self.hits += 1
            return hit[2]
        self.misses += 1
        self.dirty = True
        doc = _load_doc(path)
        self._docs[path] = (st.st_mtime_ns, st.st_size, doc)
        return doc

    def prefetch(self, paths: Iterable[Path], scratch: Path | None = None) -> int:
        """先把一批文件整批读进缓存，返回补入条数。

        全库扫描（索引重建、体检、录入前清点）都是一次性读几百份档案，逐个
        `get()` 就是几百次 20ms 的固定开销。这里先用一次原生进程读完，后面的
        `get()` 全部命中缓存。指纹不变的文件直接跳过，不做无谓的读盘。
        """
        todo: list[tuple[Path, os.stat_result]] = []
        for path in paths:
            try:
                st = path.stat()
            except OSError:
                continue
            hit = self._docs.get(path)
            if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                self.hits += 1
                continue
            todo.append((path, st))
        if not todo:
            return 0

        texts = read_many([path for path, _ in todo], scratch)
        added = 0
        for path, st in todo:
            text = texts.get(path)
            if text is None:
                continue  # 没读到就留给 get() 走单文件通路
            try:
                meta, body = split_frontmatter(text)
            except VaultError:
                continue
            self.misses += 1
            self.dirty = True
            self._docs[path] = (
                st.st_mtime_ns,
                st.st_size,
                Doc(path=path, meta=meta, body=body, raw=text),
            )
            added += 1
        return added

    def store(self, path: Path, text: str) -> None:
        """本进程刚写完的内容直接入缓存，省掉下一次读盘。

        解析失败（比如写的是半成品）就退化成失效，让后来者重新读盘。
        """
        try:
            meta, body = split_frontmatter(text)
            st = path.stat()
        except (OSError, VaultError):
            self._docs.pop(path, None)
            return
        self.dirty = True
        self._docs[path] = (st.st_mtime_ns, st.st_size, Doc(path=path, meta=meta, body=body, raw=text))

    def invalidate(self, path: Path) -> None:
        self._docs.pop(path, None)
        self.dirty = True

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "size": len(self._docs)}

    # ---------------------------------------------------------- 持久化
    def load(self, path: Path) -> int:
        """读回上次落盘的解析结果，返回可用条数。

        只做「填表」，不在这里判断新鲜度——指纹比对留给 `get()`，
        这样文件被外部改过时照常重读，缓存不会比直读更旧。

        按后缀分派：`.sqlite`/`.db` 走内容镜像，其余走老的 gzip+json。
        """
        if _is_mirror(path):
            return self._load_mirror(path)
        return self._load_gzip(path)

    def _load_mirror(self, path: Path) -> int:
        """从 sqlite 镜像整表读回。一次 open，之后全是内存操作。

        存的是**解析结果**（meta 转成 JSON）而不是原文：只存原文的话，每次载入
        都要重新 `yaml.safe_load` 一遍全部 frontmatter——1069 份要 1.2 秒，
        比省下来的读盘还贵。JSON 反序列化一个扁平字典是微秒级。
        """
        try:
            db = sqlite3.connect(str(path))
        except sqlite3.Error:
            return 0
        try:
            db.execute("PRAGMA journal_mode=MEMORY")
            meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
            if meta.get("version") != str(CACHE_VERSION):
                return 0
            rows = db.execute("SELECT rel, mtime, size, meta, body, raw FROM doc").fetchall()
        except sqlite3.Error:
            return 0
        finally:
            db.close()

        dropped = 0
        for rel, mtime, size, meta_json, body, raw in rows:
            p = Path(rel)
            # 已经删掉的文件不必留在内存里；也顺手标脏，让退出时的保存把它清掉
            if not p.exists():
                dropped += 1
                continue
            try:
                front = json.loads(meta_json)
            except (TypeError, ValueError):
                dropped += 1
                continue
            if not isinstance(front, dict):
                dropped += 1
                continue
            self._docs[p] = (
                int(mtime),
                int(size),
                Doc(path=p, meta=front, body=body, raw=raw),
            )
        if dropped:
            self.dirty = True
        return len(self._docs)

    def _load_gzip(self, path: Path) -> int:
        try:
            payload = json.loads(gzip.decompress(path.read_bytes()))
        except (OSError, ValueError, EOFError, gzip.BadGzipFile):
            return 0
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return 0
        files = payload.get("files")
        if not isinstance(files, dict):
            return 0
        dropped = 0
        for key, entry in files.items():
            p = Path(key)
            # 已经删掉的文件不必留在内存里；也顺手标脏，让退出时的保存把它清掉
            if not p.exists():
                dropped += 1
                continue
            try:
                mtime, size, text = entry
                meta, body = split_frontmatter(text)
            except (TypeError, ValueError, VaultError):
                dropped += 1
                continue
            self._docs[p] = (
                int(mtime),
                int(size),
                Doc(path=p, meta=meta, body=body, raw=text),
            )
        if dropped:
            self.dirty = True
        return len(self._docs)

    def save(self, path: Path) -> bool:
        """落盘。只有本次真的读过盘才写，纯读取的复查不会改动缓存文件。"""
        if _is_mirror(path):
            return self._save_mirror(path)
        return self._save_gzip(path)

    def _save_mirror(self, path: Path) -> bool:
        """整表重写镜像。1120 行约 0.02 秒，不值得为了增量再记一本脏页账。"""
        if not self.dirty and path.exists():
            return False
        rows = [
            (
                str(p),
                mtime,
                size,
                json.dumps(doc.meta, ensure_ascii=False),
                doc.body,
                doc.raw,
            )
            for p, (mtime, size, doc) in self._docs.items()
            # 已删除的文件不必留在镜像里；stat 在受限宿主上是免费的
            if p.exists()
        ]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(path))
            try:
                db.execute("PRAGMA journal_mode=MEMORY")
                db.execute("PRAGMA synchronous=OFF")
                # 反正整表重写，顺带确保结构是最新的（旧版镜像只有 4 列）
                db.execute("DROP TABLE IF EXISTS doc")
                db.executescript(MIRROR_SCHEMA)
                db.execute("DELETE FROM doc")
                db.executemany(
                    "INSERT INTO doc(rel, mtime, size, meta, body, raw) VALUES(?,?,?,?,?,?)",
                    rows,
                )
                db.execute(
                    "INSERT OR REPLACE INTO meta(k, v) VALUES('version', ?)",
                    (str(CACHE_VERSION),),
                )
                db.commit()
            finally:
                db.close()
        except (sqlite3.Error, TypeError, ValueError):
            return False
        return True

    def _save_gzip(self, path: Path) -> bool:
        if not self.dirty and path.exists():
            return False
        payload = {
            "version": CACHE_VERSION,
            "files": {
                str(p): [mtime, size, doc.raw]
                for p, (mtime, size, doc) in self._docs.items()
                # 已删除的文件不必留在缓存里；stat 在受限宿主上是免费的
                if p.exists()
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            blob = gzip.compress(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"), compresslevel=3
            )
            path.write_bytes(blob)
        except OSError:
            return False
        return True


_CACHE: DocCache | None = None


@contextlib.contextmanager
def cached_docs(persist: Path | None = None) -> Iterator[DocCache]:
    """在 `with` 块内让 `read_doc` 走缓存。

    给了 `persist` 就在进出时读写内容镜像：进时载入、出时只在「本次读过盘」才保存。
    反复跑同一份没改动的库会落到毫秒级，改了哪个文件就只重读哪个文件。

    嵌套调用同一份镜像时直接复用外层：命令入口开一个，里面的 Config 加载、
    体检、索引各再请求一次，不该为此反复整表读写。
    """
    global _CACHE
    prev = _CACHE
    if persist is not None and prev is not None and prev.persist == persist:
        yield prev
        return

    cache = DocCache()
    cache.persist = persist
    if persist is not None:
        cache.load(persist)
    _CACHE = cache
    try:
        yield cache
    finally:
        _CACHE = prev
        if persist is not None:
            cache.save(persist)


def _load_doc(path: Path) -> Doc:
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    return Doc(path=path, meta=meta, body=body, raw=raw)


def read_doc(path: Path) -> Doc:
    if _CACHE is not None:
        return _CACHE.get(path)
    return _load_doc(path)


def read_text(path: Path) -> str:
    """读原文。有缓存时复用缓存里的 raw，省一次读盘。"""
    if _CACHE is not None:
        return _CACHE.get(path).raw
    return path.read_text(encoding="utf-8")


def read_optional(path: Path) -> Doc | None:
    return read_doc(path) if path.exists() else None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return sha256_text(path.read_text(encoding="utf-8"))


def _log_atomic_fallback(path: Path) -> None:
    """把「降级直写」记进 `.novel_system/logs/atomic_fallback.log`，便于事后排查。"""
    for parent in path.parents:
        logs = parent / ".novel_system" / "logs"
        if logs.parent.is_dir():
            try:
                logs.mkdir(parents=True, exist_ok=True)
                with (logs / "atomic_fallback.log").open("a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now().isoformat(timespec='seconds')} {path}\n")
            except OSError:
                pass
            return


# rename 探测结果缓存：None 未知 / True 可用 / False 被环境拒绝
_RENAME_OK: bool | None = None


def _direct_write(path: Path, text: str) -> None:
    """直写 + 内存回滚：先留旧内容在内存，写坏就写回去。

    比「临时文件 + rename」少一层磁盘原子性，但**不产生任何删除操作**。
    在带文件守卫的环境里（沙箱禁止 unlink，一次批量写几百个档案就会撞上
    「批量删除需确认」而整批失败），这是唯一能跑完的写法。
    """
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        if old is not None:
            with contextlib.suppress(OSError), path.open("w", encoding="utf-8") as fh:
                fh.write(old)
                fh.flush()
        raise


def write_atomic(path: Path, text: str) -> None:
    """写盘。能用 rename 就走「临时文件 + os.replace」，不能用就直写。

    为什么要探测并记住：受限环境（容器／沙箱／某些挂载）会拒绝 rename。原来的
    写法每次失败都要留一个 `.tmp_*` 再 unlink 掉，单个进程写几百个档案时，
    这些 unlink 会累积成「批量删除」，被安全守卫判定为需要人工确认而整批中断。
    探测一次、把结果缓存在进程内，之后所有写入直接落到目标文件，删除操作归零。
    """
    global _RENAME_OK
    path.parent.mkdir(parents=True, exist_ok=True)

    if _RENAME_OK is False:
        _direct_write(path, text)
        _remember(path, text)
        return

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    renamed = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.replace(tmp, path)
            renamed = True
            _RENAME_OK = True
            _remember(path, text)
        except PermissionError:
            # rename 被环境拒绝：本进程后续一律直写，不再产生临时文件
            _RENAME_OK = False
            _log_atomic_fallback(path)
            _direct_write(path, text)
            _remember(path, text)
    finally:
        if not renamed:
            with contextlib.suppress(OSError):
                if os.path.exists(tmp):
                    os.unlink(tmp)


def _remember(path: Path, text: str) -> None:
    """写盘后把新内容塞回缓存，紧跟着的读不必再走一次 IPC。"""
    if _CACHE is not None:
        _CACHE.store(path, text)


def backup_file(path: Path, backup_dir: Path, op_id: str) -> Path | None:
    """把文件复制到 backups/<op_id>/<相对目录>/<文件名>。"""
    if not path.exists():
        return None
    target_dir = backup_dir / op_id / path.parent.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.copy2(path, target)
    return target


def restore_file(backup_path: Path, target: Path) -> None:
    if not backup_path.exists():
        raise VaultError(f"备份不存在：{backup_path}")
    write_atomic(target, backup_path.read_text(encoding="utf-8"))


def iter_markdown(root: Path, skip_dirs: tuple[str, ...] = (".obsidian", ".git", ".novel_system")) -> Iterator[Path]:
    """遍历 Markdown。跳过隐藏文件：`.tmp_*` 是原子写入的半成品，不是事实源。"""
    for dirpath, dirnames, filenames in os.walk(root):
        # 隐藏目录（.trash、.workbuddy…）与 .git/.obsidian 一样不是事实源：
        # 把它们排除在外，否则归档/回收站里的旧档案会以同名 ID 覆盖真档案
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".md") and not name.startswith("."):
                yield Path(dirpath) / name


def cleanup_stale_temp(root: Path, min_age_seconds: int = 300) -> list[Path]:
    """删除遗留的 `.tmp_*` 半成品（写入降级、进程中断留下的残骸）。

    只在索引重建这类维护动作里调用，并且只删「超过 min_age_seconds 没被修改」的文件，
    避免和正在进行的写入抢同一个临时文件。删除失败不报错：受限环境可能禁止 unlink，
    但残留文件已被索引器和 .gitignore 双重隔离，不影响事实源正确性。
    """
    removed: list[Path] = []
    now = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in (".git", ".obsidian", ".novel_system") and not d.startswith(".")
        ]
        for name in filenames:
            if not name.startswith(".tmp_"):
                continue
            target = Path(dirpath) / name
            try:
                if now - target.stat().st_mtime < min_age_seconds:
                    continue
                target.unlink()
                removed.append(target)
            except OSError:
                continue
    removed.extend(_cleanup_scratch(root / ".novel_system" / "tmp", now, min_age_seconds))
    return removed


def _cleanup_scratch(scratch: Path, now: float, min_age_seconds: int) -> list[Path]:
    """清掉批量读盘在收容目录里留下的中转副本。

    正常情况下中转层自己会删，这里只是兜底：某些宿主会禁止子进程 unlink，
    副本就一直堆着。只认 `tar.<后缀>.<6 位随机>` 这个名字形状，别的东西一概不动。
    """
    if not scratch.is_dir():
        return []
    removed: list[Path] = []
    try:
        entries = list(scratch.iterdir())
    except OSError:
        return []
    for target in entries:
        if not target.is_file():
            continue
        parts = target.name.split(".")
        if len(parts) != 3 or parts[0] != "tar" or len(parts[2]) != 6:
            continue
        try:
            if now - target.stat().st_mtime < min_age_seconds:
                continue
            target.unlink()
            removed.append(target)
        except OSError:
            continue
    return removed


def extract_section(body: str, heading: str) -> str:
    """提取 `## heading` 段落内容（到下一个 ## 为止），忽略大小写与前后空格。"""
    target = heading.strip().lower()
    out: list[str] = []
    capturing = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip().lower()
            if capturing and level <= 2:
                break
            if level == 2 and title == target:
                capturing = True
                continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def bullets(section_text: str) -> list[str]:
    """把 `- 内容` 形式的段落实体化成列表（去掉空项与占位符）。"""
    items: list[str] = []
    for line in section_text.splitlines():
        s = line.strip()
        if not s.startswith(("-", "*")):
            continue
        value = s.lstrip("-*").strip()
        if not value or value in ("-", "…", "..."):
            continue
        items.append(value)
    return items


def key_values(section_text: str) -> dict[str, str]:
    """解析 `- 键：值` 形式，中英文冒号都支持。"""
    result: dict[str, str] = {}
    for line in section_text.splitlines():
        s = line.strip().lstrip("-*").strip()
        for sep in ("：", ":"):
            if sep in s:
                key, _, value = s.partition(sep)
                result[key.strip()] = value.strip()
                break
    return result


def checkbox_checked(text: str, label: str) -> bool:
    """判断 `- [x] label` 是否勾选。"""
    for line in text.splitlines():
        s = line.strip()
        if label in s and s.startswith(("- [x]", "- [X]")):
            return True
    return False


def chapter_num(chapter_id: str) -> int:
    """CH-0010 -> 10（数值比较，禁止字符串排序）。"""
    parts = chapter_id.split("-")
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    raise VaultError(f"无法解析章节序号：{chapter_id}")


def count_words(text: str) -> int:
    """中文按字计，英文按空格分词计。"""
    import re

    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z]+", text))
    return zh + en
