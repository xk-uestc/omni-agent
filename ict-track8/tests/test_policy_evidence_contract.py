"""Policy version comparison preserves applicability and public helper types."""
import pytest

from backend.policy_evidence import select_policy
from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def document(text):
    return {'document_id':'policy','sha256':'synthetic','chunks':[
        {'source_locator':'lines:1-2','text':text}]}


@pytest.mark.parametrize('quantity,number,unit', [
    ('3日',3,'日'),('5天',5,'日'),('12个月',12,'个月'),
    ('2月',2,'个月'),('100元',100,'CNY'),('2.5小时',2.5,'小时'),
])
def test_public_string_value_and_separate_numeric_value(quantity,number,unit):
    result=select_policy(document(f'2025版期限为{quantity},自2025-01-01起生效。'),
                         as_of='2025-02-01',label='期限')
    assert result['value']==result['raw_value']==quantity
    assert result['numeric_value']==number and result['unit']==unit
    assert result['document_id']=='policy' and result['label']=='期限'


@pytest.mark.parametrize('statement', [
    '期限为3日，仅适用于VIP', '期限为3日,不适用于普通用户',
    '期限为3日至5日', '仅限VIP的期限为3日', '期限为至少3日',
])
def test_applicability_and_ranges_are_not_numeric_scalars(statement):
    result=select_policy(document(statement+',自2025-01-01起生效。'),as_of='2025-02-01',label='期限')
    assert 'numeric_value' not in result and result['unit']=='unknown'
    assert '3日' in result['value']
    assert result['value']!= '3日'


def agent(tmp_path,text):
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest(text.encode(),document_id='policy',title='政策历史',modality='txt',filename='policy.txt')
    store.ingest('期限为9日,自2025-01-01起生效。'.encode(),document_id='other',
                 title='其他政策',modality='txt',filename='other.txt')
    return DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),store)


def comparison(*,new_document='policy',new_label='期限',operator='eq'):
    return [
        {'id':'old','tool':'policy_select','args':{'document_id':'policy','as_of':'2024-12-31','label':'期限'}},
        {'id':'new','tool':'policy_select','args':{'document_id':new_document,'as_of':'2025-01-01','label':new_label}},
        {'id':'result','tool':'compare','args':{'left':{'ref':'old','path':[]},
            'right':{'ref':'new','path':[]},'operator':operator}}]


def test_typed_policy_projection_retains_version_evidence(tmp_path):
    result=agent(tmp_path,'期限为3日,生效区间为2024-01-01至2024-12-31。\n'
        '期限为6日,自2025-01-01起生效。').run(comparison(operator='lt'))
    assert result['status']=='ok'
    compared=result['results']['result']
    assert compared['matched'] is True and compared['difference']==-3
    assert compared['left']['raw_value']=='3日' and compared['left']['value']==3
    assert compared['left']['document_id']=='policy' and compared['left']['as_of']=='2024-12-31'


@pytest.mark.parametrize('override', [{'new_label':'退款期限'},{'new_document':'other'}])
def test_execution_rejects_different_policy_elements(tmp_path,override):
    result=agent(tmp_path,'期限为3日,生效区间为2024-01-01至2024-12-31。\n'
        '期限为6日,自2025-01-01起生效。\n退款期限为9日,自2025-01-01起生效。').run(comparison(**override))
    assert result['status']=='incomplete' and result['failed_task']=='result'
    assert '同一政策要素' in result['error']


def test_extra_conditions_survive_equality_and_block_numeric_comparison(tmp_path):
    tool=agent(tmp_path,'期限为3日,仅适用于VIP,生效区间为2024-01-01至2024-12-31。\n'
        '期限为3日,适用于所有客户,自2025-01-01起生效。')
    result=tool.run(comparison())
    assert result['status']=='ok' and result['results']['result']['status']=='different'
    assert 'VIP' in result['results']['result']['left']['value']
    assert '所有客户' in result['results']['result']['right']['value']
    blocked=tool.run(comparison(operator='lt'))
    assert blocked['status']=='incomplete' and blocked['failed_task']=='result'
    assert '单位明确' in blocked['error']
