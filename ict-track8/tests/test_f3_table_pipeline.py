from copy import deepcopy
from types import SimpleNamespace

import pytest
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.source_answer_dossier import build_dossier, FLAGS, replay_context
from backend.bounded_table_evidence import prepare_table_bundle
from tools.evaluate_foundation_f2 import raw_file


HEADER='编号 | 区域 | 结论 | 读数(毫克/升) | 日期'
QUESTION='请完整列出E-17批次北区且结论合格的所有编号，并带上读数和日期。'


def put(s,did='table',*,duplicate=False,other=False,unit=False,year=2026):
    pages=['批次验收记录\n'+HEADER+'\nE-17-01 | 北区 | 合格 | 12.5 | '+str(year)+'-04-01\nE-17-02 | 南区 | 合格 | 13.5 | '+str(year)+'-04-02',
        '批次验收记录（续）\n'+HEADER+'\n'+('E-17-01' if duplicate else 'E-18-03' if other else 'E-17-03')+' | 北区 | 合格 | 14.5 | '+str(year)+'-04-03\nE-17-04 | 北区 | 隔离 | 15.5 | '+str(year)+'-04-04\n隔离记录不纳入合格样本统计。']
    if unit:pages[1]=pages[1].replace('毫克/升','克/升')
    if year!=2026:pages[0]='对象代码E-17，年份2025。该版本已撤销。\n'+pages[0]
    return s.ingest(raw_file({'type':'pdf','pages':pages}),document_id=did,title='Table',modality='pdf',filename='t.pdf')


def test_real_answer_entry_prepares_all_native_rows_and_dossier(tmp_path):
    s=KnowledgeStore(tmp_path);put(s)
    result=s.answer(QUESTION,top_k=1)
    b=result['evidence_bundle']
    assert b['status']=='complete_for_declared_scope'
    assert [r['row_id'] for r in b['matching_rows']]==['E-17-01','E-17-03']
    assert b['extra_retrieval_calls']==1 and b['extra_native_page_reads']==2
    assert len(b['rows'])==4 and all(r['source_sha256']==b['source_sha256'] for r in b['rows'])
    pages,scope=build_dossier(s,QUESTION,s.search(QUESTION,top_k=1),evidence_bundle=b)
    assert len(pages)==2 and all(p['complete_native_page'] for p in pages)
    assert scope['table_bundle']['row_scope']['matching_row_ids']==['E-17-01','E-17-03']
    assert result['status']=='insufficient_evidence' # evidence is not an answer
    bad=deepcopy(b);bad['matching_rows'][0]['text']='forged'
    with pytest.raises(ValueError,match='bundle_changed'):build_dossier(s,QUESTION,s.search(QUESTION),evidence_bundle=bad)


@pytest.mark.parametrize('mode',['duplicate','other','unit'])
def test_record_sample_and_header_ambiguity_refused(tmp_path,mode):
    s=KnowledgeStore(tmp_path);put(s,**{mode:True})
    b=prepare_table_bundle(s,QUESTION,s.search(QUESTION))
    assert b['status']=='ambiguous' and not b['pages']


def test_competing_sources_budget_and_scope_refused(tmp_path):
    s=KnowledgeStore(tmp_path);put(s)
    h=s.search(QUESTION)
    assert prepare_table_bundle(s,QUESTION,h,page_budget=1)['status']=='budget_exhausted'
    assert not prepare_table_bundle(s,QUESTION,h,document_id='table',page_no=1)['pages']
    put(s,'competitor')
    assert prepare_table_bundle(s,QUESTION,h)['status']=='ambiguous'
    assert prepare_table_bundle(s,QUESTION,h,document_id='table')['status']=='complete_for_declared_scope'


def test_old_source_refused_and_update_delete_invalidates_bundle(tmp_path):
    s=KnowledgeStore(tmp_path);put(s);put(s,'old',year=2025)
    h=s.search(QUESTION);b=prepare_table_bundle(s,QUESTION,h)
    assert b['document_id']=='table'
    s.ingest(raw_file({'type':'pdf','pages':['对象代码E-18。新版本记录。']}),document_id='table',title='Table',modality='pdf',filename='t.pdf')
    with pytest.raises(SourceRevisionError):prepare_table_bundle(s,QUESTION,h)
    # Delete with current SHA; pinned dossier still cannot be reused.
    d=s.document('table');s.delete('table',expected_sha256=d['sha256'])
    with pytest.raises((SourceRevisionError,ValueError)):build_dossier(s,QUESTION,h,evidence_bundle=b)


class ReviewClient:
    supports_native_answer_dossier=True
    model='gpt-6-luna';reasoning='medium';audit={}
    def __init__(self):self.packets=[]
    def generate(self,instructions,packet,schema,**kwargs):
        self.packets.append(deepcopy(packet));self.audit={'status':'completed','model_verified':True,'http_status':200,'model':self.model,'response_model':self.model,'reasoning':self.reasoning}
        wanted=[]
        for p in packet['original_pages']:
            for line in p['text'].splitlines():
                if ' | 北区 | 合格 | ' in line:
                    wanted.append({'evidence_id':p['evidence_id'],'quote':line})
        if len(self.packets)==1:
            return {'abstain':False,'quotes':wanted,'clauses':[{'text':q['quote'],'support_ids':[i]} for i,q in enumerate(wanted,1)]}
        return {k:True for k in FLAGS}|{'required_items':[q|{'clause_ids':[i]} for i,q in enumerate(wanted,1)]}


def test_existing_review_and_native_quote_replay_consume_production_bundle(tmp_path):
    s=KnowledgeStore(tmp_path);put(s);client=ReviewClient();s.generator=SimpleNamespace(client=client)
    result=s.answer(QUESTION,top_k=1)
    assert result['answer_mode']=='native_page_dossier_model_reviewed'
    assert len(client.packets)==2 and all(len(p['original_pages'])==2 for p in client.packets)
    assert len(result['citations'])==2 and 'E-17-03' in result['answer']
    replay_context(s,result)
    result['citations'][0]['snippet']='forged'
    with pytest.raises(ValueError):replay_context(s,result)
