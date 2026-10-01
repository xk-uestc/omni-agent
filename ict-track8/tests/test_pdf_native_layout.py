"""Native source geometry regressions, independent of benchmark gold answers."""
import hashlib
from io import BytesIO
import json

import fitz
import pytest
from pypdf import PdfReader

from backend.chunk_cleaning import DocumentChunker


def native_pdf(pages):
    document = fitz.open()
    for entries in pages:
        page = document.new_page(width=612, height=792)
        for x, y, text in entries:
            page.insert_text((x, y), text, fontsize=8 if 'column' in text and not text.startswith('Shared') else 10)
    raw = document.tobytes()
    document.close()
    return raw


def extracted(raw):
    return DocumentChunker._extract_pdf_pages(raw, PdfReader(BytesIO(raw)))


def test_two_columns_are_read_down_each_column_and_never_cross_fused():
    raw = native_pdf([[
        (45, 40, 'Shared section title'),
        (45, 100, 'Alpha organization has an independent fact.'),
        (330, 100, 'Beta organization has a different fact.'),
        (45, 130, 'Alpha second paragraph follows its own fact.'),
        (330, 130, 'Beta second paragraph follows its own fact.'),
    ]])
    pages, failed, layouts = extracted(raw)
    assert not failed
    text = pages[0]
    assert text.index('Alpha second') < text.index('Beta organization')
    assert layouts[0]['reading_order']['column_count'] == 2
    assert '|' not in text
    parsed = DocumentChunker().parse_pdf(raw).to_dict()
    assert not any(chunk['content_type'] in {'row', 'table_header'} for chunk in parsed['chunks'])
    for chunk in parsed['chunks']:
        assert not ('Alpha' in chunk['text'] and 'Beta' in chunk['text'])
        assert chunk['metadata']['coordinate_evidence']['source_bbox_fitz_unrotated_pt']


def test_three_columns_with_spanning_header_footer_and_native_block_ids():
    raw = native_pdf([[
        (45, 35, 'Shared header with sufficient width across the newspaper columns and region'),
        (45, 100, 'Left column independent body statement.'),
        (222, 100, 'Middle column distinct body statement.'),
        (399, 100, 'Right column unique body statement.'),
        (45, 130, 'Left column subsequent body statement.'),
        (222, 130, 'Middle column subsequent statement.'),
        (399, 130, 'Right column subsequent statement.'),
        (45, 750, 'Shared footer with sufficient width across the newspaper columns and region'),
    ]])
    pages, _, layouts = extracted(raw)
    text = pages[0]
    assert layouts[0]['reading_order']['column_count'] == 3
    assert text.index('Left column subsequent') < text.index('Middle column distinct')
    assert text.index('Middle column subsequent') < text.index('Right column unique')
    assert text.index('Shared footer') > text.index('Right column subsequent')
    assert len(layouts[0]['reading_order']['block_ids']) == len(set(layouts[0]['reading_order']['block_ids']))


def test_short_indented_lines_do_not_establish_columns():
    raw = native_pdf([[(45, 100, 'Item A'), (330, 120, 'Item B'), (45, 160, 'Item C')]])
    pages, _, layouts = extracted(raw)
    assert layouts[0]['reading_order']['mode'] == 'native_blocks_yx'
    assert pages[0].index('Item A') < pages[0].index('Item B') < pages[0].index('Item C')


def test_separate_nonoverlapping_vertical_sections_are_not_columns():
    raw = native_pdf([[(45, 100, 'First paragraph has sufficient characters for geometry.'),
                      (330, 500, 'Second paragraph has sufficient characters for geometry.')]])
    _, _, layouts = extracted(raw)
    assert layouts[0]['reading_order']['column_count'] == 1


def test_whitespace_never_invents_delimiters_or_table_headers():
    text, _ = DocumentChunker._clean_pdf_page('First body phrase    second body phrase\nAnother phrase    unrelated clause', set())
    assert '|' not in text
    _, blocks = DocumentChunker()._pdf_blocks(text, [])
    assert all(kind != 'table' for kind, *_ in blocks)


def test_explicit_literal_pipe_table_keeps_cells_hashes_and_exact_coordinates():
    raw = native_pdf([[(45, 100, '|Department|Budget|Unit|'),
                      (45, 130, '|Research|12|USD|'), (45, 160, '|Operations|8|USD|')]])
    rows = [chunk for chunk in DocumentChunker().parse_pdf(raw).to_dict()['chunks'] if chunk['content_type'] == 'row']
    assert len(rows) == 2
    for row in rows:
        m = row['metadata']
        structure = m['pdf_table_structure']
        assert structure['status'] == 'verified' and structure['method'] == 'literal_pipe'
        assert structure['column_count'] == 3 and structure['uniform_column_count']
        assert structure['current_page_header'] and not structure['header_inherited']
        assert structure['coordinate_verified']
        assert m['coordinate_evidence']['source_bbox_fitz_unrotated_pt']
        assert m['source_headers'] == ['Department', 'Budget', 'Unit']
        assert all(isinstance(value, str) for value in m['source_row_cells'])
        assert m['source_row_cells_sha256'] == hashlib.sha256(json.dumps(m['source_row_cells'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        expected = m['table_name'] + ' | ' + ' | '.join(m['source_headers']) + '\n' + ' | '.join(
            key + ': ' + value for key, value in zip(m['source_headers'], m['source_row_cells']) if value)
        assert row['text'] == expected


def test_nonuniform_pipe_table_is_preserved_but_not_certified():
    raw = native_pdf([[(45, 100, '|Department|Budget|Unit|'), (45, 130, '|Research|12|')]])
    rows = [chunk for chunk in DocumentChunker().parse_pdf(raw).to_dict()['chunks'] if chunk['content_type'] == 'row']
    assert rows and rows[0]['metadata']['pdf_table_structure']['status'] == 'unverified'


def test_cross_page_header_reuse_survives_but_is_marked_unverified():
    raw = native_pdf([[(45, 100, '|Department|Budget|Unit|')], [(45, 100, '|Research|12|USD|')]])
    rows = [chunk for chunk in DocumentChunker().parse_pdf(raw).to_dict()['chunks'] if chunk['content_type'] == 'row']
    assert len(rows) == 1 and rows[0]['page_no'] == 2
    assert rows[0]['metadata']['source_headers'] == ['Department', 'Budget', 'Unit']
    assert rows[0]['metadata']['pdf_table_structure']['header_inherited']
    assert rows[0]['metadata']['pdf_table_structure']['status'] == 'unverified'


def test_native_page_and_chunk_identity_is_repeatable():
    raw = native_pdf([[(45, 100, 'Left organization independent body statement.'),
                      (330, 100, 'Right organization independent body statement.')]])
    first = DocumentChunker().parse_pdf(raw, document_id='same').to_dict()
    second = DocumentChunker().parse_pdf(raw, document_id='same').to_dict()
    assert first == second


def test_wrapped_comma_join_keeps_original_native_block_coordinates():
    raw = native_pdf([[(45, 100, 'The committee met,\nand approved the original plan.')]])
    chunks = DocumentChunker().parse_pdf(raw).to_dict()['chunks']
    chunk = next(c for c in chunks if 'approved' in c['text'])
    assert 'met, and approved' in chunk['text']
    assert chunk['metadata']['coordinate_evidence']['bbox_status'] == 'exact_block'
    assert chunk['metadata']['coordinate_evidence']['source_bbox_fitz_unrotated_pt']
