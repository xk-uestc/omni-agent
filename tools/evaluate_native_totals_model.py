"""Fresh generated amount statements through production retrieval, no source hints."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import string
import sys
import tempfile
import threading

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.native_total_question import replay
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def pdf(country, year, items):
    with fitz.open() as document:
        page = document.new_page(width=650, height=700)
        page.insert_text((45, 35), country+' official local allocation record', fontsize=10)
        page.insert_text((45, 55), 'FY '+str(year), fontsize=10)
        page.insert_text((45, 78), 'Named allocations are disjoint; signed totals preserve adjustments.', fontsize=9)
        for index, (name, value) in enumerate(items):
            y = 150+index*140
            page.insert_text((45, y), 'TOTAL '+name.upper()+' FUNDS:', fontsize=10)
            page.insert_text((360, y), '$'+format(value,'.2f')+'m', fontsize=10)
        return document.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, help='Replay the complete prior eight cases and their original store')
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    rng = random.SystemRandom()
    directory = Path(tempfile.mkdtemp(prefix='native-total-model-', dir=ROOT/'runtime'))
    store_path = directory/'knowledge'
    store = KnowledgeStore(store_path)
    cases = []
    source_hashes = {}
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        original_directory = Path(baseline['run_directory']).resolve()
        if not original_directory.is_relative_to((ROOT/'runtime').resolve()):
            parser.error('Baseline store must be in project runtime')
        records = baseline['records']
        if len(records) != 8 or {r['id'] for r in records} != set(range(8)):
            parser.error('Baseline must contain all original eight cases')
        cases = [{key:r[key] for key in ('id','question','expected_scoring_only',
            'expected_document_scoring_only','original_sha256')} for r in records]
        store_path = original_directory/'knowledge'
        store = KnowledgeStore(store_path)
        documents = store.list_documents()
        if {doc['document_id'] for doc in documents} != {f'{kind}-{i}' for kind in ('fresh','prior') for i in range(8)}:
            parser.error('Baseline document collection differs')
        for doc in documents:
            raw = store.verify_source(doc['document_id'], expected_sha256=doc['sha256']).read_bytes()
            source_hashes[doc['document_id']] = hashlib.sha256(raw).hexdigest()
        for case in cases:
            if source_hashes[case['expected_document_scoring_only']] != case['original_sha256']:
                parser.error('Baseline original PDF SHA differs')
    for index, operation in enumerate([] if args.baseline else ['absolute_difference','absolute_difference','difference','difference','sum','sum','lookup','lookup']):
        country = 'Region'+''.join(rng.choice(string.ascii_lowercase) for _ in range(10))
        year = 2038+index
        names = ['Procurement','Training','Transport'] if operation == 'sum' else ['Procurement','Training']
        values = [Decimal(rng.randint(100,99999))/100 for _ in names]
        if index == 3:
            values.sort()
        items = list(zip(names, values))
        source = pdf(country, year, items)
        correct_id = f'fresh-{index}'
        store.ingest(source, document_id=correct_id, title=country, modality='pdf', filename=correct_id+'.pdf')
        # Same entity, other year and amounts are genuine competing candidates.
        distractor = pdf(country, year-1, [(n,v+Decimal('17.43')) for n,v in items])
        store.ingest(distractor, document_id=f'prior-{index}', title=country, modality='pdf', filename=f'prior-{index}.pdf')
        if operation == 'absolute_difference':
            question = f'What is the difference between total Procurement funds and total Training funds for {country} in FY {year}?'
            expected = abs(values[0]-values[1])
        elif operation == 'difference':
            question = f'Subtract total Training funds from total Procurement funds for {country} in FY {year}.'
            expected = values[0]-values[1]
        elif operation == 'sum':
            question = f'What is the combined amount of total Procurement, Training and Transport funds for {country} in FY {year}?'
            expected = sum(values, Decimal(0))
        else:
            question = f'What is the total Procurement funding for {country} in FY {year}?'
            expected = values[0]
        cases.append({'id':index,'question':question,'expected_scoring_only':'$'+format(expected,'.2f')+'m',
            'expected_document_scoring_only':correct_id,'original_sha256':hashlib.sha256(source).hexdigest()})
    stop = threading.Event()
    def execute(case):
        if stop.is_set():
            return {**case,'status':'not_run','passed':False,'api_audits':[]}
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        knowledge = KnowledgeStore(store_path, generator=GroundedGenerator(client))
        try:
            result = knowledge.answer(case['question'], top_k=4)
            audits = safe_audits(client)
            supported = result['status'] == 'ok' and result['answer_mode'] == 'native_total_model_reviewed' and replay(knowledge, result)
            passed = (supported and result['answer'] == case['expected_scoring_only']
                and result['answer_scope']['document_id'] == case['expected_document_scoring_only'])
            record = {**case,'result':result,'source_replay_passed':supported,'passed':passed,'api_audits':audits}
        except Exception as error:
            audits = safe_audits(client)
            record = {**case,'error_type':type(error).__name__,'passed':False,'api_audits':audits}
        if any(a.get('http_status') in (401,403) for a in audits):
            stop.set()
        print(json.dumps({'id':case['id'],'passed':record['passed'],'mode':record.get('result',{}).get('answer_mode')}),flush=True)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(execute, cases))
    after = implementation_snapshot()
    sources_unchanged = all(hashlib.sha256(store.verify_source(did, expected_sha256=digest).read_bytes()).hexdigest()==digest
        for did,digest in source_hashes.items())
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'fresh_generated_isolated_amount_development_cases_not_official_or_blind_corpus',
        'gold_or_document_hint_sent_to_production':False,'model':'gpt-6-luna','reasoning':'medium',
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,
        'implementation_stable':before==after,'run_directory':str(store_path.parent),'records':records,
        'baseline_report':str(args.baseline) if args.baseline else None,
        'baseline_report_sha256':hashlib.sha256(args.baseline.read_bytes()).hexdigest() if args.baseline else None,
        'original_collection_sha256':source_hashes,'original_collection_unchanged':sources_unchanged,
        'passed':sum(r['passed'] for r in records),'total':len(records)}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':report['passed'],'total':report['total'],'stable':report['implementation_stable']}))
    return 0 if before==after else 1


if __name__ == '__main__':
    raise SystemExit(main())
