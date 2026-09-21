from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AutomationStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[None]:
        lock_path = self.root / ".lock"
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(handle.fileno(), mode)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load_state(self) -> dict:
        return self._read(
            self.root / "state.json",
            {"schemaVersion": 2, "sources": {}, "candidates": {}, "runs": {}},
        )

    def save_state(self, value: dict) -> None:
        self._validate_state_object(value, expected_schema=2, name="state.json")
        self._atomic_write_at(self.root / "state.json", value)

    def load_outbox(self) -> dict:
        return self._read(
            self.root / "outbox.json",
            {"schemaVersion": 1, "messages": {}},
        )

    def save_outbox(self, value: dict) -> None:
        self._validate_state_object(value, expected_schema=1, name="outbox.json")
        self._atomic_write_at(self.root / "outbox.json", value)

    def migrate_observation(self, path: Path) -> bool:
        if not path.is_file():
            return False
        state = self.load_state()
        migrations = state.setdefault("migrations", {})
        if not isinstance(migrations, dict):
            raise ValueError("state migrations must be an object")
        marker = "observationSchema1"
        if marker in migrations:
            return False

        old = json.loads(path.read_text(encoding="utf-8-sig"))
        self._validate_state_object(old, expected_schema=1, name="observation state")
        for key in ("sources", "candidates", "runs"):
            source = old.get(key)
            target = state.get(key)
            if not isinstance(source, dict) or not isinstance(target, dict):
                raise ValueError(f"observation {key} must be an object")
            state[key] = {**source, **target}
        migrations[marker] = str(path.resolve())
        self.save_state(state)
        return True

    def enqueue(self, payload: dict) -> tuple[str, bool]:
        identity: object = payload
        if payload.get("kind") == "CANDIDATES":
            candidate_ids = payload.get("candidateIds")
            if not isinstance(candidate_ids, list):
                candidate_ids = [
                    item.get("id")
                    for item in payload.get("candidates", [])
                    if isinstance(item, dict) and item.get("id")
                ]
            identity = {
                "kind": "CANDIDATES",
                "candidateIds": sorted(str(value) for value in candidate_ids),
            }
        message_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        outbox = self.load_outbox()
        messages = outbox.get("messages")
        if not isinstance(messages, dict):
            raise ValueError("outbox messages must be an object")
        if message_id in messages:
            return message_id, False
        messages[message_id] = {
            "id": message_id,
            "status": "PENDING",
            "attempts": 0,
            "nextAttemptAt": None,
            "payload": payload,
        }
        self.save_outbox(outbox)
        return message_id, True

    def requeue(self, message_id: str) -> None:
        outbox = self.load_outbox()
        messages = outbox.get("messages", {})
        item = messages.get(message_id) if isinstance(messages, dict) else None
        if not isinstance(item, dict):
            raise ValueError("unknown outbox message")
        if item.get("status") not in {"FAILED", "SENDING"}:
            raise ValueError("only FAILED or SENDING messages can be requeued")
        item["status"] = "PENDING"
        item["attempts"] = 0
        item["nextAttemptAt"] = None
        self.save_outbox(outbox)

    def save_run(self, run_id: str, manifest: dict, summary: str) -> Path:
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_at(run_dir / "manifest.json", manifest)
        self._atomic_text_at(run_dir / "summary.md", summary)
        return run_dir

    def successful_run_for(self, local_date: str) -> dict | None:
        state = self.load_state()
        runs = state.get("runs")
        if not isinstance(runs, dict):
            raise ValueError("state runs must be an object")
        matches = [
            run
            for run in runs.values()
            if isinstance(run, dict)
            and run.get("localDate") == local_date
            and run.get("dataComplete") is True
        ]
        return max(matches, key=lambda run: str(run.get("runId", "")), default=None)

    def has_unresolved_messages(self) -> bool:
        messages = self.load_outbox().get("messages", {})
        if not isinstance(messages, dict):
            raise ValueError("outbox messages must be an object")
        return any(
            isinstance(item, dict) and item.get("status") in {"PENDING", "SENDING", "FAILED"}
            for item in messages.values()
        )

    def _read(self, path: Path, default: dict) -> dict:
        if not path.exists():
            return json.loads(json.dumps(default))
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("schemaVersion"), int):
            raise ValueError(f"invalid state file: {path.name}")
        return value

    def _atomic_write_at(self, path: Path, value: dict) -> None:
        if not isinstance(value, dict):
            raise ValueError(f"invalid JSON object for {path.name}")
        self._atomic_text_at(path, canonical_json(value) + "\n")

    def atomic_text(self, path: Path, value: str) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("atomic output must remain inside state directory")
        self._atomic_text_at(resolved, value)

    @staticmethod
    def _validate_state_object(value: object, *, expected_schema: int, name: str) -> None:
        if not isinstance(value, dict) or value.get("schemaVersion") != expected_schema:
            raise ValueError(f"invalid state file: {name}")

    def _atomic_text_at(self, path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = path.stem.lstrip(".") or "state"
        temporary = path.parent / f".{prefix}-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
