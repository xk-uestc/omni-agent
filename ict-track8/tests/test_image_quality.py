from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from backend.image_quality import ImageEnhancer, ImageQualityAnalyzer


def image_bytes(width=320, height=240, *, flat=False):
    image = Image.new("L", (width, height), color=128 if flat else 30)
    if not flat:
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, width - 20, height - 20), outline=255, width=4)
        draw.line((0, 0, width, height), fill=200, width=3)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_image_quality_reports_dimensions_and_resolution_issue():
    report = ImageQualityAnalyzer().analyze(image_bytes())
    assert report.width == 320
    assert report.height == 240
    assert report.quality_score >= 0
    assert "upscale" in report.recommended_transforms


def test_flat_image_is_flagged_for_contrast_and_sharpness():
    report = ImageQualityAnalyzer().analyze(image_bytes(flat=True))
    codes = {item["code"] for item in report.issues}
    assert {"low_contrast", "blurred"} <= codes
    assert {"contrast", "sharpen"} <= set(report.recommended_transforms)


def test_invalid_image_is_rejected():
    with pytest.raises(ValueError):
        ImageQualityAnalyzer().analyze(b"not-an-image")


@pytest.mark.parametrize('angle', [float('nan'), float('inf'), 361, True])
def test_invalid_rotation_is_rejected_before_processing(angle):
    with pytest.raises(ValueError, match='rotation_degrees'):
        ImageEnhancer().enhance(image_bytes(), transforms=('rotate_to_upright',), rotation_degrees=angle)


def test_skew_padding_remains_white_instead_of_creating_black_scan_edges():
    result = ImageEnhancer().enhance(image_bytes(width=120, height=80),
        transforms=('rotate_to_upright',), rotation_degrees=5)
    with Image.open(BytesIO(result.image_bytes)) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_image_enhancer_outputs_standardized_ocr_input():
    source = image_bytes(width=320, height=240, flat=True)
    result = ImageEnhancer().enhance(
        source,
        transforms=("grayscale_normalize", "adaptive_threshold", "sharpen", "upscale"),
        max_dimension=800,
    )
    assert result.format == "PNG"
    assert max(result.width, result.height) == 800
    assert result.applied_transforms == ("grayscale_normalize", "adaptive_threshold", "sharpen", "upscale")
    assert result.image_bytes.startswith(b"\x89PNG")


def test_image_enhancer_rejects_unknown_or_invalid_crop():
    source = image_bytes(width=320, height=240)
    with pytest.raises(ValueError, match="不支持"):
        ImageEnhancer().enhance(source, transforms=("invented",))
    with pytest.raises(ValueError, match="crop_box"):
        ImageEnhancer().enhance(source, transforms=("crop",), crop_box=(0, 0, 999, 999))


