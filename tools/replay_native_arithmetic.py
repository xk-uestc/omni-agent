"""Offline replay of successful annotation computations from frozen reports.

Reads selected proof operands only, reconstructs them from SHA-pinned original
PDFs, and checks current arithmetic. No gold answers or model/API invocation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic


def replay(report_paths):
    manifest=json.loads((ROOT/'benchmarks/ohr_bench/MANIFEST.json').read_text(encoding='utf8'))
    data_root=Path(manifest['data_root_default'])
    assets={row['pdf_sha256']:data_root/row['pdf_path'] for row in manifest['documents']}
    output={'created_at':datetime.now(timezone.utc).isoformat(), 'mode':'offline_pinned_pdf_arithmetic_replay',
            'api_calls':0,'gold_used':False,'semantic_review_replayed':False,
            'note':'Historical model/source freeze is unchanged. This only validates current deterministic arithmetic against historical selected proof operands.',
            'current_arithmetic_sha256':hashlib.sha256((ROOT/'ict-track8/backend/native_table_question.py').read_bytes()).hexdigest(),
            'reports':[],'cases':[]}
    cache={}
    for path in report_paths:
        raw=path.read_bytes();report=json.loads(raw)
        record={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),
                'historical_arithmetic_sha256':report.get('implementation_file_sha256',{}).get('ict-track8/backend/native_table_question.py'),
                'successful_native_table_cases':0}
        output['reports'].append(record)
        for row in report['cases']:
            generation=row.get('generation',{})
            if generation.get('status')!='ok' or generation.get('answer_mode')!='native_table_model_reviewed':continue
            record['successful_native_table_cases']+=1
            result={'report':path.name,'ID':row['ID'],'operation':generation['computation']['operation']}
            try:
                restored=[];document_ids=set()
                for citation in generation['citations']:
                    metadata=citation['metadata'];proof=metadata['fact'];digest=metadata['source_sha256']
                    document_ids.add(metadata['document_id'])
                    asset=assets.get(digest)
                    if asset is None or not asset.exists():raise ValueError('original_pdf_unavailable')
                    pdf=asset.read_bytes()
                    if hashlib.sha256(pdf).hexdigest()!=digest:raise ValueError('original_pdf_sha256_mismatch')
                    key=(digest,metadata['page_no'])
                    if key not in cache:cache[key]=extract_native_text_tables(pdf,page_no=metadata['page_no'],expected_source_sha256=digest)
                    matching=[fact for fact in cache[key]['facts'] if fact['fact_id']==proof['fact_id']]
                    if len(matching)!=1 or matching[0]!=proof:raise ValueError('original_fact_proof_replay_mismatch')
                    restored.append(matching[0])
                if len(document_ids)!=1:raise ValueError('cross_document_operands')
                actual=annotation_arithmetic(restored,result['operation'])
                result.update(status='PASS' if actual['answer']==generation['answer'] and actual['numeric_result']==generation['computation']['numeric_result'] else 'FAIL',
                              original_pdf_sha256=restored[0]['source_sha256'],fact_proof_match=True,
                              original_answer=generation['answer'],replayed_answer=actual['answer'],
                              original_numeric_result=generation['computation']['numeric_result'],replayed_numeric_result=actual['numeric_result'])
            except (ValueError,KeyError,OSError) as exc:
                result.update(status='UNVERIFIED',reason=str(exc))
            output['cases'].append(result)
    output['summary']={'successful_native_cases':len(output['cases']),
                       'passed':sum(c['status']=='PASS' for c in output['cases']),
                       'failed':sum(c['status']=='FAIL' for c in output['cases']),
                       'unverified':sum(c['status']=='UNVERIFIED' for c in output['cases'])}
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--reports',nargs='+',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=replay(args.reports)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result['summary']))
    sys.exit(0 if result['summary']['failed']==result['summary']['unverified']==0 else 1)
