"""Freeze unseen official QA using IDs/categories only; answers are scoring-only."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'benchmarks/ohr_bench/MANIFEST.json'
CATEGORIES = ('text', 'table', 'formula', 'chart', 'reading_order', 'multi')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('asset_outside_data_root')
    return path


def select_holdout(rows, original_rows, doc_names):
    """Deterministic category strata; no answer/evidence content inspected."""
    excluded_ids = {row['ID'] for row in original_rows}
    seen_questions = {json.dumps(row['questions'], ensure_ascii=False, sort_keys=True) for row in original_rows}
    if len({row['ID'] for row in rows}) != len(rows):
        raise ValueError('duplicate_official_id')
    selected, available = [], {}
    for category in CATEGORIES:
        eligible = sorted((row for row in rows if row['doc_name'] in doc_names
                           and row['ID'] not in excluded_ids and row['evidence_source'] == category),
                          key=lambda row: row['ID'])
        available[category] = len(eligible)
        picked = 0
        for row in eligible:
            question = json.dumps(row['questions'], ensure_ascii=False, sort_keys=True)
            if question in seen_questions:
                continue
            selected.append(row)
            seen_questions.add(question)
            picked += 1
            if picked == 2:
                break
    return selected, available


def _validated_selection(manifest, root):
    raw = checked(root, manifest['selected_qa_path']).read_bytes()
    if digest(raw) != manifest['selected_qa_sha256']:
        raise ValueError('excluded_selection_sha_mismatch')
    rows = json.loads(raw)
    expected = {row['ID']: row['original_row_sha256'] for row in manifest['cases']}
    if len(expected) != len(manifest['cases']) or len(rows) != len(expected) or {row['ID'] for row in rows} != set(expected):
        raise ValueError('excluded_case_ids_mismatch')
    for row in rows:
        if digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()) != expected[row['ID']]:
            raise ValueError('excluded_row_sha_mismatch')
    for asset in manifest['documents']:
        for key, hash_key in [('pdf_path', 'pdf_sha256'), ('gt_path', 'gt_sha256')]:
            if digest(checked(root, asset[key]).read_bytes()) != asset[hash_key]:
                raise ValueError('official_asset_sha_mismatch')
    return rows


def freeze(manifest_path=DEFAULT_MANIFEST, *, output_manifest=None, data_root=None,
           exclude_manifests=(), selection_name='SELECTED_OFFICIAL_QA_HOLDOUT.json'):
    manifest_path = Path(manifest_path)
    original_bytes = manifest_path.read_bytes()
    original = json.loads(original_bytes)
    root = Path(data_root or original['data_root_default']).resolve()
    output = Path(output_manifest or manifest_path.with_name('MANIFEST_HOLDOUT.json')).resolve()
    if Path(selection_name).name != selection_name or not selection_name.endswith('.json'):
        raise ValueError('selection_name_must_be_json_basename')
    selection_path = checked(root, selection_name)
    if output == manifest_path.resolve() or output.exists() or selection_path.exists():
        raise FileExistsError('holdout_outputs_must_be_new_no_overwrite')
    qa_raw = checked(root, 'qas_v2.json').read_bytes()
    if digest(qa_raw) != original['qa_sha256']:
        raise ValueError('official_qa_sha_mismatch')
    old_raw = checked(root, original['selected_qa_path']).read_bytes()
    if digest(old_raw) != original['selected_qa_sha256']:
        raise ValueError('original_selection_sha_mismatch')
    old_rows = json.loads(old_raw)
    if {row['ID'] for row in old_rows} != {row['ID'] for row in original['cases']}:
        raise ValueError('original_case_ids_mismatch')
    expected = {row['ID']: row['original_row_sha256'] for row in original['cases']}
    for row in old_rows:
        if digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()) != expected[row['ID']]:
            raise ValueError('original_row_sha_mismatch')
    for asset in original['documents']:
        for key, hash_key in [('pdf_path', 'pdf_sha256'), ('gt_path', 'gt_sha256')]:
            if digest(checked(root, asset[key]).read_bytes()) != asset[hash_key]:
                raise ValueError('official_asset_sha_mismatch')
    exclusions = []
    for excluded_path in exclude_manifests:
        excluded_raw = Path(excluded_path).read_bytes()
        excluded = json.loads(excluded_raw)
        if any(excluded.get(key) != original.get(key) for key in ('hf_revision', 'official_code_revision', 'qa_sha256')):
            raise ValueError('excluded_dataset_revision_mismatch')
        prior = _validated_selection(excluded, root)
        old_rows.extend(prior)
        exclusions.append({'manifest': str(Path(excluded_path).resolve()), 'manifest_sha256': digest(excluded_raw),
                           'selected_qa_sha256': excluded['selected_qa_sha256'], 'case_ids': [row['ID'] for row in prior]})
    official_rows = json.loads(qa_raw)
    official_hashes = {row['ID']: digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()) for row in official_rows}
    for row in old_rows:
        if digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()) != official_hashes.get(row['ID']):
            raise ValueError('excluded_row_not_original_official_qa')
    rows, available = select_holdout(official_rows, old_rows, {item['doc_name'] for item in original['documents']})
    used = {row['doc_name'] for row in rows}
    manifest = deepcopy(original)
    selection_raw = (json.dumps(rows, ensure_ascii=False, indent=2) + '\n').encode()
    counts = Counter(row['evidence_source'] for row in rows)
    manifest.update(created_at=datetime.now(timezone.utc).isoformat(),
        selection_rule='Previously downloaded original PDFs only; exclude original IDs and exact duplicate questions; stable ID order, up to 2 per fixed evidence category; no answers/evidence used for selection.',
        selection_limit='Independent unseen-question resource-biased holdout <=12; same PDFs as pilot, not unseen-document or full official score.',
        selected_qa_path=selection_path.name, selected_qa_sha256=digest(selection_raw),
        selection_frozen_before_evaluation=True, parent_manifest_sha256=digest(original_bytes),
        parent_case_ids=[row['ID'] for row in original['cases']],
        excluded_manifests=exclusions,
        category_counts={key: counts[key] for key in CATEGORIES}, category_eligible_counts=available,
        unsupported_selection={key: {'requested': 2, 'selected': counts[key]} for key in CATEGORIES if counts[key] < 2},
        documents=[item for item in original['documents'] if item['doc_name'] in used],
        cases=[{key: row[key] for key in ('ID', 'doc_name', 'evidence_source', 'evidence_page_no')} |
               {'original_row_sha256': digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode())} for row in rows])
    selection_path.write_bytes(selection_raw)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--output-manifest', type=Path)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--exclude-manifest', type=Path, action='append', default=[])
    parser.add_argument('--selection-name', default='SELECTED_OFFICIAL_QA_HOLDOUT.json')
    args = parser.parse_args()
    result = freeze(args.manifest, output_manifest=args.output_manifest, data_root=args.data_root,
                    exclude_manifests=args.exclude_manifest, selection_name=args.selection_name)
    print(json.dumps({'selected': len(result['cases']), 'category_counts': result['category_counts'],
                      'documents': len(result['documents']), 'selection_sha256': result['selected_qa_sha256']}, ensure_ascii=False))
