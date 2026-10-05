"""Bounded connection recovery never relaxes model or answer contracts."""
import json
import pytest
import requests

from backend.responses_client import StructuredResponses, GenerationError
from tests.test_responses_audit import Session, Response, payload


@pytest.mark.parametrize('failure', [requests.Timeout('private-data'), requests.ConnectionError('private-data')])
def test_dropped_transport_recovers_once_with_identical_request_and_visible_unknown_usage(failure):
    session = Session([failure, Response(payload())])
    client = StructuredResponses('https://example.com/v1', 'test-only', model='gpt-6-luna', session=session)
    assert client.generate('instructions', {'question': 'x'}, {}) == {'plan': {}}
    assert len(session.calls) == 2 and session.calls[0] == session.calls[1]
    audit = client.audit
    assert audit['status'] == 'completed' and audit['transport_attempts'] == 2
    assert len(audit['transport_failures']) == 1 and audit['transport_usage_unknown'] is True
    assert 'private-data' not in json.dumps(client.audit_history)


def test_two_network_failures_stop_without_unbounded_requests():
    session = Session([requests.Timeout('private-data')])
    client = StructuredResponses('https://example.com/v1', 'test-only', model='gpt-6-luna', session=session)
    with pytest.raises(GenerationError):
        client.generate('instructions', {}, {})
    assert len(session.calls) == client.audit['transport_attempts'] == 2
    assert client.audit['status'] == 'failed'


@pytest.mark.parametrize('response', [
    Response({}, 401), Response({}, 403), Response({}, 429), Response({}, 503),
    requests.exceptions.SSLError('private-data'),
    Response(payload(model='gpt-6-sol')), Response(payload(status='incomplete')),
    Response(payload(output=[])),
])
def test_http_tls_and_model_output_failures_never_retry(response):
    session = Session([response, Response(payload())])
    client = StructuredResponses('https://example.com/v1', 'test-only', model='gpt-6-luna', session=session)
    with pytest.raises(GenerationError):
        client.generate('instructions', {}, {})
    assert len(session.calls) == 1 and client.audit['status'] == 'failed'
