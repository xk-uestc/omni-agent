"""Verify frozen F3 receipts against F2 and preserve existing verifier failures."""
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from foundation_common import ROOT, dump, environment, sha, source_hashes

OUT=ROOT/'docs/foundation/runs/f3'
MEM=ROOT/'docs/memory_rl/runs'


def read(p):return json.loads(Path(p).read_text())


def main():
    manifests=[ROOT/'benchmarks'/name/'manifest.json' for name in
        ('foundation_f1_20261010','foundation_f2_20261011','memory_sensitive_m1a_20261010',
         'memory_formation_m1b2_20261010','task_experience_m1c_20261010')]
    frozen={str(p.relative_to(ROOT)):all(sha(ROOT/f)==h for f,h in read(p)['files'].items()) for p in manifests}
    assert all(frozen.values())
    pairs=[];f1=[]
    for split in ('dev','retained'):
        old=read(ROOT/f'docs/foundation/runs/f2-final-persistent-{split}.json')
        new=read(OUT/f'frozen-topk-{split}.json');pipeline=read(OUT/('pipeline-dev-scopes.json' if split=='dev' else 'frozen-pipeline-retained.json'))
        assert old['scorer_sha256']==new['scorer_sha256'] and old['input_manifest_sha256']==new['input_manifest_sha256']
        assert new['source_before']==new['source_after']==source_hashes()
        for b,a in zip(old['records'],new['records']):
            assert a['id']==b['id'] and a['query']==b['query']
            p=next(p for p in pipeline['records'] if p['id']==a['id'])
            pairs.append({'id':a['id'],'split':split,'topk_before':b['evidence_recall_at_k'],'topk_after':a['evidence_recall_at_k'],
                'topk_complete_before':b['complete_required_evidence'],'topk_complete_after':a['complete_required_evidence'],
                'wrong_before':b['wrong_entity_source_binding'],'wrong_after':a['wrong_entity_source_binding'],
                'topk_plus_bundle_recall':p['topk_plus_bundle_recall'],'topk_plus_bundle_complete':p['topk_plus_bundle_complete'],
                'production_prepared_recall':p['expanded_evidence_recall'],'production_prepared_complete':p['expanded_complete'],
                'bundle_status':(p['bundle'] or {}).get('status'),'bundle_reason':(p['bundle'] or {}).get('reason')})
        old=read(ROOT/f'docs/foundation/runs/f2-final-f1-bm25-{split}.json');new=read(OUT/f'f1-flat-{split}.json')
        assert old['scorer_sha256']==new['scorer_sha256'] and old['input_manifest_sha256']==new['input_manifest_sha256']
        for b,a in zip(old['records'],new['records']):
            metrics=('source_recall_at_k','evidence_recall_at_k','mrr','ndcg','irrelevant_source_ratio','complete_annotated_evidence','sha_locator_correct')
            equal=all(a[k]==b[k] for k in metrics);assert equal
            f1.append({'id':a['id'],'split':split,'metrics_equal':equal})
    memory_checks=[]
    for suite in ('sensitive','context'):
        old=read(MEM/f'f2-final-m1b1-20261011/{suite}-summary.json')
        new=read(MEM/f'f3-final-m1b1-20261011/{suite}-summary.json')
        for b,a in zip(old['comparison'],new['comparison']):
            assert a['id']==b['id'] and a['A']==b['A'] and a['B']==b['B']
            if suite=='context':assert a['off_on_rows_equal'] and a['off_on_sql_equal'] and a['off_on_status_equal']
            memory_checks.append({'suite':suite,'id':a['id'],'pass_vectors_equal_to_f2':True})
    metadata=read(MEM/'f3-final-m1b1-20261011/metadata.json')
    formation=read(MEM/'f3-final-m1b2-20261011/summary.json');old_formation=read(MEM/'f2-final-m1b2-20261011/summary.json')
    for b,a in zip(old_formation['comparison'],formation['comparison']):
        assert a['id']==b['id'] and all(a[k]==b[k] for k in ('A','B','C'))
        assert a['BC_binding_equal'] and a['BC_rows_equal']
    assert metadata['source_after']==formation['source_after']==source_hashes()
    assert metadata['implementation_stable'] and metadata['database_read_only_verified']
    assert formation['source_stable'] and formation['database_sha256_before']==formation['database_sha256_after']
    assert formation['formation_metrics']['wrong_promotions']==0
    for v in (metadata['sensitive'],metadata['context'],formation['arms']):assert all(a['model_calls']==0 for a in v.values())
    tests=ET.parse(OUT/'final-regression.xml').getroot();old_tests=ET.parse(ROOT/'docs/foundation/runs/f2-final-tests-v4.xml').getroot()
    failures=lambda root:sorted(t.attrib['classname']+'::'+t.attrib['name'] for t in root.iter('testcase') if t.find('failure') is not None or t.find('error') is not None)
    assert failures(tests)==failures(old_tests)
    categories=Counter()
    for t in tests.iter('testcase'):
        for kind in ('task_experience','memory','foundation','f3','source_answer_dossier','native_row'):
            if 'test_'+kind in t.attrib.get('classname',''):categories[kind]+=1
    cost_before=read(OUT/'cost-before.json')['metrics'];cost_after=read(OUT/'cost-after.json')['metrics']
    report={'environment':environment(),'production_source_commit':'67c4d3963d7078edeb9c1d5f7c58981caa072ffd',
        'source_hashes':source_hashes(),'frozen_manifests_verified':frozen,'f2_pairs':pairs,'f1_pairs':f1,
        'topk_improved_queries':sum(r['topk_before'] is not None and r['topk_after']>r['topk_before'] for r in pairs),
        'topk_regressed_queries':[r['id'] for r in pairs if r['topk_before'] is not None and r['topk_after']<r['topk_before']],
        'memory_checks':memory_checks,'memory16':metadata['sensitive'],'memory185':metadata['context'],'memory22':formation['arms'],
        'formation_metrics':formation['formation_metrics'],'database_read_only':True,
        'original_m1b1_verifier_exit':'failed old M1A context equality; F2 pass vectors exactly preserved; raw failure retained',
        'test_existing_failures':failures(tests),'test_failure_list_matches_f2':True,'test_categories':dict(categories),
        'cost_before':cost_before,'cost_after':cost_after,'paid_calls':0,'answer_metrics':'not_run','training_started':False}
    dump(OUT/'final-verification.json',report)
    # Redirected stdout is still being written; a manifest cannot hash its
    # own bytes or a pending log. All finished raw receipts are included.
    artifacts=[p for p in OUT.iterdir() if p.is_file() and p.name not in ('ARTIFACT_MANIFEST.json','final-verification.txt')]
    artifacts += [p for label in ('f3-final-m1b1-20261011','f3-final-m1b2-20261011') for p in (MEM/label).rglob('*') if p.is_file()]
    dump(OUT/'ARTIFACT_MANIFEST.json',{'production_source_commit':report['production_source_commit'],
        'files':{str(p.relative_to(ROOT)):sha(p) for p in sorted(artifacts)}})
    print(json.dumps({'f2_pairs':len(pairs),'f1_pairs':len(f1),'topk_improved':report['topk_improved_queries'],
        'topk_regressed':report['topk_regressed_queries'],'memory16_185_22_preserved':True,'frozen_inputs_scorers':True,
        'old_failure_list_preserved':True,'test_categories':dict(categories)}))


if __name__=='__main__':main()
