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


def issue_record(
    item_id: str,
    *,
    title: str,
    status: str,
    severity: str = "normal",
    created: str | None = None,
    updated: str | None = None,
) -> dict:
    attributes = {
        "id": item_id,
        "title": title,
        "status": status,
        "severity": severity,
    }
    if created is not None:
        attributes["created"] = created
    if updated is not None:
        attributes["updated"] = updated
    return {
        "workitem": {
            "id": f"P/{item_id}",
            "attributes": attributes,
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


def test_incoming_issue_link_raises_changed_srs_priority() -> None:
    srs_after = {"id": "SRS-1", "title": "Changed"}
    incoming = issue_record(
        "ISSUE-7", title="Incoming", status="reopened", severity="critical"
    )
    incoming["linkedWorkItems"] = ["P/SRS-1"]

    result = correlate(
        [changed("srs", "P", "SRS-1", srs_after)],
        [],
        {"P/SRS-1": wrapped("P", "SRS-1", srs_after)},
        {"P/ISSUE-7": wrapped("P", "ISSUE-7", incoming)},
        rules(),
    )

    assert result[0]["linkedIds"] == ["P/ISSUE-7"]
    assert result[0]["priority"] == "CRITICAL"
    assert result[0]["linkEvidence"] == [
        {"direction": "ISSUE_TO_SRS", "id": "P/ISSUE-7"}
    ]


def test_project_qualified_link_never_resolves_to_same_id_in_other_project() -> None:
    srs_after = {"id": "SRS-1", "linkedWorkItems": ["P/ISSUE-7"]}
    result = correlate(
        [changed("srs", "P", "SRS-1", srs_after)],
        [],
        {"P/SRS-1": wrapped("P", "SRS-1", srs_after)},
        {
            "P/ISSUE-7": wrapped(
                "P", "ISSUE-7", issue_record("ISSUE-7", title="P", status="open")
            ),
            "Q/ISSUE-7": wrapped(
                "Q",
                "ISSUE-7",
                issue_record("ISSUE-7", title="Q", status="reopened", severity="critical"),
            ),
        },
        rules(),
    )

    assert result[0]["linkedIds"] == ["P/ISSUE-7"]
    assert result[0]["priority"] == "HIGH"


def test_changed_srs_only_considers_linked_issues_active_in_its_updated_window() -> None:
    before = {
        "id": "SRS-1",
        "title": "Before",
        "updated": "2026-09-14T09:00:00Z",
    }
    after = {
        "id": "SRS-1",
        "title": "After",
        "updated": "2026-09-21T09:00:00Z",
        "linkedWorkItems": ["P/ISSUE-7", "P/ISSUE-8"],
    }
    srs_change = compute_changes(
        "srs",
        {"P/SRS-1": wrapped("P", "SRS-1", after)},
        {"P/SRS-1": wrapped("P", "SRS-1", before)},
    )[0]
    old = issue_record(
        "ISSUE-7",
        title="Old critical issue",
        status="reopened",
        severity="critical",
        created="2026-09-01T00:00:00Z",
        updated="2026-09-14T09:00:00Z",
    )
    created_in_window = issue_record(
        "ISSUE-8",
        title="Created during SRS change",
        status="open",
        created="2026-09-21T09:00:00Z",
        updated="2026-09-22T00:00:00Z",
    )
    updated_in_window = issue_record(
        "ISSUE-9",
        title="Updated during SRS change",
        status="open",
        created="2026-08-01T00:00:00Z",
        updated="2026-09-20T00:00:00Z",
    )
    updated_in_window["linkedWorkItems"] = ["P/SRS-1"]

    result = correlate(
        [srs_change],
        [],
        {"P/SRS-1": wrapped("P", "SRS-1", after)},
        {
            "P/ISSUE-7": wrapped("P", "ISSUE-7", old),
            "P/ISSUE-8": wrapped("P", "ISSUE-8", created_in_window),
            "P/ISSUE-9": wrapped("P", "ISSUE-9", updated_in_window),
        },
        rules(),
    )

    assert result[0]["linkedIds"] == ["P/ISSUE-8", "P/ISSUE-9"]
    assert result[0]["priority"] == "HIGH"
    assert result[0]["issueWindow"] == {
        "startExclusive": "2026-09-14T09:00:00Z",
        "endInclusive": "2026-09-21T09:00:00Z",
    }
    assert result[0]["linkEvidence"] == [
        {
            "direction": "SRS_TO_ISSUE",
            "id": "P/ISSUE-8",
            "activityFields": ["created"],
        },
        {
            "direction": "ISSUE_TO_SRS",
            "id": "P/ISSUE-9",
            "activityFields": ["updated"],
        },
    ]
