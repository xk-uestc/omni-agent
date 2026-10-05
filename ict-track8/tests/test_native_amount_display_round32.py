"""Original column units must survive literal and computed amount display."""
from copy import deepcopy

import fitz
import pytest

from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic


def original_facts(header='Amount ($ bn)'):
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        page.insert_text((40, 40), 'Unseen allocation table', fontsize=10)
        page.insert_text((40, 70), 'Region', fontsize=10)
        page.insert_text((260, 70), header, fontsize=10)
        for i, (label, value) in enumerate([
                ('North', '4.1'), ('South', '2.6'), ('Total', '6.7'), ('Quarter', '2.05')]):
            page.insert_text((40, 100+i*25), label, fontsize=10)
            page.insert_text((350-fitz.get_text_length(value, fontsize=10), 100+i*25), value, fontsize=10)
        page.insert_text((40, 270), 'Unrelated note: billions of historical transactions', fontsize=10)
        raw = document.tobytes()
    facts = extract_native_text_tables(raw, page_no=1)['facts']
    return {fact['row_header']: fact for fact in facts}


@pytest.mark.parametrize('header,symbol,suffix', [
    ('Amount ($ bn)', '$', ' bn'), ('Amount ($ million)', '$', ' million'),
    ('Amount ($ thousand)', '$', ' thousand'), ('Amount USD million', '', ' USD million'),
    ('Amount EUR billion', '', ' EUR billion'), ('Amount ($)', '$', ''),
])
@pytest.mark.parametrize('operation,labels,number', [
    ('lookup', ['North'], '4.1'), ('sum', ['North', 'South'], '6.7'),
    ('difference', ['North', 'South'], '1.5'),
    ('difference', ['South', 'North'], '-1.5'),
    ('absolute_difference', ['South', 'North'], '1.5'),
])
def test_own_column_amount_scope_is_visible_without_mutating_literals(header, symbol, suffix, operation, labels, number):
    facts = original_facts(header)
    selected = [facts[label] for label in labels]
    pinned = deepcopy(selected)
    computed = annotation_arithmetic(selected, operation)
    assert computed['answer'] == symbol+number+suffix
    assert computed['numeric_result'] == number
    assert computed['operands'] == [fact['raw_value'] for fact in pinned]
    assert computed['scale'] == selected[0]['scale']
    assert computed['physical_calculator_input_eligible'] is False
    assert selected == pinned


def test_scaled_amount_ratio_and_percentage_remain_dimensionless():
    facts = original_facts()
    ratio = annotation_arithmetic([facts['North'], facts['Quarter']], 'ratio')
    percentage = annotation_arithmetic([facts['North'], facts['Total']], 'percentage')
    assert ratio['answer'] == '2' and ratio['unit'] == 'ratio' and ratio['scale'] is None
    assert percentage['answer'] == '≈61.19%' and percentage['unit'] == 'percent'
    assert percentage['scale'] is None


@pytest.mark.parametrize('mutation', ['missing_scale_proof', 'wrong_scale', 'wrong_header', 'wrong_boxes'])
def test_display_never_repairs_unverified_header_scope(mutation):
    fact = original_facts()['North']
    if mutation == 'missing_scale_proof':
        fact['scale_evidence'] = None
    elif mutation == 'wrong_scale':
        fact['scale'] = '1000000'
    elif mutation == 'wrong_header':
        fact['unit_evidence']['header_path'] = ['Amount ($ million)']
    else:
        fact['unit_evidence']['header_bboxes_display_pt'] = [[0, 0, 1, 1]]
    with pytest.raises(ValueError, match='unit_or_scale_unbound|header_unit_proof_invalid'):
        annotation_arithmetic([fact], 'lookup')


def test_adjacent_literal_suffix_is_not_duplicated_by_display():
    with fitz.open() as document:
        page = document.new_page(width=700, height=600)
        page.insert_text((100, 35), 'FY 2042 Public Assistance to Example Country', fontsize=10)
        for index, (label, amount) in enumerate([
                ('TOTAL FUNDS:', '$25.30'), ('DONATED COMMODITIES:', '$6.00'),
                ('TOTAL FY 2042 ASSISTANCE:', '$31.30')]):
            y = 70+index*18
            page.insert_text((100, y), label, fontsize=10)
            page.insert_text((320, y), amount, fontsize=10)
            page.insert_text((320+fitz.get_text_length(amount, fontsize=10)+.5, y), 'm', fontsize=10)
        raw = document.tobytes()
    facts = extract_native_text_tables(raw, page_no=1)['facts']
    total = next(f for f in facts if f['row_header'] == 'TOTAL FY 2042 ASSISTANCE:')
    assert annotation_arithmetic([total], 'lookup')['answer'] == '$31.30m'


def test_selected_header_cannot_borrow_narrative_multiplier():
    fact = original_facts('Amount ($)')['North']
    assert fact['scale'] is None
    assert annotation_arithmetic([fact], 'lookup')['answer'] == '$4.1'
