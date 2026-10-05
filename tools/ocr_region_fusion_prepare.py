"""Fresh public PDF renders plus a clearly labeled synthetic numeric stress sheet."""
from __future__ import annotations

from decimal import Decimal
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import uuid

import fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ict-track8"))
from backend.ocr_region_fusion import amount_key

BASE = Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def prepare():
    root = BASE / ("region-fusion-" + uuid.uuid4().hex)
    root.mkdir()
    (root / "samples").mkdir()
    cases = []
    def add(name, image, cells, provenance, fields=None):
        path = root / "samples" / (name + ".png")
        image.save(path)
        cases.append({"name": name, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "size_px": list(image.size), "numeric_cells": cells, "fields": fields or [], "provenance": provenance})
    existing = json.loads((BASE / "matrix-samples/manifest.json").read_text(encoding="utf-8"))["cases"]
    for case in existing:
        if case["name"] in {"budget-90", "budget-lowres"}:
            add("regression-" + case["name"], Image.open(case["path"]).convert("RGB"), [],
                {"type": "prior_sample_regression", "original_sha256": case["sha256"]}, case["fields"])
    pdfs = [
        ("amazon-operations", Path(r"D:\ICT8-OfficialDatasets\ohr-bench\pdfs\finance\DUDE_2658138cd3e6af802de36cc0079c39a5.pdf"), 9),
        ("kyrgyz-finance", Path(r"D:\ICT8-OfficialDatasets\ohr-bench\pdfs\finance\DUDE_4ead21606785b8a12a5382c99c98e38c.pdf"), 0),
    ]
    for name, path, page_index in pdfs:
        with fitz.open(path) as pdf:
            page = pdf[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            cells = [{"text": word[4], "bbox": [v * 1.5 for v in word[:4]]}
                     for word in page.get_text("words") if amount_key(word[4]) is not None and re_numeric_interest(word[4])]
            provenance = {"type": "public_native_pdf_render_not_real_scan", "pdf": str(path),
                          "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "page": page_index + 1,
                          "reference": "independent_native_PDF_word_text_and_boxes"}
            add(name + "-clean", image, cells, provenance)
            degraded = image.resize((round(image.width * .62), round(image.height * .62))).filter(ImageFilter.GaussianBlur(.45))
            buffer = BytesIO()
            degraded.save(buffer, format="JPEG", quality=55)
            buffer.seek(0)
            degraded = Image.open(buffer).convert("RGB")
            add(name + "-degraded", degraded,
                [{**cell, "bbox": [v * .62 for v in cell["bbox"]]} for cell in cells],
                {**provenance, "degradation": "resize_0.62_blur_0.45_JPEG55"})
    image = Image.new("RGB", (1100, 800), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
    draw.text((55, 25), "新样例：费用清单与负数调整", font=font, fill="black")
    pairs = [("基础服务", "12,000.00"), ("数据采购", "2,000.10"), ("返还费用", "-1,234.50"),
             ("软件授权", "100.01"), ("设备租赁", "100.10"), ("专项资金", "1,000,000.00")]
    total = sum(Decimal(amount.replace(",", "")) for _, amount in pairs)
    pairs.append(("合计", f"{total:,.2f}"))
    cells = []
    for index, (label, amount) in enumerate(pairs):
        y = 105 + index * 83
        text = "￥" + amount
        draw.text((55, y), label, font=font, fill="black")
        draw.text((700, y), text, font=font, fill="black")
        bounds = draw.textbbox((700, y), text, font=font)
        cells.append({"text": text, "bbox": list(bounds), "label": label})
        draw.line((45, y + 48, 1045, y + 48), fill="#aaaaaa", width=1)
    add("fresh-chinese-amounts", image, cells, {"type": "synthetic_known_text_not_public_benchmark"})
    degraded = image.resize((660, 480)).filter(ImageFilter.GaussianBlur(.65))
    add("fresh-chinese-amounts-degraded", degraded, [{**cell, "bbox": [v * .6 for v in cell["bbox"]]} for cell in cells],
        {"type": "synthetic_known_text_controlled_degradation"})
    (root / "manifest.json").write_text(json.dumps({"not_official_benchmark": True, "cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")
    requests = [{"id": case["name"], "path": case["path"], "sha256": case["sha256"]} for case in cases]
    (root / "page-requests.json").write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def re_numeric_interest(text):
    # Exclude page numbers/years; reference selection is never visible to routing.
    return any(character in text for character in ",.$()-")


if __name__ == "__main__":
    print(prepare())
