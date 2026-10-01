"""Offline official-style score replay; preserves actual outputs and first runs.

The official retrieval task filters by gold document/page during SCORING ONLY,
then measures normalized word LCS. This does not inject gold into retrieval.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluate_ohr_bench import ROOT, DEFAULT_MANIFEST, load_frozen, doc_id, expected_pages, reference_lcs_recall, answer_scores


def score_record(record, original):
    if record['question'] != original['questions'] or record['doc_name'] != original['doc_name']:
        raise ValueError('record_does_not_match_frozen_official_question')
    row = {'ID': original['ID'], 'evidence_source': original['evidence_source'], 'retrieval': {}}
    for mode, observed in record.get('retrieval', {}).items():
        relevant = [hit['snippet'] for hit in observed.get('hits', [])
                    if hit['document_id'] == doc_id(original['doc_name']) and hit['page_no'] in expected_pages(original)]
        row['retrieval'][mode] = {'official_style_evidence_lcs_recall': reference_lcs_recall('\n\n'.join(relevant), original['evidence_context']) if relevant else 0,
                                  'matching_gold_page_snippets': len(relevant),
                                  'all_evidence_pages_hit': observed.get('all_evidence_pages_hit', False)}
    if 'generation' in record:
        output = record['generation']
        row['generation'] = {'answer_mode': output.get('answer_mode'), 'status': output.get('status'),
                             **answer_scores(output.get('answer', ''), original['answers'])}
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != (ROOT / 'docs').resolve():
        parser.error('Output must be a new file directly in docs; no overwrite')
    manifest, _, official = load_frozen(args.manifest, args.data_root)
    originals = {row['ID']: row for row in official}
    raw = args.report.read_bytes()
    original_report = json.loads(raw)
    if original_report['selection_sha256'] != manifest['selected_qa_sha256']:
        raise ValueError('report_selection_sha_mismatch')
    rows = [score_record(row, originals[row['ID']]) for row in original_report['cases']]
    summary = {}
    for mode in ('bm25', 'hybrid'):
        values = [row['retrieval'][mode]['official_style_evidence_lcs_recall'] for row in rows if mode in row['retrieval']]
        summary[mode] = {'measured': len(values), 'mean_official_style_evidence_lcs_recall': round(sum(values) / len(values), 6) if values else None}
    generations = [row['generation'] for row in rows if 'generation' in row]
    if generations:
        summary['generation'] = {'attempted': len(generations), 'grounded': sum(row['answer_mode'] == 'model_grounded' for row in generations),
                                 'grounded_substantive_answers': sum(row['answer_mode'] == 'model_grounded' and row['status'] == 'ok' for row in generations),
                                 'model_abstentions': sum(row['answer_mode'] == 'model_grounded' and row['status'] == 'insufficient_evidence' for row in generations),
                                 'extractive_fallbacks': sum(row['answer_mode'] == 'extractive_fallback' for row in generations),
                                 'normalized_exact_matches': sum(row['normalized_exact_match'] for row in generations),
                                 'mean_token_f1_including_fallback': round(sum(row['english_token_f1'] for row in generations) / len(generations), 6)}
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'offline_metric_replay_of_frozen_actual_outputs_no_api_no_retrieval_rerun',
              'source_report': str(args.report.resolve()), 'source_report_sha256': hashlib.sha256(raw).hexdigest(),
              'selection_sha256': manifest['selected_qa_sha256'],
              'official_retrieval_scoring_source': f'https://github.com/opendatalab/OHR-Bench/blob/{manifest["official_code_revision"]}/src/tasks/retrieval.py',
              'metric_fix_note': 'First runner LCS diagnostic used all hits and stringified evidence lists. Corrected replay joins evidence strings and filters original retrieved hits by gold doc/page during scoring only; original report is preserved.',
              'limits': ['Pilot is 12 resource-biased official questions in 7 PDFs, not full benchmark.', 'Scores use snippet outputs of project top4 chunks; official retriever chunking/corpus differs.',
                         'grounded counts include validated abstentions; substantive answers and abstentions are reported separately.',
                         'Model score includes program fallback outputs; no bare-model accuracy claim.', 'Lexical F1/EM does not establish semantic factual correctness.'],
              'cases': rows, 'summary': summary}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'summary': summary, 'output': str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
