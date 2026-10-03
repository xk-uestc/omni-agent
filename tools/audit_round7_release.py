"""Seal completed round-seven reports against one unchanged backend version."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_round6_release import verify_source, api_summary, paired_sakila
from compare_ohr_runs import compare

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs'


def read(path):
    raw=Path(path).read_bytes()
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def main():
    names={'core':'REAL_MODEL_CORE_ROUND7_20261004.json',
           'rag':'OHR_ROUND7_OPTIMIZED_20261004.json',
           'regression':'LOCAL_REGRESSION_ROUND7_FINAL_20261004.json',
           'sessions':'ROUND7_NEW_DOMAIN_REAL_MODEL_20261004.json',
           'sakila':'SAKILA_ROUND7_OPTIMIZED_20261004.json'}
    reports={};pins={}
    for key,name in names.items():reports[key],pins[key]=read(DOCS/name)
    run=Path('D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/round7-optimized-20261004')
    manifest,manifest_sha=read(run/'RUN_MANIFEST.json')
    current={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}
    for report in [reports['core'],reports['rag'],reports['regression'],reports['sessions'],manifest]:
        if verify_source(report)!=current:raise ValueError('different_final_backend_versions')
    core=reports['core'];rag=reports['rag'];sessions=reports['sessions'];reg=reports['regression'];sakila=reports['sakila']
    if (core['total']!=44 or len(core['cases'])!=44 or core['not_run']
            or core['passed']!=sum(c['pass'] for c in core['cases'])):raise ValueError('core_counts_invalid')
    if (len(rag['cases'])!=36 or len({c['ID'] for c in rag['cases']})!=36
            or rag['gold_used_as_corpus_or_model_input'] is not False or len(rag['documents'])!=15
            or any(d['ingestion_status']!='ok' for d in rag['documents'])):raise ValueError('rag_counts_invalid')
    if not reg['ok'] or reg['failed'] or reg['exit_code']:raise ValueError('regression_not_passed')
    if (sessions['executed_turns']!=15 or sessions['planned_turns']!=15 or len(sessions['sessions'])!=3
            or sessions['reference_used_as_model_input'] is not False
            or any(len(s['cases'])!=5 or s['persisted_turns_after_restart']!=5 or not s['source_stable']
                   for s in sessions['sessions'])
            or sessions['passed_turns']!=sum(c['pass'] for s in sessions['sessions'] for c in s['cases'])):
        raise ValueError('sessions_counts_or_source_invalid')
    raw=(run/'observed.jsonl').read_bytes()
    if (hashlib.sha256(raw).hexdigest()!=manifest['observed_sha256'] or manifest_sha!=sakila['run_manifest_sha256']
            or manifest['reference_opened'] is not False):raise ValueError('sakila_provenance_invalid')
    observed=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
    if len(observed)!=56 or len({r['case_id'] for r in observed})!=56:raise ValueError('sakila_counts_invalid')
    apis={
        'core':api_summary([*core['preflight'],*(a for c in core['cases'] for a in c['api_audits'])],
                           dropped=core['api_summary']['dropped_calls'],saved=core['api_summary']),
        'rag':api_summary(rag['api_audits'],dropped=rag['api_summary']['dropped_calls'],saved=rag['api_summary']),
        'sessions':api_summary([a for s in sessions['sessions'] for c in s['cases'] for a in c['api_audits']]),
        'sakila':api_summary([a for r in observed for a in r['api_audits']],dropped=sum(r['api_audit_dropped'] for r in observed)),
    }
    before,_=read(DOCS/'SAKILA_ROUND7_BASELINE_20261004.json')
    paired=compare(DOCS/'OHR_ROUND7_BASELINE_20261004.json',DOCS/names['rag'])
    report={'created_at':datetime.now(timezone.utc).isoformat(),'source_audit':'PASS',
            'scope':'engineering_and_development_effects_not_contest_goal_completion',
            'backend_files_verified':len(current),'backend_file_sha256':current,
            'reports':{key:{'path':name,'sha256':pins[key]} for key,name in names.items()},
            'api_summary':apis,'core':{'passed':core['passed'],'total':44},
            'regression':{k:reg[k] for k in ('passed','failed','skipped','warnings','subtests_passed')},
            'new_sessions':{'passed_turns':sessions['passed_turns'],'total':15,
                            'all_five_pass':sessions['complete_five_turn_sessions'],'sessions':3},
            'sakila_paired':paired_sakila(before,sakila),
            'rag_paired':{k:v for k,v in paired.items() if k!='cases'},
            'limitations':['New sessions are authored development fixtures, not blind or official benchmark cases.',
                          'Sakila questions are authored; OHR uses a fixed resource-biased subset repeated after first exposure.',
                          'Single paired runs cannot establish causal improvement or statistical significance.',
                          'All failures and regressions are retained. Generalization and contest accuracy remain unfinished.']}
    with (DOCS/'ROUND7_FINAL_SOURCE_AUDIT_20261004.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'source_audit':'PASS','backend_files_verified':len(current),
                      'core':report['core'],'new_sessions':report['new_sessions'],
                      'sakila_delta':report['sakila_paired']['delta'],'rag_f1_delta':paired['f1_delta']}))


if __name__=='__main__':main()
