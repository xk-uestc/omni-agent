"""Run the frozen scorer against a read-only exported backend snapshot."""
import sys
from pathlib import Path
import foundation_common as common


def option(name):
    i=sys.argv.index(name);value=sys.argv[i+1];del sys.argv[i:i+2];return value


snapshot=Path(option('--snapshot-root'))
commit=option('--source-commit')
sys.path.insert(0,str(snapshot/'ict-track8'))
import evaluate_foundation_rag as frozen


def hashes():
    return {str(p.relative_to(snapshot)):common.sha(p) for p in sorted((snapshot/'ict-track8/backend').rglob('*.py'))}


original_dump=frozen.dump

def dump(path,report):
    report['evaluated_implementation_commit']=commit
    report['snapshot_export_only_no_branch_switch']=True
    original_dump(path,report)


if __name__=='__main__':
    frozen.source_hashes=hashes
    frozen.dump=dump
    frozen.main()
