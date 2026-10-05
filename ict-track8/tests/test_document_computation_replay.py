"""Mixed-answer composition must replay original cells and server arithmetic."""
from copy import deepcopy

import fitz
import pytest

from backend.document_parts_answer import _replay_computation
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.native_table_question import annotation_arithmetic
from backend.native_text_tables import extract_native_text_tables


def pdf(value='5/6', header='Attendance'):
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((40,35), 'Board attendance during financial year 2030')
        page.insert_text((40,65), 'Name')
        page.insert_text((250,65), header)
        for i,(name,count) in enumerate([('Person A',value),('Person B','6/6'),('Person C','1/6')]):
            page.insert_text((40,95+25*i), name)
            page.insert_text((260,95+25*i), count)
        doc.new_page().insert_text((40,65), 'No attendance table on this page')
        return doc.tobytes()


@pytest.fixture
def component(tmp_path):
    store = KnowledgeStore(tmp_path/'knowledge')
    raw = pdf()
    store.ingest(raw, document_id='board', title='Board', modality='pdf', filename='board.pdf')
    fact = extract_native_text_tables(raw,page_no=1)['facts'][0]
    computation = annotation_arithmetic([fact], 'fraction_percentage', percentage_decimal_places=1)
    child = {'question':'What percentage of meetings did Person A attend, round to one decimal place?',
        'answer_mode':'native_table_model_reviewed', 'answer':computation['answer'],
        'computation':computation,
        'citations':[{'citation_id':1,'document_id':'board','metadata':{
            'document_id':'board','source_sha256':fact['source_sha256'],'page_no':1,'fact':fact}}]}
    return store,child


def test_original_fraction_computation_replays_without_model(component):
    store,child = component
    original = deepcopy(child)
    assert child['answer']=='≈83.3%'
    assert _replay_computation(store,child) is None
    assert child == original


@pytest.mark.parametrize('field,value', [
    ('answer','83.3%'),('answer','100.0%'),('numeric_result','100.0'),
    ('operands',['6/6']),('exact_fraction',{'numerator':'100','denominator':'1'}),
    ('rounded',False),('display_decimal_places',2),('unit','ratio'),
    ('computation_domain','literal_source_quote'),('physical_calculator_input_eligible',True),
])
def test_forged_answer_or_computation_is_rejected(component,field,value):
    store,child = component
    if field=='answer': child['answer']=value
    else: child['computation'][field]=value
    with pytest.raises(ValueError,match='component_computation_replay_failed'):
        _replay_computation(store,child)


@pytest.mark.parametrize('field,value', [
    ('raw_value','6/6'),('row_header','Person B'),('column_header_path',['Tasks completed']),
    ('unit','unknown'),('table_id','another-table'),('page_no',2),('source_sha256','b'*64),
    ('bbox_display_pt',[260,70,280,80]),
])
def test_source_fact_field_or_header_change_is_rejected(component,field,value):
    store,child = component
    child['citations'][0]['metadata']['fact'][field]=value
    with pytest.raises(ValueError,match='component_fact_replay_failed'):
        _replay_computation(store,child)


def test_fraction_header_proof_tampering_is_rejected(component):
    store,child = component
    child['citations'][0]['metadata']['fact']['fraction_proof']['column_header_path']=['Tasks completed']
    with pytest.raises(ValueError,match='component_fact_replay_failed'):
        _replay_computation(store,child)


def test_citation_page_change_cannot_replay_another_page_fact(component):
    store,child = component
    child['citations'][0]['metadata']['page_no']=2
    with pytest.raises(ValueError,match='component_fact_replay_failed'):
        _replay_computation(store,child)


@pytest.mark.parametrize('replacement',[('6/6','Attendance'),('5/6','Tasks completed')])
def test_original_source_cell_or_header_revision_invalidates_pinned_proof(component,replacement):
    store,child = component
    store.ingest(pdf(*replacement),document_id='board',title='Board',modality='pdf',filename='board.pdf')
    with pytest.raises(SourceRevisionError): _replay_computation(store,child)


def test_precision_is_rebound_to_original_component_question(component):
    store,child = component
    child['question']='What percentage of meetings did Person A attend, round to two decimal places?'
    with pytest.raises(ValueError,match='component_computation_replay_failed'):
        _replay_computation(store,child)


def test_growth_cannot_reuse_count_share_computation(component):
    store,child = component
    child['question']='What percentage increase in attendance did Person A have?'
    with pytest.raises(ValueError,match='component_operation_scope_invalid'):
        _replay_computation(store,child)


def test_fraction_cannot_be_recast_as_ordinary_decimal_percentage(component):
    store,child = component
    child['computation']['operation']='percentage'
    with pytest.raises(ValueError): _replay_computation(store,child)


def test_computation_requires_native_tool_answer_mode(component):
    store,child = component
    child['answer_mode']='model_grounded'
    with pytest.raises(ValueError,match='unsupported_component_computation'):
        _replay_computation(store,child)
