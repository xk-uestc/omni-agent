"""Independent PDF geometry checks; not production answer or accuracy tests."""
import hashlib
from pathlib import Path
import sys

import fitz
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ict-track8'), str(Path(__file__).resolve().parent)]
from sparse_native_amounts import extract


def original(rows, *, suffix_gap=.5, rotation=0):
    with fitz.open() as document:
        page = document.new_page(width=700, height=500)
        for index, (label, amount, suffix) in enumerate(rows):
            y = 70+index*90  # Deliberately isolated, not a continuous table.
            page.insert_text((50, y), label, fontsize=10)
            page.insert_text((320, y), amount, fontsize=10)
            if suffix:
                page.insert_text((320+fitz.get_text_length(amount, fontsize=10)+suffix_gap, y), suffix, fontsize=10)
        page.set_rotation(rotation)
        return document.tobytes()


@pytest.mark.parametrize('left,right', [('$17.20', '$4.10'), ('$2.05', '$7.40'), ('$0.00', '$0.00')])
def test_isolated_totals_keep_own_suffix_and_distinct_source_locations(left, right):
    raw = original([('TOTAL CATEGORY ALPHA:', left, 'm'), ('TOTAL CATEGORY BETA:', right, 'm')])
    result = extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    rows = result['annotations']
    assert [r['raw_value'] for r in rows] == [left+'m', right+'m']
    assert all(r['scale'] == '1000000' for r in rows)
    assert len({r['annotation_id'] for r in rows}) == 2
    assert all(r['table_identity_verified'] is False and r['calculator_input_eligible'] is False for r in rows)
    assert result['production_answer_authority'] is False


def test_identical_labels_remain_distinct_and_unresolved():
    raw = original([('TOTAL CATEGORY:', '$17', 'm'), ('TOTAL CATEGORY:', '$29', 'm')])
    result = extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert len(result['annotations']) == 2
    assert len({r['annotation_id'] for r in result['annotations']}) == 2
    assert all(r['semantic_relation_verified'] is False for r in result['annotations'])


def test_distant_suffix_is_never_borrowed():
    raw = original([('TOTAL CATEGORY:', '$17', 'm')], suffix_gap=15)
    result = extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert result['annotations'][0]['raw_value'] == '$17'
    assert result['annotations'][0]['scale'] is None


def test_ordinary_prose_is_not_a_total_heading_candidate():
    raw = original([('The agency spent:', '$17', 'm')])
    assert extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())['annotations'] == []


def test_mismatched_source_is_rejected():
    raw = original([('TOTAL CATEGORY:', '$17', 'm')])
    with pytest.raises(ValueError, match='source_sha_mismatch'):
        extract(raw, page_no=1, expected_source_sha256='0'*64)


@pytest.mark.parametrize('page', [0, 2, True])
def test_page_scope_is_strict(page):
    raw = original([('TOTAL CATEGORY:', '$17', 'm')])
    with pytest.raises(ValueError, match='page_bounds'):
        extract(raw, page_no=page, expected_source_sha256=hashlib.sha256(raw).hexdigest())


def test_unimplemented_rotation_never_emits_wrong_boxes():
    raw = original([('TOTAL CATEGORY:', '$17', 'm')], rotation=90)
    with pytest.raises(ValueError, match='rotation_not_implemented'):
        extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
