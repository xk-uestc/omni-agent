from copy import deepcopy
import hashlib
import fitz
import pytest
from backend.pdf_amount_verification import amount,PdfAmountVerificationAgent


@pytest.mark.parametrize('text,value,currency',[
    ('$2,000','2000','$'),('USD 2000.00','2000.00','USD'),('($12.50)','-12.50','$'),
    ('€1,234.50','1234.50','EUR'),('RMB 200','200','CNY'),('￥200','200','¥'),('£-10.00','-10.00','GBP'),
    ('-$10','-10','$')])
def test_exact_decimal_and_explicit_currency(text,value,currency):
    assert amount(text)=={'currency':currency,'value':value}


@pytest.mark.parametrize('text',['$2.000','$2,00','2000','$1.234,50','$1 000','-$-10','($-10)',
    '$1,000 $2,000','USD 1e3','$NaN','$Infinity',None])
def test_no_guessed_locale_missing_currency_or_multiple_amounts(text):assert amount(text) is None


def cell(text='$2,000',sha='a'*64):
    return {'text':text,'original_geometry':{'pdf_geometry':{'source_pdf_sha256':sha,'page_no':1,
        'pdf_highlight_eligible':True,'fitz_unrotated_polygon_pt':[[0,0],[100,0],[100,20],[0,20]]}}}


def verify(text,native,*,bbox=(10,2,70,18)):
    return PdfAmountVerificationAgent.check(cell(text),[(*bbox,native)],'a'*64,1,'a'*64)


def test_matches_conflicts_currency_and_ambiguous_ocr_never_replace_text():
    assert verify('$2,000.00','$2000')['status']=='matched'
    assert verify('$200','$2,000')['reason']=='amount_value_mismatch'
    assert verify('€2,000','GBP 2,000')['reason']=='currency_mismatch'
    assert verify('USD 2,000','$2,000')['reason']=='currency_identity_unspecified'
    assert verify('$2.000','$2.000')['reason']=='native_amount_ambiguous'
    check=verify('$2.000','$2,000')
    assert check['status']=='needs_review' and check['ocr_text']=='$2.000' and check['native_value']['value']=='2000'
    assert verify('','$2,000')['reason']=='ocr_amount_ambiguous_or_missing'
    assert verify('$2,000','$2.000')['reason']=='native_amount_ambiguous'


def test_native_crossing_beyond_rounding_and_multiple_values_rejected():
    assert verify('$2,000','$2,000',bbox=(10,2,70,20.4))['status']=='matched'
    assert verify('$2,000','$2,000',bbox=(10,2,70,21))['reason']=='native_word_crosses_cell_boundary'
    check=PdfAmountVerificationAgent.check(cell(),[(10,2,35,18,'$2,000'),(40,2,80,18,'$1,000')],'a'*64,1,'a'*64)
    assert check['reason']=='native_amount_ambiguous'


@pytest.mark.parametrize('mutation',['source','page','eligible','nan','bowtie','zero'])
def test_source_binding_and_polygon_validation(mutation):
    candidate=cell();mapping=candidate['original_geometry']['pdf_geometry']
    if mutation=='source':mapping['source_pdf_sha256']='b'*64
    if mutation=='page':mapping['page_no']=2
    if mutation=='eligible':mapping['pdf_highlight_eligible']=False
    if mutation=='nan':mapping['fitz_unrotated_polygon_pt'][0][0]=float('nan')
    if mutation=='bowtie':mapping['fitz_unrotated_polygon_pt']=[[0,0],[100,20],[100,0],[0,20]]
    if mutation=='zero':mapping['fitz_unrotated_polygon_pt']=[[0,0]]*4
    check=PdfAmountVerificationAgent.check(candidate,[(10,2,70,18,'$2,000')],'a'*64,1,'a'*64)
    assert check['status']=='unavailable'


def test_real_native_pdf_text_is_independent_and_page_bound_without_mutation():
    with fitz.open() as document:
        page=document.new_page(width=200,height=100);page.insert_text((20,30),'$2,000',fontsize=10)
        raw=document.tobytes()
    sha=hashlib.sha256(raw).hexdigest();candidate=cell('$2.000',sha)
    candidate['original_geometry']['pdf_geometry']['fitz_unrotated_polygon_pt']=[[10,10],[110,10],[110,40],[10,40]]
    structure={'status':'structure_observed','page_no':1,'source_pdf_sha256':sha,'body_cells':[[candidate]],'cell_values_verified':False}
    before=deepcopy(structure)
    result=PdfAmountVerificationAgent().run(raw,[structure])[0]
    assert result['body_cells'][0][0]['amount_check']['status']=='needs_review'
    assert result['body_cells'][0][0]['amount_check']['native_text']=='$2,000'
    assert structure==before and result['cell_values_verified'] is False
    with fitz.open() as document:
        document.new_page(width=200,height=100);blank=document.tobytes()
    blank_sha=hashlib.sha256(blank).hexdigest()
    structure['source_pdf_sha256']=blank_sha;candidate['original_geometry']['pdf_geometry']['source_pdf_sha256']=blank_sha
    result=PdfAmountVerificationAgent().run(blank,[structure])[0]
    assert result['body_cells'][0][0]['amount_check']['reason']=='native_reference_unavailable'
