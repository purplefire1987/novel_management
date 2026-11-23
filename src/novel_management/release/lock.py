"""发布会话锁（PRD §18.4）。

用操作系统的 `flock`，不是「建个文件表示有人占着」：

- 进程崩溃时内核自动释放，不会留下一个看起来还在、其实早没人的锁；
- 锁的归属由内核判定，**不看文件年龄**——PRD 明令禁止「超过 N 分钟就当死锁删掉」，
  那种做法在慢机器上会直接放出第二个发布者。

锁文件里仍然写持有人（PID、主机、启动标识、任务、开始时间），但那是给人查的，
不参与判定。要强制解锁必须显式操作并留痕（`release unlock`）。
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from ..canonical import utc_now
from ..errors import ConflictError, UsageError

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


def boot_id() -> str:
    """本次开机的标识。用来区分「同一个 PID 但换了机器/重启过」。"""
    try:
        if sys.platform == "darwin":
            import subprocess

            proc = subprocess.run(
                ["sysctl", "-n", "kern.boottime"], capture_output=True, check=False
            )
            return proc.stdout.decode("utf-8", "replace").strip()[:64]
        path = Path("/proc/sys/kernel/random/boot_id")
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        pass
    return ""


@dataclass
class LockHolder:
    pid: int
    host: str
    boot: str
    task: str
    release_id: str
    acquired_at: str
    actor: str

    def as_dict(self) -> dict:
        return {
            "pid": self.pid,
            "host": self.host,
            "boot": self.boot,
            "task": self.task,
            "release_id": self.release_id,
            "acquired_at": self.acquired_at,
            "actor": self.actor,
        }

    def describe(self) -> str:
        return (
            f"pid={self.pid} host={self.host} task={self.task or '-'} "
            f"release={self.release_id or '-'} since={self.acquired_at}"
        )


def read_holder(path: Path) -> LockHolder | None:
    """读锁文件里的持有人信息。读不到不算错——锁的判定不靠它。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return LockHolder(
            pid=int(data.get("pid") or 0),
            host=str(data.get("host") or ""),
            boot=str(data.get("boot") or ""),
            task=str(data.get("task") or ""),
            release_id=str(data.get("release_id") or ""),
            acquired_at=str(data.get("acquired_at") or ""),
            actor=str(data.get("actor") or ""),
        )
    except (TypeError, ValueError):
        return None


class PublishLock:
    """上下文管理器。拿不到锁就抛，不排队——单写入者是 MVP 的前提。"""

    def __init__(self, path: Path, *, task: str = "", release_id: str = "", actor: str = ""):
        self.path = Path(path)
        self.task = task
        self.release_id = release_id
        self.actor = actor
        self._fh = None
        self.holder: LockHolder | None = None

    def acquire(self) -> "PublishLock":
        if fcntl is None:
            raise UsageError(
                "当前平台没有 fcntl，无法提供操作系统级发布锁。"
                "PRD §17.6 明确不支持未验证的平台，因此这里直接拒绝，而不是退化成假锁"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = read_holder(self.path)
            fh.close()
            detail = holder.describe() if holder else "持有人信息缺失"
            raise ConflictError(
                f"另一个发布正在进行（{detail}）。"
                "不按文件年龄或 PID 猜测是否死锁——确认那个进程已结束后用 `novel release unlock` 显式处理"
            ) from None

        self.holder = LockHolder(
            pid=os.getpid(),
            host=socket.gethostname(),
            boot=boot_id(),
            task=self.task,
            release_id=self.release_id,
            acquired_at=utc_now(),
            actor=self.actor,
        )
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(self.holder.as_dict(), ensure_ascii=False, indent=2))
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            # 先把「已释放」写进文件再解锁：留着上一位持有人的信息更糟，
            # 下一个人看到会以为锁被抢了。
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(
                json.dumps(
                    {
                        "released_at": utc_now(),
                        "released_by": os.getpid(),
                        "last_release": self.release_id,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            self._fh.flush()
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "PublishLock":
        return self.acquire()

    def __exit__(self, *exc_info) -> None:
        self.release()
