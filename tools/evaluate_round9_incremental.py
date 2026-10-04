"""Record actual related regression after the two document-only patches."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def hashes(directory):
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*.py'))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    before=hashes(ROOT/'ict-track8/backend');tests_before=hashes(ROOT/'ict-track8/tests')
    names=['test_native_text_tables.py','test_native_monetary_panels.py','test_native_table_question.py',
        'test_native_table_difference_round5.py','test_round9_document_percentages.py',
        'test_round9_rotated_native_tables.py','test_round9_native_table_lanes.py',
        'test_round9_native_review_scope.py','test_visual_source_fallback.py',
        'test_round9_visual_selection_contract.py']
    command=[sys.executable,'-m','pytest','-q',*[str(Path('ict-track8/tests')/name) for name in names]]
    temporary=ROOT/'runtime/test-temp';temporary.mkdir(exist_ok=True)
    environment={**os.environ,'PYTHONPATH':str(ROOT/'ict-track8'),
        'TEMP':str(temporary),'TMP':str(temporary),'TMPDIR':str(temporary)}
    started=time.monotonic()
    run=subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace')
    print(run.stdout,end='')
    if run.stderr:print(run.stderr,file=sys.stderr,end='')
    counts={name:int(m.group(1)) if (m:=re.search(r'(\d+) '+name+r'\b',run.stdout)) else 0
            for name in ('passed','failed','skipped')}
    after=hashes(ROOT/'ict-track8/backend');tests_after=hashes(ROOT/'ict-track8/tests')
    stable=before==after and tests_before==tests_after
    report={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'related_native_visual_contracts_after_two_document_patches_not_full_local_suite',
        'model_called':False,'command':command[1:],'exit_code':run.returncode,
        'duration_seconds':round(time.monotonic()-started,3),**counts,
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,
        'test_file_sha256_start':tests_before,'test_file_sha256_end':tests_after,
        'implementation_stable':stable,'stdout':run.stdout,'stderr':run.stderr,
        'ok':run.returncode==0 and counts['failed']==0 and counts['passed']>=100 and stable}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    return 0 if report['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
