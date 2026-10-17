"""Tool/source contract fixtures, not public-document accuracy results."""
import json

import pytest

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from tests.test_visual_table_reader import grid_pdf
from backend.native_formula_pages import extract_native_literal_formulas
import fitz
import hashlib


def stored(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.ingest(grid_pdf(), document_id='grid', title='grid', modality='pdf', filename='grid.pdf')
    return store


def test_literal_source_answer_and_typed_pdf_tool_without_model(tmp_path):
    store = stored(tmp_path)
    answer = store.answer('West 2025 Actual', document_id='grid')
    assert answer['status'] == 'ok' and answer['answer'] == '3578'
    assert answer['answer_mode'] == 'native_table_literal'
    assert answer['trace'][0]['model_requests_attempted'] == 0
    table = store.document('grid')['elements'][0]
    headers = {c['column']:c['text'] for c in table['cells'] if c['is_header']}
    fact = DependencyAgent(None,store).execute('document_cell', {
        'document_id':'grid', 'where':{headers[0]:'West'}, 'column':'2025 Actual'}, {}, {})
    assert fact['value'] == 3578 and fact['bbox_display_pt']
    assert fact['sha256'] == store.document('grid')['sha256']
    assert fact['calculator_input_eligible'] is False  # undeclared physical unit


def test_ambiguity_and_unbound_question_cannot_become_success(tmp_path):
    store = stored(tmp_path)
    store.ingest(grid_pdf(), document_id='other', title='other', modality='pdf', filename='other.pdf')
    assert store.answer('West 2025 Actual')['status'] != 'ok'
    assert store.answer('West 2025 Actual, why?',document_id='grid')['status'] != 'ok'


def test_corrupted_independent_table_cannot_authorize_cached_hit(tmp_path):
    store = stored(tmp_path)
    hit = next(h for h in store.search('West 2025 Actual',top_k=20) if h.metadata.get('native_table_record'))
    with store.connect() as connection:
        row = connection.execute('SELECT element_id,payload FROM document_elements').fetchone()
        table = json.loads(row[1]); table['cells'][-1]['text']='999999'
        connection.execute('UPDATE document_elements SET payload=? WHERE element_id=?',
                           (json.dumps(table),row[0]))
    with pytest.raises(SourceIntegrityError):
        store._generation_citations([{'citation_id':1,**hit.to_dict()}])


def test_boolean_cannot_select_numeric_row_key(tmp_path):
    store = stored(tmp_path)
    with pytest.raises(DependencyPlanError):
        DependencyAgent(None,store).execute('document_cell', {
            'document_id':'grid','where':{'2025 Actual':True},'column':'Region'}, {}, {})


def test_native_formula_proof_rejects_modified_expression_and_scripts(tmp_path):
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((30,60),'Cost = price * rate')
        raw=pdf.tobytes()
    store=KnowledgeStore(tmp_path)
    store.ingest(raw,document_id='formula',title='formula',modality='pdf',filename='formula.pdf')
    formula=DependencyAgent(None,store).execute('document_formula',{'document_id':'formula','label':'Cost'},{},{})
    store.verify_native_formula(formula)
    formula['expression']='price/rate'
    with pytest.raises(SourceIntegrityError):store.verify_native_formula(formula)
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((30,60),'R = x')
        page.insert_text((58,54),'2',fontsize=7)
        scripted=pdf.tobytes()
    assert not extract_native_literal_formulas(scripted,label='R',expected_sha256=hashlib.sha256(scripted).hexdigest())
    with fitz.open() as pdf:
        page=pdf.new_page();page.insert_text((30,60),'Cost = price * rate',render_mode=3)
        hidden=pdf.tobytes()
    assert not extract_native_literal_formulas(hidden,label='Cost',expected_sha256=hashlib.sha256(hidden).hexdigest())
