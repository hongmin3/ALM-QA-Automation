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
    def locked(self) -> Iterator[None]:
        lock_path = self.root / ".lock"
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
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
        self._atomic_write_at(self.root / "state.json", value)

    def load_outbox(self) -> dict:
        return self._read(
            self.root / "outbox.json",
            {"schemaVersion": 1, "messages": {}},
        )

    def save_outbox(self, value: dict) -> None:
        self._atomic_write_at(self.root / "outbox.json", value)

    def enqueue(self, payload: dict) -> tuple[str, bool]:
        message_id = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
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

    def save_run(self, run_id: str, manifest: dict, summary: str) -> Path:
        run_dir = self.root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
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
        if not isinstance(value, dict) or not isinstance(value.get("schemaVersion"), int):
            raise ValueError(f"invalid state value for {path.name}")
        self._atomic_text_at(path, canonical_json(value) + "\n")

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

