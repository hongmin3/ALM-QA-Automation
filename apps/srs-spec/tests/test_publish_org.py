# Validates: REQ-SRS-004
"""ORG 보관 및 다음 실행 자동 정리."""
from __future__ import annotations

import sys
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
