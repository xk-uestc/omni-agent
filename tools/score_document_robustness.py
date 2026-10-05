"""Independently score stored OCR against public page transcription.

Gold files are read only here, never by the OCR executor. Formatting markers
are normalized explicitly; CER measures transcription, not question accuracy.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from rapidfuzz.distance import Levenshtein


def normalize(text):
    text = re.sub(r'\\([%&_])', r'\1', text)
    text = text.replace('$', '')
    return ''.join(text.split()).casefold()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('gold', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    raw = args.gold.read_bytes()
    gold = json.loads(raw)
    page = report['source_page'] - 1
    texts = [item['text'] for item in gold if item['page_idx'] == page]
    if len(texts) != 1:
        raise ValueError('expected exactly one independently transcribed page')
    reference = normalize(texts[0])
    if not reference:
        raise ValueError('empty page transcription')
    results = []
    for item in report['variants']:
        observed = normalize(item['ocr']['text'])
        distance = Levenshtein.distance(reference, observed)
        results.append({'variant': item['variant'], 'character_error_rate': distance / len(reference),
            'edit_distance': distance, 'reference_characters': len(reference),
            'observed_characters': len(observed), 'ocr_status': item['ocr']['status']})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'not_official_contest_score': True,
        'metric': 'page_transcription_CER_casefold_whitespace_and_latex_percent_normalized',
        'ocr_report_sha256': hashlib.sha256(args.report.read_bytes()).hexdigest(),
        'gold_sha256': hashlib.sha256(raw).hexdigest(), 'source_page': page + 1,
        'limitations': 'Public transcription may omit logos and layout; CER is not QA or table-cell accuracy.',
        'results': results}, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
