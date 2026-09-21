"""Synthetic offline checks. Validates: REQ-OBS-001."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import os

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location("observation", ROOT / "observation.py")
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def srs(root, title="A", **extra):
    write(root / "P" / "S-1.json", dict(project_id="P", id="S-1", uid="P/S-1", title=title, status="open", **extra))
    return root


def state(root):
    return json.loads((root / "state.json").read_text(encoding="utf-8"))


def test_baseline_changes_replay_and_review(tmp_path):
    m = module()
    source = srs(tmp_path / "snapshot")
    dest = tmp_path / "state"
    args = ["--srs-current", str(source), "--state-dir", str(dest)]
    assert m.main(args) == 0
    assert not state(dest)["candidates"]
    assert list(state(dest)["runs"].values())[-1]["baselineSources"] == ["srs"]
    srs(source, "B")
    assert m.main(args) == 0
    candidate = next(iter(state(dest)["candidates"]))
    assert m.main(["--state-dir", str(dest), "--candidate", candidate, "--review-state", "DONE"]) == 0
    srs(source, "B", collected_at="tomorrow")
    assert m.main(args) == 0
    assert len(state(dest)["candidates"]) == 1
    assert state(dest)["candidates"][candidate]["reviewState"] == "DONE"
    srs(source, "A")
    assert m.main(args) == 0
    srs(source, "B")
    assert m.main(args) == 0
    assert len(state(dest)["candidates"]) == 2
    assert state(dest)["candidates"][candidate]["reviewState"] == "DONE"
    assert len(list((dest / "runs").glob("*/summary.md"))) == 6


def test_explicit_previous_and_corruption_preserves_state(tmp_path):
    m = module()
    old = srs(tmp_path / "old")
    new = srs(tmp_path / "new", "B")
    dest = tmp_path / "state"
    args = ["--srs-current", str(new), "--srs-previous", str(old), "--state-dir", str(dest)]
    assert m.main(args) == 0
    before = (dest / "state.json").read_bytes()
    write(new / "P" / "S-2.json", {"id": "bad"})
    assert m.main(args) == 2
    assert (dest / "state.json").read_bytes() == before


def issue(root, **overrides):
    manifest = dict(status="SUCCESS", count=1, successCount=1, failureCount=0, selectedCount=1, limited=False, duplicateIds=[])
    manifest.update(overrides)
    write(root / "I-1" / "backup.json", dict(workitem={"id": "P/I-1", "attributes": {"id": "I-1", "title": "Issue", "status": "open"}}, comments=[], linkedWorkItems=[], attachments=[]))
    return write(root / "manifest.json", manifest)


def test_issue_manifest_completeness_and_status_evidence(tmp_path):
    m = module()
    manifest = issue(tmp_path / "issues")
    dest = tmp_path / "state"
    args = ["--issues", str(manifest), "--state-dir", str(dest)]
    assert m.main(args) == 0
    backup = manifest.parent / "I-1" / "backup.json"
    data = json.loads(backup.read_text())
    data["workitem"]["attributes"]["status"] = "closed"
    write(backup, data)
    assert m.main(args) == 0
    candidate = next(iter(state(dest)["candidates"].values()))
    assert candidate["statusChange"] == {"before": "open", "after": "closed"}
    before = (dest / "state.json").read_bytes()
    for change in ({"status": "PARTIAL"}, {"limited": True}, {"duplicateIds": ["I-1"]}, {"selectedCount": 2}):
        issue(manifest.parent, **change)
        assert m.main(args) == 2
        assert (dest / "state.json").read_bytes() == before
    write(manifest, {"count": 1})
    assert m.main(args) == 2


def test_lock_and_cli_smoke(tmp_path):
    source = srs(tmp_path / "source")
    dest = tmp_path / "state"
    result = subprocess.run([sys.executable, str(ROOT / "observation.py"), "--srs-current", str(source), "--state-dir", str(dest)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with module().run_lock(dest):
        assert module().main(["--srs-current", str(source), "--state-dir", str(dest)]) == 2
    assert len(state(dest)["runs"]) == 1


def test_atomic_failure_preserves_previous_generation(tmp_path, monkeypatch):
    m = module()
    source = srs(tmp_path / "source")
    dest = tmp_path / "state"
    args = ["--srs-current", str(source), "--state-dir", str(dest)]
    assert m.main(args) == 0
    before = (dest / "state.json").read_bytes()
    srs(source, "B")
    def fail(*args):
        raise OSError("synthetic disk error")
    monkeypatch.setattr(m.os, "replace", fail)
    assert m.main(args) == 2
    assert (dest / "state.json").read_bytes() == before
    assert len(list((dest / "runs").iterdir())) == 1
    with m.run_lock(dest):
        pass


def test_new_project_identity_and_absence_are_not_deletion(tmp_path):
    m = module()
    source = srs(tmp_path / "source")
    dest = tmp_path / "state"
    args = ["--srs-current", str(source), "--state-dir", str(dest)]
    assert m.main(args) == 0
    write(source / "Q" / "S-1.json", dict(project_id="Q", id="S-1", uid="Q/S-1", title="Other", status="open"))
    assert m.main(args) == 0
    candidate = next(iter(state(dest)["candidates"].values()))
    assert candidate["change"] == "NEW"
    assert candidate["project"] == "Q"
    (source / "P" / "S-1.json").unlink()
    assert m.main(args) == 0
    assert len(state(dest)["candidates"]) == 1


def test_malformed_backup_collections_rejected(tmp_path):
    manifest = issue(tmp_path / "issues")
    path = manifest.parent / "I-1" / "backup.json"
    backup = json.loads(path.read_text())
    backup["comments"] = None
    write(path, backup)
    dest = tmp_path / "state"
    assert module().main(["--issues", str(manifest), "--state-dir", str(dest)]) == 2
    assert not (dest / "state.json").exists()


def test_corrupt_state_is_rejected_without_overwrite(tmp_path):
    source = srs(tmp_path / "source")
    dest = tmp_path / "state"
    write(dest / "state.json", dict(schemaVersion=1, sources={"srs": []}, candidates={}, runs={}))
    before = (dest / "state.json").read_bytes()
    assert module().main(["--srs-current", str(source), "--state-dir", str(dest)]) == 2
    assert (dest / "state.json").read_bytes() == before


def test_process_lock_released_after_process_death(tmp_path):
    source = srs(tmp_path / "source")
    dest = tmp_path / "state"
    script = (
        "import sys,time; from pathlib import Path; import observation; "
        "lock=observation.run_lock(Path(sys.argv[1])); lock.__enter__(); "
        "print('LOCKED',flush=True); time.sleep(60)"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    child = subprocess.Popen([sys.executable, "-c", script, str(dest)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    args = [sys.executable, str(ROOT / "observation.py"), "--srs-current", str(source), "--state-dir", str(dest)]
    try:
        assert child.stdout.readline().strip() == "LOCKED"
        blocked = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert blocked.returncode == 2
        assert not (dest / "state.json").exists()
    finally:
        child.kill()
        child.communicate(timeout=10)
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (dest / ".lock").exists()


def test_optional_manifest_health_fields_rejected(tmp_path):
    for index, change in enumerate(({"pdfStatus": "FAILED"}, {"missingIdCount": 1}, {"warnings": ["incomplete"]}, {"searchedCount": 2})):
        manifest = issue(tmp_path / str(index), **change)
        dest = tmp_path / ("state" + str(index))
        assert module().main(["--issues", str(manifest), "--state-dir", str(dest)]) == 2
        assert not (dest / "state.json").exists()
