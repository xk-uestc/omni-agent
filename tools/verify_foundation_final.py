"""Independent final paired audit; preserve scores and original verifier failures."""
import json
from pathlib import Path
from foundation_common import ROOT, dump, sha, source_hashes, environment

OUT = ROOT/'docs/foundation/runs'
MEM = ROOT/'docs/memory_rl/runs/f1-memory-final-20261010'


def read(path): return json.loads(Path(path).read_text())


def main():
    pairs=[]
    for split in ['dev','retained']:
        for arm in ['A','B']:
            before=read(OUT/f'rag-{arm}-{split}-before.json')
            after=read(OUT/f'rag-{arm}-{split}-final.json')
            assert before['input_manifest_sha256']==after['input_manifest_sha256']
            assert before['scorer_sha256']==after['scorer_sha256']
            old={r['id']:r for r in before['records']}
            for r in after['records']:
                b=old[r['id']]
                assert b['query']==r['query']
                metrics=['source_recall_at_k','evidence_recall_at_k','mrr','ndcg','irrelevant_source_ratio','complete_annotated_evidence','sha_locator_correct']
                pairs.append({'id':r['id'],'split':split,'arm':arm,
                              'before':{k:b[k] for k in metrics},'after':{k:r[k] for k in metrics},
                              'before_document_ids':[h['document_id'] for h in b['hits']],
                              'after_document_ids':[h['document_id'] for h in r['hits']],
                              'before_chunk_ids':[h['metadata']['chunk_id'] for h in b['hits']],
                              'after_chunk_ids':[h['metadata']['chunk_id'] for h in r['hits']],
                              'before_query_ms':b['query_ms'],'after_query_ms':r['query_ms']})
    contexts=read(MEM/'context-summary.json')
    current={arm:read(MEM/f'context-{arm}.json')['cases'] for arm in ['A','B']}
    deltas=[]
    for row in contexts['comparison']:
        if row['M1A']!=row['A'] or row['M1A']!=row['B']:
            # Check the GENERAL changed behavior, not a whitelist of case IDs.
            receipts=[]
            for arm in ['A','B']:
                actual=next(x for x in current[arm] if x['id']==row['id'])
                response=actual['response'];result=response.get('result') or {}
                rejected=(result.get('clarification_code')=='read_only_query_required' and not result.get('sql')
                          and not result.get('rows') and response['status']=='clarification')
                assert rejected and actual['pass']
                receipts.append({'arm':arm,'whole_request_rejected_without_sql':rejected,'question':response['question']})
            deltas.append({'id':row['id'],'before':row['M1A'],'after_A':row['A'],'after_B':row['B'],'receipts':receipts})
    sensitive=read(MEM/'sensitive-summary.json');metadata=read(MEM/'metadata.json')
    formation=read(ROOT/'docs/memory_rl/runs/f1-formation-final-20261010/summary.json')
    manifests=[ROOT/'benchmarks/foundation_f1_20261010/manifest.json',ROOT/'benchmarks/task_experience_m1c_20261010/manifest.json',
               ROOT/'benchmarks/memory_sensitive_m1a_20261010/manifest.json',ROOT/'benchmarks/memory_formation_m1b2_20261010/manifest.json']
    frozen={str(p.relative_to(ROOT)):all(sha(ROOT/f)==h for f,h in read(p)['files'].items()) for p in manifests}
    assert all(frozen.values()) and metadata['implementation_stable'] and metadata['database_read_only_verified']
    assert metadata['source_after']==source_hashes() and formation['source_after']==source_hashes()
    assert all(r['A']==r['M1A'] for r in sensitive['comparison'])
    assert all(r['off_on_rows_equal'] and r['off_on_sql_equal'] and r['off_on_status_equal'] for r in contexts['comparison'])
    report={'environment':environment(),'evaluated_source_commit':metadata['source_commit'],'source_hashes':source_hashes(),
            'frozen_manifest_verified':frozen,'rag_pairs':pairs,
            'rag_correctness_changes':sum(p['before']!=p['after'] for p in pairs),
            'memory_sensitive16':sensitive['arms'],'memory_context185':contexts['arms'],
            'memory_authorized_safety_deltas':deltas,'memory_regressions':contexts['regressions_vs_m1a'],
            'memory_on_off_sql_rows_status_equal':True,'memory_source_and_databases_stable':True,
            'formation22':formation['arms'],'formation_metrics':formation['formation_metrics'],
            'original_verifier_exit':'failed_unchanged_vector_assertion_due_to_whole_request_safety_improvement',
            'historical_m1c_four_arm':'retained_audit_only_not_rerun','paid_api_calls':0,'training_started':False}
    dump(OUT/'final-verification-v2.json',report)
    print(json.dumps({'rag_pairs':len(pairs),'rag_correctness_changes':report['rag_correctness_changes'],
                      'safety_deltas':len(deltas),'memory_regressions':report['memory_regressions'],
                      'frozen_verified':all(frozen.values()),'source_stable':True}))


if __name__=='__main__':main()
