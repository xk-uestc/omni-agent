import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[2] / 'tools/verify_checkpoint.py'
spec = importlib.util.spec_from_file_location('checkpoint_sample_verifier', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def samples():
    return [{'document_id': str(i), 'sha256': format(i, '064x'), 'modality': 'pdf'} for i in range(16)]


def test_new_sample_count_is_verified_against_shipped_manifest():
    expected = samples()
    assert module.sample_documents_match(list(reversed(expected)), {'files': expected})


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate', 'sha256', 'modality'])
def test_matching_count_cannot_hide_missing_or_stale_sample(change):
    expected = samples()
    actual = deepcopy(expected)
    if change == 'missing':
        actual.pop()
    elif change == 'extra':
        actual.append({'document_id': 'extra', 'sha256': 'a' * 64, 'modality': 'pdf'})
    elif change == 'duplicate':
        actual[-1] = deepcopy(actual[0])
    else:
        actual[0][change] = 'changed'
    assert not module.sample_documents_match(actual, {'files': expected})
