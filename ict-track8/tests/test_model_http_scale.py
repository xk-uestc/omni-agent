"""Performance harness contracts only; no API or performance claims."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def scale_tool(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'tools'))
    spec = importlib.util.spec_from_file_location('http_scale_test_tool', root / 'tools/evaluate_model_http_scale.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_data():
    def audit(operation):
        return {'provider': 'responses', 'operation': operation, 'model': 'gpt-6-luna',
                'reasoning': 'medium', 'model_verified': True, 'status': 'completed',
                'http_status': 200, 'latency_ms': 30, 'total_tokens': 17}
    return {'status': 'ok', 'route': 'sql', 'planner_source': 'model_validated',
            'trace': [{'attempts': [{'api_audit': audit('omni_plan')}]}],
            'result': {'status': 'ok', 'rows': [{'销售额': 90}], 'plan': {
                'planner_source': 'model_validated', 'planner_audit': {'provider': audit('nl2sql_plan')}}}}


def test_preview_never_loads_credentials_or_runs_process(scale_tool, monkeypatch, capsys):
    import model_runtime
    monkeypatch.setattr(model_runtime, 'enable_local_model', lambda *_: pytest.fail('no credential read'))
    monkeypatch.setattr(scale_tool.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('no server'))
    monkeypatch.setattr('sys.argv', ['evaluate_model_http_scale.py', '--preview'])
    assert scale_tool.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['row_scales'] == [1000, 10000, 100000]
    assert plan['warm_concurrency'] == [1, 2, 4]
    assert plan['total_requests'] == 24
    assert plan['document_page_scale_measured'] is False


def test_fixed_oracle_uses_data_not_agent_sql(scale_tool, tmp_path):
    path = tmp_path / 'sales.sqlite'
    expected = scale_tool.make_database(path, 1000)
    independently = sum(100 + i % 997 for i in range(1000) if i % 3 == 0 and i % 2 == 1)
    assert expected == independently
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM sales_orders').fetchone()[0] == 1000
        assert connection.execute('SELECT COUNT(DISTINCT region) FROM sales_orders').fetchone()[0] == 3


def test_metrics_use_wall_not_api_sum_and_keep_failures(scale_tool):
    records = [{'wall_ms': 80, 'pass': True, 'http_status': 200, 'api_audits': [{'latency_ms': 600}]},
               {'wall_ms': 100, 'pass': False, 'http_status': 500, 'api_audits': [{'latency_ms': 700}]}]
    result = scale_tool.metrics(records, 120)
    assert result['actual_qps'] == pytest.approx(16.666667)
    assert result['correct_qps'] == pytest.approx(8.333333)
    assert result['correct_rate'] == .5 and result['http_errors'] == 1
    assert result['p50_ms'] == 90 and result['p95_ms'] == 99


def test_real_value_does_not_hide_rules_fallback_or_wrong_model(scale_tool):
    data = sample_data()
    checks, audits = scale_tool.evaluate_response(data, 90)
    assert all(checks.values()) and len(audits) == 2
    data['result']['plan']['planner_source'] = 'rules_fallback'
    checks, _ = scale_tool.evaluate_response(data, 90)
    assert checks['oracle_value'] and not checks['real_sql']
    data['trace'][0]['attempts'][0]['api_audit']['model'] = 'other-model'
    checks, _ = scale_tool.evaluate_response(data, 90)
    assert not checks['allowed_completed_model']


def test_visible_audits_exclude_secrets_dedupe_and_preserve_auth_failure(scale_tool):
    data = sample_data()
    audit = data['trace'][0]['attempts'][0]['api_audit']
    audit.update({'token': 'DO_NOT_COPY', 'headers': {'Authorization': 'DO_NOT_COPY'}, 'http_status': 401})
    data['duplicate'] = audit
    audits = scale_tool.safe_audits(data)
    assert len(audits) == 2
    assert 'DO_NOT_COPY' not in json.dumps(audits)
    assert scale_tool.auth_failed([{'api_audits': audits}])


def test_unknown_usage_is_not_reported_as_zero_or_complete(scale_tool):
    records = [{'wall_ms': 80, 'pass': True, 'http_status': 200, 'api_audits': [
        {'input_tokens': 7, 'total_tokens': 10}, {'status': 'failed'}]}]
    result = scale_tool.metrics(records, 100)
    assert result['visible_api_calls'] == 2
    assert result['visible_usage_lower_bound']['input_tokens'] == {
        'reported_sum': 7, 'unknown_visible_calls': 1, 'complete_transport_history': False}
    assert result['visible_usage_lower_bound']['output_tokens']['unknown_visible_calls'] == 2


def test_output_never_overwrites_or_targets_config(scale_tool, monkeypatch, tmp_path):
    monkeypatch.setattr(scale_tool, 'ROOT', tmp_path)
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/prior.json').write_text('{}')
    with pytest.raises(ValueError, match='覆盖'):
        scale_tool.report_path('docs/prior.json')
    with pytest.raises(ValueError, match='docs'):
        scale_tool.report_path('runtime/model_config.json')
    assert scale_tool.report_path('docs/new.json') == (tmp_path / 'docs/new.json').resolve()


def test_model_environment_restores_parent_and_rejects_nonmedium(scale_tool, monkeypatch):
    import model_runtime
    monkeypatch.setenv('ICT8_OPENAI_API_KEY', 'PARENT_SENTINEL')
    def fake_enable(_):
        scale_tool.os.environ['ICT8_OPENAI_API_KEY'] = 'CHILD_SENTINEL'
        scale_tool.os.environ['ICT8_NEW_ENV'] = 'temporary'
        return {'reasoning': 'medium'}
    monkeypatch.setattr(model_runtime, 'enable_local_model', fake_enable)
    child = scale_tool.model_environment()
    assert child['ICT8_OPENAI_API_KEY'] == 'CHILD_SENTINEL'
    assert scale_tool.os.environ['ICT8_OPENAI_API_KEY'] == 'PARENT_SENTINEL'
    assert 'ICT8_NEW_ENV' not in scale_tool.os.environ
    monkeypatch.setattr(model_runtime, 'enable_local_model', lambda _: {'reasoning': 'high'})
    with pytest.raises(ValueError, match='medium'):
        scale_tool.model_environment()


def test_timeout_records_exception_class_not_sensitive_text(scale_tool, monkeypatch):
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def post(self, *_args, **_kwargs):
            raise scale_tool.requests.Timeout('SECRET HEADER URL')
    monkeypatch.setattr(scale_tool.requests, 'Session', Client)
    record = scale_tool.request_one('http://127.0.0.1:1', 90, 180)
    assert not record['pass'] and record['error_type'] == 'Timeout'
    assert 'SECRET' not in json.dumps(record)
