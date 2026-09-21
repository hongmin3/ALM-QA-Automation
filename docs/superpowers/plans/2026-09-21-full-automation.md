# ALM QA Full Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 평일 09:00에 검증된 SRS·이슈 수집을 실행하고 명시적 링크 기반 검토 후보를 만든 뒤, 기존 SMTP 설정을 사용하는 영속 대기함으로 중복 없는 이메일을 발송한다.

**Architecture:** 기존 두 앱은 별도 프로세스로 유지하고 루트 `automation.py`가 단계 상태와 종료 코드를 관리한다. `automation_core` 패키지는 설정, 원자적 상태, 연계 분석, 이메일 대기함을 분리하며 `.automation/`만 운영 상태로 사용한다.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, subprocess, JSON/YAML, smtplib/EmailMessage, pytest, Windows PowerShell 5.1 Task Scheduler.

**Spec:** `docs/superpowers/specs/2026-09-21-full-automation-design.md`, `SPEC.md` REQ-AUTO-001~004.

## Global Constraints

- 실제 `config.yaml`, `config/config.yaml`, `.env`, `IMPLEMENTATION_LOG.md`의 내용은 개발·테스트 중 열람하거나 출력하지 않는다.
- 새 Python 패키지를 설치하지 않고 현재 의존성만 사용한다.
- Polarion은 조회만 하며 이슈 수정, 자동 승인, 외부 AI API 호출을 추가하지 않는다.
- 운영 상태·원문·메일 대기함은 `.automation/` 아래에 두고 Git에서 제외한다.
- SRS 또는 이슈 결과가 완전 성공이 아니면 분석 기준선을 갱신하지 않는다.
- 실제 SMTP 발송과 실제 예약 작업 전환은 합성 검증 후 별도 운영 검증으로 수행하고 결과를 구분한다.

## Review Focus

- 이슈가 `PARTIAL`이거나 manifest 건수가 모순될 때 기존 분석 기준선과 후보가 그대로 유지되는지 Task 5에서 검증한다.
- 제목이 같아도 명시적 ID 링크가 없으면 SRS·이슈를 연결하지 않는지 Task 2에서 검증한다.
- outbox 저장 후 발송 전 프로세스가 중단되면 다음 실행에서 발송하고, SENDING 확정 후 중단되면 자동 재발송하지 않는지 Task 3에서 검증한다.
- 기존 `.observation` 후보의 `DONE` 상태가 통합 상태로 이전되고 원본 파일은 변하지 않는지 Task 2에서 검증한다.
- 새 예약 작업 등록 또는 첫 통합 성공 검증이 실패하면 기존 SRS 작업이 활성 상태로 남는지 Task 6에서 검증한다.

---

### Task 1: Automation Configuration and Atomic State

**Files:**
- Create: `automation_core/__init__.py`
- Create: `automation_core/config.py`
- Create: `automation_core/state.py`
- Create: `automation.example.yaml`
- Modify: `.gitignore`
- Test: `tests/test_automation_state.py`

**Interfaces:**
- Consumes: repository root `Path`, public YAML settings, injectable UTC `datetime`.
- Produces: `AutomationConfig`, `PriorityRules`, `AutomationStore.locked()`, `AutomationStore.load_state()`, `AutomationStore.save_state()`, `AutomationStore.load_outbox()`, `AutomationStore.save_outbox()`, `AutomationStore.enqueue()`, `AutomationStore.save_run()`, `AutomationStore.successful_run_for()`, `AutomationStore.has_unresolved_messages()`.

- [ ] **Step 1: Write failing configuration and state tests**

```python
def test_config_rejects_state_outside_root(tmp_path):
    path = write_yaml(tmp_path / "automation.yaml", {
        "state_dir": "../outside", "srs_snapshot_dir": "apps/srs-spec/snapshots",
        "srs_config": "apps/srs-spec/config/config.yaml",
        "issue_config": "apps/issue-export/config.yaml",
    })
    with pytest.raises(ValueError, match="inside project"):
        load_automation_config(path, tmp_path)

def test_enqueue_is_content_deduplicated_and_atomic(tmp_path):
    store = AutomationStore(tmp_path / ".automation")
    with store.locked():
        first, created1 = store.enqueue({"kind": "CANDIDATES", "candidateIds": ["a"]})
        second, created2 = store.enqueue({"candidateIds": ["a"], "kind": "CANDIDATES"})
    assert created1 is True and created2 is False and first == second
    assert not list((tmp_path / ".automation").glob(".outbox-*.tmp"))

def test_lock_is_released_when_owner_process_dies(tmp_path):
    proc = start_lock_holder(tmp_path / ".automation")
    wait_until_locked(proc)
    proc.terminate(); proc.wait(timeout=10)
    with AutomationStore(tmp_path / ".automation").locked():
        pass
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_automation_state.py -q`

Expected: collection error for missing `automation_core.config` and `automation_core.state`.

- [ ] **Step 3: Implement typed configuration and atomic JSON state**

```python
@dataclass(frozen=True)
class PriorityRules:
    reopened_statuses: frozenset[str]
    open_statuses: frozenset[str]
    critical_severities: frozenset[str]
    notify_priorities: frozenset[str]

@dataclass(frozen=True)
class AutomationConfig:
    root: Path
    state_dir: Path
    srs_snapshot_dir: Path
    srs_config: Path
    issue_config: Path
    max_email_attempts: int
    retry_minutes: tuple[int, int, int]
    rules: PriorityRules

def load_automation_config(path: Path, root: Path) -> AutomationConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    root = root.resolve()
    def project_path(key: str, default: str) -> Path:
        candidate = (root / str(raw.get(key, default))).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"{key} must remain inside project")
        return candidate
    priority = raw.get("priority") or {}
    email = raw.get("email") or {}
    retries = tuple(int(value) for value in email.get("retry_minutes", [15, 60, 240]))
    if len(retries) != 3 or any(value < 1 for value in retries):
        raise ValueError("email.retry_minutes must contain three positive integers")
    return AutomationConfig(
        root=root, state_dir=project_path("state_dir", ".automation"),
        srs_snapshot_dir=project_path("srs_snapshot_dir", "apps/srs-spec/snapshots"),
        srs_config=project_path("srs_config", "apps/srs-spec/config/config.yaml"),
        issue_config=project_path("issue_config", "apps/issue-export/config.yaml"),
        max_email_attempts=int(email.get("max_attempts", 3)),
        retry_minutes=(retries[0], retries[1], retries[2]),
        rules=PriorityRules(
            reopened_statuses=frozenset(map(str.casefold, priority.get("reopened_statuses", ["reopened"]))),
            open_statuses=frozenset(map(str.casefold, priority.get("open_statuses", ["open", "in_progress", "in_review", "reopened"]))),
            critical_severities=frozenset(map(str.casefold, priority.get("critical_severities", ["blocker", "critical"]))),
            notify_priorities=frozenset(priority.get("notify_priorities", ["CRITICAL", "HIGH", "MEDIUM"])),
        ),
    )

class AutomationStore:
    def load_state(self) -> dict:
        return self._read("state.json", {"schemaVersion": 2, "sources": {}, "candidates": {}, "runs": {}})
    def save_state(self, value: dict) -> None:
        self._atomic_write("state.json", value)
    def load_outbox(self) -> dict:
        return self._read("outbox.json", {"schemaVersion": 1, "messages": {}})
    def save_outbox(self, value: dict) -> None:
        self._atomic_write("outbox.json", value)
    def enqueue(self, payload: dict) -> tuple[str, bool]:
        message_id = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        outbox = self.load_outbox()
        if message_id in outbox["messages"]:
            return message_id, False
        outbox["messages"][message_id] = {
            "id": message_id, "status": "PENDING", "attempts": 0,
            "nextAttemptAt": None, "payload": payload,
        }
        self.save_outbox(outbox)
        return message_id, True
    def save_run(self, run_id: str, manifest: dict, summary: str) -> Path:
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        self._atomic_write_at(run_dir / "manifest.json", manifest)
        self._atomic_text_at(run_dir / "summary.md", summary)
        return run_dir
    def successful_run_for(self, local_date: str) -> dict | None:
        state = self.load_state()
        matches = [run for run in state["runs"].values()
                   if run.get("localDate") == local_date and run.get("dataComplete") is True]
        return max(matches, key=lambda run: run["runId"], default=None)
    def has_unresolved_messages(self) -> bool:
        return any(item["status"] in {"PENDING", "SENDING", "FAILED"}
                   for item in self.load_outbox()["messages"].values())
```

`_atomic_write`는 같은 폴더의 고유 임시 파일을 `open("x")`, `flush`, `os.fsync`, `os.replace` 순서로 확정한다. `locked`는 Windows에서 `msvcrt.locking`, POSIX에서 `fcntl.flock`을 사용하고 lock 파일 inode를 삭제하지 않는다. `_read`는 JSON 객체와 schemaVersion을 검사한다. outbox ID는 정렬 JSON의 SHA-256이며 초기 상태는 `PENDING`, `attempts=0`, `nextAttemptAt=null`이다.

- [ ] **Step 4: Add the public example and ignore private state**

```yaml
state_dir: ".automation"
srs_snapshot_dir: "apps/srs-spec/snapshots"
srs_config: "apps/srs-spec/config/config.yaml"
issue_config: "apps/issue-export/config.yaml"
email:
  max_attempts: 3
  retry_minutes: [15, 60, 240]
priority:
  reopened_statuses: ["reopened"]
  open_statuses: ["open", "in_progress", "in_review", "reopened"]
  critical_severities: ["blocker", "critical"]
  notify_priorities: ["CRITICAL", "HIGH", "MEDIUM"]
```

Add `automation.yaml` and `.automation/` to `.gitignore`; keep `automation.example.yaml` tracked.

- [ ] **Step 5: Run Task 1 tests and commit**

Run: `python -m pytest tests/test_automation_state.py -q`

Expected: all Task 1 tests PASS.

```powershell
git add .gitignore automation.example.yaml automation_core/__init__.py automation_core/config.py automation_core/state.py tests/test_automation_state.py
git commit -m "feat: add durable automation state"
```

---

### Task 2: Explicit Correlation, Priority, and Observation Migration

**Files:**
- Create: `automation_core/correlation.py`
- Modify: `observation.py`
- Modify: `automation_core/state.py`
- Test: `tests/test_automation_correlation.py`
- Test: `tests/test_observation.py`

**Interfaces:**
- Consumes: `PriorityRules`, normalized SRS/issue records, existing observation candidates.
- Produces: `compute_changes(source, current, previous) -> list[dict]`, `extract_polarion_ids(value) -> frozenset[str]`, `correlate(srs_changes, issue_changes, srs_records, issue_records, rules) -> list[dict]`, `AutomationStore.migrate_observation(path) -> bool`.

- [ ] **Step 1: Write failing exact-link and priority tests**

```python
def test_links_only_exact_ids_and_does_not_match_similar_titles(rules):
    srs = changed_srs("P/SRS-1", title="Login failure", linked=["P/ISSUE-7"])
    linked = issue("P/ISSUE-7", title="Different", status="open")
    same_title = issue("P/ISSUE-8", title="Login failure", status="open")
    result = correlate([srs], [], {"P/SRS-1": srs["after"]},
                       {"P/ISSUE-7": linked, "P/ISSUE-8": same_title}, rules)
    assert result[0]["linkedIds"] == ["P/ISSUE-7"]
    assert result[0]["priority"] == "HIGH"

def test_reopened_critical_link_is_critical(rules):
    result = correlate_fixture(issue_status="reopened", severity="critical", rules=rules)
    assert result[0]["priority"] == "CRITICAL"
    assert set(result[0]["reasons"]) == {"CHANGED_SRS", "LINKED_REOPENED_ISSUE", "CRITICAL_SEVERITY"}

def test_unknown_values_remain_medium(rules):
    result = correlate_fixture(linked=False, issue_status="custom", severity="custom", rules=rules)
    assert {candidate["priority"] for candidate in result} == {"MEDIUM"}

def test_first_complete_input_is_baseline_without_candidates():
    assert compute_changes("srs", {"P/SRS-1": normalized_srs()}, None) == []
```

- [ ] **Step 2: Write failing migration test**

```python
def test_migration_preserves_done_review_and_original_file(tmp_path):
    old = tmp_path / ".observation" / "state.json"
    original = observation_state(candidate="abc", review_state="DONE")
    write_json(old, original)
    store = AutomationStore(tmp_path / ".automation")
    assert store.migrate_observation(old) is True
    assert store.load_state()["candidates"]["abc"]["reviewState"] == "DONE"
    assert read_json(old) == original
    assert store.migrate_observation(old) is False
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest tests/test_automation_correlation.py tests/test_observation.py -q`

Expected: FAIL because correlation and migration interfaces do not exist.

- [ ] **Step 4: Implement exact ID extraction and deterministic candidates**

```python
ID_RE = re.compile(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]+-[0-9]+)(?![A-Z0-9_])")

def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()

def workitem_attributes(record: dict | None) -> dict:
    if not record:
        return {}
    workitem = record.get("workitem", record)
    return workitem.get("attributes", workitem) if isinstance(workitem, dict) else {}

def status_of(source: str, record: dict | None) -> str | None:
    value = workitem_attributes(record).get("status")
    return str(value) if value is not None else None

def severity(record: dict) -> str:
    attrs = workitem_attributes(record)
    return str(attrs.get("severity") or attrs.get("defectSeverity") or "")

def compute_changes(source: str, current: dict[str, dict], previous: dict[str, dict] | None) -> list[dict]:
    if previous is None:
        return []
    changes = []
    for key, item in sorted(current.items()):
        before = previous.get(key, {}).get("record")
        after = item["record"]
        if before == after:
            continue
        changes.append({
            "source": source, "project": item["project"], "itemId": item["itemId"],
            "change": "NEW" if before is None else "CHANGED",
            "beforeVersion": digest(before), "afterVersion": digest(after),
            "before": before, "after": after,
            "statusChange": {"before": status_of(source, before), "after": status_of(source, after)},
        })
    return changes

def extract_polarion_ids(value: object) -> frozenset[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(ID_RE.findall(value.upper()))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(extract_polarion_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(extract_polarion_ids(item))
    return frozenset(found)

def candidate_priority(change: dict, relevant_issues: list[dict], rules: PriorityRules) -> tuple[str, list[str]]:
    reasons = ["CHANGED_SRS" if change["source"] == "srs" else "CHANGED_ISSUE"]
    reopened = any((status_of("issues", issue) or "").casefold() in rules.reopened_statuses for issue in relevant_issues)
    severe = any(severity(issue).casefold() in rules.critical_severities for issue in relevant_issues)
    if reopened or severe:
        if reopened: reasons.append("LINKED_REOPENED_ISSUE")
        if severe: reasons.append("CRITICAL_SEVERITY")
        return "CRITICAL", reasons
    if any((status_of("issues", issue) or "").casefold() in rules.open_statuses for issue in relevant_issues):
        return "HIGH", reasons + ["LINKED_OPEN_ISSUE"]
    return "MEDIUM", reasons

def correlate(srs_changes: list[dict], issue_changes: list[dict],
              srs_records: dict[str, dict], issue_records: dict[str, dict],
              rules: PriorityRules) -> list[dict]:
    issues_by_id = {record["itemId"].upper(): (key, record) for key, record in issue_records.items()}
    srs_by_id = {record["itemId"].upper(): (key, record) for key, record in srs_records.items()}
    result = []
    for change in [*srs_changes, *issue_changes]:
        record = change["after"]
        references = extract_polarion_ids(record)
        if change["source"] == "srs":
            linked = [issues_by_id[identifier] for identifier in references if identifier in issues_by_id]
            relevant_issues = [item["record"] for _, item in linked]
        else:
            linked = [srs_by_id[identifier] for identifier in references if identifier in srs_by_id]
            relevant_issues = [record]
        linked_ids = sorted(key for key, _ in linked)
        priority, reasons = candidate_priority(change, relevant_issues, rules)
        identity = [change["source"], change["project"], change["itemId"],
                    change["beforeVersion"], change["afterVersion"], linked_ids]
        result.append({
            **change, "id": digest(identity), "priority": priority,
            "reasons": sorted(set(reasons)), "linkedIds": linked_ids,
        })
    return sorted(result, key=lambda item: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}[item["priority"]], item["project"], item["itemId"]))
```

Use explicit linked resource IDs first and exact ID references in structured fields second. Never compare titles. Candidate hashes include the sorted linked ID set.

- [ ] **Step 5: Implement schema-2 migration and shared manual state path**

`AutomationStore.migrate_observation` copies schema-1 sources, candidates, runs into schema 2 only when the new store has no migration marker. Modify `observation.py` default state directory to `.automation`, import the store lock/atomic writer, and on first use migrate the old `.observation/state.json` without deleting it. Keep all existing CLI flags and review states compatible.

- [ ] **Step 6: Run Task 2 tests and commit**

Run: `python -m pytest tests/test_automation_correlation.py tests/test_observation.py -q`

Expected: all Task 2 and observation regression tests PASS.

```powershell
git add automation_core/correlation.py automation_core/state.py observation.py tests/test_automation_correlation.py tests/test_observation.py
git commit -m "feat: correlate SRS changes with linked issues"
```

---

### Task 3: Persistent SMTP Outbox

**Files:**
- Create: `automation_core/email.py`
- Modify: `apps/srs-spec/src/notify.py`
- Modify: `automation_core/state.py`
- Create: `apps/srs-spec/tests/test_notify.py`
- Create: `tests/test_automation_email.py`

**Interfaces:**
- Consumes: `AutomationStore`, existing SRS `MailSettings`, structured payload without source text.
- Produces: `send_message(settings, message) -> bool`, `existing_srs_sender(root, config_path) -> Callable[[EmailMessage], bool]`, `build_digest(payload) -> EmailMessage`, `drain_outbox(store, sender, now, max_attempts, retry_minutes) -> DeliveryResult`, `AutomationStore.requeue(message_id)`.

- [ ] **Step 1: Write failing SRS generic sender regression tests**

```python
def test_send_run_report_uses_generic_sender(monkeypatch, settings):
    sent = []
    monkeypatch.setattr(notify, "send_message", lambda cfg, msg: sent.append(msg) or True)
    assert notify.send_run_report(settings, sample_summary()) is True
    assert len(sent) == 1
    assert "SRS" in sent[0]["Subject"]
```

- [ ] **Step 2: Write failing outbox delivery tests**

```python
def test_saved_message_is_retried_once_after_interruption(tmp_path, fixed_now):
    store = AutomationStore(tmp_path / ".automation")
    message_id, _ = store.enqueue(candidate_payload(["candidate-a"]))
    attempts = []
    result = drain_outbox(store, lambda msg: attempts.append(msg) or True,
                          fixed_now, 3, (15, 60, 240))
    assert result.sent == [message_id]
    assert len(attempts) == 1
    result = drain_outbox(store, lambda msg: attempts.append(msg) or True,
                          fixed_now, 3, (15, 60, 240))
    assert result.sent == [] and len(attempts) == 1

def test_three_failures_become_failed_and_can_be_requeued(tmp_path, fixed_now):
    store = seeded_outbox(tmp_path, attempts=2, next_attempt_at=fixed_now)
    drain_outbox(store, lambda msg: False, fixed_now, 3, (15, 60, 240))
    item = only_message(store)
    assert item["status"] == "FAILED" and item["attempts"] == 3
    store.requeue(item["id"])
    assert only_message(store)["status"] == "PENDING"

def test_ambiguous_sending_is_not_automatically_retried(tmp_path, fixed_now):
    store = seeded_outbox(tmp_path, status="SENDING", attempts=1)
    calls = []
    result = drain_outbox(store, lambda msg: calls.append(msg) or True,
                          fixed_now, 3, (15, 60, 240))
    assert calls == [] and result.ambiguous == [only_message(store)["id"]]

def test_email_payload_excludes_source_records_and_secrets():
    message = build_digest(candidate_payload(["a"], source_record="PRIVATE", password="SECRET"))
    serialized = message.as_string()
    assert "PRIVATE" not in serialized and "SECRET" not in serialized
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest apps/srs-spec/tests/test_notify.py tests/test_automation_email.py -q`

Expected: FAIL for missing generic sender and outbox delivery module.

- [ ] **Step 4: Extract generic SMTP send without changing existing behavior**

```python
def send_message(settings: MailSettings, message: EmailMessage) -> bool:
    if not settings.enabled or settings.missing_fields():
        return False
    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout_seconds) as server:
            server.ehlo()
            if settings.use_starttls:
                server.starttls(); server.ehlo()
            server.login(settings.user, settings.password)
            server.send_message(message)
        return True
    except (smtplib.SMTPException, OSError):
        return False
```

Make `send_run_report` build its existing message and delegate to `send_message`; keep its subject, attachment limit, and return contract unchanged.

- [ ] **Step 5: Implement digest rendering and retry transitions**

```python
@dataclass(frozen=True)
class DeliveryResult:
    sent: list[str]
    pending: list[str]
    failed: list[str]
    ambiguous: list[str]

def build_digest(payload: dict) -> EmailMessage:
    safe_candidates = [{key: item.get(key) for key in
                        ("priority", "project", "itemId", "reasons", "linkedIds", "statusChange")}
                       for item in payload.get("candidates", [])]
    body = json.dumps({"status": payload["status"], "summaryPath": payload["summaryPath"],
                       "candidates": safe_candidates}, ensure_ascii=False, indent=2)
    message = EmailMessage()
    message["Subject"] = f"[ALM QA] {payload['status']} · 검토 후보 {len(safe_candidates)}건"
    message["Message-ID"] = f"<{payload['messageId']}@alm-qa-automation.local>"
    message.set_content(body)
    return message

def existing_srs_sender(root: Path, config_path: Path) -> Callable[[EmailMessage], bool]:
    srs_root = root / "apps" / "srs-spec"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    notify = load_srs_notify_module(srs_root)
    settings = notify.load_mail_settings(raw, srs_root)
    def sender(message: EmailMessage) -> bool:
        message["From"] = settings.from_addr
        message["To"] = ", ".join(settings.to_addrs)
        return notify.send_message(settings, message)
    return sender

def eligible(item: dict, now: datetime) -> bool:
    if item["status"] != "PENDING":
        return False
    due = item.get("nextAttemptAt")
    return due is None or datetime.fromisoformat(due) <= now

def drain_outbox(store: AutomationStore, sender: Callable[[EmailMessage], bool],
                 now: datetime, max_attempts: int,
                 retry_minutes: tuple[int, int, int]) -> DeliveryResult:
    result = DeliveryResult([], [], [], [])
    outbox = store.load_outbox()
    for item in sorted(outbox["messages"].values(), key=lambda value: value["id"]):
        if item["status"] == "SENDING":
            result.ambiguous.append(item["id"]); continue
        if not eligible(item, now):
            continue
        item["status"] = "SENDING"; item["attempts"] += 1
        store.save_outbox(outbox)
        sent = sender(build_digest({**item["payload"], "messageId": item["id"]}))
        if sent:
            item["status"] = "SENT"; item["nextAttemptAt"] = None; result.sent.append(item["id"])
        elif item["attempts"] >= max_attempts:
            item["status"] = "FAILED"; item["nextAttemptAt"] = None; result.failed.append(item["id"])
        else:
            item["status"] = "PENDING"
            item["nextAttemptAt"] = (now + timedelta(minutes=retry_minutes[item["attempts"] - 1])).isoformat()
            result.pending.append(item["id"])
        store.save_outbox(outbox)
    return result
```

`AutomationStore.requeue` requires an existing `FAILED` or `SENDING` item, resets it to `PENDING`, sets attempts to 0 and clears `nextAttemptAt`, then atomically saves the outbox. `load_srs_notify_module` uses `importlib.util.spec_from_file_location` with a private module name and never logs the loaded configuration. Only store and render run status, priority, project/item IDs, reasons, status changes, linked IDs, and local summary path. On failure increment attempts and calculate `nextAttemptAt` from the retry sequence; at the limit set `FAILED`.

- [ ] **Step 6: Run Task 3 tests and commit**

Run: `python -m pytest apps/srs-spec/tests/test_notify.py tests/test_automation_email.py -q`

Expected: all Task 3 tests PASS.

```powershell
git add apps/srs-spec/src/notify.py apps/srs-spec/tests/test_notify.py automation_core/email.py tests/test_automation_email.py
git commit -m "feat: add persistent email outbox"
```

---

### Task 4: SRS Mail Suppression for Integrated Runs

**Files:**
- Modify: `apps/srs-spec/main.py`
- Create: `apps/srs-spec/tests/test_main_options.py`

**Interfaces:**
- Consumes: CLI `--no-mail` boolean.
- Produces: existing pipeline result with only `send_run_report` skipped; collection, validation, publication, marker and exit codes unchanged.

- [ ] **Step 1: Write failing CLI behavior tests**

```python
def test_no_mail_parses_without_changing_other_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-mail"])
    args = main.parse_args()
    assert args.no_mail is True and args.dry_run is False

def test_no_mail_skips_sender_after_success(monkeypatch, synthetic_pipeline):
    sent = []
    monkeypatch.setattr(main, "send_run_report", lambda *a, **k: sent.append(a))
    assert synthetic_pipeline.run(extra_args=["--no-mail"]) == 0
    assert sent == []
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest apps/srs-spec/tests/test_main_options.py -q`

Expected: FAIL because `--no-mail` is not accepted.

- [ ] **Step 3: Add the flag at the notification boundary**

```python
p.add_argument("--no-mail", action="store_true",
               help="통합 자동화가 단일 요약 메일을 보낼 때 SRS 개별 메일을 생략")

if not args.no_mail:
    send_run_report(load_mail_settings(config.raw, PROJECT_ROOT), summary, report_path=html_path)
```

Do not make `--no-mail` imply dry-run, crawl-only, or marker suppression.

- [ ] **Step 4: Run Task 4 tests and commit**

Run: `python -m pytest apps/srs-spec/tests/test_main_options.py apps/srs-spec/tests -q`

Expected: all SRS tests PASS.

```powershell
git add apps/srs-spec/main.py apps/srs-spec/tests/test_main_options.py
git commit -m "feat: suppress duplicate SRS mail in integrated runs"
```

---

### Task 5: Integrated Orchestrator and CLI

**Files:**
- Create: `automation_core/orchestrator.py`
- Create: `automation.py`
- Modify: `run.py`
- Modify: `run.ps1`
- Modify: `실행하기.bat` only if its menu invocation needs a new mode; preserve current double-click behavior.
- Create: `tests/test_automation_orchestrator.py`
- Modify: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `AutomationConfig`, `AutomationStore`, injected `run_command(args, cwd) -> int`, injected `send_email(message) -> bool`, local date/UTC clock.
- Produces: `run_automation(config, store, runner, sender, now) -> dict` manifest; CLI `run`, `--check-local`, `--retry-email ID`, `--no-send`.

- [ ] **Step 1: Write failing stage-order and failure-gate tests**

```python
def test_success_runs_stages_in_order_and_records_manifest(fixture):
    calls = []
    runner = fixture.runner(calls, srs=0, issues=0)
    result = run_automation(fixture.config, fixture.store, runner, fixture.sender, fixture.now)
    assert [call.mode for call in calls] == ["srs", "issues"]
    assert result["status"] == "SUCCESS"
    assert result["stages"] == {
        "srs": "SUCCESS", "issues": "SUCCESS", "analysis": "SUCCESS",
        "outbox": "SUCCESS", "email": "SUCCESS",
    }

def test_partial_issue_result_does_not_update_baseline(fixture):
    before = fixture.store.load_state()
    runner = fixture.runner([], srs=0, issues=4)
    result = run_automation(fixture.config, fixture.store, runner, fixture.sender, fixture.now)
    assert result["status"] == "FAILED" and result["stages"]["analysis"] == "NOT_RUN"
    assert fixture.store.load_state() == before
    assert only_message(fixture.store)["payload"]["kind"] == "RUN_FAILED"

def test_successful_same_day_rerun_only_drains_outbox(fixture):
    fixture.seed_successful_run(email_pending=True)
    calls = []
    result = run_automation(fixture.config, fixture.store,
                            fixture.runner(calls), fixture.sender, fixture.now)
    assert calls == [] and result["resumedOutboxOnly"] is True
```

- [ ] **Step 2: Write failing local-check and launcher tests**

```python
def test_check_local_never_runs_network_or_smtp(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("process started"))
    assert automation.main(["--config", str(valid_example(tmp_path)), "--check-local"]) == 0

def test_launcher_routes_automatic_mode(monkeypatch):
    with patch("builtins.input", side_effect=["4"]):
        assert load_launcher().menu_arguments() == ["auto"]
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest tests/test_automation_orchestrator.py tests/test_launcher.py -q`

Expected: FAIL because automation CLI, orchestrator, and `auto` launcher mode do not exist.

- [ ] **Step 4: Implement the orchestrator with explicit command construction**

```python
def srs_command(root: Path) -> tuple[list[str], Path]:
    return [sys.executable, str(root / "run.py"), "srs", "--no-mail"], root

def issue_command(root: Path, output: Path, issue_config: Path) -> tuple[list[str], Path]:
    return [sys.executable, str(root / "run.py"), "issues", "--config", str(issue_config),
            "--out", str(output)], root

def sanitize_candidates(candidates: list[dict]) -> list[dict]:
    allowed = ("id", "priority", "project", "itemId", "reasons", "linkedIds", "statusChange")
    return [{key: candidate.get(key) for key in allowed} for candidate in candidates]

def render_summary(manifest: dict) -> str:
    stages = manifest.get("stages", {})
    lines = ["# ALM QA automation", "", f"Run: {manifest['runId']}",
             f"Status: {manifest['status']}", f"Data complete: {manifest['dataComplete']}", ""]
    lines.extend(f"- {name}: {status}" for name, status in sorted(stages.items()))
    return "\n".join(lines) + "\n"

def run_automation(config: AutomationConfig, store: AutomationStore,
                   runner: CommandRunner, sender: MessageSender,
                   now: datetime) -> dict:
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    local_date = now.astimezone().date().isoformat()
    stages = {name: "NOT_RUN" for name in ("srs", "issues", "analysis", "outbox", "email")}
    def finish(manifest: dict) -> dict:
        store.save_run(run_id, manifest, render_summary(manifest))
        state = store.load_state()
        state["runs"][run_id] = manifest
        store.save_state(state)
        return manifest
    def fail(stage: str, code: int) -> dict:
        stages[stage] = "FAILED"
        message_id, _ = store.enqueue({"kind": "RUN_FAILED", "status": "FAILED",
                                       "stage": stage, "runId": run_id,
                                       "summaryPath": f".automation/runs/{run_id}/summary.md"})
        delivery = drain_outbox(store, sender, now, config.max_email_attempts,
                                config.retry_minutes)
        stages["outbox"] = "SUCCESS"
        stages["email"] = "PARTIAL" if store.has_unresolved_messages() else "SUCCESS"
        return finish({"runId": run_id, "localDate": local_date, "status": "FAILED",
                       "dataComplete": False, "failedStage": stage, "childExitCode": code,
                       "failureMessageId": message_id, "stages": stages,
                       "delivery": asdict(delivery)})

    with store.locked():
        initial_delivery = drain_outbox(store, sender, now, config.max_email_attempts,
                                        config.retry_minutes)
        prior = store.successful_run_for(local_date)
        if prior:
            unresolved = store.has_unresolved_messages()
            stages["outbox"] = "SUCCESS"
            stages["email"] = "PARTIAL" if unresolved else "SUCCESS"
            return finish({"runId": run_id, "localDate": local_date,
                           "status": "PARTIAL" if unresolved else "SUCCESS",
                           "dataComplete": True, "resumedOutboxOnly": True,
                           "stages": stages, "delivery": asdict(initial_delivery)})
        args, cwd = srs_command(config.root)
        if (code := runner(args, cwd)) != 0:
            return fail("srs", code)
        stages["srs"] = "SUCCESS"
        issue_output = store.root / "collections" / run_id / "issues"
        args, cwd = issue_command(config.root, issue_output, config.issue_config)
        if (code := runner(args, cwd)) != 0:
            return fail("issues", code)
        stages["issues"] = "SUCCESS"
        current_srs = load_srs(config.srs_snapshot_dir / local_date)
        current_issues = load_issues(issue_output / "manifest.json")
        state = store.load_state()
        srs_changes = compute_changes("srs", current_srs, state["sources"].get("srs"))
        issue_changes = compute_changes("issues", current_issues, state["sources"].get("issues"))
        candidates = correlate(srs_changes, issue_changes, current_srs, current_issues, config.rules)
        stages["analysis"] = "SUCCESS"
        new_candidates = []
        for candidate in candidates:
            if candidate["id"] not in state["candidates"]:
                candidate["reviewState"] = "NEW"
                state["candidates"][candidate["id"]] = candidate
                new_candidates.append(candidate)
        state["sources"] = {"srs": current_srs, "issues": current_issues}
        if new_candidates:
            store.enqueue({"kind": "CANDIDATES", "status": "SUCCESS", "runId": run_id,
                           "summaryPath": f".automation/runs/{run_id}/summary.md",
                           "candidates": sanitize_candidates(new_candidates)})
        stages["outbox"] = "SUCCESS"
        store.save_state(state)
        delivery = drain_outbox(store, sender, now, config.max_email_attempts,
                                config.retry_minutes)
        incomplete_mail = store.has_unresolved_messages()
        stages["email"] = "PARTIAL" if incomplete_mail else "SUCCESS"
        return finish({"runId": run_id, "localDate": local_date,
                       "status": "PARTIAL" if incomplete_mail else "SUCCESS",
                       "dataComplete": True, "resumedOutboxOnly": False,
                       "candidateCount": len(new_candidates), "stages": stages,
                       "delivery": asdict(delivery)})
```

Use a per-run issue collection path. Locate SRS input as `config.srs_snapshot_dir / now.date().isoformat()`. Validate issue manifest with the same completeness rules as `observation.load_issues`. Write failure messages without raw exception strings.

- [ ] **Step 5: Implement the root CLI and manual requeue path**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "automation.yaml")
    parser.add_argument("--check-schema", action="store_true")
    parser.add_argument("--check-local", action="store_true")
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--retry-email", metavar="MESSAGE_ID")
    return parser
```

`--check-schema` validates only the supplied automation policy and tracked entrypoints, so CI can use `automation.example.yaml` without private files. `--check-local` additionally validates required private file existence, writable state parent, Python/app entrypoints, and SMTP field presence through the existing loader without network or send. It never prints protected paths beyond project-relative names. CLI maps manifest `SUCCESS/PARTIAL/FAILED` to 0/4/1 and configuration errors to 2.

- [ ] **Step 6: Add `auto` to the shared menu and PowerShell mode**

Add a fourth menu line `완전 자동화` with a clear warning that it performs server collection, SRS publication, and configured email. Direct CLI help must include `python run.py auto --help`. The launcher propagates 0/1/2/4/130 unchanged.

- [ ] **Step 7: Run Task 5 tests and commit**

Run: `python -m pytest tests/test_automation_orchestrator.py tests/test_launcher.py tests/test_observation.py tests/test_issue_reliability.py -q`

Expected: all Task 5 and related regression tests PASS.

```powershell
git add automation.py automation_core/orchestrator.py run.py run.ps1 실행하기.bat tests/test_automation_orchestrator.py tests/test_launcher.py
git commit -m "feat: orchestrate ALM QA automation"
```

---

### Task 6: Weekday Scheduler and Safe Transition

**Files:**
- Create: `scripts/install_automation_task.ps1`
- Create: `tests/test_automation_scheduler.py`
- Modify: `.gitignore` only if the scheduled-task XML backup path is not already ignored.

**Interfaces:**
- Consumes: PowerShell parameters `-TaskName`, `-PythonExe`, `-At`, `-Install`, `-FinalizeTransition`, common `-WhatIf`.
- Produces: task `ALM_QA_Automation_Daily`; JSON plan for read-only validation; verified legacy-task disable operation.

- [ ] **Step 1: Write failing plan and transition tests**

```python
def test_schedule_plan_is_weekdays_at_nine_and_integrated_root():
    result = run_powershell("scripts/install_automation_task.ps1", "-PlanJson")
    plan = json.loads(result.stdout)
    assert plan["taskName"] == "ALM_QA_Automation_Daily"
    assert plan["days"] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert plan["at"] == "09:00" and plan["mode"] == "auto"
    assert Path(plan["workingDirectory"]).name == "ALM-QA-Automation"

def test_finalize_rejects_failed_manifest_without_task_mutation(tmp_path):
    manifest = write_json(tmp_path / "manifest.json", {"status": "FAILED", "dataComplete": False})
    result = run_powershell("scripts/install_automation_task.ps1",
                            "-FinalizeTransition", "-Manifest", str(manifest), "-WhatIf")
    assert result.returncode != 0
    assert "Disable-ScheduledTask" not in result.stdout

def test_finalize_success_whatif_names_both_legacy_tasks(tmp_path):
    manifest = write_json(tmp_path / "manifest.json", {"status": "SUCCESS", "dataComplete": True})
    result = run_powershell("scripts/install_automation_task.ps1",
                            "-FinalizeTransition", "-Manifest", str(manifest), "-WhatIf")
    assert result.returncode == 0
    assert "VXvue_SRS_Spec_Automation" in result.stdout
    assert "VXvue_SRS_Spec_Automation_CatchUp" in result.stdout
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_automation_scheduler.py -q`

Expected: FAIL because the scheduler script does not exist.

- [ ] **Step 3: Implement plan, registration, readback, and backup**

```powershell
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = 'ALM_QA_Automation_Daily',
    [string]$PythonExe = '',
    [string]$At = '09:00',
    [switch]$PlanJson,
    [switch]$Install,
    [switch]$FinalizeTransition,
    [string]$Manifest = ''
)
```

Build one action: `python.exe automation.py`, working directory repository root. Use five weekly triggers or the Windows weekly trigger representation that readback proves covers Monday–Friday. Backup existing named task XML to `.migration/scheduled-tasks/` before mutation. After `Register-ScheduledTask`, compare execute, arguments, working directory, trigger days/time, network, `IgnoreNew`, execution limit, and priority; throw before reporting success on any mismatch.

- [ ] **Step 4: Implement guarded transition finalization**

Load only the named manifest path, require `status == "SUCCESS"` and `dataComplete is True`, then call `Disable-ScheduledTask` for both legacy names through `ShouldProcess`. Do not unregister them. Under `-WhatIf`, print the two intended disable operations without changing state.

- [ ] **Step 5: Run Task 6 tests and commit**

Run: `python -m pytest tests/test_automation_scheduler.py -q`

Expected: all scheduler tests PASS on Windows PowerShell.

```powershell
git add scripts/install_automation_task.ps1 tests/test_automation_scheduler.py .gitignore
git commit -m "feat: add weekday automation schedule"
```

---

### Task 7: Documentation, Full Verification, and Controlled Activation

**Files:**
- Modify: `README.md`
- Modify: `SPEC.md`
- Modify: `CHANGELOG.md`
- Modify: `progress.md`
- Modify: `docs/AUTOMATION_ROADMAP.md`
- Create: `docs/FULL_AUTOMATION_VALIDATION.md`

**Interfaces:**
- Consumes: completed Task 1–6 CLI, tests, manifests, schedule plan.
- Produces: recruiter-readable current behavior, requirement-to-code traceability, reproducible validation record, explicit operational limits.

- [ ] **Step 1: Update product and operator documentation**

Document `python run.py auto --help`, `python automation.py --check-local`, `--no-send`, manual email retry, `.automation/` recovery meaning, priority reasons, and the two-phase scheduler transition. Keep AI-assisted development distinct from runtime deterministic analysis.

- [ ] **Step 2: Update requirement status and changelog**

Change REQ-AUTO-001~004 traceability from the design document to exact implementation/test paths. Mark only synthetic/local-verified behavior as verified. Keep actual Polarion collection, SMTP delivery, first scheduled run, and long-running recovery under operational verification until evidence exists.

- [ ] **Step 3: Run the full automated suite**

Run: `python -m pytest tests apps/srs-spec/tests -q`

Expected: all tests PASS with no failures. Report any real-data test skip separately.

- [ ] **Step 4: Run structural and CLI validation**

Run:

```powershell
node ../tools/spec-lint.js ALM-QA-Automation
python automation.py --config automation.example.yaml --check-schema
python run.py auto --help
powershell -NoProfile -File scripts/install_automation_task.ps1 -PlanJson
git diff --check
```

Expected: spec lint has no WARN/ERROR; example schema check exits 0 without reading private configuration or using network; help exits 0; schedule plan reports weekday 09:00 and integrated root; diff check exits 0.

- [ ] **Step 5: Run a synthetic end-to-end pipeline**

Use temporary SRS snapshots, successful issue manifest/backups, injected child runner and SMTP sender through the test harness. Confirm one correlated candidate, one SENT email, one final SUCCESS manifest, and a replay with zero new candidates/messages. Store only non-sensitive summary evidence under `.migration/full-automation-smoke/`.

- [ ] **Step 6: Create the private runtime config without displaying it**

If `automation.yaml` is absent, copy `automation.example.yaml` byte-for-byte and rely on the existing SRS/issue protected configs. Run `automation.py --check-local`; do not print or commit private file contents.

- [ ] **Step 7: Install the new task without disabling legacy tasks**

First run `scripts/install_automation_task.ps1 -Install -WhatIf`, inspect the plan, then run `-Install`. Read back task name, state, trigger, action, working directory and settings without printing credentials. Do not run `-FinalizeTransition` until a real integrated data run produces a success manifest.

- [ ] **Step 8: Record validation and commit**

Write `docs/FULL_AUTOMATION_VALIDATION.md` with commands, exit codes, test counts, scheduler readback, and remaining live checks. Do not claim delivered email without mailbox evidence.

```powershell
git add README.md SPEC.md CHANGELOG.md progress.md docs/AUTOMATION_ROADMAP.md docs/FULL_AUTOMATION_VALIDATION.md
git commit -m "docs: document full automation operation"
```

- [ ] **Step 9: Final review and push**

Inspect `git diff HEAD~7..HEAD`, `git status --short`, tracked secret paths, local/remote commit IDs, and GitHub CI if present. Exclude `akela/learnings-log.jsonl`, `.automation/`, private configs, and `.migration/` from commits. Push `main` only after all required checks pass.

---

### Task 8: Filter Related Issues by the SRS Updated Window

**Files:**
- Modify: `automation_core/correlation.py`
- Modify: `automation_core/orchestrator.py`
- Modify: `automation_core/email.py`
- Modify: `tests/test_automation_correlation.py`
- Modify: `tests/test_automation_email.py`
- Modify: `README.md`
- Modify: `SPEC.md`
- Modify: `docs/superpowers/specs/2026-09-21-full-automation-design.md`

**Interfaces:**
- Consumes: changed SRS before/after records and linked issue `created`/`updated` attributes.
- Produces: linked issue IDs restricted to `(previous SRS updated, current SRS updated]`, plus the applied window and matched activity fields in candidate evidence.

- [ ] **Step 1: Add a failing temporal-correlation test**

Use outgoing and incoming links together. Put an old critical issue outside the SRS interval, one issue created inside it, and another updated inside it. Require only the latter two to affect priority and evidence.

- [ ] **Step 2: Apply the timestamp filter and retain evidence**

Parse ISO timestamps as UTC when they have no offset. Apply an exclusive lower bound and inclusive upper bound. Preserve the existing link-only behavior for legacy records whose two SRS timestamps do not form a valid interval.

- [ ] **Step 3: Verify report safety and full regression**

Ensure the digest contains only the safe interval/evidence fields, then run correlation, email, orchestrator, and full repository tests before activation.
