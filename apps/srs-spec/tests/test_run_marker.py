# Validates: REQ-SRS-005
"""주간 실행 마커 - PC가 꺼져 있어 놓친 주만 부팅 시 만회한다."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.run_marker import already_ran_this_week, marker_path, write_run


def test_no_marker_means_not_run(tmp_path):
    ran, data = already_ran_this_week(tmp_path, today=date(2026, 8, 24))
    assert ran is False and data is None


def test_same_week_is_skipped_even_when_run_failed(tmp_path):
    """실패한 주는 자동 재실행하지 않는다 - 실패는 메일로만 알린다(사용자 결정)."""
    write_run(tmp_path, "2026-08-24", ok=False, today=date(2026, 8, 24))
    ran, data = already_ran_this_week(tmp_path, today=date(2026, 8, 26))  # 같은 주 수요일
    assert ran is True
    assert data["ok"] is False


def test_new_week_is_not_skipped(tmp_path):
    write_run(tmp_path, "2026-08-24", ok=True, today=date(2026, 8, 24))
    # 2026-08-31은 다음 주 월요일
    ran, _ = already_ran_this_week(tmp_path, today=date(2026, 8, 31))
    assert ran is False


def test_sunday_belongs_to_the_same_iso_week_as_previous_monday(tmp_path):
    """ISO 주는 월요일 시작이므로 일요일은 그 주 월요일과 같은 주다."""
    write_run(tmp_path, "2026-08-24", ok=True, today=date(2026, 8, 24))  # 월
    ran, _ = already_ran_this_week(tmp_path, today=date(2026, 8, 30))     # 일
    assert ran is True


def test_corrupt_marker_is_treated_as_not_run(tmp_path):
    """건너뛰는 쪽보다 한 번 더 실행하는 쪽으로 기운다."""
    marker_path(tmp_path).write_text("{ not json", encoding="utf-8")
    ran, data = already_ran_this_week(tmp_path, today=date(2026, 8, 24))
    assert ran is False and data is None
