"""Read-only evidence verification and independent scorer counterexamples."""
from copy import deepcopy
import argparse
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile

from evaluate_memory_sensitive_m1a import ROOT, BENCH, digest, dump
from score_memory_sensitive_m1a import score


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True);a=p.parse_args()
    out=ROOT/'docs/memory_rl/runs'/a.label;target=out/'verification.json'
    if target.exists():p.error('preserve evidence: verification already exists')
    metadata=json.loads((out/'metadata.json').read_text());manifest=json.loads((BENCH/'manifest.json').read_text())
    frozen=all(digest(ROOT/f)==h for f,h in manifest['files'].items())
    source_stable=all(digest(ROOT/f)==h for f,h in metadata['source_after'].items())
    sensitive=json.loads((out/'sensitive-summary.json').read_text());context=json.loads((out/'context-summary.json').read_text())
    database=Path(metadata['fixtures']['mixed']['database']);golds=json.loads((BENCH/'gold.json').read_text())['tasks']
    old=json.loads((ROOT/'docs/memory_rl/runs/m1a-memory-sensitive-no-memory-20261010/summary.json').read_text())
    adversaries=[]
    base=json.loads((out/'B/s03.json').read_text())['response']
    def evaluate(name,r,require_consumption=True):
        passed,detail=score(golds['s03'],r,database,sensitive['document_versions']['original'])
        consumption=bool(r.get('memory',{}).get('consumed'))
        adversaries.append({'name':name,'frozen_scorer_pass':bool(passed),'consumed':consumption,
            'combined_pass':bool(passed) and consumption if require_consumption else bool(passed),'detail':detail})
    evaluate('original_actual_s03',base)
    r=deepcopy(base);r['result']['parameters'][0]='2024-01-01';r['result']['parameters'][1]='2025-01-01';evaluate('wrong_year',r)
    r=deepcopy(base);r['result']['parameters'][2]='门店';evaluate('wrong_channel',r)
    r=deepcopy(base);r['result']['parameters'][3]='华南';evaluate('wrong_region',r)
    r=deepcopy(base);r['result']['sql']=r['result']['sql'].replace('sales_amount','cost_amount');evaluate('wrong_physical_field',r)
    r=deepcopy(base);r['result']['sql']=r['result']['sql'].replace('SUM(', 'AVG(');evaluate('wrong_aggregation',r)
    r=deepcopy(base);r['result']['sql']=None;evaluate('no_sql_execution_receipt',r)
    r=deepcopy(base);r['result']['provenance'].pop('source_revision',None);evaluate('no_source_version',r)
    r=deepcopy(base);r['memory']['consumed']=[];evaluate('no_memory_consumption',r)
    assert adversaries[0]['combined_pass'] and not any(r['combined_pass'] for r in adversaries[1:])
    zip_comparison={}
    for variant in sensitive['document_versions']:
        oldsha=old['document_versions'][variant]['targets'];newsha=sensitive['document_versions'][variant]['targets']
        oldpath=ROOT/'runtime/m1a-memory-sensitive-no-memory-20261010/knowledge'/variant/'assets'/(oldsha+'.xlsx')
        newpath=ROOT/'runtime'/a.label/'documents'/variant/'assets'/(newsha+'.xlsx')
        with ZipFile(oldpath) as z1,ZipFile(newpath) as z2:
            changed=[n for n in z1.namelist() if z1.read(n)!=z2.read(n)]
            zip_comparison[variant]={'m1a_sha256':oldsha,'new_sha256':newsha,'different_members':changed,
                'worksheet_members_equal':all(z1.read(n)==z2.read(n) for n in z1.namelist() if n.startswith('xl/worksheets/')),
                'old_core_xml':z1.read('docProps/core.xml').decode(),'new_core_xml':z2.read('docProps/core.xml').decode()}
    storage={}
    for arm in ('A','B'):
        memories=list((ROOT/'runtime'/a.label/arm).glob('*/memory.sqlite'))+list((ROOT/'runtime'/a.label/'context-memory'/arm).glob('*.sqlite'))
        totals={'files':len(memories),'sqlite_file_bytes':sum(m.stat().st_size for m in memories),'event_count':0,'event_payload_bytes':0,'candidate_payload_bytes':0}
        for m in memories:
            with sqlite3.connect(m.resolve().as_uri()+'?mode=ro',uri=True) as db:
                count,size=db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM memory_events').fetchone()
                totals['event_count']+=count;totals['event_payload_bytes']+=size
                totals['candidate_payload_bytes']+=db.execute('SELECT COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0) FROM memories').fetchone()[0]
        storage[arm]=totals
    report={'frozen_inputs_and_scorers_unchanged':frozen,'backend_matches_evaluated_hashes':source_stable,
        'source_commit':metadata['source_commit'],'source_stable_during_run':metadata['implementation_stable'],
        'database_read_only':metadata['database_read_only_verified'],
        'database_matches_m1a':metadata['database_sha256_before']['mixed']==old['database_sha256_before'],
        'A_pass_vector_matches_m1a':all(r['A']==r['M1A'] for r in sensitive['comparison']),
        'context_pass_vectors_match_m1a':all(r['A']==r['B']==r['M1A'] for r in context['comparison']),
        'context_off_on_sql_rows_status_equal':all(r['off_on_sql_equal'] and r['off_on_rows_equal'] and r['off_on_status_equal'] for r in context['comparison']),
        'adversarial_scorer_checks':adversaries,'xlsx_serialization_diagnostic':zip_comparison,'storage':storage,
        'storage_note':'A also pre-provisioned test candidates for fair availability. Default-off production creates no memory DB. Sizes include SQLite page allocation; zero file growth does not imply zero persisted event bytes.'}
    dump(target,report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'adversarial_scorer_checks','xlsx_serialization_diagnostic'}},ensure_ascii=False))
    assert frozen and source_stable and report['context_pass_vectors_match_m1a']


if __name__=='__main__':main()
