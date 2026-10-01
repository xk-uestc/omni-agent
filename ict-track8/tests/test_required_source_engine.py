"""A source requirement must be checked before a business SQL is executed."""
import pytest

from backend.nl2sql import engine as engine_module
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


SOURCE = '2025年不含华北的销售额'


def test_requirement_extraction_uses_rules_without_model_or_business_execution(tmp_path, monkeypatch):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Requirement extraction must not call a model or execute result SQL')
    engine.model_plan_provider = forbidden
    monkeypatch.setattr(engine_module, 'execute_read_only', forbidden)
    required = engine.extract_required_intent(SOURCE)
    assert required.source_scope_question == SOURCE
    assert required.clarification is None
    assert required.filters and required.metric_column == 'sales_amount' and required.metric_function == 'SUM'
    assert 'source_scope_question' not in required.to_dict()


@pytest.mark.parametrize('question', [
    '2025年销售额', '不含华北的销售额', '2025年华北销售额', '2024年不含华北的销售额',
])
def test_missing_reversed_or_changed_source_filters_never_execute(tmp_path, monkeypatch, question):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite'))
    required = engine.extract_required_intent(SOURCE)
    def forbidden(*args, **kwargs):
        raise AssertionError('A mismatched source cannot execute business SQL')
    monkeypatch.setattr(engine_module, 'execute_read_only', forbidden)
    result = engine.answer(question, required_intent=required)
    assert result.status == 'incomplete' and result.sql is None and result.rows == ()
    assert result.result_state == 'unexecuted'
    assert result.clarification_code == 'source_constraint_mismatch'
    audit = result.provenance['source_constraint_validation']
    assert audit['status'] == 'rejected' and audit['error_codes']


def test_matching_scope_executes_and_has_real_constraint_provenance(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite'))
    required = engine.extract_required_intent(SOURCE)
    baseline = engine.answer(SOURCE)
    result = engine.answer(SOURCE, required_intent=required)
    assert result.status == 'ok' and result.sql and result.rows == baseline.rows
    assert result.parameters == baseline.parameters
    assert result.provenance['source_constraint_validation']['status'] == 'verified'


def test_scope_is_reextracted_not_trusting_mutated_carrier_filters(tmp_path, monkeypatch):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite'))
    required = engine.extract_required_intent(SOURCE)
    required.filters = []
    def forbidden(*args, **kwargs):
        raise AssertionError('Removing carrier filters cannot authorize a broader query')
    monkeypatch.setattr(engine_module, 'execute_read_only', forbidden)
    result = engine.answer('2025年销售额', required_intent=required)
    assert result.status == 'incomplete' and result.sql is None


def test_ambiguous_source_stays_unverified_without_execution(tmp_path, monkeypatch):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite'))
    required = engine.extract_required_intent('含糊的来源口径')
    assert required.clarification
    monkeypatch.setattr(engine_module, 'execute_read_only', lambda *a, **k: pytest.fail('Unverified scope executed'))
    result = engine.answer('2025年销售额', required_intent=required)
    assert result.status == 'incomplete' and result.sql is None
    assert 'source_scope_unverified' in result.provenance['source_constraint_validation']['error_codes']


@pytest.mark.parametrize('question', ['', 'x' * 1001, None])
def test_requirement_input_bounds(tmp_path, question):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'data.sqlite'))
    with pytest.raises(ValueError):
        engine.extract_required_intent(question)
