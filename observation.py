"""Offline observation of explicitly selected saved collections (REQ-OBS-001).

state.json is the atomic commit record. Only run directories referenced by it
are committed; a crash can leave an unreferenced run directory. No input is
modified and no absence is interpreted as deletion.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from automation_core.config import PriorityRules
from automation_core.correlation import compute_changes, correlate
from automation_core.state import AutomationStore


REVIEW_STATES = ("NEW", "IN_REVIEW", "DONE", "EXCLUDED")
VOLATILE = {"collected_at", "collectedAt", "generatedAt", "elapsedSeconds",
            "local_path", "relative_path", "downloaded_at", "outputDirectory"}


def read_json(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def semantic(value):
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def add_record(records, project, item_id, record):
    if not isinstance(project, str) or not project.strip() or not isinstance(item_id, str) or not item_id.strip():
        raise ValueError("Missing project/item identity")
    key = encoded([project, item_id])
    if key in records:
        raise ValueError("Duplicate project/item identity")
    records[key] = {"project": project, "itemId": item_id, "record": semantic(record)}


def load_srs(root):
    if not root.is_dir():
        raise ValueError("SRS date directory does not exist")
    records = {}
    for path in sorted(root.glob("*/*.json")):
        record = read_json(path)
        project, item_id = record.get("project_id"), record.get("id")
        if project != path.parent.name or record.get("uid") != f"{project}/{item_id}":
            raise ValueError("SRS identity does not match snapshot layout")
        add_record(records, project, item_id, record)
    if not records:
        raise ValueError("SRS snapshot has no verifiable records")
    return records


def load_issues(path):
    manifest = read_json(path)
    counts = ("count", "successCount", "failureCount", "selectedCount")
    if any(type(manifest.get(key)) is not int or manifest[key] < 0 for key in counts):
        raise ValueError("Unverified issue manifest: missing/invalid counts")
    if (manifest.get("status") != "SUCCESS" or manifest.get("limited") is not False
            or manifest.get("duplicateIds") != [] or manifest["failureCount"] != 0
            or len({manifest[key] for key in ("count", "successCount", "selectedCount")}) != 1):
        raise ValueError("Unverified issue manifest: collection is incomplete")
    if (manifest.get("pdfStatus") == "FAILED"
            or ("missingIdCount" in manifest and (type(manifest["missingIdCount"]) is not int or manifest["missingIdCount"] != 0))
            or ("warnings" in manifest and manifest["warnings"] != [])
            or ("searchedCount" in manifest and (type(manifest["searchedCount"]) is not int or manifest["searchedCount"] != manifest["selectedCount"]))):
        raise ValueError("Unverified issue manifest: incomplete output or selection")
    records = {}
    for backup_path in sorted(path.parent.glob("*/backup.json")):
        backup = read_json(backup_path)
        if not all(key in backup for key in ("workitem", "comments", "linkedWorkItems", "attachments")):
            raise ValueError("Incomplete issue backup")
        if any(not isinstance(backup[key], list) for key in ("comments", "linkedWorkItems", "attachments")):
            raise ValueError("Invalid issue backup collections")
        workitem = backup["workitem"]
        if not isinstance(workitem, dict) or not isinstance(workitem.get("attributes"), dict):
            raise ValueError("Invalid issue workitem")
        identity = workitem.get("id", "")
        if not isinstance(identity, str) or "/" not in identity:
            raise ValueError("Issue project identity is unavailable")
        project, item_id = identity.split("/", 1)
        if workitem["attributes"].get("id") != item_id:
            raise ValueError("Issue identity is inconsistent")
        add_record(records, project, item_id, backup)
    if len(records) != manifest["successCount"]:
        raise ValueError("Issue backup count does not match manifest")
    return records


@contextmanager
def run_lock(root):
    try:
        with AutomationStore(root).locked(blocking=False):
            yield
    except OSError as exc:
        raise ValueError("Another observation run holds the lock") from exc


def write_file(path, text):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def status_of(source, record):
    if record is None:
        return None
    return record.get("status") if source == "srs" else record["workitem"]["attributes"].get("status")


def markdown(summary):
    lines = ["# Offline observation", "", f"Run: {summary['runId']}",
             f"Baseline sources: {', '.join(summary['baselineSources']) or 'none'}",
             "No deletion inference; no notifications or network access.", ""]
    for candidate in summary["candidates"]:
        lines.extend([f"## Candidate {candidate['id']}",
                      f"{candidate['source']}: {candidate['project']}/{candidate['itemId']}",
                      f"Change: {candidate['change']} | Review: {candidate['reviewState']}",
                      "", "Evidence (JSON):", "", "````json", encoded(candidate), "````", ""])
    return "\n".join(lines)


def validate_state(state):
    if (state.get("schemaVersion") != 2
            or any(not isinstance(state.get(key), dict) for key in ("sources", "candidates", "runs"))):
        raise ValueError("Unsupported or corrupt observation state")
    for source, records in state["sources"].items():
        if source not in ("srs", "issues") or not isinstance(records, dict):
            raise ValueError("Corrupt observation source state")
        for item in records.values():
            if not isinstance(item, dict) or not isinstance(item.get("record"), dict):
                raise ValueError("Corrupt observation record state")
    for candidate in state["candidates"].values():
        if not isinstance(candidate, dict) or candidate.get("reviewState") not in REVIEW_STATES:
            raise ValueError("Corrupt candidate state")


def observe(args):
    root = args.state_dir.resolve()
    with run_lock(root):
        store = AutomationStore(root)
        if root.name == ".automation":
            store.migrate_observation(root.parent / ".observation" / "state.json")
        state = store.load_state()
        validate_state(state)
        sources = {}
        if args.srs_current:
            sources["srs"] = load_srs(args.srs_current)
        if args.issues:
            sources["issues"] = load_issues(args.issues)
        previous_srs = load_srs(args.srs_previous) if args.srs_previous else None
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex
        summary = {"runId": run_id, "status": "SAVED", "baselineSources": [],
                   "sourceCounts": {}, "candidateIds": [], "newCandidateIds": [],
                   "candidates": [], "reviewUpdate": None,
                   "inputs": {key: str(value.resolve()) for key, value in (
                       ("srsCurrent", args.srs_current), ("srsPrevious", args.srs_previous),
                       ("issues", args.issues)) if value is not None}}
        changes = {"srs": [], "issues": []}
        for source, records in sources.items():
            previous = previous_srs if source == "srs" and previous_srs is not None else state["sources"].get(source)
            summary["sourceCounts"][source] = len(records)
            if previous is None:
                summary["baselineSources"].append(source)
            else:
                changes[source] = compute_changes(source, records, previous)
            # Absences are not deletions, even for a successful collection of a
            # different query. Retain last-known records until explicitly seen.
            state["sources"][source] = {**state["sources"].get(source, {}), **records}
        default_rules = PriorityRules(
            reopened_statuses=frozenset({"reopened"}),
            open_statuses=frozenset({"open", "in_progress", "in_review", "reopened"}),
            critical_severities=frozenset({"blocker", "critical"}),
            notify_priorities=frozenset({"CRITICAL", "HIGH", "MEDIUM"}),
        )
        correlated = correlate(
            changes["srs"],
            changes["issues"],
            state["sources"].get("srs", {}),
            state["sources"].get("issues", {}),
            default_rules,
        )
        for candidate in correlated:
            candidate_id = candidate["id"]
            if candidate_id not in state["candidates"]:
                state["candidates"][candidate_id] = {
                    **candidate,
                    "reviewState": "NEW",
                    "firstRunId": run_id,
                }
                summary["newCandidateIds"].append(candidate_id)
            summary["candidateIds"].append(candidate_id)
        if args.candidate:
            if args.candidate not in state["candidates"]:
                raise ValueError("Unknown candidate ID")
            state["candidates"][args.candidate]["reviewState"] = args.review_state
            summary["reviewUpdate"] = {"id": args.candidate, "reviewState": args.review_state}
        summary["candidates"] = [copy.deepcopy(state["candidates"][key]) for key in summary["candidateIds"]]
        state["runs"][run_id] = summary
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True)
        try:
            store.atomic_text(run_dir / "summary.json", encoded(summary))
            store.atomic_text(run_dir / "summary.md", markdown(summary))
            store.save_state(state)
        except BaseException:
            for filename in ("summary.json", "summary.md"):
                (run_dir / filename).unlink(missing_ok=True)
            run_dir.rmdir()
            raise
        return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyze saved SRS/issues offline; no network, deployment or notifications.")
    parser.add_argument("--srs-current", type=Path, help="Snapshot date directory containing project JSON directories")
    parser.add_argument("--srs-previous", type=Path, help="Explicit prior SRS date directory")
    parser.add_argument("--issues", type=Path, help="Verified successful issue manifest.json")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent / ".automation")
    parser.add_argument("--candidate", help="Candidate ID whose review state should change")
    parser.add_argument("--review-state", choices=REVIEW_STATES)
    args = parser.parse_args(argv)
    if bool(args.candidate) != bool(args.review_state):
        parser.error("--candidate and --review-state must be supplied together")
    if not (args.srs_current or args.issues or args.candidate):
        parser.error("provide a saved source or candidate review update")
    if args.srs_previous and not args.srs_current:
        parser.error("--srs-previous requires --srs-current")
    try:
        summary = observe(args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Avoid exposing raw JSON/source content in errors.
        print(f"Observation failed ({type(exc).__name__}); verify input completeness, state and lock.", file=sys.stderr)
        return 2
    print(f"Saved observation {summary['runId']}: {len(summary['newCandidateIds'])} new candidate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
