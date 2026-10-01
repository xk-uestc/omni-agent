"""Bounded Responses structured-output transport; never logs auth or raw responses."""
from __future__ import annotations

import json
import threading

import requests


class GenerationError(ValueError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


class StructuredResponses:
    def __init__(self, base_url, token, *, model, reasoning='medium', timeout=60, session=None):
        if not base_url.startswith(('https://', 'http://127.0.0.1')) or not token or not model:
            raise ValueError('模型服务必须明确配置 URL、密钥与模型')
        self.url, self.token, self.model = base_url.rstrip('/') + '/responses', token, model
        self.reasoning, self.timeout = reasoning, timeout
        self.session = session or requests.Session()
        self._local = threading.local()

    @property
    def audit(self):
        return getattr(self._local, 'audit', {})

    def generate(self, instructions, context, schema, *, name='answer', max_tokens=4000):
        self._local.audit = {}
        body = {'model': self.model, 'store': False, 'reasoning': {'effort': self.reasoning},
                'max_output_tokens': max_tokens, 'instructions': instructions,
                'input': json.dumps(context, ensure_ascii=False),
                'text': {'format': {'type': 'json_schema', 'name': name, 'strict': True, 'schema': schema}}}
        try:
            response = self.session.post(self.url, json=body,
                headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
                timeout=(10, self.timeout))
            response.raise_for_status()
            if len(response.content) > 500_000:
                raise GenerationError('模型响应超过大小限制')
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            self._local.audit = {'provider': 'responses', 'model': self.model, 'http_status': status, 'status': 'failed'}
            raise GenerationError('模型服务未返回可用结构化响应', status=status) from exc
        if not isinstance(payload, dict) or payload.get('status') != 'completed':
            raise GenerationError('模型响应未完成')
        texts = []
        for output in payload.get('output', []):
            if output.get('type') != 'message':
                continue
            for part in output.get('content', []):
                if part.get('type') == 'refusal':
                    raise GenerationError('模型拒绝处理请求')
                if part.get('type') == 'output_text':
                    texts.append(part.get('text', ''))
        try:
            result = json.loads(''.join(texts))
        except (ValueError, TypeError) as exc:
            raise GenerationError('模型输出不是合法 JSON') from exc
        if not isinstance(result, dict):
            raise GenerationError('模型输出必须为对象')
        usage = payload.get('usage') or {}
        self._local.audit = {'provider': 'responses', 'model': self.model, 'reasoning': self.reasoning,
                             'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens')}
        return result
