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


def pick_category_metadata(rows, members, excluded_docs, excluded_ids, excluded_hashes,
                           hash_document, per_category=6, max_pdf_bytes=2_000_000):
    """Select from metadata only; check original bytes before admitting any ID.

    `rows` deliberately contains no question, answer, evidence text or GT.
    PDF bytes are hashed, never parsed. Aliases of exposed or selected bytes
    cannot enter the new corpus, even when their document names are different.
    """
    eligible = [r for r in rows if r['ID'] not in excluded_ids
                and r['doc_name'] not in excluded_docs
                and r['evidence_source'] in CATEGORIES and r['doc_name'] in members
                and 0 < members[r['doc_name']].file_size <= max_pdf_bytes]
    selected, owners, checked, rejected = [], {}, {}, {}
    for category in CATEGORIES:
        pool = sorted((r for r in eligible if r['evidence_source'] == category),
                      key=lambda r: (members[r['doc_name']].file_size, r['doc_name'], r['ID']))
        count = 0
        for row in pool:
            if count == per_category:
                break
            name = row['doc_name']
            if name not in checked:
                checked[name] = hash_document(name)
            digest = checked[name]
            if digest in excluded_hashes:
                rejected[name] = 'original_sha256_previously_exposed'
                continue
            if digest in owners and owners[digest] != name:
                rejected[name] = 'original_sha256_alias_of_selected_document'
                continue
            owners[digest] = name
            selected.append(row)
            count += 1
    return selected, sorted(set(r['doc_name'] for r in selected)), rejected


def freeze_metadata_categories(args, destination, base, qa_raw, rows):
    """Strict document holdout; historic texts are never used for selection."""
    docs, ids, hashes, exclusions = set(), set(), set(), []
    for path in sorted((ROOT/'benchmarks/ohr_bench').glob('MANIFEST*.json')):
        raw = path.read_bytes()
        prior = json.loads(raw)
        prior_root = Path(prior['data_root_default'])
        if sha256((prior_root/prior['selected_qa_path']).read_bytes()) != prior['selected_qa_sha256']:
            raise ValueError('prior_exposure_manifest_mismatch')
        docs.update(d['doc_name'] for d in prior['documents'])
        hashes.update(d['pdf_sha256'] for d in prior['documents'])
        ids.update(r['ID'] for r in prior['cases'])
        exclusions.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(raw)})
    # All OHR saved runs, not only the original OHR_BENCH naming convention.
    for path in sorted((ROOT/'docs').glob('OHR*.json')):
        raw = path.read_bytes()
        prior = json.loads(raw)
        for document in prior.get('documents', []):
            if document.get('doc_name'):
                docs.add(document['doc_name'])
            if document.get('pdf_sha256'):
                hashes.add(document['pdf_sha256'])
        for row in prior.get('cases', []):
            if row.get('doc_name'):
                docs.add(row['doc_name'])
            if row.get('ID'):
                ids.add(row['ID'])
        exclusions.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': sha256(raw)})
    # A saved exposure without a hash must be resolved from its original PDF.
    for name in sorted(docs):
        original = DATA/('pdfs/'+name+'.pdf')
        if not original.is_file():
            raise ValueError('previously_exposed_original_unavailable_for_sha_exclusion')
        hashes.add(sha256(original.read_bytes()))
    metadata = [{k: row[k] for k in ('ID', 'doc_name', 'evidence_source')} for row in rows]
    reader = RemoteZip(HF_ROOT+'/pdfs.zip', PDF_ZIP_BYTES)
    candidate_bytes = {}
    with zipfile.ZipFile(reader) as archive:
        members = pdf_members(archive)
        def hash_document(name):
            member = members[name]
            raw = archive.read(member)
            if len(raw) != member.file_size or not raw.startswith(b'%PDF'):
                raise ValueError('official_original_pdf_invalid')
            candidate_bytes[name] = raw
            return sha256(raw)
        selected_metadata, chosen, rejected = pick_category_metadata(
            metadata, members, docs, ids, hashes, hash_document,
            args.per_category, args.max_pdf_bytes)
        selected_ids = {r['ID'] for r in selected_metadata}
        opaque_rows = {r['ID']: r for r in rows if r['ID'] in selected_ids}
        selected = [opaque_rows[r['ID']] for r in selected_metadata]
        frozen_at = datetime.now(timezone.utc).isoformat()
        selected_raw = (json.dumps(selected, ensure_ascii=False, indent=2)+'\n').encode()
        save_exact(DATA/args.selection_name, selected_raw)
        assets = []
        for name in chosen:
            member, raw = members[name], candidate_bytes[name]
            pdf_path = 'pdfs/'+name+'.pdf'
            save_exact(DATA/pdf_path, raw)
            gt_url = CODE_ROOT+'/data/retrieval_base/gt/'+quote(name, safe='/')+'.json'
            # Preserve opaque official GT bytes for later scoring only.
            gt_raw = get_bounded(gt_url)
            gt_path = 'gt/'+name+'.json'
            save_exact(DATA/gt_path, gt_raw)
            assets.append({'doc_name': name, 'pdf_path': pdf_path, 'pdf_sha256': sha256(raw),
                           'pdf_bytes': len(raw), 'archive_member': member.filename,
                           'archive_member_crc32': member.CRC, 'gt_path': gt_path,
                           'gt_sha256': sha256(gt_raw), 'gt_url': gt_url})
            print(json.dumps({'domain': name.split('/')[0], 'downloaded_pdf_bytes': len(raw)}), flush=True)
    counts = Counter(r['evidence_source'] for r in selected_metadata)
    unsupported = {c: {'requested': args.per_category, 'selected': counts[c],
                      'missing': args.per_category-counts[c]}
                   for c in CATEGORIES if counts[c] < args.per_category}
    manifest = {**base, 'created_at': frozen_at, 'selection_frozen_before_evaluation': True,
        'selection_rule': 'Exclude every historical manifest/run document name, stable ID and original PDF SHA256. For each of six evidence categories, rank by original PDF bytes, doc_name and stable ID only. Hash candidate original bytes to reject exposed/selected aliases before freezing IDs. Question, answer, evidence text, GT and model outcomes are never inspected for selection.',
        'selection_limit': f'Resource-biased strict new-document subset; requested {6*args.per_category} QA ({args.per_category}/category), selected {len(selected)}, PDF <= {args.max_pdf_bytes} bytes. Original bytes are hashed before selection but not parsed; QA and GT are sealed for scoring. Not a full or representative benchmark.',
        'evaluation_scope': 'strict_original_sha_new_documents_metadata_only_category_balanced',
        'requested_case_count': 6*args.per_category, 'selected_case_count': len(selected),
        'requested_per_category': args.per_category, 'max_pdf_bytes': args.max_pdf_bytes,
        'excluded_document_count': len(docs), 'excluded_query_count': len(ids),
        'excluded_original_sha256_count': len(hashes), 'excluded_original_sha256': sorted(hashes),
        'exclusion_inputs': exclusions, 'excluded_doc_names': sorted(docs),
        'rejected_candidate_metadata': rejected, 'data_root_default': str(DATA),
        'selected_qa_path': args.selection_name, 'selected_qa_sha256': sha256(selected_raw),
        'documents': assets, 'cases': [{k: r[k] for k in ('ID','doc_name','evidence_source','evidence_page_no')} |
            {'original_row_sha256': sha256(json.dumps(r, ensure_ascii=False, sort_keys=True).encode())} for r in selected],
        'category_counts': {c: counts[c] for c in CATEGORIES}, 'unsupported_selection': unsupported,
        'archive_range_download_bytes': reader.transferred, 'archive_full_sha_verified': False,
        'freeze_tool_sha256': sha256(Path(__file__).read_bytes())}
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'documents': len(assets), 'requested_questions': 6*args.per_category,
        'questions': len(selected), 'category_counts': dict(counts), 'unsupported_selection': unsupported,
        'selection_sha256': manifest['selected_qa_sha256'], 'metadata_only_selection': True,
        'excluded_original_sha256_count': len(hashes)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-name', default=DEST.name)
    parser.add_argument('--selection-name', default='SELECTED_OFFICIAL_QA_NEW_DOCUMENTS.json')
    parser.add_argument('--per-category', type=int, help='Strict metadata-only SHA-excluded category holdout; new manifest required')
    parser.add_argument('--max-pdf-bytes', type=int, default=2_000_000)
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
    if args.per_category is not None:
        if args.per_category <= 0 or args.max_pdf_bytes <= 0:
            parser.error('Strict category and PDF limits must be positive')
        return freeze_metadata_categories(args, destination, base, qa_raw, rows)
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
