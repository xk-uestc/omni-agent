"""Evidence-first store recovery with real files and mock-only audited calls."""
from copy import deepcopy
import pytest
from backend.knowledge_store import KnowledgeStore,SourceRevisionError
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import GenerationError
QUESTION='Who is the research lead?'


class Client:
    model='gpt-6-luna';reasoning='medium'
    def __init__(self,initial='abstain',reject=None,hook=None,audit_change=None):
        self.initial,self.reject,self.hook,self.audit_change=initial,reject,hook,audit_change
        self.calls=[];self.contexts=[];self.audit={}
    def generate(self,instructions,context,schema,**kwargs):
        name=kwargs['name'];self.calls.append(name);self.contexts.append(deepcopy(context))
        self.audit={'status':'completed','http_status':200,'model_verified':True,
            'model':self.model,'reasoning':self.reasoning,'response_model':self.model,'operation':name}
        if name.startswith('grounded_answer'):
            if self.initial in ('401','403','transport','stale_transport'):
                if self.initial!='stale_transport':
                    self.audit.update(status='failed',http_status=int(self.initial) if self.initial.isdigit() else None)
                raise GenerationError('private provider payload')
            if self.initial=='invalid':
                return {'abstain':False,'claims':[{'text':'The research lead is Noah Lee.',
                    'support':[{'citation_id':context['evidence'][0]['citation_id'],'quote':context['evidence'][0]['text']}]}]}
            return {'abstain':True,'claims':[]}
        if name=='source_span_selection':
            anchor=next(e for e in context['quote_catalog'] if e['quote']=='The research lead is Mira Chen.')
            return {'abstain':False,'answer_span':'Mira Chen','answer_context_id':anchor['quote_id'],
                'answer_type':'entity','scope_ids':[anchor['quote_id']]}
        if self.hook:self.hook()
        if self.audit_change:self.audit.update(self.audit_change)
        review={key:True for key in schema['properties']};review['reason_code']='supported_unique_complete_scope'
        if self.reject:review[self.reject]=False
        return review


def prepare(tmp_path,**kwargs):
    client=Client(**kwargs);store=KnowledgeStore(tmp_path/'knowledge',generator=GroundedGenerator(client))
    store.ingest(b'The research lead is Mira Chen.',document_id='record',title='Research lead',modality='txt',filename='r.txt')
    return store,client


@pytest.mark.parametrize('initial,generation_calls',[('abstain',1),('invalid',2)])
def test_initial_abstention_or_semantic_rejection_recovers_without_claim_fabrication(tmp_path,initial,generation_calls):
    store,client=prepare(tmp_path,initial=initial)
    result=store.answer(QUESTION)
    assert result['status']=='ok' and result['answer']=='Mira Chen' and result['claims']==[]
    assert result['answer_mode']=='source_span_model_reviewed'
    assert result['answer_strategy']=='evidence_first_literal_source_span'
    assert len(client.calls)==generation_calls+2
    assert client.calls[-2:]==['source_span_selection','source_span_independent_review']
    assert 'complete_validated_facts' not in client.contexts[-2]
    assert result['answer_span_result']['evidence_contract']=='raw_source_only_no_validated_facts'
    assert result['prior_generation_output']


@pytest.mark.parametrize('initial',['401','403','transport'])
def test_provider_failure_never_triggers_source_recovery(tmp_path,initial):
    store,client=prepare(tmp_path,initial=initial)
    result=store.answer(QUESTION)
    assert client.calls==['grounded_answer']
    assert 'answer_span_result' not in result and result['answer_mode']=='extractive_fallback'
    assert 'private provider payload' not in str(result)


@pytest.mark.parametrize('flag',['approved','source_answer_relation_visible','answer_itself_answers_whole_question','no_evidence_conflict'])
def test_negative_review_does_not_publish_source_answer(tmp_path,flag):
    store,client=prepare(tmp_path,reject=flag)
    result=store.answer(QUESTION)
    assert result['status']=='insufficient_evidence' and 'answer_span_result' not in result
    assert result['trace'][-1]['status']=='unsupported' and len(client.calls)==3


def test_conflicting_sources_remain_in_review_context(tmp_path):
    store,client=prepare(tmp_path,reject='no_evidence_conflict')
    store.ingest(b'The research lead is Noah Lee.',document_id='other',title='Research lead',modality='txt',filename='o.txt')
    result=store.answer(QUESTION)
    assert result['status']=='insufficient_evidence'
    assert len(client.contexts[-1]['evidence'])==2


@pytest.mark.parametrize('change',[{'status':'failed'},{'http_status':403},{'http_status':True},
    {'model_verified':False},{'response_model':'gpt-6-sol'},{'reasoning':'high'}])
def test_source_review_must_be_completed_verified_allowed_model(tmp_path,change):
    store,_=prepare(tmp_path,audit_change=change)
    result=store.answer(QUESTION)
    assert result['status']=='insufficient_evidence' and 'answer_span_result' not in result


def test_source_change_during_review_never_publishes_answer(tmp_path):
    store,client=prepare(tmp_path)
    client.hook=lambda:store.ingest(b'The research lead is Noah Lee.',document_id='record',title='Research lead',modality='txt',filename='r.txt')
    with pytest.raises(SourceRevisionError):store.answer(QUESTION)


def test_fresh_replay_failure_does_not_publish_answer(tmp_path,monkeypatch):
    store,_=prepare(tmp_path)
    monkeypatch.setattr('backend.source_span_answer.replay_source_span_proof',lambda *args:False)
    result=store.answer(QUESTION)
    assert result['status']=='insufficient_evidence' and 'answer_span_result' not in result
    assert result['trace'][-1]['status']!='model_reviewed'


def test_provider_exception_with_stale_completed_audit_does_not_recover(tmp_path):
    store,client=prepare(tmp_path,initial='stale_transport')
    result=store.answer(QUESTION)
    assert client.calls==['grounded_answer'] and 'answer_span_result' not in result
