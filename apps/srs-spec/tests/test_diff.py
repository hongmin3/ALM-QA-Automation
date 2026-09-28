# Validates: REQ-SRS-003
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.diff import diff_snapshots


def _rec(**kwargs):
    base = {
        "uid": "VXvue/VP-1",
        "id": "VP-1",
        "project_id": "VXvue",
        "title": "Sample",
        "status": "draft",
        "content_html": "<p>hello</p>",
        "severity": "normal",
        "priority": "50.0",
        "active": "active",
        "jira_id": None,
        "old_id": "01-10-10",
        "linked_work_items": [],
        "parent_id": None,
        "attachments_meta": [],
        "image_results": [],
        "comments": [],
    }
    base.update(kwargs)
    return base


def test_new_srs_detected():
    diffs = diff_snapshots({}, {"VXvue/VP-1": _rec()})
    assert len(diffs) == 1
    assert diffs[0].change_type == "new"


def test_deleted_srs_detected():
    diffs = diff_snapshots({"VXvue/VP-1": _rec()}, {})
    assert diffs[0].change_type == "deleted"


def test_unchanged_srs():
    rec = _rec()
    diffs = diff_snapshots({"VXvue/VP-1": rec}, {"VXvue/VP-1": dict(rec)})
    assert diffs[0].change_type == "unchanged"
    assert diffs[0].field_changes == []


def test_status_change_detected():
    prev = _rec(status="draft")
    curr = _rec(status="reviewed")
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "status" in diffs[0].field_changes
    assert diffs[0].status_before == "draft"
    assert diffs[0].status_after == "reviewed"


def test_description_text_change_produces_diff_lines():
    prev = _rec(content_html="<p>Detector shall reconnect automatically.</p>")
    curr = _rec(content_html="<p>Detector shall reconnect automatically within 10 seconds.</p>")
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "description" in diffs[0].field_changes
    assert any("10 seconds" in line for line in diffs[0].text_diff_lines)


def test_description_change_produces_sentence_before_after_pair():
    """리포트 표(Before/After)의 재료가 되는 문장 단위 쌍. 안 바뀐 문장은 포함하지 않는다."""
    prev = _rec(
        content_html="<p>Detector shall reconnect automatically.</p><p>Battery status is shown.</p>"
    )
    curr = _rec(
        content_html="<p>Detector shall reconnect automatically within 10 seconds.</p>"
        "<p>Battery status is shown.</p>"
    )
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    changes = diffs[0].sentence_changes
    assert len(changes) == 1
    assert changes[0]["before"] == "Detector shall reconnect automatically."
    assert changes[0]["after"] == "Detector shall reconnect automatically within 10 seconds."


def test_sentence_change_groups_adjacent_replacement_into_one_row():
    prev = _rec(content_html="<p>Sentence A.</p><p>Sentence B.</p>")
    curr = _rec(content_html="<p>Sentence A.</p><p>Sentence C.</p><p>Sentence D.</p>")
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    changes = diffs[0].sentence_changes
    assert changes == [{"before": "Sentence B.", "after": "Sentence C. Sentence D."}]


def test_sentence_change_pure_deletion_leaves_after_empty():
    prev = _rec(content_html="<p>Sentence A.</p><p>Sentence B.</p>")
    curr = _rec(content_html="<p>Sentence A.</p>")
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert diffs[0].sentence_changes == [{"before": "Sentence B.", "after": ""}]


def test_strikethrough_added_detected():
    prev = _rec(content_html="<p>keep this</p>")
    curr = _rec(content_html='<p><span style="text-decoration: line-through;">keep this</span></p>')
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "strikethrough_added" in diffs[0].field_changes


def test_underline_added_detected():
    prev = _rec(content_html="<p>keep this</p>")
    curr = _rec(content_html='<p><span style="text-decoration: underline;">keep this</span></p>')
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "underline_added" in diffs[0].field_changes


def test_linked_work_items_change_detected():
    prev = _rec(parent_id="VP-100")
    curr = _rec(parent_id="VP-200")
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "linked_work_items" in diffs[0].field_changes


def test_image_change_detected():
    prev = _rec(image_results=[{"filename": "a.png", "ok": True}])
    curr = _rec(image_results=[{"filename": "b.png", "ok": True}])
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "images" in diffs[0].field_changes


def test_comment_added_detected():
    prev = _rec(comments=[])
    curr = _rec(comments=[{"id": "c1", "text": "reviewed, looks good"}])
    diffs = diff_snapshots({"VXvue/VP-1": prev}, {"VXvue/VP-1": curr})
    assert "comments" in diffs[0].field_changes
