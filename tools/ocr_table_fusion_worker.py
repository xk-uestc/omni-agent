"""Isolated upstream adapters. Requests contain images, never reference answers."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import traceback

BASE = Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ict-track8"))


def serial(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items() if k not in {"input_img", "output_img", "imgs"}}
    if isinstance(value, (tuple, list)):
        return [serial(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def box(poly):
    return [min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["rapid-structure", "paddle-structure"])
    parser.add_argument("requests", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    requests = json.loads(args.requests.read_text(encoding="utf-8"))
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(BASE / "paddle-cache")
    started = time.perf_counter()
    if args.provider == "rapid-structure":
        import cv2
        import numpy as np
        from PIL import Image
        from rapidocr import RapidOCR
        from rapid_table import RapidTable, RapidTableInput
        from img2table.document import Image as TableImage
        from img2table.ocr._types import OCRInstance, OCRData
        from quantulum3 import parser as quantities
        engine = RapidOCR()
        structure = RapidTable(RapidTableInput(use_ocr=False))
        versions = {p: importlib.metadata.version(p) for p in ("rapidocr", "rapid-table", "img2table", "quantulum3")}
    else:
        from paddleocr import TableStructureRecognition
        engine = TableStructureRecognition(model_name="SLANet_plus", device="cpu", enable_mkldnn=False, cpu_threads=4)
        versions = {p: importlib.metadata.version(p) for p in ("paddleocr", "paddlex", "paddlepaddle")}
    initialization = time.perf_counter() - started
    report = {"provider": args.provider, "versions": versions, "initialization_seconds": initialization, "records": []}
    for request in requests:
        started = time.perf_counter()
        record = {"id": request["id"], "path": request["path"], "sha256": request["sha256"], "error": None}
        try:
            if hashlib.sha256(Path(request["path"]).read_bytes()).hexdigest() != request["sha256"]:
                raise ValueError("input_identity_changed")
            if args.provider == "paddle-structure":
                record["predictions"] = [serial(dict(r)) for r in engine.predict(request["path"])]
            else:
                image = Image.open(request["path"]).convert("RGB")
                # RapidOCR ndarray input is BGR; a PIL RGB array silently swaps colours.
                # The path loader performs the library's own colour conversion.
                result = engine(request["path"], return_word_box=True, return_single_char_box=True)
                record["ocr_seconds"] = time.perf_counter()-started
                nodes = []
                word_lines = result.word_results or []
                for i, (text, score, poly) in enumerate(zip(result.txts or [], result.scores if result.scores is not None else [], result.boxes if result.boxes is not None else [])):
                    words = []
                    for wt, ws, wp in (word_lines[i] if i < len(word_lines) else []) or []:
                        polygon = serial(wp)
                        words.append({"text": wt, "confidence": float(ws), "polygon": polygon, "bbox": box(polygon)})
                    polygon = serial(poly)
                    nodes.append({"text": text, "confidence": float(score), "polygon": polygon, "bbox": box(polygon), "words": words})
                record["nodes"] = nodes
                table_start = time.perf_counter()
                out = structure(request["path"])
                record["rapid_table"] = {"cell_bboxes": serial(out.cell_bboxes), "logic_points": serial(out.logic_points), "seconds": time.perf_counter()-table_start,
                    "model": "SLANet-plus", "correlated_with_paddle_slanet": True}
                records = [{"id": str(i), "parent": str(i), "value": n["text"], "confidence": min(99, int(100*n["confidence"])),
                            "x1": int(n["bbox"][0]), "y1": int(n["bbox"][1]), "x2": int(n["bbox"][2]), "y2": int(n["bbox"][3])} for i, n in enumerate(nodes)]
                class ObservedOCR(OCRInstance):
                    def of(self, document):
                        return OCRData(records={0: records})
                table_start = time.perf_counter()
                try:
                    tables = TableImage(src=request["path"], detect_rotation=False).extract_tables(ocr=ObservedOCR(), borderless_tables=True, implicit_rows=True, implicit_columns=True, min_confidence=50)
                    record["img2table"] = {"seconds": time.perf_counter()-table_start, "tables": [
                        {"bbox": [t.bbox.x1,t.bbox.y1,t.bbox.x2,t.bbox.y2], "cells": [
                            {"row": r, "column": c, "text": cell.value or "", "bbox": [cell.bbox.x1,cell.bbox.y1,cell.bbox.x2,cell.bbox.y2]}
                            for r, row in t.content.items() for c, cell in enumerate(row)]} for t in tables]}
                except Exception:
                    record["img2table"] = {"error": traceback.format_exc(limit=3), "tables": [], "seconds": time.perf_counter()-table_start}
                unit_probes = []
                for n in nodes:
                    if any(x in n["text"] for x in ("$", "million", "万元", "￥")):
                        try:
                            parsed = quantities.parse(n["text"])
                            unit_probes.append({"text": n["text"], "parsed": [q.to_dict(include_unit_dict=True) for q in parsed]})
                        except Exception as exc:
                            unit_probes.append({"text": n["text"], "error": str(exc)})
                record["quantulum3"] = unit_probes
        except Exception:
            record["error"] = traceback.format_exc(limit=6)
        record["seconds"] = time.perf_counter() - started
        report["records"].append(record)
        args.output.write_text(json.dumps(serial(report), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"id": request["id"], "seconds": round(record["seconds"], 2), "error": record["error"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
