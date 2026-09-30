from __future__ import annotations

from pathlib import Path as _Path

DATA_DIR = _Path(__file__).resolve().parents[1] / "data"

import json


def test_offline_fixture_corpus_has_eleven_document_types():
    with open(DATA_DIR / "knowledge_documents.json", encoding="utf-8") as handle:
        documents = json.load(handle)
    types = {item.get("metadata", {}).get("document_type") for item in documents}
    assert len(documents) >= 11
    assert len(types) >= 11
    assert None not in types
