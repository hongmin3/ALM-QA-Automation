from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from automation_core.email import build_digest, drain_outbox, load_existing_srs_mail_settings
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
                "linkEvidence": [
                    {
                        "direction": "SRS_TO_ISSUE",
                        "id": "P/ISSUE-7",
                        "activityFields": ["updated"],
                    }
                ],
                "issueWindow": {
                    "startExclusive": "2026-09-14T09:00:00Z",
                    "endInclusive": "2026-09-21T09:00:00Z",
                },
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
    raw = next(message.iter_attachments()).get_content()
    raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    assert "startExclusive" in raw
    assert "activityFields" in raw


def test_candidate_message_dedup_ignores_run_specific_metadata(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / ".automation")
    first = candidate_payload(["candidate-a"])
    first.update({"runId": "run-a", "summaryPath": ".automation/runs/run-a/summary.md"})
    second = candidate_payload(["candidate-a"])
    second.update({"runId": "run-b", "summaryPath": ".automation/runs/run-b/summary.md"})

    first_id, first_created = store.enqueue(first)
    second_id, second_created = store.enqueue(second)

    assert first_created is True
    assert second_created is False
    assert first_id == second_id


def test_existing_srs_env_is_loaded_before_mail_settings(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    srs_root = root / "apps" / "srs-spec"
    source = srs_root / "src"
    source.mkdir(parents=True)
    (srs_root / ".env").write_text(
        "SMTP_HOST=smtp.env.test\nSMTP_USER=env-user\nSMTP_PASSWORD=env-secret\n"
        "MAIL_FROM=from@env.test\nMAIL_TO=to@env.test\n",
        encoding="utf-8",
    )
    config_path = srs_root / "config.yaml"
    config_path.write_text("mail:\n  enabled: true\n", encoding="utf-8")
    (source / "config.py").write_text(
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "def load_config(path):\n"
        "    return SimpleNamespace(raw={'mail': {'enabled': True}}, snapshots_dir=Path(path).parent/'snapshots')\n",
        encoding="utf-8",
    )
    (source / "notify.py").write_text(
        "import os\n"
        "from types import SimpleNamespace\n"
        "def load_mail_settings(raw, root):\n"
        "    values=[os.getenv('SMTP_HOST'),os.getenv('SMTP_USER'),os.getenv('SMTP_PASSWORD'),os.getenv('MAIL_FROM'),os.getenv('MAIL_TO')]\n"
        "    return SimpleNamespace(enabled=True, from_addr=values[3], to_addrs=[values[4]] if values[4] else [], missing_fields=lambda: [] if all(values) else ['env'])\n"
        "def send_message(settings, message): return True\n",
        encoding="utf-8",
    )
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "MAIL_TO"):
        monkeypatch.delenv(name, raising=False)

    _, settings, runtime = load_existing_srs_mail_settings(root, config_path)

    assert settings.missing_fields() == []
    assert runtime.snapshots_dir == srs_root / "snapshots"


def _korean_payload(medium_count: int = 0) -> dict:
    payload = candidate_payload(["VP-631"])
    payload["candidates"][0].update({"title": "로그인 화면", "changedFields": ["content_html", "updated"]})
    for index in range(medium_count):
        payload["candidates"].append(
            {
                "priority": "MEDIUM",
                "project": "P",
                "itemId": f"VP-{1000 + index}",
                "title": f"사양 {index}",
                "reasons": ["CHANGED_SRS"],
                "linkedIds": [],
                "changedFields": ["description_raw", "updated"],
            }
        )
    payload["messageId"] = "abc123"
    return payload


def test_candidate_mail_is_a_korean_summary_with_json_attachment() -> None:
    # Validates: REQ-AUTO-003
    message = build_digest(_korean_payload(medium_count=2), polarion_host="https://polarion.example")

    assert message["Subject"] == "[ALM QA] 검토 후보 3건 · 우선 확인 1건 (사양 변경 3 · 이슈 변경 0)"
    body = message.get_body(("plain",)).get_content()
    assert "우선 확인 (HIGH) 1건" in body
    assert "VP-631 로그인 화면" in body
    assert "바뀐 곳: 본문" in body
    assert "연결 이슈: P/ISSUE-7" in body
    assert "https://polarion.example/polarion/#/project/P/workitem?id=VP-631" in body
    assert "{" not in body  # 원자료 JSON 은 본문에 넣지 않는다
    [attachment] = list(message.iter_attachments())
    assert attachment.get_filename() == "alm_qa_candidates.json"
    raw = attachment.get_content()
    raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    assert "startExclusive" in raw and "activityFields" in raw


def test_long_medium_list_is_cut_in_body_but_complete_in_attachment() -> None:
    # Validates: REQ-AUTO-003
    message = build_digest(_korean_payload(medium_count=60))

    body = message.get_body(("plain",)).get_content()
    assert body.count("VP-10") == 50
    assert "나머지 10건은 첨부 파일" in body
    raw = next(message.iter_attachments()).get_content()
    raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    assert raw.count('"itemId"') == 61


def test_run_failure_mail_is_korean() -> None:
    # Validates: REQ-AUTO-003
    message = build_digest(
        {"kind": "RUN_FAILED", "status": "FAILED", "stage": "srs", "summaryPath": "s.md", "messageId": "x"}
    )

    assert message["Subject"] == "[ALM QA] 실행 실패 · SRS 수집 단계"
    assert "s.md" in message.get_body(("plain",)).get_content()
