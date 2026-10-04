"""A literal matching record is not proof of a complete requested set."""
import pytest

from backend.answer_contract import question_contract
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import GenerationError


class Client:
    model = 'gpt-6-luna'
    reasoning = 'medium'

    def __init__(self):
        self.calls = []
        self.audit = {}

    def generate(self, instructions, context, schema, **options):
        self.calls.append(options['name'])
        self.audit = {'status':'completed', 'model_verified':True, 'model':self.model,
                      'reasoning':self.reasoning, 'response_model':self.model, 'http_status':200}
        if options['name'] == 'grounded_whole_question_review':
            assert len(context['parts']) == 1
            assert 'ALL matching source records' in instructions
            return {'approved':False,'parts':[{'part_id':1,'answered':False,'claim_ids':[1]}]}
        return {'abstain':False,'claims':[{'text':'Iron was tested.',
                 'support':[{'citation_id':1,'quote':'Iron was tested.'}]}]}


def test_single_plural_question_requires_full_set_review():
    client = Client()
    citations = [{'citation_id':1,'title':'Analytical report',
                 'snippet':'Iron was tested. Manganese was tested.',
                 'metadata':{'source_locator':'page:1'}}]
    with pytest.raises(GenerationError,match='完整回答'):
        GroundedGenerator(client).answer('Which elements were tested?',citations)
    assert client.calls == ['grounded_answer','grounded_whole_question_review',
                            'grounded_answer_correction','grounded_whole_question_review']


@pytest.mark.parametrize('question', ['Which test failed?', 'What is the amount for Formula and Diapers?',
                                     'What activity is suggested for November?'])
def test_single_fact_does_not_require_enumeration_review(question):
    assert not question_contract(question)['exhaustive_selection_required']


@pytest.mark.parametrize('question',['Which two tests were used?','What 3 methods are suitable?'])
def test_numbered_plural_requests_still_require_complete_set(question):
    assert question_contract(question)['exhaustive_selection_required']
