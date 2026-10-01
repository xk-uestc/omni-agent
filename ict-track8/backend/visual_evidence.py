"""Original PDF page/crop assets; provenance only, never visual QA or OCR.

The caller supplies verified bytes, not a path or URL. Native words remain
observations from the PDF text layer, not verified table cells or chart facts.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

import fitz


class VisualEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class VisualAsset:
    png_bytes: bytes
    manifest: dict[str, Any]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rect(rect) -> list[float]:
    return [float(value) for value in rect]


def _matrix(matrix) -> list[float]:
    return [float(getattr(matrix, key)) for key in ('a', 'b', 'c', 'd', 'e', 'f')]


def _positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise VisualEvidenceError(f'{name} must be a positive integer')


def render_pdf_evidence(
    pdf_bytes: bytes,
    *,
    page_no: int,
    expected_source_sha256: str | None = None,
    crop_display_pt: list[float] | tuple[float, ...] | None = None,
    render_scale: float = 2.0,
    max_source_bytes: int = 20 * 1024 * 1024,
    max_pages: int = 1000,
    max_pixels: int = 6_000_000,
    max_png_bytes: int = 12 * 1024 * 1024,
    max_native_words: int = 20_000,
) -> VisualAsset:
    """Render exactly one 1-based page or an in-bounds displayed-page crop.

    Display coordinates have top-left origin, points, and PDF Rotate already
    applied. Raster bounds include pixel rounding; use the returned affine
    mapping (not an assumed crop offset) for highlights. No file is written.
    """
    for value, name in ((max_source_bytes, 'max_source_bytes'), (max_pages, 'max_pages'),
                        (max_pixels, 'max_pixels'), (max_png_bytes, 'max_png_bytes'),
                        (max_native_words, 'max_native_words')):
        _positive_integer(value, name)
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes or len(pdf_bytes) > max_source_bytes:
        raise VisualEvidenceError('PDF bytes empty, invalid or over source budget')
    if type(page_no) is not int or page_no < 1:
        raise VisualEvidenceError('page_no must be a 1-based integer')
    if type(render_scale) not in (float, int) or not math.isfinite(render_scale) or not 0.1 <= render_scale <= 4:
        raise VisualEvidenceError('render_scale outside finite 0.1..4 range')
    digest = _sha(pdf_bytes)
    if expected_source_sha256 is not None and expected_source_sha256 != digest:
        raise VisualEvidenceError('source_sha256_mismatch')
    try:
        document = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception as exc:
        raise VisualEvidenceError('PDF cannot be parsed') from exc
    with document:
        if document.needs_pass:
            raise VisualEvidenceError('encrypted PDF not accepted')
        if len(document) > max_pages or page_no > len(document):
            raise VisualEvidenceError('page bounds or page-count budget exceeded')
        page = document[page_no - 1]
        page_rect = page.rect
        if not page_rect.is_valid or page_rect.is_empty or page_rect.is_infinite:
            raise VisualEvidenceError('invalid displayed page rectangle')
        clip = fitz.Rect(page_rect)
        if crop_display_pt is not None:
            if (not isinstance(crop_display_pt, (list, tuple)) or len(crop_display_pt) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in crop_display_pt)):
                raise VisualEvidenceError('crop must be four finite display-point coordinates')
            clip = fitz.Rect(crop_display_pt)
            if not clip.is_valid or clip.is_empty or clip.is_infinite or not page_rect.contains(clip):
                raise VisualEvidenceError('crop outside displayed page bounds')
        scale = float(render_scale)
        raster_bounds = (clip * fitz.Matrix(scale, scale)).irect
        if raster_bounds.width <= 0 or raster_bounds.height <= 0 or raster_bounds.width * raster_bounds.height > max_pixels:
            raise VisualEvidenceError('render pixel budget exceeded before rasterization')
        try:
            words = page.get_text('words', sort=False)
        except Exception as exc:
            raise VisualEvidenceError('native word extraction failed') from exc
        if len(words) > max_native_words:
            raise VisualEvidenceError('native word budget exceeded')
        try:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip,
                                    colorspace=fitz.csRGB, alpha=False, annots=True)
            if pixmap.width * pixmap.height > max_pixels:
                raise VisualEvidenceError('render pixel budget exceeded')
            png = pixmap.tobytes('png')
        except VisualEvidenceError:
            raise
        except Exception as exc:
            raise VisualEvidenceError('PDF rasterization failed') from exc
        if len(png) > max_png_bytes:
            raise VisualEvidenceError('PNG byte budget exceeded')
        # PyMuPDF rounds the raster origin to integral pixels. This affine
        # mapping therefore uses pixmap.x/y, not clip.x0/y0 * scale.
        display_to_asset = fitz.Matrix(scale, 0, 0, scale, -pixmap.x, -pixmap.y)
        asset_to_display = ~display_to_asset
        native = []
        for index, word in enumerate(words):
            unrotated = fitz.Rect(word[:4])
            displayed = unrotated * page.rotation_matrix
            if not displayed.intersects(clip):
                continue
            native.append({
                'word_id': f'page:{page_no}:word:{index}', 'text': str(word[4]),
                'block_no': int(word[5]), 'line_no': int(word[6]), 'word_no': int(word[7]),
                'bbox_fitz_unrotated_pt': _rect(unrotated),
                'bbox_pdf_user_pt': _rect(unrotated * ~page.transformation_matrix),
                'bbox_display_pt': _rect(displayed),
                'bbox_asset_px': _rect(displayed * display_to_asset),
                'fully_contained_in_crop': clip.contains(displayed),
                'provenance': 'native_pdf_text_layer_not_visual_fact_validation',
            })
        renderer = f'PyMuPDF:{fitz.VersionBind}:RGB:alpha0:annots1'
        identity = {'source_sha256': digest, 'page_no': page_no, 'renderer': renderer,
                    'scale': scale, 'crop_display_pt': _rect(clip),
                    'rotation': page.rotation, 'cropbox': _rect(page.cropbox)}
        evidence_id = _sha(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode())
        manifest = {
            'schema_version': 'pdf-visual-asset-v1', 'evidence_id': evidence_id,
            'status': 'rendered_provenance_only', 'asset_kind': 'crop' if crop_display_pt is not None else 'page',
            'source_sha256': digest, 'source_byte_count': len(pdf_bytes),
            'page_no': page_no, 'page_count': len(document), 'renderer': renderer,
            'render_scale': scale, 'render_sha256': _sha(png), 'png_byte_count': len(png),
            'size_px': [pixmap.width, pixmap.height],
            'rotation_degrees': page.rotation, 'media_box_pdf_pt': _rect(page.mediabox),
            'crop_box_pdf_pt': _rect(page.cropbox), 'page_display_rect_pt': _rect(page_rect),
            'requested_crop_display_pt': _rect(clip),
            'raster_display_rect_pt': _rect(fitz.Rect(pixmap.x, pixmap.y, pixmap.x + pixmap.width,
                                                   pixmap.y + pixmap.height) * fitz.Matrix(1 / scale, 1 / scale)),
            'coordinate_frame': {'origin': 'top_left', 'unit': 'pt', 'rotation_applied': True},
            'mappings': {'pdf_user_to_fitz_unrotated': _matrix(page.transformation_matrix),
                         'fitz_unrotated_to_display': _matrix(page.rotation_matrix),
                         'display_to_asset_px': _matrix(display_to_asset),
                         'asset_px_to_display': _matrix(asset_to_display)},
            'native_words': native,
            'warnings': ['native_text_is_not_ocr_or_verified_cell_or_chart_fact'],
            'budgets': {'max_source_bytes': max_source_bytes, 'max_pages': max_pages,
                        'max_pixels': max_pixels, 'max_png_bytes': max_png_bytes,
                        'max_native_words': max_native_words},
        }
        return VisualAsset(png, manifest)
