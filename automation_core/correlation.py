from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from automation_core.config import PriorityRules


REFERENCE_RE = re.compile(
    r"(?<![A-Z0-9_/-])(?:([A-Z][A-Z0-9_-]*)/)?([A-Z][A-Z0-9_]*-[0-9]+)(?![A-Z0-9_/-])"
)
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


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _srs_updated_window(change: dict) -> tuple[datetime, datetime] | None:
    start = _parse_timestamp(workitem_attributes(change.get("before")).get("updated"))
    end = _parse_timestamp(workitem_attributes(change.get("after")).get("updated"))
    if start is None or end is None or end <= start:
        return None
    return start, end


def _issue_activity_fields(
    record: dict,
    start: datetime,
    end: datetime,
) -> list[str]:
    attributes = workitem_attributes(record)
    return [
        field
        for field in ("created", "updated")
        if (timestamp := _parse_timestamp(attributes.get(field))) is not None
        and start < timestamp <= end
    ]


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


def extract_polarion_references(value: object) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, str):
        for match in REFERENCE_RE.finditer(value.upper()):
            project, item_id = match.groups()
            found.add(f"{project}/{item_id}" if project else item_id)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(extract_polarion_references(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(extract_polarion_references(item))
    return frozenset(found)


def extract_polarion_ids(value: object) -> frozenset[str]:
    return frozenset(reference.rsplit("/", 1)[-1] for reference in extract_polarion_references(value))


def _record_indexes(
    records: dict[str, dict],
) -> tuple[dict[str, tuple[str, dict]], dict[str, list[tuple[str, dict]]]]:
    by_full: dict[str, tuple[str, dict]] = {}
    by_item: dict[str, list[tuple[str, dict]]] = {}
    for item in records.values():
        item_id = str(item["itemId"]).upper()
        logical_id = f"{item['project']}/{item['itemId']}"
        entry = (logical_id, item)
        by_full[logical_id.upper()] = entry
        by_item.setdefault(item_id, []).append(entry)
    return by_full, by_item


def _resolve_references(
    value: object,
    source_project: str,
    indexes: tuple[dict[str, tuple[str, dict]], dict[str, list[tuple[str, dict]]]],
) -> list[tuple[str, dict]]:
    by_full, by_item = indexes
    resolved: dict[str, tuple[str, dict]] = {}
    for reference in extract_polarion_references(value):
        if "/" in reference:
            entry = by_full.get(reference.upper())
        else:
            same_project = by_full.get(f"{source_project}/{reference}".upper())
            matches = by_item.get(reference.upper(), [])
            entry = same_project or (matches[0] if len(matches) == 1 else None)
        if entry is not None:
            resolved[entry[0]] = entry
    return [resolved[key] for key in sorted(resolved)]


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
    issue_indexes = _record_indexes(issue_records)
    srs_indexes = _record_indexes(srs_records)
    result: list[dict] = []

    for change in [*srs_changes, *issue_changes]:
        if change["source"] == "srs":
            linked_by_id: dict[str, tuple[str, dict]] = {}
            evidence: list[dict] = []
            for entry in _resolve_references(change["after"], change["project"], issue_indexes):
                linked_by_id[entry[0]] = entry
                evidence.append({"direction": "SRS_TO_ISSUE", "id": entry[0]})
            changed_id = f"{change['project']}/{change['itemId']}"
            for issue in issue_records.values():
                targets = _resolve_references(issue["record"], issue["project"], srs_indexes)
                if any(target_id == changed_id for target_id, _ in targets):
                    issue_id = f"{issue['project']}/{issue['itemId']}"
                    linked_by_id[issue_id] = (issue_id, issue)
                    evidence.append({"direction": "ISSUE_TO_SRS", "id": issue_id})
            issue_window = _srs_updated_window(change)
            if issue_window is not None:
                activity_by_id = {
                    logical_id: _issue_activity_fields(item["record"], *issue_window)
                    for logical_id, item in linked_by_id.values()
                }
                linked_by_id = {
                    logical_id: entry
                    for logical_id, entry in linked_by_id.items()
                    if activity_by_id[logical_id]
                }
                evidence = [
                    {
                        **item,
                        "activityFields": activity_by_id[item["id"]],
                    }
                    for item in evidence
                    if activity_by_id.get(item["id"])
                ]
            linked = [linked_by_id[key] for key in sorted(linked_by_id)]
            relevant_issues = [item["record"] for _, item in linked]
            priority, reasons = _srs_priority(relevant_issues, rules)
        else:
            linked_by_id = {}
            evidence = []
            for entry in _resolve_references(change["after"], change["project"], srs_indexes):
                linked_by_id[entry[0]] = entry
                evidence.append({"direction": "ISSUE_TO_SRS", "id": entry[0]})
            changed_id = f"{change['project']}/{change['itemId']}"
            for srs in srs_records.values():
                targets = _resolve_references(srs["record"], srs["project"], issue_indexes)
                if any(target_id == changed_id for target_id, _ in targets):
                    srs_id = f"{srs['project']}/{srs['itemId']}"
                    linked_by_id[srs_id] = (srs_id, srs)
                    evidence.append({"direction": "SRS_TO_ISSUE", "id": srs_id})
            linked = [linked_by_id[key] for key in sorted(linked_by_id)]
            priority, reasons = "MEDIUM", ["CHANGED_ISSUE"]
            issue_window = None

        linked_ids = sorted(logical_id for logical_id, _ in linked)
        unique_evidence = {
            (
                item["direction"],
                item["id"],
                tuple(item.get("activityFields", [])),
            )
            for item in evidence
        }
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
                "linkEvidence": [
                    {
                        "direction": direction,
                        "id": item_id,
                        **({"activityFields": list(activity_fields)} if activity_fields else {}),
                    }
                    for direction, item_id, activity_fields in sorted(
                        unique_evidence, key=lambda item: (item[1], item[0], item[2])
                    )
                ],
                **(
                    {
                        "issueWindow": {
                            "startExclusive": workitem_attributes(change["before"])["updated"],
                            "endInclusive": workitem_attributes(change["after"])["updated"],
                        }
                    }
                    if issue_window is not None
                    else {}
                ),
            }
        )

    return sorted(
        result,
        key=lambda item: (PRIORITY_ORDER[item["priority"]], item["project"], item["itemId"]),
    )
