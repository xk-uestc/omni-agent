from copy import deepcopy
from decimal import localcontext
import hashlib

import fitz
import pytest

from backend.native_fraction import (parse_native_fraction_cell, fraction_percentage,
                                     validate_native_fraction_proof, replay_native_fraction_cell)


def proof(value='5/6', header='Attendance'):
    return parse_native_fraction_cell(value, column_header_path=[header],
        column_header_bboxes_display_pt=[[200, 40, 270, 50]],
        bbox_display_pt=[220, 70, 240, 80], source_sha256='a'*64, page_no=1)


@pytest.mark.parametrize('value,answer,exact,rounded', [
    ('5/6', '≈83.33%', ('250','3'), True),
    ('1/8', '12.50%', ('25','2'), False),
    ('0/6', '0.00%', ('0','1'), False),
    ('6/6', '100.00%', ('100','1'), False),
    ('0005/0006', '≈83.33%', ('250','3'), True),
])
def test_exact_rational_and_literal_preserved(value, answer, exact, rounded):
    with localcontext() as context:
        context.prec = 1
        result = fraction_percentage(proof(value))
    assert result['answer'] == answer and result['rounded'] is rounded
    assert result['exact_fraction'] == dict(zip(('numerator','denominator'),exact))
    assert result['operands'] == [value]
    assert result['physical_calculator_input_eligible'] is False


@pytest.mark.parametrize('places', range(7))
def test_supported_precision_and_half_up(places):
    # 100 * 1 / (200*10**places) lies at an exact half display unit.
    result = fraction_percentage(proof(f'1/{200*10**places}'), decimal_places=places)
    assert result['answer'] == '≈' + ('1' if places == 0 else '0.'+'0'*(places-1)+'1')+'%'
    assert result['rounded'] is True


@pytest.mark.parametrize('value', ['5 / 6','5/ 6','5 /6','-5/6','+5/6','1.5/6',
                                  '1/2/2024','1/6 note','1⁄6','½','5／6',
                                  '1/0','0/0','7/6','1234567890123/9999999999999'])
def test_malformed_or_out_of_bounds_counts_rejected(value):
    with pytest.raises(ValueError): proof(value)


@pytest.mark.parametrize('header', ['Date','Version','Odds','Ratio','Completion rate',
                                   'Attendance %','Attendance date','Attendance odds',
                                   'Amount','Total','完成率','Attendance USD price'])
def test_ambiguous_slash_domains_or_unknown_units_rejected(header):
    with pytest.raises(ValueError): proof(header=header)


@pytest.mark.parametrize('header', ['Attendance','Meetings attended','Tasks completed',
                                   'Completion count','出席次数','完成次数'])
def test_explicit_count_headers_only(header):
    assert proof(header=header)['unit'] == 'count_fraction'


@pytest.mark.parametrize('precision', [True,-1,7,1.5,'2',None])
def test_precision_rejected(precision):
    with pytest.raises(ValueError): fraction_percentage(proof(),decimal_places=precision)


@pytest.mark.parametrize('field,value', [
    ('numerator','6'),('denominator','7'),('unit','unknown'),('value_kind','native_aligned_cell_literal'),
    ('calculator_input_eligible',True),('binding','guessed'),('scale','1000')])
def test_forged_proof_rejected(field,value):
    changed = proof(); changed[field] = value
    with pytest.raises(ValueError): validate_native_fraction_proof(changed)
    with pytest.raises(ValueError): fraction_percentage(changed)


def native_pdf():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((200,50),'Meetings attended')
        page.insert_text((220,80),'5/6')
        raw = pdf.tobytes()
    with fitz.open(stream=raw,filetype='pdf') as pdf:
        page = pdf[0]
        words = page.get_text('words')
        cell = next(w for w in words if w[4]=='5/6')
        header = [w for w in words if w[4] in ('Meetings','attended')]
        box = fitz.Rect(min(w[0] for w in header),min(w[1] for w in header),
                        max(w[2] for w in header),max(w[3] for w in header))
        parsed = parse_native_fraction_cell('5/6',column_header_path=['Meetings attended'],
            column_header_bboxes_display_pt=[list(box)],bbox_display_pt=list(cell[:4]),
            source_sha256=hashlib.sha256(raw).hexdigest(),page_no=1)
    return raw,parsed


def test_replay_original_native_header_and_cell():
    raw, parsed = native_pdf()
    assert replay_native_fraction_cell(raw, parsed) == parsed
    assert fraction_percentage(parsed)['answer'] == '≈83.33%'


def test_replay_cannot_accept_mutated_bytes_or_wrong_page():
    raw, parsed = native_pdf()
    with pytest.raises(ValueError,match='source_replay'): replay_native_fraction_cell(raw+b'x',parsed)
    parsed['page_no'] = 2
    with pytest.raises(ValueError,match='page_replay'): replay_native_fraction_cell(raw,parsed)


def test_replay_rejects_bbox_or_header_text_forgery():
    raw, parsed = native_pdf()
    changed = deepcopy(parsed); changed['bbox_display_pt'][0] += 1
    with pytest.raises(ValueError,match='cell_replay'): replay_native_fraction_cell(raw,changed)
    changed = deepcopy(parsed); changed['column_header_path']=['Attendance']
    with pytest.raises(ValueError,match='header_replay'): replay_native_fraction_cell(raw,changed)


@pytest.mark.parametrize('box', [[0,0,0,1],[220,70,240,float('nan')],[220,70,240,True]])
def test_invalid_geometry_rejected(box):
    p = proof(); p['bbox_display_pt'] = box
    with pytest.raises(ValueError): validate_native_fraction_proof(p)


def test_competing_or_below_cell_header_is_not_bound():
    p = proof(); p['column_header_bboxes_display_pt'] = [[20,40,50,50]]
    with pytest.raises(ValueError): validate_native_fraction_proof(p)
    p = proof(); p['column_header_bboxes_display_pt'] = [[200,90,270,100]]
    with pytest.raises(ValueError): validate_native_fraction_proof(p)
