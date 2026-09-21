# Validates: REQ-SRS-003
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.period_report import build_period_report, render_html, render_markdown


def _rec(**kwargs):
    base = {
        "uid": "VXvue/VP-1",
        "id": "VP-1",
        "project_id": "VXvue",
        "title": "Old title",
        "status": "draft",
        "content_html": "<p>Detector shall reconnect automatically.</p>",
        "severity": "normal",
        "priority": "50.0",
        "active": "active",
        "jira_id": None,
        "old_id": "08-10-90",
        "linked_work_items": [],
        "parent_id": None,
        "attachments_meta": [],
        "image_results": [],
        "comments": [],
    }
    base.update(kwargs)
    return base


def test_comparable_row_links_to_alm():
    prev = {"VXvue/VP-1": _rec()}
    curr = {
        "VXvue/VP-1": _rec(
            status="reviewed",
            content_html="<p>Detector shall reconnect automatically within 10 seconds.</p>",
        )
    }
    report = build_period_report(
        since="2026-07-25",
        until="2026-08-24",
        changed={"VXvue/VP-1": {"id": "VP-1", "project_id": "VXvue", "title": "Old title", "updated": "2026-08-01"}},
        current_by_uid=curr,
        baseline_by_uid=prev,
        baseline_snapshot="2026-07-25",
        alm_host="https://alm.example.com",
    )
    md = render_markdown(report)
    assert "https://alm.example.com/polarion/#/project/VXvue/workitem?id=VP-1" in md
    assert "```diff" not in md
    assert "| 상태 | draft | reviewed |" in md

    html = render_html(report)
    assert "href='https://alm.example.com/polarion/#/project/VXvue/workitem?id=VP-1'" in html


def test_listed_only_row_links_to_alm():
    report = build_period_report(
        since="2026-07-25",
        until="2026-08-24",
        changed={"VXvue/VP-2": {"id": "VP-2", "project_id": "VXvue", "title": "No baseline", "updated": "2026-08-01"}},
        current_by_uid={"VXvue/VP-2": _rec(uid="VXvue/VP-2", id="VP-2")},
        baseline_by_uid={},
        baseline_snapshot=None,
        alm_host="https://alm.example.com",
    )
    md = render_markdown(report)
    assert "https://alm.example.com/polarion/#/project/VXvue/workitem?id=VP-2" in md

    html = render_html(report)
    assert "href='https://alm.example.com/polarion/#/project/VXvue/workitem?id=VP-2'" in html


# ---------- 분류 정확성: '비교 가능'을 부풀리지 않는다 ----------


def _rec_for_period(uid, content, title="T", status="draft"):
    return {
        "uid": uid,
        "id": uid.split("/")[-1],
        "project_id": uid.split("/")[0],
        "title": title,
        "old_id": "01-10-10",
        "status": status,
        "content_html": content,
        "is_category": False,
        "linked_work_items": [],
        "parent_id": None,
        "attachments_meta": [],
        "comments": [],
    }


def test_srs_changed_before_snapshot_start_is_not_counted_as_comparable():
    """스냅샷 보관 이전에 수정된 SRS는 기준 스냅샷에 이미 '수정 후' 내용이 들어 있다.

    이런 건을 'Before/After 확인 가능'으로 세면 요약이 실제보다 부풀려져,
    읽는 사람이 전부 비교할 수 있다고 오해한다.
    """
    from src.period_report import build_period_report

    # VP-1: 기준일 이전에 수정됨 -> 기준 스냅샷과 현재가 동일
    # VP-2: 기준 스냅샷 이후 수정됨 -> 실제 차이 있음
    baseline = {
        "VXvue/VP-1": _rec_for_period("VXvue/VP-1", "<p>이미 수정된 내용</p>"),
        "VXvue/VP-2": _rec_for_period("VXvue/VP-2", "<p>예전 내용</p>"),
    }
    current = {
        "VXvue/VP-1": _rec_for_period("VXvue/VP-1", "<p>이미 수정된 내용</p>"),
        "VXvue/VP-2": _rec_for_period("VXvue/VP-2", "<p>새로운 내용</p>"),
    }
    changed = {
        "VXvue/VP-1": {"id": "VP-1", "project_id": "VXvue", "title": "T", "updated": "2026-07-28T00:00:00Z"},
        "VXvue/VP-2": {"id": "VP-2", "project_id": "VXvue", "title": "T", "updated": "2026-08-22T00:00:00Z"},
    }

    report = build_period_report(
        since="2026-07-25",
        until="2026-08-24",
        changed=changed,
        current_by_uid=current,
        baseline_by_uid=baseline,
        baseline_snapshot="2026-08-20",
    )

    assert len(report.changes) == 2, "기간 내 변경 건수는 Polarion 조회 결과 그대로"
    assert [c.id for c in report.comparable] == ["VP-2"], "실제 Before/After가 있는 건만 비교 가능"
    assert [c.id for c in report.listed_only] == ["VP-1"]


def test_no_baseline_at_all_lists_everything_without_inventing_history():
    from src.period_report import build_period_report

    changed = {
        "VXvue/VP-1": {"id": "VP-1", "project_id": "VXvue", "title": "T", "updated": "2026-07-28T00:00:00Z"},
    }
    report = build_period_report(
        since="2026-07-25",
        until="2026-08-24",
        changed=changed,
        current_by_uid={"VXvue/VP-1": _rec_for_period("VXvue/VP-1", "<p>x</p>")},
        baseline_by_uid={},
        baseline_snapshot=None,
    )
    assert report.comparable == []
    assert [c.id for c in report.listed_only] == ["VP-1"]
    md = __import__("src.period_report", fromlist=["render_markdown"]).render_markdown(report)
    assert "변경 시점만 확인" in md
    assert "추정해서 채우지 않습니다" in md
