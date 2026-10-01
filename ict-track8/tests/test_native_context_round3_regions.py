"""Fresh native region coverage, unrelated to any benchmark gold answers."""
import fitz
import pytest
import json

from backend.pdf_native_context import extract_native_page_context
from backend.evidence_context import text_sha256


def native_pdf(entries):
    document = fitz.open()
    page = document.new_page(width=700, height=900)
    for x, y, text in entries:
        page.insert_text((x, y), text, fontsize=10)
    raw = document.tobytes()
    document.close()
    return raw


def test_literal_blank_paragraph_recovers_complete_scope_in_large_native_block():
    raw = native_pdf([(40, 40, 'Unrelated background.\n \n'
        'Only eligible employees are included.\nThe participation proportion is 42 percent.\n \n'
        + '\n'.join('Unrelated supplementary records are listed independently.' for _ in range(20)))])
    result = extract_native_page_context(raw, 1, 'participation proportion is 42', max_chars=180)
    assert result and result['text'] == ('Only eligible employees are included.\n'
                                          'The participation proportion is 42 percent.')
    assert result['members'][0]['source_range']
    assert result['members'][0]['parent_text_sha256']
    assert result['text_sha256'] == text_sha256(result['text'])
    assert not result['calculator_input_eligible']
    assert result == extract_native_page_context(raw, 1, 'participation proportion is 42', max_chars=180)


def test_full_anchor_crossing_two_blocks_preserves_both_complete_blocks():
    raw = native_pdf([(40, 50, 'The first requirement is certified.'),
                      (40, 75, 'The second requirement is independently reviewed.')])
    anchor = 'requirement is certified.\nThe second requirement'
    result = extract_native_page_context(raw, 1, anchor)
    assert result and len(result['members']) == 2
    assert result['text'].startswith('The first requirement')
    assert result['text'].endswith('independently reviewed.')
    assert extract_native_page_context(raw, 1, anchor, max_chars=40) is None


def test_disjoint_column_anchor_cannot_forge_a_paragraph():
    raw = native_pdf([(40, 50, 'The first requirement is certified.'),
                      (360, 75, 'The second requirement is independently reviewed.')])
    assert extract_native_page_context(raw, 1, 'certified.The second') is None


def test_table_title_recovers_literal_headers_and_whole_rows_without_math_claim():
    entries = [(40, 40, 'Table 4: Example staffing records'),
               (40, 66, 'Name'), (230, 66, 'Year'), (340, 66, 'Units')]
    for index, name in enumerate(('Alice', 'Bob', 'Carol')):
        entries.extend([(40, 86 + 14 * index, name), (230, 86 + 14 * index, str(2001 + index)),
                        (340, 86 + 14 * index, str(3 + index))])
    raw = native_pdf(entries)
    result = extract_native_page_context(raw, 1, 'Example staffing records')
    assert result and result['mode'] == 'original_native_complete_table_region'
    assert all(word in result['text'] for word in ('Name', 'Year', 'Units', 'Alice', 'Carol', '2003'))
    assert not result['calculator_input_eligible']
    assert all(member['bbox_fitz_unrotated_pt'] and member['lines'] for member in result['members'])
    assert extract_native_page_context(raw, 1, 'Example staffing records', max_chars=25) is None


def test_duplicate_anchor_and_absent_native_anchor_fail_closed():
    raw = native_pdf([(40, 50, 'Repeated scope.'), (40, 130, 'Repeated scope.')])
    assert extract_native_page_context(raw, 1, 'Repeated scope') is None
    assert extract_native_page_context(raw, 1, 'Forged chunk metadata') is None
    assert extract_native_page_context(raw, 2, 'Repeated scope') is None


def test_table_region_keeps_forecast_and_units_and_never_crops_them_for_budget():
    entries = [(40, 18, 'Forecast data.'), (40, 43, 'Units: thousands USD.'),
               (40, 66, 'Name'), (230, 66, 'Year'), (340, 66, 'Units')]
    for index, name in enumerate(('Alice', 'Bob', 'Carol')):
        entries.extend([(40, 86 + 14 * index, name), (230, 86 + 14 * index, str(2001 + index)),
                        (340, 86 + 14 * index, str(3 + index))])
    raw = native_pdf(entries)
    result = extract_native_page_context(raw, 1, 'Alice 2001 3')
    assert result and result['mode'] == 'original_native_complete_table_region'
    assert 'Forecast data.' in result['text'] and 'Units: thousands USD.' in result['text']
    assert extract_native_page_context(raw, 1, 'Alice 2001 3', max_chars=len(result['text']) - 1) is None


def test_split_paragraph_keeps_forecast_declaration():
    raw = native_pdf([(40, 40, 'Forecast data.\n \nProjected receipts may reach 42 dollars.\n \n'
        + '\n'.join('Unrelated later supplementary records.' for _ in range(20)))])
    result = extract_native_page_context(raw, 1, 'Projected receipts may reach 42', max_chars=100)
    assert result and result['text'].startswith('Forecast data.')
    assert result['text'].endswith('42 dollars.')


def test_giant_native_block_final_unclosed_paragraph_cannot_become_complete():
    raw = native_pdf([(40, 40, '\n'.join('Unrelated supplementary records.' for _ in range(20))
        + '\n \nThe final paragraph is describing requirements for the participants and their representatives')])
    assert extract_native_page_context(raw, 1, 'requirements for the participants', max_chars=150) is None


def test_store_multiblock_context_is_fresh_replayed_not_metadata_authority(tmp_path):
    from backend.knowledge_store import KnowledgeStore, SourceRevisionError
    raw = native_pdf([(40, 50, 'The first requirement is certified.'),
                      (40, 75, 'The second requirement is independently reviewed.')])
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest(raw, document_id='requirements', title='Requirements', modality='pdf',
                 filename='requirements.pdf', language='eng')
    hits = store.search('first second requirement', top_k=4)
    # Model an upstream chunk joining two real native blocks. Its layout
    # metadata cannot establish authority; only the fresh PDF replay can.
    with store.connect() as connection:
        identifier = hits[0].metadata['chunk_id']
        payload = json.loads(connection.execute('SELECT payload FROM chunks WHERE chunk_id=?',
                                                (identifier,)).fetchone()[0])
        payload['text'] = ('The first requirement is certified.\n'
                           'The second requirement is independently reviewed.')
        payload['metadata']['coordinate_evidence'] = {}
        connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?',
                           (json.dumps(payload), identifier))
    citations, omitted = store._generation_citations([
        {'citation_id': index, **hit.to_dict()} for index, hit in enumerate(hits, 1)])
    assert citations and not omitted
    context = citations[0]['generation_evidence']['native_context']
    assert context['mode'] == 'original_native_complete_paragraph_region'
    assert len(context['members']) == 2
    store._verify_generation_chunks(citations)
    context['members'][0]['bbox_fitz_unrotated_pt'][0] += 1
    with pytest.raises(SourceRevisionError):
        store._verify_generation_chunks(citations)
