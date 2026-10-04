"""Seal same-source round-nine measurements, retaining every failed case."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_round6_release import verify_source, api_summary, paired_core
from compare_ohr_runs import compare

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    raw=path.read_bytes()
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New report directly in docs required')
    names={
        'regression':'LOCAL_REGRESSION_ROUND9_FINAL_20261004.json',
        'core':'REAL_MODEL_CORE_ROUND9_FINAL_20261004.json',
        'complex':'ROUND9_COMPLEX_PROBES_FINAL_20261004.json',
        'fresh_schema':'ROUND9_FRESH_SCHEMA_FINAL_20261004.json',
        'arithmetic':'ROUND9_DOCUMENT_ARITHMETIC_FINAL_20261004.json',
        'topics':'ROUND9_TOPIC_RECOVERY_FINAL_20261004.json',
        'raster':'ROUND9_RASTER_DOCUMENTS_FINAL_20261004.json',
        'source_pdf':'ROUND9_ORIGINAL_PDF_REPLAY_EXTENDED_20261004.json',
        'ohr':'OHR_ROUND9_FINAL_20261004.json',
    }
    runs={};pins={}
    for key,name in names.items():
        runs[key],pins[key]=read(ROOT/'docs'/name)
    current={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}
    for key,run in runs.items():
        if verify_source(run)!=current:
            raise ValueError('source_version_mismatch_'+key)
    core=runs['core'];topics=runs['topics'];ohr=runs['ohr']
    if len(core['cases'])!=44 or core['not_run'] or len({c['id'] for c in core['cases']})!=44:
        raise ValueError('core_denominator')
    for key,total in [('complex',12),('fresh_schema',6),('arithmetic',5),('raster',4)]:
        run=runs[key]
        if len(run['cases'])!=total or len({c['id'] for c in run['cases']})!=total or run['total']!=total:
            raise ValueError('probe_denominator_'+key)
        if run.get('reference_used_as_model_input') is not False:
            raise ValueError('probe_reference_contract_'+key)
    if topics['executed_turns']!=10 or topics['recreate_before_turn']!=[2,5] or topics['reference_used_as_model_input']:
        raise ValueError('topic_denominator_or_recovery_protocol')
    for session in topics['sessions']:
        if session['persisted_turns_after_restart']!=5 or not session['source_stable']:
            raise ValueError('topic_persistence')
        if [r['before_turn'] for r in session['recreations']]!=[2,5] or any(
            not r['context_equal'] or not r['new_agent_instance'] or
            r['kind']!='fresh_objects_from_persistent_storage_not_os_process_restart'
            for r in session['recreations']):
            raise ValueError('topic_recreation')
        for case in session['cases']:
            if case['pass'] and (not case['api_audits'] or any(
                    a.get('status')!='completed' or a.get('model_verified') is not True
                    for a in case['api_audits'])):
                raise ValueError('topic_passing_turn_missing_verified_api')
    if len(ohr['cases'])!=36 or len(ohr['documents'])!=15 or ohr['gold_used_as_corpus_or_model_input']:
        raise ValueError('ohr_denominator')
    api={}
    api['core']=api_summary([*core['preflight'],*(a for c in core['cases'] for a in c['api_audits'])],
        dropped=core['api_summary']['dropped_calls'],saved=core['api_summary'])
    for key in ('complex','fresh_schema','arithmetic','raster'):
        api[key]=api_summary([a for c in runs[key]['cases'] for a in c.get('api_audits',[])])
    api['topics']=api_summary([a for s in topics['sessions'] for c in s['cases'] for a in c['api_audits']])
    api['ohr']=api_summary(ohr['api_audits'],dropped=ohr['api_summary']['dropped_calls'],saved=ohr['api_summary'])
    ohr_comparison=compare(ROOT/'docs/OHR_ROUND8_FINAL4_20261004.json',ROOT/'docs'/names['ohr'])
    baseline,_=read(ROOT/'docs/REAL_MODEL_CORE_ROUND8_FINAL4_20261004.json')
    core_comparison=paired_core(baseline,core)
    reg=runs['regression']
    report={'created_at':datetime.now(timezone.utc).isoformat(),
        'source_audit':'PASS','scope':'same_source_engineering_and_development_evidence_not_full_contest_completion',
        'backend_files_verified':len(current),'backend_file_sha256':current,
        'backend_lf_normalized_sha256':{path:hashlib.sha256((ROOT/path).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for path in current},
        'reports':{key:{'path':name,'sha256':pins[key]} for key,name in names.items()},
        'local_regression_passed':reg['ok'],
        'regression':{k:reg[k] for k in ('passed','failed','skipped','warnings','subtests_passed','workers')},
        'api_summary':api,'core_paired':core_comparison,
        'probes':{key:{'passed':runs[key]['passed'],'total':runs[key]['total'],
                       'failed_ids':[c['id'] for c in runs[key]['cases'] if not c['pass']]}
                  for key in ('complex','fresh_schema','arithmetic','raster')},
        'topics':{k:topics[k] for k in ('passed_turns','executed_turns','complete_five_turn_sessions','recreate_before_turn')},
        'original_pdf_replay_passed':runs['source_pdf']['ok'],
        'ohr_paired':ohr_comparison,
        'limitations':[
            'Source provenance PASS does not turn local or model failures into passes.',
            'OHR and the 44-case core suite are exposed development replays, not official competition accuracy.',
            'Fresh random-schema probes use tiny SQLite databases and explicit field definitions; six cases do not prove generalization.',
            'Topic recovery rebuilds production objects from persistent SQLite, not an OS-process restart.',
            'Authored native arithmetic PDF amounts are right aligned in this final run; earlier candidate inputs were left aligned.',
            'Independent model semantic reviews are fallible; arbitrary scanned formulas and large complex documents remain unverified.',
        ]}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'source_audit':'PASS','regression':report['regression'],
        'core':core['passed'],'probes':report['probes'],'topics':report['topics'],
        'ohr_delta':ohr_comparison['exact_match_delta']}))


if __name__=='__main__':
    main()
