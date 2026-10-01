"""Select explicitly dated policy statements, stopping on overlapping versions."""
import re
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
                value = re.search(re.escape(label) + r'(?:为|[：:=])\s*([^，,。；;\n]+)', line)
                candidates.append({'value': value.group(1).strip() if value else line.strip(), 'text': line.strip(),
                    'valid_from': start.isoformat(), 'valid_to': end.isoformat() if end else None,
                    'as_of': target.isoformat(), 'source_uri': f'/api/v1/knowledge/documents/{document["document_id"]}/original',
                    'locator': chunk['source_locator'], 'sha256': document['sha256']})
    unique = {(item['text'], item['valid_from'], item['valid_to']): item for item in candidates}
    if len(unique) != 1:
        raise ValueError('政策在该日期缺失或版本重叠；请澄清版本与适用日期')
    return next(iter(unique.values()))
