"""Reversible text repair previews; source evidence is never overwritten."""
from __future__ import annotations

import hashlib
import re
import threading
from functools import lru_cache

from opencc import OpenCC

NORMALIZATION_ID = 'opencc-python-reimplemented-0.1.7:t2s:v1'
_LOCAL = threading.local()
# Deliberately bounded business lexicon, not a general Chinese spell checker.
_TYPO_RULES = {'销受额': '销售额', '保修其': '保修期', '首欠响应': '首次响应',
               '退货申清': '退货申请', '报销标淮': '报销标准', '订单金颔': '订单金额',
               '銷受額': '销售额', '首欠響應': '首次响应', '退貨申清': '退货申请',
               '報銷標淮': '报销标准', '訂單金頷': '订单金额'}
_TYPO_RE = re.compile('|'.join(map(re.escape, _TYPO_RULES)))
_PROTECTED_RE = re.compile(r'```[\s\S]*?```|`[^`\n]*`|https?://[^\s<>，。；！？]+')


def simplify_for_retrieval(text: str) -> str:
    return _simplify_cached(text) if len(text) <= 4000 else _simplify(text)


@lru_cache(maxsize=512)
def _simplify_cached(text: str) -> str:
    return _simplify(text)


def _simplify(text: str) -> str:
    if not hasattr(_LOCAL, 'converter'):
        _LOCAL.converter = OpenCC('t2s')
    parts, position = [], 0
    for match in _PROTECTED_RE.finditer(text):
        parts.extend([_LOCAL.converter.convert(text[position:match.start()]), match.group()])
        position = match.end()
    parts.append(_LOCAL.converter.convert(text[position:]))
    return ''.join(parts)


def text_quality(text: str, *, include_preview: bool = False) -> dict:
    if not isinstance(text, str):
        raise ValueError('文本必须为字符串')
    protected = [(m.start(), m.end()) for m in _PROTECTED_RE.finditer(text)]
    candidates = []
    candidate_count = 0
    offset = 0
    converted_characters = 0
    converted_lines = 0
    # Line-by-line conversion preserves source line numbers even for long files.
    normalized = simplify_for_retrieval(text)
    for line, converted in zip(text.splitlines(keepends=True), normalized.splitlines(keepends=True)):
        if converted != line:
            converted_lines += 1
            converted_characters += sum(a != b for a, b in zip(line, converted)) + abs(len(line)-len(converted))
        for match in _TYPO_RE.finditer(line):
            start, end = offset + match.start(), offset + match.end()
            if any(start < b and end > a for a, b in protected):
                continue
            candidate_count += 1
            if len(candidates) < 100:
                before = match.group()
                identity = hashlib.sha256(f'{start}:{end}:{before}:{_TYPO_RULES[before]}'.encode()).hexdigest()[:16]
                candidates.append({'id': identity, 'start': start, 'end': end, 'before': before,
                    'after': _TYPO_RULES[before], 'line_no': text.count('\n', 0, start)+1,
                    'status': 'requires_confirmation', 'rule': 'bounded_business_lexicon'})
        offset += len(line)
    report = {'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'normalization': NORMALIZATION_ID, 'original_preserved': True,
        'changed_lines': converted_lines, 'changed_characters': converted_characters,
        'typo_candidate_count': candidate_count, 'typo_candidates': candidates,
        'candidates_truncated': candidate_count > len(candidates),
        'warnings': (['traditional_variant_detected'] if converted_lines else []) +
                    (['possible_typo_requires_confirmation'] if candidate_count else []) +
                    (['unrecognized_character'] if '\ufffd' in text else []),
        'limits': ['bounded_lexicon_not_general_spell_checker', 'unknown_names_and_numeric_errors_not_repaired',
                   'normalized_text_is_not_a_literal_source_quote']}
    if include_preview:
        report['simplified_preview'] = normalized
    return report


def repair_preview(text: str, *, source_sha256: str, accepted_ids: list[str], simplify: bool = True) -> dict:
    report = text_quality(text)
    if report['source_sha256'] != source_sha256:
        raise ValueError('原文已变化，请重新检查后确认')
    indexed = {item['id']: item for item in report['typo_candidates']}
    if len(set(accepted_ids)) != len(accepted_ids) or any(item not in indexed for item in accepted_ids):
        raise ValueError('校正选项不属于当前原文或重复')
    applied = sorted([indexed[item] for item in accepted_ids], key=lambda item: item['start'])
    parts, position = [], 0
    for item in applied:
        parts.extend([text[position:item['start']], item['after']])
        position = item['end']
    parts.append(text[position:])
    revised = ''.join(parts)
    if simplify:
        revised = simplify_for_retrieval(revised)
    return {'status': 'preview_only', 'original_preserved': True, 'source_sha256': source_sha256,
            'revised_sha256': hashlib.sha256(revised.encode()).hexdigest(),
            'revised_text': revised, 'applied_edits': applied, 'simplify': simplify,
            'warning': '校正预览不是原始证据；系统没有修改文件、切片或知识库。'}
