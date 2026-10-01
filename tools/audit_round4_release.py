"""Audit saved fourth-round outputs without calling a model or changing scores."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
REPORTS = {
    'core': 'REAL_MODEL_CORE_ROUND4_ACCEPTANCE_20261002.json',
    'northwind': 'NORTHWIND_ROUND4_ACCEPTANCE_20261002.json',
    'rag_exposed': 'OHR_ROUND4_EXPOSED_FINAL_20261002.json',
    'rag_second': 'OHR_ROUND4_SECOND_FINAL_20261002.json',
    'rag_new_documents': 'OHR_ROUND4_UNSEEN_FINAL_20261002.json',
}
RAG_UNUSED_WORKFLOW_FILES = {'ict-track8/backend/omni_agent.py',
                             'ict-track8/backend/fusion_constraints.py'}


def load(name):
    raw = (DOCS / name).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def audit():
    records, backend_snapshots = {}, []
    for scope, filename in REPORTS.items():
        run, digest = load(filename)
        start = run.get('implementation_sha256_start', run.get('implementation_file_sha256'))
        end = run.get('implementation_sha256_end', run.get('implementation_file_sha256_end'))
        assert run['implementation_stable'] is True and start == end, filename
        excluded = RAG_UNUSED_WORKFLOW_FILES if scope.startswith('rag') else set()
        mismatches = [path for path, value in start.items()
                      if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != value]
        assert start and not (set(mismatches) - excluded), filename
        backend_snapshots.append({path: value for path, value in start.items()
                                  if path.startswith('ict-track8/backend/')
                                  and path not in RAG_UNUSED_WORKFLOW_FILES})
        summary = run.get('api_summary')
        if summary is None:
            from evaluate_model import summarise_audits
            audits = [a for row in run['cases'] for a in row.get('api_audits', [])]
            summary = summarise_audits(audits, sum(row.get('audit_dropped_count', 0) for row in run['cases']))
        audits = (run.get('api_audits') or
                  [*run.get('preflight', []), *(a for row in run['cases'] for a in row.get('api_audits', []))])
        assert audits and all(a['model'] == 'gpt-6-luna' and a['reasoning'] == 'medium' for a in audits)
        assert len(audits) == summary['retained_calls'] and summary['dropped_calls'] == 0
        if scope.startswith('rag'):
            assert run['gold_used_as_corpus_or_model_input'] is False
            result = run['summary']['model']
            assert len(run['cases']) == result['attempted'] == 18
            metrics = {k: result[k] for k in ('attempted', 'normalized_exact_matches', 'mean_english_token_f1')}
        else:
            assert run['total'] == len(run['cases']) == (44 if scope == 'core' else 16)
            assert run['passed'] == sum(row['pass'] for row in run['cases'])
            assert len({row['id'] for row in run['cases']}) == run['total']
            metrics = {'passed': run['passed'], 'total': run['total']}
        records[scope] = {'report': filename, 'sha256': digest, 'source_files_checked': len(start) - len(excluded),
                          'stable_and_matches_current_execution_scope': True,
                          'full_backend_matches_current_source': not mismatches,
                          'full_source_mismatches': mismatches, 'excluded_execution_scope': sorted(excluded),
                          'exclusion_reason': ('OHR evaluator calls KnowledgeStore directly, without Omni planning or fusion source binding.'
                                               if excluded else None),
                          'metrics': metrics, 'api_summary': summary}
    assert all(snapshot == backend_snapshots[0] for snapshot in backend_snapshots)
    old, old_digest = load('REAL_MODEL_CORE_ROUND3_RELEASE_20261002.json')
    new, new_digest = load(REPORTS['core'])
    assert old['implementation_stable'] and old['cases_sha256'] == new['cases_sha256']
    assert old['database_sha256'] == new['database_sha256']
    before, after = ({c['id']: c for c in r['cases']} for r in (old, new))
    assert set(before) == set(after) and len(before) == len(after) == 44
    changes = []
    for identifier, b in before.items():
        a = after[identifier]
        assert all(b.get(k) == a.get(k) for k in ('question', 'kind', 'sources', 'contains'))
        if b['pass'] != a['pass'] or not a['pass']:
            changes.append({'id': identifier, 'before_pass': b['pass'], 'after_pass': a['pass'],
                            'before_checks': b['checks'], 'after_checks': a['checks']})
    paired = {'scope': 'paired_exposed_synthetic_core_development_suite_not_official_accuracy',
              'baseline': {'report': 'REAL_MODEL_CORE_ROUND3_RELEASE_20261002.json',
                           'sha256': old_digest, 'passed': old['passed'], 'total': old['total']},
              'release': {'report': REPORTS['core'], 'sha256': new_digest,
                          'passed': new['passed'], 'total': new['total']},
              'cases_sha256': new['cases_sha256'], 'database_sha256': new['database_sha256'],
              'same_inputs_verified': True, 'complete_unique_case_ids_verified': True,
              'better_cases': sum(not c['before_pass'] and c['after_pass'] for c in changes),
              'worse_cases': sum(c['before_pass'] and not c['after_pass'] for c in changes),
              'changes': changes,
              'limitations': ['One run per version does not separate model variation from causal program changes.',
                              'All failed checks and every regression retained. No contest score conversion.']}
    regression, regression_digest = load('LOCAL_REGRESSION_ROUND4_ACCEPTANCE_20261002.json')
    assert regression['ok'] and regression['failed'] == 0
    summary = {'scope': 'saved_fixed_source_fourth_round_acceptance_not_overall_goal_completion',
               'common_backend_source_files_checked': len(backend_snapshots[0]),
               'all_final_runs_have_same_common_backend_scope': True, 'reports': records,
               'local_regression': {'report': 'LOCAL_REGRESSION_ROUND4_ACCEPTANCE_20261002.json',
                                    'sha256': regression_digest, **regression},
               'retained_api_calls': sum(v['api_summary']['retained_calls'] for v in records.values()),
               'completed_api_calls': sum(v['api_summary']['completed_calls'] for v in records.values()),
               'failed_api_calls': sum(v['api_summary']['failed_calls'] for v in records.values()),
               'reported_total_tokens': sum(v['api_summary']['tokens']['total_tokens']['reported_sum'] for v in records.values()),
               'goal_status': 'active',
               'limitations': ['Development replay and new-document subset are separate evidence scopes.',
                               'Small resource-biased samples do not establish official 90 percent accuracy.',
                               'Runtime credentials are never read by this audit.']}
    for filename, data in [('CORE_PAIRED_ROUND4_FINAL_20261002.json', paired),
                           ('ROUND4_FINAL_SOURCE_AUDIT_20261002.json', summary)]:
        with (DOCS / filename).open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps({'source_audit': 'PASS', 'core': records['core']['metrics'],
                      'northwind': records['northwind']['metrics'],
                      'api_calls': summary['retained_api_calls']}, ensure_ascii=False))


if __name__ == '__main__':
    audit()
