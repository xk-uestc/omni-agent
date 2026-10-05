"""Replayable original-PDF aligned row registry; no semantic/calculation authority.

Keep all physical fields, including text/unit/date cells. A repeated header
proposes a page chain; it does not prove subject identity or exhaustive closure.
This prototype is deliberately isolated from running production evaluations.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

import fitz
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.chunk_cleaning import DocumentChunker
from backend.native_table_chain import probe
from backend.pdf_native_context import _aligned_native_cells


def digest(value):
    return hashlib.sha256(value).hexdigest()


def extract(raw):
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError('row_registry_source_budget')
    proposal = probe(raw)
    if proposal['status'] == 'budget_exceeded':
        raise ValueError('row_registry_page_budget')
    records, pages = [], {}
    with fitz.open(stream=raw, filetype='pdf') as document:
        for chain_index, chain in enumerate(proposal['candidates']):
            for context in chain['pages']:
                pno = context['page_no']
                page = document[pno - 1]
                page.set_rotation(0)
                blocks = DocumentChunker._pdf_text_blocks(page)
                by_id = {block['block_id']: block for block in blocks}
                header = by_id.get(context['header_block_id'])
                labels = _aligned_native_cells(header) if header else None
                if not labels or len({c['text'] for c in labels}) != len(labels):
                    raise ValueError('row_registry_header_ambiguous')
                pages[pno] = {'page_no': pno, 'text': context['text'],
                              'text_sha256': context['text_sha256'],
                              'members': deepcopy(context['members'])}
                for block_id in context['row_block_ids']:
                    block = by_id.get(block_id)
                    cells = _aligned_native_cells(block) if block else None
                    if not cells or len(cells) != len(labels) or any(
                            abs(a['bbox'][0] - b['bbox'][0]) > 5 for a, b in zip(labels, cells)):
                        raise ValueError('row_registry_cell_geometry_mismatch')
                    fields = []
                    for index, (label, cell) in enumerate(zip(labels, cells)):
                        # Header and value stay literal. Parsing a decimal is
                        # only an annotation, never an arithmetic permission.
                        value = re.fullmatch(r'(?P<qualifier>[<>≤≥*]?)\s*(?P<number>[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s+(?P<unit>[^\s]+)', cell['text'])
                        annotation = value.groupdict() if value else None
                        fields.append({'column_index': index, 'header': label['text'],
                            'header_bbox_pt': deepcopy(label['bbox']), 'text': cell['text'],
                            'bbox_pt': deepcopy(cell['bbox']), 'numeric_annotation': annotation,
                            'calculator_input_eligible': False})
                    records.append({'row_id': f'C{chain_index + 1}:P{pno}:B{block_id}',
                        'chain_index': chain_index, 'page_no': pno, 'block_id': block_id,
                        'source_sha256': proposal['source_sha256'],
                        'row_text': block['normalized_text'], 'row_text_sha256': block['text_sha256'],
                        'fields': fields})
    return {'version': 'native-aligned-row-registry-prototype-v1',
        'source_sha256': digest(raw), 'pages': [pages[p] for p in sorted(pages)],
        'records': records, 'header_alignment_verified': bool(records),
        'semantic_sample_identity_verified': False,
        'exhaustive_table_closure_verified': False,
        'calculator_input_eligible': False, 'production_answer_authority': False}


def replay(raw, registry):
    try:
        return registry.get('source_sha256') == digest(raw) and extract(raw) == registry
    except (ValueError, TypeError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != ROOT / 'docs' or args.output.exists():
        parser.error('New project docs output required')
    raw = args.source.read_bytes()
    registry = extract(raw)
    assert replay(args.source.read_bytes(), registry)
    # Generic adversarial replay: no expected answers or corpus identifiers.
    changed = deepcopy(registry)
    if changed['records']:
        changed['records'][0]['fields'][0]['text'] += ' forged'
        assert not replay(raw, changed)
    assert not replay(raw + b'\n', registry)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump({'scope': 'extraction_prototype_not_answer_accuracy',
            'source_replay': True, 'cell_tamper_rejected': True, 'source_tamper_rejected': True,
            'registry': registry}, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'records': len(registry['records']), 'pages': len(registry['pages']),
                      'source_replay': True, 'production_answer_authority': False}))


if __name__ == '__main__':
    main()
