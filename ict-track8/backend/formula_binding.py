"""Safe document formula execution with explicit parameter provenance."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .document_analysis import DocumentAnalyzer, SafeFormulaEvaluator
from .unit_algebra import UNITS, infer


@dataclass(frozen=True)
class ParameterEvidence:
    value: float
    source_uri: str
    locator: str
    unit: str = 'unknown'


class FormulaBinder:
    def __init__(self):
        self.evaluator = SafeFormulaEvaluator()

    def calculate(self, expression: str, parameters: dict[str, ParameterEvidence], *, formula_source: str, formula_locator: str) -> dict[str, Any]:
        if not formula_source.strip() or not formula_locator.strip():
            raise ValueError('公式必须携带来源与定位')
        normalized = DocumentAnalyzer._normalize_formula(expression)
        required = self.evaluator.parameters(normalized)
        if set(required) != set(parameters):
            raise ValueError('公式参数缺失或多余；不能猜测参数')
        for parameter in parameters.values():
            if not parameter.source_uri.strip() or not parameter.locator.strip():
                raise ValueError('每个参数必须携带来源与定位')
            if isinstance(parameter.value, bool) or not isinstance(parameter.value, (int, float)) or not math.isfinite(parameter.value):
                raise ValueError('参数必须是有限数字')
        units = {name: evidence.unit for name, evidence in parameters.items()}
        known = all(unit in UNITS for unit in units.values())
        numeric = {name: evidence.value * UNITS[evidence.unit][1] if evidence.unit in UNITS else evidence.value
                   for name, evidence in parameters.items()}
        dimension, result_unit = infer(normalized, units) if known else (None, 'unknown')
        value = self.evaluator.evaluate(normalized, numeric)
        return {
            'status': 'ok', 'expression': normalized, 'value': value,
            'formula_source': formula_source, 'formula_locator': formula_locator,
            'parameters': {name: asdict(evidence) for name, evidence in parameters.items()},
            'unit_validation': 'validated' if known else 'not_inferred',
            'result_unit': result_unit, 'result_dimension': dimension, 'normalized_parameters': numeric,
            'trace': [
                {'stage': 'formula_parse', 'status': 'validated', 'parameters': list(required)},
                {'stage': 'parameter_binding', 'status': 'complete', 'sources': [p.source_uri for p in parameters.values()]},
                {'stage': 'safe_calculation', 'status': 'complete', 'value': value},
            ],
        }
