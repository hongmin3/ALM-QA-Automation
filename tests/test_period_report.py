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
