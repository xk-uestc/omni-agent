"""Independent completeness review must not authorize missing/changed facts."""
from copy import deepcopy

import pytest

from backend.answer_contract import requested_question_parts
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import GenerationError


QUESTION='What is the purpose of the service and who manages it?'
TEXT='The service is intended to protect records. Alice manages the service.'
CLAIMS=[{'text':'The service is intended to protect records.',
         'support':[{'citation_id':1,'quote':'The service is intended to protect records.'}]},
        {'text':'Alice manages the service.',
         'support':[{'citation_id':1,'quote':'Alice manages the service.'}]}]


class Client:
    model='gpt-6-luna'
    reasoning='medium'
    def __init__(self,mode='good'):
        self.mode=mode;self.calls=[];self.audit={}
    def generate(self,instructions,context,schema,**kwargs):
        self.calls.append((kwargs['name'],deepcopy(context)))
        self.audit={'status':'completed','http_status':200,'model_verified':True,
                    'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if kwargs['name']=='grounded_whole_question_review':
            if self.mode=='unauthorized':
                self.audit.update(status='failed',http_status=401)
                raise GenerationError('unavailable')
            rows=[{'part_id':1,'answered':True,'claim_ids':[1]},
                  {'part_id':2,'answered':True,'claim_ids':[2]}]
            if self.mode=='partial':rows[1].update(answered=False,claim_ids=[])
            if self.mode=='invented':rows[1]['claim_ids']=[3]
            if self.mode=='duplicate':rows[1]['part_id']=1
            if self.mode=='invalid':rows[1]['claim_ids']=[{}]
            if self.mode=='bad_model':self.audit['response_model']='gpt-5.5'
            return {'approved':self.mode!='partial','parts':rows}
        return {'abstain':False,'claims':deepcopy(CLAIMS)}


def citations():
    return [{'citation_id':1,'title':'Service guide','snippet':TEXT,
             'generation_evidence':{'text':TEXT},'metadata':{'source_locator':'page:1'}}]


def test_whole_question_review_binds_existing_claims_and_original_evidence():
    client=Client();result=GroundedGenerator(client).answer(QUESTION,citations())
    assert result['status']=='ok'
    assert [name for name,_ in client.calls]==['grounded_answer','grounded_whole_question_review']
    assert result['whole_question_review']['parts'][1]['claim_ids']==[2]
    context=client.calls[-1][1]
    assert context['question']==QUESTION and context['evidence'][0]['text']==TEXT
    assert result['generation_attempts'][0]['whole_question_review']==result['whole_question_review']


@pytest.mark.parametrize('mode',['partial','invented','duplicate','invalid','bad_model'])
def test_missing_parts_fabricated_ids_wrong_model_and_invalid_payload_are_rejected(mode):
    client=Client(mode)
    with pytest.raises(GenerationError,match='完整回答') as caught:
        GroundedGenerator(client).answer(QUESTION,citations())
    assert len(client.calls)==4
    assert caught.value.generation_attempts[-1]['error_category']=='incomplete_question_answer'


def test_review_auth_failure_stops_without_answer_repair_or_more_api_calls():
    client=Client('unauthorized')
    with pytest.raises(GenerationError) as caught:
        GroundedGenerator(client).answer(QUESTION,citations())
    assert len(client.calls)==2
    assert caught.value.generation_attempts[-1]['provider_audit']['http_status']==401
    assert caught.value.generation_attempts[-1]['error_category']=='provider_unavailable'


@pytest.mark.parametrize('question,parts',[
    ('What is the amount for Formula and Diapers?',1),
    ('What changed and when did it change?',2),
    ('What is the total and how is it calculated and why is it lower?',3),
    ('费用是多少以及为什么变化？',2),
    ('甲和乙的费用是多少？',1),
])
def test_literal_question_spans_do_not_split_entities(question,parts):
    assert len(requested_question_parts(question))==parts
