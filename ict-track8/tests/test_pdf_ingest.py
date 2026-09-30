from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter

from backend.pdf_ingest import PdfIngestor


def blank_pdf(page_count=2):
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_scanned_pdf_pages_are_explicitly_marked_for_ocr():
    result = PdfIngestor().analyze(blank_pdf(), document_id="scan.pdf")
    assert result["document_id"] == "scan.pdf"
    assert result["pdf_metrics"]["page_count"] == 2
    assert result["pdf_metrics"]["ocr_required_pages"] == [1, 2]
    assert result["issues"][0]["code"] == "no_text"
    assert result["ocr_retry_plan"]["status"] == "required"
    assert [page["page_no"] for page in result["ocr_retry_plan"]["pages"] if page["required"]] == [1, 2]


def test_invalid_pdf_is_rejected():
    try:
        PdfIngestor().analyze(b"not-a-pdf")
    except ValueError as exc:
        assert "无法解析" in str(exc)
    else:
        raise AssertionError("invalid PDF was accepted")
