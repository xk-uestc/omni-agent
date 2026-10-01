import pytest

from backend.cross_source import DocumentRecord, JsonDocumentRetriever


def retriever():
    return JsonDocumentRetriever([
        DocumentRecord(str(i), 'Report', 'Budget expense shipping ' + str(i), 'document://report', {})
        for i in range(35)
    ])


def test_explicit_store_candidate_pool_can_reach_past_twentieth_hit():
    search = retriever()
    public = search.search('budget expense shipping', top_k=100)
    candidates = search.search('budget expense shipping', top_k=100, candidate_limit=100)
    assert len(public) == 20
    assert len(candidates) == 35
    assert [hit.document_id for hit in candidates[:20]] == [hit.document_id for hit in public]
    assert set(hit.document_id for hit in candidates[20:]).isdisjoint(hit.document_id for hit in public)


@pytest.mark.parametrize('limit', [0, 19, 101, True, 20.0])
def test_candidate_pool_remains_bounded(limit):
    with pytest.raises(ValueError):
        retriever().search('budget', top_k=100, candidate_limit=limit)
