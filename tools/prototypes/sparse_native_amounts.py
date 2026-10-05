"""Source-only isolated total annotations; never a table or answer authority."""
import hashlib
import re

import fitz

from backend.native_text_tables import _MONEY, _SCALES, _bbox, _text


def extract(raw, *, page_no, expected_source_sha256):
    if not isinstance(raw, bytes) or not raw or len(raw) > 20*1024*1024:
        raise ValueError('sparse_amount_source_budget')
    sha = hashlib.sha256(raw).hexdigest()
    if sha != expected_source_sha256:
        raise ValueError('sparse_amount_source_sha_mismatch')
    with fitz.open(stream=raw, filetype='pdf') as document:
        if type(page_no) is not int or not 1 <= page_no <= len(document):
            raise ValueError('sparse_amount_page_bounds')
        page = document[page_no-1]
        if page.rotation != 0:
            raise ValueError('sparse_amount_rotation_not_implemented')
        words = page.get_text('words')
        if len(words) > 20000:
            raise ValueError('sparse_amount_word_budget')
        rows = []
        for word in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
            cy = (word[1]+word[3])/2
            if rows and abs(rows[-1]['cy']-cy) <= 2:
                rows[-1]['words'].append(word)
            else:
                rows.append({'cy': cy, 'words': [word]})
        annotations = []
        for row in rows:
            local = sorted(row['words'], key=lambda w: w[0])
            anchors = [(index, word, _MONEY.fullmatch(word[4])) for index, word in enumerate(local)
                       if _MONEY.fullmatch(word[4])]
            for ordinal, (position, word, match) in enumerate(anchors):
                lower = anchors[ordinal-1][1][2]+8 if ordinal else 0
                labels = [w for w in local[:position] if w[0] >= lower and w[2] <= word[0]-2]
                text = _text(labels) if labels else ''
                # A printed total heading is a candidate role, not a inferred
                # table header or semantic relation. Preserve duplicate labels.
                if (not re.match(r'^TOTAL\b', text, re.I) or not 3 <= len(text) <= 200
                        or any(_MONEY.fullmatch(w[4]) for w in labels)):
                    continue
                parts = [word]
                suffix = match.group(3)
                if not suffix and position+1 < len(local):
                    next_word = local[position+1]
                    if next_word[4] in _SCALES and 0 <= next_word[0]-word[2] <= 2:
                        parts.append(next_word)
                        suffix = next_word[4]
                box = list(_bbox(parts))
                identity = hashlib.sha256(f'{sha}:{page_no}:{box}:{text}'.encode()).hexdigest()[:24]
                annotations.append({'annotation_id': identity, 'source_sha256': sha, 'page_no': page_no,
                    'label': text, 'label_bbox_pt': list(_bbox(labels)), 'amount_bbox_pt': box,
                    'raw_value': ''.join(w[4] for w in parts), 'currency_symbol': match.group(1),
                    'scale': _SCALES.get(suffix), 'scale_binding': 'own_literal_suffix_only' if suffix else None,
                    'original_words': [list(w[:5]) for w in labels+parts],
                    'table_identity_verified': False, 'semantic_relation_verified': False,
                    'calculator_input_eligible': False})
        return {'source_sha256': sha, 'page_no': page_no, 'annotations': annotations,
            'scope': 'isolated_native_total_annotation_geometry_not_table_or_question_semantics',
            'model_calls': 0, 'production_answer_authority': False}
