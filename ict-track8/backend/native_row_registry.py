"""Original-PDF aligned rows; geometry is provenance, never semantic authority."""
from copy import deepcopy
import hashlib
import re
import fitz
from .chunk_cleaning import DocumentChunker
from .native_table_chain import probe
from .pdf_native_context import _aligned_native_cells


def extract_rows(raw, *, document_id):
    if not isinstance(document_id, str) or not document_id or len(raw) > 20 * 1024 * 1024:
        raise ValueError('native_rows_source_budget_or_id')
    proposal = probe(raw)
    if proposal['status'] == 'budget_exceeded':
        raise ValueError('native_rows_page_budget')
    records, pages = [], {}
    with fitz.open(stream=raw, filetype='pdf') as document:
        for chain_index, chain in enumerate(proposal['candidates']):
            for context in chain['pages']:
                pno = context['page_no']; page = document[pno - 1]; page.set_rotation(0)
                by_id = {b['block_id']: b for b in DocumentChunker._pdf_text_blocks(page)}
                header = by_id.get(context['header_block_id'])
                labels = _aligned_native_cells(header) if header else None
                if not labels or len({c['text'] for c in labels}) != len(labels):
                    raise ValueError('native_rows_header_ambiguous')
                pages[pno] = {'page_no': pno, 'text': context['text'],
                    'text_sha256': context['text_sha256'], 'members': deepcopy(context['members'])}
                for block_id in context['row_block_ids']:
                    block = by_id.get(block_id); cells = _aligned_native_cells(block) if block else None
                    if not cells or len(cells) != len(labels) or any(
                            abs(a['bbox'][0] - b['bbox'][0]) > 5 for a, b in zip(labels, cells)):
                        raise ValueError('native_rows_geometry_mismatch')
                    fields = []
                    for index, (label, cell) in enumerate(zip(labels, cells)):
                        value = re.fullmatch(r'(?P<qualifier>[<>≤≥*]?)\s*(?P<number>[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s+(?P<unit>[^\s]+)', cell['text'])
                        fields.append({'column_index': index, 'header': label['text'],
                            'header_bbox_pt': deepcopy(label['bbox']), 'text': cell['text'],
                            'bbox_pt': deepcopy(cell['bbox']),
                            'numeric_annotation': value.groupdict() if value else None,
                            'calculator_input_eligible': False})
                    records.append({'row_id': f'{document_id}:C{chain_index + 1}:P{pno}:B{block_id}',
                        'document_id': document_id, 'chain_index': chain_index, 'page_no': pno,
                        'block_id': block_id, 'source_sha256': proposal['source_sha256'],
                        'row_text': block['normalized_text'], 'row_text_sha256': block['text_sha256'],
                        'fields': fields})
    return {'version': 'original-native-aligned-row-registry-v1', 'document_id': document_id,
        'source_sha256': hashlib.sha256(raw).hexdigest(), 'pages': [pages[p] for p in sorted(pages)],
        'records': records, 'header_alignment_verified': bool(records),
        'semantic_sample_identity_verified': False, 'exhaustive_table_closure_verified': False,
        'calculator_input_eligible': False}
