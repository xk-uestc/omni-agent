import pytest

from backend.dependency_agent import DependencyAgent
from backend.fusion_constraints import (SourceConstraintError, VerifiedFormulaTarget,
                                       bind_source_constraints, extract_source_clauses)
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


@pytest.fixture
def context(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest('来年预算 = 基准销售额 * 1.1\n错误预算 = 基准销售额 * 2'.encode(),
                 document_id='budget', title='预算公式', modality='txt', filename='budget.txt')
    agent = DependencyAgent(engine, store)
    formula = agent.execute('document_formula', {'document_id': 'budget', 'label': '来年预算'}, {}, {})
    proof = VerifiedFormulaTarget(formula['label'], 'budget', formula['sha256'], 'formula')
    question = '根据预算文档公式和数据库2024年华南销售额，算2027年来年预算。'
    tasks = [{'id': 'formula', 'tool': 'document_formula', 'args': {'document_id': 'budget', 'label': '来年预算'}},
             {'id': 'baseline', 'tool': 'sql', 'args': {'question': '2024年华南销售额'}},
             {'id': 'calculate', 'tool': 'calculate', 'args': {'formula': {'ref': 'formula', 'path': []},
               'parameters': {'基准销售额': {'ref': 'baseline', 'path': ['rows', 0, '销售额']}}}}]
    return engine, agent, question, tasks, proof, formula


def test_generic_new_target_and_year_bind_to_real_formula_without_sql_year_inheritance(context):
    engine, agent, question, tasks, proof, formula = context
    bound, audit = bind_source_constraints(question, tasks, engine, verified_formula_targets=(proof,))
    binding = audit[0]['target_binding']
    assert binding['label'] == '来年预算'
    assert binding['target_year'] == 2027
    assert binding['placement'] == 'explicit_postfix_calculation'
    assert audit[0]['text'] == '2024年华南销售额'
    assert any(tuple(item.value) == ('2024-01-01', '2025-01-01') for item in bound['baseline'].filters if isinstance(item.value, (list, tuple)))
    result = agent.run(tasks, original_question=question)
    assert result['status'] == 'ok', result
    assert result['results']['calculate']['target_formula_validation']['status'] == 'verified'


@pytest.mark.parametrize('suffix', ['算2027年有效来年预算', '算2027年来年预算不含退货',
                                   '算2027年神秘预算', '算2027年华北来年预算',
                                   '算2027年仅批准来年预算', '算2027年2028年来年预算'])
def test_unknown_full_target_conditions_and_conflicting_entity_reject(context, suffix):
    engine, agent, question, tasks, proof, formula = context
    question = question.split('，')[0] + '，' + suffix
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(), engine=engine, verified_formula_targets=(proof,))


def test_model_label_without_real_formula_proof_cannot_authorize_target(context):
    engine, agent, question, tasks, proof, formula = context
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(), engine=engine)


def test_postfix_exact_same_entity_is_separately_audited(context):
    engine, agent, question, tasks, proof, formula = context
    question = question.replace('2027年来年预算', '2027年华南地区来年预算')
    clause = extract_source_clauses(question, engine.schema(), engine=engine, verified_formula_targets=(proof,))[0]
    assert clause.text == '2024年华南销售额'
    assert clause.target_binding['target_entity'] == {'table': 'sales_orders', 'column': 'region', 'value': '华南'}


def test_two_postfix_targets_do_not_guess_shared_formula(context):
    engine, agent, question, tasks, proof, formula = context
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question.rstrip('。') + '，计算2028年来年预算', engine.schema(),
                               engine=engine, verified_formula_targets=(proof,))


def test_model_dictionary_is_not_a_verified_formula_target(context):
    engine, agent, question, tasks, proof, formula = context
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(), engine=engine,
                               verified_formula_targets=({'label': '来年预算', 'task_id': 'formula'},))


def test_two_sql_sources_cannot_guess_postfix_antecedent(context):
    engine, agent, question, tasks, proof, formula = context
    question = question.replace('，算', '，再从数据库2025年华东销售额，算')
    with pytest.raises(SourceConstraintError):
        extract_source_clauses(question, engine.schema(), engine=engine, verified_formula_targets=(proof,))


@pytest.mark.parametrize('mutation', ['wrong_formula', 'no_sql_ancestor', 'unused_formula'])
def test_final_calculator_must_consume_exact_target_formula_and_bound_sql(context, mutation):
    engine, agent, question, tasks, proof, formula = context
    if mutation == 'wrong_formula':
        tasks.insert(1, {'id': 'wrong', 'tool': 'document_formula', 'args': {'document_id': 'budget', 'label': '错误预算'}})
        tasks[-1]['args']['formula']['ref'] = 'wrong'
    elif mutation == 'no_sql_ancestor':
        tasks[-1]['args']['parameters']['基准销售额'] = 1
    else:
        tasks.pop()
    result = agent.run(tasks, original_question=question)
    assert result['status'] != 'ok'
    assert result['error_code'] in {'formula_target_not_consumed', 'formula_target_source_not_consumed'}
