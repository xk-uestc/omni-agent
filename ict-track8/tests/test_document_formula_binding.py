import pytest

from backend.document_analysis import DocumentAnalyzer, SafeFormulaEvaluator
from backend.formula_binding import FormulaBinder, ParameterEvidence


def test_numeric_formula_does_not_capture_following_heading():
    result = DocumentAnalyzer().analyze('毛利率 = (500 - 300) / 500\n2.2 客单价\n客单价 = 销售额 / 订单数')
    assert len(result.formulas) == 2
    first, second = result.formulas
    assert first.value == 0.4
    assert first.line_no == 1
    assert second.status == 'requires_parameters'
    assert set(second.parameters) == {'销售额', '订单数'}
    assert second.line_no == 3


def test_symbolic_formula_preserves_x_and_percent_literals():
    e = SafeFormulaEvaluator()
    assert e.evaluate('x/2', {'x': 10}) == 5
    assert DocumentAnalyzer().analyze('税额 = 500 × １２．５％').formulas[0].value == 62.5


def test_binding_returns_actual_parameter_sources():
    result = FormulaBinder().calculate('销售额 / 订单数', {
        '销售额': ParameterEvidence(500, 'sql://query-1', 'row:0/column:销售额', 'CNY'),
        '订单数': ParameterEvidence(2, 'sql://query-1', 'row:0/column:订单数', 'count'),
    }, formula_source='document://metric-policy', formula_locator='page:2/line:4')
    assert result['value'] == 250
    assert result['parameters']['销售额']['source_uri'] == 'sql://query-1'
    assert result['trace'][-1]['value'] == 250


@pytest.mark.parametrize('expression,values', [
    ('a/0', {'a': 5}), ('a', {}), ('a', {'a': 1, 'b': 2}),
    ('__import__("os")', {}), ('a.__class__', {'a': 1}),
    ('a[0]', {'a': 1}), ('(-1)**0.5', {}), ('10%0', {}),
    ('1e100', {}), ('a', {'a': True}), ('a', {'a': float('nan')}),
])
def test_unsafe_or_incomplete_formulas_are_rejected(expression, values):
    with pytest.raises((ValueError, SyntaxError)):
        SafeFormulaEvaluator().evaluate(expression, values)


def test_binding_requires_provenance():
    with pytest.raises(ValueError, match='来源'):
        FormulaBinder().calculate('x', {'x': ParameterEvidence(1, '', '')}, formula_source='doc', formula_locator='line:1')
