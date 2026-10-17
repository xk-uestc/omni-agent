"""Author synthetic F1 development/retained tasks, isolated from official gold."""
from foundation_common import ROOT, dump, sha

BENCH = ROOT / 'benchmarks/foundation_f1_20261010'


def main():
    BENCH.mkdir(exist_ok=True)
    docs, splits, gold = [], {'dev': [], 'retained': []}, {}
    topics = [('cedar', '杉木'), ('harbor', '港湾'), ('meadow', '草原'), ('summit', '峰顶')]
    for idx, (en, zh) in enumerate(topics):
        split = 'dev' if idx < 3 else 'retained'
        did = 'operations-' + en
        year = 2021 + idx
        clauses = [f'{en.title()} field operations {year}. Site {en.title()} uses calibration interval {17+idx} days. '
                   f'Approved devices are Larch, Birch, Spruce and Maple. Only outdoor devices are included. '
                   f'The budget is {710+idx} USD, excluding tax.',
                   f'{zh}运营{year}年。设备故障由维护团队处理，维修免费期限为{14+idx}个月。'
                   f'每个设备的电池充满可连续工作{7+idx}小时。',
                   f'{en.title()} emergency contact is the response desk. Escalation deadline is {29+idx} minutes. '
                   f'Process date is {year}-06-01. Scope excludes visitor badges.',
                   'Device | Class | Status | Reading (mg/L)\n'
                   'Larch | outdoor | approved | 1.2\nBirch | outdoor | approved | 2.3',
                   f'{en.title()} devices continued. Device | Class | Status | Reading (mg/L)\n'
                   'Spruce | outdoor | approved | 3.4\nMaple | outdoor | approved | 4.5\n'
                   'Willow | indoor | rejected | 0.5']
        docs.append({'id': did, 'title': en.title()+' operating record', 'type': 'pdf', 'pages': clauses})
        companion = 'finance-' + en
        docs.append({'id': companion, 'title': en.title()+' finance statement', 'type': 'txt',
                     'text': f'{en.title()} finance {year}. Payment due {31+idx} days. Invoice currency USD. '
                     f'Account owner is the accounting desk. Separate from operations contact.'})
        questions = [
            (f'What is the {year} {en.title()} calibration interval?', {did: [f'{17+idx} days']}, 'single'),
            (f'{zh}设备坏了可以免费修理多久？', {did: [f'{14+idx}个月']}, 'zh_semantic'),
            (f'List all approved outdoor devices in the {en.title()} operations record, excluding indoor devices.',
             {did: ['Larch', 'Birch', 'Spruce', 'Maple', 'mg/L']}, 'cross_page_enumeration'),
            (f'For {en.title()}, give operations emergency contact and finance payment deadline.',
             {did: ['response desk'], companion: [f'{31+idx} days']}, 'multi_source'),
            (f'{zh}设备充电后可以持续用多长时间？', {did: [f'{7+idx}小时']}, 'zh_semantic'),
            (f'What is the {en.title()} emergency escalation deadline and process date?',
             {did: [f'{29+idx} minutes', f'{year}-06-01']}, 'multi_part'),
            (f'What is the {en.title()} operations budget currency and tax exclusion?',
             {did: [f'{710+idx} USD', 'excluding tax']}, 'units_qualifiers'),
            (f'What is the {en.title()} unauthorized satellite access password?', {}, 'insufficient_evidence'),
        ]
        for n, (query, expected, kind) in enumerate(questions):
            ident = f'{split}-{en}-{n+1:02}'
            splits[split].append({'id': ident, 'query': query, 'kind': kind, 'top_k': 4})
            gold[ident] = {'sources': list(expected), 'evidence': expected,
                           'should_abstain': not bool(expected)}
    # A high-density unrelated record can monopolize a flat chunk candidate pool.
    for i in range(24):
        text = '\n\n'.join(f'Internal catalog section {n}. Calibration interval emergency contact finance deadline '
                            f'approved outdoor devices. Administration notes for unrelated warehouse {i}. '
                            'The catalog does not contain operating measurements or approved device records.'
                            for n in range(12))
        docs.append({'id': f'noise-{i:02}', 'title': 'Generic catalog', 'type': 'txt', 'text': text})
    # Same entity name, disjoint ownership. A title alone never proves an answer.
    docs.append({'id': 'title-only', 'title': 'Cedar Harbor Meadow Summit emergency calibration budget',
                 'type': 'txt', 'text': 'Archived visitor directory. No operational values are published here.'})
    # Long document navigation cannot claim the unseen remainder was examined.
    docs.append({'id': 'long-reference', 'title': 'Reference atlas', 'type': 'pdf',
                 'pages': [f'Atlas chapter {i}. Inventory methods and unrelated administrative reference. '
                           'No field operations numbers or device approvals.' for i in range(40)]})
    dump(BENCH/'documents.json', docs)
    for split, tasks in splits.items(): dump(BENCH/(split+'.json'), tasks)
    dump(BENCH/'scoring.json', gold)
    dump(BENCH/'protocol.json', {'top_k': 4, 'source_candidates': 6, 'candidate_pool': 20,
                                'answer_metrics': 'not_run_without_paid_generation_budget',
                                'retained_policy': 'no retained scores used to choose settings; final paired run only',
                                'scope': 'authored_synthetic_development_not_official_benchmark'})
    files = [*BENCH.glob('*.json'), ROOT/'tools/freeze_foundation_rag.py', ROOT/'tools/evaluate_foundation_rag.py']
    dump(BENCH/'manifest.json', {'files': {str(p.relative_to(ROOT)): sha(p) for p in files}})
    print('Frozen synthetic F1 inputs: dev=24, retained=8; official gold untouched.')


if __name__ == '__main__': main()
