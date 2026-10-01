"""Metadata-only selection must reject original-byte aliases before freezing."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
spec = importlib.util.spec_from_file_location('strict_newdoc_freeze', ROOT/'tools/freeze_new_document_ohr.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def metadata(identifier, document, category):
    # No question, answer, evidence text or GT exists in selection input.
    return {'ID': identifier, 'doc_name': document, 'evidence_source': category}


def test_category_selection_is_stable_and_needs_only_metadata():
    rows = [metadata(f'{c}-z', f'{c}-larger', c) for c in module.CATEGORIES]
    rows += [metadata(f'{c}-a', f'{c}-smaller', c) for c in module.CATEGORIES]
    members = {r['doc_name']: SimpleNamespace(file_size=100 if r['ID'].endswith('-a') else 200)
               for r in rows}
    call = lambda values: module.pick_category_metadata(values, members, set(), set(), set(),
                                                        lambda name: 'hash-'+name, per_category=1)
    chosen, docs, rejected = call(rows)
    assert chosen == call(list(reversed(rows)))[0]
    assert len(chosen) == len(docs) == 6 and not rejected
    assert all(r['ID'].endswith('-a') for r in chosen)
    assert {r['evidence_source'] for r in chosen} == set(module.CATEGORIES)


def test_original_sha_excludes_old_alias_and_selected_alias():
    rows = [metadata('t1', 'old-alias', 'text'), metadata('t2', 'fresh', 'text'),
            metadata('b1', 'fresh-alias', 'table'), metadata('b2', 'other', 'table')]
    members = {r['doc_name']: SimpleNamespace(file_size=100+i) for i, r in enumerate(rows)}
    hashes = {'old-alias': 'exposed', 'fresh': 'new', 'fresh-alias': 'new', 'other': 'other'}
    chosen, docs, rejected = module.pick_category_metadata(rows, members, set(), set(), {'exposed'},
                                                           hashes.__getitem__, per_category=1)
    assert [r['ID'] for r in chosen] == ['t2', 'b2']
    assert docs == ['fresh', 'other']
    assert rejected == {'old-alias': 'original_sha256_previously_exposed',
                        'fresh-alias': 'original_sha256_alias_of_selected_document'}


def test_resource_shortage_is_retained_without_cross_category_backfill():
    rows = [metadata('old', 'one', 'text'), metadata('new', 'one', 'text'),
            metadata('oversize', 'big', 'table')]
    members = {'one': SimpleNamespace(file_size=100), 'big': SimpleNamespace(file_size=201)}
    chosen, docs, rejected = module.pick_category_metadata(rows, members, set(), {'old'}, set(),
        lambda name: name, per_category=6, max_pdf_bytes=200)
    assert [r['ID'] for r in chosen] == ['new']
    assert docs == ['one'] and not rejected
