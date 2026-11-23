"""发布锁（PRD §18.4）。

三条要钉住的：内核判定归属、不按年龄猜死锁、写不进 fcntl 就拒绝而不是退化成假锁。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from novel_management.errors import ConflictError, UsageError
from novel_management.release import lock as lock_mod
from novel_management.release.lock import PublishLock, boot_id, read_holder


@pytest.fixture()
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "locks" / "publish.lock"


def test_acquire_creates_parent_and_records_holder(lock_path):
    lock = PublishLock(lock_path, task="task_000007", release_id="REL-000003", actor="author")
    lock.acquire()
    try:
        holder = read_holder(lock_path)
        assert holder is not None
        assert holder.pid == os.getpid()
        assert holder.task == "task_000007"
        assert holder.release_id == "REL-000003"
        assert holder.actor == "author"
        assert holder.acquired_at.endswith("Z"), "记录时间统一 UTC（PRD §17.3）"
    finally:
        lock.release()


def test_second_acquire_fails_with_holder_detail(lock_path):
    """第二个发布者被挡在门外，并且能从报错里看出是谁占着。"""
    first = PublishLock(lock_path, task="task_000001", release_id="REL-000001", actor="author")
    first.acquire()
    try:
        second = PublishLock(lock_path, task="task_000002", release_id="REL-000002", actor="author")
        with pytest.raises(ConflictError) as exc:
            second.acquire()
        message = str(exc.value)
        assert "task_000001" in message
        assert "REL-000001" in message
        assert "不按文件年龄或 PID 猜测" in message
        assert "release unlock" in message
    finally:
        first.release()


def test_release_allows_next_holder(lock_path):
    first = PublishLock(lock_path, task="task_000001", actor="author")
    first.acquire()
    first.release()

    second = PublishLock(lock_path, task="task_000002", actor="author")
    second.acquire()
    try:
        assert read_holder(lock_path).task == "task_000002"
    finally:
        second.release()


def test_release_is_idempotent(lock_path):
    lock = PublishLock(lock_path, task="task_000001", actor="author")
    lock.acquire()
    lock.release()
    lock.release()  # 不许抛：finally 里重复调用过一次


def test_release_leaves_no_stale_holder(lock_path):
    """释放后不能留着上一位持有人的信息，否则下一个人会以为锁被抢了。"""
    lock = PublishLock(lock_path, task="task_000001", actor="author")
    lock.acquire()
    lock.release()
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert "pid" not in payload
    assert payload["last_release"] == ""
    assert payload["released_by"] == os.getpid()


def test_context_manager_releases(lock_path):
    with PublishLock(lock_path, task="task_000001", actor="author"):
        with pytest.raises(ConflictError):
            PublishLock(lock_path, task="task_000002", actor="author").acquire()
    # 出了 with 就能再拿
    with PublishLock(lock_path, task="task_000002", actor="author"):
        pass


def test_read_holder_returns_none_for_missing_or_broken_file(lock_path):
    assert read_holder(lock_path) is None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{ 不是 JSON", encoding="utf-8")
    assert read_holder(lock_path) is None
    lock_path.write_text('{"pid": "不是数字"}', encoding="utf-8")
    assert read_holder(lock_path) is None


def test_refuses_when_fcntl_unavailable(lock_path, monkeypatch):
    """没有 fcntl 就直说做不到，不退化成「建个文件表示占着」。"""
    monkeypatch.setattr(lock_mod, "fcntl", None)
    with pytest.raises(UsageError) as exc:
        PublishLock(lock_path, task="task_000001", actor="author").acquire()
    assert "假锁" in str(exc.value)
    assert not lock_path.exists(), "拒绝时不该留下一个看似有效的锁文件"


def test_boot_id_is_a_stable_string():
    first = boot_id()
    assert isinstance(first, str)
    assert len(first) <= 64
    assert boot_id() == first
