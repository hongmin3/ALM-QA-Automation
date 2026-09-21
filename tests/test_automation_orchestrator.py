from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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

    def runner(self, calls: list, *, srs: int = 0, issues: int = 0):
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
                                "title": "Requirement",
                                "status": "approved",
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
