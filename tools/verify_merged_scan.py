"""Exercise merged-cell extraction using real OCR on a labelled controlled scan.

This is a reproducible development fixture, not a public document benchmark.
"""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import sys
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.ocr import RapidOcrExecutor, OcrPipeline


def main():
    image = Image.new('RGB', (1000, 650), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 32)
    for x in (80,920):
        draw.line((x,80,x,560), fill='black', width=4)
    for y in (80,200,320,440,560):
        draw.line((80,y,920,y), fill='black', width=4)
    draw.line((500,200,500,560), fill='black', width=4)
    for position, text in [((300,120),'Annual Budget'), ((120,240),'Personnel'),
                           ((550,240),'47000'), ((120,360),'Supplies'),
                           ((550,360),'2000'), ((120,480),'Total'), ((550,480),'49000')]:
        draw.text(position, text, font=font, fill='black')
    output = BytesIO()
    image.save(output, format='PNG')
    raw = output.getvalue()
    result = OcrPipeline(RapidOcrExecutor()).run(raw, language='eng').to_dict()
    grids = result['metadata'].get('scanned_grids', {})
    table = grids.get('tables', [{}])[0]
    cells = table.get('cells', [])
    passed = (grids.get('status') == 'observed' and table.get('merged_cell_count') == 1
        and cells[0][0]['colspan'] == 2 and 'Annual' in cells[0][0]['text']
        and cells[1][1]['text'] == '47000' and cells[2][1]['text'] == '2000'
        and cells[3][1]['text'] == '49000')
    report = {'fixture_kind': 'controlled_generated_printed_scan_real_rapidocr',
        'not_public_benchmark': True, 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'passed': passed, 'ocr': result}
    destination = ROOT / 'runtime/merged-scan-real-ocr-20261004.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': passed, 'rows': table.get('row_count'),
        'columns': table.get('column_count'), 'merged': table.get('merged_cell_count'),
        'texts': [[cell['text'] for cell in row] for row in cells]}, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
