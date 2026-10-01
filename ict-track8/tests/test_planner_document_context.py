"""Planning must see relevant evidence beyond catalogue/preview cutoffs.

The capturing client is a contract stub, not evidence of a real model call.
"""
from io import BytesIO
import json

from openpyxl import Workbook

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


class CapturingPlanner:
    def generate(self, instructions, data, *args, **kwargs):
        self.data = data
        return {'route':'clarify', 'effective_question':data['question'],
                'clarification':'请补充时间范围。', 'tasks_json':'[]'}


def agent_for(tmp_path):
    store = KnowledgeStore(tmp_path/'knowledge')
    planner = CapturingPlanner()
    agent = OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),store,ConversationStore(),planner)
    return agent, store, planner


def test_planner_catalogue_retrieves_document_after_first_hundred(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    for i in range(105):
        store.ingest(f'普通仓库编号{i}。'.encode(),document_id=f'a{i:03}',title='仓库说明',modality='txt',filename='w.txt')
    store.ingest('深海机器人定期检修周期为17个月。'.encode(),document_id='z-target',title='深海机器人检修',modality='txt',filename='target.txt')
    agent.query('深海机器人检修周期多久')
    selected = planner.data['documents']
    assert any(d['id']=='z-target' and '17个月' in d['text'] for d in selected)
    assert len(selected) <= 12


def test_planner_preview_uses_relevant_chunk_after_three_thousand_characters(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    text = '\n'.join(f'第{i}节 普通说明\n电源模块装配步骤应由现场技术人员确认。'*10 for i in range(25))
    text += '\n深海机器人检修周期为17个月。'
    store.ingest(text.encode(),document_id='deep-manual',title='综合手册',modality='txt',filename='manual.txt')
    agent.query('深海机器人检修周期多久')
    assert any('17个月' in d['text'] for d in planner.data['documents'])


def test_planner_table_context_selects_row_after_first_thirty(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    wb = Workbook()
    sheet = wb.active
    sheet.append(['装备名称','增长率','适用年份'])
    for i in range(70):
        sheet.append([f'常规装备{i}',0.01,2026])
    sheet.append(['深海机器人',0.17,2026])
    raw=BytesIO(); wb.save(raw)
    store.ingest(raw.getvalue(),document_id='equipment-targets',title='装备参数',modality='xlsx',filename='targets.xlsx')
    agent.query('深海机器人2026增长率参数是多少')
    table_rows=[row for d in planner.data['documents'] for row in d['table_rows']]
    assert any('深海机器人' in row['values'] and 0.17 in row['values'] for row in table_rows)
    assert all(row.get('locator') for row in table_rows)


def test_shared_title_does_not_displace_high_relevance_late_document(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    for i in range(20):
        store.ingest(f'普通装配说明编号{i}。'.encode(),document_id=f'a{i:03}',title='技术手册',modality='txt',filename='w.txt')
    store.ingest('深海机器人检修周期为17个月。'.encode(),document_id='z-target',title='机器人手册',modality='txt',filename='target.txt')
    agent.query('技术手册中深海机器人检修周期多久')
    assert any(d['id']=='z-target' and '17个月' in d['text'] for d in planner.data['documents'])


def test_followup_retrieval_keeps_document_topic_and_avoids_table_counts(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    for i in range(20):
        store.ingest(f'2026年普通记录{i}。'.encode(),document_id=f'a{i:03}',title='记录',modality='txt',filename='w.txt')
    store.ingest('深海机器人2026年检修周期为17个月。'.encode(),document_id='z-target',title='机器人手册',modality='txt',filename='target.txt')
    agent.conversations.remember('topic',question='深海机器人检修周期多久',effective_question='深海机器人检修周期多久',state={'route':'document'})
    agent.query('那2026年呢',session_id='topic')
    assert any(d['id']=='z-target' for d in planner.data['documents'])
    assert all('row_count' not in table for table in planner.data['database_schema']['tables'])


def test_context_budget_and_truncation_keep_source_ranges(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    ids = [f'manual-{i:02}' for i in range(12)]
    for document_id in ids:
        store.ingest(('深海机器人检修说明。'*1000).encode(),document_id=document_id,
                     title='检修说明',modality='txt',filename='manual.txt')
    agent.query('深海机器人检修 '+ ' '.join(ids))
    selected = planner.data['documents']
    assert len(selected) == 12
    assert sum(len(d['text']) + sum(len(json.dumps(row,ensure_ascii=False))
               for row in d['table_rows']) for d in selected) <= 24000
    assert all(len(d['text']) <= 3000 and d['preview_truncated'] for d in selected)
    for document in selected:
        detail = store.document(document['id'])
        original = {chunk['chunk_id']:chunk for chunk in detail['chunks']}
        assert document['source_sha256'] == detail['sha256']
        for excerpt in document['excerpts']:
            shown = document['text'][excerpt['text_start']:excerpt['text_end']]
            assert shown and original[excerpt['chunk_id']]['text'].startswith(shown)
            assert excerpt['locator'] == original[excerpt['chunk_id']]['source_locator']
            assert 'text' not in excerpt  # Evidence is sent only once.


def test_no_retrieval_match_exposes_only_metadata(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    for i in range(15):
        store.ingest('普通仓库说明。'.encode(),document_id=f'warehouse-{i}',
                     title='仓库',modality='txt',filename='w.txt')
    agent.query('火星辐射的研究结论是什么')
    assert len(planner.data['documents']) == 12
    assert all(d['selection']=='metadata_only_no_retrieval_match' and not d['text']
               and not d['table_rows'] for d in planner.data['documents'])


def test_table_preview_reports_omitted_rows_and_keeps_source(tmp_path):
    agent, store, planner = agent_for(tmp_path)
    wb = Workbook(); sheet = wb.active
    sheet.append(['装备名称','增长率'])
    for i in range(50):
        sheet.append([f'深海机器人{i}',0.17])
    raw=BytesIO(); wb.save(raw)
    store.ingest(raw.getvalue(),document_id='robot-table',title='装备',modality='xlsx',filename='r.xlsx')
    agent.query('robot-table中的深海机器人增长率')
    document = next(d for d in planner.data['documents'] if d['id']=='robot-table')
    assert len(document['table_rows']) == 30
    count = sum(bool(c['metadata'].get('headers')) for c in store.document('robot-table')['chunks'])
    assert document['omitted_table_rows'] == count - 30 > 0
    assert document['preview_truncated']
