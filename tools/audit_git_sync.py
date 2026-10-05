"""Read-only prospective/staged source audit; never prints secret contents."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
    'long_api_key':re.compile(rb'\bsk-[A-Za-z0-9_-]{24,}'),
    'github_token':re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'private_key':re.compile(rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'bearer_literal':re.compile(rb'(?i)Bearer\s+[A-Za-z0-9._-]{35,}'),
}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--staged',action='store_true');args=parser.parse_args()
    if args.staged:
        raw=subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'],cwd=ROOT)
    else:
        raw=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=ROOT)
    files=sorted(set(p.decode('utf-8') for p in raw.split(b'\0') if p));findings=[];manifest=[]
    configured_secrets=[]
    config_path=ROOT/'runtime/model_config.json'
    if config_path.is_file():
        config=json.loads(config_path.read_text(encoding='utf-8'))
        key=config.get('api_key')
        if isinstance(key,str) and len(key)>=8:configured_secrets.append(key.encode())
    for name in files:
        path=ROOT/name
        if args.staged:
            data=subprocess.check_output(['git','show',':'+name],cwd=ROOT)
        elif path.is_file():data=path.read_bytes()
        else:continue
        parts=Path(name).parts
        if any(p in {'runtime','backups','.venv','node_modules','.git'} for p in parts) or path.name in {'auth.json','model_config.json'} or path.suffix in {'.key','.pem','.sqlite','.sqlite3'}:
            findings.append({'file':name,'reason':'runtime_or_credential_path'})
        if len(data)>50*1024*1024:findings.append({'file':name,'reason':'large_file_over_50MiB','bytes':len(data)})
        for label,pattern in PATTERNS.items():
            if pattern.search(data):findings.append({'file':name,'reason':label})
        if any(secret in data for secret in configured_secrets):findings.append({'file':name,'reason':'exact_project_credential'})
        manifest.append({'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    output=ROOT/'runtime'/('git-sync-staged-audit.json' if args.staged else 'git-sync-prospective-audit.json')
    output.parent.mkdir(exist_ok=True)
    report={'passed':not findings,'staged':args.staged,'files':len(manifest),'bytes':sum(f['bytes'] for f in manifest),'findings':findings,'manifest':manifest}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='manifest'},ensure_ascii=False))
    return 0 if not findings else 1


if __name__=='__main__':sys.exit(main())
