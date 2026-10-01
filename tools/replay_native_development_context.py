"""Rebuild omitted native contexts from an explicitly exposed development run.

No question, gold, model, or database mutation is used. This is extraction
diagnosis, not an answer-accuracy score. Old omissions remain in the report.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.pdf_native_context import extract_native_page_context


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contexts(value):
    if isinstance(value, dict):
        if isinstance(value.get('citations'), list) and isinstance(value.get('trace'), list):
            yield value
        for child in value.values():
            yield from contexts(child)
    elif isinstance(value, list):
        for child in value:
            yield from contexts(child)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--development-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw_report = args.development_report.read_bytes()
    report = json.loads(raw_report)
    if report.get('evaluation_purpose') != 'exposed_development_replay':
        raise ValueError('Only explicitly exposed development reports permitted')
    store = Path(report['store_path']).resolve()
    database = store / 'knowledge.sqlite'
    if not database.is_file():
        raise ValueError('Historical store unavailable')
    records, observed = [], set()
    saved_contexts, saved_observed = [], set()
    with sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
        for node in contexts(report.get('cases', [])):
            citations = {row['citation_id']: row for row in node['citations']}
            for citation in node['citations']:
                evidence = citation.get('generation_evidence', {})
                previous = evidence.get('native_context')
                if not previous:
                    continue
                metadata = citation['metadata']
                key = (metadata['document_id'], metadata['chunk_id'])
                if key in saved_observed:
                    continue
                saved_observed.add(key)
                chunk_row = connection.execute('SELECT payload FROM chunks WHERE document_id=? AND chunk_id=?', key).fetchone()
                doc_row = connection.execute('SELECT payload FROM documents WHERE document_id=?', (key[0],)).fetchone()
                if not chunk_row or not doc_row:
                    raise ValueError('Historical selected chunk/document unavailable')
                chunk, document = json.loads(chunk_row[0]), json.loads(doc_row[0])
                source_path = (store / 'assets' / document['asset']).resolve()
                if source_path.parent != (store / 'assets').resolve():
                    raise ValueError('Original asset outside historical store')
                original = source_path.read_bytes()
                if sha(original) != document['sha256'] or document['sha256'] != metadata['source_sha256']:
                    raise ValueError('Historical selected original SHA mismatch')
                current = extract_native_page_context(original, chunk['page_no'], chunk['text'],
                                                      max_chars=evidence['native_context_max_chars'])
                saved_contexts.append({'document_id': key[0], 'chunk_id': key[1], 'page_no': chunk['page_no'],
                    'source_sha256': sha(original), 'old_context_chars': len(previous['text']),
                    'old_mode': previous['mode'], 'current_available': current is not None,
                    'current_context_chars': len(current['text']) if current else 0,
                    'current_mode': current.get('mode') if current else None,
                    'literal_text_changed': current is None or current['text'] != previous['text'],
                    'calculator_input_eligible': current.get('calculator_input_eligible') if current else False})
            for trace in node['trace']:
                if trace.get('stage') != 'generation_evidence':
                    continue
                for omitted in trace.get('omitted', []):
                    if omitted.get('reason') != 'native_complete_context_unavailable':
                        continue
                    citation = citations.get(omitted['citation_id'])
                    if not citation:
                        continue
                    metadata = citation['metadata']
                    key = (metadata['document_id'], metadata['chunk_id'])
                    if key in observed:
                        continue
                    observed.add(key)
                    chunk_row = connection.execute('SELECT payload FROM chunks WHERE document_id=? AND chunk_id=?', key).fetchone()
                    doc_row = connection.execute('SELECT payload FROM documents WHERE document_id=?', (key[0],)).fetchone()
                    if not chunk_row or not doc_row:
                        raise ValueError('Historical chunk/document unavailable')
                    chunk, document = json.loads(chunk_row[0]), json.loads(doc_row[0])
                    source_path = (store / 'assets' / document['asset']).resolve()
                    if source_path.parent != (store / 'assets').resolve():
                        raise ValueError('Original asset outside historical store')
                    original = source_path.read_bytes()
                    if sha(original) != document['sha256'] or document['sha256'] != metadata['source_sha256']:
                        raise ValueError('Historical original SHA mismatch')
                    limit = trace['max_native_chars_per_item']
                    result = extract_native_page_context(original, chunk['page_no'], chunk['text'], max_chars=limit)
                    records.append({'document_id': key[0], 'chunk_id': key[1], 'page_no': chunk['page_no'],
                        'source_sha256': sha(original), 'stored_chunk_sha256': sha(chunk['text'].encode()),
                        'previous_omission': omitted['reason'], 'complete_context_available': result is not None,
                        'context_mode': result.get('mode') if result else None,
                        'context_chars': len(result['text']) if result else 0,
                        'context_sha256': result.get('text_sha256') if result else None,
                        'extraction_version': result.get('extraction_version') if result else None,
                        'member_count': len(result['members']) if result else 0,
                        'max_chars': limit,
                        'calculator_input_eligible': result.get('calculator_input_eligible') if result else False})
    result = {'scope': 'exposed_development_native_extraction_diagnosis_not_answer_accuracy',
        'development_report': str(args.development_report), 'report_sha256': sha(raw_report),
        'questions_gold_or_model_used': False, 'historical_database_open_mode': 'ro_immutable',
        'unique_omitted_chunks_found': len(records),
        'now_complete_contexts': sum(row['complete_context_available'] for row in records),
        'still_unavailable': sum(not row['complete_context_available'] for row in records), 'records': records,
        'saved_selected_contexts': saved_contexts,
        'saved_selected_context_count': len(saved_contexts),
        'saved_selected_literal_changes': sum(row['literal_text_changed'] for row in saved_contexts)}
    if args.development_report.read_bytes() != raw_report:
        raise ValueError('Historical report changed')
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('scope', 'unique_omitted_chunks_found',
        'now_complete_contexts', 'still_unavailable')}))


if __name__ == '__main__':
    main()
