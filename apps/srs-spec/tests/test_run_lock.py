# Validates: REQ-SRS-005
"""단일 인스턴스 실행 락 - 정기 실행과 CatchUp 실행이 겹치는 것을 막는다."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.run_lock import LockHeldError, RunLock, lock_path


def test_acquire_creates_lock_file(tmp_path):
    lock = RunLock(lock_path(tmp_path))
    lock.acquire()
    try:
        assert lock.path.exists()
    finally:
        lock.release()


def test_second_acquire_while_held_raises(tmp_path):
    """정기 실행이 락을 쥐고 있는 동안 CatchUp 실행이 들어오면 즉시 넘겨야 한다."""
    first = RunLock(lock_path(tmp_path))
    first.acquire()
    try:
        second = RunLock(lock_path(tmp_path))
        with pytest.raises(LockHeldError):
            second.acquire()
    finally:
        first.release()


def test_release_allows_next_acquire(tmp_path):
    path = lock_path(tmp_path)
    first = RunLock(path)
    first.acquire()
    first.release()

    second = RunLock(path)
    second.acquire()
    try:
        assert second.path.exists()
    finally:
        second.release()


def test_context_manager_releases_on_exit(tmp_path):
    path = lock_path(tmp_path)
    with RunLock(path):
        assert path.exists()
    assert not path.exists()


def test_context_manager_releases_on_exception(tmp_path):
    path = lock_path(tmp_path)
    with pytest.raises(ValueError):
        with RunLock(path):
            raise ValueError("boom")
    assert not path.exists()


def test_stale_lock_is_reclaimed(tmp_path, monkeypatch):
    """비정상 종료로 락이 안 지워졌어도, 충분히 오래됐으면 다음 실행이 회수해야 한다."""
    path = lock_path(tmp_path)
    path.write_text(f"12345\n{time.time() - 999999}\n", encoding="utf-8")

    lock = RunLock(path)
    lock.acquire()
    try:
        assert path.exists()
        pid_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert pid_line != "12345"
    finally:
        lock.release()


def test_fresh_lock_is_not_reclaimed(tmp_path):
    """방금 시작한 다른 프로세스의 락은 나이가 어리므로 죽은 락으로 오판하면 안 된다."""
    path = lock_path(tmp_path)
    path.write_text(f"12345\n{time.time()}\n", encoding="utf-8")

    lock = RunLock(path)
    with pytest.raises(LockHeldError):
        lock.acquire()


def test_corrupt_lock_falls_back_to_mtime_for_staleness(tmp_path):
    """락 내용이 손상돼도 최소한 mtime으로는 나이를 판단해 영구히 막히지 않게 한다."""
    path = lock_path(tmp_path)
    path.write_text("not a valid lock body", encoding="utf-8")
    old = time.time() - 999999
    import os

    os.utime(path, (old, old))

    lock = RunLock(path)
    lock.acquire()
    lock.release()
