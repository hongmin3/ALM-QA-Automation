from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from automation_core.config import AutomationConfig, load_automation_config
from automation_core.email import (
    drain_outbox,
    existing_srs_sender,
    load_existing_srs_mail_settings,
)
from automation_core.orchestrator import AutomationBusyError, run_automation
from automation_core.state import AutomationStore


ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ALM QA 통합 수집·분석·알림 자동화")
    parser.add_argument("--config", type=Path, default=ROOT / "automation.yaml")
    parser.add_argument("--check-schema", action="store_true")
    parser.add_argument("--check-local", action="store_true")
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--retry-email", metavar="MESSAGE_ID")
    return parser


def _required_entrypoints(root: Path) -> tuple[Path, ...]:
    return (
        root / "automation.py",
        root / "run.py",
        root / "apps" / "srs-spec" / "main.py",
        root / "apps" / "issue-export" / "polarion_query_backup.py",
    )


def _check_schema(config: AutomationConfig) -> None:
    missing = [path for path in _required_entrypoints(config.root) if not path.is_file()]
    if missing:
        raise ValueError("required tracked entrypoint is missing")


def _check_local(config: AutomationConfig, *, require_mail: bool = True) -> Path:
    _check_schema(config)
    if not config.srs_config.is_file() or not config.issue_config.is_file():
        raise ValueError("required private configuration is missing")
    config.state_dir.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(config.state_dir.parent, os.W_OK):
        raise ValueError("automation state parent is not writable")
    _, settings, runtime = load_existing_srs_mail_settings(config.root, config.srs_config)
    snapshot_dir = Path(runtime.snapshots_dir).resolve()
    if not snapshot_dir.is_relative_to(config.root):
        raise ValueError("SRS snapshot directory must remain inside project")
    if require_mail and (not settings.enabled or settings.missing_fields()):
        raise ValueError("existing SRS mail settings are incomplete")
    return snapshot_dir


def _runner(args: list[str], cwd: Path) -> int:
    return subprocess.run(args, cwd=cwd, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_automation_config(args.config, ROOT)
        _check_schema(config)
        if args.check_schema:
            print("Automation schema check: OK")
            return 0
        if args.check_local:
            _check_local(config, require_mail=not args.no_send)
            print("Automation local check: OK")
            return 0

        if not (args.retry_email and args.no_send):
            snapshot_dir = _check_local(config, require_mail=not args.no_send)
            config = replace(config, srs_snapshot_dir=snapshot_dir)
        store = AutomationStore(config.state_dir)
        sender = None if args.no_send else existing_srs_sender(config.root, config.srs_config)
        now = datetime.now(timezone.utc)
        if args.retry_email:
            try:
                with store.locked(blocking=False):
                    store.requeue(args.retry_email)
                    if sender is not None:
                        drain_outbox(
                            store,
                            sender,
                            now,
                            config.max_email_attempts,
                            config.retry_minutes,
                        )
                    return 4 if store.has_unresolved_messages() else 0
            except OSError as exc:
                raise AutomationBusyError("another automation run holds the lock") from exc

        manifest = run_automation(config, store, _runner, sender, now)
        print(
            f"ALM QA automation: {manifest['status']} "
            f"(run {manifest['runId']}, candidates {manifest.get('candidateCount', 0)})"
        )
        return {"SUCCESS": 0, "PARTIAL": 4, "FAILED": 1}[manifest["status"]]
    except AutomationBusyError:
        print("Automation is already running.", file=sys.stderr)
        return 4
    except Exception:
        print("Automation configuration or local state check failed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
