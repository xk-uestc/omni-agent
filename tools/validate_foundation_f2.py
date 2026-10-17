"""Audit F2 source-family isolation and literal gold provenance before use."""
import json
from collections import Counter
from foundation_common import ROOT, sha
from freeze_foundation_f2 import BENCH


def validate():
    manifest=json.loads((BENCH/'manifest.json').read_text())
    assert all(sha(ROOT/p)==h for p,h in manifest['files'].items())
    docs=json.loads((BENCH/'documents.json').read_text())
    sources={d['id']:'\n'.join(d.get('pages',[]))+'\n'+d.get('text','') for d in docs}
    doc_families={d['id']:d['family'] for d in docs}
    families=Counter(d['family'] for d in docs)
    assert len(docs)==30 and set(families.values())=={5}
    splits={s:json.loads((BENCH/f'{s}.json').read_text()) for s in ('dev','retained')}
    family_sets={s:{t['source_family'] for t in ts} for s,ts in splits.items()}
    assert len(splits['dev'])==len(splits['retained'])==24
    assert family_sets['dev'].isdisjoint(family_sets['retained'])
    assert all(sorted(Counter(t['source_family'] for t in ts).values())==[8,8,8] for ts in splits.values())
    gold=json.loads((BENCH/'scoring.json').read_text())
    for task in sum(splits.values(),[]):
        assert task['id'] in gold
        assert set(task['required']).issubset(sources)
        assert all(literal in sources[did] for did,values in task['required'].items() for literal in values),task['id']
        if task['expected_abstain']:
            assert not task['required']
        assert all(did in sources and doc_families[did]==task['source_family']
                   for did in task.get('wrong_binding_sources',[]))
    print(json.dumps({'tasks':sum(map(len,splits.values())),'documents':len(docs),
        'families':{s:sorted(v) for s,v in family_sets.items()},'literal_gold_verified':True,
        'source_family_leakage':False,'input_hashes_verified':True},ensure_ascii=False))


if __name__=='__main__':validate()
