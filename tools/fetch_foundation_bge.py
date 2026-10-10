"""Download only missing assets from the existing pinned BGE manifest."""
import os
import time
import requests
from foundation_common import ROOT, dump, sha
import json


def main():
    manifest_path = ROOT / 'models/bge-small-zh-v1.5/ASSET_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    records = []
    for item in manifest['files']:
        path = ROOT / item['path']
        fetched = False
        started = time.perf_counter()
        if not path.exists():
            partial = path.with_suffix(path.suffix + '.download')
            try:
                with requests.get(item['url'], stream=True, timeout=(20, 60)) as response:
                    response.raise_for_status()
                    with partial.open('xb') as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            stream.write(chunk)
                if partial.stat().st_size != item['bytes'] or sha(partial) != item['sha256']:
                    raise ValueError('pinned_asset_integrity_mismatch')
                os.replace(partial, path)
                fetched = True
            finally:
                partial.unlink(missing_ok=True)
        if path.stat().st_size != item['bytes'] or sha(path) != item['sha256']:
            raise ValueError('existing_asset_integrity_mismatch')
        records.append({'path': item['path'], 'sha256': item['sha256'],
                        'bytes': item['bytes'], 'downloaded': fetched,
                        'wall_ms': (time.perf_counter() - started) * 1000})
    dump(ROOT / 'docs/foundation/runs/bge-assets.json',
         {'model': manifest['model'], 'revision': manifest['revision'],
          'manifest_sha256': sha(manifest_path), 'records': records,
          'paid_api_calls': 0})
    print('Pinned BGE assets verified; weights remain Git ignored.')


if __name__ == '__main__':
    main()
