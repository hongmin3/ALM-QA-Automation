import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collector import CollectStats
from src.pdf import PdfResult
from src.validate import check_duplicates, validate_run


def test_check_duplicates_finds_repeated_uid():
    records = [{"uid": "VXvue/VP-1"}, {"uid": "VXvue/VP-2"}, {"uid": "VXvue/VP-1"}]
    assert check_duplicates(records) == ["VXvue/VP-1"]


def test_validate_run_fails_when_srs_count_mismatch_protects_existing_pdfs():
    stats = [CollectStats(project_id="VXvue", total_items=350, expected_total=360)]
    result = validate_run(
        collect_stats=stats,
        all_records=[{"uid": f"VXvue/VP-{i}", "id": f"VP-{i}"} for i in range(350)],
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=100, page_count=5, ok=True)],
        expected_pdf_count=1,
        partition_warnings=[],
    )
    assert result.ok is False
    failed_names = [c["name"] for c in result.checks if not c["passed"]]
    assert "srs_count_match:VXvue" in failed_names


def test_validate_run_fails_when_pdf_is_empty():
    stats = [CollectStats(project_id="VXvue", total_items=1, expected_total=1)]
    result = validate_run(
        collect_stats=stats,
        all_records=[{"uid": "VXvue/VP-1", "id": "VP-1"}],
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=0, page_count=0, ok=False, error="empty")],
        expected_pdf_count=1,
        partition_warnings=[],
    )
    assert result.ok is False


def test_validate_run_passes_when_all_checks_ok():
    stats = [CollectStats(project_id="VXvue", total_items=2, expected_total=2)]
    result = validate_run(
        collect_stats=stats,
        all_records=[{"uid": "VXvue/VP-1", "id": "VP-1"}, {"uid": "VXvue/VP-2", "id": "VP-2"}],
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=100, page_count=3, ok=True)],
        expected_pdf_count=1,
        partition_warnings=[],
    )
    assert result.ok is True
