"""Download four pinned official PDFs and optionally ingest them into local Omni-Agent.

Run with the project's .venv (requests and pypdf are existing dependencies):
  .venv/bin/python tools/download_product_pdfs.py
  .venv/bin/python tools/download_product_pdfs.py --ingest --api-base http://127.0.0.1:8030
  .venv/bin/python tools/download_product_pdfs.py --document-id public-stats-disclosure-2024 --ingest

The default writes originals and receipts only under ignored runtime/. It never
reads Gold or calls a language model. --ingest replaces the same document IDs
with the exact verified source bytes. An encrypted PDF is kept intact and the
server's rejection is recorded; the script does not remove encryption.
All selected files are attempted. Exit 1 means at least one operation failed;
the bundled encrypted Shanghai PDF currently yields HTTP 400 with --ingest.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader
from pypdf.errors import PdfReadError

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PAGES = 1000
MAX_DOWNLOAD_SECONDS = 90
SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate_pdf(raw: bytes, item: dict) -> dict:
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('PDF byte limit exceeded or file empty')
    if not raw.startswith(b'%PDF-'):
        raise ValueError('Source is not a PDF (missing signature)')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != item['sha256'] or len(raw) != item['bytes']:
        raise ValueError('Source bytes differ from the pinned manifest; existing file is preserved')
    reader = PdfReader(io.BytesIO(raw), strict=False)
    page_count = len(reader.pages)
    if not 0 < page_count <= MAX_PAGES or page_count != item['page_count']:
        raise ValueError('PDF page count differs from the pinned manifest or exceeds the limit')
    if reader.is_encrypted != item['encrypted']:
        raise ValueError('PDF encryption metadata differs from the manifest')
    return {'sha256': digest, 'bytes': len(raw), 'page_count': page_count,
            'encrypted': reader.is_encrypted, 'status': 'verified'}


def source_bytes(item: dict, destination: Path) -> tuple[bytes, dict]:
    if destination.exists():
        if destination.stat().st_size > MAX_BYTES:
            raise ValueError('Existing file exceeds the byte limit; file is preserved')
        raw = destination.read_bytes()
        return raw, {**validate_pdf(raw, item), 'download': 'existing_verified_file'}
    started = time.monotonic()
    with requests.get(item['url'], stream=True, timeout=(15, 30)) as response:
        response.raise_for_status()
        if urlparse(response.url).scheme != 'https':
            raise ValueError('Official source redirected away from HTTPS')
        length = response.headers.get('Content-Length')
        if length and int(length) > MAX_BYTES:
            raise ValueError('HTTP content length exceeds PDF byte limit')
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_BYTES or time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                raise ValueError('Download exceeded the byte or time limit')
            chunks.append(chunk)
    raw = b''.join(chunks)
    validation = validate_pdf(raw, item)
    temporary = destination.with_suffix('.pdf.download')
    temporary.write_bytes(raw)
    temporary.replace(destination)
    return raw, {**validation, 'download': 'downloaded_and_verified'}


def ingest(raw: bytes, item: dict, api_base: str, token: str) -> dict:
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    payload = {'document_id': item['document_id'], 'title': item['title'],
               'filename': item['filename'], 'modality': 'pdf',
               'file_base64': base64.b64encode(raw).decode('ascii'), 'language': 'chi_sim+eng'}
    started = time.monotonic()
    # Never forward a service access token through a redirect.
    with requests.post(api_base + '/api/v1/knowledge/ingest', json=payload,
                       headers=headers, timeout=(15, 180), stream=True,
                       allow_redirects=False) as response:
        result, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError('Ingest response exceeds the recording limit')
            result.append(chunk)
        body = b''.join(result)
        try:
            data = json.loads(body)
        except (UnicodeError, ValueError):
            data = {'detail': 'Response is not valid JSON', 'response_bytes': len(body)}
        success = response.status_code == 200
        if success and (data.get('document_id') != item['document_id']
                        or data.get('sha256') != item['sha256']):
            raise ValueError('Ingest response is not bound to the pinned document')
        return {'status': 'ok' if success else 'failed', 'http_status': response.status_code,
                'seconds': round(time.monotonic() - started, 3), 'response': data}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'docs/product/PUBLIC_PDFS_20261013.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/product_20261013/public_pdfs')
    parser.add_argument('--document-id', action='append', dest='document_ids', help='Repeat to select specific manifest IDs')
    parser.add_argument('--ingest', action='store_true', help='Upload verified original bytes; no language-model calls')
    parser.add_argument('--api-base', default='http://127.0.0.1:8030')
    parser.add_argument('--token-env', default='ICT8_API_TOKEN', help='Optional service token environment variable; value is never recorded')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    items = manifest['documents']
    if not isinstance(items, list) or not 1 <= len(items) <= 4:
        parser.error('This bounded replay requires a manifest of 1–4 documents')
    known = {item.get('document_id') for item in items}
    selected = set(args.document_ids or known)
    if not selected <= known:
        parser.error('Unknown document ID')
    api = urlparse(args.api_base)
    if api.scheme not in {'http', 'https'} or not api.netloc or api.username or api.password or api.query or api.fragment:
        parser.error('--api-base must be an HTTP(S) service URL without credentials, query or fragment')
    token = os.environ.get(args.token_env, '') if args.ingest else ''
    if token and api.scheme != 'https' and api.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        parser.error('Service tokens require HTTPS outside localhost')
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'manifest': str(args.manifest), 'ingest_requested': args.ingest, 'documents': []}
    operation = 'ingest' if args.ingest else 'download'
    report_path = args.output / (operation + '-receipt.json')
    failed = False
    for item in items:
        document_id = item['document_id']
        if document_id not in selected:
            continue
        if not SAFE_ID.fullmatch(document_id) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            parser.error('Invalid document ID or SHA-256 in manifest')
        filename = item['filename']
        if Path(filename).name != filename or '\\' in filename or not filename.endswith('.pdf'):
            parser.error('Manifest filename must be a plain PDF filename')
        url = urlparse(item['url'])
        if url.scheme != 'https' or not url.netloc or url.username or url.password:
            parser.error('Manifest sources must use public HTTPS URLs without credentials')
        receipt = {'document_id': document_id, 'title': item['title'], 'url': item['url']}
        try:
            raw, verification = source_bytes(item, args.output / filename)
            receipt['verification'] = verification
            if args.ingest:
                receipt['ingest'] = ingest(raw, item, args.api_base.rstrip('/'), token)
                failed |= receipt['ingest']['status'] != 'ok'
            receipt['status'] = receipt.get('ingest', verification)['status']
        except (OSError, ValueError, PdfReadError, requests.RequestException) as exc:
            failed = True
            # Exception type/code only: requests errors can contain credential-bearing URLs.
            receipt['status'] = 'failed'
            receipt['error_type'] = type(exc).__name__
            receipt['error'] = str(exc)[:300] if isinstance(exc, ValueError) else 'Download, file validation or service request failed; inspect local connectivity and source availability.'
        receipt_path = args.output / (document_id + '-' + operation + '-replay.json')
        write_json(receipt_path, receipt)
        report['documents'].append(receipt)
        write_json(report_path, report)
        print(json.dumps({'document_id': document_id, 'status': receipt['status'],
                          'receipt': str(receipt_path)}, ensure_ascii=False), flush=True)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['had_failures'] = failed
    write_json(report_path, report)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
