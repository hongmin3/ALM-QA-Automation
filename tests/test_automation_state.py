from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from automation_core.config import load_automation_config
from automation_core.state import AutomationStore


def write_yaml(path: Path, value: dict) -> Path:
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def test_config_rejects_state_outside_root(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path / "automation.yaml",
        {
            "state_dir": "../outside",
            "srs_snapshot_dir": "apps/srs-spec/snapshots",
            "srs_config": "apps/srs-spec/config/config.yaml",
            "issue_config": "apps/issue-export/config.yaml",
        },
    )

    with pytest.raises(ValueError, match="inside project"):
        load_automation_config(path, tmp_path)


def test_enqueue_is_content_deduplicated_and_atomic(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / ".automation")

    with store.locked():
        first, created1 = store.enqueue({"kind": "CANDIDATES", "candidateIds": ["a"]})
        second, created2 = store.enqueue({"candidateIds": ["a"], "kind": "CANDIDATES"})

    assert created1 is True
    assert created2 is False
    assert first == second
    assert not list((tmp_path / ".automation").glob(".outbox-*.tmp"))


def test_lock_is_released_when_owner_process_dies(tmp_path: Path) -> None:
    state_dir = tmp_path / ".automation"
    code = (
        "from pathlib import Path; import time; "
        "from automation_core.state import AutomationStore; "
        f"store=AutomationStore(Path({str(state_dir)!r})); "
        "lock=store.locked(); lock.__enter__(); "
        "print('locked', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        process.terminate()
        process.wait(timeout=10)

        with AutomationStore(state_dir).locked():
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_run_lookup_and_unresolved_outbox(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / ".automation")
    store.save_state(
        {
            "schemaVersion": 2,
            "sources": {},
            "candidates": {},
            "runs": {
                "older": {"runId": "20260921T000000Z", "localDate": "2026-09-21", "dataComplete": True},
                "newer": {"runId": "20260921T010000Z", "localDate": "2026-09-21", "dataComplete": True},
                "partial": {"runId": "20260922T010000Z", "localDate": "2026-09-22", "dataComplete": False},
            },
        }
    )

    assert store.successful_run_for("2026-09-21")["runId"] == "20260921T010000Z"
    assert store.successful_run_for("2026-09-22") is None
    assert store.has_unresolved_messages() is False

    store.enqueue({"kind": "FAILURE", "runId": "example"})
    assert store.has_unresolved_messages() is True
