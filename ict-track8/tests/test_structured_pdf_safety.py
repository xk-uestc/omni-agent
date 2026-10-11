"""Security/identity fixtures; these are not real-document accuracy scores."""
import hashlib
import json

import fitz
import pytest

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.formula_conversion import convert_latex_arithmetic
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.pdf_backend import LocalMinerUPdfBackend
from backend.structured_document import html_cells, seal


def pdf_bytes(text='R = x / y. x is input. y is output.'):
    with fitz.open() as p:
        page=p.new_page(width=300,height=300)
        page.insert_text((30,60),text)
        return p.tobytes()


class ObservedBackend:
    def parse(self, raw):
        structure=seal([{'kind':'formula','page_no':1,'order':0,'bbox':[30,40,200,65],
                         'text':r'R=\frac{x}{y}'},
                        {'kind':'text','page_no':1,'order':1,'bbox':[30,70,220,110],
                         'text':'x is input. y is output.'}],[(300,300)],hashlib.sha256(raw).hexdigest(),
                        {'name':'safety_fixture','version':'1'})
        structure['cost']={'pages_read':1,'seconds':0}
        return structure


def test_observed_formula_cannot_become_calculation_or_generation_evidence(tmp_path):
    store=KnowledgeStore(tmp_path,pdf_backend=ObservedBackend())
    result=store.ingest(pdf_bytes(),document_id='p',title='formula',modality='pdf',filename='p.pdf')
    records=store.formula_records('p',expected_sha256=result['sha256'])
    assert len(records)==1 and records[0]['calculator_input_eligible'] is False
    agent=DependencyAgent(None,store)
    with pytest.raises(DependencyPlanError,match='审核'):
        agent.execute('document_formula',{'document_id':'p','label':'R'},{}, {})
    hit=next(h for h in store.search('R x y',top_k=20) if h.metadata.get('structured_element_id'))
    selected,omitted=store._generation_citations([{'citation_id':1,**hit.to_dict()}])
    assert selected==[] and omitted[0]['reason']=='structured_recognition_not_reviewed'


def test_private_review_binds_image_formula_and_parameters_then_revision_invalidates(tmp_path):
    store=KnowledgeStore(tmp_path/'store',pdf_backend=ObservedBackend())
    result=store.ingest(pdf_bytes(),document_id='p',title='formula',modality='pdf',filename='p.pdf')
    formula=store.formula_records('p',expected_sha256=result['sha256'])[0]
    image=store.visual_asset('p',page_no=1,expected_source_sha256=result['sha256'],crop_display_pt=formula['bbox'])
    receipt={'document_id':'p','element_id':formula['element_id'],'source_sha256':result['sha256'],
             'observation_sha256':formula['observation_sha256'],'region_png_sha256':hashlib.sha256(image.png_bytes).hexdigest(),
             'visually_verified':True,'reviewer':'security fixture, not accuracy evidence','label':'R','expression':'x/y',
             'parameter_descriptions':{'x':{'element_id':'e00001','quote':'x is input.'},
                                       'y':{'element_id':'e00001','quote':'y is output.'}}}
    path=tmp_path/'review.json';path.write_text(json.dumps(receipt));path.chmod(0o600)
    path.chmod(0o644)
    with pytest.raises(PermissionError):store.review_formula('p',formula['element_id'],receipt_path=path)
    path.chmod(0o600)
    receipt['region_png_sha256']='0'*64;path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='visual_region'):store.review_formula('p',formula['element_id'],receipt_path=path)
    receipt['region_png_sha256']=hashlib.sha256(image.png_bytes).hexdigest();path.write_text(json.dumps(receipt))
    reviewed=store.review_formula('p',formula['element_id'],receipt_path=path)
    assert reviewed['calculator_input_eligible'] is True
    formula_result=DependencyAgent(None,store).execute('document_formula',{'document_id':'p','label':'R'},{},{})
    assert formula_result['expression']=='x/y' and formula_result['parameters']==['x','y']
    store.ingest(pdf_bytes('R = x + y.'),document_id='p',title='formula',modality='pdf',filename='p.pdf')
    with pytest.raises(SourceRevisionError):store.formula_records('p',expected_sha256=result['sha256'])
    new=store.document('p');assert all(e['review_status']=='requires_review' for e in new['elements'])
    store.delete('p',expected_sha256=new['sha256'])
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM document_elements').fetchone()[0]==0


@pytest.mark.parametrize('source',[r'\sum_{n=1}^{k}x',r'\int x',r'\sqrt{x}',r'2x',r'x(y)',r'\input{secret}',r'x.__class__'])
def test_unsupported_latex_never_computes(source):
    with pytest.raises((ValueError,SyntaxError)):convert_latex_arithmetic(source)


def test_explicit_subset_and_table_spans_keep_identity():
    candidate=convert_latex_arithmetic(r'\frac{x_1}{y}+z^{2}')
    assert candidate['parameters']==['x_1','y','z'] and candidate['calculator_input_eligible'] is False
    cells=html_cells('<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>')
    assert [(c['row'],c['column'],c['row_span']) for c in cells]==[(0,0,2),(0,1,1),(1,1,1)]
    with pytest.raises(ValueError):html_cells('<table><tr><td rowspan="99999">A</td></tr></table>')


def test_document_budget_refuses_incomplete_ingest_before_starting_parser(tmp_path):
    backend=LocalMinerUPdfBackend('/bin/true',tmp_path,page_budget=1)
    with fitz.open() as pdf:
        pdf.new_page();pdf.new_page();raw=pdf.tobytes()
    store=KnowledgeStore(tmp_path/'store',pdf_backend=backend)
    with pytest.raises(ValueError,match='page_budget'):
        store.ingest(raw,document_id='p',title='p',modality='pdf',filename='p.pdf')
    assert store.list_documents()==[]
