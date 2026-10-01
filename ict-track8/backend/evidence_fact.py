"""Conservative numeric facts from verified, explicitly scoped search excerpts.

This handles one numeric assertion per clause, not general language entailment.
Ranges, multiple numbers with the requested unit, and conflicting sources abstain.
"""
from __future__ import annotations

import math
import re


_DURATION_UNITS = frozenset({'小时', '分钟', '天', '日', '个月'})


def _label_span(clause, label, unit):
    """Bind a duration's event label; never replace it with a synonym.

    “首次响应时间” and “2小时内首次响应” express the same literal
    event with the measurement expressed by the explicit unit. Only a
    terminal measurement noun may be removed; event qualifiers stay intact.
    Non-duration labels and dates are deliberately not normalized.
    """
    if label in clause:
        return label, clause.index(label), 'literal_label'
    if unit not in _DURATION_UNITS:
        return None
    match = re.fullmatch(r'(.{2,})(?:时间|时长|耗时|小时数|分钟数|天数|月数)', label)
    if not match:
        return None
    event = match.group(1)
    if (event not in clause or clause.count(event) != 1
            or re.search(r'日期|时刻|年份|月份|日历|截止日', event)):
        return None
    suffix = label[len(event):]
    explicit_suffix_units = {'小时数': '小时', '分钟数': '分钟', '天数': '天', '月数': '个月'}
    if suffix in explicit_suffix_units and explicit_suffix_units[suffix] != unit:
        return None
    return event, clause.index(event), 'literal_event_explicit_duration_unit'


def _event_duration_relation(clause, event, numeric_match):
    """Require a local duration-to-event relation, not just co-occurrence."""
    amount = re.escape(numeric_match.group())
    literal_event = re.escape(event)
    before_event = re.search(amount + r'(?:内|以内|之内)(?:完成|进行)?' + literal_event
                             + r'(?![A-Za-z0-9_\u4e00-\u9fff])', clause)
    after_event = re.search(literal_event + r'(?:要求|期限)?(?:为|是|[:：])?' + amount + r'$', clause)
    return bool(before_event or after_event)


def extract_search_fact(knowledge, evidence, *, scope, label, unit):
    if any(not isinstance(text, str) or not 1 <= len(text.strip()) <= 50 for text in (scope, label, unit)):
        raise ValueError('事实定位必须明确适用对象、要素与单位')
    if unit not in {'小时', '分钟', '天', '日', '个月', '元', 'CNY', '%'}:
        raise ValueError('事实单位未受支持；不得自动推断或转换单位')
    if not isinstance(evidence, dict) or not isinstance(evidence.get('hits'), list):
        raise ValueError('事实提取必须引用真实检索结果')
    sources = []
    number = re.compile(r'(?<![\d.+-])\d+(?:\.\d+)?\s*' + re.escape(unit) + r'(?![A-Za-z])')
    for hit in evidence['hits']:
        if not isinstance(hit, dict) or not isinstance(hit.get('metadata'), dict):
            raise ValueError('检索证据结构无效')
        metadata = hit['metadata']
        document = knowledge.document(metadata.get('document_id'))
        chunk = next((c for c in document['chunks'] if c['chunk_id'] == metadata.get('chunk_id')), None)
        expected_uri = f'/api/v1/knowledge/documents/{document["document_id"]}/original'
        snippet = hit.get('snippet')
        if (chunk is None or not isinstance(snippet, str) or not snippet.strip() or snippet not in chunk['text']
                or metadata.get('source_sha256') != document['sha256']
                or metadata.get('source_locator') != chunk['source_locator'] or hit.get('source_uri') != expected_uri):
            raise ValueError('检索摘录与当前原文件、切片或来源不一致')
        # Keep the original quote; whitespace normalization is for matching only.
        for raw_clause in re.split(r'[，,。；;\r\n]', snippet):
            quote = raw_clause.strip()
            clause = re.sub(r'\s+', '', quote)
            bound_label = _label_span(clause, label, unit)
            if scope not in clause or bound_label is None:
                continue
            if re.search(r'不是|并非|不适用|无需|不用|否认|未|没有|没在|不得|不能|不应|禁止|不许|至少|超过|大于|[?？<>≥]', clause):
                raise ValueError('否定、下界或疑问条款不能简化为唯一数值事实')
            if re.search(r'\d+(?:\.\d+)?(?:至|到|~|～|—|-)\d', clause):
                raise ValueError('数值范围不能作为唯一数值事实')
            matches = list(number.finditer(clause))
            if len(matches) != 1:
                if matches:
                    raise ValueError('同一适用条款含多个数值，必须澄清')
                continue
            match = matches[0]
            literal_label, label_start, binding_method = bound_label
            if (binding_method == 'literal_event_explicit_duration_unit'
                    and not _event_duration_relation(clause, literal_label, match)):
                continue
            if min(abs(match.end()-label_start), abs(match.start()-(label_start+len(literal_label)))) > 20:
                continue
            raw_number = match.group().removesuffix(unit).strip()
            value = float(raw_number) if '.' in raw_number else int(raw_number)
            if not math.isfinite(value):
                raise ValueError('事实不是有限数字')
            sources.append({'value': value, 'unit': unit, 'source_uri': expected_uri,
                            'locator': chunk['source_locator'], 'chunk_id': chunk['chunk_id'],
                            'sha256': document['sha256'], 'document_id': document['document_id'], 'quote': quote,
                            'label_binding': {'method': binding_method, 'requested_label': label,
                                              'literal_label': literal_label,
                                              'normalized_clause_start': label_start,
                                              'normalized_clause_end': label_start+len(literal_label)}})
    if not sources:
        raise ValueError('检索摘录没有适用对象、要素与显式单位相符的唯一事实')
    if len({item['value'] for item in sources}) != 1:
        raise ValueError('检索来源的数值相互冲突，请明确适用版本')
    unique = list({(s['document_id'], s['chunk_id'], s['quote']): s for s in sources}.values())
    return {**unique[0], 'scope': scope, 'label': label, 'sources': unique,
            'validation': 'literal_scoped_numeric_fact_not_general_entailment'}
