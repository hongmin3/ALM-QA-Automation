from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from types import ModuleType

from dotenv import load_dotenv

from automation_core.state import AutomationStore


@dataclass(frozen=True)
class DeliveryResult:
    sent: list[str]
    pending: list[str]
    failed: list[str]
    ambiguous: list[str]


def build_digest(payload: dict) -> EmailMessage:
    candidates = payload.get("candidates", [])
    safe_candidates = [
        {
            key: item.get(key)
            for key in (
                "priority",
                "project",
                "itemId",
                "reasons",
                "linkedIds",
                "linkEvidence",
                "issueWindow",
                "statusChange",
            )
        }
        for item in candidates
        if isinstance(item, dict)
    ]
    safe_body = {
        "kind": str(payload.get("kind", "UNKNOWN")),
        "status": str(payload.get("status", "UNKNOWN")),
        "stage": str(payload.get("stage", "")),
        "summaryPath": str(payload.get("summaryPath", "")),
        "candidates": safe_candidates,
    }
    message = EmailMessage()
    message["Subject"] = (
        f"[ALM QA] {safe_body['status']} · 검토 후보 {len(safe_candidates)}건"
    )
    message_id = str(payload.get("messageId", "unknown"))
    message["Message-ID"] = f"<{message_id}@alm-qa-automation.local>"
    message.set_content(json.dumps(safe_body, ensure_ascii=False, indent=2))
    return message


def _load_srs_module(srs_root: Path, filename: str, module_name: str) -> ModuleType:
    module_path = srs_root / "src" / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError("unable to load SRS notification module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_existing_srs_mail_settings(root: Path, config_path: Path):
    srs_root = root / "apps" / "srs-spec"
    load_dotenv(srs_root / ".env", override=False)
    config_module = _load_srs_module(srs_root, "config.py", "_alm_qa_srs_config")
    runtime = config_module.load_config(config_path)
    notify = _load_srs_module(srs_root, "notify.py", "_alm_qa_srs_notify")
    return notify, notify.load_mail_settings(runtime.raw, srs_root), runtime


def existing_srs_sender(
    root: Path,
    config_path: Path,
) -> Callable[[EmailMessage], bool]:
    notify, settings, _ = load_existing_srs_mail_settings(root, config_path)

    def sender(message: EmailMessage) -> bool:
        message["From"] = settings.from_addr
        message["To"] = ", ".join(settings.to_addrs)
        return bool(notify.send_message(settings, message))

    return sender


def _eligible(item: dict, now: datetime) -> bool:
    if item.get("status") != "PENDING":
        return False
    due = item.get("nextAttemptAt")
    return due is None or datetime.fromisoformat(str(due)) <= now


def drain_outbox(
    store: AutomationStore,
    sender: Callable[[EmailMessage], bool],
    now: datetime,
    max_attempts: int,
    retry_minutes: tuple[int, int, int],
) -> DeliveryResult:
    if max_attempts < 1 or len(retry_minutes) < max_attempts:
        raise ValueError("retry policy does not cover max attempts")
    result = DeliveryResult([], [], [], [])
    outbox = store.load_outbox()
    messages = outbox.get("messages", {})
    if not isinstance(messages, dict):
        raise ValueError("outbox messages must be an object")

    for item in sorted(messages.values(), key=lambda value: str(value.get("id", ""))):
        message_id = str(item.get("id", ""))
        if item.get("status") == "SENDING":
            result.ambiguous.append(message_id)
            continue
        if not _eligible(item, now):
            continue

        item["status"] = "SENDING"
        item["attempts"] = int(item.get("attempts", 0)) + 1
        store.save_outbox(outbox)

        sent = sender(build_digest({**item["payload"], "messageId": message_id}))
        if sent:
            item["status"] = "SENT"
            item["nextAttemptAt"] = None
            result.sent.append(message_id)
        elif item["attempts"] >= max_attempts:
            item["status"] = "FAILED"
            item["nextAttemptAt"] = None
            result.failed.append(message_id)
        else:
            item["status"] = "PENDING"
            delay = retry_minutes[item["attempts"] - 1]
            item["nextAttemptAt"] = (now + timedelta(minutes=delay)).isoformat()
            result.pending.append(message_id)
        store.save_outbox(outbox)

    return result
