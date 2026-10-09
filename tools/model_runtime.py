"""Load only the user-authorized, excluded project model configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def enable_local_model(model='gpt-6-luna'):
    project_config = Path(__file__).resolve().parents[1] / 'runtime/model_config.json'
    if not project_config.exists():
        raise ValueError('请配置 runtime/model_config.json；不会读取其他项目或本机 Codex 凭据')
    if project_config.exists():
        config = json.loads(project_config.read_text(encoding='utf-8'))
        if config.get('model') != 'gpt-6-luna' or model != 'gpt-6-luna':
            raise ValueError('本项目仅允许用户指定的 gpt-6-luna')
        address = urlsplit(config.get('base_url', ''))
        if (address.scheme != 'https' or address.hostname != 'spacetimeai.cc'
                or address.port not in (None, 443) or address.path.rstrip('/') not in ('', '/v1')
                or address.username or address.password or address.query or address.fragment
                or not isinstance(config.get('api_key'), str) or not config['api_key'].strip()):
            raise ValueError('项目本地模型配置不完整')
        os.environ['ICT8_OPENAI_BASE_URL'] = 'https://spacetimeai.cc/v1'
        os.environ['ICT8_OPENAI_API_KEY'] = config['api_key']
        os.environ['ICT8_OPENAI_MODEL'] = model
        os.environ['ICT8_OPENAI_REASONING'] = config.get('reasoning', 'medium')
        headers = config.get('http_headers', {})
        if (not isinstance(headers, dict) or any(
                name != 'x-openai-actor-authorization' or not isinstance(value, str)
                or not value or len(value) > 256 or '\r' in value or '\n' in value
                for name, value in headers.items())):
            raise ValueError('项目模型请求头配置无效')
        os.environ['ICT8_OPENAI_HEADERS'] = json.dumps(headers)
        fallback_file = project_config.with_name('fallback_model_config.json')
        fallback_config = json.loads(fallback_file.read_text(encoding='utf-8')) if fallback_file.exists() else []
        allowed = {
            'conpera': ('code.conpera.ai', 'gpt-5.6-sol'),
            'yescode': ('ai.yescode.cloud', 'gpt-5.5'),
            'spacetime': ('spacetimeai.cc', 'gpt-5.5'),
        }
        if not isinstance(fallback_config, list) or len(fallback_config) > 3:
            raise ValueError('备用模型配置必须包含不超过3个服务')
        for item in fallback_config:
            if not isinstance(item, dict) or item.get('provider') not in allowed:
                raise ValueError('备用模型来源无效')
            host, expected_model = allowed[item['provider']]
            endpoint = urlsplit(item.get('base_url', ''))
            if (endpoint.scheme != 'https' or endpoint.hostname != host or endpoint.port not in (None, 443)
                    or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                    or item.get('model') != expected_model
                    or not isinstance(item.get('api_key'), str) or not item['api_key'].strip()
                    or item.get('reasoning', 'medium') not in {'low', 'medium', 'high', 'xhigh'}):
                raise ValueError('备用模型配置校验失败')
        os.environ['ICT8_OPENAI_FALLBACKS'] = json.dumps(fallback_config)
        os.environ.setdefault('ICT8_OPENAI_HEDGE_DELAY', '2.5')
        os.environ['ICT8_GENERATION_PROVIDER'] = 'responses'
        os.environ['ICT8_PLAN_PROVIDER'] = 'responses'
        return {'model': model, 'reasoning': config.get('reasoning', 'medium'), 'credential_source': 'project_local_excluded_file'}


def local_model_headers():
    """Return only the explicitly configured gateway header, never bearer auth."""
    return json.loads(os.environ.get('ICT8_OPENAI_HEADERS', '{}'))
