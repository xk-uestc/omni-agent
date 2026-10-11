"""Fetch explicitly selected local parser assets, pinned and LFS-hash checked.

No hosted inference, no API keys. Resumable ranges avoid broken large transfers.
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import requests

from prepare_real_document_evaluation import get_bounded, ranged_asset, write_once


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--revision', required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--record', type=Path, required=True)
    args = p.parse_args()
    meta = requests.get(f'https://huggingface.co/api/models/{args.repo}/revision/{args.revision}', timeout=40)
    meta.raise_for_status()
    rev = meta.json()['sha']
    response = requests.get(f'https://huggingface.co/api/models/{args.repo}/tree/{rev}?recursive=true&expand=false', timeout=40)
    response.raise_for_status()
    files = [f for f in response.json() if f['type']=='file' and not f['path'].startswith('.')]
    if sum(f['size'] for f in files)>4_000_000_000:
        raise ValueError('model_repo_exceeds_4GB_download_budget')
    result = {'repo': args.repo, 'revision': rev, 'files': []}
    for f in files:
        path = args.destination/f['path']
        if not path.resolve().is_relative_to(args.destination.resolve()):
            raise ValueError('unsafe_model_path')
        expected = f.get('lfs', {}).get('oid')
        if path.exists() and path.stat().st_size==f['size'] and (not expected or hashlib.sha256(path.read_bytes()).hexdigest()==expected):
            raw = path.read_bytes()
        else:
            url = f'https://huggingface.co/{args.repo}/resolve/{rev}/'+quote(f['path'], safe='/')
            raw = (ranged_asset(url, f['size'], Path('runtime/real_document_upgrade')) if f['size']>8_000_000
                   else get_bounded(url, bound=8_000_000))
            observed = hashlib.sha256(raw).hexdigest()
            if len(raw)!=f['size'] or expected and observed!=expected:
                raise ValueError('model_asset_checksum_mismatch')
            write_once(path, raw)
        result['files'].append({'path': f['path'], 'bytes':len(raw), 'sha256':hashlib.sha256(raw).hexdigest(),
                                'official_lfs_sha_verified': bool(expected)})
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(result,indent=2))
        print(json.dumps({'file':f['path'], 'bytes':len(raw), 'verified':bool(expected)}),flush=True)


if __name__=='__main__':
    main()
