"""Protocol repair fixtures cannot be reported as autonomous-model success."""
from copy import deepcopy
import json

import pytest

from backend.omni_agent import OmniAgent
from backend.responses_client import GenerationError
from backend.session import ConversationStore
from tests.test_sql_document_cells import environment, graph, QUESTION


def failure_audit():
    return {'status': 'failed', 'http_status': 200, 'model': 'protocol-fixture',
            'response_model': 'protocol-fixture', 'model_verified': True, 'response_completed': True,
            'output_failure': {'kind': 'invalid_json'}}


class ProtocolFixture:
    def __init__(self, audit=None, always_fail=False):
        self.calls = []; self.audit = {}; self.failure = audit or failure_audit(); self.always_fail = always_fail
    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(deepcopy(context))
        if len(self.calls) == 1 or self.always_fail:
            self.audit = deepcopy(self.failure)
            raise GenerationError('invalid provider JSON: malicious raw suffix not reusable')
        self.audit = {'status': 'completed', 'http_status': 200}
        return {'route': 'fusion', 'effective_question': QUESTION, 'clarification': '',
                'tasks_json': json.dumps(graph(), ensure_ascii=False)}


def test_completed_invalid_json_gets_one_fresh_plan_without_raw_text(environment):
    engine, knowledge = environment; client = ProtocolFixture()
    result = OmniAgent(engine, knowledge, ConversationStore(), client).query(QUESTION)
    assert result['status'] == 'ok' and len(client.calls) == 2
    feedback = client.calls[-1]['plan_completion_feedback']
    assert feedback['errors'] == ['completed_plan_invalid_json']
    assert 'malicious' not in str(client.calls[-1])
    assert list(result['result']['results']) == ['rank', 'cell']


@pytest.mark.parametrize('change', [
    {'http_status': 503}, {'http_status': None}, {'model_verified': False},
    {'response_completed': False}, {'response_model': 'unapproved-model'},
    {'output_failure': {'kind': 'refusal'}}, {'transport_usage_unknown': True},
])
def test_transport_truncation_refusal_and_identity_failure_never_use_repair(environment, change):
    engine, knowledge = environment
    client = ProtocolFixture({**failure_audit(), **change})
    result = OmniAgent(engine, knowledge, ConversationStore(), client).query(QUESTION)
    assert len(client.calls) == 1 and result['status'] != 'ok'
    assert result['result'].get('results', {}) == {}


def test_repeated_protocol_failure_stops_after_two_calls(environment):
    engine, knowledge = environment; client = ProtocolFixture(always_fail=True)
    result = OmniAgent(engine, knowledge, ConversationStore(), client).query(QUESTION)
    assert len(client.calls) == 2 and result['status'] != 'ok'
