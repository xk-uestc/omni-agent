import hashlib
import fitz
import pytest
from backend.native_text_tables import extract_native_text_tables


def pdf(columns=2,*,header=True,multi=False,overlap=False,mixed=False,unit=True):
    doc=fitz.open();p=doc.new_page(width=580,height=400)
    p.insert_text((40,35),'Budget period 2022-2023',fontsize=10)
    xs=[40,220,340,460][:columns]
    if header:
        for c,x in enumerate(xs):p.insert_text((x,65),'Item' if c==0 else 'Amount '+str(c),fontsize=10)
        if multi:
            for c,x in enumerate(xs):p.insert_text((x,82),'Category' if c==0 else str(2030+c),fontsize=10)
    for r,label in enumerate(['Service A','Service B','Service C','Service D']):
        y=110+r*25
        p.insert_text((40,y),label,fontsize=10)
        for c,x in enumerate(xs[1:],1):p.insert_text((x,y),('$' if unit else '')+f'{1000*(r+1)+c:,}',fontsize=10)
        if overlap and r==1:p.insert_text((xs[1],y),'9999',fontsize=10)
        if mixed and r==1:p.insert_text((170,y),'note crosses columns here',fontsize=10)
    raw=doc.tobytes();doc.close();return raw


def extract(raw):return extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('columns',[2,3,4])
def test_native_borderless_columns_real_bboxes(columns):
    raw=pdf(columns);r=extract(raw)
    assert len(r['tables'])==1,r['rejected_tables']
    assert len(r['facts'])==4*(columns-1)
    f=r['facts'][0]
    assert f['row_header']=='Service A' and f['column_header_path']==['Amount 1']
    assert f['raw_value']=='$1,001' and f['unit']=='currency_symbol:$'
    assert f['source_sha256']==hashlib.sha256(raw).hexdigest()
    assert f['calculator_input_eligible'] is False


def test_multiline_external_column_headers_and_years():
    r=extract(pdf(3,multi=True));assert len(r['tables'])==1,r['rejected_tables']
    assert r['facts'][0]['column_header_path']==['Amount 1','2031']
    assert r['facts'][0]['period']=='2031'
    assert r['tables'][0]['scope_status']=='multiple_years_not_assigned_to_cells'


def test_no_explicit_headers_is_candidate_not_fact():
    r=extract(pdf(header=False,unit=False));assert not r['facts']
    assert r['rejected_tables'][0]['reason']=='missing_explicit_column_headers'
    assert r['rejected_tables'][0]['candidate_rows_unverified'][0]['raw_values']==['1,001']


def test_headless_monetary_list_is_annotation_only_no_fabricated_header():
    r=extract(pdf(header=False));assert len(r['facts'])==4
    assert r['tables'][0]['table_kind']=='monetary_key_value_list'
    f=r['facts'][0]
    assert f['column_header_path']==[] and f['column_role']=='native_currency_annotation'
    assert f['currency']=='unknown' and f['currency_symbol']=='$'
    assert f['physical_calculator_input_eligible'] is False
    assert f['period_scope_text'][0]['text']=='Budget period 2022-2023'


def test_no_head_multiple_amount_columns_not_monetary_list():
    r=extract(pdf(3,header=False));assert not r['facts']


def test_headless_single_sentence_amount_not_a_continuous_list():
    d=fitz.open();p=d.new_page();p.insert_text((40,60),'The operating expense is $1,000 annually.')
    raw=d.tobytes();d.close();assert not extract(raw)['facts']


def test_duplicate_entity_rows_are_rejected():
    d=fitz.open();p=d.new_page()
    for y in [70,95,120]:
        p.insert_text((40,y),'Same Entity',fontsize=10);p.insert_text((220,y),'$1,000',fontsize=10)
    raw=d.tobytes();d.close();r=extract(raw)
    assert not r['facts'] and r['rejected_tables'][0]['reason']=='row_header_not_unique'


def test_multiple_source_periods_not_coerced_to_single_year():
    r=extract(pdf(header=False));f=r['facts'][0]
    assert f['period'] is None
    assert '2022-2023' in f['period_scope_text'][0]['text']


def test_currency_symbols_are_not_guessed_iso_codes():
    r=extract(pdf(header=False));f=r['facts'][0]
    assert f['native_amount_domain']=='1001'
    assert f['currency']=='unknown' and f['scale'] is None


@pytest.mark.parametrize('kwargs',[{'overlap':True},{'mixed':True}])
def test_overlap_and_crosscolumn_prose_do_not_certify_whole_table(kwargs):
    r=extract(pdf(**kwargs));assert not r['facts']


def test_unknown_unit_explicit_not_calculator_input():
    r=extract(pdf(unit=False));assert r['facts']
    assert all(f['unit']=='unknown' and f['calculator_input_eligible'] is False for f in r['facts'])


def test_native_missing_ocr_unsupported():
    d=fitz.open();d.new_page();raw=d.tobytes();d.close()
    r=extract(raw);assert not r['facts']
    assert r['rejected_tables'][0]['reason']=='no_native_words_ocr_not_supported'


def test_digest_pin_rejected():
    with pytest.raises(ValueError,match='sha256'):
        extract_native_text_tables(pdf(),page_no=1,expected_source_sha256='0'*64)
