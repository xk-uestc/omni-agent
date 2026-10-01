"""Freeze genuinely new official PDFs/QA by metadata, never by gold answers."""
from collections import Counter
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
import zipfile
from urllib.parse import quote

from fetch_ohr_bench import (ROOT, HF_ROOT, HF_REVISION, CODE_ROOT, CODE_REVISION,
                            PDF_ZIP_BYTES, RemoteZip, pdf_members, get_bounded, sha256, CATEGORIES)

DATA = Path('D:/ICT8-OfficialDatasets/ohr-bench')
DEST = ROOT/'benchmarks/ohr_bench/MANIFEST_NEW_DOCUMENTS.json'
DOMAINS = ('academic', 'administration', 'finance', 'law', 'manual', 'news', 'textbook')


def question_key(row):
    return ' '.join(json.dumps(row['questions'], ensure_ascii=False, sort_keys=True).casefold().split())


def pick(rows, members, excluded_docs, excluded_ids, excluded_questions):
    """Seven domains; two QA/document minimum, then balance six categories to 18."""
    eligible = [r for r in rows if r['ID'] not in excluded_ids and r['doc_name'] not in excluded_docs
                and question_key(r) not in excluded_questions and r['doc_name'] in members
                and 0 < members[r['doc_name']].file_size <= 2_000_000]
    grouped = {}
    for row in eligible:
        grouped.setdefault(row['doc_name'], []).append(row)
    chosen_docs, covered = [], set()
    for domain in DOMAINS:
        candidates = [name for name, cases in grouped.items()
                      if name.split('/')[0] == domain and len(cases) >= 2]
        if not candidates:
            raise ValueError('required_domain_has_no_resource_eligible_new_document')
        name = min(candidates, key=lambda n: (-len({r['evidence_source'] for r in grouped[n]} - covered),
                   -len({r['evidence_source'] for r in grouped[n]}), members[n].file_size, n))
        chosen_docs.append(name)
        covered.update(r['evidence_source'] for r in grouped[name])
    selected, counts, seen = [], Counter(), set(excluded_questions)
    def select(pool):
        available = [r for r in pool if r['ID'] not in {p['ID'] for p in selected} and question_key(r) not in seen]
        if not available:
            raise ValueError('not_enough_distinct_questions')
        row = min(available, key=lambda r: (counts[r['evidence_source']], CATEGORIES.index(r['evidence_source']), r['ID']))
        selected.append(row); counts[row['evidence_source']] += 1; seen.add(question_key(row))
    for name in chosen_docs:
        select(grouped[name]); select(grouped[name])
    pool = [r for name in chosen_docs for r in grouped[name]]
    while len(selected) < 18:
        select(pool)
    return selected, chosen_docs


def save_exact(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('existing_asset_changed_no_overwrite')
    else:
        path.write_bytes(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-name', default=DEST.name)
    parser.add_argument('--selection-name', default='SELECTED_OFFICIAL_QA_NEW_DOCUMENTS.json')
    args = parser.parse_args()
    if (not re.fullmatch(r'MANIFEST[A-Z0-9_]*\.json', args.manifest_name)
            or not re.fullmatch(r'SELECTED_OFFICIAL_QA[A-Z0-9_]*\.json', args.selection_name)):
        parser.error('Use new simple frozen manifest/selection names, never a path')
    destination = DEST.with_name(args.manifest_name)
    if destination.exists():
        raise FileExistsError('selection_already_frozen')
    base = json.loads((ROOT/'benchmarks/ohr_bench/MANIFEST.json').read_text(encoding='utf-8'))
    qa_raw = (DATA/'qas_v2.json').read_bytes()
    if sha256(qa_raw) != base['qa_sha256']:
        raise ValueError('official_qa_revision_changed')
    rows = json.loads(qa_raw)
    if len({r['ID'] for r in rows}) != len(rows):
        raise ValueError('duplicate_official_id')
    docs, ids, questions, exclusions, old_pdf_hashes = set(), set(), set(), [], set()
    for path in sorted((ROOT/'benchmarks/ohr_bench').glob('MANIFEST*.json')):
        raw = path.read_bytes(); manifest = json.loads(raw)
        selected_raw = (DATA/manifest['selected_qa_path']).read_bytes()
        if sha256(selected_raw) != manifest['selected_qa_sha256']:
            raise ValueError('prior_exposure_manifest_mismatch')
        prior = json.loads(selected_raw)
        docs.update(d['doc_name'] for d in manifest['documents'])
        old_pdf_hashes.update(d['pdf_sha256'] for d in manifest['documents'])
        ids.update(r['ID'] for r in prior); questions.update(question_key(r) for r in prior)
        exclusions.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(raw)})
    # Also exclude any saved diagnostic runs not represented by a manifest.
    for path in sorted((ROOT/'docs').glob('OHR_BENCH*.json')):
        raw = path.read_bytes(); report = json.loads(raw)
        for case in report.get('cases', []):
            if case.get('doc_name'): docs.add(case['doc_name'])
            if case.get('ID'): ids.add(case['ID'])
            if case.get('question'): questions.add(question_key({'questions': case['question']}))
        exclusions.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(raw)})
    reader = RemoteZip(HF_ROOT+'/pdfs.zip', PDF_ZIP_BYTES)
    with zipfile.ZipFile(reader) as archive:
        members = pdf_members(archive)
        selected, chosen = pick(rows, members, docs, ids, questions)
        frozen_at = datetime.now(timezone.utc).isoformat()
        selected_raw = (json.dumps(selected, ensure_ascii=False, indent=2)+'\n').encode()
        selection_name = args.selection_name
        # Freeze before parsing PDFs, looking at GT or running the system.
        save_exact(DATA/selection_name, selected_raw)
        assets = []
        for name in chosen:
            member = members[name]; raw = archive.read(member)
            if len(raw) != member.file_size or not raw.startswith(b'%PDF'):
                raise ValueError('official_original_pdf_invalid')
            if sha256(raw) in old_pdf_hashes or any(a['pdf_sha256'] == sha256(raw) for a in assets):
                # Byte-identical documents under another name are not new.
                # Do not replace a frozen selected case to improve a score.
                raise ValueError('new_document_bytes_duplicate_exposed_or_selected_pdf')
            pdf_path = 'pdfs/'+name+'.pdf'; save_exact(DATA/pdf_path, raw)
            gt_url = CODE_ROOT+'/data/retrieval_base/gt/'+quote(name, safe='/')+'.json'
            gt_raw = get_bounded(gt_url); json.loads(gt_raw)
            gt_path = 'gt/'+name+'.json'; save_exact(DATA/gt_path, gt_raw)
            assets.append({'doc_name': name, 'pdf_path': pdf_path, 'pdf_sha256': sha256(raw),
                'pdf_bytes': len(raw), 'archive_member': member.filename, 'archive_member_crc32': member.CRC,
                'gt_path': gt_path, 'gt_sha256': sha256(gt_raw), 'gt_url': gt_url})
            print(json.dumps({'domain': name.split('/')[0], 'downloaded_pdf_bytes': len(raw)}), flush=True)
    counts = Counter(r['evidence_source'] for r in selected)
    if set(chosen) & docs or any(r['ID'] in ids or question_key(r) in questions for r in selected):
        raise ValueError('exposure_overlap')
    manifest = {**base, 'created_at': frozen_at, 'selection_frozen_before_evaluation': True,
        'selection_rule': 'Exclude all prior manifest and saved-run documents/IDs/exact normalized questions. Fixed seven domains; rank by missing category coverage, category diversity, PDF bytes and doc_name only; two questions/document minimum then balance category counts to 18 by stable ID. Answers/evidence/parser/model outcomes NEVER used.',
        'selection_limit': 'Unseen-document AND unseen-query resource-biased seven-domain official subset; 18 QA, 7 PDFs <=2MB each. Not full benchmark or statistically representative accuracy. Selection frozen before any PDF/GT inspection or model invocation.',
        'evaluation_scope': 'new_documents_and_queries_official_resource_bounded_subset',
        'excluded_document_count': len(docs), 'excluded_query_count': len(ids),
        'exclusion_inputs': exclusions, 'excluded_doc_names': sorted(docs),
        'data_root_default': str(DATA), 'selected_qa_path': selection_name,
        'selected_qa_sha256': sha256(selected_raw), 'documents': assets,
        'cases': [{k: r[k] for k in ('ID','doc_name','evidence_source','evidence_page_no')} |
                  {'original_row_sha256': sha256(json.dumps(r, ensure_ascii=False, sort_keys=True).encode())} for r in selected],
        'category_counts': dict(counts), 'unsupported_selection': {c: {'requested': 1, 'selected': 0} for c in CATEGORIES if counts[c] == 0},
        'archive_range_download_bytes': reader.transferred, 'archive_full_sha_verified': False}
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'documents': len(assets), 'questions': len(selected), 'category_counts': dict(counts),
        'selection_sha256': manifest['selected_qa_sha256'], 'gold_or_model_used_for_selection': False}))


if __name__ == '__main__':
    main()
