"""Read-only inspection of native cross-page navigation candidates."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ict-track8'))
from backend.native_table_chain import probe

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf',type=Path)
    args=parser.parse_args()
    print(json.dumps(probe(args.pdf.read_bytes()),ensure_ascii=False,indent=2))
