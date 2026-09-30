from __future__ import annotations

import json
import sys

import pytest

from eval import ocr_eval
import backend.ocr as ocr_backend


def test_ocr_eval_writes_truthful_skipped_report(monkeypatch, tmp_path):
    class Unavailable:
        def health(self):
            return {"ready": False, "detail": "synthetic OCR unavailable"}

    output = tmp_path / "ocr.json"
    monkeypatch.setattr(ocr_backend, "TesseractOcrExecutor", Unavailable)
    monkeypatch.setattr(sys, "argv", ["ocr_eval.py", "--repo", ".", "--out", str(output)])
    ocr_eval.main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "skipped"
    assert report["overall"] is None
    assert report["reason"] == "synthetic OCR unavailable"


def test_ocr_eval_require_executor_is_a_release_gate(monkeypatch, tmp_path):
    class Unavailable:
        def health(self):
            return {"ready": False, "detail": "missing executor"}

    output = tmp_path / "ocr-required.json"
    monkeypatch.setattr(ocr_backend, "TesseractOcrExecutor", Unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        ["ocr_eval.py", "--repo", ".", "--out", str(output), "--require-executor"],
    )
    with pytest.raises(SystemExit) as exc:
        ocr_eval.main()
    assert exc.value.code == 2
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "skipped"
