"""Parser-certified rows are distinct from whitespace-inferred PDF tables."""
from copy import deepcopy
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from backend.chunk_cleaning import DocumentChunker
from backend.knowledge_store import KnowledgeStore, _verified_pdf_row
from backend.grounded_generation import GroundedGenerator


def literal_pdf():
    output = BytesIO()
    document = canvas.Canvas(output)
    document.drawString(50, 750, '| Department | Budget |')
    document.drawString(50, 735, '| Engineering | 25 |')
    document.save()
    return output.getvalue()


def literal_rows():
    chunks = DocumentChunker().parse_pdf(literal_pdf(), document_id='literal').to_dict()['chunks']
    return [chunk for chunk in chunks if chunk['content_type'] == 'row']


def test_actual_parser_literal_row_is_authorized():
    rows = literal_rows()
    assert rows and _verified_pdf_row(rows[0])


@pytest.mark.parametrize('mutation', ['uncertified', 'inherited', 'unknown_columns', 'wrong_cells', 'wrong_text', 'split'])
def test_row_certificate_does_not_authorize_broken_relations(mutation):
    row = deepcopy(literal_rows()[0])
    metadata = row['metadata']
    if mutation == 'uncertified':
        metadata.pop('pdf_table_structure')
    elif mutation == 'inherited':
        metadata['pdf_table_structure']['header_inherited'] = True
    elif mutation == 'unknown_columns':
        metadata['pdf_table_structure']['uniform_column_count'] = False
    elif mutation == 'wrong_cells':
        metadata['source_row_cells'][-1] = '90'
    elif mutation == 'wrong_text':
        row['text'] = row['text'].replace('25', '90')
    else:
        metadata['part_no'] = 1
    assert not _verified_pdf_row(row)


def test_certified_pdf_row_reaches_generator_and_fact_validator(tmp_path):
    class LiteralClient:
        model = 'gpt-6-luna'
        audit = {'status': 'completed', 'http_status': 200}

        def generate(self, instructions, context, schema, **kwargs):
            item = context['evidence'][0]
            return {'abstain': False, 'claims': [{'text': item['text'], 'support': [
                {'citation_id': item['citation_id'], 'quote': item['text']}]}]}

    store = KnowledgeStore(tmp_path / 'knowledge', generator=GroundedGenerator(LiteralClient()))
    store.ingest(literal_pdf(), document_id='literal', title='Department budgets', modality='pdf', filename='budgets.pdf')
    result = store.answer('Engineering budget', top_k=1)
    assert result['answer_mode'] == 'model_grounded', result
    assert '25' in result['answer']
    assert result['citations'][0]['generation_evidence']['text']


def test_registered_visual_asset_pins_document_revision(tmp_path):
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest(literal_pdf(), document_id='literal', title='Department budgets', modality='pdf', filename='budgets.pdf')
    digest = store.document('literal')['sha256']
    asset = store.visual_asset('literal', page_no=1, expected_source_sha256=digest)
    assert asset.png_bytes.startswith(b'\x89PNG')
    assert asset.manifest['document_id'] == 'literal'
    assert asset.manifest['original_uri'].endswith('/literal/original')
    with pytest.raises(ValueError):
        store.visual_asset('literal', page_no=1, expected_source_sha256='0' * 64)
