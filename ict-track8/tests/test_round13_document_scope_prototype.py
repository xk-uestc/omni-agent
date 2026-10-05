"""Offline scope-manifest prototype tests; no model calls or corpus gold data."""
import hashlib

import fitz

from backend.round13_document_scope_prototype import build_declared_native_grid_scope
from backend.visual_tables import extract_pdf_tables


GRID = [
    ["Area", "2025 Actual", "2026 Forecast"],
    ["Alpha", "125", "250"],
    ["Beta", "125", "300"],
    ["Gamma", "80", "160"],
]


def _draw_grid(page, x=70, y=70, *, border=True):
    for column in range(4):
        if border:
            page.draw_line((x + column * 100, y), (x + column * 100, y + len(GRID) * 35))
    for row in range(len(GRID) + 1):
        if border:
            page.draw_line((x, y + row * 35), (x + 300, y + row * 35))
    for row_index, row in enumerate(GRID):
        for column_index, text in enumerate(row):
            page.insert_text((x + 5 + column_index * 100, y + 20 + row_index * 35), text, fontsize=9)


def _pdf(*, second_page=False, border=True):
    with fitz.open() as document:
        page = document.new_page(width=420, height=320)
        _draw_grid(page, border=border)
        if second_page:
            document.new_page(width=420, height=320).insert_text((70, 80), "Continuation page", fontsize=10)
        return document.tobytes()


def _table_id(raw, page_no=1):
    digest = hashlib.sha256(raw).hexdigest()
    extraction = extract_pdf_tables(raw, page_no=page_no, expected_source_sha256=digest)
    assert extraction["status"] == "layout_verified", extraction
    assert len(extraction["tables"]) == 1, extraction
    return extraction["tables"][0]["table_id"]


def test_manifest_binds_grid_rows_columns_and_blocks_to_actual_pdf_bytes():
    raw = _pdf()
    digest = hashlib.sha256(raw).hexdigest()
    table_id = _table_id(raw)

    manifest = build_declared_native_grid_scope(
        raw, page_nos=[1], selected_table_ids_by_page={1: [table_id]}
    )

    assert manifest["source_sha256"] == digest
    assert manifest["coverage_status"] == "complete_for_declared_native_grid_tables"
    assert manifest["declared_scope"]["page_nos"] == [1]
    assert len(manifest["tables"]) == 1
    table = manifest["tables"][0]
    assert table["source_sha256"] == digest
    assert table["row_count"] == 4 and table["column_count"] == 3
    assert table["rows"][2]["row_header"] == "Beta"
    assert table["rows"][2]["cells"][1]["raw_text"] == "125"
    assert table["rows"][2]["cells"][1]["row_index"] == 2
    assert table["rows"][2]["cells"][1]["column_index"] == 1
    assert table["rows"][2]["cells"][1]["native_word_ids"]
    assert table["rows"][2]["cells"][1]["native_block_refs"]
    assert table["columns"][1]["header_path"] == ["2025 Actual"]
    assert table["columns"][1]["header_source"]["source_sha256"] == digest
    assert manifest["model_output_used_as_evidence"] is False
    assert "does_not_claim_all_matching_records_in_the_whole_document_or_corpus" in manifest["limitations"]


def test_budget_omission_keeps_multi_page_declared_scope_incomplete():
    raw = _pdf(second_page=True)
    table_id = _table_id(raw)

    manifest = build_declared_native_grid_scope(
        raw, page_nos=[1, 2], selected_table_ids_by_page={1: [table_id]}, max_page_scans=1
    )

    assert manifest["coverage_status"] == "incomplete"
    assert manifest["incomplete_reason"] == "scan_budget_exhausted"
    assert manifest["budget"]["exhausted"] is True
    assert manifest["omitted_page_nos"] == [2]


def test_table_id_from_another_source_cannot_be_rebound_to_this_pdf():
    first = _pdf()
    second = _pdf(second_page=True)
    table_id = _table_id(first)

    manifest = build_declared_native_grid_scope(
        second, page_nos=[1], selected_table_ids_by_page={1: [table_id]}
    )

    assert manifest["source_sha256"] == hashlib.sha256(second).hexdigest()
    assert manifest["coverage_status"] == "incomplete"
    assert manifest["incomplete_reason"] == "selected_native_grid_table_not_found"
    assert manifest["tables"] == []


def test_no_strict_grid_does_not_turn_unframed_text_into_a_complete_table():
    raw = _pdf(border=False)

    manifest = build_declared_native_grid_scope(raw, page_nos=[1])

    assert manifest["coverage_status"] == "incomplete"
    assert manifest["incomplete_reason"] == "no_declared_native_grid_table_found"
    assert manifest["tables"] == []
