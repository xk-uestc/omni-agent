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


@pytest.mark.parametrize('degrees,size,order',[(90,(2,3),[3,0,4,1,5,2]),
                                             (180,(3,2),[5,4,3,2,1,0]),
                                             (270,(2,3),[2,5,1,4,0,3])])
def test_preview_api_accepts_quarter_and_half_turns_and_preserves_actual_pixels(degrees,size,order):
    import base64
    from fastapi.testclient import TestClient
    from backend.app import app
    colors = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255),(255,0,255)]
    source = Image.new('RGB',(3,2))
    source.putdata(colors)
    buffer = BytesIO()
    source.save(buffer,format='PNG')
    response = TestClient(app).post('/api/v1/documents/image-enhance',json={
        'image_base64':base64.b64encode(buffer.getvalue()).decode(),
        'transforms':['rotate_to_upright'],'rotation_degrees':degrees})
    assert response.status_code==200 and response.json()['ocr_executed'] is False
    with Image.open(BytesIO(base64.b64decode(response.json()['image_base64']))) as result:
        assert result.size==size and list(result.getdata())==[colors[index] for index in order]


@pytest.mark.parametrize('degrees',[-361,361])
def test_preview_api_rejects_out_of_range_rotation(degrees):
    import base64
    from fastapi.testclient import TestClient
    from backend.app import app
    response = TestClient(app).post('/api/v1/documents/image-enhance',json={
        'image_base64':base64.b64encode(image_bytes()).decode(),
        'transforms':['rotate_to_upright'],'rotation_degrees':degrees})
    assert response.status_code==422
