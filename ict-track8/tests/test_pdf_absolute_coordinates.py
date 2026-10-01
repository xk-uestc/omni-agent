"""Physical PDF-user coordinates must survive cropping and page Rotate.

A round trip alone cannot detect two mutually inverse, equally wrong matrices.
Expected boxes come from a distinct original, uncropped physical page.
"""
import fitz
import pytest

from backend.chunk_cleaning import DocumentChunker


def original_pdf():
    with fitz.open() as document:
        page = document.new_page(width=400, height=300)
        page.insert_text((80, 100), 'Absolute physical location evidence.')
        block = next(b for b in page.get_text('blocks') if 'Absolute physical' in b[4])
        # Original PDF page height and uncropped top-left text bbox provide
        # an independent physical reference, without the tested transform.
        expected = [block[0], 300 - block[3], block[2], 300 - block[1]]
        return document.tobytes(), expected


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('cropbox', [None, [40, 30, 360, 260], [25, 20, 380, 285]])
def test_parser_absolute_pdf_user_bbox_matches_uncropped_original(rotation, cropbox):
    original, expected = original_pdf()
    with fitz.open(stream=original, filetype='pdf') as document:
        page = document[0]
        if cropbox:
            page.set_cropbox(fitz.Rect(cropbox))
        page.set_rotation(rotation)
        changed = document.tobytes()
    parsed = DocumentChunker().parse_pdf(changed, document_id='absolute-position')
    chunk = next(c for c in parsed.chunks if 'Absolute physical' in c.text)
    coordinates = chunk.metadata['coordinate_evidence']
    assert coordinates['source_bbox_pdf_user_pt'] == pytest.approx(expected, abs=0.001)
    assert coordinates['page_geometry']['rotation_degrees'] == rotation
    assert coordinates['page_geometry']['coordinate_mapping_version'] == 'cropbox-unrotated-v2'


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_geometry_restores_rotation_and_display_render_dimensions(rotation):
    original, _ = original_pdf()
    with fitz.open(stream=original, filetype='pdf') as document:
        page = document[0]
        page.set_cropbox(fitz.Rect(40, 30, 360, 260))
        page.set_rotation(rotation)
        before_rect, before_matrix = list(page.rect), list(page.rotation_matrix)
        before_render = page.get_pixmap().tobytes('png')
        geometry = DocumentChunker._pdf_page_geometry(page, render_scale=1)
        assert page.rotation == rotation
        assert list(page.rect) == before_rect
        assert list(page.rotation_matrix) == before_matrix
        assert page.get_pixmap().tobytes('png') == before_render
        assert geometry['display_rect_fitz_pt'] == before_rect
        assert geometry['mappings']['fitz_unrotated_to_display'] == before_matrix


def test_matrix_failure_still_restores_original_rotation():
    class FailingPage:
        rotation = 90
        mediabox = cropbox = rect = [0, 0, 100, 100]

        def set_rotation(self, value):
            self.rotation = value

        @property
        def transformation_matrix(self):
            assert self.rotation == 0
            raise RuntimeError('fixture matrix failure')

    page = FailingPage()
    with pytest.raises(RuntimeError, match='fixture'):
        DocumentChunker._pdf_page_geometry(page)
    assert page.rotation == 90
