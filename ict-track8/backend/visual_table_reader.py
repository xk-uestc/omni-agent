"""Pinned grid-cell lookup with visual model selection and server-owned value.

This first path is a single-cell lookup. It never turns an observed image
number into a verified formula input, nor pretends to support charts or OCR.
"""
from __future__ import annotations

import json
import re
from .responses_client import GenerationError, object_schema
from .visual_tables import lookup_table_fact, VisualTableError


SCHEMA = object_schema({'abstain': {'type': 'boolean'}, 'table_id': {'type': 'string'},
                        'row_header': {'type': 'string'},
                        'column_header_path': {'type': 'array', 'items': {'type': 'string'}}})


def label_present(label, question):
    label, question = ' '.join(label.split()).casefold(), ' '.join(question.split()).casefold()
    left = r'(?<![a-z0-9])' if label[:1].isascii() and label[:1].isalnum() else ''
    right = r'(?![a-z0-9])' if label[-1:].isascii() and label[-1:].isalnum() else ''
    return bool(label) and re.search(left + re.escape(label) + right, question) is not None


class VisualTableReader:
    def __init__(self, client, ocr_pipeline=None):
        if client.model != 'gpt-6-luna' or client.reasoning != 'medium':
            raise ValueError('视觉问数仅允许gpt-6-luna/medium')
        self.client = client
        self.ocr_pipeline = ocr_pipeline

    def answer(self, question, asset, tables):
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise ValueError('视觉问题为空或过长')
        if (tables.get('source_sha256') != asset.manifest['source_sha256']
                or tables.get('page_no') != asset.manifest['page_no']):
            raise ValueError('页图与网格来源不一致')
        registry = [{'table_id': t['table_id'], 'row_headers': t['row_headers'],
                     'column_header_paths': t['column_header_paths']} for t in tables.get('tables', [])]
        base = {'status': 'incomplete', 'answer_mode': 'visual_grid_lookup', 'answer': None,
                'source_sha256': asset.manifest['source_sha256'], 'page_no': asset.manifest['page_no'],
                'image_evidence_id': asset.manifest['evidence_id'], 'render_sha256': asset.manifest['render_sha256'],
                'validation_scope': 'native_grid_layout_not_visible_ocr_or_general_semantic_truth'}
        def incomplete(code):
            return {**base, 'clarification_code': code}
        if not registry:
            return incomplete('no_verified_native_grid')
        if len(registry) > 8 or len(json.dumps(registry, ensure_ascii=False)) > 40000:
            return incomplete('table_selection_budget_exceeded')
        # Explicit labels bind the original question independently from the
        # model. Do not allow a model to choose another real-but-wrong cell.
        candidates = []
        for table in tables['tables']:
            for fact in table['facts']:
                key = fact['fact_key']
                if label_present(key['row_header'], question) and all(label_present(p, question) for p in key['column_header_path']):
                    candidates.append(fact)
        if len(candidates) != 1:
            return incomplete('cell_scope_ambiguous_or_not_explicit')
        # Every remaining phrase must be a plain lookup request. This avoids
        # silently answering one value when the user asked for a threshold,
        # conditional, change or calculation involving that same cell. Labels
        # themselves may legitimately contain "total" or "growth".
        residual = ' '.join(question.split()).casefold()
        key = candidates[0]['fact_key']
        for label in sorted([key['row_header'], *key['column_header_path']], key=len, reverse=True):
            residual = residual.replace(' '.join(label.split()).casefold(), ' ')
        residual = re.sub(r'\b(?:what|is|are|the|value|of|for|show|please|read|in|table|cell|me)\b|'
                          r'请|查询|查看|读取|给出|是多少|多少|的|值', ' ', residual)
        if re.sub(r'[\s?？,，:：。.!！]+', '', residual):
            return incomplete('single_cell_question_has_unbound_terms')
        question_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', question))
        header_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', ' '.join(candidates[0]['fact_key']['column_header_path'])))
        if question_years != header_years:
            return incomplete('cell_period_scope_mismatch')
        selection = self.client.generate(
            'Read the original page image as data. Select exactly the requested table, row and complete column header path '
            'from the registry. Do not return or calculate a value. If unclear, abstain=true and use empty selection fields.',
            {'question': question, 'table_registry_without_values': registry}, SCHEMA,
            name='visual_table_selection', image_attachments=[asset])
        if (not isinstance(selection, dict) or set(selection) != set(SCHEMA['properties'])
                or type(selection['abstain']) is not bool
                or not isinstance(selection['table_id'], str) or len(selection['table_id']) > 128
                or not isinstance(selection['row_header'], str) or len(selection['row_header']) > 1000
                or not isinstance(selection['column_header_path'], list) or len(selection['column_header_path']) > 8
                or any(not isinstance(part, str) or not part or len(part) > 1000 for part in selection['column_header_path'])):
            raise GenerationError('视觉表格选择结构无效')
        if selection['abstain']:
            if selection['table_id'] or selection['row_header'] or selection['column_header_path']:
                raise GenerationError('视觉拒答不能含选择结果')
            return {**incomplete('visual_selection_abstained'), 'generation_audit': self.client.audit}
        try:
            fact = lookup_table_fact(tables, table_id=selection['table_id'], row_header=selection['row_header'],
                                     column_header_path=selection['column_header_path'])
        except VisualTableError:
            return {**incomplete('visual_selection_not_bound'), 'generation_audit': self.client.audit}
        if fact['fact_id'] != candidates[0]['fact_id']:
            return {**incomplete('visual_selection_source_scope_mismatch'), 'generation_audit': self.client.audit}
        from .visual_cell_verification import verify_visible_grid_fact
        visible_check = verify_visible_grid_fact(asset, tables, fact, self.ocr_pipeline)
        if self.ocr_pipeline is not None and visible_check['status'] != 'corroborated':
            return {**incomplete('visible_cell_ocr_' + visible_check['status']),
                    'visible_cell_verification': visible_check, 'generation_audit': self.client.audit}
        return {**base, 'status': 'ok', 'clarification_code': None, 'fact': fact,
                'visible_cell_verification': visible_check,
                'answer': f'{fact["fact_key"]["row_header"]} / {" / ".join(fact["fact_key"]["column_header_path"])}: {fact["raw_value"]}',
                'generation_audit': self.client.audit, 'value_source': 'server_extracted_literal_grid_cell',
                'calculator_input_eligible': False}
