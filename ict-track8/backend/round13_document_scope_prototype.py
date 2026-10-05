"""Round 13 prototype for provenance-bound, explicitly scoped PDF table scans.

This module is intentionally not connected to production answer routing. It
proves only that the selected native strict-grid tables were fully extracted
within the declared pages and budgets. It does not prove document-wide,
OCR, unframed-table, chart, or cross-page record completeness.
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Mapping, Sequence

import fitz

from .visual_tables import VisualTableError, extract_pdf_tables


MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 1000
MAX_PAGE_SCANS = 1000
MAX_TOTAL_CELLS = 4000
MAX_SECONDS = 60.0


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _block_inventory(raw: bytes, page_no: int, source_sha256: str) -> dict[int, dict[str, Any]]:
    """Return native text blocks keyed by PyMuPDF's page-local block number."""
    with fitz.open(stream=raw, filetype="pdf") as document:
        page = document[page_no - 1]
        page.set_rotation(0)
        result = {}
        for item in page.get_text("blocks", sort=False):
            if len(item) < 7 or int(item[6]) != 0:
                continue
            block_no = int(item[5])
            text = str(item[4])
            result[block_no] = {
                "block_id": f"page:{page_no}:fitz-block:{block_no}",
                "text_sha256": _sha(text.encode("utf-8")),
                "source_sha256": source_sha256,
                "bbox_fitz_unrotated_pt": [float(value) for value in item[:4]],
                "text_chars": len(text),
                "source": "pymupdf_native_text_block",
            }
        return result


def _cell_source(cell: Mapping[str, Any], *, page_no: int, table_id: str,
                 word_map: Mapping[str, Mapping[str, Any]],
                 block_map: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    word_ids = cell.get("word_ids")
    if not isinstance(word_ids, list) or any(word_id not in word_map for word_id in word_ids):
        raise ValueError("native_grid_word_provenance_missing")
    block_nos = sorted({int(word_map[word_id]["block_no"]) for word_id in word_ids})
    if any(block_no not in block_map for block_no in block_nos):
        raise ValueError("native_grid_block_provenance_missing")
    raw_text = str(cell.get("raw_text", ""))
    return {
        "cell_id": cell["cell_id"],
        "page_no": page_no,
        "table_id": table_id,
        "row_index": int(cell["row_index"]),
        "column_index": int(cell["column_index"]),
        "raw_text": raw_text,
        "raw_text_sha256": _sha(raw_text.encode("utf-8")),
        "bbox_display_pt": list(cell["bbox_display_pt"]),
        "native_word_ids": list(word_ids),
        "native_block_refs": [dict(block_map[number]) for number in block_nos],
        "source_sha256": None,
    }


def _table_scope(table: Mapping[str, Any], *, page_no: int, source_sha256: str,
                 word_map: Mapping[str, Mapping[str, Any]],
                 block_map: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    table_id = table["table_id"]
    cells = table.get("cells")
    if (table.get("status") != "layout_verified" or not isinstance(cells, list)
            or len(cells) != int(table.get("row_count", -1))
            or any(not isinstance(row, list) or len(row) != int(table.get("column_count", -1))
                   for row in cells)):
        raise ValueError("native_grid_table_shape_incomplete")
    rows = []
    for row_index, row in enumerate(cells):
        scoped_cells = [_cell_source(cell, page_no=page_no, table_id=table_id,
                                     word_map=word_map, block_map=block_map)
                        for cell in row]
        for cell in scoped_cells:
            cell["source_sha256"] = source_sha256
        rows.append({
            "row_index": row_index,
            "row_header": str(row[0].get("raw_text", "")),
            "row_header_cell_id": row[0]["cell_id"],
            "cells": scoped_cells,
        })
    columns = []
    for column_index, header_path in enumerate(table["column_header_paths"]):
        header_cell = cells[0][column_index]
        source = _cell_source(header_cell, page_no=page_no, table_id=table_id,
                              word_map=word_map, block_map=block_map)
        source["source_sha256"] = source_sha256
        columns.append({
            "column_index": column_index,
            "header_path": list(header_path),
            "header_cell_id": header_cell["cell_id"],
            "header_source": source,
        })
    return {
        "table_id": table_id,
        "page_no": page_no,
        "source_sha256": source_sha256,
        "status": "layout_verified",
        "bbox_display_pt": list(table["bbox_display_pt"]),
        "row_count": len(rows),
        "column_count": len(columns),
        "rows": rows,
        "columns": columns,
        "scope_semantics": "one_page_native_strict_grid_table_only",
    }


def _overlap(left: Sequence[float], right: Sequence[float]) -> bool:
    return (left[0] < right[2] and right[0] < left[2]
            and left[1] < right[3] and right[1] < left[3])


def build_declared_native_grid_scope(
    pdf_bytes: bytes,
    *,
    page_nos: Sequence[int],
    selected_table_ids_by_page: Mapping[int, Sequence[str]] | None = None,
    max_page_scans: int = MAX_PAGE_SCANS,
    max_total_cells: int = MAX_TOTAL_CELLS,
    max_seconds: float = MAX_SECONDS,
) -> dict[str, Any]:
    """Scan declared PDF pages and build a source-hash-bound table manifest.

    If ``selected_table_ids_by_page`` is absent, scope means every accepted
    strict native grid found on the declared pages; any rejected candidate
    makes the result incomplete. If supplied, scope means exactly those IDs,
    which must be rediscovered from the current source bytes. Neither mode
    asserts that those tables contain every matching record in the document.
    """
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes or len(pdf_bytes) > MAX_SOURCE_BYTES:
        raise ValueError("document_scope_source_budget_invalid")
    if (not isinstance(page_nos, Sequence) or isinstance(page_nos, (str, bytes))
            or not page_nos or any(type(page) is not int or page < 1 for page in page_nos)
            or len(set(page_nos)) != len(page_nos)):
        raise ValueError("document_scope_pages_invalid")
    if (type(max_page_scans) is not int or not 1 <= max_page_scans <= MAX_PAGE_SCANS
            or type(max_total_cells) is not int or not 1 <= max_total_cells <= MAX_TOTAL_CELLS
            or type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("document_scope_budget_invalid")
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            if document.needs_pass or not 1 <= len(document) <= MAX_PAGES:
                raise ValueError("document_scope_pdf_bounds_invalid")
            page_count = len(document)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("document_scope_pdf_unreadable") from exc

    declared_pages = list(page_nos)
    if any(page > page_count for page in declared_pages):
        raise ValueError("document_scope_page_out_of_bounds")
    expected = None
    if selected_table_ids_by_page is not None:
        if not isinstance(selected_table_ids_by_page, Mapping):
            raise ValueError("document_scope_table_selection_invalid")
        expected = {}
        for page, ids in selected_table_ids_by_page.items():
            if (type(page) is not int or page not in declared_pages
                    or not isinstance(ids, Sequence) or isinstance(ids, (str, bytes))
                    or not ids or any(not isinstance(item, str) or not item for item in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError("document_scope_table_selection_invalid")
            expected[page] = set(ids)

    source_sha256 = _sha(pdf_bytes)
    started = time.monotonic()
    pages, tables, selected_seen = [], [], {}
    total_cells = 0
    budget_exhausted = False
    incomplete_reason = None

    for page_no in declared_pages[:max_page_scans]:
        remaining_cells = max_total_cells - total_cells
        if remaining_cells <= 0 or time.monotonic() - started >= max_seconds:
            budget_exhausted = True
            incomplete_reason = "scan_budget_exhausted"
            break
        try:
            extraction = extract_pdf_tables(
                pdf_bytes,
                page_no=page_no,
                expected_source_sha256=source_sha256,
                max_cells=min(remaining_cells, 4000),
            )
            if extraction.get("source_sha256") != source_sha256 or extraction.get("page_no") != page_no:
                raise ValueError("native_grid_source_page_mismatch")
            block_map = _block_inventory(pdf_bytes, page_no, source_sha256)
            word_map = {word["word_id"]: word for word in extraction["native_words"]}
            observed_ids = {table["table_id"] for table in extraction["tables"]}
            selected = expected.get(page_no, set()) if expected is not None else observed_ids
            if expected is not None and selected and not selected.issubset(observed_ids):
                incomplete_reason = "selected_native_grid_table_not_found"
            unresolved = extraction.get("rejected_tables", [])
            if expected is None and unresolved:
                incomplete_reason = "native_grid_candidate_rejected"
            selected_tables = [table for table in extraction["tables"] if table["table_id"] in selected]
            if expected is not None and any(
                _overlap(table["bbox_display_pt"], rejected["bbox_display_pt"])
                for table in selected_tables for rejected in unresolved
                if isinstance(rejected.get("bbox_display_pt"), list)
            ):
                incomplete_reason = "native_grid_candidate_rejected"
            page_table_scopes = []
            for table in selected_tables:
                cell_count = int(table["row_count"]) * int(table["column_count"])
                total_cells += cell_count
                page_table_scopes.append(_table_scope(table, page_no=page_no,
                    source_sha256=source_sha256, word_map=word_map, block_map=block_map))
            selected_seen[page_no] = observed_ids.intersection(selected)
            tables.extend(page_table_scopes)
            pages.append({
                "page_no": page_no,
                "source_sha256": source_sha256,
                "page_count": page_count,
                "native_word_count": len(extraction["native_words"]),
                "native_block_count": len(block_map),
                "accepted_table_ids": sorted(observed_ids),
                "rejected_table_count": len(unresolved),
                "selected_table_ids": sorted(observed_ids.intersection(selected)),
                "tables": page_table_scopes,
                "scan_status": "page_extraction_completed",
                "coverage_note": "strict_native_grid_only_not_OCR_or_all_page_content",
            })
            if time.monotonic() - started > max_seconds:
                budget_exhausted = True
                incomplete_reason = "scan_budget_exhausted"
                break
        except VisualTableError as exc:
            message = str(exc)
            budget_exhausted = "budget" in message.lower() or "exceeded" in message.lower()
            incomplete_reason = "native_grid_extraction_budget_or_parser_failure"
            break
        except (KeyError, TypeError, ValueError) as exc:
            incomplete_reason = str(exc) or "native_grid_provenance_invalid"
            break

    omitted_pages = [page for page in declared_pages if page not in {item["page_no"] for item in pages}]
    if len(declared_pages) > max_page_scans:
        budget_exhausted = True
        incomplete_reason = "scan_budget_exhausted"
    if expected is not None:
        for page, ids in expected.items():
            found = selected_seen.get(page, set())
            if ids - found:
                incomplete_reason = incomplete_reason or "selected_native_grid_table_not_found"
    if not tables and incomplete_reason is None:
        incomplete_reason = "no_declared_native_grid_table_found"
    complete = (not omitted_pages and not budget_exhausted and incomplete_reason is None
                and len(pages) == len(declared_pages))
    return {
        "schema_version": "round13-declared-native-grid-scope-v1",
        "source_sha256": source_sha256,
        "source_bytes": len(pdf_bytes),
        "source_page_count": page_count,
        "extractor": "backend.visual_tables.extract_pdf_tables",
        "declared_scope": {
            "kind": "selected_native_strict_grid_tables_on_declared_pages",
            "page_nos": declared_pages,
            "selected_table_ids_by_page": {
                str(page): sorted(ids) for page, ids in sorted(expected.items())
            } if expected is not None else None,
            "selection_rule": "explicit_source_derived_table_ids" if expected is not None
                             else "all_accepted_strict_grid_tables_on_declared_pages",
        },
        "coverage_status": "complete_for_declared_native_grid_tables" if complete else "incomplete",
        "incomplete_reason": incomplete_reason,
        "budget": {
            "max_page_scans": max_page_scans,
            "declared_page_count": len(declared_pages),
            "scanned_page_count": len(pages),
            "max_total_cells": max_total_cells,
            "scanned_cells": total_cells,
            "max_seconds": max_seconds,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "exhausted": budget_exhausted,
        },
        "scanned_pages": pages,
        "omitted_page_nos": omitted_pages,
        "tables": tables,
        "limitations": [
            "completeness_applies_only_to_the_exact_declared_page_and_table_ids",
            "does_not_claim_all_matching_records_in_the_whole_document_or_corpus",
            "native_text_layer_and_strict_line_grid_only_no_OCR_or_unframed_tables",
            "does_not_join_tables_across_pages_or_certify_record_semantics",
        ],
        "model_output_used_as_evidence": False,
    }
