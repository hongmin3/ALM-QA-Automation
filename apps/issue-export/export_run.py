"""Locked, staged issue exports. Existing results are never deleted up front."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import uuid
from datetime import datetime, timezone


class ExportRun:
    def __init__(self, target: Path):
        if target.is_symlink():
            raise ValueError('Output directory cannot be a symbolic link')
        self.target = target.resolve()
        if any(part.casefold() == '.git' for part in self.target.parts):
            raise ValueError('Git metadata cannot be an output directory')
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
        self.stage: Path | None = None
        self.lock = None
        self.finished = False
        self.details = {'searchedCount': None, 'selectedCount': None, 'successCount': 0,
                        'failureCount': 0, 'pdfStatus': 'NOT_RUN'}

    def __enter__(self):
        cwd = Path.cwd().resolve()
        source = Path(__file__).resolve().parent
        if (self.target in {Path(self.target.anchor), Path.home().resolve(), cwd, source}
                or self.target in cwd.parents or self.target in source.parents
                or (self.target / '.git').exists() or self.target.is_symlink()):
            raise ValueError('Output must be a dedicated export directory')
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.lock = (self.target.parent / f'.{self.target.name}.export.lock').open('a+b')
        try:
            self.lock.seek(0, 2)
            if self.lock.tell() == 0:
                self.lock.write(b'0'); self.lock.flush()
            self.lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.stage = Path(tempfile.mkdtemp(prefix=f'.{self.target.name}.staging-', dir=self.target.parent))
        except BaseException:
            self.lock.close()
            raise
        return self

    def _write(self, manifest: dict, destination: Path):
        payload = {**self.details, **manifest, 'runId': self.run_id,
                   'generatedAt': datetime.now(timezone.utc).isoformat(),
                   'outputDirectory': str(destination)}
        path = self.stage / 'manifest.json'
        temp = path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)

    def finish(self, manifest: dict) -> Path:
        success = manifest['status'] == 'SUCCESS'
        destination = self.target if success else self.target.with_name(self.target.name + '.failed-' + self.run_id)
        self._write(manifest, destination)
        previous = self.target.with_name(self.target.name + '.previous-' + self.run_id)
        moved_previous = False
        if success and self.target.exists():
            self.target.rename(previous)
            moved_previous = True
        try:
            self.stage.rename(destination)
        except BaseException:
            if moved_previous:
                previous.rename(self.target)
            raise
        self.finished = True
        return destination

    def __exit__(self, exc_type, exc, tb):
        try:
            if not self.finished and self.stage is not None and self.stage.exists():
                # Keep all evidence, including interrupted exports. Never copy exception
                # messages here: server errors may contain credentials or private URLs.
                self.finish({'status': 'FAILED', 'errorType': exc_type.__name__ if exc_type else 'IncompleteRun'})
        finally:
            if self.lock:
                self.lock.close()  # OS releases ownership even if the process exits.
        return False


def child_path(directory: Path, name: str) -> Path:
    """Output filenames must not escape the per-run staging directory."""
    target = (directory / name).resolve()
    if not target.is_relative_to(directory.resolve()) or target == directory.resolve():
        raise ValueError('Output filename escapes the export directory')
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
