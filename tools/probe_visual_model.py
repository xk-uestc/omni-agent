"""Two image-only transport probes, not visual QA or benchmark accuracy.

Gold numbers are freshly randomized and rasterized. The Responses caller gets
only the question and VisualAsset; no gold, PDF native words or answer hints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.responses_client import GenerationError, StructuredResponses, object_schema
from backend.visual_evidence import render_pdf_evidence
from evaluate_model import report_output, summarise_audits

MODEL = 'gpt-6-luna'
DATA_ROOT = Path('D:/ICT8-OfficialDatasets/diag/visual-model-probe')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def font(size):
    for candidate in ('C:/Windows/Fonts/arial.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def centered(draw, box, text, size=36, fill='black'):
    f = font(size)
    bounds = draw.textbbox((0, 0), text, font=f)
    x = (box[0] + box[2] - (bounds[2] - bounds[0])) / 2 - bounds[0]
    y = (box[1] + box[3] - (bounds[3] - bounds[1])) / 2 - bounds[1]
    draw.text((x, y), text, font=f, fill=fill)


def raster_pdf(image):
    png = BytesIO()
    image.save(png, format='PNG')
    with fitz.open() as document:
        page = document.new_page(width=image.width, height=image.height)
        page.insert_image(page.rect, stream=png.getvalue())
        return document.tobytes()


def build_probes(directory):
    rng = secrets.SystemRandom()
    table_numbers = rng.sample(range(1000, 10000), 3)
    image = Image.new('RGB', (680, 360), 'white')
    draw = ImageDraw.Draw(image)
    centered(draw, (0, 5, 680, 75), 'WAREHOUSE COUNTS', size=30)
    draw.rectangle((40, 80, 640, 330), outline='black', width=3)
    draw.line((340, 80, 340, 330), fill='black', width=3)
    for y in (135, 200, 265):
        draw.line((40, y, 640, y), fill='black', width=2)
    centered(draw, (40, 80, 340, 135), 'Region', size=28)
    centered(draw, (340, 80, 640, 135), 'Units', size=28)
    for i, (label, number) in enumerate(zip(('East', 'West', 'North'), table_numbers)):
        y = 135 + 65 * i
        centered(draw, (40, y, 340, y + 65), label)
        centered(draw, (340, y, 640, y + 65), str(number))
    table = {'id': 'random-table-cell', 'image': image,
             'question': 'Read the image table. What integer is in the West row, Units column? Return only the requested JSON.',
             'gold': {'units': table_numbers[1]},
             'schema': object_schema({'units': {'type': 'integer'}})}

    palette = [('red', (220, 25, 35)), ('blue', (25, 75, 220)),
               ('green', (15, 155, 55)), ('orange', (245, 135, 15))]
    rng.shuffle(palette)
    values = rng.sample(range(10000, 100000), 4)
    image = Image.new('RGB', (600, 420), 'white')
    draw = ImageDraw.Draw(image)
    for i, ((_, color), value) in enumerate(zip(palette, values)):
        x, y = 20 + (i % 2) * 290, 20 + (i // 2) * 200
        box = (x, y, x + 270, y + 180)
        draw.rectangle(box, fill=color)
        # White number panel keeps color identification separate from contrast.
        panel = (x + 25, y + 55, x + 245, y + 125)
        draw.rectangle(panel, fill='white')
        centered(draw, panel, str(value), size=40)
    color = {'id': 'random-color-position', 'image': image,
             'question': 'In the image, read the integer in the lower-left card and identify that card background color. Return only the requested JSON.',
             'gold': {'value': values[2], 'color': palette[2][0]},
             'schema': object_schema({'value': {'type': 'integer'},
                                      'color': {'type': 'string', 'enum': ['red', 'blue', 'green', 'orange']}})}
    result = []
    for probe in (table, color):
        raw = raster_pdf(probe.pop('image'))
        asset = render_pdf_evidence(raw, page_no=1, expected_source_sha256=sha(raw), render_scale=1)
        if asset.manifest['native_words']:
            raise ValueError('probe must have no native answer text')
        pdf_path, png_path = directory / (probe['id'] + '.pdf'), directory / (probe['id'] + '.png')
        # New run directory, exclusive creation: prior evidence cannot be replaced.
        with pdf_path.open('xb') as stream:
            stream.write(raw)
        with png_path.open('xb') as stream:
            stream.write(asset.png_bytes)
        result.append({**probe, 'asset': asset, 'pdf_path': str(pdf_path), 'png_path': str(png_path)})
    return result


def sanitized_result(value, gold):
    if not isinstance(value, dict) or set(value) != set(gold):
        return None
    safe = {}
    for key, expected in gold.items():
        actual = value[key]
        if type(expected) is int:
            if type(actual) is not int or not -1_000_000 <= actual <= 1_000_000:
                return None
        elif actual not in {'red', 'blue', 'green', 'orange'}:
            return None
        safe[key] = actual
    return safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true', help='Generate probe assets without credentials or API calls')
    parser.add_argument('--output', help='A new JSON filename under project docs')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_id = stamp + '-' + secrets.token_hex(4)
    output = report_output(args.output or f'docs/VISUAL_MODEL_PROBE_{run_id}.json')
    directory = DATA_ROOT / run_id
    directory.mkdir(parents=True, exist_ok=False)
    probes = build_probes(directory)
    if args.preview:
        print(json.dumps({'mode': 'preview_no_credentials_no_api', 'probe_count': len(probes),
                          'image_directory': str(directory), 'native_words': [len(p['asset'].manifest['native_words']) for p in probes]}))
        return 0

    # No preflight request: exactly the two probe calls, no retry or repair.
    from model_runtime import enable_local_model, local_model_headers
    configured = enable_local_model(MODEL)
    if configured.get('reasoning') != 'medium':
        raise ValueError('Only gpt-6-luna medium is authorized')
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
                                 model=MODEL, reasoning='medium', http_headers=local_model_headers())
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'model': MODEL, 'reasoning': 'medium',
              'scope': 'two_randomized_image_only_transport_probes_not_complete_visual_qa_or_benchmark',
              'gold_or_native_words_sent_as_text': False, 'requests_planned': 2,
              'image_directory': str(directory), 'status': 'running', 'cases': [],
              'implementation_sha256': {name: sha((ROOT / name).read_bytes()) for name in (
                  'tools/probe_visual_model.py', 'ict-track8/backend/responses_client.py',
                  'ict-track8/backend/visual_evidence.py')},
              'limits': ['Two easy synthetic images do not establish real PDF/table/chart QA accuracy.',
                         'Observed model identity relies on the provider response audit.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    started = time.perf_counter()
    for probe in probes:
        client.reset_audit()
        error_type = None
        try:
            value = client.generate('Read only the provided image. Do not invent unreadable values. Return the exact requested structured JSON.',
                                    {'question': probe['question']}, probe['schema'], name='visual_transport_probe',
                                    max_tokens=1000, image_attachments=[probe['asset']])
            prediction = sanitized_result(value, probe['gold'])
        except (GenerationError, ValueError, TypeError) as exc:
            prediction, error_type = None, type(exc).__name__
        audits = client.audit_history
        verified = len(audits) == 1 and all(a.get('status') == 'completed' and a.get('model_verified') is True
                                           and a.get('model') == MODEL for a in audits)
        case = {'id': probe['id'], 'question': probe['question'], 'gold': probe['gold'], 'prediction': prediction,
                'pass': verified and prediction == probe['gold'], 'error_type': error_type,
                'assets': {'pdf': probe['pdf_path'], 'png': probe['png_path']},
                'manifest': probe['asset'].manifest, 'api_audits': audits,
                'audit_dropped_calls': client.audit_dropped_count}
        report['cases'].append(case)
        report.update(passed=sum(c['pass'] for c in report['cases']), attempted=len(report['cases']))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'id': probe['id'], 'pass': case['pass'], 'error_type': error_type}), flush=True)
        if any(a.get('http_status') in (401, 403) for a in audits):
            break
    audits = [a for case in report['cases'] for a in case['api_audits']]
    report.update(status='passed' if len(report['cases']) == 2 and all(c['pass'] for c in report['cases']) else 'failed',
                  api_summary=summarise_audits(audits, sum(c['audit_dropped_calls'] for c in report['cases'])),
                  wall_ms=round((time.perf_counter() - started) * 1000, 3))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(output), 'status': report['status'], 'passed': report['passed'],
                      'attempted': report['attempted'], 'sha256': sha(output.read_bytes())}), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
