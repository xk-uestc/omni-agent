"""Actual PDF -> store -> native chart -> one visual key selection."""
import fitz
import pytest

from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.responses_client import GenerationError
from tests.test_visual_charts import chart_pdf


class Client:
    model = 'gpt-6-luna'
    audit = {}

    def __init__(self, year=2023, mutation=None, fail=False):
        self.calls = []
        self.year, self.mutation, self.fail = year, mutation, fail

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append((context, kwargs))
        assert kwargs['name'] == 'visual_chart_selection'
        assert kwargs['image_attachments']
        if self.mutation:
            self.mutation()
        if self.fail:
            raise GenerationError('test failure')
        chart = context['chart_registry_without_values'][0]
        assert 'facts' not in chart
        return {'abstain': False, 'chart_id': chart['chart_id'], 'series': 'Alpha', 'year': self.year}


def setup(tmp_path, **kwargs):
    client = Client(**kwargs)
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(chart_pdf(), document_id='chart', title='Alpha Beta chart', modality='pdf', filename='chart.pdf')
    return store, client


def test_main_route_returns_native_value_and_unknown_unit(tmp_path):
    store, client = setup(tmp_path)
    result = store.answer('What is the value of Alpha in 2023?', document_id='chart')
    assert result['status'] == 'ok', result
    assert result['answer'] == '60' and result['answer_mode'] == 'visual_chart_native_annotated'
    assert result['answer_scope']['unit'] == 'unknown'
    assert result['calculator_input_eligible'] is False
    assert result['citations'][0]['metadata']['fact']['bbox_display_pt']
    assert result['trace'][0]['complete_candidate_scan'] is True
    assert len(client.calls) == 1


def test_first_crossing_reads_complete_periods(tmp_path):
    store, client = setup(tmp_path, year=2025)
    result = store.answer('When did Alpha first drop below 50?', document_id='chart')
    assert result['status'] == 'ok' and result['answer'] == '2025'
    assert result['chart_binding']['comparison_scope'] == 'all_displayed_chart_periods_only'
    assert result['fact']['raw_value'] == '30'
    assert len(client.calls) == 1


def test_duplicate_chart_sources_require_disambiguation(tmp_path):
    store, client = setup(tmp_path)
    store.ingest(chart_pdf(), document_id='other', title='Alpha chart', modality='pdf', filename='other.pdf')
    result = store.answer('What is the value of Alpha in 2023?')
    assert result['status'] == 'incomplete'
    assert result['clarification_code'] == 'chart_source_or_question_scope_ambiguous'
    assert not client.calls


def test_unknown_qualifier_does_not_fall_back_to_text(tmp_path):
    store, client = setup(tmp_path)
    result = store.answer('What is the value of Alpha in 2023 for approved orders?', document_id='chart')
    assert result['status'] == 'incomplete' and result['answer'] is None
    assert not client.calls


def test_related_chart_with_missing_annotation_never_falls_back(tmp_path):
    store, client = setup(tmp_path)
    store.ingest(chart_pdf(missing_label=(0, 1)), document_id='chart', title='Alpha chart',
                 modality='pdf', filename='chart.pdf')
    result = store.answer('What is the value of Alpha in 2023?', document_id='chart')
    assert result['status'] == 'incomplete' and not client.calls
    assert result['trace'][0]['scope_rejections']


@pytest.mark.parametrize('year,fail,code', [(2025, False, 'chart_visual_selection_scope_mismatch'),
                                          (2023, True, 'chart_visual_model_unavailable')])
def test_failed_visual_key_does_not_authorize_native_value(tmp_path, year, fail, code):
    store, client = setup(tmp_path, year=year, fail=fail)
    result = store.answer('What is the value of Alpha in 2023?', document_id='chart')
    assert result['status'] == 'incomplete' and result['answer'] is None
    assert result['clarification_code'] == code
    assert len(client.calls) == 1


def test_source_revision_after_model_invalidates_result(tmp_path):
    store, client = setup(tmp_path)
    client.mutation = lambda: store.ingest(chart_pdf(values=((81,61,31),(71,56,46))), document_id='chart',
                                         title='Alpha chart', modality='pdf', filename='chart.pdf')
    with pytest.raises(SourceRevisionError):
        store.answer('What is the value of Alpha in 2023?', document_id='chart')


def test_later_duplicate_page_is_not_skipped(tmp_path):
    store, client = setup(tmp_path)
    with fitz.open(stream=chart_pdf(), filetype='pdf') as source, fitz.open() as doc:
        doc.insert_pdf(source)
        doc.insert_pdf(source)
        raw = doc.tobytes()
    store.ingest(raw, document_id='chart', title='Alpha chart', modality='pdf', filename='chart.pdf')
    result = store.answer('What is the value of Alpha in 2023?', document_id='chart')
    assert result['status'] == 'incomplete' and not client.calls
    assert result['trace'][0]['complete_key_matches'] == 2
    selected = store.answer('What is the value of Alpha in 2023?', document_id='chart', page_no=2)
    assert selected['status'] == 'ok' and selected['fact']['page_no'] == 2


def test_budget_refuses_partial_uniqueness(tmp_path, monkeypatch):
    store, client = setup(tmp_path)
    monkeypatch.setattr('backend.visual_chart_routing.MAX_BYTES', 1)
    result = store.answer('What is the value of Alpha in 2023?', document_id='chart')
    assert result['status'] == 'incomplete' and not client.calls
    assert result['clarification_code'] == 'chart_candidate_byte_budget_exceeded'
