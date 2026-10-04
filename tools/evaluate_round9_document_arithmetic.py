"""Real-model document arithmetic on fresh native PDFs; references score only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from types import SimpleNamespace

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from model_runtime import enable_local_model, local_model_headers
from backend.knowledge_store import KnowledgeStore
from backend.native_table_question import route_native_table_question
from backend.responses_client import StructuredResponses, GenerationError


def hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def pdf(north, south, total):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50,40), 'Regional operating cost allocation for 2030')
        for index,(label,value) in enumerate([('North',north),('South',south),('Total',total)]):
            page.insert_text((50,100+index*25), label, fontsize=10)
            literal = '$'+str(value)
            page.insert_text((310-fitz.get_text_length(literal, fontsize=10),100+index*25), literal, fontsize=10)
        return document.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay-from',type=Path,
        help='Reuse all five exact original PDF bytes/questions/scoring references from a retained docs report')
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists():
        parser.error('New report directly in docs required')
    enable_local_model('gpt-6-luna')
    before = hashes()
    root = ROOT/'runtime'/('round9-doc-arithmetic-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    root.mkdir()
    amount = 100 + secrets.randbelow(800)
    definitions = [
        ('share', 'What percentage of Total cost is North in 2030?', amount, amount*2, amount*3, 'percentage', '≈33.33%'),
        ('absolute_gap', 'What is the absolute difference between North cost and South cost in 2030?', amount, amount*2, amount*3, 'absolute_difference', '$'+str(amount)),
        ('signed_gap', 'Subtract South cost from North cost in 2030.', amount, amount*2, amount*3, 'difference', '$-'+str(amount)),
        ('zero_denominator', 'What percentage of Total cost is North in 2030?', amount, -amount, 0, None, None),
        ('missing_growth_period', 'What percentage growth in North cost occurred from 2030 to 2031?', amount, amount*2, amount*3, None, None),
    ]
    originals={}
    replay_pin=None
    if args.replay_from:
        if args.replay_from.resolve().parent!=(ROOT/'docs').resolve():
            parser.error('Replay report must be directly in docs')
        replay_raw=args.replay_from.read_bytes()
        previous=json.loads(replay_raw)
        ids={row[0] for row in definitions}
        if (previous.get('model')!='gpt-6-luna' or previous.get('reasoning')!='medium'
                or previous.get('reference_used_as_model_input') is not False
                or len(previous.get('cases',[]))!=5 or {c['id'] for c in previous['cases']}!=ids):
            parser.error('Replay must retain the complete five-case protocol')
        directories=sorted((ROOT/'runtime').glob('round9-doc-arithmetic-*'))
        definitions=[]
        for case in previous['cases']:
            for directory in directories:
                if not (directory/case['id']/'knowledge.sqlite').is_file():
                    continue
                source=KnowledgeStore(directory/case['id'])
                doc=source.document(case['id'])
                if doc['sha256']==case['original_sha256']:
                    originals[case['id']]=source.verify_source(case['id'],expected_sha256=doc['sha256']).read_bytes()
                    break
            if case['id'] not in originals:
                parser.error('Exact original replay PDF is unavailable')
            definitions.append((case['id'],case['question'],None,None,None,
                case['scoring_only']['operation'],case['scoring_only']['expected_answer']))
        replay_pin={'path':str(args.replay_from),'sha256':hashlib.sha256(replay_raw).hexdigest(),
                    'contract':'identical_questions_original_pdf_bytes_and_scoring_references'}
    stop=threading.Event()
    def execute(row):
        identifier, question, north, south, total, operation, expected = row
        if stop.is_set():
            return {'id':identifier,'question':question,'pass':False,'status':'not_run','api_audits':[]}
        if hashes() != before:
            raise ValueError('source_changed')
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        store = KnowledgeStore(root/identifier, generator=SimpleNamespace(client=client))
        original = originals[identifier] if identifier in originals else pdf(north,south,total)
        try:
            store.ingest(original, document_id=identifier, title='Regional cost allocation', modality='pdf', filename='allocation.pdf')
            result, trace = route_native_table_question(store, question,
                [SimpleNamespace(metadata={'document_id':identifier})], document_id=identifier)
            completed = bool(client.audit_history) and all(a.get('status') == 'completed'
                and a.get('model_verified') is True for a in client.audit_history)
            passed = completed and ((result is None and operation is None) or (result is not None
                and result['status'] == 'ok' and result['computation']['operation'] == operation
                and result['answer'] == expected))
            return {'id':identifier,'question':question,'pass':passed,'result':result,'trace':trace,
                'original_sha256':hashlib.sha256(original).hexdigest(),
                'scoring_only':{'operation':operation,'expected_answer':expected},'api_audits':client.audit_history}
        except GenerationError as exc:
            if exc.status in (401,403):
                stop.set()
            return {'id':identifier,'question':question,'pass':False,'error_type':type(exc).__name__,
                    'original_sha256':hashlib.sha256(original).hexdigest(),
                    'scoring_only':{'operation':operation,'expected_answer':expected},
                    'api_audits':client.audit_history}
        except Exception as exc:
            return {'id':identifier,'question':question,'pass':False,'error_type':type(exc).__name__,
                    'original_sha256':hashlib.sha256(original).hexdigest(),
                    'scoring_only':{'operation':operation,'expected_answer':expected},
                    'api_audits':client.audit_history}
    with ThreadPoolExecutor(max_workers=3) as pool:
        cases = list(pool.map(execute, definitions))
    after = hashes()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':('exact_original_native_PDF_replay_not_blind_or_public_benchmark' if replay_pin else
                 'fresh_authored_native_PDF_arithmetic_probes_not_public_benchmark'),
        'model':'gpt-6-luna','reasoning':'medium','reference_used_as_model_input':False,
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,
        'implementation_stable':before == after,'replay_source':replay_pin,'cases':cases,'passed':sum(c['pass'] for c in cases),
        'total':len(cases), 'limitations':['Five small one-page native PDF probes; not arbitrary scanned documents or full benchmark coverage.']}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':report['passed'],'total':report['total'],'stable':report['implementation_stable']}))
    return 0 if all(c['pass'] for c in cases) and before == after else 1


if __name__ == '__main__':
    raise SystemExit(main())
