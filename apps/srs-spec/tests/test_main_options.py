from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent


def load_main():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("srs_main_options", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_no_mail_parses_without_changing_other_defaults(monkeypatch) -> None:
    main = load_main()
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-mail"])

    args = main.parse_args()

    assert args.no_mail is True
    assert args.dry_run is False


def test_no_mail_skips_sender_after_success(monkeypatch) -> None:
    main = load_main()
    sent = []
    monkeypatch.setattr(main, "load_mail_settings", lambda *args: object())
    monkeypatch.setattr(main, "send_run_report", lambda *args, **kwargs: sent.append(args) or True)
    config = SimpleNamespace(raw={})

    result = main._send_pipeline_notification(
        Namespace(no_mail=True),
        config,
        {"ok": True},
        Path("report.html"),
    )

    assert result is False
    assert sent == []
