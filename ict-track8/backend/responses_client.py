"""Bounded Responses structured-output transport; never logs auth or raw responses."""
from __future__ import annotations

import json
import base64
import hashlib
import os
from io import BytesIO
import threading
import re
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy

import requests
from urllib.parse import urlsplit


class GenerationError(ValueError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def verified_response_audit(audit):
    """Validate the model identity recorded by this configured client pool."""
    model = audit.get('model') if isinstance(audit, dict) else None
    response_model = audit.get('response_model') if isinstance(audit, dict) else None
    same_family = (isinstance(model, str) and isinstance(response_model, str)
                   and (response_model == model or re.fullmatch(re.escape(model) + r'-\d{4}-\d{2}-\d{2}',
                                                                 response_model) is not None))
    return (isinstance(audit, dict) and audit.get('status') == 'completed'
            and audit.get('model_verified') is True and same_family
            and type(audit.get('http_status')) is int and 200 <= audit['http_status'] < 300)


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


class StructuredResponses:
    # This adapter supports raw-source selection and its independent-review
    # schemas. Other generator adapters must explicitly declare this capability.
    supports_source_first_projection = True
    supports_native_answer_dossier = True
    supports_verified_sql_routing = True

    def __init__(self, base_url, token, *, model, reasoning='medium', timeout=60, session=None, http_headers=None,
                 transport_attempts=2, service_tier=None):
        if not base_url.startswith(('https://', 'http://127.0.0.1')) or not token or not model:
            raise ValueError('模型服务必须明确配置 URL、密钥与模型')
        self.url, self.token, self.model = base_url.rstrip('/') + '/responses', token, model
        self.reasoning, self.timeout = reasoning, timeout
        if service_tier not in {None, 'auto', 'default', 'fast', 'flex', 'priority'}:
            raise ValueError('模型服务等级无效')
        self.service_tier = service_tier
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
        from .multimodal_input import ChatImageInput, MAX_IMAGE_PIXELS
        from PIL import Image
        if not isinstance(image_attachments, (list, tuple)) or not 1 <= len(image_attachments) <= 3:
            raise GenerationError('视觉输入必须包含1至3张有效图片')
        content, descriptors, total = [], [], 0
        visual_count = 0
        for asset in image_attachments:
            if isinstance(asset, ChatImageInput):
                raw, mime_type = asset.data, asset.mime_type
                if (not isinstance(raw, bytes) or not raw
                        or hashlib.sha256(raw).hexdigest() != asset.sha256):
                    raise GenerationError('用户图片完整性校验失败')
                try:
                    with Image.open(BytesIO(raw)) as picture:
                        width, height, image_format = picture.width, picture.height, picture.format
                        if (image_format != {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}.get(mime_type)
                                or width * height > MAX_IMAGE_PIXELS
                                or [width, height] != asset.size_px):
                            raise GenerationError('用户图片格式或尺寸无效')
                        picture.verify()
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    raise GenerationError('用户图片完整性校验失败') from exc
                total += len(raw)
                if len(raw) > 8 * 1024 * 1024 or total > 18 * 1024 * 1024:
                    raise GenerationError('用户图片超过模型输入预算')
                descriptor = {'source': 'user_upload', 'sha256': asset.sha256,
                              'mime_type': mime_type, 'size_px': asset.size_px}
                descriptors.append(descriptor)
                content.extend([{'type': 'input_text', 'text': json.dumps({'user_image': descriptor})},
                                {'type': 'input_image', 'image_url': 'data:' + mime_type + ';base64:' +
                                 base64.b64encode(raw).decode('ascii'), 'detail': 'high'}])
                continue
            if not isinstance(asset, VisualAsset) or not isinstance(asset.png_bytes, bytes):
                raise GenerationError('视觉输入类型无效')
            visual_count += 1
            if visual_count > 2:
                raise GenerationError('PDF视觉证据最多包含2页')
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
        if self.service_tier is not None:
            body['service_tier'] = self.service_tier
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


class HedgedResponses:
    """Return the first usable structured response from staggered providers."""

    _executor = ThreadPoolExecutor(max_workers=24, thread_name_prefix='responses-hedge')
    _stats_lock = threading.Lock()
    _latencies = {}

    def __init__(self, providers, *, hedge_delay=2.5, max_parallel=2):
        if (not providers or len(providers) > 4 or not 0.25 <= hedge_delay <= 10
                or type(max_parallel) is not int or not 1 <= max_parallel <= 2):
            raise ValueError('模型服务池配置无效')
        self.providers = list(providers)
        self.model = self.providers[0].model
        self.reasoning = self.providers[0].reasoning
        self.hedge_delay = float(hedge_delay)
        self.max_parallel = max_parallel
        self.supports_source_first_projection = all(p.supports_source_first_projection for p in providers)
        self.supports_native_answer_dossier = all(p.supports_native_answer_dossier for p in providers)
        self.supports_verified_sql_routing = all(p.supports_verified_sql_routing for p in providers)
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

    @classmethod
    def _ordered(cls, providers):
        with cls._stats_lock:
            return sorted(providers, key=lambda p: cls._latencies.get((p.url, p.model), float('inf')))

    @classmethod
    def _observe(cls, provider, elapsed):
        key = (provider.url, provider.model)
        with cls._stats_lock:
            previous = cls._latencies.get(key)
            cls._latencies[key] = elapsed if previous is None else previous * 0.75 + elapsed * 0.25

    @staticmethod
    def _call(provider, instructions, context, schema, kwargs):
        started = time.perf_counter()
        try:
            value = provider.generate(instructions, context, schema, **kwargs)
            return {'ok': True, 'value': value, 'audit': provider.audit,
                    'completed_at': time.perf_counter(), 'elapsed': time.perf_counter() - started}
        except Exception as exc:
            return {'ok': False, 'error': exc, 'audit': provider.audit,
                    'completed_at': time.perf_counter(), 'elapsed': time.perf_counter() - started}

    def _record(self, audit, started):
        self._local.calls = getattr(self._local, 'calls', 0) + 1
        saved = {**audit, 'call_index': self._local.calls,
                 'latency_ms': round((time.perf_counter() - started) * 1000, 3)}
        self._local.audit = saved
        if not hasattr(self._local, 'history'):
            self._local.history = deque(maxlen=64)
        self._local.history.append(saved)

    def generate(self, instructions, context, schema, *, name='answer', max_tokens=4000, image_attachments=None):
        self._local.audit = {}
        started = time.perf_counter()
        providers = self._ordered(self.providers)
        kwargs = {'name': name, 'max_tokens': max_tokens}
        if image_attachments is not None:
            kwargs['image_attachments'] = image_attachments
        attempts, pending = [], {}
        next_provider = 0
        next_launch_at = started
        winner = None
        while winner is None and (pending or next_provider < len(providers)):
            now = time.perf_counter()
            if (next_provider < len(providers) and len(pending) < self.max_parallel
                    and (not pending or now >= next_launch_at)):
                provider = providers[next_provider]
                future = self._executor.submit(self._call, provider, instructions, context, schema, kwargs)
                pending[future] = provider
                attempts.append({'provider': urlsplit(provider.url).hostname, 'model': provider.model,
                                 'status': 'running'})
                next_provider += 1
                next_launch_at = time.perf_counter() + self.hedge_delay
                continue
            can_launch = next_provider < len(providers) and len(pending) < self.max_parallel
            timeout = max(0.0, next_launch_at - time.perf_counter()) if can_launch else None
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                continue
            completed = sorted(((future.result(), future, pending[future]) for future in done),
                               key=lambda row: row[0]['completed_at'])
            for outcome, future, provider in completed:
                pending.pop(future, None)
                entry = next(item for item in attempts if item['status'] == 'running'
                             and item['provider'] == urlsplit(provider.url).hostname
                             and item['model'] == provider.model)
                entry.update(status='completed' if outcome['ok'] else 'failed',
                             latency_ms=round(outcome['elapsed'] * 1000, 3),
                             http_status=outcome['audit'].get('http_status'))
                if outcome['ok']:
                    self._observe(provider, outcome['elapsed'])
                    if winner is None:
                        winner = (outcome, provider)
                elif next_provider < len(providers):
                    next_launch_at = min(next_launch_at, time.perf_counter())
            if winner is not None:
                for future, provider in list(pending.items()):
                    cancelled = future.cancel()
                    attempts.append({'provider': urlsplit(provider.url).hostname, 'model': provider.model,
                                     'status': 'cancelled_before_start' if cancelled else 'in_flight_after_winner'})
                    if not cancelled:
                        future.add_done_callback(lambda completed, selected=provider:
                            self._observe(selected, completed.result()['elapsed']))
                break
        if winner is None:
            audit = {'provider': 'responses_pool', 'model': self.model, 'reasoning': self.reasoning,
                     'operation': name, 'http_status': next((x.get('http_status') for x in reversed(attempts)
                         if x.get('http_status') is not None), None), 'status': 'failed', 'model_verified': False,
                     'provider_attempts': attempts}
            self._record(audit, started)
            raise GenerationError('所有已配置模型服务均未返回可用结构化响应', status=audit['http_status'])
        outcome, provider = winner
        audit = {**outcome['audit'], 'provider_attempts': attempts,
                 'winning_provider': urlsplit(provider.url).hostname,
                 'hedge_delay_ms': round(self.hedge_delay * 1000, 1)}
        self._record(audit, started)
        return outcome['value']


def configured_responses_client(*, timeout=60, transport_attempts=2):
    """Build a provider pool from process-local configuration without logging credentials."""
    fallbacks = json.loads(os.environ.get('ICT8_OPENAI_FALLBACKS', '[]'))
    if not isinstance(fallbacks, list):
        raise ValueError('备用模型配置必须为列表')
    pool_timeout = min(timeout, 30) if fallbacks else timeout
    attempt_limit = 1 if fallbacks else transport_attempts
    providers = [StructuredResponses(
        os.environ.get('ICT8_OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        os.environ.get('ICT8_OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', ''),
        model=os.environ.get('ICT8_OPENAI_MODEL', ''),
        reasoning=os.environ.get('ICT8_OPENAI_REASONING', 'medium'), timeout=pool_timeout,
        http_headers=json.loads(os.environ.get('ICT8_OPENAI_HEADERS', '{}')),
        transport_attempts=attempt_limit)]
    for item in fallbacks:
        if not isinstance(item, dict) or set(item) - {
                'base_url', 'api_key', 'model', 'reasoning', 'provider', 'service_tier'}:
            raise ValueError('备用模型配置字段无效')
        if item.get('provider') not in {'conpera', 'yescode', 'spacetime'}:
            raise ValueError('备用模型来源未获允许')
        providers.append(StructuredResponses(item.get('base_url', ''), item.get('api_key', ''),
            model=item.get('model', ''), reasoning=item.get('reasoning', 'medium'), timeout=pool_timeout,
            transport_attempts=attempt_limit, service_tier=item.get('service_tier')))
    delay = float(os.environ.get('ICT8_OPENAI_HEDGE_DELAY', '2.5'))
    return HedgedResponses(providers, hedge_delay=delay) if len(providers) > 1 else providers[0]
