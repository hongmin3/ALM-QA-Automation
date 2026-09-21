from __future__ import annotations

from automation_core.config import PriorityRules
from automation_core.correlation import compute_changes, correlate, extract_polarion_ids


def rules() -> PriorityRules:
    return PriorityRules(
        reopened_statuses=frozenset({"reopened"}),
        open_statuses=frozenset({"open", "in_progress", "reopened"}),
        critical_severities=frozenset({"blocker", "critical"}),
        notify_priorities=frozenset({"CRITICAL", "HIGH", "MEDIUM"}),
    )


def wrapped(project: str, item_id: str, record: dict) -> dict:
    return {"project": project, "itemId": item_id, "record": record}


def changed(source: str, project: str, item_id: str, record: dict) -> dict:
    current = {f"{project}/{item_id}": wrapped(project, item_id, record)}
    previous = {
        f"{project}/{item_id}": wrapped(project, item_id, {**record, "title": "before"})
    }
    return compute_changes(source, current, previous)[0]


def issue_record(item_id: str, *, title: str, status: str, severity: str = "normal") -> dict:
    return {
        "workitem": {
            "id": f"P/{item_id}",
            "attributes": {
                "id": item_id,
                "title": title,
                "status": status,
                "severity": severity,
            },
        },
        "comments": [],
        "linkedWorkItems": [],
        "attachments": [],
    }


def test_links_only_exact_ids_and_does_not_match_similar_titles() -> None:
    srs_after = {"id": "SRS-1", "title": "Login failure", "linkedWorkItems": ["P/ISSUE-7"]}
    srs_change = changed("srs", "P", "SRS-1", srs_after)
    linked = wrapped("P", "ISSUE-7", issue_record("ISSUE-7", title="Different", status="open"))
    same_title = wrapped("P", "ISSUE-8", issue_record("ISSUE-8", title="Login failure", status="open"))

    result = correlate(
        [srs_change],
        [],
        {"P/SRS-1": wrapped("P", "SRS-1", srs_after)},
        {"P/ISSUE-7": linked, "P/ISSUE-8": same_title},
        rules(),
    )

    assert result[0]["linkedIds"] == ["P/ISSUE-7"]
    assert result[0]["priority"] == "HIGH"


def test_reopened_critical_link_is_critical() -> None:
    srs_after = {"id": "SRS-1", "linkedWorkItems": ["P/ISSUE-7"]}
    result = correlate(
        [changed("srs", "P", "SRS-1", srs_after)],
        [],
        {"P/SRS-1": wrapped("P", "SRS-1", srs_after)},
        {
            "P/ISSUE-7": wrapped(
                "P",
                "ISSUE-7",
                issue_record("ISSUE-7", title="Different", status="reopened", severity="critical"),
            )
        },
        rules(),
    )

    assert result[0]["priority"] == "CRITICAL"
    assert set(result[0]["reasons"]) == {
        "CHANGED_SRS",
        "LINKED_REOPENED_ISSUE",
        "CRITICAL_SEVERITY",
    }


def test_unknown_values_and_issue_only_changes_remain_medium() -> None:
    issue_after = issue_record("ISSUE-7", title="Changed", status="custom", severity="custom")
    result = correlate(
        [],
        [changed("issues", "P", "ISSUE-7", issue_after)],
        {},
        {"P/ISSUE-7": wrapped("P", "ISSUE-7", issue_after)},
        rules(),
    )

    assert result[0]["priority"] == "MEDIUM"
    assert result[0]["reasons"] == ["CHANGED_ISSUE"]


def test_first_complete_input_is_baseline_without_candidates() -> None:
    current = {"P/SRS-1": wrapped("P", "SRS-1", {"id": "SRS-1", "title": "A"})}

    assert compute_changes("srs", current, None) == []


def test_id_extraction_uses_token_boundaries() -> None:
    value = {"text": "See ISSUE-7 and P/I-2, but not XISSUE-71Z."}

    assert extract_polarion_ids(value) == frozenset({"ISSUE-7", "I-2"})
