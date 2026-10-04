"""Actual rotated glyph directions, display boxes and own-column scale."""
import hashlib

import fitz
import pytest

from backend.native_text_tables import extract_native_text_tables, column_unit_declaration
from backend.native_table_question import annotation_arithmetic


def source():
    document = fitz.open()
    page = document.new_page(width=600,height=400)
    page.insert_text((40,40), 'Regional allocation 2030', fontsize=10)
    page.insert_text((40,70), 'Region', fontsize=10)
    page.insert_text((280,70), 'Amount ($ bn)', fontsize=10)
    for row,(name,value) in enumerate([('North','4.1'),('South','2.6'),('Total','6.7')]):
        page.insert_text((40,100+row*25), name, fontsize=10)
        page.insert_text((340-fitz.get_text_length(value,fontsize=10),100+row*25), value, fontsize=10)
    return document


def rotated_pdf(rotation):
    with source() as original, fitz.open() as target:
        page = target.new_page(width=400 if rotation in (90,270) else 600,
                               height=600 if rotation in (90,270) else 400)
        page.show_pdf_page(page.rect, original, 0, rotate=rotation)
        page.set_rotation(rotation)
        return target.tobytes()


@pytest.mark.parametrize('rotation', [90,180,270])
def test_declared_rotation_restores_real_native_table_and_display_coordinates(rotation):
    raw = rotated_pdf(rotation)
    report = extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert report['native_layout_orientation']['page_rotation_degrees'] == rotation
    assert len(report['facts']) == 3
    first = report['facts'][0]
    assert first['row_header'] == 'North' and first['raw_value'] == '4.1'
    assert first['unit'] == 'currency_symbol:$' and first['scale'] == '1000000000'
    assert first['unit_evidence']['binding'] == 'own_explicit_column_header_only'
    with fitz.open(stream=raw,filetype='pdf') as document:
        page = document[0]
        actual = next(word for word in page.get_text('words') if word[4] == '4.1')
        assert first['bbox_display_pt'] == pytest.approx(list(fitz.Rect(actual[:4])*page.rotation_matrix))
        box = fitz.Rect(first['bbox_display_pt'])
        assert page.rect.contains(box)
    assert extract_native_text_tables(raw,page_no=1) == report
    computed = annotation_arithmetic([report['facts'][0], report['facts'][2]], 'percentage')
    assert computed['exact_fraction'] == {'numerator':'4100','denominator':'67'}
    assert computed['answer'] == '≈61.19%'


def test_rotation_of_ordinary_horizontal_text_keeps_original_grid_path():
    with source() as document:
        document[0].set_rotation(90)
        raw = document.tobytes()
    report = extract_native_text_tables(raw,page_no=1)
    assert 'native_layout_orientation' not in report
    assert len(report['facts']) == 3


def test_bn_scale_requires_own_explicit_currency_header():
    assert column_unit_declaration(['Amount ($ bn)'])['scale'] == '1000000000'
    assert column_unit_declaration(['Amount USD bn'])['scale'] == '1000000000'
    assert column_unit_declaration(['Amount bn'])['scale'] is None
    with pytest.raises(ValueError,match='scale_declaration'):
        column_unit_declaration(['Amount ($ bn million)'])
