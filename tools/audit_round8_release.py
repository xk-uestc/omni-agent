"""Audit final same-version reports without replacing retained failed runs."""
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path

from audit_round6_release import verify_source,api_summary
from compare_ohr_runs import compare

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    raw=Path(path).read_bytes()
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suffix',default='FINAL')
    parser.add_argument('--core-report',type=Path,help='Explicit retained full run after an incomplete transport preflight')
    parser.add_argument('--regression-report',type=Path,help='Explicit retained rerun after an environmental budget failure')
    parser.add_argument('--retain-failed-regression', action='store_true',
                        help='Verify source provenance while retaining failed full regression; never mark acceptance passed')
    parser.add_argument('--run-directory',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():parser.error('New docs report required')
    names={'regression':f'LOCAL_REGRESSION_ROUND8_{args.suffix}_20261004.json',
        'core':f'REAL_MODEL_CORE_ROUND8_{args.suffix}_20261004.json',
        'sessions':f'ROUND8_NEW_DOMAIN_{args.suffix}_20261004.json',
        'probes':f'ROUND8_COMPLEX_PROBES_{args.suffix}_20261004.json',
        'raster':f'ROUND8_RASTER_DOCUMENTS_{args.suffix}_20261004.json',
        'topics':f'ROUND8_TOPIC_SESSIONS_{args.suffix}_20261004.json',
        'rag':f'OHR_ROUND8_{args.suffix}_20261004.json',
        'sakila':f'SAKILA_ROUND8_{args.suffix}_DEVELOPMENT_20261004.json',
        'artifact':f'SAKILA_ROUND8_ARTIFACT_DELIVERY_{args.suffix}_20261004.json'}
    if args.core_report:
        if args.core_report.resolve().parent!=(ROOT/'docs').resolve():parser.error('Core report must be in docs')
        names['core']=args.core_report.name
    if args.regression_report:
        if args.regression_report.resolve().parent!=(ROOT/'docs').resolve():parser.error('Regression report must be in docs')
        names['regression']=args.regression_report.name
    reports={};pins={}
    for key,name in names.items():reports[key],pins[key]=read(ROOT/'docs'/name)
    current={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}
    manifest,msha=read(args.run_directory/'RUN_MANIFEST.json')
    for key in ('regression','core','sessions','probes','raster','topics','rag'):
        if verify_source(reports[key])!=current:raise ValueError('source_version_mismatch_'+key)
    if verify_source(manifest)!=current:raise ValueError('sakila_source_version_mismatch')
    raw=(args.run_directory/'observed.jsonl').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['observed_sha256'] or manifest['reference_opened']:
        raise ValueError('sakila_observations_or_reference_contract')
    if reports['sakila']['run_manifest_sha256']!=msha or reports['artifact']['run_manifest_sha256']!=msha:
        raise ValueError('sakila_score_manifest_mismatch')
    observed=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
    if len(observed)!=56 or len({r['case_id'] for r in observed})!=56:raise ValueError('sakila_denominator')
    reg,core,sessions,probes,raster,rag=(reports[k] for k in ('regression','core','sessions','probes','raster','rag'))
    if not reg['ok'] and not args.retain_failed_regression:raise ValueError('local_regression_failed')
    if len(core['cases'])!=44 or core['not_run']:raise ValueError('core_denominator')
    if sessions['executed_turns']!=15 or any(s['persisted_turns_after_restart']!=5 for s in sessions['sessions']):
        raise ValueError('session_persistence_or_denominator')
    if probes['total']!=12 or len(probes['cases'])!=12:raise ValueError('probe_denominator')
    if raster['total']!=4 or raster['executed']!=4 or raster['reference_used_as_model_input']:
        raise ValueError('raster_denominator_or_reference')
    topics=reports['topics']
    if (topics['executed_turns']!=10 or topics['reference_used_as_model_input']
            or any(s['persisted_turns_after_restart']!=5 or not s['source_stable'] for s in topics['sessions'])):
        raise ValueError('topic_session_persistence_or_denominator')
    if len(rag['cases'])!=36 or len(rag['documents'])!=15 or rag['gold_used_as_corpus_or_model_input']:
        raise ValueError('ohr_denominator_or_reference')
    audits={
        'core':api_summary([*core['preflight'],*(a for c in core['cases'] for a in c['api_audits'])],
            dropped=core['api_summary']['dropped_calls'],saved=core['api_summary']),
        'sessions':api_summary([a for s in sessions['sessions'] for c in s['cases'] for a in c['api_audits']]),
        'probes':api_summary([a for c in probes['cases'] for a in c.get('api_audits',[])]),
        'raster':api_summary([a for c in raster['cases'] for a in c['api_audits']]),
        'topics':api_summary([a for s in topics['sessions'] for c in s['cases'] for a in c['api_audits']]),
        'ohr':api_summary(rag['api_audits'],dropped=rag['api_summary']['dropped_calls'],saved=rag['api_summary']),
        'sakila':api_summary([a for c in observed for a in c['api_audits']],
            dropped=sum(c['api_audit_dropped'] for c in observed))}
    paired=compare(ROOT/'docs/OHR_ROUND7_RELEASE_20261004.json',ROOT/'docs'/names['rag'])
    output={'created_at':datetime.now(timezone.utc).isoformat(),'source_audit':'PASS',
        'local_regression_passed': bool(reg['ok']),
        'release_acceptance': 'FULL_REGRESSION_PASSED' if reg['ok'] else 'FULL_REGRESSION_FAILED_RETAINED',
        'scope':'engineering_evidence_not_full_task_completion_or_official_score',
        'backend_files_verified':len(current),'backend_file_sha256':current,
        'backend_lf_normalized_sha256':{path:hashlib.sha256((ROOT/path).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for path in current},
        'reports':{k:{'path':v,'sha256':pins[k]} for k,v in names.items()},'api_summary':audits,
        'regression':{k:reg[k] for k in ('passed','failed','skipped','warnings','subtests_passed')},
        'core':{'passed':core['passed'],'total':44,'failed_ids':[c['id'] for c in core['cases'] if not c['pass']]},
        'sessions':{'passed_turns':sessions['passed_turns'],'total':15,'complete_five_turn_sessions':sessions['complete_five_turn_sessions']},
        'complex_probes':{'passed':probes['passed'],'total':12,'failed_ids':[c['id'] for c in probes['cases'] if not c['pass']]},
        'raster_probes':{'passed':raster['passed'],'total':4,'failed_ids':[c['id'] for c in raster['cases'] if not c['pass']]},
        'topic_sessions':{k:topics[k] for k in ('passed_turns','executed_turns','complete_five_turn_sessions')},
        'sakila':{k:reports['sakila'][k] for k in ('passed','planned_total','sessions_all_passed','failure_counts')},
        'artifact_delivery':{k:reports['artifact'][k] for k in ('passed','inline_passed','planned_total','sessions_all_passed','session_turns_passed')},
        'ohr_paired':{k:v for k,v in paired.items() if k!='cases'},
        'limitations':['Both Sakila and OHR questions are exposed development replays, not blind or contest scores.',
            'Inline full-result and artifact-aware delivery scores are separate contracts; not directly comparable.',
            'Independent model review remains fallible; visible quote regions are approximate model-selected locations.',
            'Images cover at most two pages per visual fallback; long documents and complex formula execution remain incomplete.']}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(output,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:output[k] for k in ('source_audit','regression','core','sessions','complex_probes','raster_probes','artifact_delivery')}))


if __name__=='__main__':main()
