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


PRIORITY_LABELS = {
    "CRITICAL": "긴급 (CRITICAL)",
    "HIGH": "우선 확인 (HIGH)",
    "MEDIUM": "참고 (MEDIUM)",
}
FIELD_LABELS = {
    "content_html": "본문",
    "description_raw": "본문",
    "description_kr_raw": "본문(한국어)",
    "title": "제목",
    "status": "상태",
    "severity": "심각도",
    "linked_work_items": "연결 항목",
    "attachments_meta": "첨부",
    "comments": "댓글",
    "priority": "우선순위",
    "resolution": "해결 방법",
}
IGNORED_FIELDS = {"updated"}
REASON_LABELS = {
    "CHANGED_SRS": "사양 변경",
    "CHANGED_ISSUE": "이슈 변경",
    "LINKED_OPEN_ISSUE": "열린 이슈 연결",
    "LINKED_REOPENED_ISSUE": "다시 열린 이슈 연결",
    "LINKED_CRITICAL_ISSUE": "최상위 심각도 이슈 연결",
}
STAGE_LABELS = {"srs": "SRS 수집", "issues": "이슈 수집", "analysis": "분석", "outbox": "메일 대기함"}
MEDIUM_BODY_LIMIT = 50
CANDIDATE_KEYS = (
    "priority",
    "project",
    "itemId",
    "title",
    "source",
    "change",
    "changedFields",
    "reasons",
    "linkedIds",
    "linkEvidence",
    "issueWindow",
    "statusChange",
)


def _workitem_url(host: str, project: str, item_id: str) -> str:
    return f"{host.rstrip('/')}/polarion/#/project/{project}/workitem?id={item_id}"


def _changed_label(fields: object) -> str:
    labels: list[str] = []
    for field in fields if isinstance(fields, list) else []:
        if field in IGNORED_FIELDS:
            continue
        label = FIELD_LABELS.get(str(field), str(field))
        if label not in labels:
            labels.append(label)
    return ", ".join(labels) or "수정 시각만"


def _candidate_lines(item: dict, polarion_host: str | None) -> list[str]:
    title = item.get("title") or "(제목 없음)"
    lines = [f"- {item.get('itemId')} {title}  [{item.get('project')}]"]
    reasons = [REASON_LABELS.get(str(r), str(r)) for r in item.get("reasons") or []]
    detail = f"바뀐 곳: {_changed_label(item.get('changedFields'))}"
    if reasons:
        detail += f" · 이유: {', '.join(reasons)}"
    status = item.get("statusChange") or {}
    if isinstance(status, dict) and status.get("before") != status.get("after"):
        detail += f" · 상태: {status.get('before')} → {status.get('after')}"
    lines.append(f"    {detail}")
    if item.get("linkedIds"):
        lines.append(f"    연결 이슈: {', '.join(str(i) for i in item['linkedIds'])}")
    if polarion_host and item.get("project") and item.get("itemId"):
        lines.append(f"    {_workitem_url(polarion_host, str(item['project']), str(item['itemId']))}")
    return lines


def _candidate_body(candidates: list[dict], summary_path: str, polarion_host: str | None) -> str:
    srs = sum(1 for c in candidates if c.get("source", "srs") == "srs")
    lines = [
        "ALM QA 자동화가 어제와 오늘의 Polarion 자료를 비교해 검토할 항목을 골랐습니다.",
        f"검토 후보 {len(candidates)}건: 사양 변경 {srs}건, 이슈 변경 {len(candidates) - srs}건.",
        "우선 확인 항목부터 보고, 참고 항목은 필요할 때 확인하세요.",
        "",
    ]
    for priority in ("CRITICAL", "HIGH", "MEDIUM"):
        group = [c for c in candidates if c.get("priority") == priority]
        if not group:
            continue
        lines.append(f"■ {PRIORITY_LABELS[priority]} {len(group)}건")
        shown = group if priority != "MEDIUM" else group[:MEDIUM_BODY_LIMIT]
        for item in shown:
            lines.extend(_candidate_lines(item, polarion_host))
        if len(shown) < len(group):
            lines.append(f"  … 나머지 {len(group) - len(shown)}건은 첨부 파일(alm_qa_candidates.json)에 있습니다.")
        lines.append("")
    lines.append(f"실행 기록: {summary_path}")
    lines.append("전체 원자료는 첨부 파일 alm_qa_candidates.json 에 있습니다.")
    return "\n".join(lines) + "\n"


def build_digest(payload: dict, polarion_host: str | None = None) -> EmailMessage:
    candidates = payload.get("candidates", [])
    safe_candidates = [
        {key: item.get(key) for key in CANDIDATE_KEYS if key in item}
        for item in candidates
        if isinstance(item, dict)
    ]
    kind = str(payload.get("kind", "UNKNOWN"))
    safe_body = {
        "kind": kind,
        "status": str(payload.get("status", "UNKNOWN")),
        "stage": str(payload.get("stage", "")),
        "summaryPath": str(payload.get("summaryPath", "")),
        "candidates": safe_candidates,
    }
    message = EmailMessage()
    if kind == "RUN_FAILED":
        stage = STAGE_LABELS.get(safe_body["stage"], safe_body["stage"] or "알 수 없는")
        message["Subject"] = f"[ALM QA] 실행 실패 · {stage} 단계"
        message.set_content(
            f"ALM QA 자동화가 {stage} 단계에서 멈췄습니다. 오늘은 검토 후보를 만들지 못했습니다.\n"
            f"실행 기록: {safe_body['summaryPath']}\n"
            "다음 평일 09:00 에 다시 실행합니다. 원인은 실행 기록과 로그에서 확인하세요.\n"
        )
    else:
        urgent = sum(1 for c in safe_candidates if c.get("priority") in ("CRITICAL", "HIGH"))
        srs = sum(1 for c in safe_candidates if c.get("source", "srs") == "srs")
        message["Subject"] = (
            f"[ALM QA] 검토 후보 {len(safe_candidates)}건 · 우선 확인 {urgent}건 "
            f"(사양 변경 {srs} · 이슈 변경 {len(safe_candidates) - srs})"
        )
        message.set_content(_candidate_body(safe_candidates, safe_body["summaryPath"], polarion_host))
        message.add_attachment(
            json.dumps(safe_body, ensure_ascii=False, indent=2).encode("utf-8"),
            maintype="application",
            subtype="json",
            filename="alm_qa_candidates.json",
        )
    message_id = str(payload.get("messageId", "unknown"))
    message["Message-ID"] = f"<{message_id}@alm-qa-automation.local>"
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
    notify, settings, runtime = load_existing_srs_mail_settings(root, config_path)

    def sender(message: EmailMessage) -> bool:
        message["From"] = settings.from_addr
        message["To"] = ", ".join(settings.to_addrs)
        return bool(notify.send_message(settings, message))

    # 메일 본문의 Polarion 링크에 쓴다. drain_outbox 가 읽는다.
    sender.polarion_host = getattr(runtime, "host", None)  # type: ignore[attr-defined]
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

        sent = sender(
            build_digest(
                {**item["payload"], "messageId": message_id},
                polarion_host=getattr(sender, "polarion_host", None),
            )
        )
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
