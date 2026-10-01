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
        os.environ['ICT8_GENERATION_PROVIDER'] = 'responses'
        os.environ['ICT8_PLAN_PROVIDER'] = 'responses'
        return {'model': model, 'reasoning': config.get('reasoning', 'medium'), 'credential_source': 'project_local_excluded_file'}


def local_model_headers():
    """Return only the explicitly configured gateway header, never bearer auth."""
    return json.loads(os.environ.get('ICT8_OPENAI_HEADERS', '{}'))
