"""PDF 到文档分析页面信号的最小可复现入口。"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from .document_analysis import DocumentAnalyzer, PageSignal


class PdfIngestor:
    def __init__(self, analyzer: DocumentAnalyzer | None = None):
        self.analyzer = analyzer or DocumentAnalyzer()

    def analyze(self, pdf_bytes: bytes, *, document_id: str = "document.pdf") -> dict[str, Any]:
        if not pdf_bytes:
            raise ValueError("PDF 不能为空")
        try:
            reader = PdfReader(BytesIO(pdf_bytes), strict=False)
        except Exception as exc:
            raise ValueError("PDF 无法解析") from exc
        if getattr(reader, "is_encrypted", False):
            raise ValueError("PDF 已加密，无法解析")
        max_pages = int(os.getenv("ICT8_PDF_MAX_PAGES", "1000") or 1000)
        try:
            page_count = len(reader.pages)
        except Exception as exc:
            raise ValueError("PDF 无法解析") from exc
        if page_count > max_pages:
            raise ValueError(f"PDF 页数 {page_count} 超过上限 {max_pages}")
        min_chars = int(os.getenv("ICT8_PDF_MIN_TEXT_CHARS", "20") or 20)
        pages: list[PageSignal] = []
        ocr_required: list[int] = []
        low_text: list[int] = []
        failed: list[int] = []
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception:
                # 单页损坏不应让整份文档失败；该页转入 OCR 计划
                text = ""
                failed.append(page_no)
            content_chars = sum(1 for ch in text if not ch.isspace())
            # 只有页码/页眉的扫描页也有少量文字层，按阈值判定为需要 OCR
            scanned = content_chars < min_chars
            if scanned:
                ocr_required.append(page_no)
                if content_chars:
                    low_text.append(page_no)
            pages.append(PageSignal(page_no=page_no, text=text, scanned=scanned))
        result = self.analyzer.analyze(document_id=document_id, pages=pages)
        payload = result.to_dict()
        payload["pdf_metrics"] = {
            "page_count": len(pages),
            "ocr_required_pages": ocr_required,
            "text_extracted_page_count": len(pages) - len(ocr_required),
            "low_text_layer_pages": low_text,
            "extract_failed_pages": failed,
            "min_text_chars_per_page": min_chars,
        }
        return payload
