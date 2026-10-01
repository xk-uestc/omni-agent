"""Pinned native, explicitly annotated vector line charts.

This bounded adapter verifies geometric annotation membership, not the truth of
the chart. Exact values come ONLY from original native text labels. Axis fitting
corroborates labels; it never supplies rounded/pixel-derived factual values.
Raster charts, unlabeled curves, dual/nonlinear axes and ambiguous legends abstain.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from fractions import Fraction

import fitz

_VERSION = 'native-annotated-vector-line-v1'
_NUM = re.compile(r'([+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(%?)')


def compute_chart_annotations(facts, operation):
    """Exact arithmetic in one native annotation domain, never inferred units.

    Inputs must still be replayed against the source manifest by the caller.
    Nonterminating ratios abstain rather than inventing a rounded value.
    """
    if operation not in {'lookup', 'sum', 'difference', 'ratio'} or not 1 <= len(facts) <= 12:
        raise ValueError('chart_arithmetic_operands_invalid')
    if (operation == 'lookup' and len(facts) != 1 or operation == 'sum' and len(facts) < 2
            or operation in {'difference', 'ratio'} and len(facts) != 2):
        raise ValueError('chart_arithmetic_operands_invalid')
    if len({f['fact_id'] for f in facts}) != len(facts):
        raise ValueError('chart_arithmetic_duplicate_operand')
    if len({(f['source_sha256'], f['page_no'], f['chart_id'], f['unit'], f.get('scale')) for f in facts}) != 1:
        raise ValueError('chart_arithmetic_scope_mismatch')
    if any(f.get('scale') is not None for f in facts):
        raise ValueError('chart_arithmetic_scale_unbound')
    if operation == 'sum' and any(re.search(r'\b(?:total|subtotal)\b|合计|总计|小计', f['series'], re.I) for f in facts):
        raise ValueError('chart_arithmetic_total_components_unsupported')
    values, suffixes = [], set()
    for fact in facts:
        raw = fact['raw_value']
        if not isinstance(raw, str) or len(raw) > 512:
            raise ValueError('chart_arithmetic_literal_budget')
        parsed = _numeric(raw)
        if parsed is None or str(parsed[0]) != fact['numeric_value']:
            raise ValueError('chart_arithmetic_literal_mismatch')
        values.append(Fraction(parsed[0]))
        suffixes.add(parsed[1])
    if len(suffixes) != 1:
        raise ValueError('chart_arithmetic_unit_mismatch')
    if operation == 'lookup':
        value = values[0]
    elif operation == 'sum':
        value = sum(values, Fraction(0))
    elif operation == 'difference':
        value = values[0] - values[1]
    else:
        if values[1] == 0:
            raise ValueError('chart_arithmetic_zero_denominator')
        value = values[0] / values[1]
    denominator, twos, fives = value.denominator, 0, 0
    while denominator % 2 == 0:
        twos += 1
        denominator //= 2
    while denominator % 5 == 0:
        fives += 1
        denominator //= 5
    if denominator != 1:
        raise ValueError('chart_arithmetic_nonterminating_ratio')
    places = max(twos, fives)
    if places > 2048:
        raise ValueError('chart_arithmetic_result_budget')
    scaled = value.numerator * 2 ** (places - twos) * 5 ** (places - fives)
    digits = str(abs(scaled)).zfill(places + 1)
    literal = (digits[:-places] + '.' + digits[-places:]).rstrip('0').rstrip('.') if places else digits
    literal = ('-' if scaled < 0 else '') + literal
    suffix = next(iter(suffixes)) if operation != 'ratio' else ''
    return {'operation': operation, 'answer': facts[0]['raw_value'] if operation == 'lookup' else literal + suffix, 'numeric_result': literal,
            'operand_fact_ids': [f['fact_id'] for f in facts], 'operands': [f['raw_value'] for f in facts],
            'unit': 'ratio' if operation == 'ratio' else facts[0]['unit'], 'scale': None,
            'computation_domain': 'same_chart_native_annotation_arithmetic_not_inferred_physical_quantity',
            'calculator_input_eligible': False}


def _numeric(text):
    match = _NUM.fullmatch(text.strip())
    if not match:
        return None
    try:
        return Decimal(match[1].replace(',', '').replace('−', '-')), match[2]
    except InvalidOperation:
        return None


def _coords(rect):
    return [float(v) for v in rect]


def _center(rect):
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def _color(path):
    value = path.get('color')
    return tuple(round(float(v), 6) for v in value) if value is not None else None


def _distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def extract_pdf_charts(raw: bytes, *, page_no: int, expected_source_sha256: str | None = None):
    """Read a pinned page, returning chart/fact manifests and explicit rejections.

    Supports rectangular plot frames, strictly connected monotone-x native
    polylines, unique same-color legend swatches, explicit year slots, at least
    three consistent linear-y ticks, and a complete bijection of point labels.
    All coordinates are rotated display points. No files or model calls.
    """
    if not isinstance(raw, bytes) or not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError('chart_source_bytes_invalid_or_over_budget')
    digest = hashlib.sha256(raw).hexdigest()
    if expected_source_sha256 is not None and digest != expected_source_sha256:
        raise ValueError('chart_source_sha256_mismatch')
    if type(page_no) is not int or page_no < 1:
        raise ValueError('chart_page_no_invalid')
    report = {'schema_version': _VERSION, 'source_sha256': digest, 'page_no': page_no,
              'charts': [], 'facts': [], 'rejected_charts': [],
              'validation_scope': 'native_annotation_geometry_not_visible_ocr_or_semantic_truth',
              'calculator_input_eligible': False}
    with fitz.open(stream=raw, filetype='pdf') as doc:
        if doc.needs_pass or len(doc) > 1000 or page_no > len(doc):
            raise ValueError('chart_page_bounds_or_encryption_invalid')
        page = doc[page_no-1]
        rotation = fitz.Matrix(page.rotation_matrix)
        page.set_rotation(0)
        words = page.get_text('words', sort=False)
        paths = page.get_drawings()
        if len(words) > 20000 or sum(len(p['items']) for p in paths) > 20000:
            raise ValueError('chart_native_parser_budget_exceeded')
        blocks = []
        for block in page.get_text('blocks'):
            if block[6] == 0:
                blocks.append({'text': ' '.join(block[4].split()), 'bbox': fitz.Rect(block[:4]), 'block_no': block[5]})
        frames = []
        for path in paths:
            if len(path['items']) != 1 or path['items'][0][0] != 're':
                continue
            rect = path['rect']
            if rect.width < 80 or rect.height < 60 or path.get('fill') is not None:
                continue
            if any(max(abs(a-b) for a,b in zip(rect, previous)) <= 1 for previous in frames):
                continue
            frames.append(rect)
        if len(frames) > 16:
            raise ValueError('chart_frame_budget_exceeded')
        report['native_word_count'] = len(words)
        report['vector_path_count'] = len(paths)
        for frame_no, frame in enumerate(frames):
            chart_id = hashlib.sha256(json.dumps([_VERSION,digest,page_no,_coords(frame)], separators=(',', ':')).encode()).hexdigest()[:24]
            def reject(reason):
                scope = [b for b in blocks if b['bbox'].x0 >= frame.x0-30 and b['bbox'].x1 <= frame.x1+30
                         and frame.y0-60 <= b['bbox'].y0 and b['bbox'].y1 <= frame.y0+45]
                report['rejected_charts'].append({'chart_id': chart_id, 'bbox_display_pt': _coords(frame * rotation),
                    'reason': reason, 'scope_text_candidates_unverified':
                    [{'text': b['text'], 'bbox_display_pt': _coords(b['bbox']*rotation)} for b in scope],
                    'calculator_input_eligible': False})
            series_paths = []
            for path_no, path in enumerate(paths):
                items = path['items']
                if not items or not all(item[0] == 'l' for item in items):
                    continue
                if (path.get('fill') is not None or _color(path) is None or path.get('dashes', '[] 0') != '[] 0'
                        or not frame.contains(path['rect']) or path['rect'].width < frame.width*.35):
                    continue
                if path['rect'].height == 0 and min(abs(path['rect'].y0-frame.y0), abs(path['rect'].y0-frame.y1)) <= 1:
                    continue
                points = [items[0][1]]
                for item in items:
                    if _distance(points[-1], item[1]) > .1:
                        points = []
                        break
                    points.append(item[2])
                if len(points) < 2 or any(b.x <= a.x for a,b in zip(points,points[1:])):
                    continue
                series_paths.append((path_no,path,points))
            if not series_paths:
                reject('no_supported_connected_native_polyline')
                continue
            colors = [_color(path) for _,path,_ in series_paths]
            if len(set(colors)) != len(colors) or len(series_paths) > 16:
                reject('series_style_not_unique_or_over_budget')
                continue
            years = [(index,word) for index,word in enumerate(words)
                     if re.fullmatch(r'\d{4}',word[4]) and frame.x0 < _center(word)[0] < frame.x1
                     and frame.y1 < word[1] < frame.y1+30]
            if years:
                # Caption/source dates below the chart are not x-axis labels.
                # Only the nearest aligned native text row is eligible.
                axis_row_y = min(_center(word)[1] for _,word in years)
                years = [(index,word) for index,word in years if abs(_center(word)[1]-axis_row_y) <= 2]
            years.sort(key=lambda pair: pair[1][0])
            if (not 2 <= len(years) <= 100 or len({w[4] for _,w in years}) != len(years)
                    or any(int(b[1][4]) <= int(a[1][4]) for a,b in zip(years,years[1:]))):
                reject('year_slots_missing_ambiguous_or_unordered')
                continue
            year_centers = [_center(w)[0] for _,w in years]
            edges = [frame.x0, *[(a+b)/2 for a,b in zip(year_centers,year_centers[1:])], frame.x1]
            def year_slot(x):
                matches = [i for i in range(len(years)) if edges[i] < x < edges[i+1]]
                return matches[0] if len(matches) == 1 else None
            ticks = [(i,w,_numeric(w[4])) for i,w in enumerate(words)
                     if _numeric(w[4]) is not None and frame.x0-80 < w[0] and w[2] <= frame.x0+1
                     and frame.y0-1 <= _center(w)[1] <= frame.y1+1]
            right_ticks = [w for w in words if _numeric(w[4]) is not None and frame.x1 <= w[0] < frame.x1+50
                           and frame.y0 <= _center(w)[1] <= frame.y1]
            if right_ticks:
                reject('possible_dual_y_axis')
                continue
            if len(ticks) < 3 or len({n[1] for _,_,n in ticks}) != 1:
                reject('linear_axis_ticks_missing_or_units_conflict')
                continue
            ticks.sort(key=lambda item: _center(item[1])[1])
            ys = [_center(w)[1] for _,w,_ in ticks]
            vals = [float(n[0]) for _,_,n in ticks]
            if any(b <= a for a,b in zip(ys,ys[1:])) or any(b >= a for a,b in zip(vals,vals[1:])):
                reject('inverted_or_ambiguous_y_axis')
                continue
            ymean, vmean = sum(ys)/len(ys), sum(vals)/len(vals)
            denom = sum((y-ymean)**2 for y in ys)
            slope = sum((y-ymean)*(v-vmean) for y,v in zip(ys,vals))/denom
            intercept = vmean - slope*ymean
            residual = max(abs((v-intercept)/slope-y) for y,v in zip(ys,vals))
            if residual > .5:
                reject('nonlinear_or_inconsistent_y_axis')
                continue
            axis_unit = 'percent' if ticks[0][2][1] == '%' else 'unknown'
            unit_labels = [b for b in blocks if b['bbox'].width < 120 and b['bbox'].height < 30
                           and b['bbox'].x1 <= frame.x0+1 and b['bbox'].x0 >= frame.x0-100
                           and frame.y0-25 <= b['bbox'].y0 <= frame.y1
                           and re.fullmatch(r'count|counts|number(?: of .+)?|数量|人数|USD|CNY|EUR|percent|percentage|%', b['text'], re.I)]
            declared_units = {'percent' if re.fullmatch(r'percent|percentage|%', b['text'],re.I) else
                              'count' if re.fullmatch(r'count|counts|number(?: of .+)?|数量|人数',b['text'],re.I) else b['text'].upper()
                              for b in unit_labels}
            if len(declared_units) > 1 or (axis_unit != 'unknown' and declared_units and declared_units != {axis_unit}):
                reject('axis_unit_declarations_conflict')
                continue
            if declared_units:
                axis_unit = next(iter(declared_units))
            numeric_labels = [(i,w,_numeric(w[4])) for i,w in enumerate(words)
                              if _numeric(w[4]) is not None and frame.contains(fitz.Rect(w[:4]))]
            used_labels, names, legends_ledger, facts, failure = set(), [], [], [], None
            for path_no,path,points in series_paths:
                swatches = [(i,p) for i,p in enumerate(paths) if _color(p) == _color(path)
                            and len(p['items']) == 1 and p['items'][0][0] == 'l'
                            and p['rect'].height < .1 and 6 <= p['rect'].width <= min(40,frame.width*.15)
                            and frame.contains(p['rect'])]
                if len(swatches) != 1:
                    failure = 'legend_swatch_missing_or_ambiguous'; break
                swatch_no,swatch = swatches[0]
                sx,sy = _center(swatch['rect'])
                legends = [b for b in blocks if b['bbox'].height <= 26 and b['bbox'].width <= 120
                           and re.search(r'[A-Za-z\u3400-\u9fff]',b['text'])
                           and b['bbox'].x0 <= sx <= b['bbox'].x1 and 0 <= sy-b['bbox'].y1 <= 8]
                if len(legends) != 1:
                    failure = 'legend_text_missing_or_ambiguous'; break
                legend = legends[0]
                name = legend['text']
                if name.casefold() in {n.casefold() for n in names}:
                    failure = 'duplicate_series_name'; break
                names.append(name)
                legends_ledger.append({'series': name, 'legend_block_no': legend['block_no'],
                                       'legend_bbox_display_pt': _coords(legend['bbox']*rotation),
                                       'swatch_path_no': swatch_no, 'color': list(_color(swatch)),
                                       'swatch_bbox_display_pt': _coords(swatch['rect']*rotation),
                                       'series_path_no': path_no})
                slots = [year_slot(point.x) for point in points]
                if slots != list(range(len(years))):
                    failure = 'series_period_points_incomplete_or_ambiguous'; break
                for point_no,(point,slot) in enumerate(zip(points,slots)):
                    matching = []
                    for label_no,word,number in numeric_labels:
                        if year_slot(_center(word)[0]) != slot or number[1] != ticks[0][2][1]:
                            continue
                        expected_y = (float(number[0])-intercept)/slope
                        label_gap = min(abs(point.y-word[1]),abs(point.y-word[3]))
                        if abs(expected_y-point.y) <= 1 and label_gap <= 20:
                            matching.append((label_no,word,number,abs(expected_y-point.y)))
                    if len(matching) != 1 or matching[0][0] in used_labels:
                        failure = 'point_annotation_missing_conflicting_or_ambiguous'; break
                    label_no,word,number,error = matching[0]
                    used_labels.add(label_no)
                    fact_id = f'{chart_id}:path:{path_no}:point:{point_no}:word:{label_no}'
                    facts.append({'fact_id': fact_id, 'chart_id': chart_id, 'series': name,
                                  'year': int(years[slot][1][4]), 'raw_value': word[4], 'numeric_value': str(number[0]),
                                  'unit': axis_unit, 'scale': None, 'source_sha256': digest, 'page_no': page_no,
                                  'bbox_display_pt': _coords(fitz.Rect(word[:4])*rotation),
                                  'point_display_pt': list(point*rotation), 'series_path_no': path_no,
                                  'legend_path_no': swatch_no, 'legend_block_no': legend['block_no'],
                                  'year_word_id': f'page:{page_no}:word:{years[slot][0]}',
                                  'value_word_id': f'page:{page_no}:word:{label_no}',
                                  'annotation_axis_residual_pt': error, 'value_kind': 'native_annotation_only',
                                  'validation_scope': report['validation_scope'], 'calculator_input_eligible': False})
                if failure: break
            if failure:
                reject(failure); continue
            if len(used_labels) != len(numeric_labels):
                reject('unbound_numeric_annotations_in_plot'); continue
            scope_blocks = [b for b in blocks if b['bbox'].y1 <= frame.y0+1 and b['bbox'].y0 >= frame.y0-60
                            and b['bbox'].x0 >= frame.x0-30 and b['bbox'].x1 <= frame.x1+30]
            chart = {'chart_id': chart_id, 'status': 'native_annotations_bound', 'source_sha256': digest, 'page_no': page_no,
                     'bbox_display_pt': _coords(frame*rotation), 'series': names, 'years': [int(w[4]) for _,w in years],
                     'title_context': [{'text': b['text'], 'bbox_display_pt': _coords(b['bbox']*rotation)} for b in scope_blocks],
                     'legend_scope': legends_ledger, 'axis': {'unit': axis_unit, 'scale': None, 'linear_tick_residual_pt': residual,
                         'ticks': [{'raw_value': w[4], 'bbox_display_pt': _coords(fitz.Rect(w[:4])*rotation)} for _,w,_ in ticks],
                         'unit_declarations': [b['text'] for b in unit_labels]},
                     'complete_periods': True, 'facts': facts, 'value_kind': 'native_annotation_only',
                     'validation_scope': report['validation_scope'], 'calculator_input_eligible': False}
            report['charts'].append(chart)
            report['facts'].extend(facts)
    report['status'] = 'native_annotations_bound' if report['charts'] else 'incomplete'
    return report


def query_chart_fact(question: str, charts: dict):
    """Select an explicit series/year label or bounded raw-numeric threshold.

    Unknown units remain unknown. Never converts annotations to physical counts
    or calculator inputs. First/crossing comparisons require every chart period.
    """
    def incomplete(reason):
        return {'status': 'incomplete', 'clarification_code': reason, 'fact': None, 'calculator_input_eligible': False}
    if not isinstance(question,str) or not question.strip() or len(question)>1000:
        raise ValueError('chart_question_invalid')
    if not isinstance(charts,dict) or charts.get('schema_version') != _VERSION:
        return incomplete('no_supported_native_chart_manifest')
    text = ' '.join(question.split()).casefold()
    bound = []
    for chart in charts.get('charts',[]):
        for series in chart['series']:
            pattern = r'(?<![a-z0-9])'+re.escape(series.casefold())+r'(?![a-z0-9])'
            if re.search(pattern,text): bound.append((chart,series,pattern))
    if len(bound)!=1:
        return incomplete('chart_series_scope_ambiguous_or_not_explicit')
    chart,series,pattern = bound[0]
    residual = re.sub(pattern,' ',text)
    unit = chart['axis']['unit']
    unit_cues = re.findall(r'[%$€¥]|\b(?:usd|cny|eur|dollars?|percent(?:age)?|people|persons?|counts?|thousands?|millions?|billions?)\b|人数|万元|亿元|百分比',residual)
    if unit_cues:
        # Unit-restricted questions need a separate declared-unit binding API.
        return incomplete('chart_question_unit_or_scale_not_bound')
    threshold = re.search(r'\b(?:below|under)\s+([+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\b',residual)
    first = bool(re.search(r'\bfirst\b',residual))
    crossing = bool(re.search(r'\b(?:drop(?:ped)?|fall|fell)\b',residual))
    threshold_value = Decimal(threshold[1].replace(',','').replace('−','-')) if threshold else None
    if threshold:
        residual = residual[:threshold.start()]+' '+residual[threshold.end():]
    years = re.findall(r'(?<!\d)\d{4}(?!\d)',residual)
    for year in years: residual = re.sub(r'(?<!\d)'+year+r'(?!\d)',' ',residual)
    residual = re.sub(r'\b(?:what|which|when|is|are|was|were|the|number|value|of|for|in|year|did|does|it|its|first|time|drop|dropped|fall|fell|show|please|read|me)\b',' ',residual)
    if re.sub(r'[\s?.,:!]+','',residual):
        return incomplete('chart_question_has_unbound_scope')
    selected = sorted([f for f in chart['facts'] if f['series']==series],key=lambda f:f['year'])
    scope = {key:chart[key] for key in ('chart_id','source_sha256','page_no','title_context','legend_scope','axis','validation_scope')}
    if threshold:
        if years or not chart.get('complete_periods') or [f['year'] for f in selected]!=chart['years']:
            return incomplete('threshold_requires_complete_chart_periods')
        candidates = []
        for index,fact in enumerate(selected):
            if Decimal(fact['numeric_value'])>=threshold_value: continue
            if crossing:
                if index == 0: return incomplete('crossing_predecessor_outside_chart_scope')
                if Decimal(selected[index-1]['numeric_value'])<threshold_value: continue
            candidates.append(fact)
        if not candidates:
            return incomplete('no_matching_chart_period')
        if len(candidates)>1 and not first:
            return incomplete('threshold_period_ambiguous_without_first')
        fact = candidates[0]
        return {'status':'verified','fact':fact,'scope':scope,'unit':unit,'value_kind':'native_annotation_only',
                'threshold_domain':'raw_annotation_numeric','threshold':str(threshold_value),
                'comparison_scope':'all_displayed_chart_periods_only','calculator_input_eligible':False}
    if len(years)!=1:
        return incomplete('chart_lookup_requires_one_explicit_year')
    facts = [f for f in selected if f['year']==int(years[0])]
    if len(facts)!=1:
        return incomplete('chart_series_year_not_uniquely_bound')
    return {'status':'verified','fact':facts[0],'scope':scope,'unit':unit,
            'value_kind':'native_annotation_only','calculator_input_eligible':False}
