# Validates: REQ-SRS-002
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import pdf


def _child_worker(monkeypatch, tmp_path, *, recover=False, hang=False, sensitive=True, delay=0, budgets=None):
    """Run real child processes; replace only the slow Chromium boundary."""
    original_popen = subprocess.Popen
    attempts = tmp_path / "attempts.txt"
    script = """
import sys, time
from pathlib import Path
attempts, output, recover, hang, sensitive, delay = sys.argv[1:]
p = Path(attempts)
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
if hang == 'True':
    time.sleep(30)
if n == 1 or recover != 'True':
    time.sleep(float(delay))
    Path(output).write_bytes(b'partial')
    print('TimeoutError: Page.goto: Timeout 30000ms exceeded', file=sys.stderr)
    if sensitive == 'True':
        print('Authorization: Bearer synthetic-secret', file=sys.stderr)
    sys.exit(1)
assert not Path(output).exists(), 'partial PDF must be removed before retry'
from pypdf import PdfWriter
w = PdfWriter()
w.add_blank_page(width=595, height=842)
w.write(output)
"""

    def launch(args, **kwargs):
        proc = original_popen(
            [sys.executable, "-c", script, str(attempts), args[-1], str(recover), str(hang), str(sensitive), str(delay)],
            **kwargs,
        )
        if budgets is not None:
            original_wait = proc.wait

            def wait(timeout=None):
                if timeout is not None:
                    budgets.append(timeout)
                return original_wait(timeout=timeout)

            proc.wait = wait
        return proc

    monkeypatch.setattr(pdf.subprocess, "Popen", launch)
    return attempts


def test_worker_error_retries_and_validates_real_pdf(tmp_path, monkeypatch):
    attempts = _child_worker(monkeypatch, tmp_path, recover=True)
    result = pdf.html_to_pdf(tmp_path / "source.html", tmp_path / "output.pdf", 10)
    assert result.ok
    assert result.page_count == 1
    assert result.size_bytes > 0
    assert attempts.read_text() == "2"


def test_persistent_worker_error_is_bounded_and_logged_safely(tmp_path, monkeypatch, caplog):
    attempts = _child_worker(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING, logger="srs_automation"):
        result = pdf.html_to_pdf(tmp_path / "source.html", tmp_path / "output.pdf", 10)
    assert not result.ok
    assert attempts.read_text() == "2"
    assert "REDACTED" in caplog.text
    assert "synthetic-secret" not in caplog.text


def test_timeout_keeps_recovery_signal_and_does_not_retry(tmp_path, monkeypatch):
    attempts = _child_worker(monkeypatch, tmp_path, hang=True)
    # Avoid taskkill launching through the substituted worker boundary.
    monkeypatch.setattr(pdf, "_kill_process_tree", lambda pid: None)
    result = pdf.html_to_pdf(tmp_path / "source.html", tmp_path / "output.pdf", 3)
    assert not result.ok
    assert "timeout" in result.error
    assert attempts.read_text() == "1"


def test_worker_diagnostic_is_saved_in_application_log(tmp_path, monkeypatch, caplog):
    _child_worker(monkeypatch, tmp_path, sensitive=False)
    with caplog.at_level(logging.WARNING, logger="srs_automation"):
        result = pdf.html_to_pdf(tmp_path / "source.html", tmp_path / "output.pdf", 10)
    assert not result.ok
    assert "TimeoutError: Page.goto: Timeout 30000ms exceeded" in caplog.text


def test_retry_uses_remaining_time_budget(tmp_path, monkeypatch):
    budgets = []
    _child_worker(monkeypatch, tmp_path, recover=True, delay=0.5, budgets=budgets)
    result = pdf.html_to_pdf(tmp_path / "source.html", tmp_path / "output.pdf", 10)
    assert result.ok
    assert len(budgets) == 2
    assert 0 < budgets[1] < budgets[0] - 0.4
