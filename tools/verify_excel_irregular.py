"""Generate reproducible synthetic Excel samples and score the real parser."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.chunk_cleaning import DocumentChunker
from excel_irregular_cases import build_cases,evaluate_case,write_samples


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-head-parser',action='store_true')
    args=parser.parse_args();cases=build_cases();write_samples(args.output/'samples',cases)
    baseline=None
    if args.baseline_head_parser:
        import ast,subprocess
        from backend import chunk_cleaning
        source=subprocess.check_output(['git','show','HEAD:ict-track8/backend/chunk_cleaning.py'],cwd=ROOT).decode('utf-8')
        tree=ast.parse(source);definition=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='DocumentChunker')
        method=next(node for node in definition.body if isinstance(node,ast.FunctionDef) and node.name=='parse_xlsx')
        namespace=dict(vars(chunk_cleaning));exec(compile(ast.Module(body=[method],type_ignores=[]),'<baseline parse_xlsx>','exec'),namespace)
        DocumentChunker.parse_xlsx=namespace['parse_xlsx']
        baseline={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
                  'method_sha256':hashlib.sha256(ast.get_source_segment(source,method).encode()).hexdigest()}
    results=[]
    for case in cases:
        start=time.perf_counter()
        try:
            payload=DocumentChunker().parse_xlsx(case.raw,document_id=case.name).to_dict()
            (args.output/(case.name+'-parsed.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            result=evaluate_case(case,payload)
        except Exception as exc:result={'name':case.name,'description':case.description,'passed':False,'error':str(exc)}
        result['seconds']=time.perf_counter()-start;results.append(result)
        print(json.dumps({'case':case.name,'passed':result['passed'],'seconds':round(result['seconds'],3)},ensure_ascii=False),flush=True)
    files=['ict-track8/backend/chunk_cleaning.py','ict-track8/backend/excel_layout.py']
    report={'synthetic_developer_fixtures':True,'passed':sum(r['passed'] for r in results),'total':len(results),
            'baseline':baseline,'source_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files if (ROOT/f).exists()},'results':results}
    (args.output/'parser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
