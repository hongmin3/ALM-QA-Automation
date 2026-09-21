import importlib.util
import base64
import json
import shutil
import pytest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_launcher():
    spec = importlib.util.spec_from_file_location('qa_launcher', ROOT / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_srs_routes_and_preserves_arguments():
    launcher = load_launcher()
    with patch.object(launcher.subprocess, 'call', return_value=7) as call:
        assert launcher.main(['srs', '--since', '2026-09-01']) == 7
    app = ROOT / 'apps' / 'srs-spec'
    call.assert_called_once_with(
        [sys.executable, str(app / 'main.py'), '--since', '2026-09-01'], cwd=app
    )


def test_issue_query_is_one_argument():
    launcher = load_launcher()
    query = 'status:(open) AND title:"two words"'
    with patch.object(launcher.subprocess, 'call', return_value=0) as call:
        assert launcher.main(['issues', '-query', query]) == 0
    app = ROOT / 'apps' / 'issue-export'
    call.assert_called_once_with(
        [sys.executable, str(app / 'polarion_query_backup.py'), '-query', query], cwd=app
    )


def test_invalid_mode_does_not_run_child():
    launcher = load_launcher()
    with patch.object(launcher.subprocess, 'call') as call:
        assert launcher.main(['invalid']) == 2
        call.assert_not_called()


def test_help_from_foreign_directory(tmp_path):
    for arguments in (['--help'], ['srs', '--help'], ['issues', '--help']):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'run.py'), *arguments],
            cwd=tmp_path, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert 'usage:' in result.stdout.lower()


@pytest.mark.parametrize('arguments', [
    ['issues', '-query', 'status:(open) AND title:"two words"', '-o', '한글 path\\'],
    ['srs'],
    ['issues', '--title', ''],
])
def test_powershell_preserves_native_arguments_and_exit_code(tmp_path, arguments):
    shutil.copy2(ROOT / 'run.ps1', tmp_path / 'run.ps1')
    (tmp_path / 'run.py').write_text(
        'import os, json, sys\n'
        'args=json.loads(os.environ["ALM_QA_LAUNCH_ARGS"]) if "ALM_QA_LAUNCH_ARGS" in os.environ else sys.argv[1:]\n'
        'print(json.dumps(args, ensure_ascii=True))\n'
        'sys.exit(7)\n', encoding='utf-8'
    )
    literal = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = '& ' + literal(tmp_path / 'run.ps1') + ' ' + ' '.join(map(literal, arguments))
    script += '; exit $LASTEXITCODE'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded], capture_output=True, text=True, timeout=30)
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == arguments


@pytest.mark.parametrize('answers,expected', [
    (['0'], None),
    (['1', ''], ['srs', '--dry-run']),
    (['1', '1'], ['srs']),
    (['1', '3', '2026-09-01'], ['srs', '--since', '2026-09-01']),
    (['2', '1', 'SAMPLE-1,SAMPLE-2'], ['issues', '-id', 'SAMPLE-1,SAMPLE-2', '--timestamp', '--open']),
    (['2', '2', 'title:"two words"'], ['issues', '-query', 'title:"two words"', '--timestamp', '--open']),
    (['2', '3'], ['issues', '--timestamp', '--open']),
    (['2', '4'], ['issues', '--check']),
])
def test_menu_routes(answers, expected):
    with patch('builtins.input', side_effect=answers):
        assert load_launcher().menu_arguments() == expected


@pytest.mark.parametrize('answers', [['9'], ['1', '3', '2026-02-30'], ['2', '1', '']])
def test_invalid_menu_never_starts_application(answers):
    launcher = load_launcher()
    with patch('builtins.input', side_effect=answers), patch.object(launcher.subprocess, 'call') as call:
        assert launcher.main(['--menu']) == 2
        call.assert_not_called()
