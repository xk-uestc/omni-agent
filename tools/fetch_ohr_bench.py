"""Download a frozen, resource-bounded official OHR-Bench pilot, not full corpus."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
HF_REVISION = '7f833e3eda9a571a9ea545a8f6d476fa1685033d'
CODE_REVISION = '1f421eb428f9f5b8ac0bc8064d6ad1f13fab7af7'
HF_ROOT = f'https://huggingface.co/datasets/opendatalab/OHR-Bench/resolve/{HF_REVISION}'
CODE_ROOT = f'https://raw.githubusercontent.com/opendatalab/OHR-Bench/{CODE_REVISION}'
PDF_ZIP_BYTES = 1516951813
DEFAULT_DATA = Path('D:/ICT8-OfficialDatasets/ohr-bench')
CATEGORIES = ('text', 'table', 'formula', 'chart', 'reading_order', 'multi')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def get_bounded(url, bound=12_000_000):
    with requests.get(url, timeout=(10, 60), stream=True) as response:
        response.raise_for_status()
        chunks, count = [], 0
        for chunk in response.iter_content(262144):
            count += len(chunk)
            if count > bound:
                raise ValueError('download_size_limit')
            chunks.append(chunk)
    return b''.join(chunks)


class RemoteZip(io.RawIOBase):
    """Read only requested ranges; reject 200 so the 1.5 GB archive is never read."""
    def __init__(self, url, size):
        self.url, self.size, self.position = url, size, 0
        self.transferred = 0
    def readable(self):
        return True
    def seekable(self):
        return True
    def tell(self):
        return self.position
    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if position < 0:
            raise ValueError('negative_seek')
        self.position = position
        return position
    def read(self, count=-1):
        count = self.size - self.position if count < 0 else min(count, self.size - self.position)
        if count <= 0:
            return b''
        if count > 8_000_000:
            raise ValueError('range_read_limit')
        start, end = self.position, self.position + count - 1
        # Range-specific URL avoids HTTP proxy caches reusing a different range.
        url = self.url + f'?download=true&range_start={start}&range_end={end}'
        with requests.get(url, headers={'Range': f'bytes={start}-{end}'},
                          stream=True, timeout=(10, 60)) as response:
            if response.status_code != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{self.size}':
                raise ValueError('server_did_not_honor_exact_range')
            raw = response.raw.read(count + 1)
        if len(raw) != count:
            raise ValueError('incorrect_range_length')
        self.transferred += count
        self.position += count
        return raw


def pdf_members(archive):
    members = {}
    for member in archive.infolist():
        name = member.filename.replace('\\', '/')
        if not name.lower().endswith('.pdf') or '/__MACOSX/' in '/' + name:
            continue
        # Archive prefixes vary; official doc_name is domain/file stem.
        match = re.search(r'(finance|manual|administration|academic|law|news|textbook)/(.+)\.pdf$', name)
        if match:
            key = match.group(1) + '/' + match.group(2)
            if key in members:
                raise ValueError('duplicate_pdf_doc_name')
            members[key] = member
    return members


def choose_cases(questions, members, per_category=2, max_pdf_bytes=2_000_000):
    """Resource-first fixed selection; never uses answer/evidence or model score."""
    selected, unsupported = [], {}
    for category in CATEGORIES:
        eligible = [row for row in questions if row['evidence_source'] == category
                    and row['doc_name'] in members
                    and 0 < members[row['doc_name']].file_size <= max_pdf_bytes]
        eligible.sort(key=lambda row: (members[row['doc_name']].file_size, row['ID']))
        selected.extend(eligible[:per_category])
        if len(eligible) < per_category:
            unsupported[category] = {'requested': per_category, 'eligible': len(eligible)}
    return selected, unsupported


def restore_selected(questions, manifest):
    by_id = {row['ID']: row for row in questions}
    selected = []
    for frozen in manifest['cases']:
        row = by_id[frozen['ID']]
        if sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8')) != frozen['original_row_sha256']:
            raise ValueError('frozen_official_row_changed')
        selected.append(row)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'benchmarks/ohr_bench/MANIFEST.json')
    parser.add_argument('--per-category', type=int, default=2, choices=(1, 2, 3))
    parser.add_argument('--restore-frozen', action='store_true', help='Restore exact existing manifest assets to data-root; never reselect or overwrite manifest')
    args = parser.parse_args()
    if args.manifest.exists() and not args.restore_frozen:
        parser.error('Frozen manifest already exists; do not overwrite selection')
    if args.restore_frozen and not args.manifest.exists():
        parser.error('Restore requires existing frozen manifest')
    frozen_manifest = json.loads(args.manifest.read_text(encoding='utf-8')) if args.restore_frozen else None
    if frozen_manifest and (frozen_manifest['hf_revision'] != HF_REVISION or frozen_manifest['official_code_revision'] != CODE_REVISION):
        raise ValueError('frozen_source_revision_mismatch')
    args.data_root.mkdir(parents=True, exist_ok=True)
    qa_raw = get_bounded(CODE_ROOT + '/data/qas_v2.json')
    questions = json.loads(qa_raw)
    if len(questions) != 8498 or not all({'ID', 'doc_name', 'questions', 'answers', 'evidence_source', 'evidence_page_no'} <= row.keys() for row in questions):
        raise ValueError('official_qa_schema_changed')
    if frozen_manifest and sha256(qa_raw) != frozen_manifest['qa_sha256']:
        raise ValueError('official_qa_source_sha_mismatch')
    (args.data_root / 'qas_v2.json').write_bytes(qa_raw)
    readme = get_bounded(HF_ROOT + '/README.md')
    (args.data_root / 'DATASET_README.md').write_bytes(readme)
    metadata = get_bounded('https://huggingface.co/api/datasets/opendatalab/OHR-Bench/revision/' + HF_REVISION)
    meta = json.loads(metadata)
    if meta.get('sha') != HF_REVISION:
        raise ValueError('dataset_revision_mismatch')
    (args.data_root / 'HF_METADATA.json').write_bytes(metadata)
    reader = RemoteZip(HF_ROOT + '/pdfs.zip', PDF_ZIP_BYTES)
    assets, cases = [], []
    with zipfile.ZipFile(reader) as archive:
        members = pdf_members(archive)
        if frozen_manifest:
            selected, unsupported = restore_selected(questions, frozen_manifest), frozen_manifest['unsupported_selection']
        else:
            selected, unsupported = choose_cases(questions, members, args.per_category)
        # Freeze selection BEFORE parsing/retrieval/model calls. Exact original
        # rows remain gold; no corrections or synthesized questions allowed.
        selected_raw = (json.dumps(selected, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if frozen_manifest and sha256(selected_raw) != frozen_manifest['selected_qa_sha256']:
            raise ValueError('frozen_selection_sha_mismatch')
        frozen = args.data_root / 'SELECTED_OFFICIAL_QA.json'
        if frozen.exists() and frozen.read_bytes() != selected_raw:
            raise ValueError('existing_frozen_selection_mismatch_do_not_replace')
        if not frozen.exists():
            frozen.write_bytes(selected_raw)
        for doc_name in sorted({row['doc_name'] for row in selected}):
            member = members[doc_name]
            raw = archive.read(member)
            if len(raw) != member.file_size or not raw.startswith(b'%PDF'):
                raise ValueError('invalid_original_pdf')
            expected_asset = next((a for a in frozen_manifest['documents'] if a['doc_name'] == doc_name), None) if frozen_manifest else None
            if expected_asset and sha256(raw) != expected_asset['pdf_sha256']:
                raise ValueError('frozen_pdf_sha_mismatch')
            relative = 'pdfs/' + doc_name + '.pdf'
            path = args.data_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            gt_url = CODE_ROOT + '/data/retrieval_base/gt/' + quote(doc_name, safe='/') + '.json'
            gt_raw = get_bounded(gt_url)
            json.loads(gt_raw)
            if expected_asset and sha256(gt_raw) != expected_asset['gt_sha256']:
                raise ValueError('frozen_gt_sha_mismatch')
            gt_path = args.data_root / ('gt/' + doc_name + '.json')
            gt_path.parent.mkdir(parents=True, exist_ok=True)
            gt_path.write_bytes(gt_raw)
            assets.append({'doc_name': doc_name, 'pdf_path': relative, 'pdf_bytes': len(raw),
                           'pdf_sha256': sha256(raw), 'archive_member': member.filename,
                           'archive_member_crc32': member.CRC, 'gt_path': 'gt/' + doc_name + '.json',
                           'gt_sha256': sha256(gt_raw), 'gt_url': gt_url})
            print(json.dumps({'document': doc_name, 'pdf_bytes': len(raw)}, ensure_ascii=False), flush=True)
        cases = [{'ID': row['ID'], 'doc_name': row['doc_name'], 'evidence_source': row['evidence_source'],
                  'evidence_page_no': row['evidence_page_no'], 'original_row_sha256': sha256(
                      json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8'))} for row in selected]
    if frozen_manifest:
        print(json.dumps({'restored_official_questions': len(cases), 'restored_documents': len(assets),
                          'manifest_unchanged': True, 'data_root': str(args.data_root.resolve())}, ensure_ascii=False))
        return 0
    manifest = {'dataset': 'opendatalab/OHR-Bench', 'official_dataset_url': 'https://huggingface.co/datasets/opendatalab/OHR-Bench',
                'hf_revision': HF_REVISION, 'official_code_revision': CODE_REVISION,
                'dataset_card_license': meta.get('cardData', {}).get('license'),
                'pdf_usage_statement': 'Official README: research purposes only, not commercial use; source PDFs retain original copyright.',
                'data_root_default': str(args.data_root.resolve()), 'created_at': datetime.now(timezone.utc).isoformat(),
                'selection_frozen_before_evaluation': True,
                'selection_rule': f'For each of six evidence categories, first {args.per_category} rows sorted by original PDF bytes then ID; PDF <=2000000 bytes; answer/evidence not used for selection.',
                'selection_limit': 'Resource-biased small pilot, not official full benchmark or representative accuracy; categories with missing eligible PDFs explicitly reported.',
                'qa_source_url': CODE_ROOT + '/data/qas_v2.json', 'qa_sha256': sha256(qa_raw),
                'official_qa_total': len(questions), 'selected_qa_path': 'SELECTED_OFFICIAL_QA.json',
                'selected_qa_sha256': sha256(selected_raw), 'readme_sha256': sha256(readme),
                'pdf_archive_lfs_sha256': 'f9bc65f383172c4ea47940c47dfab01dd36c03a120bc0450d7a962917098c783',
                'archive_full_sha_verified': False, 'archive_range_download_bytes': reader.transferred,
                'unsupported_selection': unsupported, 'cases': cases, 'documents': assets}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'selected_official_questions': len(cases), 'documents': len(assets),
                      'range_download_bytes': reader.transferred, 'manifest': str(args.manifest)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
