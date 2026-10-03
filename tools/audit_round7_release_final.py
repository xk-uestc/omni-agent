"""Seal the final round-seven source and measurements, retaining failures."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from audit_round6_release import verify_source,api_summary,paired_sakila
from compare_ohr_runs import compare

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs'


def read(path):
    raw=Path(path).read_bytes()
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def main():
    names={'core':'REAL_MODEL_CORE_ROUND7_RELEASE_20261004.json',
        'regression':'LOCAL_REGRESSION_ROUND7_RELEASE_20261004.json',
        'sessions':'ROUND7_NEW_DOMAIN_RELEASE_20261004.json',
        'probes':'ROUND7_COMPLEX_PROBES_RELEASE_20261004.json',
        'rag':'OHR_ROUND7_RELEASE_20261004.json',
        'sakila':'SAKILA_ROUND7_RELEASE_DEVELOPMENT_20261004.json'}
    reports={};pins={}
    for key,name in names.items():reports[key],pins[key]=read(DOCS/name)
    run=Path('D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/round7-release-development-20261004')
    manifest,manifest_sha=read(run/'RUN_MANIFEST.json')
    current={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}
    for report in [*(reports[k] for k in ('core','regression','sessions','probes','rag')),manifest]:
        if verify_source(report)!=current:raise ValueError('different_final_backend_versions')
    core,reg,sessions,probes,rag,sakila=(reports[k] for k in ('core','regression','sessions','probes','rag','sakila'))
    if len(core['cases'])!=44 or core['not_run'] or core['passed']!=sum(c['pass'] for c in core['cases']):
        raise ValueError('core_denominator')
    if not reg['ok'] or reg['failed'] or reg['exit_code']:raise ValueError('regression_not_passed')
    if (len(sessions['sessions'])!=3 or sessions['planned_turns']!=15 or sessions['executed_turns']!=15
        or sessions['reference_used_as_model_input'] is not False
        or any(len(s['cases'])!=5 or s['persisted_turns_after_restart']!=5 or not s['source_stable'] for s in sessions['sessions'])
        or sessions['passed_turns']!=sum(c['pass'] for s in sessions['sessions'] for c in s['cases'])):
        raise ValueError('session_denominator_or_persistence')
    if (len(probes['cases'])!=12 or probes['total']!=12 or probes['reference_used_as_model_input'] is not False
        or probes['passed']!=sum(c['pass'] for c in probes['cases'])):raise ValueError('probe_denominator')
    if (len(rag['cases'])!=36 or len({c['ID'] for c in rag['cases']})!=36
        or len(rag['documents'])!=15 or rag['gold_used_as_corpus_or_model_input'] is not False
        or any(d['ingestion_status']!='ok' for d in rag['documents'])):raise ValueError('rag_denominator')
    raw=(run/'observed.jsonl').read_bytes()
    if (hashlib.sha256(raw).hexdigest()!=manifest['observed_sha256']
        or manifest_sha!=sakila['run_manifest_sha256'] or manifest['reference_opened'] is not False):
        raise ValueError('sakila_provenance')
    observed=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
    if len(observed)!=56 or len({r['case_id'] for r in observed})!=56:raise ValueError('sakila_denominator')
    apis={
        'core':api_summary([*core['preflight'],*(a for c in core['cases'] for a in c['api_audits'])],
            dropped=core['api_summary']['dropped_calls'],saved=core['api_summary']),
        'sessions':api_summary([a for s in sessions['sessions'] for c in s['cases'] for a in c['api_audits']]),
        'probes':api_summary([a for c in probes['cases'] for a in c.get('api_audits',[])]),
        'rag':api_summary(rag['api_audits'],dropped=rag['api_summary']['dropped_calls'],saved=rag['api_summary']),
        'sakila':api_summary([a for r in observed for a in r['api_audits']],dropped=sum(r['api_audit_dropped'] for r in observed)),
    }
    before,_=read(DOCS/'SAKILA_ROUND7_BASELINE_20261004.json')
    rag_pair=compare(DOCS/'OHR_ROUND7_BASELINE_20261004.json',DOCS/names['rag'])
    normalized={path:hashlib.sha256((ROOT/path).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for path in current}
    report={'created_at':datetime.now(timezone.utc).isoformat(),'source_audit':'PASS',
        'scope':'engineering_development_results_not_contest_completion_or_official_score',
        'backend_files_verified':len(current),'backend_file_sha256':current,
        'backend_lf_normalized_sha256':normalized,
        'normalization_scope':'portable_line_endings_only_original_execution_byte_pins_retained',
        'reports':{k:{'path':name,'sha256':pins[k]} for k,name in names.items()},
        'api_summary':apis,'core':{'passed':core['passed'],'total':44},
        'regression':{k:reg[k] for k in ('passed','failed','skipped','warnings','subtests_passed')},
        'new_sessions':{'passed_turns':sessions['passed_turns'],'total':15,
            'complete_five_turn_sessions':sessions['complete_five_turn_sessions'],'sessions':3,
            'expected_clarification_turns':3},
        'complex_sql_and_document_probes':{'passed':probes['passed'],'total':12,
            'sql_passed':sum(c['pass'] for c in probes['cases'][:8]),'sql_total':8,
            'failed_ids':[c['id'] for c in probes['cases'] if not c['pass']],
            'cross_page_formula_is_production_tool_test_not_model_planning_test':True},
        'sakila_paired':paired_sakila(before,sakila),
        'sakila_question_exposure':'all_56_questions_inspected_for_development_diagnosis_after_first_candidate',
        'rag_paired':{k:v for k,v in rag_pair.items() if k!='cases'},
        'limitations':['Only gpt-6-luna/medium was requested. Failed calls and cases remain in denominators.',
            'Authored probes and sessions are development fixtures, not official or blind scores.',
            'Sakila remains an exposed development replay; oracle/reference SQL never goes into model payloads.',
            'OHR is a fixed 15-document, 36-question resource-biased subset, not a full benchmark or contest score.',
            'Independent model review is not a formal semantic proof. Single paired runs do not establish causality.',
            'Native cross-page formulas require adjacent, explicit, geometrically consistent math; arbitrary scans and subscripts are unsupported.',
            'Read-only relational proposals do not bypass typed required_intent in cross-source tasks.',
            'Query execution retains bounded rows; complete artifacts remain explicit opt-in.']}
    with (DOCS/'ROUND7_RELEASE_SOURCE_AUDIT_20261004.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ('source_audit','backend_files_verified','core','regression','new_sessions','complex_sql_and_document_probes')}))


if __name__=='__main__':main()
