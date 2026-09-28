# Validates: REQ-SRS-004
"""ORG 보관 및 다음 실행 자동 정리."""
from __future__ import annotations

import sys
import pytest
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.publish import archive_and_publish, default_org_folder


def _setup(tmp_path, existing: dict[str, bytes], generated: list[str]):
    knowledge = tmp_path / "VXvue" / "VXvue 지식파일"
    knowledge.mkdir(parents=True)
    for name, data in existing.items():
        (knowledge / name).write_bytes(data)

    out = tmp_path / "out"
    out.mkdir()
    pdfs = []
    for name in generated:
        f = out / name
        f.write_bytes(b"new-" + name.encode())
        pdfs.append(f)
    return knowledge, pdfs


def test_default_org_folder_is_sibling_of_knowledge_folder(tmp_path):
    kf = tmp_path / "VXvue" / "VXvue 지식파일"
    assert default_org_folder(kf) == tmp_path / "VXvue" / "ORG"


def test_previous_generation_moved_to_org_and_new_published(tmp_path):
    knowledge, pdfs = _setup(
        tmp_path,
        {"(사양서) VXvue 사양서1(260820).pdf": b"old"},
        ["(사양서) VXvue 사양서1(260824).pdf"],
    )
    result = archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")

    org = default_org_folder(knowledge)
    assert result.retained == ["(사양서) VXvue 사양서1(260820).pdf"]
    assert (org / "260824" / "(사양서) VXvue 사양서1(260820).pdf").read_bytes() == b"old"
    # 지식파일 폴더에는 신규만 남는다
    assert sorted(p.name for p in knowledge.glob("(사양서)*.pdf")) == ["(사양서) VXvue 사양서1(260824).pdf"]
    assert result.published == ["(사양서) VXvue 사양서1(260824).pdf"]
    # 최초 실행이므로 정리할 이전 세대가 없다
    assert result.purged_generations == []


def test_next_run_purges_previous_org_generation(tmp_path):
    """다음 실행이 검증을 통과해 여기 도달하면, 지난 세대 보관본이 자동 삭제된다."""
    knowledge, pdfs = _setup(
        tmp_path,
        {"(사양서) VXvue 사양서1(260820).pdf": b"gen1"},
        ["(사양서) VXvue 사양서1(260824).pdf"],
    )
    archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")

    # 그 다음 주 실행
    out2 = tmp_path / "out2"
    out2.mkdir()
    next_pdf = out2 / "(사양서) VXvue 사양서1(260831).pdf"
    next_pdf.write_bytes(b"gen3")
    result = archive_and_publish(knowledge_folder=knowledge, generated_pdfs=[next_pdf], file_date="260831")

    org = default_org_folder(knowledge)
    assert result.purged_generations == ["260824"]
    assert result.purged_files == 1
    assert not (org / "260824").exists(), "지난 세대는 자동 삭제되어야 한다"
    # ORG에는 항상 직전 세대 하나만 남는다
    assert sorted(p.name for p in org.iterdir()) == ["260831"]
    retained_260824 = org / "260831" / "(사양서) VXvue 사양서1(260824).pdf"
    assert retained_260824.read_bytes() == "new-(사양서) VXvue 사양서1(260824).pdf".encode("utf-8")


def test_purge_is_noop_when_org_already_deleted_by_hand(tmp_path):
    """사람이 이미 지웠거나 폴더가 없으면 아무것도 하지 않는다(오류 아님)."""
    knowledge, pdfs = _setup(tmp_path, {}, ["(사양서) VXvue 사양서1(260824).pdf"])
    result = archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert result.purged_generations == []
    assert result.retained == []
    assert result.published == ["(사양서) VXvue 사양서1(260824).pdf"]


def test_same_day_rerun_does_not_retain_its_own_output(tmp_path):
    """같은 날 재실행하면 '오늘 날짜' 파일은 이전 세대가 아니므로 보관하지 않는다."""
    knowledge, pdfs = _setup(
        tmp_path,
        {
            "(사양서) VXvue 사양서1(260820).pdf": b"real-previous",
            "(사양서) VXvue 사양서1(260824).pdf": b"same-day",
        },
        ["(사양서) VXvue 사양서1(260824).pdf"],
    )
    result = archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")

    org = default_org_folder(knowledge)
    assert result.retained == ["(사양서) VXvue 사양서1(260820).pdf"]
    assert result.overwritten == ["(사양서) VXvue 사양서1(260824).pdf"]
    assert sorted(p.name for p in (org / "260824").iterdir()) == ["(사양서) VXvue 사양서1(260820).pdf"]
    assert not any("_dup" in p.name for p in (org / "260824").iterdir())


def test_missing_knowledge_folder_is_skipped_not_fatal(tmp_path):
    result = archive_and_publish(knowledge_folder=None, generated_pdfs=[], file_date="260824")
    assert result.skipped is True
    assert result.published == []


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4, 5])
def test_copy_failure_preserves_live_and_previous_org(tmp_path, monkeypatch, failure_at):
    knowledge, pdfs = _setup(
        tmp_path,
        {"(사양서) old.pdf": b"old", "(사양서) same.pdf": b"same-old"},
        ["(사양서) same.pdf", "(사양서) new.pdf"],
    )
    org = default_org_folder(knowledge)
    (org / "260817").mkdir(parents=True)
    (org / "260817" / "(사양서) archived.pdf").write_bytes(b"archive")
    original_copy = shutil.copy2
    calls = 0

    def failing_copy(src, dst, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            Path(dst).write_bytes(b"partial")
            raise OSError("synthetic copy failure")
        return original_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", failing_copy)
    with pytest.raises(OSError, match="synthetic copy failure"):
        archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert {p.name: p.read_bytes() for p in knowledge.iterdir()} == {
        "(사양서) old.pdf": b"old", "(사양서) same.pdf": b"same-old",
    }
    assert (org / "260817" / "(사양서) archived.pdf").read_bytes() == b"archive"


def test_purge_preserves_unrelated_files_and_directories(tmp_path):
    knowledge, pdfs = _setup(tmp_path, {}, ["(사양서) new.pdf"])
    org = default_org_folder(knowledge)
    for folder in ["notes", "260817"]:
        (org / folder).mkdir(parents=True)
        (org / folder / "keep.txt").write_text("keep")
    (org / "notes" / "(사양서) reference.pdf").write_bytes(b"reference")
    (org / "260817" / "(사양서) old.pdf").write_bytes(b"old")
    archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert (org / "notes" / "(사양서) reference.pdf").read_bytes() == b"reference"
    assert (org / "260817" / "keep.txt").read_text() == "keep"
    assert not (org / "260817" / "(사양서) old.pdf").exists()


def test_empty_publish_does_not_remove_live_generation(tmp_path):
    knowledge, _ = _setup(tmp_path, {"(사양서) old.pdf": b"old"}, [])
    with pytest.raises(ValueError, match="PDF"):
        archive_and_publish(knowledge_folder=knowledge, generated_pdfs=[], file_date="260824")
    assert (knowledge / "(사양서) old.pdf").read_bytes() == b"old"


@pytest.mark.parametrize("operation", ["publish", "remove", "archive"])
def test_commit_io_failure_rolls_back_and_keeps_org(tmp_path, monkeypatch, operation):
    knowledge, pdfs = _setup(tmp_path,
        {"(사양서) old.pdf": b"old", "(사양서) same.pdf": b"same-old"},
        ["(사양서) same.pdf", "(사양서) new.pdf"])
    org = default_org_folder(knowledge)
    archived = org / "260817" / "(사양서) archived.pdf"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"archive")
    replace = Path.replace
    unlink = Path.unlink

    def fail_replace(src, dest):
        if ((operation == "publish" and src.parent.name == "incoming" and src.name.endswith("new.pdf"))
                or (operation == "archive" and src.parent.name.startswith(".srs-archive-"))):
            raise OSError("synthetic commit failure")
        return replace(src, dest)

    def fail_unlink(path, *args, **kwargs):
        if operation == "remove" and path == knowledge / "(사양서) old.pdf":
            raise OSError("synthetic commit failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError, match="synthetic commit failure"):
        archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert {p.name: p.read_bytes() for p in knowledge.iterdir()} == {
        "(사양서) old.pdf": b"old", "(사양서) same.pdf": b"same-old"}
    assert archived.read_bytes() == b"archive"
    assert not list((org / "260824").glob("*.pdf"))


def test_rollback_failure_keeps_recovery_copies(tmp_path, monkeypatch):
    knowledge, pdfs = _setup(tmp_path, {"(사양서) same.pdf": b"original"},
                             ["(사양서) same.pdf", "(사양서) new.pdf"])
    replace = Path.replace

    def fail_replace(src, dest):
        if src.name.endswith("new.pdf") or src.name == "restore.pdf":
            raise OSError("synthetic unavailable destination")
        return replace(src, dest)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    recovery = list(knowledge.parent.glob(".srs-publish-*/originals/(사양서) same.pdf"))
    assert len(recovery) == 1
    assert recovery[0].read_bytes() == b"original"


def test_archive_collision_preserves_all_existing_copies(tmp_path):
    knowledge, pdfs = _setup(tmp_path, {"(사양서) old.pdf": b"current"}, ["(사양서) new.pdf"])
    target = default_org_folder(knowledge) / "260824"
    target.mkdir(parents=True)
    (target / "(사양서) old.pdf").write_bytes(b"previous")
    (target / "(사양서) old_dup1.pdf").write_bytes(b"earlier")
    archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert sorted(p.read_bytes() for p in target.iterdir()) == [b"current", b"earlier", b"previous"]


def test_multiple_archive_names_cannot_overwrite_one_another(tmp_path):
    knowledge, pdfs = _setup(tmp_path,
        {"(사양서) old.pdf": b"current", "(사양서) old_dup1.pdf": b"other"},
        ["(사양서) new.pdf"])
    target = default_org_folder(knowledge) / "260824"
    target.mkdir(parents=True)
    (target / "(사양서) old.pdf").write_bytes(b"previous")
    archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert sorted(p.read_bytes() for p in target.iterdir()) == [b"current", b"other", b"previous"]


def test_publish_supports_python311_path_api(tmp_path, monkeypatch):
    knowledge, pdfs = _setup(tmp_path, {}, ["(사양서) new.pdf"])
    previous = default_org_folder(knowledge) / "260817"
    previous.mkdir(parents=True)
    (previous / "(사양서) old.pdf").write_bytes(b"old")
    monkeypatch.delattr(Path, "is_junction", raising=False)
    archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert not previous.exists()


def test_unexpected_interruption_preserves_recovery_copies(tmp_path, monkeypatch):
    knowledge, pdfs = _setup(tmp_path, {"(사양서) same.pdf": b"original"},
                             ["(사양서) same.pdf", "(사양서) new.pdf"])
    replace = Path.replace

    def interrupt_replace(src, dest):
        if src.name.endswith("new.pdf"):
            raise KeyboardInterrupt()
        return replace(src, dest)

    monkeypatch.setattr(Path, "replace", interrupt_replace)
    with pytest.raises(KeyboardInterrupt):
        archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    recovery = list(knowledge.parent.glob(".srs-publish-*/originals/(사양서) same.pdf"))
    assert recovery and recovery[0].read_bytes() == b"original"


def test_purge_listing_failure_does_not_report_committed_publish_as_failed(tmp_path, monkeypatch):
    knowledge, pdfs = _setup(tmp_path, {"(사양서) old.pdf": b"old"}, ["(사양서) new.pdf"])
    org = default_org_folder(knowledge)
    iterdir = Path.iterdir

    def fail_listing(path):
        if path == org:
            raise OSError("synthetic cleanup failure")
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_listing)
    result = archive_and_publish(knowledge_folder=knowledge, generated_pdfs=pdfs, file_date="260824")
    assert result.published == ["(사양서) new.pdf"]
    assert (org / "260824" / "(사양서) old.pdf").read_bytes() == b"old"
