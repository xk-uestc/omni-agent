"""Compare pinned rendered pixels with the selected native grid key and value.

Agreement is OCR corroboration, not an independent proof of semantic truth.
No OCR result may replace the server's native literal or supply a new value.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import math
import re
import unicodedata

import fitz
from PIL import Image, ImageOps


def _literal(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def _comparison_literal(text, role):
    literal = _literal(text)
    if role == 'column_header':
        # OCR commonly drops a space between a four-digit year and its label.
        # Preserve digit-digit and word-word spacing, punctuation and case.
        literal = re.sub(r'(?<!\d)((?:19|20)\d{2})\s+(?=[A-Za-z])', r'\1', literal)
    return literal


def verify_visible_grid_fact(asset, tables, fact, pipeline):
    base = {'status': 'unverified', 'scope': 'selected_cell_and_internal_headers_ocr_corroboration',
            'source_sha256': asset.manifest['source_sha256'], 'page_no': asset.manifest['page_no'],
            'render_sha256': asset.manifest['render_sha256'], 'fact_id': fact['fact_id'],
            'checks': [], 'calculator_input_eligible': False}
    if pipeline is None:
        return {**base, 'reason': 'ocr_not_configured'}
    if (hashlib.sha256(asset.png_bytes).hexdigest() != base['render_sha256']
            or any(item.get('source_sha256') != base['source_sha256']
                   or item.get('page_no') != base['page_no'] for item in (tables, fact))):
        raise ValueError('Visible-cell verification source mismatch')
    table = next((t for t in tables['tables'] if t['table_id'] == fact['table_id']), None)
    if table is None:
        raise ValueError('Visible-cell verification table missing')
    cells = {cell['cell_id']: cell for row in table['cells'] for cell in row}
    requests = [('row_header', fact['row_header_cell_id']),
                *[('column_header', key) for key in fact['column_header_cell_ids']],
                ('value', fact['value_cell_id'])]
    if len(requests) > 4:
        raise ValueError('Visible-cell verification budget exceeded')
    matrix = fitz.Matrix(asset.manifest['mappings']['display_to_asset_px'])
    rotation = asset.manifest['rotation_degrees']
    if rotation not in (0, 90, 180, 270):
        raise ValueError('Invalid page rotation')
    with Image.open(BytesIO(asset.png_bytes)) as image:
        if list(image.size) != asset.manifest['size_px'] or image.width * image.height > 6_000_000:
            raise ValueError('Visible-cell verification image bounds mismatch')
        image.load()
        for role, cell_id in requests:
            cell = cells.get(cell_id)
            if cell is None:
                raise ValueError('Visible-cell verification key missing')
            rect = fitz.Rect(cell['bbox_display_pt']) * matrix
            if (not all(math.isfinite(v) for v in rect) or rect.x0 < -0.1 or rect.y0 < -0.1
                    or rect.x1 > image.width + 0.1 or rect.y1 > image.height + 0.1
                    or rect.width < 6 or rect.height < 6):
                return {**base, 'reason': 'selected_cell_outside_render_or_too_small'}
            # Exclude the one-pixel grid stroke, preserving the cell interior.
            box = (max(0, math.ceil(rect.x0) + 1), max(0, math.ceil(rect.y0) + 1),
                   min(image.width, math.floor(rect.x1) - 1), min(image.height, math.floor(rect.y1) - 1))
            crop = image.crop(box).convert('RGB')
            if rotation:
                crop = crop.rotate(rotation, expand=True)
            crop = ImageOps.expand(crop, border=12, fill='white')
            output = BytesIO()
            crop.save(output, format='PNG')
            raw = output.getvalue()
            check = {'role': role, 'cell_id': cell_id, 'crop_sha256': hashlib.sha256(raw).hexdigest(),
                     'comparison': 'nfc_whitespace_with_year_label_boundary' if role == 'column_header' else 'nfc_whitespace',
                     'crop_size_px': list(crop.size), 'status': 'unverified'}
            try:
                payload = pipeline.run(raw, language='chi_sim+eng', max_attempts=1).to_dict()
                if not isinstance(payload, dict):
                    raise ValueError('Invalid OCR payload')
            except Exception:
                check['reason'] = 'ocr_execution_failed'
                base['checks'].append(check)
                return {**base, 'reason': 'ocr_execution_failed'}
            confidence = payload.get('confidence')
            check['confidence'] = confidence if type(confidence) in (int, float) and math.isfinite(confidence) else None
            if (payload.get('status') != 'ok' or not isinstance(payload.get('text'), str)
                    or check['confidence'] is None or not 0.85 <= check['confidence'] <= 1):
                check['reason'] = 'ocr_not_confident'
                base['checks'].append(check)
                return {**base, 'reason': 'ocr_not_confident'}
            if _comparison_literal(payload['text'], role) != _comparison_literal(cell['raw_text'], role):
                check['status'] = 'conflict'
                # Store the observed literal for local source auditing, never use it as an answer.
                check['observed_text'] = payload['text'][:1000]
                base['checks'].append(check)
                return {**base, 'status': 'conflict', 'reason': 'visible_native_literal_mismatch'}
            check['status'] = 'agrees'
            base['checks'].append(check)
    return {**base, 'status': 'corroborated', 'reason': None}
