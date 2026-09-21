"""파이프라인 단일 인스턴스 실행 락.

이 자동화는 Windows 작업 스케줄러에 서로 다른 두 개의 작업으로 등록된다
(`VXvue_SRS_Spec_Automation` 정기 실행, `VXvue_SRS_Spec_Automation_CatchUp`
부팅 5분 후 만회 실행 - `scheduler-operations.md` 참고). 각 작업의
`MultipleInstances=IgnoreNew` 설정은 **같은 작업이 스스로 겹치는 것만** 막을 뿐,
서로 다른 두 작업이 거의 같은 시각에 각자 `main.py`를 실행하는 것은 막지 못한다.

2026-09-21 실측: 월요일 07:00 정기 실행이 PC가 꺼져 있어 밀렸고
(`StartWhenAvailable`로 부팅 후 지연 실행), 동시에 부팅 5분 후 CatchUp 작업도
"이번 주 미실행"을 감지해 실행되면서, 두 프로세스가 같은 날짜의
output/snapshot 폴더에 완전히 병렬로 접근했다. 그 결과:
- 이미지 다운로드 단계에서 두 프로세스가 동일 파일에 동시에 쓰다 충돌
  (WinError 32 / PermissionDenied)해 정상적으로 받을 수 있었던 이미지까지
  실패 처리됨.
- 먼저 검증을 마친 프로세스가, 아직 다른 프로세스가 쓰는 중이던 PDF를
  size=0으로 보고 검증 실패 처리(오탐) - 실제로는 뒤이어 완료된 프로세스가
  정상적으로 지식파일 폴더를 갱신했다.

Task Scheduler 설정으로는 서로 다른 작업 간 배타 실행을 표현할 수 없으므로,
애플리케이션 레벨에서 파일 락으로 직접 막는다.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

LOCK_FILENAME = "automation.lock"

# Windows 작업의 ExecutionTimeLimit(2시간, `scheduler-operations.md` 참고)을 넘겨도
# 락 파일이 남아있다면, 정상 해제가 아니라 강제 종료 등으로 release()가 실행되지
# 못한 것이다. 그보다 넉넉히 오래된 락은 죽은 락으로 간주하고 회수한다.
STALE_AFTER_SECONDS = 3 * 60 * 60  # 3시간


class LockHeldError(Exception):
    """다른 프로세스가 이미 락을 보유하고 있어 이번 실행을 넘긴다."""


def lock_path(logs_dir: Path) -> Path:
    return logs_dir / LOCK_FILENAME


@dataclass
class RunLock:
    """`with RunLock(path):` 형태로 쓰는 단일 인스턴스 실행 락.

    같은 파이프라인의 동시 실행을, 어느 작업(정기/CatchUp/수동 실행)이
    트리거했는지와 무관하게 막는다.
    """

    path: Path

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 1 and self._reclaim_if_stale():
                    continue
                raise LockHeldError(
                    f"락 파일이 이미 존재합니다: {self.path} (보유자: {self._describe_holder()})"
                ) from None
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(f"{os.getpid()}\n{time.time()}\n")
                return

    def release(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def _acquired_at(self) -> float | None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            return float(lines[1])
        except Exception:
            # 락 내용이 손상되었으면 파일 mtime으로 대신 나이를 판단한다.
            try:
                return self.path.stat().st_mtime
            except OSError:
                return None

    def _describe_holder(self) -> str:
        try:
            pid = self.path.read_text(encoding="utf-8").splitlines()[0]
        except Exception:
            return "정보 없음"
        acquired_at = self._acquired_at()
        age = f"{time.time() - acquired_at:.0f}초 전 시작" if acquired_at is not None else "시작 시각 불명"
        return f"pid={pid}, {age}"

    def _reclaim_if_stale(self) -> bool:
        acquired_at = self._acquired_at()
        if acquired_at is None or time.time() - acquired_at < STALE_AFTER_SECONDS:
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
