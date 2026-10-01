"""Compare frozen paired runs without rescoring or changing saved answers."""
import argparse
import hashlib
import json
from pathlib import Path


def compare(baseline_path, optimized_path):
    baseline_path, optimized_path = Path(baseline_path), Path(optimized_path)
    baseline_raw, optimized_raw = baseline_path.read_bytes(), optimized_path.read_bytes()
    before, after = json.loads(baseline_raw), json.loads(optimized_raw)
    if before.get('implementation_stable') is not True or after.get('implementation_stable') is not True:
        raise ValueError('paired_source_not_stable')
    for key in ('selection_sha256', 'dataset_revision', 'official_code_revision', 'manifest_sha256'):
        if before.get(key) is None or before[key] != after.get(key):
            raise ValueError('paired_input_mismatch')
    for run in (before, after):
        if run.get('model_enabled') is not True or run.get('gold_used_as_corpus_or_model_input') is not False:
            raise ValueError('paired_model_protocol_mismatch')
        if any(a.get('model') != 'gpt-6-luna' or a.get('reasoning') != 'medium'
               for a in run.get('api_audits', [])):
            raise ValueError('paired_model_not_allowed')
    old, new = {c['ID']: c for c in before['cases']}, {c['ID']: c for c in after['cases']}
    if len(old) != len(before['cases']) or len(new) != len(after['cases']) or set(old) != set(new):
        raise ValueError('paired_case_ids_mismatch')
    changes = []
    for identifier in old:
        b, a = old[identifier], new[identifier]
        if any(b.get(k) != a.get(k) for k in ('question', 'official_answer', 'doc_name', 'evidence_source')):
            raise ValueError('paired_question_or_gold_mismatch')
        bg, ag = b['generation'], a['generation']
        bs, ars = bg['scores'], ag['scores']
        changes.append({'ID': identifier, 'question': b['question'], 'category': b['evidence_source'],
                        'before_answer': bg.get('answer'), 'after_answer': ag.get('answer'),
                        'before_status': bg.get('status'), 'after_status': ag.get('status'),
                        'before_exact_match': bs['normalized_exact_match'],
                        'after_exact_match': ars['normalized_exact_match'],
                        'before_f1': bs['english_token_f1'], 'after_f1': ars['english_token_f1']})
    def metrics(run):
        return {k: run['summary']['model'][k] for k in
                ('attempted', 'normalized_exact_matches', 'mean_english_token_f1')}
    bm, am = metrics(before), metrics(after)
    return {'scope': 'paired_resource_biased_official_query_subset_not_full_benchmark_or_unseen_document',
            'baseline': {'path': str(baseline_path), 'sha256': hashlib.sha256(baseline_raw).hexdigest(), **bm},
            'optimized': {'path': str(optimized_path), 'sha256': hashlib.sha256(optimized_raw).hexdigest(), **am},
            'selection_sha256': before['selection_sha256'], 'case_count': len(changes),
            'exact_match_delta': am['normalized_exact_matches'] - bm['normalized_exact_matches'],
            'f1_delta': round(am['mean_english_token_f1'] - bm['mean_english_token_f1'], 6),
            'better_f1_cases': sum(c['after_f1'] > c['before_f1'] for c in changes),
            'worse_f1_cases': sum(c['after_f1'] < c['before_f1'] for c in changes),
            'same_f1_cases': sum(c['after_f1'] == c['before_f1'] for c in changes), 'cases': changes,
            'limitations': ['One run per version: model variation is not statistical significance.',
                            'Fixed small PDF corpus and resource biased sampling; no contest score conversion.',
                            'Saved outputs and scores are used verbatim; failures and regressions are retained.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline', type=Path)
    parser.add_argument('optimized', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.baseline, args.optimized)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('case_count', 'exact_match_delta', 'f1_delta',
                                            'better_f1_cases', 'worse_f1_cases')}, ensure_ascii=False))
