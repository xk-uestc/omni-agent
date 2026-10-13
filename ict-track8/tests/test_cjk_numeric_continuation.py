"""Fresh native Chinese prose line wraps; no model calls or benchmark gold."""
from copy import deepcopy
import hashlib

import fitz
import pytest

from backend.answer_contract import native_source_only_contract_valid
from backend.chunk_cleaning import DocumentChunker
from backend.grounded_generation import GroundedGenerator
from backend.native_continuation import choose_cjk_numeric_continuation
from backend.pdf_native_context import extract_native_page_context
from backend.responses_client import GenerationError


SHA = 'a' * 64


def block(identifier, text, box):
    text = DocumentChunker.clean_text(text)
    return {'block_id': identifier, 'native_block_id': identifier,
            'normalized_text': text, 'bbox_fitz_unrotated_pt': box,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'native_lines': [{'text': text, 'bbox': box,
                              'native_style': (('ExampleFont', 12.0, 4),),
                              'native_direction': (1.0, 0.0)}]}


def pair(number='73', counter='件'):
    lead = block(1, '按照年度设备运行管理记录，全厂累计处理' + number,
                 [40, 70, 430, 82])
    follow = block(2, counter + '作业，全部通过核对。其他信息不纳入本句。',
                   [40, 99, 420, 111])
    return [lead, follow]


def read(blocks, max_chars=1800):
    order = {'source_sha256': SHA, 'block_ids': [item['block_id'] for item in blocks],
             'column_count': 1}
    return choose_cjk_numeric_continuation(blocks[0], blocks, order, max_chars=max_chars)


@pytest.mark.parametrize('number,counter', [('73', '件'), ('248', '次'), ('16.5', '吨')])
def test_cjk_number_wrap_preserves_actual_literal_counter_and_first_closed_sentence(number, counter):
    blocks = pair(number, counter)
    result = read(blocks)
    assert result['text'] == blocks[0]['normalized_text'] + '\n' + counter + '作业,全部通过核对。'
    assert result['source_sha256'] == SHA
    assert result['continuation_policy_version'] == 'native-cjk-numeric-unit-wrap-v1'
    assert result['calculator_input_eligible'] is False
    assert result['members'][1]['source_range'] == [0, len(counter + '作业,全部通过核对。')]


@pytest.mark.parametrize('change', ['font', 'no_style', 'direction', 'indented', 'far_away',
                                  'independent_prose', 'heading', 'label', 'no_closure',
                                  'hash', 'competing_block'])
def test_cjk_wrap_requires_fresh_unambiguous_native_prose_continuity(change):
    blocks = pair()
    if change == 'font':
        blocks[1]['native_lines'][0]['native_style'] = (('DifferentFont', 12.0, 4),)
    elif change == 'no_style':
        blocks[0]['native_lines'][0].pop('native_style')
    elif change == 'direction':
        blocks[1]['native_lines'][0]['native_direction'] = (0.0, 1.0)
    elif change == 'indented':
        blocks[1] = block(2, blocks[1]['normalized_text'], [66, 99, 420, 111])
    elif change == 'far_away':
        blocks[1] = block(2, blocks[1]['normalized_text'], [40, 199, 420, 211])
    elif change == 'independent_prose':
        blocks[1] = block(2, '另一个部门的记录采用独立口径。', [40, 99, 420, 111])
    elif change == 'heading':
        blocks[1]['is_heading'] = True
    elif change == 'label':
        blocks[0] = block(1, '设备数量73', [40, 70, 430, 82])
    elif change == 'no_closure':
        blocks[1] = block(2, '件作业且还需要继续核对', [40, 99, 420, 111])
    elif change == 'hash':
        blocks[1]['text_sha256'] = 'b' * 64
    elif change == 'competing_block':
        blocks.append(block(3, '另一个重叠的原始记录。', [40, 100, 420, 112]))
    assert read(blocks) is None


def test_no_shortened_suffix_when_complete_cjk_continuation_exceeds_budget():
    blocks = pair()
    assert read(blocks, max_chars=len(blocks[0]['normalized_text']) + 3) is None


def test_existing_relation_gate_still_rejects_changed_number_or_removed_condition():
    blocks = pair()
    blocks[0] = block(1, '仅限通过复核的年度设备记录，全厂累计处理73', [40, 70, 430, 82])
    original = read(blocks)['text']
    good = {'abstain': False, 'claims': [{'text': original,
             'support': [{'citation_id': 1, 'quote': original}]}]}
    assert GroundedGenerator.validate(good, {1: original})
    for text in (original.replace('73', '99'), '全厂累计处理73\n件作业'):
        wrong = deepcopy(good)
        wrong['claims'][0]['text'] = text
        with pytest.raises(GenerationError):
            GroundedGenerator.validate(wrong, {1: original})


def test_actual_pdf_rehydrates_number_and_counter_with_replayable_provenance():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    anchor = '按照年度设备运行管理记录，全厂累计处理73'
    page.insert_text((40, 90), anchor, fontname='china-s', fontsize=12)
    page.insert_text((40, 126), '件作业，全部通过核对。', fontname='china-s', fontsize=12)
    raw = doc.tobytes()
    doc.close()
    result = extract_native_page_context(raw, 1, DocumentChunker.clean_text(anchor), max_chars=3200)
    assert result and '73\n件作业' in result['text']
    assert result['extraction_version'] == 'original-native-complete-block-context-v6'
    assert result['mode'] == 'original_native_complete_continuation'
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert native_source_only_contract_valid(result)
    forged = deepcopy(result)
    forged['continuation_policy_version'] = 'unverified'
    assert not native_source_only_contract_valid(forged)
    assert extract_native_page_context(raw, 1, DocumentChunker.clean_text(anchor), max_chars=3200) == result
