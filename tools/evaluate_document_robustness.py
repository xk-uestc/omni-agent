"""Real OCR on an original public PDF page and explicitly labelled degradations.

No gold answers are passed to the OCR executor. Metrics measure text stability
against upright OCR, not absolute recognition accuracy or contest scores.
"""
from pathlib import Path
from io import BytesIO
import argparse
import hashlib
import json
import sys
import time
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from PIL import Image, ImageEnhance, ImageFilter
import fitz
from backend.ocr import OcrPipeline, RapidOcrExecutor
from backend.chunk_cleaning import DocumentChunker


def png(image):
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--variants', nargs='+', help='Select a bounded subset for rechecking source pages')
    args = parser.parse_args()
    raw = args.pdf.read_bytes()
    with fitz.open(stream=raw, filetype='pdf') as doc:
        page = doc[args.page - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        image = Image.open(BytesIO(pixmap.tobytes('png'))).convert('RGB')
    pipeline = OcrPipeline(RapidOcrExecutor())
    records = []
    variants = [('upright', image),
                ('rotated90', image.rotate(90, expand=True, fillcolor='white')),
                ('rotated180', image.rotate(180, expand=True, fillcolor='white')),
                ('rotated270', image.rotate(270, expand=True, fillcolor='white')),
                ('skew5', image.rotate(5, expand=True, fillcolor='white')),
                ('dark', ImageEnhance.Brightness(image).enhance(.35)),
                ('blur', image.filter(ImageFilter.GaussianBlur(1.2)))]
    reference = ''
    for name, variant in variants:
        if args.variants and name not in args.variants:
            continue
        started = time.monotonic()
        data = png(variant)
        result = pipeline.run(data, language='chi_sim+eng').to_dict()
        compact = ''.join(result['text'].split())
        if not reference:
            reference = compact
        # Scan-only PDF exercises actual ingest; the underlying page image
        # has no native text layer and must reach the OCR pipeline.
        with fitz.open() as scanned:
            scan_page = scanned.new_page(width=variant.width / 2, height=variant.height / 2)
            scan_page.insert_image(scan_page.rect, stream=data)
            parsed = DocumentChunker().parse_pdf(scanned.tobytes(), document_id=name,
                ocr_pipeline=pipeline, language='chi_sim+eng').to_dict()
        record = {'variant': name, 'sha256': hashlib.sha256(data).hexdigest(),
                  'elapsed_seconds': round(time.monotonic() - started, 3),
                  'ocr': result, 'text_stability_vs_upright_ocr': SequenceMatcher(None, reference, compact).ratio(),
                  'pdf_ingest_stats': parsed['stats'], 'chunk_count': len(parsed['chunks'])}
        records.append(record)
        print(json.dumps({k: record[k] for k in ('variant', 'elapsed_seconds', 'text_stability_vs_upright_ocr', 'chunk_count')}) , flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({'source_pdf': str(args.pdf), 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'source_page': args.page, 'not_official_benchmark': True,
            'metric_limitation': 'relative OCR stability, no independent text gold', 'variants': records},
            ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
