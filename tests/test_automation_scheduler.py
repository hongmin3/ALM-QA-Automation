from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_automation_task.ps1"
HELPERS = ROOT / "scripts" / "automation_task_helpers.ps1"


def run_powershell(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_schedule_plan_is_weekdays_at_nine_and_integrated_root() -> None:
    result = run_powershell("-PlanJson")

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["taskName"] == "ALM_QA_Automation_Daily"
    assert plan["days"] == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert plan["at"] == "09:00"
    assert plan["mode"] == "auto"
    assert Path(plan["workingDirectory"]).name == "ALM-QA-Automation"
    assert Path(plan["entrypoint"]).name == "automation.py"


def test_finalize_rejects_failed_manifest_without_task_mutation(tmp_path: Path) -> None:
    manifest = write_json(tmp_path / "manifest.json", {"status": "FAILED", "dataComplete": False})

    result = run_powershell(
        "-FinalizeTransition", "-Manifest", str(manifest), "-WhatIf"
    )

    assert result.returncode != 0
    assert "Disable-ScheduledTask" not in result.stdout


def test_finalize_success_whatif_names_both_legacy_tasks(tmp_path: Path) -> None:
    manifest = write_json(tmp_path / "manifest.json", {"status": "SUCCESS", "dataComplete": True})

    result = run_powershell(
        "-FinalizeTransition", "-Manifest", str(manifest), "-WhatIf"
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "VXvue_SRS_Spec_Automation" in combined
    assert "VXvue_SRS_Spec_Automation_CatchUp" in combined
    assert "Unregister-ScheduledTask" not in combined


def test_real_weekday_trigger_uses_exact_windows_bitmask() -> None:
    command = (
        f". '{HELPERS}'; "
        "$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 "
        "-DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '09:00'; "
        "if (Test-AutomationWeekdayTrigger $trigger) { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
