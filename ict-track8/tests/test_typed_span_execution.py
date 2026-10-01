"""No API: literal source execution, review scope and replay tamper guards."""
from copy import deepcopy
import pytest
from backend.typed_span_execution import execute_threshold
from backend.grounded_span_answer import bind_grounded_span_answer, replay_literal_span_proof, THRESHOLD_REVIEW
from tests.test_grounded_span_answer import fixture, approved, Client


QUESTION='If the measured defect rate is 28%, does it meet the sample acceptance criteria?'


@pytest.mark.parametrize('source,expected',[
    ('Defect rate ≤ 30%','Yes'),('Defect rate < 28%','No'),
    ('Defect rate >= 28%','Yes'),('Defect rate > 28%','No'),
    ('Defect rate must not exceed 20%','No'),('Defect rate at most 28%','Yes'),
    ('Defect rate at least 29%','No'),('Defect rate no more than 28%','Yes'),
    ('Sample acceptance requirement: defect rate must not exceed 30%.','Yes'),
    ('缺陷率不超过30%','Yes'),('缺陷率不低于30%','No')])
def test_literal_percent_threshold_comparison(source,expected):
    result=execute_threshold(QUESTION,source)
    assert result['answer_value']==expected and result['question_observation']['value']=='28'
    assert result['answer_type']=='boolean'


@pytest.mark.parametrize('source',[
    'Defect rate 30%','Defect rate ≤ 20% or ≤ 30%','Defect rate not less than 30%',
    'Defect rate not ≤ 30%','Defect rate ≤ 1/2%','Defect rate ≤ 3e1%',
    'Defect rate ≤ 30 ppm','2025 Defect rate ≤ 30%','Defect rate ≤ 30% unless sample is red'])
def test_unbound_ambiguous_or_unsupported_source_refuses(source):
    with pytest.raises(ValueError):execute_threshold(QUESTION,source)


@pytest.mark.parametrize('question',[
    'Does it meet the criteria?','If the rate is 28% and 15%, does it meet the criteria?',
    'If the rate is 28%, does it not meet the criteria?',
    'If the rate is 1/2%, does it meet the criteria?',
    'If the rate is 3e1%, does it meet the criteria?',
    'If the rate is 1e2%, does it meet the criteria?',
    'If the rate is 1,028%, does it meet the criteria?',
    'If the rate is 28±2%, does it meet the criteria?',
    'If the rate is ½28%, does it meet the criteria?',
    'If the rate is 28% within ½ hour, does it meet the criteria?',
    'If the rate is 20.5.5%, does it meet the criteria?',
    'If the rate is 20.5% for 2 samples, does it meet the criteria?',
    'If the rate is 20–28%, does it meet the criteria?',
    'What is the rate at 28%?'])
def test_question_must_supply_one_explicit_nonnegated_observation(question):
    with pytest.raises(ValueError):execute_threshold(question,'Defect rate ≤ 30%')


class ThresholdClient(Client):
    def __init__(self,reject=None,mutate=None):
        super().__init__(mutate=mutate);self.reject=reject
    def generate(self,instructions,context,schema,**kwargs):
        super().generate(instructions,context,schema,**kwargs)
        if len(self.calls)==1:
            anchors=[e for e in context['quote_catalog'] if e['context_eligible']]
            anchor=next(e for e in anchors if e['quote']==getattr(self,'threshold_quote','Defect rate ≤ 30%.'))
            return {'abstain':False,'threshold_quote_id':anchor['quote_id'],'scope_ids':[anchor['quote_id']]}
        result={key:True for key in schema['properties']};result['reason_code']='supported_unique_complete_scope'
        if self.reject:result[self.reject]=False
        return result


def run(client=None):
    claim,citation=fixture('Defect rate ≤ 30%.')
    client=client or ThresholdClient()
    return bind_grounded_span_answer(QUESTION,[claim],[citation],client),citation,client


def test_production_binding_executes_boolean_and_replays_original_sources():
    result,citation,client=run()
    assert result['status']=='model_reviewed' and result['answer_value']=='Yes'
    assert len(client.calls)==2 and client.calls[1][1]['server_execution']['outcome'] is True
    assert replay_literal_span_proof(QUESTION,result,[citation])
    assert result['claims'][0]['text']=='Defect rate ≤ 30%.'


@pytest.mark.parametrize('flag',[key for key in THRESHOLD_REVIEW['properties'] if key!='reason_code'])
def test_any_failed_independent_alignment_guard_refuses(flag):
    result,_,_=run(ThresholdClient(reject=flag))
    assert result['status']=='unsupported' and 'answer_value' not in result


def test_boolean_question_cannot_publish_literal_threshold_even_if_model_would_approve():
    claim,citation=fixture('Defect rate ≤ 30%.')
    client=Client(candidate={'abstain':False,'citation_id':1,'answer_span':'≤ 30%',
        'answer_context':claim['text'],'answer_type':'quantity','scope':[]})
    result=bind_grounded_span_answer(QUESTION,[claim],[citation],client)
    assert result['status']=='unsupported' and len(client.calls)==1


def test_missing_question_observation_refuses_before_model_call():
    claim,citation=fixture('Defect rate ≤ 30%.');client=ThresholdClient()
    result=bind_grounded_span_answer('Does this sample meet the criteria?',[claim],[citation],client)
    assert result['status']=='unsupported' and client.calls==[]


@pytest.mark.parametrize('field,value',[('answer_value','No'),('answer_type','quantity')])
def test_replay_rejects_changed_output(field,value):
    result,citation,_=run();result[field]=value
    assert not replay_literal_span_proof(QUESTION,result,[citation])


def test_replay_rejects_changed_execution_even_with_rehashed_saved_proof():
    from backend.grounded_span_answer import _sha
    result,citation,_=run();result['answer_proof']['execution']['threshold_value']='99'
    result['saved_proof_sha256']=_sha(result['answer_proof'])
    assert not replay_literal_span_proof(QUESTION,result,[citation])


@pytest.mark.parametrize('flag',[key for key in THRESHOLD_REVIEW['properties'] if key!='reason_code'])
def test_replay_rejects_any_missing_or_false_review_guard_even_if_rehashed(flag):
    from backend.grounded_span_answer import _sha
    result,citation,_=run();result['answer_proof']['semantic_review'][flag]=False
    result['saved_proof_sha256']=_sha(result['answer_proof'])
    assert not replay_literal_span_proof(QUESTION,result,[citation])


def test_decimal_observation_preserves_whole_token():
    question='If the defect rate is 20.5%, does it meet the criteria?'
    result=execute_threshold(question,'Defect rate ≤ 20.4%')
    assert result['answer_value']=='No' and result['question_observation']['value']=='20.5'


def test_conflicting_source_is_visible_to_independent_review_and_refuses():
    claim,citation=fixture('Defect rate ≤ 30%.')
    other,second=fixture('Defect rate ≤ 20%.',cid=2)
    client=ThresholdClient(reject='no_evidence_conflict')
    result=bind_grounded_span_answer(QUESTION,[claim,other],[citation,second],client)
    assert result['status']=='unsupported' and len(client.calls[1][1]['evidence'])==2
    assert client.calls[1][1]['complete_validated_facts']==[claim,other]


def test_only_replayable_native_complete_region_contract_may_raise_evidence_cap():
    from backend.grounded_span_answer import _snapshot
    text='Sample context. '*130+'Defect rate ≤ 30%.'
    claim,citation=fixture(text);source=citation['generation_evidence']
    claim={'text':'Defect rate ≤ 30%.','support':[{'citation_id':1,'quote':'Defect rate ≤ 30%.'}]}
    with pytest.raises(ValueError):_snapshot([claim],[citation])
    source['native_context_max_chars']=3200
    source['native_context']={'extraction_version':'original-native-bounded-page-region-v1',
        'mode':'original_native_complete_table_region','max_chars':3200,
        'source_sha256':source['source_sha256'],'evidence_sha256':source['evidence_sha256'],
        'page_no':1,'calculator_input_eligible':False}
    assert _snapshot([claim],[citation])[0][1]==text
    source['native_context']['calculator_input_eligible']=True
    with pytest.raises(ValueError):_snapshot([claim],[citation])


def test_source_mutation_during_review_refuses():
    claim,citation=fixture('Defect rate ≤ 30%.')
    def mutate(call):
        if call==2:citation['generation_evidence']['text']='Defect rate ≤ 20%.'
    client=ThresholdClient(mutate=mutate)
    result=bind_grounded_span_answer(QUESTION,[claim],[citation],client)
    assert result['status']=='unsupported' and 'answer_value' not in result


def test_who_question_refuses_quantity_before_review():
    claim,citation=fixture('Revenue: -125.50 USD.');client=Client()
    result=bind_grounded_span_answer('Who manages revenue?',[claim],[citation],client)
    assert result['status']=='unsupported' and len(client.calls)==1


def test_entity_type_label_cannot_disguise_a_procedure_answer():
    text='Mira Chen will email the regulatory agency.';claim,citation=fixture(text)
    client=Client(candidate={'abstain':False,'citation_id':1,'answer_span':text,
        'answer_context':text,'answer_type':'entity','scope':[]})
    result=bind_grounded_span_answer('Who contacts the regulatory agency?',[claim],[citation],client)
    assert result['status']=='unsupported' and len(client.calls)==1


@pytest.mark.parametrize('source,reject', [
    ('Defect rate ≤ 30%.', None),
    ('Defect rate ≤ 30%.', 'server_boolean_answers_whole_question'),
    ('Maximum allowable defect rate: 30%.', 'server_boolean_answers_whole_question'),
    ('Defect rate must not exceed 30%.', 'server_boolean_answers_whole_question'),
])
def test_store_uses_executed_boolean_with_fresh_source_replay(tmp_path, source, reject):
    from backend.knowledge_store import KnowledgeStore
    from backend.grounded_generation import GroundedGenerator
    class StoreClient(ThresholdClient):
        def generate(self,instructions,context,schema,**kwargs):
            if kwargs['name'].startswith('grounded_answer'):
                self.calls.append((instructions,deepcopy(context),kwargs))
                self.audit={'model':self.model,'reasoning':self.reasoning,'status':'completed',
                    'http_status':200,'model_verified':True,'response_model':self.model}
                hit=context['evidence'][0]
                return {'abstain':False,'claims':[{'text':hit['text'],
                    'support':[{'citation_id':hit['citation_id'],'quote':hit['text']}]}]}
            # ThresholdClient distinguishes selector and review by call count.
            before=len(self.calls);self.calls=[]
            if kwargs['name']=='grounded_threshold_independent_review':self.calls=[None]
            answer=super().generate(instructions,context,schema,**kwargs)
            self.calls=[None]*before+[self.calls[-1]]
            return answer
    client=StoreClient(reject=reject);client.threshold_quote=source
    store=KnowledgeStore(tmp_path,generator=GroundedGenerator(client))
    store.ingest(source.encode(),document_id='record',title='Defect rate acceptance',
        modality='txt',filename='record.txt')
    result=store.answer(QUESTION)
    if reject:
        assert result['status']=='insufficient_evidence'
        assert result['answer_strategy']=='incomplete_boolean_abstention'
        assert '30%' in result['full_fact_answer']
        assert not result.get('answer_span_result')
        return
    assert result['answer']=='Yes' and result['answer_span_result']['answer_type']=='boolean'
    assert result['answer_strategy']=='model_reviewed_source_span'
    assert result['full_fact_answer'] and len(client.calls)==3
