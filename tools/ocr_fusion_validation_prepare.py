"""Fixed, answer-independent selection of previously unused public OCR images.

Images and official annotations are separate; inference requests never contain
annotations. This is a small transfer test, not a leaderboard submission.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def fetch(url):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "OmniAgent-OCR-validation"}), timeout=90).read()
        except urllib.error.HTTPError as error:
            if error.code not in {429,502,503,504} or attempt==2:raise
            time.sleep(2*(attempt+1))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def numeric_word(text, category="", locale=""):
    s = str(text).strip().replace("−", "-").replace(" ", "")
    if not re.fullmatch(r"[$¥￥]?-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?", s):
        return None
    raw = s.lstrip("$¥￥")
    # CORD price annotations use Indonesian thousands punctuation. Counts
    # are not money; do not normalize their decimal separators as thousands.
    money = "price" in category
    if locale == "id-ID" and money and re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", raw):
        value = Decimal(raw.replace(".", "").replace(",", "."))
    else:
        try:
            value = Decimal(raw.replace(",", ""))
        except Exception:
            return None
    return {"text": text, "value": str(value), "category": category,
            "reference_currency": "IDR" if locale == "id-ID" and money else None,
            "reference_basis": "official_word_transcription_and_dataset_locale"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--offset",type=int,default=0)
    ap.add_argument("--count",type=int,default=8)
    ap.add_argument("--holdout-only",action="store_true")
    ap.add_argument("--resume-download",action="store_true")
    args = ap.parse_args()
    out = args.output
    if args.resume_download and (out/"manifest.json").exists():raise ValueError("refuse_to_overwrite_completed_selection")
    out.mkdir(parents=True, exist_ok=args.resume_download)
    for name in ("images", "annotations", "frozen/backend"):
        (out/name).mkdir(parents=True,exist_ok=args.resume_download)
    files = ["ocr_table_fusion.py", "ocr_region_fusion.py", "image_geometry.py"]
    if (ROOT/"ict-track8/backend/ocr_fast_fusion.py").exists():files.append("ocr_fast_fusion.py")
    if (ROOT/"ict-track8/backend/ocr_gap_fusion.py").exists():files.append("ocr_gap_fusion.py")
    hashes = {}
    (out/"frozen/backend/__init__.py").write_text("", encoding="utf-8")
    for name in files:
        p = ROOT/"ict-track8/backend"/name
        shutil.copy2(p, out/"frozen/backend"/name)
        hashes[name] = sha(p.read_bytes())
    freeze = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": hashes,
              "sample_selection": f"sorted_FUNSD_test_and_CORD_test_rows_{args.offset}_to_{args.offset+args.count-1}",
              "split": "all_unopened_holdout" if args.holdout_only else "first_four_each_frozen_validation_dev; remaining_unopened_holdout",
              "upstream_learning_overlap_unknown": True}
    (out/"freeze.json").write_text(json.dumps(freeze, indent=2), encoding="utf-8")
    url = "https://guillaumejaume.github.io/FUNSD/dataset.zip"
    archive = (out/"FUNSD-source.zip").read_bytes() if args.resume_download and (out/"FUNSD-source.zip").exists() else fetch(url)
    (out/"FUNSD-source.zip").write_bytes(archive)
    cases = []
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = sorted(n for n in z.namelist() if "/testing_data/annotations/" in n and n.endswith(".json")
                       and not n.startswith("__MACOSX/") and not Path(n).name.startswith("."))[args.offset:args.offset+args.count]
        if len(names) != args.count:
            raise ValueError("FUNSD_layout_changed")
        for i, name in enumerate(names):
            data = z.read(name)
            annotation = json.loads(data)
            image_name = name.replace("/annotations/", "/images/").replace(".json", ".png")
            image = z.read(image_name)
            cid = "funsd-"+Path(name).stem
            ip = out/"images"/(cid+".png")
            ip.write_bytes(image)
            (out/"annotations"/(cid+".json")).write_bytes(data)
            truth = []
            for region in annotation["form"]:
                for word in region["words"]:
                    n = numeric_word(word["text"])
                    if n:
                        truth.append(dict(n, bbox=word["box"]))
            cases.append({"id": cid, "path": str(ip), "sha256": sha(image), "split": "dev" if i<4 and not args.holdout_only else "holdout",
                          "scene": "real_noisy_scanned_form", "dataset": "FUNSD", "numeric_cells": truth,
                          "source": {"url": url, "member": image_name, "archive_sha256": sha(archive),
                                     "annotation_sha256": sha(data), "license": "FUNSD_dataset_terms_source_site",
                                     "annotation_scope": "official_annotated_words_numeric_only_not_full_text"}})
    # Dataset authors link this exact repository in the CORD official README.
    rows_url = f"https://datasets-server.huggingface.co/rows?dataset=naver-clova-ix/cord-v2&config=default&split=test&offset={args.offset}&length={args.count}"
    rows_data = fetch(rows_url)
    rows = json.loads(rows_data)["rows"]
    (out/"CORD-source-rows.json").write_bytes(rows_data)
    def receipt(row):
        i = row["row_idx"]
        img = fetch(row["row"]["image"]["src"])
        annotation_raw = row["row"]["ground_truth"]
        annotation = json.loads(annotation_raw)
        cid = f"cord-{i:03d}"
        ip = out/"images"/(cid+".jpg")
        ip.write_bytes(img)
        (out/"annotations"/(cid+".json")).write_text(annotation_raw, encoding="utf-8")
        truth = []
        for line in annotation["valid_line"]:
            for word in line["words"]:
                n = numeric_word(word["text"], line["category"], "id-ID")
                if n:
                    q = word["quad"]
                    box = [min(q[f"x{j}"] for j in range(1,5)), min(q[f"y{j}"] for j in range(1,5)),
                           max(q[f"x{j}"] for j in range(1,5)), max(q[f"y{j}"] for j in range(1,5))]
                    truth.append(dict(n, bbox=box))
        return {"id": cid, "path": str(ip), "sha256": sha(img), "split": "dev" if i-args.offset<4 and not args.holdout_only else "holdout",
                "scene": "real_receipt_capture_capture_method_not_all_documented", "dataset": "CORD-v2", "numeric_cells": truth,
                "source": {"url": "https://huggingface.co/datasets/naver-clova-ix/cord-v2", "revision": "7f0115a4b758a71d6473b8d085751692da2fef98",
                           "row": i, "annotation_sha256": sha(annotation_raw.encode()), "license": "CC-BY-4.0",
                           "annotation_scope": "official_valid_line_numeric_words_only"}}
    with ThreadPoolExecutor(max_workers=4) as pool:
        cases.extend(pool.map(receipt, rows))
    manifest = {"freeze": freeze, "cases": cases, "new_to_project_validation": True, "not_official_contest_benchmark": True}
    (out/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"root": str(out), "pages": len(cases), "numeric_references": sum(len(c["numeric_cells"]) for c in cases),
                      "dev_pages":sum(c["split"]=="dev" for c in cases),"holdout_pages":sum(c["split"]=="holdout" for c in cases)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
