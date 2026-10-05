"""Verify the running frontend backend on a rotated public scan-only PDF."""
import base64
import hashlib
from io import BytesIO
import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')
    raw = source.read_bytes()
    with fitz.open(stream=raw, filetype='pdf') as document:
        png = document[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes('png')
    with Image.open(BytesIO(png)) as image:
        rotated = image.rotate(90, expand=True, fillcolor='white')
    output = BytesIO()
    rotated.save(output, format='PNG')
    with fitz.open() as scan:
        page = scan.new_page(width=rotated.width/2, height=rotated.height/2)
        page.insert_image(page.rect, stream=output.getvalue())
        assert not page.get_text().strip(), 'verification PDF must contain no text layer'
        payload = {'document_id': 'rotated-public-scan-verification', 'modality': 'pdf',
                   'language': 'chi_sim+eng', 'file_base64': base64.b64encode(scan.tobytes()).decode()}
    request = Request('http://127.0.0.1:8030/api/v1/documents/chunks-preview',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=300) as response:
        result = json.load(response)
    grids = [chunk.get('metadata', {}).get('ocr_metadata', {}).get('scanned_grids', {})
             for chunk in result['chunks']]
    text='\n'.join(chunk.get('text','') for chunk in result['chunks'])
    text_checks={'budget_heading':'budget' in text.casefold(),
                 'total_amount':'100,000' in text,'substantial_text':len(text)>=300}
    report = {'not_official_benchmark': True, 'source_sha256': hashlib.sha256(raw).hexdigest(),
              'scan_has_no_text_layer':True,'controlled_rotation_degrees':90,
              'recognized_text_checks':text_checks,
              'status': 'passed' if result['stats'].get('ocr_succeeded_pages') == [1] and all(text_checks.values()) else 'failed',
              'stats': result['stats'], 'chunk_count': len(result['chunks']),
              'grid_statuses': [grid.get('status') for grid in grids if grid],
              'grid_table_counts': [len(grid.get('tables', [])) for grid in grids if grid]}
    report_path=ROOT / f'runtime/scanned-pdf-http-{uuid.uuid4().hex}.json'
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    print(str(report_path))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
