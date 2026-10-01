"""OHR dataset boundary/metric contracts, never benchmark scores."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('test_' + name, root / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selection_ignores_answers_and_evidence_not_result_filtered():
    tool = load('fetch_ohr_bench')
    members = {'law/A': SimpleNamespace(file_size=20), 'law/B': SimpleNamespace(file_size=10)}
    rows = [{'ID': 'A', 'doc_name': 'law/A', 'evidence_source': 'text', 'answers': 'easy'},
            {'ID': 'B', 'doc_name': 'law/B', 'evidence_source': 'text', 'answers': 'impossible'}]
    chosen, unsupported = tool.choose_cases(rows, members, 1)
    assert chosen[0]['ID'] == 'B'
    assert 'chart' in unsupported
    rows[1]['answers'] = 'different'
    assert tool.choose_cases(rows, members, 1)[0][0]['ID'] == 'B'


def test_restore_never_reselects_or_accepts_changed_official_row():
    tool = load('fetch_ohr_bench')
    original = {'ID': 'B', 'answers': 'original'}
    manifest = {'cases': [{'ID': 'B', 'original_row_sha256': tool.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode())}]}
    assert tool.restore_selected([{'ID': 'A'}, original], manifest) == [original]
    with pytest.raises(ValueError, match='row_changed'):
        tool.restore_selected([{'ID': 'B', 'answers': 'easier'}], manifest)


def test_official_zero_based_page_array_is_not_scalar_or_off_by_one():
    tool = load('evaluate_ohr_bench')
    assert tool.expected_pages({'evidence_page_no': 0}) == {1}
    assert tool.expected_pages({'evidence_page_no': [0, 2]}) == {1, 3}
    row = {'doc_name': 'law/A', 'evidence_page_no': [0, 2], 'evidence_context': 'fact', 'answers': 'fact'}
    hits = [SimpleNamespace(metadata={'document_id': tool.doc_id('law/A'), 'page_no': 1}, snippet='fact')]
    scores = tool.retrieval_scores(hits, row)
    assert scores['any_evidence_page_hit'] and not scores['all_evidence_pages_hit']


def test_gold_is_not_sent_to_store_or_generator():
    tool = load('evaluate_ohr_bench')
    seen = []
    class Store:
        def answer(self, question, **kwargs):
            seen.append((question, kwargs))
            return {'answer': 'actual'}
    assert tool.query_generated(Store(), 'original official question') == {'answer': 'actual'}
    assert seen == [('original official question', {'top_k': 4})]


def test_token_f1_lcs_and_substring_are_separate_metrics():
    tool = load('evaluate_ohr_bench')
    scores = tool.answer_scores('The total is $30.83m.', '$30.83m')
    assert not scores['normalized_exact_match']
    assert scores['normalized_gold_substring']
    assert 0 < scores['english_token_f1'] < 1
    assert tool.reference_lcs_recall('third first second', 'first second third') == pytest.approx(2 / 3, abs=1e-6)


def test_verified_projection_score_is_separate_and_unprojected_questions_are_not_dropped():
    tool = load('evaluate_ohr_bench')
    cases = [{'generation': {'answer_mode': 'model_grounded', 'status': 'ok',
              'scores': tool.answer_scores('Revenue was 100 USD.', '100 USD'),
              'answer_projection': {'status': 'verified', 'answer_value': '100 USD'},
              'projection_scores': tool.answer_scores('100 USD', '100 USD')}},
             {'generation': {'answer_mode': 'model_grounded', 'status': 'insufficient_evidence',
              'scores': tool.answer_scores('Cannot answer', '200 USD')}}]
    summary = tool.summarise_cases(cases)
    assert summary['model']['normalized_exact_matches'] == 0
    assert summary['typed_projection']['verified_count'] == 1
    assert summary['typed_projection']['total_questions'] == 2
    assert summary['typed_projection']['normalized_exact_matches'] == 1
    assert summary['typed_projection']['mean_english_token_f1_all_questions'] == .5


def test_official_lcs_filters_wrong_documents_and_handles_evidence_array():
    tool = load('evaluate_ohr_bench')
    row = {'doc_name': 'law/A', 'evidence_page_no': 0, 'evidence_context': ['real fact'], 'answers': 'fact'}
    hits = [SimpleNamespace(metadata={'document_id': tool.doc_id('law/B'), 'page_no': 1}, snippet='real fact')]
    assert tool.retrieval_scores(hits, row)['evidence_text_lcs_recall'] == 0
    hits.append(SimpleNamespace(metadata={'document_id': tool.doc_id('law/A'), 'page_no': 1}, snippet='real fact'))
    assert tool.retrieval_scores(hits, row)['evidence_text_lcs_recall'] == 1


def test_asset_path_traversal_is_rejected(tmp_path):
    tool = load('evaluate_ohr_bench')
    with pytest.raises(ValueError, match='outside'):
        tool.checked_path(tmp_path, '../other.json')


def test_frozen_qa_tamper_is_rejected_before_ingestion(tmp_path):
    tool = load('evaluate_ohr_bench')
    raw = b'[]'
    (tmp_path / 'qa.json').write_bytes(raw)
    manifest = {'data_root_default': str(tmp_path), 'selected_qa_path': 'qa.json',
                'selected_qa_sha256': 'incorrect', 'cases': [], 'documents': []}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='sha_mismatch'):
        tool.load_frozen(tmp_path / 'manifest.json')
