from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from automation_core.config import AutomationConfig
from automation_core.correlation import compute_changes, correlate
from automation_core.email import DeliveryResult, drain_outbox
from automation_core.state import AutomationStore
from observation import load_issues, load_srs


CommandRunner = Callable[[list[str], Path], int]
MessageSender = Callable[[EmailMessage], bool]


class AutomationBusyError(RuntimeError):
    pass


def srs_command(root: Path, srs_config: Path) -> tuple[list[str], Path]:
    return [
        sys.executable,
        str(root / "run.py"),
        "srs",
        "--config",
        str(srs_config),
        "--no-mail",
    ], root


def issue_command(
    root: Path,
    output: Path,
    issue_config: Path,
) -> tuple[list[str], Path]:
    return [
        sys.executable,
        str(root / "run.py"),
        "issues",
        "--config",
        str(issue_config),
        "--out",
        str(output),
    ], root


def sanitize_candidates(candidates: list[dict]) -> list[dict]:
    allowed = (
        "id",
        "priority",
        "project",
        "itemId",
        "reasons",
        "linkedIds",
        "linkEvidence",
        "issueWindow",
        "statusChange",
    )
    return [{key: candidate.get(key) for key in allowed} for candidate in candidates]


def render_summary(manifest: dict) -> str:
    stages = manifest.get("stages", {})
    lines = [
        "# ALM QA automation",
        "",
        f"Run: {manifest['runId']}",
        f"Status: {manifest['status']}",
        f"Data complete: {manifest['dataComplete']}",
        "",
    ]
    lines.extend(f"- {name}: {status}" for name, status in sorted(stages.items()))
    return "\n".join(lines) + "\n"


def _empty_delivery() -> DeliveryResult:
    return DeliveryResult([], [], [], [])


def _merge_delivery(*results: DeliveryResult) -> DeliveryResult:
    return DeliveryResult(
        sent=[item for result in results for item in result.sent],
        pending=[item for result in results for item in result.pending],
        failed=[item for result in results for item in result.failed],
        ambiguous=[item for result in results for item in result.ambiguous],
    )


def _relative_input(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def run_automation(
    config: AutomationConfig,
    store: AutomationStore,
    runner: CommandRunner,
    sender: MessageSender | None,
    now: datetime,
) -> dict:
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    local_date = now.astimezone().date().isoformat()
    stages = {
        name: "NOT_RUN"
        for name in ("srs", "issues", "analysis", "outbox", "email")
    }
    progress = {
        "runId": run_id,
        "localDate": local_date,
        "status": "RUNNING",
        "dataComplete": False,
        "resumedOutboxOnly": False,
        "stages": dict(stages),
        "inputs": {},
    }

    def deliver() -> DeliveryResult:
        if sender is None:
            return _empty_delivery()
        return drain_outbox(
            store,
            sender,
            now,
            config.max_email_attempts,
            config.retry_minutes,
        )

    def checkpoint() -> None:
        progress["stages"] = dict(stages)
        store.save_run(run_id, progress, render_summary(progress))

    def finish(manifest: dict) -> dict:
        manifest.setdefault("inputs", dict(progress["inputs"]))
        store.save_run(run_id, manifest, render_summary(manifest))
        state = store.load_state()
        state["runs"][run_id] = manifest
        store.save_state(state)
        return manifest

    initial_delivery = _empty_delivery()

    def fail(stage: str, code: int) -> dict:
        stages[stage] = "FAILED"
        summary_path = f".automation/runs/{run_id}/summary.md"
        message_id, _ = store.enqueue(
            {
                "kind": "RUN_FAILED",
                "status": "FAILED",
                "stage": stage,
                "runId": run_id,
                "summaryPath": summary_path,
            }
        )
        stages["outbox"] = "SUCCESS"
        final_delivery = deliver()
        unresolved = store.has_unresolved_messages()
        stages["email"] = "PARTIAL" if unresolved else "SUCCESS"
        return finish(
            {
                "runId": run_id,
                "localDate": local_date,
                "status": "FAILED",
                "dataComplete": False,
                "failedStage": stage,
                "childExitCode": code,
                "failureMessageId": message_id,
                "stages": dict(stages),
                "inputs": dict(progress["inputs"]),
                "delivery": asdict(_merge_delivery(initial_delivery, final_delivery)),
            }
        )

    try:
        lock = store.locked(blocking=False)
        lock.__enter__()
    except OSError as exc:
        raise AutomationBusyError("another automation run holds the lock") from exc

    try:
        store.migrate_observation(config.root / ".observation" / "state.json")
        checkpoint()
        initial_delivery = deliver()
        prior = store.successful_run_for(local_date)
        if prior:
            unresolved = store.has_unresolved_messages()
            stages["outbox"] = "SUCCESS"
            stages["email"] = "PARTIAL" if unresolved else "SUCCESS"
            return finish(
                {
                    "runId": run_id,
                    "localDate": local_date,
                    "status": "PARTIAL" if unresolved else "SUCCESS",
                    "dataComplete": True,
                    "resumedOutboxOnly": True,
                    "stages": dict(stages),
                    "delivery": asdict(initial_delivery),
                }
            )

        args, cwd = srs_command(config.root, config.srs_config)
        try:
            code = runner(args, cwd)
        except OSError:
            code = 1
        if code != 0:
            return fail("srs", code)
        stages["srs"] = "SUCCESS"
        progress["inputs"]["srsSnapshot"] = _relative_input(
            config.srs_snapshot_dir / local_date, config.root
        )
        checkpoint()

        issue_output = store.root / "collections" / run_id / "issues"
        args, cwd = issue_command(config.root, issue_output, config.issue_config)
        try:
            code = runner(args, cwd)
        except OSError:
            code = 1
        if code != 0:
            return fail("issues", code)
        stages["issues"] = "SUCCESS"
        progress["inputs"]["issueManifest"] = _relative_input(
            issue_output / "manifest.json", config.root
        )
        checkpoint()

        try:
            current_srs = load_srs(config.srs_snapshot_dir / local_date)
        except (OSError, ValueError, TypeError, KeyError):
            return fail("srs", 4)
        try:
            current_issues = load_issues(issue_output / "manifest.json")
        except (OSError, ValueError, TypeError, KeyError):
            return fail("issues", 4)

        state = store.load_state()
        srs_changes = compute_changes("srs", current_srs, state["sources"].get("srs"))
        issue_changes = compute_changes(
            "issues", current_issues, state["sources"].get("issues")
        )
        candidates = correlate(
            srs_changes,
            issue_changes,
            current_srs,
            current_issues,
            config.rules,
        )
        stages["analysis"] = "SUCCESS"
        checkpoint()

        new_candidates: list[dict] = []
        for candidate in candidates:
            if candidate["id"] in state["candidates"]:
                continue
            candidate["reviewState"] = "NEW"
            candidate["firstRunId"] = run_id
            state["candidates"][candidate["id"]] = candidate
            new_candidates.append(candidate)
        state["sources"] = {"srs": current_srs, "issues": current_issues}

        notified = [
            candidate
            for candidate in new_candidates
            if candidate["priority"] in config.rules.notify_priorities
        ]
        if notified:
            store.enqueue(
                {
                    "kind": "CANDIDATES",
                    "status": "SUCCESS",
                    "runId": run_id,
                    "summaryPath": f".automation/runs/{run_id}/summary.md",
                    "candidates": sanitize_candidates(notified),
                }
            )
        stages["outbox"] = "SUCCESS"
        store.save_state(state)
        checkpoint()

        final_delivery = deliver()
        unresolved = store.has_unresolved_messages()
        stages["email"] = "PARTIAL" if unresolved else "SUCCESS"
        return finish(
            {
                "runId": run_id,
                "localDate": local_date,
                "status": "PARTIAL" if unresolved else "SUCCESS",
                "dataComplete": True,
                "resumedOutboxOnly": False,
                "candidateCount": len(new_candidates),
                "notificationCount": len(notified),
                "stages": dict(stages),
                "inputs": dict(progress["inputs"]),
                "delivery": asdict(_merge_delivery(initial_delivery, final_delivery)),
            }
        )
    finally:
        lock.__exit__(None, None, None)
