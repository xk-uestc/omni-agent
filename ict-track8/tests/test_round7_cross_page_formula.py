import hashlib

import fitz
import pytest

from backend.native_formula_pages import extract_cross_page_formulas
from backend.knowledge_store import KnowledgeStore
from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def pdf(head='Efficiency = useful /',tail='(input + losses)', *, ypos=740, xpos=40, fontsize=11, overlap=False):
    document=fitz.open()
    first=document.new_page();first.insert_text((40,60),'Engineering specification.')
    first.insert_text((40,ypos),head,fontsize=11)
    second=document.new_page();second.insert_text((xpos,60),tail,fontsize=fontsize)
    second.insert_text((40,110),'Parameter definitions follow below.')
    if overlap:second.insert_text((400,60),'Other column.')
    raw=document.tobytes();document.close();return raw


def extract(raw):
    return extract_cross_page_formulas(raw,label='Efficiency',expected_sha256=hashlib.sha256(raw).hexdigest())


def test_adjacent_open_formula_joins_only_explicit_native_math():
    raw=pdf();result=extract(raw)
    assert len(result)==1
    assert result[0]['expression']=='useful/(input+losses)'
    assert set(result[0]['parameters'])=={'useful','input','losses'}
    assert [part['page_no'] for part in result[0]['cross_page_literal_proof']['parts']]==[1,2]
    assert result[0]['sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('kwargs',[{'head':'Efficiency = useful / input'},
    {'tail':'unrelated sentence begins here'}, {'tail':'input = 100'},
    {'ypos':150}, {'xpos':150}, {'fontsize':9}, {'overlap':True},
    {'tail':'__import__(input)'}, {'head':'Efficiency = useful + (','tail':'input + losses'}])
def test_unclosed_or_unrelated_or_ambiguous_pages_never_become_formula(kwargs):
    assert extract(pdf(**kwargs))==[]


def test_formula_source_hash_is_not_optional():
    with pytest.raises(ValueError,match='source_invalid'):
        extract_cross_page_formulas(pdf(),label='Efficiency',expected_sha256='0'*64)


def test_production_document_formula_tool_reads_pinned_cross_page_original(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge')
    raw=pdf();store.ingest(raw,document_id='spec',title='Engineering',modality='pdf',filename='spec.pdf')
    agent=DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),store)
    result=agent.execute('document_formula',{'document_id':'spec','label':'Efficiency'}, {}, {})
    assert result['expression']=='useful/(input+losses)'
    assert result['sha256']==hashlib.sha256(raw).hexdigest()
    assert len(result['cross_page_literal_proof']['parts'])==2
    assert result['locator']=='page:1-2:native-formula'
