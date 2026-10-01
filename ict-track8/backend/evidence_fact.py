"""Conservative numeric facts from verified, explicitly scoped search excerpts.

This handles one numeric assertion per clause, not general language entailment.
Ranges, multiple numbers with the requested unit, and conflicting sources abstain.
"""
from __future__ import annotations

import math
import re


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
            if scope not in clause or label not in clause:
                continue
            if re.search(r'不是|并非|不适用|无需|不用|否认|至少|超过|大于|[?？<>≥]', clause):
                raise ValueError('否定、下界或疑问条款不能简化为唯一数值事实')
            if re.search(r'\d+(?:\.\d+)?(?:至|到|~|～|—|-)\d', clause):
                raise ValueError('数值范围不能作为唯一数值事实')
            matches = list(number.finditer(clause))
            if len(matches) != 1:
                if matches:
                    raise ValueError('同一适用条款含多个数值，必须澄清')
                continue
            match = matches[0]
            label_start = clause.index(label)
            if min(abs(match.end()-label_start), abs(match.start()-(label_start+len(label)))) > 20:
                continue
            raw_number = match.group().removesuffix(unit).strip()
            value = float(raw_number) if '.' in raw_number else int(raw_number)
            if not math.isfinite(value):
                raise ValueError('事实不是有限数字')
            sources.append({'value': value, 'unit': unit, 'source_uri': expected_uri,
                            'locator': chunk['source_locator'], 'chunk_id': chunk['chunk_id'],
                            'sha256': document['sha256'], 'document_id': document['document_id'], 'quote': quote})
    if not sources:
        raise ValueError('检索摘录没有适用对象、要素与显式单位相符的唯一事实')
    if len({item['value'] for item in sources}) != 1:
        raise ValueError('检索来源的数值相互冲突，请明确适用版本')
    unique = list({(s['document_id'], s['chunk_id'], s['quote']): s for s in sources}.values())
    return {**unique[0], 'scope': scope, 'label': label, 'sources': unique,
            'validation': 'literal_scoped_numeric_fact_not_general_entailment'}
