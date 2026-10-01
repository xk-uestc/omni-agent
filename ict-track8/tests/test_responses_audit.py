"""API contract tests use in-memory responses, never real credentials or calls."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from backend.responses_client import StructuredResponses, GenerationError
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.nl2sql.model_contract import ModelPlanError


def payload(**overrides):
    return {"model": "gpt-6-luna", "status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": '{"plan": {}}'}]}],
        "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17,
                  "input_tokens_details": {"cached_tokens": 3},
                  "output_tokens_details": {"reasoning_tokens": 2}}, **overrides}


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
        self.content = json.dumps(data).encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[min(len(self.calls)-1, len(self.responses)-1)]
        if isinstance(response, Exception):
            raise response
        return response


def client(data=None):
    return StructuredResponses('https://example.com/v1', 'private-test-token', model='gpt-6-luna',
                               session=Session([Response(payload() if data is None else data)]))


def generate(c):
    return c.generate('private-instructions', {'private_input': 'should-not-appear'}, {})


@pytest.mark.parametrize('model', ['gpt-6-luna', 'gpt-6-luna-2026-09-01'])
def test_returned_model_is_verified_and_all_usage_is_preserved(model):
    c = client(payload(model=model))
    assert generate(c) == {'plan': {}}
    assert c.audit['response_model'] == model
    assert c.audit['model_verified'] is True
    assert c.audit['cached_input_tokens'] == 3
    assert c.audit['reasoning_tokens'] == 2
    assert c.audit['total_tokens'] == 17
    assert c.session.calls[0][1]['allow_redirects'] is False
    for secret in ('private-test-token', 'private-instructions', 'should-not-appear', 'private_input'):
        assert secret not in json.dumps(c.audit_history)
    copied = c.audit_history
    copied[0]['model'] = 'mutated'
    assert c.audit['model'] == 'gpt-6-luna'


@pytest.mark.parametrize('model', [None, '', 'gpt-6-sol', 'gpt-6-luna-malicious-string', 10])
def test_missing_or_wrong_model_never_becomes_a_success(model):
    c = client(payload(model=model))
    with pytest.raises(GenerationError, match='指定模型'):
        generate(c)
    assert c.audit['status'] == 'failed'
    assert c.audit['model_verified'] is False
    assert c.audit['response_model'] is None
    assert len(c.audit_history) == 1


@pytest.mark.parametrize('data', [
    payload(status='incomplete'), payload(output=None), payload(output={'type': 'message'}),
    payload(output=[{'type': 'message', 'content': None}]),
    payload(output=[{'type': 'message', 'content': [{'type': 'refusal'}]}]),
    payload(output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': 'not-json'}]}]),
    payload(output=[{'type': 'message', 'content': [{'type': 'output_text', 'text': '[]'}]}]),
    [],
])
def test_failed_output_is_recorded_without_raw_payload(data):
    c = client(data)
    with pytest.raises(GenerationError):
        generate(c)
    assert len(c.audit_history) == 1
    assert c.audit['status'] == 'failed'
    assert c.audit['http_status'] == 200


@pytest.mark.parametrize('invalid', [None, True, -1, 1.5, '12'])
def test_invalid_or_absent_usage_is_unknown_not_zero(invalid):
    c = client(payload(usage={'input_tokens': invalid, 'output_tokens': invalid,
                            'total_tokens': invalid, 'input_tokens_details': {'cached_tokens': invalid},
                            'output_tokens_details': {'reasoning_tokens': invalid}}))
    generate(c)
    assert all(c.audit[field] is None for field in (
        'input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens'))


@pytest.mark.parametrize('status', [301, 401, 403])
def test_permanent_http_failure_is_not_retried(status):
    s = Session([Response({'error': 'private-test-token'}, status)])
    p = ResponsesModelPlanProvider('https://example.com/v1', 'private-test-token', model='gpt-6-luna', session=s)
    with pytest.raises(ModelPlanError):
        p('销售额', ())
    assert len(s.calls) == len(p.audit_history) == 1
    assert p.audit['http_status'] == status
    assert 'private-test-token' not in json.dumps(p.audit_history)


@pytest.mark.parametrize('first', [Response({}, 429), Response({}, 503), requests.Timeout('private-test-token')])
def test_retry_audit_preserves_failed_and_successful_attempts(first):
    s = Session([first, Response(payload())])
    p = ResponsesModelPlanProvider('https://example.com/v1', 'private-test-token', model='gpt-6-luna', session=s)
    assert p('销售额', ()) == {}
    assert [a['status'] for a in p.audit_history] == ['failed', 'completed']
    assert [a['call_index'] for a in p.audit_history] == [1, 2]
    assert p.audit_history[-1]['total_tokens'] == 17
    assert 'private-test-token' not in json.dumps(p.audit_history)


def test_audit_history_is_bounded_resettable_and_thread_local():
    c = client()
    for _ in range(66):
        generate(c)
    assert len(c.audit_history) == 64
    assert c.audit_history[0]['call_index'] == 3
    assert c.audit_dropped_count == 2
    def other_thread():
        assert c.audit_history == []
        generate(c)
        return c.audit['call_index']
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(other_thread).result() == 1
    assert c.audit['call_index'] == 66
    c.reset_audit()
    assert c.audit_history == [] and c.audit == {} and c.audit_dropped_count == 0
    generate(c)
    assert c.audit['call_index'] == 1
