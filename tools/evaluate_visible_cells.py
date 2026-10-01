"""Local OCR pixel/native agreement and hidden-layer conflict development probe."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.ocr import OcrPipeline, RapidOcrExecutor
from backend.visual_evidence import render_pdf_evidence
from backend.visual_tables import extract_pdf_tables
from backend.visual_cell_verification import verify_visible_grid_fact


def source(conflict=False, rotation=0, cropped=False):
    with fitz.open() as document:
        page = document.new_page(width=430, height=280)
        for x in (50, 150, 250, 350):
            page.draw_line((x, 50), (x, 155))
        for y in (50, 85, 120, 155):
            page.draw_line((50, y), (350, y))
        for r, row in enumerate([['Region', '2025 Actual', '2026 Forecast'], ['West', '3578', '4800'], ['East', '9900', '7000']]):
            for c, value in enumerate(row):
                page.insert_text((55 + c * 100, 72 + r * 35), value, fontsize=9)
        if conflict:
            # Image overlay changes visible pixels only, preserving the original native 3578.
            image = Image.new('RGB', (390, 128), 'white')
            font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 36)
            ImageDraw.Draw(image).text((16, 40), '9999', font=font, fill='black')
            output = BytesIO()
            image.save(output, format='PNG')
            page.insert_image(fitz.Rect(151, 86, 249, 119), stream=output.getvalue())
        if cropped:
            page.set_cropbox(fitz.Rect(20, 20, 410, 260))
        page.set_rotation(rotation)
        return document.tobytes()


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    data = Path('D:/ICT8-OfficialDatasets/diag/visible-cell-ocr') / stamp
    data.mkdir(parents=True)
    pipeline = OcrPipeline(RapidOcrExecutor())
    results = []
    for rotation in (0, 90, 180, 270):
      for case, conflict in [('agreement', False), ('visible_hidden_conflict', True)]:
        name = f'{case}-rotate{rotation}-cropbox'
        raw = source(conflict, rotation=rotation, cropped=True)
        (data / f'{name}.pdf').write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        asset = render_pdf_evidence(raw, page_no=1, expected_source_sha256=sha)
        tables = extract_pdf_tables(raw, page_no=1, expected_source_sha256=sha)
        fact = next(f for f in tables['tables'][0]['facts'] if f['fact_key']['row_header'] == 'West'
                    and f['fact_key']['column_header_path'] == ['2025 Actual'])
        result = verify_visible_grid_fact(asset, tables, fact, pipeline)
        expected = 'conflict' if conflict else 'corroborated'
        results.append({'case': name, 'expected': expected, 'passed': result['status'] == expected,
                        'native_value': fact['raw_value'], 'verification': result})
    report = {'scope': 'eight_synthetic_local_pixel_native_ocr_rotation_cropbox_development_cases_not_official_accuracy',
              'external_model_calls': 0, 'data_directory': str(data), 'results': results,
              'passed': sum(r['passed'] for r in results), 'total': len(results)}
    path = ROOT / 'docs' / f'VISIBLE_CELL_OCR_{stamp}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(path), 'passed': report['passed'], 'total': report['total']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
