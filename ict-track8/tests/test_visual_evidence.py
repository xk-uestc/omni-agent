"""Offline visual-asset invariants; no model requests or external fixtures."""
import hashlib
from io import BytesIO

import fitz
import pytest
from PIL import Image

from backend.visual_evidence import VisualEvidenceError, render_pdf_evidence


def pdf_bytes(rotation=0, crop=False):
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text((80, 100), 'Delta Robotics 125 USD')
        page.draw_rect(fitz.Rect(75, 75, 240, 110), color=(1, 0, 0))
        if crop:
            page.set_cropbox(fitz.Rect(40, 30, 360, 260))
        page.set_rotation(rotation)
        return doc.tobytes()


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
def test_original_page_words_rotation_cropbox_and_round_trip(rotation, crop):
    raw = pdf_bytes(rotation, crop)
    asset = render_pdf_evidence(raw, page_no=1)
    m = asset.manifest
    assert m['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert m['render_sha256'] == hashlib.sha256(asset.png_bytes).hexdigest()
    assert m['rotation_degrees'] == rotation
    with Image.open(BytesIO(asset.png_bytes)) as image:
        assert list(image.size) == m['size_px']
    expected = [320, 230] if crop else [400, 300]
    if rotation in (90, 270):
        expected.reverse()
    assert m['size_px'] == [v * 2 for v in expected]
    if crop:
        assert m['crop_box_pdf_pt'] == [40, 30, 360, 260]
    assert [w['text'] for w in m['native_words']] == ['Delta', 'Robotics', '125', 'USD']
    forward = fitz.Matrix(*m['mappings']['display_to_asset_px'])
    inverse = fitz.Matrix(*m['mappings']['asset_px_to_display'])
    pdf_to_unrotated = fitz.Matrix(*m['mappings']['pdf_user_to_fitz_unrotated'])
    for word in m['native_words']:
        display = fitz.Rect(word['bbox_display_pt'])
        assert list(display * forward) == pytest.approx(word['bbox_asset_px'])
        assert list(fitz.Rect(word['bbox_asset_px']) * inverse) == pytest.approx(list(display))
        assert list(fitz.Rect(word['bbox_pdf_user_pt']) * pdf_to_unrotated) == pytest.approx(
            word['bbox_fitz_unrotated_pt'], abs=0.001)
        assert word['fully_contained_in_crop']


def test_fractional_crop_maps_raster_origin_not_assumed_crop_offset():
    raw = pdf_bytes()
    asset = render_pdf_evidence(raw, page_no=1, crop_display_pt=[79.25, 85.25, 145.75, 104.75], render_scale=1.5)
    m = asset.manifest
    assert m['asset_kind'] == 'crop'
    assert m['requested_crop_display_pt'] == [79.25, 85.25, 145.75, 104.75]
    assert m['mappings']['display_to_asset_px'][4:] == [-118.0, -127.0]
    assert any(w['text'] == 'Delta' for w in m['native_words'])
    assert any(not w['fully_contained_in_crop'] for w in m['native_words'])
    assert all('125' != w['text'] for w in m['native_words'])


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_display_crop_on_rotated_page_retains_expected_word(rotation):
    raw = pdf_bytes(rotation, True)
    full = render_pdf_evidence(raw, page_no=1)
    word = full.manifest['native_words'][0]
    bounds = fitz.Rect(word['bbox_display_pt']) + (-1, -1, 1, 1)
    crop = render_pdf_evidence(raw, page_no=1, crop_display_pt=list(bounds))
    assert any(w['text'] == 'Delta' and w['fully_contained_in_crop'] for w in crop.manifest['native_words'])
    assert crop.manifest['size_px'][0] < full.manifest['size_px'][0]


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_crop_pixels_equal_same_region_of_original_page(rotation):
    raw = pdf_bytes(rotation, True)
    full = render_pdf_evidence(raw, page_no=1)
    bounds = full.manifest['native_words'][0]['bbox_display_pt']
    clip = [int(bounds[0]) - 2, int(bounds[1]) - 2, int(bounds[2]) + 2, int(bounds[3]) + 2]
    crop = render_pdf_evidence(raw, page_no=1, crop_display_pt=clip)
    with Image.open(BytesIO(full.png_bytes)) as page_image, Image.open(BytesIO(crop.png_bytes)) as crop_image:
        assert page_image.crop(tuple(v * 2 for v in clip)).tobytes() == crop_image.tobytes()


def test_assets_are_deterministic_and_identity_changes_with_input_or_crop():
    raw = pdf_bytes()
    a = render_pdf_evidence(raw, page_no=1)
    b = render_pdf_evidence(raw, page_no=1)
    assert a.png_bytes == b.png_bytes and a.manifest == b.manifest
    c = render_pdf_evidence(raw, page_no=1, crop_display_pt=[0, 0, 200, 200])
    assert a.manifest['evidence_id'] != c.manifest['evidence_id']
    assert a.manifest['render_sha256'] != c.manifest['render_sha256']
    with pytest.raises(VisualEvidenceError, match='source_sha256_mismatch'):
        render_pdf_evidence(raw, page_no=1, expected_source_sha256='0' * 64)


@pytest.mark.parametrize('kwargs', [
    {'page_no': 0}, {'page_no': True}, {'page_no': 2},
    {'render_scale': float('nan')}, {'render_scale': True}, {'render_scale': 5},
    {'crop_display_pt': [0, 0, 401, 300]}, {'crop_display_pt': [10, 10, 0, 0]},
    {'crop_display_pt': [0, 0, float('inf'), 2]}, {'crop_display_pt': 'https://example.com'},
    {'max_pixels': 100}, {'max_png_bytes': 10}, {'max_native_words': 1},
    {'max_source_bytes': 10}, {'max_pixels': True},
])
def test_invalid_geometry_and_budgets_fail_explicitly(kwargs):
    args = {'page_no': 1, **kwargs}
    with pytest.raises(VisualEvidenceError):
        render_pdf_evidence(pdf_bytes(), **args)


def test_pixel_budget_is_checked_before_rasterization(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('rasterization must not start')
    monkeypatch.setattr(fitz.Page, 'get_pixmap', forbidden)
    with pytest.raises(VisualEvidenceError, match='before rasterization'):
        render_pdf_evidence(pdf_bytes(), page_no=1, max_pixels=10)


@pytest.mark.parametrize('raw', [b'', b'not pdf', 'C:/private.pdf', 'https://example.com/a.pdf'])
def test_only_pdf_bytes_are_accepted(raw):
    with pytest.raises(VisualEvidenceError):
        render_pdf_evidence(raw, page_no=1)


def test_page_count_budget_and_encryption():
    with fitz.open() as doc:
        doc.new_page()
        doc.new_page()
        raw = doc.tobytes()
        encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='user')
    with pytest.raises(VisualEvidenceError, match='page-count'):
        render_pdf_evidence(raw, page_no=1, max_pages=1)
    with pytest.raises(VisualEvidenceError, match='encrypted'):
        render_pdf_evidence(encrypted, page_no=1)


def test_image_only_page_does_not_invent_native_words():
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.draw_rect(fitz.Rect(10, 10, 80, 80), fill=(0, 0, 1))
        raw = doc.tobytes()
    m = render_pdf_evidence(raw, page_no=1).manifest
    assert m['native_words'] == []
    assert m['status'] == 'rendered_provenance_only'
    assert m['warnings'] == ['native_text_is_not_ocr_or_verified_cell_or_chart_fact']
