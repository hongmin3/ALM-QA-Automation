from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


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


def _casefold_values(values: object, default: list[str]) -> frozenset[str]:
    selected = default if values is None else values
    if not isinstance(selected, list):
        raise ValueError("priority values must be lists")
    return frozenset(str(value).casefold() for value in selected)


def load_automation_config(path: Path, root: Path) -> AutomationConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(raw, dict):
        raise ValueError("automation configuration must be a mapping")

    root = root.resolve()

    def project_path(key: str, default: str) -> Path:
        candidate = (root / str(raw.get(key, default))).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"{key} must remain inside project")
        return candidate

    priority = raw.get("priority") or {}
    email = raw.get("email") or {}
    if not isinstance(priority, dict) or not isinstance(email, dict):
        raise ValueError("priority and email settings must be mappings")

    retries = tuple(int(value) for value in email.get("retry_minutes", [15, 60, 240]))
    if len(retries) != 3 or any(value < 1 for value in retries):
        raise ValueError("email.retry_minutes must contain three positive integers")
    max_attempts = int(email.get("max_attempts", 3))
    if max_attempts < 1:
        raise ValueError("email.max_attempts must be positive")

    return AutomationConfig(
        root=root,
        state_dir=project_path("state_dir", ".automation"),
        srs_snapshot_dir=project_path("srs_snapshot_dir", "apps/srs-spec/snapshots"),
        srs_config=project_path("srs_config", "apps/srs-spec/config/config.yaml"),
        issue_config=project_path("issue_config", "apps/issue-export/config.yaml"),
        max_email_attempts=max_attempts,
        retry_minutes=(retries[0], retries[1], retries[2]),
        rules=PriorityRules(
            reopened_statuses=_casefold_values(priority.get("reopened_statuses"), ["reopened"]),
            open_statuses=_casefold_values(
                priority.get("open_statuses"),
                ["open", "in_progress", "in_review", "reopened"],
            ),
            critical_severities=_casefold_values(
                priority.get("critical_severities"),
                ["blocker", "critical"],
            ),
            notify_priorities=frozenset(
                str(value).upper()
                for value in priority.get("notify_priorities", ["CRITICAL", "HIGH", "MEDIUM"])
            ),
        ),
    )
