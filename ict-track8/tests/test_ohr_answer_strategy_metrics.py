import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location('ohr_metrics', Path(__file__).resolve().parents[2] / 'tools/evaluate_ohr_bench.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_program_answer_not_full_fact_is_primary_and_all_evidence_is_retained():
    result = {'answer': '100 USD', 'full_fact_answer': 'The annual revenue is 100 USD.',
              'answer_strategy': 'model_reviewed_source_span', 'answer_span_result': {'status': 'model_reviewed', 'proof': 'literal'},
              'answer_scope': {'period': '2025'}, 'computation': {'operation': 'sum'},
              'semantic_review': {'accepted': True}, 'semantic_verification': 'model_review', 'calculator_input_eligible': False}
    fields = tool.answer_output_fields(result, '100 USD')
    assert fields['scores']['normalized_exact_match'] == 1
    assert fields['full_fact_scores']['normalized_exact_match'] == 0
    assert fields['full_fact_answer'] == result['full_fact_answer']
    for key in result:
        assert fields[key] == result[key]


def test_reviewed_scores_are_separate_from_deterministic_projection_and_use_total_denominator():
    records = [
        {'status': 'ok', 'answer_mode': 'model_grounded', 'answer_strategy': 'model_reviewed_source_span',
         'answer_span_result': {'status': 'model_reviewed'}, 'answer': '10', 'full_fact_answer': 'Full fact is 10.',
         'answer_projection': {'status': 'verified', 'answer_value': '10'}},
        {'status': 'ok', 'answer_mode': 'native_table_model_reviewed', 'answer': '10',
         'answer_projection': {'status': 'verified', 'answer_value': '10'}},
        {'status': 'ok', 'answer_mode': 'model_grounded', 'answer_strategy': 'deterministic_typed_projection',
         'answer': '10', 'answer_projection': {'status': 'verified', 'answer_value': '10'}},
        {'status': 'failed', 'answer_mode': 'model_grounded', 'answer': 'failed'},
    ]
    cases = [{'generation': {**record, **tool.answer_output_fields(record, '10')}} for record in records] + [{}]
    summary = tool.summarise_cases(cases)
    assert summary['model']['normalized_exact_matches'] == 3
    assert summary['typed_projection']['verified_count'] == 1
    for key in ('model_reviewed_span', 'native_table_model_reviewed'):
        assert summary[key]['count'] == 1
        assert summary[key]['total_questions'] == 5
        assert summary[key]['mean_english_token_f1_all_questions'] == .2
    assert summary['full_fact_answer']['count'] == 1


def test_unreviewed_or_failed_span_is_not_counted_as_reviewed_success():
    cases = [{'generation': {'status': 'failed', 'answer_mode': 'model_grounded',
             'answer_strategy': 'model_reviewed_source_span', 'answer_span_result': {'status': 'model_reviewed'},
             'scores': tool.answer_scores('10', '10')}},
             {'generation': {'status': 'ok', 'answer_mode': 'model_grounded',
             'answer_strategy': 'model_reviewed_source_span', 'answer_span_result': {'status': 'unsupported'},
             'scores': tool.answer_scores('10', '10')}}]
    assert tool.summarise_cases(cases)['model_reviewed_span']['count'] == 0
