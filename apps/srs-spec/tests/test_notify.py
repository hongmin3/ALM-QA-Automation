from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import notify


def settings() -> notify.MailSettings:
    return notify.MailSettings(
        enabled=True,
        host="smtp.example.test",
        port=587,
        user="user",
        password="password",
        from_addr="sender@example.test",
        to_addrs=["recipient@example.test"],
        use_starttls=True,
        attach_report=False,
        timeout_seconds=10,
    )


def sample_summary() -> dict:
    return {
        "ok": True,
        "run_date": "2026-09-21",
        "diff": {"changed": 1, "new": 0, "deleted": 0, "unchanged": 1},
        "pdfs": [],
        "warnings": [],
        "failed_checks": [],
    }


def test_send_run_report_uses_generic_sender(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(notify, "send_message", lambda config, message: sent.append(message) or True)

    assert notify.send_run_report(settings(), sample_summary()) is True
    assert len(sent) == 1
    assert "SRS" in sent[0]["Subject"]
