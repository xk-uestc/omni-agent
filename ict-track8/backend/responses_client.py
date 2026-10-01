"""Bounded Responses structured-output transport; never logs auth or raw responses."""
from __future__ import annotations

import json
import threading
import re
import time
from collections import deque

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
        return dict(getattr(self._local, 'audit', {}))

    @property
    def audit_history(self):
        return [dict(item) for item in getattr(self._local, 'history', ())]

    @property
    def audit_dropped_count(self):
        return max(0, getattr(self._local, 'calls', 0) - len(self.audit_history))

    def reset_audit(self):
        self._local.audit = {}
        self._local.history = deque(maxlen=64)
        self._local.calls = 0

    def _record(self, audit, started):
        self._local.calls = getattr(self._local, 'calls', 0) + 1
        safe = {**audit, 'call_index': self._local.calls,
                'latency_ms': round((time.perf_counter()-started)*1000, 3)}
        self._local.audit = safe
        if not hasattr(self._local, 'history'):
            self._local.history = deque(maxlen=64)
        self._local.history.append(safe)

    @staticmethod
    def token_count(value):
        return value if type(value) is int and value >= 0 else None

    def generate(self, instructions, context, schema, *, name='answer', max_tokens=4000):
        self._local.audit = {}
        started = time.perf_counter()
        audit = {'provider': 'responses', 'model': self.model, 'reasoning': self.reasoning,
                 'operation': name, 'http_status': None, 'status': 'failed', 'model_verified': False}
        body = {'model': self.model, 'store': False, 'reasoning': {'effort': self.reasoning},
                'max_output_tokens': max_tokens, 'instructions': instructions,
                'input': json.dumps(context, ensure_ascii=False),
                'text': {'format': {'type': 'json_schema', 'name': name, 'strict': True, 'schema': schema}}}
        try:
            response = self.session.post(self.url, json=body,
                headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
                timeout=(10, self.timeout), allow_redirects=False)
            audit['http_status'] = response.status_code
            if not 200 <= response.status_code < 300:
                raise GenerationError('模型服务拒绝请求', status=response.status_code)
            response.raise_for_status()
            if len(response.content) > 500_000:
                raise GenerationError('模型响应超过大小限制')
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            status = getattr(exc,'status',None) or getattr(getattr(exc, 'response', None), 'status_code', None)
            audit['http_status'] = status or audit['http_status']
            self._record(audit, started)
            raise GenerationError('模型服务未返回可用结构化响应', status=status) from exc
        try:
            if not isinstance(payload, dict):
                raise GenerationError('模型响应必须为对象')
            actual_model = payload.get('model')
            same_family = isinstance(actual_model,str) and (actual_model == self.model or
                re.fullmatch(re.escape(self.model)+r'-\d{4}-\d{2}-\d{2}', actual_model) is not None)
            # Never publish arbitrary provider strings into logs. A mismatched
            # model cannot satisfy the user's one-model-only requirement.
            audit['response_model'] = actual_model if same_family else None
            audit['model_verified'] = same_family
            usage = payload.get('usage') if isinstance(payload.get('usage'),dict) else {}
            for field in ('input_tokens','output_tokens','total_tokens'):
                audit[field] = self.token_count(usage.get(field))
            for target, parent, field in [('cached_input_tokens','input_tokens_details','cached_tokens'),
                                          ('reasoning_tokens','output_tokens_details','reasoning_tokens')]:
                details = usage.get(parent)
                audit[target] = self.token_count(details.get(field)) if isinstance(details,dict) else None
            if not same_family:
                raise GenerationError('服务端未确认指定模型')
            if payload.get('status') != 'completed':
                raise GenerationError('模型响应未完成')
            texts = []
            outputs = payload.get('output')
            if not isinstance(outputs, list):
                raise GenerationError('模型输出结构无效')
            for output in outputs:
                if not isinstance(output,dict) or output.get('type') != 'message':
                    continue
                content = output.get('content')
                if not isinstance(content, list):
                    raise GenerationError('模型消息结构无效')
                for part in content:
                    if not isinstance(part,dict):
                        raise GenerationError('模型消息结构无效')
                    if part.get('type') == 'refusal':
                        raise GenerationError('模型拒绝处理请求')
                    if part.get('type') == 'output_text':
                        texts.append(part.get('text', ''))
            result = json.loads(''.join(texts))
            if not isinstance(result, dict):
                raise GenerationError('模型输出必须为对象')
        except (ValueError, TypeError) as exc:
            self._record(audit, started)
            if isinstance(exc,GenerationError):
                raise
            raise GenerationError('模型输出不是合法 JSON') from exc
        audit['status'] = 'completed'
        self._record(audit, started)
        return result
