import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('freeze_holdout', Path(__file__).resolve().parents[2] / 'tools/freeze_ohr_holdout.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(identifier, question, category='text', document='one'):
    return {'ID': identifier, 'questions': question, 'answers': 'scoring-only', 'doc_name': document,
            'evidence_source': category, 'evidence_page_no': 0}


def test_stable_selection_excludes_old_ids_duplicates_and_missing_categories():
    old = [row('old', 'old-question')]
    candidates = [row('old', 'old-question'), row('a', 'old-question'), row('d', 'new-two'),
                  row('b', 'new-one'), row('c', 'new-one'), row('x', 'elsewhere', document='other'),
                  row('t', 'one-table', 'table')]
    chosen, available = module.select_holdout(candidates, old, {'one'})
    assert [item['ID'] for item in chosen] == ['b', 'd', 't']
    assert module.select_holdout(list(reversed(candidates)), old, {'one'})[0] == chosen
    assert available['formula'] == 0
    altered = [{**item, 'answers': 'DIFFERENT-GOLD'} for item in candidates]
    assert [item['ID'] for item in module.select_holdout(altered, old, {'one'})[0]] == ['b', 'd', 't']


@pytest.fixture
def source(tmp_path):
    old, all_rows = [row('old', 'q0')], [row('old', 'q0'), row('new', 'q1')]
    raw = json.dumps(all_rows).encode()
    selected = json.dumps(old).encode()
    (tmp_path / 'qas_v2.json').write_bytes(raw)
    (tmp_path / 'SELECTED_OFFICIAL_QA.json').write_bytes(selected)
    (tmp_path / 'one.pdf').write_bytes(b'original-pdf')
    (tmp_path / 'one.json').write_bytes(b'original-gt')
    manifest = {'data_root_default': str(tmp_path), 'qa_sha256': module.digest(raw),
        'selected_qa_path': 'SELECTED_OFFICIAL_QA.json', 'selected_qa_sha256': module.digest(selected),
        'hf_revision': 'hf-pinned', 'official_code_revision': 'code-pinned',
        'cases': [{'ID': 'old', 'original_row_sha256': module.digest(json.dumps(old[0], ensure_ascii=False, sort_keys=True).encode())}],
        'documents': [{'doc_name': 'one', 'pdf_path': 'one.pdf', 'pdf_sha256': module.digest(b'original-pdf'),
                       'gt_path': 'one.json', 'gt_sha256': module.digest(b'original-gt')}]}
    path = tmp_path / 'MANIFEST.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def test_freeze_preserves_parent_assets_rows_and_refuses_overwrite(source):
    before = source.read_bytes()
    result = module.freeze(source)
    assert source.read_bytes() == before
    assert result['hf_revision'] == 'hf-pinned'
    assert result['official_code_revision'] == 'code-pinned'
    assert result['documents'] == json.loads(before)['documents']
    selected = json.loads((source.parent / result['selected_qa_path']).read_bytes())
    assert selected == [row('new', 'q1')]
    assert result['unsupported_selection']['text']['selected'] == 1
    with pytest.raises(FileExistsError):
        module.freeze(source)


@pytest.mark.parametrize('filename', ['qas_v2.json', 'SELECTED_OFFICIAL_QA.json', 'one.pdf', 'one.json'])
def test_hash_tampering_prevents_any_output(source, filename):
    (source.parent / filename).write_bytes(b'tampered')
    with pytest.raises(ValueError):
        module.freeze(source)
    assert not (source.parent / 'MANIFEST_HOLDOUT.json').exists()
    assert not (source.parent / 'SELECTED_OFFICIAL_QA_HOLDOUT.json').exists()


def test_cannot_replace_parent_manifest(source):
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        module.freeze(source, output_manifest=source)
    assert source.read_bytes() == before


def test_multiple_prior_manifests_exclude_ids_and_exact_duplicate_questions(source):
    original = json.loads(source.read_text())
    candidates = [row('old', 'q0'), row('new', 'q1'), row('dup', 'q1'), row('fresh', 'q2')]
    raw = json.dumps(candidates).encode()
    (source.parent / 'qas_v2.json').write_bytes(raw)
    original['qa_sha256'] = module.digest(raw)
    source.write_text(json.dumps(original))
    first = module.freeze(source)
    # First selects fresh/new by ID, leaving no unknown nonduplicate question.
    second = module.freeze(source, output_manifest=source.parent / 'SECOND.json',
        selection_name='SECOND_QA.json', exclude_manifests=[source.parent / 'MANIFEST_HOLDOUT.json'])
    assert second['cases'] == []
    assert second['excluded_manifests'][0]['case_ids'] == [item['ID'] for item in first['cases']]
    assert (source.parent / 'MANIFEST_HOLDOUT.json').exists()


@pytest.mark.parametrize('mutation', ['row_hash', 'selection_hash', 'asset_hash', 'revision'])
def test_damaged_prior_manifest_prevents_second_freeze(source, mutation):
    module.freeze(source)
    prior_path = source.parent / 'MANIFEST_HOLDOUT.json'
    prior = json.loads(prior_path.read_text())
    if mutation == 'row_hash':
        prior['cases'][0]['original_row_sha256'] = 'wrong'
    elif mutation == 'selection_hash':
        prior['selected_qa_sha256'] = 'wrong'
    elif mutation == 'asset_hash':
        prior['documents'][0]['pdf_sha256'] = 'wrong'
    else:
        prior['hf_revision'] = 'other'
    prior_path.write_text(json.dumps(prior))
    with pytest.raises(ValueError):
        module.freeze(source, output_manifest=source.parent / 'SECOND.json',
            selection_name='SECOND_QA.json', exclude_manifests=[prior_path])
    assert not (source.parent / 'SECOND_QA.json').exists()
