"""증분 Diff 검증 - 실제 스냅샷 레코드 구조를 그대로 사용한다.

첫 실행에는 비교 대상 스냅샷이 없어 465건 전부 `new`로만 나왔고, `changed`/`deleted`가
실제 데이터에서 올바르게 계산되는지는 검증되지 않은 상태였다. 이 테스트는 로컬에
저장된 실제 스냅샷 JSON을 tmp 디렉터리로 복사해 '이전 스냅샷'을 만들고, 거기에
의도적인 변경을 넣어 두 날짜를 비교한다. 운영 디렉터리는 읽기만 한다.

실제 스냅샷이 없는 환경(CI 등)에서는 skip된다.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.diff import diff_snapshots
from src.snapshot_store import find_previous_snapshot_date, load_snapshot, save_snapshot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SNAPSHOT_DIR = PROJECT_ROOT / "snapshots"


def _latest_real_snapshot() -> tuple[str, str, dict]:
    """(date, project_id, records) - 가장 최근 실제 스냅샷 하나."""
    if not REAL_SNAPSHOT_DIR.exists():
        pytest.skip("실제 스냅샷이 없는 환경")
    dates = sorted(p.name for p in REAL_SNAPSHOT_DIR.iterdir() if p.is_dir())
    for date_str in reversed(dates):
        for proj_dir in sorted((REAL_SNAPSHOT_DIR / date_str).iterdir()):
            if not proj_dir.is_dir() or proj_dir.name.startswith("_"):
                continue
            records = load_snapshot(REAL_SNAPSHOT_DIR, date_str, proj_dir.name)
            if records:
                return date_str, proj_dir.name, records
    pytest.skip("사용 가능한 실제 스냅샷이 없음")


def test_incremental_diff_on_real_snapshot_shape(tmp_path):
    """실제 레코드로 new / changed / deleted / unchanged가 모두 정확히 분류된다."""
    _, project_id, current = _latest_real_snapshot()
    uids = sorted(current)
    assert len(uids) >= 4, "검증에 최소 4건 필요"

    # 이전 스냅샷 = 현재에서 1건 제거(=new), 1건 유지(=unchanged),
    #               1건 본문 변경(=changed), 1건 추가(=deleted)
    uid_new, uid_unchanged, uid_changed, uid_deleted = uids[0], uids[1], uids[2], uids[3]

    previous = {u: json.loads(json.dumps(r)) for u, r in current.items()}
    del previous[uid_new]
    previous[uid_changed]["content_html"] = "<p>이전 버전 본문</p>"

    # 현재에서 지워진 항목을 만들기 위해 현재 쪽에서 1건 제거
    current_trimmed = {u: r for u, r in current.items() if u != uid_deleted}

    diffs = diff_snapshots(previous, current_trimmed)
    by_uid = {d.uid: d for d in diffs}

    assert by_uid[uid_new].change_type == "new"
    assert by_uid[uid_unchanged].change_type == "unchanged"
    assert by_uid[uid_changed].change_type == "changed"
    assert "description" in by_uid[uid_changed].field_changes
    assert by_uid[uid_deleted].change_type == "deleted"

    # 전체 건수 보존: 합집합 크기와 같아야 한다(누락/중복 없음)
    assert len(diffs) == len(set(previous) | set(current_trimmed))


def test_find_previous_snapshot_date_picks_the_immediately_previous(tmp_path):
    """두 개 이상의 날짜가 있을 때 직전 날짜를 고른다 (증분 비교의 기준 선택)."""
    _, project_id, records = _latest_real_snapshot()
    sample = list(records.values())[:3]

    for date_str in ("2026-08-10", "2026-08-17", "2026-08-24"):
        save_snapshot(tmp_path, date_str, project_id, sample)

    assert find_previous_snapshot_date(tmp_path, "2026-08-24") == "2026-08-17"
    assert find_previous_snapshot_date(tmp_path, "2026-08-17") == "2026-08-10"
    assert find_previous_snapshot_date(tmp_path, "2026-08-10") is None


def test_state_file_at_snapshots_root_does_not_break_date_scan(tmp_path):
    """snapshots/ 루트의 render_problem_state.json이 날짜 디렉터리 탐색을 방해하지 않는다."""
    from src.problem_state import STATE_FILENAME
    from src.snapshot_store import list_snapshot_dates

    _, project_id, records = _latest_real_snapshot()
    save_snapshot(tmp_path, "2026-08-17", project_id, list(records.values())[:2])
    (tmp_path / STATE_FILENAME).write_text("{}", encoding="utf-8")

    assert list_snapshot_dates(tmp_path) == ["2026-08-17"]


def test_real_snapshots_contain_no_render_fallback_pollution():
    """스냅샷은 원본이어야 한다 - fallback 적용본이 저장되면 캐시 해시가 오염된다."""
    _, _, records = _latest_real_snapshot()
    polluted = [u for u, r in records.items() if r.get("render_fallback_applied")]
    assert polluted == [], f"스냅샷에 fallback 적용본이 저장됨: {polluted}"
