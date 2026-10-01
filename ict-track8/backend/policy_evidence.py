"""Select explicitly dated policy statements, stopping on overlapping versions."""
import re
import math
from datetime import date


def select_policy(document, *, as_of, label):
    target = date.fromisoformat(as_of)
    if not label or len(label) > 50:
        raise ValueError('必须给出明确政策要素')
    candidates = []
    for chunk in document['chunks']:
        for line in chunk['text'].splitlines():
            if label not in line:
                continue
            window = re.search(r'(?:生效区间为|有效期[：:]?)(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})', line)
            since = re.search(r'自(\d{4}-\d{2}-\d{2})起生效', line)
            if not window and not since:
                continue
            start = date.fromisoformat((window or since).group(1))
            end = date.fromisoformat(window.group(2)) if window else None
            if end and end < start:
                raise ValueError('政策生效区间非法')
            if start <= target and (end is None or target <= end):
                # Remove only explicit validity metadata. Commas also delimit
                # applicability clauses, so they must not truncate the value.
                statement = (window or since).re.sub('', line).strip(' ，,。；;')
                statement = re.sub(r'^\d{4}版\s*', '', statement)
                value = re.search(re.escape(label) + r'(?:为|[：:=])\s*(.+)', statement)
                prefix = statement[:value.start()].strip() if value else statement
                plain_prefix = not prefix
                raw = value.group(1).strip(' ，,。；;') if value and plain_prefix else statement
                candidates.append({'value': raw, 'text': line.strip(),
                    'document_id': document['document_id'], 'label': label, 'evidence_type': 'policy',
                    'valid_from': start.isoformat(), 'valid_to': end.isoformat() if end else None,
                    'as_of': target.isoformat(), 'source_uri': f'/api/v1/knowledge/documents/{document["document_id"]}/original',
                    'locator': chunk['source_locator'], 'sha256': document['sha256']})
    unique = {(item['text'], item['valid_from'], item['valid_to']): item for item in candidates}
    if len(unique) != 1:
        raise ValueError('政策在该日期缺失或版本重叠；请澄清版本与适用日期')
    result = next(iter(unique.values()))
    # Only a complete, explicitly unit-bearing scalar is numeric. Preserve
    # raw clauses/ranges/qualifiers as text; never extract a number from them.
    raw = result['value']
    quantity = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)\s*(小时|分钟|个月|月|天|日|CNY|元|%)', raw)
    result['raw_value'], result['unit'] = raw, 'unknown'
    if quantity:
        numeric = float(quantity[1]) if '.' in quantity[1] else int(quantity[1])
        if not math.isfinite(numeric):
            raise ValueError('政策数值必须为有限值')
        # Public helper retains its string contract. The typed execution tool
        # may explicitly project this separately validated numeric field.
        result['numeric_value'] = numeric
        result['unit'] = {'天':'日', '月':'个月', '元':'CNY'}.get(quantity[2], quantity[2])
    return result
