"""Bounded Responses structured-output transport; never logs auth or raw responses."""
from __future__ import annotations

import json
import base64
import hashlib
from io import BytesIO
import threading
import re
import time
from collections import deque
from copy import deepcopy

import requests
from urllib.parse import urlsplit


class GenerationError(ValueError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


class StructuredResponses:
    # This adapter supports raw-source selection and its independent-review
    # schemas. Other generator adapters must explicitly declare this capability.
    supports_source_first_projection = True
    supports_native_answer_dossier = True

    def __init__(self, base_url, token, *, model, reasoning='medium', timeout=60, session=None, http_headers=None,
                 transport_attempts=2):
        if not base_url.startswith(('https://', 'http://127.0.0.1')) or not token or not model:
            raise ValueError('模型服务必须明确配置 URL、密钥与模型')
        self.url, self.token, self.model = base_url.rstrip('/') + '/responses', token, model
        self.reasoning, self.timeout = reasoning, timeout
        self.session = session or requests.Session()
        if type(transport_attempts) is not int or transport_attempts not in (1, 2):
            raise ValueError('传输尝试次数必须为1或2')
        self.transport_attempt_limit = transport_attempts
        headers = http_headers or {}
        if (not isinstance(headers, dict) or any(
                name != 'x-openai-actor-authorization' or not isinstance(value, str)
                or not value or len(value) > 256 or '\r' in value or '\n' in value
                for name, value in headers.items())):
            raise ValueError('模型请求头配置无效')
        destination = urlsplit(base_url)
        if headers and (destination.scheme != 'https' or destination.hostname != 'spacetimeai.cc'
                        or destination.port not in (None, 443)):
            raise ValueError('自定义模型请求头仅允许指定网关')
        self.http_headers = dict(headers)
        self._local = threading.local()

    @property
    def audit(self):
        return deepcopy(getattr(self._local, 'audit', {}))

    @property
    def audit_history(self):
        return [deepcopy(item) for item in getattr(self._local, 'history', ())]

    @property
    def audit_dropped_count(self):
        return max(0, getattr(self._local, 'calls', 0) - len(self.audit_history))

    @property
    def audit_generation(self):
        return getattr(self._local, 'audit_generation', 0)

    def reset_audit(self):
        self._local.audit_generation = self.audit_generation + 1
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

    @staticmethod
    def _image_content(image_attachments):
        from .visual_evidence import VisualAsset
        from PIL import Image
        if not isinstance(image_attachments, (list, tuple)) or not 1 <= len(image_attachments) <= 2:
            raise GenerationError('视觉输入必须包含1至2份本地证据资产')
        content, descriptors, total = [], [], 0
        for asset in image_attachments:
            if not isinstance(asset, VisualAsset) or not isinstance(asset.png_bytes, bytes):
                raise GenerationError('视觉输入只能使用服务器生成的PNG资产')
            raw, manifest = asset.png_bytes, asset.manifest
            total += len(raw)
            if not raw or len(raw) > 12 * 1024 * 1024 or total > 16 * 1024 * 1024:
                raise GenerationError('视觉输入超过字节预算')
            if not isinstance(manifest, dict) or manifest.get('schema_version') != 'pdf-visual-asset-v1':
                raise GenerationError('视觉证据契约无效')
            for field in ('evidence_id', 'source_sha256', 'render_sha256'):
                if not isinstance(manifest.get(field), str) or not re.fullmatch('[a-f0-9]{64}', manifest[field]):
                    raise GenerationError('视觉证据标识无效')
            if (hashlib.sha256(raw).hexdigest() != manifest['render_sha256']
                    or manifest.get('png_byte_count') != len(raw)
                    or type(manifest.get('page_no')) is not int or manifest['page_no'] < 1):
                raise GenerationError('视觉证据摘要或页码不匹配')
            try:
                with Image.open(BytesIO(raw)) as picture:
                    width, height = picture.size
                    if (picture.format != 'PNG' or width * height > 6_000_000
                            or manifest.get('size_px') != [width, height]):
                        raise GenerationError('视觉输入尺寸或格式超出契约')
                    picture.verify()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise GenerationError('视觉输入PNG完整性无效') from exc
            descriptor = {key: manifest[key] for key in ('evidence_id', 'source_sha256', 'render_sha256', 'page_no', 'size_px')}
            descriptors.append(descriptor)
            # Native text is deliberately not sent here. The image must be
            # independently visible, including for image-only capability probes.
            content.extend([{'type': 'input_text', 'text': json.dumps({'image_evidence': descriptor})},
                            {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii'), 'detail': 'high'}])
        return content, descriptors

    def generate(self, instructions, context, schema, *, name='answer', max_tokens=4000, image_attachments=None):
        self._local.audit = {}
        started = time.perf_counter()
        audit = {'provider': 'responses', 'model': self.model, 'reasoning': self.reasoning,
                 'operation': name, 'http_status': None, 'status': 'failed', 'model_verified': False}
        body = {'model': self.model, 'store': False, 'reasoning': {'effort': self.reasoning},
                'max_output_tokens': max_tokens, 'instructions': instructions,
                'input': json.dumps(context, ensure_ascii=False),
                'text': {'format': {'type': 'json_schema', 'name': name, 'strict': True, 'schema': schema}}}
        if image_attachments is not None:
            try:
                image_content, descriptors = self._image_content(image_attachments)
            except GenerationError:
                audit['input_validation'] = 'visual_asset_rejected'
                self._record(audit, started)
                raise
            body['input'] = [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': json.dumps(context, ensure_ascii=False)}, *image_content]}]
            audit['image_evidence'] = descriptors
            audit['image_count'] = len(descriptors)
        try:
            # One transport-only retry for a dropped connection or timeout.
            # No HTTP rejection, TLS error, invalid output or semantic failure
            # can trigger it. Both attempts carry the identical request.
            audit['transport_attempts'] = 0
            audit['transport_failures'] = []
            for attempt in range(self.transport_attempt_limit):
                audit['transport_attempts'] += 1
                try:
                    response = self.session.post(self.url, json=body,
                        headers={**self.http_headers, 'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
                        timeout=(10, self.timeout), allow_redirects=False)
                    break
                except (requests.ConnectionError, requests.Timeout) as exc:
                    if isinstance(exc, requests.exceptions.SSLError):
                        raise
                    audit['transport_failures'].append('timeout' if isinstance(exc, requests.Timeout) else 'connection_lost')
                    # A dropped response may already have consumed provider
                    # tokens. Its usage is unknown, never silently zero.
                    audit['transport_usage_unknown'] = True
                    if attempt + 1 == self.transport_attempt_limit:
                        raise
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
