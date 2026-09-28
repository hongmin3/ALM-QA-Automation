from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import automation
from automation_core.config import AutomationConfig, PriorityRules
from automation_core.orchestrator import run_automation
from automation_core.state import AutomationStore


@dataclass
class Fixture:
    root: Path
    config: AutomationConfig
    store: AutomationStore
    now: datetime

    @property
    def sender(self):
        return lambda message: True

    def runner(
        self,
        calls: list,
        *,
        srs: int = 0,
        issues: int = 0,
        srs_title: str = "Requirement",
        linked_issues: list[str] | None = None,
    ):
        local_date = self.now.date().isoformat()

        def run(args: list[str], cwd: Path) -> int:
            mode = args[2]
            calls.append(SimpleNamespace(mode=mode, args=args, cwd=cwd))
            if mode == "srs":
                if srs == 0:
                    path = self.config.srs_snapshot_dir / local_date / "P" / "SRS-1.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        json.dumps(
                            {
                                "project_id": "P",
                                "id": "SRS-1",
                                "uid": "P/SRS-1",
                                "title": srs_title,
                                "status": "approved",
                                "linkedWorkItems": linked_issues or [],
                            }
                        ),
                        encoding="utf-8",
                    )
                return srs
            if mode == "issues":
                output = Path(args[args.index("--out") + 1])
                if issues == 0:
                    issue_dir = output / "ISSUE-7"
                    issue_dir.mkdir(parents=True, exist_ok=True)
                    (issue_dir / "backup.json").write_text(
                        json.dumps(
                            {
                                "workitem": {
                                    "id": "P/ISSUE-7",
                                    "attributes": {
                                        "id": "ISSUE-7",
                                        "title": "Issue",
                                        "status": "open",
                                    },
                                },
                                "comments": [],
                                "linkedWorkItems": [],
                                "attachments": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                    (output / "manifest.json").write_text(
                        json.dumps(
                            {
                                "status": "SUCCESS",
                                "count": 1,
                                "successCount": 1,
                                "failureCount": 0,
                                "selectedCount": 1,
                                "searchedCount": 1,
                                "limited": False,
                                "duplicateIds": [],
                                "warnings": [],
                                "missingIdCount": 0,
                                "pdfStatus": "SUCCESS",
                            }
                        ),
                        encoding="utf-8",
                    )
                return issues
            raise AssertionError(f"unexpected mode: {mode}")

        return run

    def seed_successful_run(self, *, email_pending: bool) -> None:
        state = self.store.load_state()
        state["runs"]["prior"] = {
            "runId": "prior",
            "localDate": self.now.date().isoformat(),
            "dataComplete": True,
        }
        self.store.save_state(state)
        if email_pending:
            self.store.enqueue(
                {
                    "kind": "RUN_FAILED",
                    "status": "FAILED",
                    "summaryPath": ".automation/runs/prior/summary.md",
                }
            )


def make_fixture(tmp_path: Path) -> Fixture:
    root = tmp_path / "repo"
    root.mkdir()
    config = AutomationConfig(
        root=root,
        state_dir=root / ".automation",
        srs_snapshot_dir=root / "apps" / "srs-spec" / "snapshots",
        srs_config=root / "apps" / "srs-spec" / "config" / "config.yaml",
        issue_config=root / "apps" / "issue-export" / "config.yaml",
        max_email_attempts=3,
        retry_minutes=(15, 60, 240),
        rules=PriorityRules(
            reopened_statuses=frozenset({"reopened"}),
            open_statuses=frozenset({"open"}),
            critical_severities=frozenset({"critical", "blocker"}),
            notify_priorities=frozenset({"CRITICAL", "HIGH", "MEDIUM"}),
        ),
    )
    return Fixture(root, config, AutomationStore(config.state_dir), datetime(2026, 9, 21, tzinfo=timezone.utc))


def test_success_runs_stages_in_order_and_records_manifest(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    calls = []

    result = run_automation(fixture.config, fixture.store, fixture.runner(calls), fixture.sender, fixture.now)

    assert [call.mode for call in calls] == ["srs", "issues"]
    assert result["status"] == "SUCCESS"
    assert result["stages"] == {
        "srs": "SUCCESS",
        "issues": "SUCCESS",
        "analysis": "SUCCESS",
        "outbox": "SUCCESS",
        "email": "SUCCESS",
    }
    assert (fixture.store.root / "runs" / result["runId"] / "manifest.json").is_file()
    assert "--config" in calls[0].args
    assert calls[0].args[calls[0].args.index("--config") + 1] == str(fixture.config.srs_config)


def test_partial_issue_result_does_not_update_baseline(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    before = fixture.store.load_state()

    result = run_automation(fixture.config, fixture.store, fixture.runner([], issues=4), fixture.sender, fixture.now)

    assert result["status"] == "FAILED"
    assert result["stages"]["analysis"] == "NOT_RUN"
    after = fixture.store.load_state()
    assert after["sources"] == before["sources"]
    assert after["candidates"] == before["candidates"]
    message = next(iter(fixture.store.load_outbox()["messages"].values()))
    assert message["payload"]["kind"] == "RUN_FAILED"


def test_successful_same_day_rerun_only_drains_outbox(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    fixture.seed_successful_run(email_pending=True)
    calls = []

    result = run_automation(fixture.config, fixture.store, fixture.runner(calls), fixture.sender, fixture.now)

    assert calls == []
    assert result["resumedOutboxOnly"] is True


def test_synthetic_end_to_end_sends_one_candidate_once(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    baseline = run_automation(
        fixture.config,
        fixture.store,
        fixture.runner([]),
        fixture.sender,
        fixture.now,
    )
    assert baseline["candidateCount"] == 0

    fixture.now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    sent = []
    result = run_automation(
        fixture.config,
        fixture.store,
        fixture.runner([], srs_title="Changed", linked_issues=["P/ISSUE-7"]),
        lambda message: sent.append(message) or True,
        fixture.now,
    )

    assert result["status"] == "SUCCESS"
    assert result["candidateCount"] == 1
    assert result["notificationCount"] == 1
    assert len(sent) == 1
    assert next(iter(fixture.store.load_outbox()["messages"].values()))["status"] == "SENT"

    calls = []
    replay = run_automation(
        fixture.config,
        fixture.store,
        fixture.runner(calls, srs_title="Changed", linked_issues=["P/ISSUE-7"]),
        lambda message: sent.append(message) or True,
        fixture.now,
    )
    assert replay["resumedOutboxOnly"] is True
    assert calls == []
    assert len(sent) == 1


def test_crash_between_candidate_enqueue_and_state_save_does_not_duplicate_mail(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = make_fixture(tmp_path)
    run_automation(fixture.config, fixture.store, fixture.runner([]), fixture.sender, fixture.now)
    fixture.now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    original_save = fixture.store.save_state
    crashed = False

    def crash_after_enqueue(value: dict) -> None:
        nonlocal crashed
        if value.get("candidates") and not crashed:
            crashed = True
            raise OSError("synthetic commit interruption")
        original_save(value)

    monkeypatch.setattr(fixture.store, "save_state", crash_after_enqueue)
    with pytest.raises(OSError, match="synthetic commit interruption"):
        run_automation(
            fixture.config,
            fixture.store,
            fixture.runner([], srs_title="Changed", linked_issues=["P/ISSUE-7"]),
            fixture.sender,
            fixture.now,
        )
    monkeypatch.setattr(fixture.store, "save_state", original_save)

    sent = []
    result = run_automation(
        fixture.config,
        fixture.store,
        fixture.runner([], srs_title="Changed", linked_issues=["P/ISSUE-7"]),
        lambda message: sent.append(message) or True,
        fixture.now,
    )

    assert result["status"] == "SUCCESS"
    assert len(sent) == 1
    assert len(fixture.store.load_outbox()["messages"]) == 1


def test_automatic_run_migrates_existing_observation_before_baseline(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    old_path = fixture.root / ".observation" / "state.json"
    old_path.parent.mkdir()
    old = {
        "schemaVersion": 1,
        "sources": {},
        "candidates": {
            "reviewed": {
                "id": "reviewed",
                "source": "srs",
                "project": "P",
                "itemId": "SRS-OLD",
                "reviewState": "DONE",
            }
        },
        "runs": {},
    }
    old_path.write_text(json.dumps(old), encoding="utf-8")

    run_automation(fixture.config, fixture.store, fixture.runner([]), fixture.sender, fixture.now)

    assert fixture.store.load_state()["candidates"]["reviewed"]["reviewState"] == "DONE"
    assert json.loads(old_path.read_text(encoding="utf-8")) == old


def test_interrupted_run_keeps_progress_manifest(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    normal = fixture.runner([])

    def interrupted(args: list[str], cwd: Path) -> int:
        if args[2] == "issues":
            raise RuntimeError("synthetic interruption")
        return normal(args, cwd)

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        run_automation(fixture.config, fixture.store, interrupted, fixture.sender, fixture.now)

    manifests = list((fixture.store.root / "runs").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "RUNNING"
    assert manifest["stages"]["srs"] == "SUCCESS"
    assert manifest["stages"]["issues"] == "NOT_RUN"
    assert manifest["inputs"]["srsSnapshot"].endswith("2026-09-21")


def test_check_local_never_runs_network_or_smtp(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    for path in (
        root / "apps" / "srs-spec" / "src",
        root / "apps" / "srs-spec" / "config",
        root / "apps" / "issue-export",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (root / "run.py").write_text("", encoding="utf-8")
    (root / "automation.py").write_text("", encoding="utf-8")
    (root / "apps" / "srs-spec" / "main.py").write_text("", encoding="utf-8")
    (root / "apps" / "issue-export" / "polarion_query_backup.py").write_text("", encoding="utf-8")
    (root / "apps" / "issue-export" / "config.yaml").write_text("{}", encoding="utf-8")
    (root / "apps" / "srs-spec" / "config" / "config.yaml").write_text(
        yaml.safe_dump({"mail": {"enabled": True}}), encoding="utf-8"
    )
    (root / "apps" / "srs-spec" / "src" / "config.py").write_text(
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "def load_config(path): return SimpleNamespace(raw={'mail': {'enabled': True}}, snapshots_dir=Path(path).parent.parent/'snapshots')\n",
        encoding="utf-8",
    )
    (root / "apps" / "srs-spec" / "src" / "notify.py").write_text(
        "class Settings:\n"
        "    enabled=True\n"
        "    from_addr='from@example.test'\n"
        "    to_addrs=['to@example.test']\n"
        "    def missing_fields(self): return []\n"
        "def load_mail_settings(raw, root): return Settings()\n"
        "def send_message(settings, message): raise AssertionError('SMTP called')\n",
        encoding="utf-8",
    )
    config_path = root / "automation.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "state_dir": ".automation",
                "srs_snapshot_dir": "apps/srs-spec/snapshots",
                "srs_config": "apps/srs-spec/config/config.yaml",
                "issue_config": "apps/issue-export/config.yaml",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(automation, "ROOT", root)
    monkeypatch.setattr(automation.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("process started")))

    assert automation.main(["--config", str(config_path), "--check-local"]) == 0


def test_retry_email_lock_contention_returns_partial_code(tmp_path: Path, monkeypatch) -> None:
    fixture = make_fixture(tmp_path)
    monkeypatch.setattr(automation, "load_automation_config", lambda path, root: fixture.config)
    monkeypatch.setattr(automation, "_check_schema", lambda config: None)

    @contextmanager
    def busy_lock(self, *, blocking=True):
        raise OSError("busy")
        yield

    monkeypatch.setattr(AutomationStore, "locked", busy_lock)

    assert automation.main(["--retry-email", "message", "--no-send"]) == 4


def test_normal_run_requires_local_preflight_before_external_work(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = make_fixture(tmp_path)
    ran = []
    monkeypatch.setattr(automation, "load_automation_config", lambda path, root: fixture.config)
    monkeypatch.setattr(automation, "_check_schema", lambda config: None)
    monkeypatch.setattr(
        automation,
        "_check_local",
        lambda config, require_mail=True: (_ for _ in ()).throw(ValueError("mail disabled")),
    )
    monkeypatch.setattr(automation, "existing_srs_sender", lambda *args: lambda message: True)
    monkeypatch.setattr(
        automation,
        "run_automation",
        lambda *args: ran.append(True) or {"status": "SUCCESS", "runId": "x"},
    )

    assert automation.main([]) == 2
    assert ran == []


def test_malformed_yaml_returns_sanitized_configuration_error(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "automation.yaml"
    path.write_text("password: TOP-SECRET-VALUE\nbroken: [\n", encoding="utf-8")

    assert automation.main(["--config", str(path), "--check-schema"]) == 2
    captured = capsys.readouterr()
    assert "TOP-SECRET-VALUE" not in captured.err
