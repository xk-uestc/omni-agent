"""Literal native-table values. No OCR correction or inferred unit conversion."""
import math
import re
from decimal import Decimal


def label(text):
    text = ' '.join(text.split())
    return re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)


def literal_cell(text, header):
    from .native_text_tables import column_unit_declaration
    raw = ' '.join(text.split())
    result = {'raw_value': text, 'display_text': text, 'unit': 'unknown', 'numeric_value': None}
    # Group separators must be exact. A damaged decimal cannot be "corrected".
    match = re.fullmatch(r'([+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)([%％]?)', raw)
    if not match:
        return result
    value = Decimal(match[1].replace(',', '').replace('−', '-'))
    try:
        declaration = column_unit_declaration([header])
    except ValueError:
        return result
    if declaration['scale']:
        # Keep printed-scale values readable but refuse physical operands.
        result['declared_scale'] = declaration['scale']
        return result
    unit = declaration['unit']
    if match[2] and unit not in {'percent', 'unknown'}:
        return result
    if match[2] or unit == 'percent':
        value /= 100
        unit = 'ratio'
    elif unit.startswith('currency:'):
        unit = declaration['currency']
    elif unit.startswith('currency_symbol:'):
        # A dollar symbol alone cannot choose USD vs CAD etc.
        unit = 'unknown'
    numeric = float(value)
    if not math.isfinite(numeric) or abs(numeric) > 1e15:
        return result
    result.update(raw_value=int(value) if value == int(value) else numeric,
                  numeric_value=int(value) if value == int(value) else numeric, unit=unit)
    return result
