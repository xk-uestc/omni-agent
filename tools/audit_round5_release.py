"""Audit sealed fifth-round runs without another model request or rescoring."""
import hashlib
import json
from pathlib import Path

from compare_ohr_runs import compare

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'


def load(name):
    raw = (DOCS / name).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def verify_source(run):
    start = run.get('implementation_sha256_start', run.get('implementation_file_sha256'))
    end = run.get('implementation_sha256_end', run.get('implementation_file_sha256_end'))
    assert run['implementation_stable'] and start and start == end
    assert all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
               for path, digest in start.items())
    return {path: digest for path, digest in start.items() if path.startswith('ict-track8/backend/')}


def verify_api(run, *, core=False):
    audits = ([*run['preflight'], *(row for case in run['cases'] for row in case['api_audits'])]
              if core else run['api_audits'])
    summary = run['api_summary']
    assert audits and all(row['model'] == 'gpt-6-luna' and row['reasoning'] == 'medium' for row in audits)
    assert len(audits) == summary['retained_calls'] and summary['dropped_calls'] == 0
    assert summary['completed_calls'] + summary['failed_calls'] == len(audits)
    # A failed call stays failed; missing usage stays unknown. Neither is
    # repaired by another request or omitted from the final denominator.
    return summary


def main():
    rag_name = 'OHR_ROUND5_UNSEEN_ACCEPTANCE_20261002.json'
    core_name = 'REAL_MODEL_CORE_ROUND5_ACCEPTANCE_20261002.json'
    rag, rag_sha = load(rag_name)
    core, core_sha = load(core_name)
    rag_source, core_source = verify_source(rag), verify_source(core)
    assert rag_source == core_source
    assert len(rag['cases']) == len({row['ID'] for row in rag['cases']}) == 36
    assert rag['summary']['model']['attempted'] == 36
    assert len(rag['documents']) == 15 and all(row['ingestion_status'] == 'ok' for row in rag['documents'])
    assert rag['gold_used_as_corpus_or_model_input'] is False
    assert core['planned_cases'] == core['total'] == len(core['cases']) == 44
    assert len({row['id'] for row in core['cases']}) == 44 and not core['not_run']
    assert core['passed'] == sum(row['pass'] for row in core['cases'])
    rag_api, core_api = verify_api(rag), verify_api(core, core=True)
    paired_rag = compare(DOCS / 'OHR_ROUND5_UNSEEN_BASELINE_20261002.json', DOCS / rag_name)
    previous, previous_sha = load('REAL_MODEL_CORE_ROUND4_ACCEPTANCE_20261002.json')
    assert previous['implementation_stable'] and previous['cases_sha256'] == core['cases_sha256']
    assert previous['database_sha256'] == core['database_sha256']
    before, after = ({row['id']: row for row in run['cases']} for run in (previous, core))
    assert set(before) == set(after)
    core_changes = []
    for identifier, old in before.items():
        new = after[identifier]
        assert all(old.get(key) == new.get(key) for key in ('question', 'kind', 'sources', 'contains'))
        if old['pass'] != new['pass'] or not new['pass']:
            core_changes.append({'id': identifier, 'before_pass': old['pass'], 'after_pass': new['pass'],
                'before_checks': old['checks'], 'after_checks': new['checks']})
    regression, regression_sha = load('LOCAL_REGRESSION_ROUND5_ACCEPTANCE_20261002.json')
    assert regression['exit_code'] == 0 and regression['passed'] == 2662 and regression['failed'] == 0
    report = {'scope': 'fixed_source_fifth_round_acceptance_not_overall_goal_completion',
        'backend_sources_verified': len(rag_source), 'same_final_backend': True,
        'rag': {'report': rag_name, 'sha256': rag_sha, 'metrics': rag['summary']['model'], 'api_summary': rag_api},
        'core': {'report': core_name, 'sha256': core_sha, 'passed': core['passed'], 'total': core['total'],
                 'api_summary': core_api, 'changes_from_round4': core_changes,
                 'round4_report_sha256': previous_sha},
        'regression': {'report': 'LOCAL_REGRESSION_ROUND5_ACCEPTANCE_20261002.json',
                       'sha256': regression_sha, 'passed': 2662, 'subtests_passed': 12},
        'intermediate_stopped_run': 'ROUND5_INTERMEDIATE_EVALUATION_STOP_20261002.json',
        'goal_status': 'active',
        'limitations': ['One run per version does not establish statistical significance.',
            'All failures and regressions retained, no best-run selection or score conversion.',
            'Resource-biased official-query subset is not the complete official benchmark.',
            'RAG F1 is lexical overlap, not factual accuracy or evidence entailment.',
            'Interrupted intermediate run has no recoverable API usage total and supplies no scored evidence.',
            'Source extraction and local unit tests cannot replace real model acceptance.']}
    for filename, value in [('OHR_UNSEEN_PAIRED_ROUND5_ACCEPTANCE_20261002.json', paired_rag),
                            ('ROUND5_FINAL_SOURCE_AUDIT_20261002.json', report)]:
        with (DOCS / filename).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps({'source_audit': 'PASS', 'rag_em': rag['summary']['model']['normalized_exact_matches'],
        'rag_f1': rag['summary']['model']['mean_english_token_f1'],
        'core_passed': core['passed'], 'core_total': core['total'],
        'api_failed_calls': rag_api['failed_calls'] + core_api['failed_calls']}))


if __name__ == '__main__':
    main()
