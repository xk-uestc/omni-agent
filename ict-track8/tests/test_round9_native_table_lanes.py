"""Native explicit monetary headers isolate tables from competing page layouts."""
import hashlib

import fitz
import pytest

from backend.native_text_tables import extract_native_text_tables


def put(page, x, y, text, size=10):
    page.insert_text((x, y), text, fontsize=size)


def table(page, left=360, edge=690, currency='$', scale='bn', labels=None):
    labels = labels or ['North', 'South', 'Top 20 Allocation Total', 'Other']
    put(page, left+20, 70, 'Area')
    put(page, edge-90, 70, 'Amount Posted')
    put(page, edge-42, 85, f'({currency} {scale})')
    for i,(label,value) in enumerate(zip(labels, ['4.1','2.6','6.7','0.4'])):
        put(page, left, 105+i*25, label)
        put(page, edge-fitz.get_text_length(value,fontsize=10), 105+i*25, value)


def source(kind='chart', rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=720, height=420)
    table(page)
    if kind=='chart':
        # A left chart has different baselines and numeric-column counts. It
        # must not split right-table rows or become part of their row labels.
        for x,y,text in [(35,105,'Chart Alpha $91.0'), (110,119,'Figure 2'),
                         (55,131,'Chart Beta $31.0'), (70,155,'Uses $18.0'),
                         (90,181,'Scale 50'), (180,165,'Chart Gamma 77 88')]:
            put(page,x,y,text)
    elif kind=='duplicate':
        doc.close();doc=fitz.open();page=doc.new_page(width=720,height=420)
        table(page,labels=['North','North','Top 20 Allocation Total','Other'])
    elif kind=='cross_gutter':
        put(page,340,119,'NARRATIVE CROSSING THE LOCAL OUTER GUTTER')
    elif kind=='competing_units':
        put(page,652,85,'EUR')
    elif kind=='unheaded_numeric':
        for i in range(4):put(page,555,105+i*25,str(10+i))
    elif kind=='competing_amount_lanes':
        for i in range(4):put(page,637,105+i*25,str(10+i))
    if rotation:
        target=fitz.open()
        target_page=target.new_page(width=420,height=720)
        target_page.show_pdf_page(target_page.rect,doc,0,rotate=rotation)
        target_page.set_rotation(rotation)
        doc.close();doc=target
    raw=doc.tobytes();doc.close()
    return raw


@pytest.mark.parametrize('rotation', [0,90,270])
def test_chart_on_same_page_does_not_join_explicit_header_lane(rotation):
    raw=source(rotation=rotation)
    result=extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert len(result['tables'])==1 and len(result['facts'])==4
    facts=result['facts']
    assert [f['row_header'] for f in facts]==['North','South','Top 20 Allocation Total','Other']
    assert [f['raw_value'] for f in facts]==['4.1','2.6','6.7','0.4']
    assert all(f['column_header_path']==['Amount Posted','($ bn)'] for f in facts)
    assert all(f['scale']=='1000000000' and f['unit']=='currency_symbol:$' for f in facts)
    assert all(f['unit_evidence']['binding']=='own_explicit_column_header_only' for f in facts)
    assert not any(f['calculator_input_eligible'] for f in facts)
    assert extract_native_text_tables(raw,page_no=1)==result
    with fitz.open(stream=raw,filetype='pdf') as doc:
        p=doc[0]
        for fact in facts:
            word=next(w for w in p.get_text('words') if w[4]==fact['raw_value'])
            assert fact['bbox_display_pt']==pytest.approx(list(fitz.Rect(word[:4])*p.rotation_matrix))
            assert p.rect.contains(fitz.Rect(fact['bbox_display_pt']))


def test_separate_tables_do_not_borrow_currency_or_multiplier():
    with fitz.open() as doc:
        p=doc.new_page(width=1000,height=420)
        table(p,left=30,edge=335,currency='$',scale='bn')
        table(p,left=540,edge=950,currency='EUR',scale='million')
        raw=doc.tobytes()
    report=extract_native_text_tables(raw,page_no=1)
    assert len(report['tables'])==2 and len(report['facts'])==8
    units={table['facts'][0]['unit']:table for table in report['tables']}
    assert set(units)=={'currency_symbol:$','currency:EUR'}
    assert {f['scale'] for f in units['currency_symbol:$']['facts']}=={'1000000000'}
    assert {f['scale'] for f in units['currency:EUR']['facts']}=={'1000000'}
    assert units['currency_symbol:$']['table_id']!=units['currency:EUR']['table_id']
    for panel in report['tables']:
        assert all(f['unit_evidence']['header_path']==f['column_header_path'] for f in panel['facts'])


@pytest.mark.parametrize('kind', ['duplicate','cross_gutter','competing_units','unheaded_numeric','competing_amount_lanes'])
def test_competing_or_unbounded_layout_never_certifies_lane(kind):
    report=extract_native_text_tables(source(kind),page_no=1)
    assert not report['facts']


def test_nomination_requires_original_unit_and_hash():
    raw=source()
    with pytest.raises(ValueError,match='sha256_mismatch'):
        extract_native_text_tables(raw,page_no=1,expected_source_sha256='0'*64)
    with fitz.open() as doc:
        p=doc.new_page(width=720,height=420)
        put(p,380,70,'Area');put(p,600,70,'Amount')
        for i,(label,value) in enumerate([('North','4.1'),('South','2.6'),('Top 20 Total','6.7')]):
            put(p,360,105+i*25,label);put(p,670,105+i*25,value)
        raw=doc.tobytes()
    # Existing full-page tables may disclose unknown units. The new monetary
    # lane path must never fabricate a currency or multiplier from a number.
    report=extract_native_text_tables(raw,page_no=1)
    assert all(f['unit']=='unknown' and f['scale'] is None for f in report['facts'])
    assert not any(t.get('local_geometry_proposal') for t in report['tables'])
