from __future__ import annotations

import hashlib
import json
import re

from automation_core.config import PriorityRules


ID_RE = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]*-[0-9]+)(?![A-Z0-9_])")
PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}


def digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def workitem_attributes(record: dict | None) -> dict:
    if not record:
        return {}
    workitem = record.get("workitem", record)
    if not isinstance(workitem, dict):
        return {}
    attributes = workitem.get("attributes", workitem)
    return attributes if isinstance(attributes, dict) else {}


def status_of(record: dict | None) -> str | None:
    value = workitem_attributes(record).get("status")
    return str(value) if value is not None else None


def severity_of(record: dict | None) -> str:
    attributes = workitem_attributes(record)
    return str(attributes.get("severity") or attributes.get("defectSeverity") or "")


def compute_changes(
    source: str,
    current: dict[str, dict],
    previous: dict[str, dict] | None,
) -> list[dict]:
    if previous is None:
        return []
    changes: list[dict] = []
    for key, item in sorted(current.items()):
        prior = previous.get(key)
        before = prior.get("record") if isinstance(prior, dict) else None
        after = item["record"]
        if before == after:
            continue
        changes.append(
            {
                "source": source,
                "project": item["project"],
                "itemId": item["itemId"],
                "change": "NEW" if before is None else "CHANGED",
                "beforeVersion": digest(before),
                "afterVersion": digest(after),
                "before": before,
                "after": after,
                "statusChange": {"before": status_of(before), "after": status_of(after)},
            }
        )
    return changes


def extract_polarion_ids(value: object) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(ID_RE.findall(value.upper()))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(extract_polarion_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(extract_polarion_ids(item))
    return frozenset(found)


def _records_by_item_id(records: dict[str, dict]) -> dict[str, tuple[str, dict]]:
    indexed: dict[str, tuple[str, dict]] = {}
    for item in records.values():
        item_id = str(item["itemId"])
        logical_id = f"{item['project']}/{item_id}"
        indexed[item_id.upper()] = (logical_id, item)
    return indexed


def _srs_priority(
    relevant_issues: list[dict],
    rules: PriorityRules,
) -> tuple[str, list[str]]:
    reasons = ["CHANGED_SRS"]
    reopened = any(
        (status_of(issue) or "").casefold() in rules.reopened_statuses
        for issue in relevant_issues
    )
    severe = any(
        severity_of(issue).casefold() in rules.critical_severities
        for issue in relevant_issues
    )
    if reopened or severe:
        if reopened:
            reasons.append("LINKED_REOPENED_ISSUE")
        if severe:
            reasons.append("CRITICAL_SEVERITY")
        return "CRITICAL", reasons
    if any(
        (status_of(issue) or "").casefold() in rules.open_statuses
        for issue in relevant_issues
    ):
        return "HIGH", reasons + ["LINKED_OPEN_ISSUE"]
    return "MEDIUM", reasons


def correlate(
    srs_changes: list[dict],
    issue_changes: list[dict],
    srs_records: dict[str, dict],
    issue_records: dict[str, dict],
    rules: PriorityRules,
) -> list[dict]:
    issues_by_id = _records_by_item_id(issue_records)
    srs_by_id = _records_by_item_id(srs_records)
    result: list[dict] = []

    for change in [*srs_changes, *issue_changes]:
        references = extract_polarion_ids(change["after"])
        if change["source"] == "srs":
            linked = [issues_by_id[item_id] for item_id in references if item_id in issues_by_id]
            relevant_issues = [item["record"] for _, item in linked]
            priority, reasons = _srs_priority(relevant_issues, rules)
        else:
            linked = [srs_by_id[item_id] for item_id in references if item_id in srs_by_id]
            priority, reasons = "MEDIUM", ["CHANGED_ISSUE"]

        linked_ids = sorted(logical_id for logical_id, _ in linked)
        identity = [
            change["source"],
            change["project"],
            change["itemId"],
            change["beforeVersion"],
            change["afterVersion"],
            linked_ids,
        ]
        result.append(
            {
                **change,
                "id": digest(identity),
                "priority": priority,
                "reasons": sorted(set(reasons)),
                "linkedIds": linked_ids,
            }
        )

    return sorted(
        result,
        key=lambda item: (PRIORITY_ORDER[item["priority"]], item["project"], item["itemId"]),
    )
