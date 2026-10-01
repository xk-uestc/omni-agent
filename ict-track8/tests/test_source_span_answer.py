"""Evidence-only provenance is separate from validated-claim projection."""
from copy import deepcopy
import pytest
from backend.source_span_answer import bind_source_span_answer,replay_source_span_proof,SOURCE_REVIEW
from backend.grounded_span_answer import bind_grounded_span_answer,_sha
from backend.responses_client import GenerationError
from tests.test_grounded_span_answer import fixture


class Client:
    model='gpt-6-luna';reasoning='medium'
    def __init__(self,span='Mira Chen',kind='entity',reject=None,fail=None,hook=None):
        self.span,self.kind,self.reject,self.fail,self.hook=span,kind,reject,fail,hook
        self.calls=[];self.audit={}
    def generate(self,instructions,context,schema,**kwargs):
        self.calls.append(deepcopy(context));self.audit={'status':'completed','http_status':200,
            'model':'gpt-6-luna','reasoning':'medium','model_verified':True,'response_model':'gpt-6-luna'}
        if self.hook:self.hook(len(self.calls))
        if len(self.calls)==self.fail:raise GenerationError('private payload')
        if len(self.calls)==1:
            source=context['evidence'][0]
            return {'abstain':False,'citation_id':source['citation_id'],'answer_span':self.span,
                'answer_context':source['text'],'answer_type':self.kind,
                'scope':[{'citation_id':source['citation_id'],'quote':source['text']}]}
        review={k:True for k in schema['properties']};review['reason_code']='supported_unique_complete_scope'
        if self.reject:review[self.reject]=False
        return review


def run(client=None,text='The research lead is Mira Chen.',question='Who is the research lead?'):
    _,citation=fixture(text);client=client or Client()
    return bind_source_span_answer(question,[citation],client),citation,client


def test_source_lane_answers_without_inventing_validated_claims_and_replays():
    result,citation,client=run()
    assert result['status']=='model_reviewed' and result['answer_value']=='Mira Chen'
    assert 'claims' not in result and 'complete_validated_facts' not in client.calls[0]
    assert client.calls[1]['evidence']==client.calls[0]['evidence']
    assert result['evidence_contract']=='raw_source_only_no_validated_facts'
    assert replay_source_span_proof('Who is the research lead?',result,[citation])
    assert bind_grounded_span_answer('Who is the research lead?',[],[citation],Client())['status']=='unsupported'


@pytest.mark.parametrize('flag',[key for key in SOURCE_REVIEW['properties'] if key!='reason_code'])
def test_every_semantic_review_guard_is_mandatory(flag):
    result,_,client=run(Client(reject=flag))
    assert result['status']=='unsupported' and len(client.calls)==2 and 'answer_value' not in result


@pytest.mark.parametrize('flag',[key for key in SOURCE_REVIEW['properties'] if key!='reason_code'])
def test_replay_rejects_review_flag_tamper_even_rehashed(flag):
    result,citation,_=run();result['answer_proof']['semantic_review'][flag]=False
    result['saved_proof_sha256']=_sha(result['answer_proof'])
    assert not replay_source_span_proof('Who is the research lead?',result,[citation])


@pytest.mark.parametrize('text,span',[
    ('Rate: -125.50 USD.','125.50 USD'),('Count: 1,74,459.','74,459'),
    ('Value: 1e2 units.','1'),('Value: 1/2 units.','2'),('Value: ½28 units.','28'),
    ('Date: 2045.','45')])
def test_partial_numeric_tokens_refuse_before_semantic_review(text,span):
    result,_,client=run(Client(span=span,kind='quantity'),text,'What is the value?')
    assert result['status']=='unsupported' and len(client.calls)==1


def test_catalog_model_uses_source_ids_without_retyping_native_context():
    class CatalogClient(Client):
        def generate(self,instructions,context,schema,**kwargs):
            result=super().generate(instructions,context,schema,**kwargs)
            if len(self.calls)==1:
                anchor=next(e for e in context['quote_catalog'] if e['quote']==context['evidence'][0]['text'])
                return {'abstain':False,'answer_span':'Mira\nChen','answer_context_id':anchor['quote_id'],
                    'answer_type':'entity','scope_ids':[anchor['quote_id']]}
            return result
    result,_,_=run(CatalogClient(),text='The research lead is Mira\nChen.')
    assert result['status']=='model_reviewed' and result['answer_value']=='Mira\nChen'


def test_competing_sources_are_preserved_and_review_can_reject():
    _,first=fixture('The research lead is Mira Chen.')
    _,second=fixture('The research lead is Noah Lee.',cid=2)
    client=Client(reject='no_evidence_conflict')
    result=bind_source_span_answer('Who is the research lead?',[first,second],client)
    assert result['status']=='unsupported' and len(client.calls[1]['evidence'])==2


@pytest.mark.parametrize('call',[1,2])
def test_provider_failure_never_accepts_and_never_leaks(call):
    result,_,_=run(Client(fail=call))
    assert result['status']=='unsupported' and 'private payload' not in str(result)


def test_mutation_during_review_refuses_even_after_approval():
    _,citation=fixture('The research lead is Mira Chen.')
    def hook(call):
        if call==2:citation['generation_evidence']['source_locator']='different'
    result=bind_source_span_answer('Who is the research lead?',[citation],Client(hook=hook))
    assert result['status']=='unsupported' and 'answer_value' not in result


def test_source_lane_does_not_let_threshold_quote_answer_boolean():
    result,_,client=run(Client(span='≤ 30%',kind='quantity'),text='Defect rate ≤ 30%.',
        question='If the rate is 28%, does it meet the criteria?')
    assert result['status']=='unsupported' and client.calls==[]


def test_operand_cannot_be_approved_as_calculated_answer():
    result,_,_=run(Client(span='90',kind='quantity',reject='answer_itself_answers_whole_question'),
        text='Earlier count 90; later count 60.',question='What is the decline?')
    assert result['status']=='unsupported'


def test_forged_pin_or_output_never_replays():
    result,citation,_=run();result['answer_value']='Noah Lee'
    assert not replay_source_span_proof('Who is the research lead?',result,[citation])
    result,citation,_=run();citation['generation_evidence']['source_sha256']='b'*64
    assert not replay_source_span_proof('Who is the research lead?',result,[citation])
