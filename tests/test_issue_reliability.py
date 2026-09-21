"""Synthetic exporter tests; never contact a real ALM server."""
# Validates: REQ-OPS-002
# Validates: REQ-ISSUE-001
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml

APP = Path(__file__).resolve().parents[1] / 'apps' / 'issue-export'


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    spec = importlib.util.spec_from_file_location('issue_under_test', APP / 'polarion_query_backup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_export(exporter, monkeypatch, tmp_path, items, *, pdf_error=False, query_error=False, fail_item=False, real_renderer=False, extra=(), output_options=None):
    target = tmp_path / 'export'
    config = tmp_path / 'settings.yaml'
    config.write_text(yaml.safe_dump({
        'polarion': {'host': 'https://example.invalid', 'project_id': 'TEST'},
        'search': {'query': 'type:defect'}, 'fields': {},
        'output': {'directory': str(target), 'generate_pdf': True, 'generate_markdown': True,
                   'generate_per_issue_html': False, 'save_raw_json': True, **(output_options or {})},
    }), encoding='utf-8')

    class FakeClient:
        project_id = 'TEST'
        host = 'https://example.invalid'
        def __init__(self, config): pass
        def query_workitems(self, **kwargs):
            if query_error: raise RuntimeError('synthetic query failure')
            return items
        def workitem_children(self, *args):
            if fail_item: raise RuntimeError('synthetic item failure')
            return []
        def request(self, *args, **kwargs):
            raise OSError('synthetic image failure')

    monkeypatch.setattr(exporter, 'PolarionClient', FakeClient)
    if not real_renderer:
        monkeypatch.setattr(exporter, 'render_workitem', lambda *a: '<h1>synthetic issue</h1>')
        monkeypatch.setattr(exporter, 'render_workitem_markdown', lambda *a: '# synthetic issue')
    def pdf(html, dest, **kwargs):
        if pdf_error: raise RuntimeError('synthetic pdf failure')
        dest.write_bytes(b'%PDF-synthetic')
    monkeypatch.setattr(exporter, 'generate_pdf_from_html', pdf)
    monkeypatch.setattr(sys, 'argv', ['export', '--config', str(config), '-y', *extra])
    code = exporter.main()
    return code, target


def item(id='TEST-1'):
    return {'id': 'TEST/'+id, 'attributes': {'id': id, 'title': 'Synthetic', 'status': 'open'}}


def manifests(tmp_path):
    return [json.loads(p.read_text(encoding='utf-8')) for p in tmp_path.rglob('manifest.json')]


def test_success_has_complete_manifest(exporter, monkeypatch, tmp_path):
    code, target = run_export(exporter, monkeypatch, tmp_path, [item()])
    assert code == 0
    result = json.loads((target/'manifest.json').read_text(encoding='utf-8'))
    assert result['status'] == 'SUCCESS'
    assert result['searchedCount'] == result['selectedCount'] == result['successCount'] == 1
    assert result['pdfStatus'] == 'SUCCESS'


def test_pdf_failure_keeps_previous_success(exporter, monkeypatch, tmp_path):
    target = tmp_path/'export'; target.mkdir(); (target/'keep.txt').write_text('previous')
    code, _ = run_export(exporter, monkeypatch, tmp_path, [item()], pdf_error=True)
    assert code == 4
    assert (target/'keep.txt').read_text() == 'previous'
    result = manifests(tmp_path)[0]
    assert result['status'] == 'PARTIAL' and result['pdfStatus'] == 'FAILED'


def test_zero_results_get_new_manifest_and_preserve_old_copy(exporter, monkeypatch, tmp_path):
    target=tmp_path/'export'; target.mkdir(); (target/'keep.txt').write_text('previous')
    code, _ = run_export(exporter, monkeypatch, tmp_path, [])
    assert code == 0
    assert json.loads((target/'manifest.json').read_text())['selectedCount'] == 0
    assert any(p.read_text() == 'previous' for p in tmp_path.glob('export.previous-*/keep.txt'))


def test_limit_is_partial(exporter, monkeypatch, tmp_path):
    code, _ = run_export(exporter, monkeypatch, tmp_path, [item(), item('TEST-2')], extra=['--limit','1'])
    assert code == 4
    result=manifests(tmp_path)[0]
    assert result['limited'] is True and result['searchedCount'] == 2
    assert result['selectedCount'] == 1


def test_duplicate_ids_are_not_complete(exporter, monkeypatch, tmp_path):
    code, _ = run_export(exporter, monkeypatch, tmp_path, [item(),item()])
    assert code == 4
    result=manifests(tmp_path)[0]
    assert result['duplicateIds'] == ['TEST-1']


def test_item_failure_is_failed(exporter, monkeypatch, tmp_path):
    code, _ = run_export(exporter, monkeypatch, tmp_path, [item()], fail_item=True)
    assert code == 1
    assert manifests(tmp_path)[0]['status'] == 'FAILED'


def test_query_failure_records_unknown_count_and_keeps_old(exporter, monkeypatch, tmp_path):
    target=tmp_path/'export'; target.mkdir(); (target/'keep.txt').write_text('previous')
    code, _ = run_export(exporter, monkeypatch, tmp_path, [], query_error=True)
    assert code == 1 and (target/'keep.txt').read_text() == 'previous'
    result=manifests(tmp_path)[0]
    assert result['status'] == 'FAILED' and result['searchedCount'] is None


def test_output_commit_failure_restores_previous(exporter, monkeypatch, tmp_path):
    target=tmp_path/'export'; target.mkdir(); (target/'keep.txt').write_text('previous')
    rename=Path.rename
    def fail_commit(self, dest):
        if self.name.startswith('.export.staging-') and Path(dest) == target:
            raise OSError('synthetic commit failure')
        return rename(self,dest)
    monkeypatch.setattr(Path,'rename',fail_commit)
    code, _ = run_export(exporter, monkeypatch, tmp_path, [item()])
    assert code == 1 and (target/'keep.txt').read_text() == 'previous'
    assert manifests(tmp_path)[0]['status'] == 'FAILED'


def test_concurrent_output_lock_rejects_second_run(exporter, tmp_path):
    from export_run import ExportRun
    with ExportRun(tmp_path/'export'):
        with pytest.raises(OSError):
            with ExportRun(tmp_path/'export'):
                pytest.fail('second run entered')
    with ExportRun(tmp_path/'export') as run:
        run.finish({'status':'SUCCESS'})


def test_filenames_cannot_escape_stage(exporter, tmp_path):
    from export_run import child_path
    with pytest.raises(ValueError):
        child_path(tmp_path/'stage', '../outside.txt')


def test_local_check_does_not_construct_network_client(exporter, monkeypatch, tmp_path):
    config=tmp_path/'local.yaml'
    config.write_text(yaml.safe_dump({'polarion':{'host':'https://test.invalid','project_id':'TEST'},'output':{'generate_pdf':False}}))
    monkeypatch.setenv('POLARION_TOKEN','synthetic-only')
    def no_client(*a): pytest.fail('network client should not be created')
    monkeypatch.setattr(exporter,'PolarionClient',no_client)
    assert exporter.run_environment_check(config,offline=True)


@pytest.mark.parametrize('options',[{'html_filename':'manifest.json.tmp'}, {'html_filename':'manifest.json'}, {'pdf_filename':'polarion_query_backup.html'}, {'md_filename':'field_inventory.json'}])
def test_artifact_collision_is_failure(exporter, monkeypatch, tmp_path, options):
    code, target=run_export(exporter,monkeypatch,tmp_path,[item()],output_options=options)
    assert code == 1
    assert not target.exists()


def test_body_image_failure_is_a_recorded_warning(exporter):
    class Client:
        host='https://example.invalid'
        export_warnings=[]
        def request(self,*a,**kw): raise OSError('synthetic')
    client=Client()
    exporter.embed_images_in_rich_html({'type':'text/html','value':'<img src="https://example.invalid/a.png"><img src="unknown.png">'},client,{},True)
    assert len(client.export_warnings) == 2


def test_git_metadata_cannot_be_output(exporter, tmp_path):
    from export_run import ExportRun
    target=tmp_path/'.git'/'objects'
    with pytest.raises(ValueError):
        with ExportRun(target): pytest.fail('metadata accepted')


def test_real_renderer_reports_incomplete_body_image(exporter, monkeypatch, tmp_path):
    value = item()
    value['attributes']['description'] = {'type': 'text/html', 'value': '<p>Synthetic body</p><img src="https://example.invalid/a.png">'}
    code, target = run_export(exporter, monkeypatch, tmp_path, [value], real_renderer=True)
    assert code == 4 and not target.exists()
    result = manifests(tmp_path)[0]
    assert result['successCount'] == 1 and result['warnings']
    assert 'Synthetic body' in next(tmp_path.glob('export.failed-*/*.html')).read_text(encoding='utf-8')


def test_late_failure_retains_completed_phase_counts(exporter, monkeypatch, tmp_path):
    def fail_markdown(*args): raise OSError('synthetic late failure')
    monkeypatch.setattr(exporter, 'make_markdown', fail_markdown)
    code, target = run_export(exporter, monkeypatch, tmp_path, [item()])
    result = manifests(tmp_path)[0]
    assert code == 1 and not target.exists()
    assert result['successCount'] == 1 and result['pdfStatus'] == 'SUCCESS'
