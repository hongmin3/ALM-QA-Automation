from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from automation_core.email import build_digest, drain_outbox
from automation_core.state import AutomationStore


FIXED_NOW = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)


def candidate_payload(candidate_ids: list[str], **private_values: str) -> dict:
    return {
        "kind": "CANDIDATES",
        "status": "SUCCESS",
        "summaryPath": ".automation/runs/example/summary.md",
        "candidateIds": candidate_ids,
        "candidates": [
            {
                "priority": "HIGH",
                "project": "P",
                "itemId": candidate_ids[0],
                "reasons": ["CHANGED_SRS", "LINKED_OPEN_ISSUE"],
                "linkedIds": ["P/ISSUE-7"],
                "statusChange": {"before": "draft", "after": "approved"},
                **private_values,
            }
        ],
        **private_values,
    }


def only_message(store: AutomationStore) -> dict:
    return next(iter(store.load_outbox()["messages"].values()))


def seeded_outbox(
    tmp_path: Path,
    *,
    status: str = "PENDING",
    attempts: int = 0,
    next_attempt_at: datetime | None = None,
) -> AutomationStore:
    store = AutomationStore(tmp_path / ".automation")
    store.enqueue(candidate_payload(["candidate-a"]))
    outbox = store.load_outbox()
    item = next(iter(outbox["messages"].values()))
    item["status"] = status
    item["attempts"] = attempts
    item["nextAttemptAt"] = next_attempt_at.isoformat() if next_attempt_at else None
    store.save_outbox(outbox)
    return store


def test_saved_message_is_sent_once_and_replay_does_not_resend(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / ".automation")
    message_id, _ = store.enqueue(candidate_payload(["candidate-a"]))
    attempts = []

    result = drain_outbox(store, lambda message: attempts.append(message) or True, FIXED_NOW, 3, (15, 60, 240))
    assert result.sent == [message_id]
    assert len(attempts) == 1

    result = drain_outbox(store, lambda message: attempts.append(message) or True, FIXED_NOW, 3, (15, 60, 240))
    assert result.sent == []
    assert len(attempts) == 1


def test_three_failures_become_failed_and_can_be_requeued(tmp_path: Path) -> None:
    store = seeded_outbox(tmp_path, attempts=2, next_attempt_at=FIXED_NOW)

    drain_outbox(store, lambda message: False, FIXED_NOW, 3, (15, 60, 240))
    item = only_message(store)
    assert item["status"] == "FAILED"
    assert item["attempts"] == 3

    store.requeue(item["id"])
    assert only_message(store)["status"] == "PENDING"
    assert only_message(store)["attempts"] == 0


def test_ambiguous_sending_is_not_automatically_retried(tmp_path: Path) -> None:
    store = seeded_outbox(tmp_path, status="SENDING", attempts=1)
    calls = []

    result = drain_outbox(store, lambda message: calls.append(message) or True, FIXED_NOW, 3, (15, 60, 240))

    assert calls == []
    assert result.ambiguous == [only_message(store)["id"]]


def test_confirmed_failure_is_scheduled_for_later(tmp_path: Path) -> None:
    store = seeded_outbox(tmp_path)

    result = drain_outbox(store, lambda message: False, FIXED_NOW, 3, (15, 60, 240))

    item = only_message(store)
    assert result.pending == [item["id"]]
    assert item["status"] == "PENDING"
    assert item["nextAttemptAt"] == "2026-09-21T00:15:00+00:00"


def test_email_payload_excludes_source_records_and_secrets() -> None:
    message = build_digest(
        {
            **candidate_payload(["a"], source_record="PRIVATE", password="SECRET"),
            "messageId": "abc123",
        }
    )

    serialized = message.as_string()
    assert "PRIVATE" not in serialized
    assert "SECRET" not in serialized
