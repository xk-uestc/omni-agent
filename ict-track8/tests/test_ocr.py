from __future__ import annotations

import base64
from dataclasses import dataclass

from fastapi.testclient import TestClient

import backend.app as app_module
from backend.ocr import OcrPipeline, OcrResponse, build_ocr_pipeline


@dataclass
class FakeQuality:
    recommended_transforms: tuple[str, ...] = ("grayscale_normalize", "upscale")


class FakeAnalyzer:
    def analyze(self, _image_bytes):
        return FakeQuality()


class FakeEnhancer:
    def __init__(self):
        self.calls = []

    def enhance(self, image_bytes, *, transforms):
        self.calls.append(tuple(transforms))
        return type("Enhanced", (), {"image_bytes": image_bytes})()


class FakeExecutor:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def execute(self, _image_bytes, *, language):
        self.calls += 1
        assert language == "chi_sim+eng"
        if self.calls == 1:
            return OcrResponse("模糊结果", 0.42, {})
        return OcrResponse("清晰结果", 0.96, {})


def test_ocr_pipeline_preserves_retry_evidence():
    enhancer = FakeEnhancer()
    executor = FakeExecutor()
    result = OcrPipeline(executor, analyzer=FakeAnalyzer(), enhancer=enhancer).run(
        b"image", language="chi_sim+eng", max_attempts=3
    )
    assert result.status == "ok"
    assert result.text == "清晰结果"
    assert result.selected_transforms == ("grayscale_normalize",)
    assert [attempt.status for attempt in result.attempts] == ["low_confidence", "accepted"]
    assert enhancer.calls == [(), ("grayscale_normalize",)]


def test_ocr_endpoint_returns_attempts_from_real_pipeline(monkeypatch):
    pipeline = OcrPipeline(FakeExecutor(), analyzer=FakeAnalyzer(), enhancer=FakeEnhancer())
    monkeypatch.setattr(app_module, "ocr_pipeline", pipeline)
    client = TestClient(app_module.app)
    response = client.post(
        "/api/v1/documents/ocr",
        json={"image_base64": base64.b64encode(b"image").decode("ascii"), "language": "chi_sim+eng"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert len(payload["attempts"]) == 2
    assert payload["executor"] == "fake"


def test_ocr_pipeline_health_is_read_only_and_describes_executor():
    pipeline = OcrPipeline(FakeExecutor(), analyzer=FakeAnalyzer(), enhancer=FakeEnhancer())
    health = pipeline.health()
    assert health["configured"] is True
    assert health["ready"] is True
    assert health["provider"] == "fake"


def test_invalid_ocr_url_disables_executor_without_import_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setenv("ICT8_OCR_URL", "ftp://not-supported")
    assert build_ocr_pipeline(warnings) is None
    assert warnings == ["ICT8_OCR_URL: invalid; OCR disabled"]


def test_quality_retries_do_not_replace_better_baseline_with_last_attempt():
    class PoorQuality(FakeAnalyzer):
        def analyze(self, _image_bytes):
            return type('Quality', (), {'quality_score': 0.4, 'recommended_transforms': ('upscale', 'contrast')})()

    class RegressingExecutor:
        name = 'fake'
        def __init__(self):
            self.responses = iter([OcrResponse('一般工单24小时', .98, {'selected': 'baseline'}),
                                   OcrResponse('一工单24小时', .94, {}), OcrResponse('一工单24小时', .95, {})])
        def execute(self, _image_bytes, *, language):
            return next(self.responses)

    result = OcrPipeline(RegressingExecutor(), analyzer=PoorQuality(), enhancer=FakeEnhancer()).run(b'image')
    assert len(result.attempts) == 3
    assert result.text == '一般工单24小时'
    assert result.selected_transforms == ()
    assert result.metadata['selected'] == 'baseline'
