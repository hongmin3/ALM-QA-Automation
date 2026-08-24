"""주간 실행 마커.

Task Scheduler의 `StartWhenAvailable`은 예정 시각에 PC가 꺼져 있었던 경우를 다음
가능 시점에 만회해 주지만, 실행 시점이 OS 사정에 따라 늦어질 수 있다. 그래서 부팅
시 트리거(`--catch-up`)를 하나 더 두고, 이 마커로 "이번 주에 이미 실행했는가"를
판단해 중복 실행을 막는다.

정책:
- 이번 주(월요일 기준 ISO 주)에 **이미 파이프라인이 수행된 기록이 있으면** 즉시 종료한다.
  성공이든 실패든 마찬가지다 - 실패한 주를 자동으로 다시 돌리지 않고, 실패는 메일
  알림으로만 알린다(사용자 결정).
- 기록이 없으면 전체 파이프라인을 수행한다. 즉 PC가 꺼져 있어 실행을 놓친 주는
  다음 부팅에서 자동으로 만회된다.

마커를 읽을 수 없으면 '미실행'으로 간주해 실행하는 쪽으로 기운다. 사양서를 갱신하지
못하는 것보다 한 번 더 실행하는 편이 안전하기 때문이다.
`--dry-run`은 배포를 하지 않으므로 마커를 남기지 않는다.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger("srs_automation")

MARKER_FILENAME = "last_run.json"


def _iso_week(d: date) -> str:
    """월요일 기준 ISO 주 식별자 (예: 2026-W35)."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def marker_path(logs_dir: Path) -> Path:
    return logs_dir / MARKER_FILENAME


def read_last_run(logs_dir: Path) -> dict | None:
    path = marker_path(logs_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("주간 실행 마커를 읽을 수 없어 미실행으로 간주합니다 (%s): %s", path, exc)
        return None


def already_ran_this_week(logs_dir: Path, today: date | None = None) -> tuple[bool, dict | None]:
    """(이번 주에 이미 수행했는가, 그 실행 기록)"""
    today = today or date.today()
    data = read_last_run(logs_dir)
    if not data:
        return False, None
    return data.get("iso_week") == _iso_week(today), data


def write_run(logs_dir: Path, run_date: str, *, ok: bool, today: date | None = None) -> None:
    today = today or date.today()
    logs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_date": run_date,
        "iso_week": _iso_week(today),
        "ok": ok,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    marker_path(logs_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("주간 실행 마커 기록: %s (%s, ok=%s)", run_date, payload["iso_week"], ok)
