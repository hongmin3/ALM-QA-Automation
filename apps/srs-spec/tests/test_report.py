# Validates: REQ-SRS-003
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.diff import diff_snapshots
from src.report import render_html, render_markdown


def _rec(**kwargs):
    base = {
        "uid": "VXvue/VP-1",
        "id": "VP-1",
        "project_id": "VXvue",
        "title": "Old title",
        "status": "draft",
        "content_html": "<p>Detector shall reconnect automatically.</p><p>Battery status is shown.</p>",
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


def _sample_diffs():
    prev = _rec()
    curr = _rec(
        title="New title",
        status="reviewed",
        content_html="<p>Detector shall reconnect automatically within 10 seconds.</p>"
        "<p>Battery status is shown.</p>",
    )
    return diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})


def test_markdown_report_has_before_after_table_not_unified_diff():
    diffs = _sample_diffs()
    md = render_markdown(
        diffs,
        execution_date="2026-08-24",
        previous_date="2026-08-20",
        current_date="2026-08-24",
        pdf_sanity=[],
        alm_host="https://alm.example.com",
    )
    assert "```diff" not in md
    assert "| 항목 | Before | After |" in md
    assert "| 상태 | draft | reviewed |" in md
    assert "| 제목 | Old title | New title |" in md
    assert "Detector shall reconnect automatically within 10 seconds." in md
    # 변경 없는 문장("Battery status is shown.")은 표에 나오지 않는다
    assert "Battery status is shown." not in md


def test_markdown_report_links_each_changed_srs_to_alm():
    diffs = _sample_diffs()
    md = render_markdown(
        diffs,
        execution_date="2026-08-24",
        previous_date="2026-08-20",
        current_date="2026-08-24",
        pdf_sanity=[],
        alm_host="https://alm.example.com",
    )
    assert "https://alm.example.com/polarion/#/project/VXvue/workitem?id=VP-1" in md


def test_html_report_links_each_changed_srs_to_alm():
    diffs = _sample_diffs()
    html = render_html(
        diffs,
        execution_date="2026-08-24",
        previous_date="2026-08-20",
        current_date="2026-08-24",
        pdf_sanity=[],
        alm_host="https://alm.example.com",
    )
    assert '<div class="diff">' not in html
    assert 'href="https://alm.example.com/polarion/#/project/VXvue/workitem?id=VP-1"' in html
    assert "<table class='changes'>" in html


def test_unchanged_srs_produces_no_row():
    prev = _rec()
    curr = _rec()
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    md = render_markdown(
        diffs,
        execution_date="2026-08-24",
        previous_date="2026-08-20",
        current_date="2026-08-24",
        pdf_sanity=[],
        alm_host="https://alm.example.com",
    )
    assert "VP-1" not in md
