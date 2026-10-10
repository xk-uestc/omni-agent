"""Isolated C experiment using the unchanged frozen layered scorer."""
import threading
import evaluate_foundation_rag as frozen
from foundation_common import sha, ROOT
from prototypes.foundation_source_navigation import navigate_sources
from backend.knowledge_store import KnowledgeStore


class ExperimentalStore(KnowledgeStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scope = threading.local()

    def records(self, document_id=None):
        rows = super().records(document_id)
        ids = getattr(self._scope, 'ids', None)
        return [r for r in rows if r.metadata['document_id'] in ids] if ids is not None else rows

    def search(self, query, *, strategy='flat', **kwargs):
        if strategy == 'flat': return super().search(query, **kwargs)
        if strategy != 'hierarchical': raise ValueError('unsupported prototype strategy')
        ids, audit = navigate_sources(query, super().records(kwargs.get('document_id')))
        self._scope.ids = ids
        try:
            result = super().search(query, **kwargs)
        finally:
            del self._scope.ids
        if kwargs.get('with_audit'):
            result[1]['source_navigation'] = audit
        return result


original_dump = frozen.dump

def dump(path, report):
    report['prototype_only_not_production'] = True
    report['prototype_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in
        [ROOT/'tools/evaluate_foundation_navigation.py', ROOT/'tools/prototypes/foundation_source_navigation.py']}
    original_dump(path, report)


if __name__ == '__main__':
    frozen.KnowledgeStore = ExperimentalStore
    frozen.dump = dump
    frozen.main()
