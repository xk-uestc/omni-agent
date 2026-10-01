"""Project credentials must never silently fall back to another provider."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'tools/model_runtime.py'
    spec = importlib.util.spec_from_file_location('isolated_model_runtime', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'tools/model_runtime.py'))
    (tmp_path / 'runtime').mkdir()
    return module, tmp_path / 'runtime/model_config.json'


def test_missing_config_does_not_read_codex_credentials(runtime):
    module, _ = runtime
    with pytest.raises(ValueError, match='不会读取'):
        module.enable_local_model()


@pytest.mark.parametrize('model,address', [('gpt-5.6-terra', 'https://spacetimeai.cc/v1'),
                                          ('gpt-6-luna', 'https://another.example/v1'),
                                          ('gpt-6-luna', 'https://spacetimeai.cc.other.example/v1')])
def test_unauthorized_model_and_destination_rejected(runtime, model, address):
    module, path = runtime
    path.write_text(json.dumps({'model': model, 'base_url': address, 'api_key': 'test-only'}), encoding='utf-8')
    with pytest.raises(ValueError):
        module.enable_local_model()


def test_user_host_and_model_loaded_without_key_in_return(runtime, monkeypatch):
    module, path = runtime
    path.write_text(json.dumps({'model': 'gpt-6-luna', 'base_url': 'https://spacetimeai.cc', 'api_key': 'test-only'}), encoding='utf-8')
    for name in ('ICT8_OPENAI_BASE_URL', 'ICT8_OPENAI_API_KEY', 'ICT8_OPENAI_MODEL', 'ICT8_OPENAI_REASONING', 'ICT8_GENERATION_PROVIDER', 'ICT8_PLAN_PROVIDER'):
        monkeypatch.setenv(name, '')
    result = module.enable_local_model()
    assert result['model'] == 'gpt-6-luna'
    assert 'test-only' not in json.dumps(result)
    assert module.os.environ['ICT8_OPENAI_BASE_URL'] == 'https://spacetimeai.cc/v1'


def test_local_header_loaded_and_stale_header_cleared(runtime, monkeypatch):
    module, path = runtime
    monkeypatch.setenv('ICT8_OPENAI_HEADERS', '{}')
    config = {'model': 'gpt-6-luna', 'base_url': 'https://spacetimeai.cc/v1',
              'api_key': 'test-only', 'http_headers': {'x-openai-actor-authorization': 'actor-test'}}
    path.write_text(json.dumps(config), encoding='utf-8')
    result = module.enable_local_model()
    assert module.local_model_headers() == config['http_headers']
    assert 'actor-test' not in json.dumps(result)
    del config['http_headers']
    path.write_text(json.dumps(config), encoding='utf-8')
    module.enable_local_model()
    assert module.local_model_headers() == {}
