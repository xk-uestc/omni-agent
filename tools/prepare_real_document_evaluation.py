"""Freeze public originals and isolated evaluation references before parser trials.

Research-only assets stay under runtime, never enter KnowledgeStore from Gold.
OmniDocBench v1.0 is deliberate: its pinned release includes original PDF slices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fetch_ohr_bench import CODE_ROOT, HF_ROOT, HF_REVISION, PDF_ZIP_BYTES, RemoteZip, get_bounded, pdf_members

ROOT = Path(__file__).resolve().parents[1]
ODB_REVISION = 'f5f559bddf50e36f7f9899d842d0006f13ce8afc'
ODB_ROOT = 'https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/' + ODB_REVISION


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_once(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError('frozen_asset_changed:' + str(path))
    if not path.exists():
        path.write_bytes(raw)


def dump(path, obj):
    write_once(path, (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode())


def ranged_asset(url, size, base):
    """Resume exact bounded ranges; a proxy truncation never publishes a file."""
    parts = base/'downloads'/sha(url.encode())
    parts.mkdir(parents=True, exist_ok=True)
    block = 4_000_000

    def fetch(start):
        length = min(block, size-start)
        path = parts/str(start)
        if path.exists() and path.stat().st_size == length:
            return path
        for attempt in range(3):
            try:
                reader = RemoteZip(url, size)
                reader.seek(start)
                raw = reader.read(length)
                write_once(path, raw)
                print(json.dumps({'range_start': start, 'bytes': length}), flush=True)
                return path
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(fetch, range(0, size, block)))
    raw = b''.join(p.read_bytes() for p in paths)
    if len(raw) != size:
        raise ValueError('ranged_asset_size_mismatch')
    return raw


def original_group(name):
    return re.sub(r'(?:_page_\d+|\.pdf_\d+|_\d+)$', '', Path(name).stem)


def freeze_odb(base):
    raw = ranged_asset(ODB_ROOT + '/OmniDocBench.json', 78_134_884, base)
    write_once(base/'gold/OmniDocBench-v1_0.json', raw)
    readme = get_bounded(ODB_ROOT + '/README.md')
    write_once(base/'licenses/OmniDocBench-v1_0-README.md', readme)
    pages = json.loads(raw)
    # Selection uses ONLY attributes/category presence, no recognition content.
    selected, used = [], set()
    for source in ('academic_literature', 'research_report', 'book', 'newspaper', 'colorful_textbook', 'PPT2PDF'):
        eligible = [p for p in pages if p['page_info']['page_attribute']['data_source'] == source]
        eligible.sort(key=lambda p: sha(p['page_info']['image_path'].encode()))
        for split in ('dev', 'retained'):
            for target in ('equation_isolated', 'table'):
                options = [p for p in eligible if original_group(p['page_info']['image_path']) not in used
                           and any(d['category_type'] == target and not d.get('ignore') for d in p['layout_dets'])]
                if not options:
                    options = [p for p in eligible if original_group(p['page_info']['image_path']) not in used]
                if not options:
                    raise ValueError('selection_bucket_empty')
                p = options[0]
                name = p['page_info']['image_path']
                used.add(original_group(name))
                selected.append({'split': split, 'image_name': name, 'group': original_group(name),
                                 'source': source, 'target_category': target,
                                 'attributes': p['page_info']['page_attribute']})
    dump(base/'odb-selection.json', selected)
    assets = []
    for row in selected:
        name = str(Path(row['image_name']).with_suffix('.pdf'))
        url = ODB_ROOT + '/ori_pdfs/' + quote(name, safe='/')
        pdf = get_bounded(url, bound=20*1024*1024)
        if not pdf.startswith(b'%PDF'):
            raise ValueError('not_original_pdf')
        path = Path('inputs/odb')/name
        write_once(base/path, pdf)
        assets.append({**row, 'dataset': 'OmniDocBench-v1.0', 'pdf_path': str(path),
                       'pdf_sha256': sha(pdf), 'pdf_bytes': len(pdf), 'url': url})
        print(json.dumps({'odb': name, 'bytes': len(pdf)}), flush=True)
    return {'revision': ODB_REVISION, 'gold_sha256': sha(raw), 'readme_sha256': sha(readme),
            'original_pdf_slices_not_image_wrappers': True, 'documents': assets}


def freeze_ohr(base):
    excluded = set()
    for path in (ROOT/'benchmarks/ohr_bench').glob('MANIFEST*.json'):
        excluded.update(d['doc_name'] for d in json.loads(path.read_text())['documents'])
    # Include prior diagnostic use beyond manifest names.
    for folder in ('docs', 'tools', 'ict-track8/tests'):
        for path in (ROOT/folder).rglob('*'):
            if path.is_file() and path.suffix in {'.json', '.md', '.py'}:
                excluded.update(re.findall(r'(?:finance|manual|administration|academic|law|news|textbook)/DUDE_[a-f0-9]{32}', path.read_text(errors='replace')))
    raw = get_bounded(CODE_ROOT + '/data/qas_v2.json')
    questions = json.loads(raw)
    write_once(base/'gold/OHR-qas_v2.json', raw)
    readme = get_bounded(HF_ROOT + '/README.md')
    write_once(base/'licenses/OHR-README.md', readme)
    selected, used = [], set(excluded)
    assets = []
    with zipfile.ZipFile(RemoteZip(HF_ROOT+'/pdfs.zip', PDF_ZIP_BYTES)) as archive:
        members = pdf_members(archive)
        for split in ('dev', 'retained'):
            for category in ('text', 'table', 'formula', 'reading_order', 'multi', 'chart'):
                options = [q for q in questions if q['evidence_source'] == category and q['doc_name'] not in used
                           and q['doc_name'] in members and members[q['doc_name']].file_size <= 2_000_000]
                options.sort(key=lambda q: sha(q['doc_name'].encode()))
                if not options:
                    raise ValueError('ohr_bucket_empty')
                q = options[0]
                used.add(q['doc_name'])
                rows = sorted([r for r in questions if r['doc_name'] == q['doc_name']], key=lambda r: r['ID'])[:3]
                selected.extend({'split': split, 'ID': r['ID'], 'doc_name': r['doc_name'],
                                 'questions': r['questions'], 'evidence_source': r['evidence_source'],
                                 'original_row_sha256': sha(json.dumps(r, ensure_ascii=False, sort_keys=True).encode())}
                                for r in rows)
        dump(base/'ohr-selection.json', selected)
        for name in sorted({q['doc_name'] for q in selected}):
            raw_pdf = archive.read(members[name])
            relative = Path('inputs/ohr')/(name+'.pdf')
            write_once(base/relative, raw_pdf)
            gt = get_bounded(CODE_ROOT+'/data/retrieval_base/gt/'+name+'.json')
            write_once(base/'gold/ohr'/(name+'.json'), gt)
            assets.append({'dataset': 'OHR-Bench', 'doc_name': name,
                           'split': next(q['split'] for q in selected if q['doc_name']==name),
                           'pdf_path': str(relative), 'pdf_sha256': sha(raw_pdf), 'pdf_bytes': len(raw_pdf),
                           'gt_path': 'gold/ohr/'+name+'.json', 'gt_sha256': sha(gt)})
            print(json.dumps({'ohr': name, 'bytes': len(raw_pdf)}), flush=True)
    return {'revision': HF_REVISION, 'qa_sha256': sha(raw), 'readme_sha256': sha(readme),
            'excluded_previous_documents': sorted(excluded), 'cases': selected, 'documents': assets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT/'runtime/real_document_upgrade/data')
    parser.add_argument('--dataset', choices=('odb', 'ohr'), required=True)
    args = parser.parse_args()
    result = freeze_odb(args.root) if args.dataset == 'odb' else freeze_ohr(args.root)
    dump(args.root/(args.dataset+'-manifest.json'), result)
    print(json.dumps({'dataset': args.dataset, 'documents': len(result['documents'])}), flush=True)


if __name__ == '__main__':
    main()
