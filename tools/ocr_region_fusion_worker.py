"""One isolated model environment; actual page or cropped-line inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ict-track8"))
BASE = Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["rapid-page", "rapid-region", "rapid-line", "rapid-fragment", "paddle-line", "paddle-fragment", "paddle-page", "paddle-region"])
    parser.add_argument("requests", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    requests = json.loads(args.requests.read_text(encoding="utf-8"))
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(BASE / "paddle-cache")
    process = psutil.Process()
    peak = [process.memory_info().rss / 1024**2]
    stop = threading.Event()
    def monitor():
        while not stop.wait(.1):
            peak[0] = max(peak[0], process.memory_info().rss / 1024**2)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    started = time.perf_counter()
    if args.provider.startswith("rapid"):
        from rapidocr import RapidOCR
        from backend.orientation_quality import assess_text_orientation
        class ObservedRapidOCR(RapidOCR):
            orientation = None
            def run_ocr_steps(self, image, op_record):
                result = super().run_ocr_steps(image, op_record)
                det, cls, rec, crops = result
                boxes = det.boxes.tolist() if det.boxes is not None else []
                recognized = list(zip(rec.txts or [], rec.scores if rec.scores is not None else []))
                self.orientation = assess_text_orientation(boxes, cls.cls_res or [], recognized)
                return result
        engine = ObservedRapidOCR()
    elif args.provider in {"paddle-line", "paddle-fragment"}:
        from paddleocr import TextRecognition
        engine = TextRecognition(model_name="PP-OCRv6_medium_rec", model_dir=str(BASE / "paddle-cache/official_models/PP-OCRv6_medium_rec"),
                                 device="cpu", enable_mkldnn=False, cpu_threads=4)
    else:
        from paddleocr import PaddleOCR
        models = BASE / "paddle-cache/official_models"
        region = args.provider == "paddle-region"
        engine = PaddleOCR(device="cpu", use_doc_orientation_classify=not region, use_textline_orientation=not region,
            use_doc_unwarping=False, enable_mkldnn=False, cpu_threads=4,
            text_detection_model_dir=str(models / "PP-OCRv6_medium_det"), text_recognition_model_dir=str(models / "PP-OCRv6_medium_rec"),
            doc_orientation_classify_model_dir=str(models / "PP-LCNet_x1_0_doc_ori"),
            textline_orientation_model_dir=str(models / "PP-LCNet_x1_0_textline_ori"))
    initialization = time.perf_counter() - started
    records = []
    for request in requests:
        started = time.perf_counter()
        record = {"id": request["id"], "path": request["path"], "sha256": request["sha256"], "error": None}
        try:
            path = Path(request["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != request["sha256"]:
                raise ValueError("input_identity_changed")
            if args.provider.startswith("rapid"):
                page = args.provider in {"rapid-page", "rapid-region"}
                result = engine(str(path), use_det=page, use_cls=True, use_rec=True)
                texts = list(result.txts) if result.txts is not None else []
                scores = list(result.scores) if result.scores is not None else []
                if page:
                    nodes = []
                    for text, score, poly in zip(texts, scores, result.boxes if result.boxes is not None else []):
                        polygon = poly.tolist()
                        nodes.append({"text": text, "confidence": float(score), "polygon": polygon,
                                      "bbox": [min(p[0] for p in polygon), min(p[1] for p in polygon),
                                               max(p[0] for p in polygon), max(p[1] for p in polygon)]})
                    record.update(nodes=nodes, text="\n".join(texts), orientation=engine.orientation)
                else:
                    record.update(text=" ".join(texts), confidence=float(min(scores)) if scores else 0.)
            elif args.provider in {"paddle-line", "paddle-fragment"}:
                results = engine.predict(str(path))
                if len(results) != 1:
                    raise ValueError("unexpected_line_result_count")
                record.update(text=str(results[0]["rec_text"]), confidence=float(results[0]["rec_score"]))
            else:
                nodes = []
                for result in engine.predict(str(path)):
                    preprocessing = result.get("doc_preprocessor_res", {})
                    angle = int(preprocessing.get("angle", 0))
                    angle = angle if angle >= 0 else 0
                    record["rotation_ccw_degrees"] = angle
                    if "output_img" in preprocessing:
                        h, w = preprocessing["output_img"].shape[:2]
                        record["output_size_px"] = [w, h]
                    for text, score, poly in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"]):
                        polygon = poly.tolist()
                        nodes.append({"text": str(text), "confidence": float(score), "polygon": polygon,
                                      "bbox": [min(p[0] for p in polygon), min(p[1] for p in polygon),
                                               max(p[0] for p in polygon), max(p[1] for p in polygon)]})
                record.update(nodes=nodes, text="\n".join(node["text"] for node in nodes), coordinate_frame="paddle_native")
        except Exception:
            record["error"] = traceback.format_exc(limit=4)
        record["seconds"] = round(time.perf_counter() - started, 4)
        records.append(record)
        args.output.write_text(json.dumps({"provider": args.provider, "initialization_seconds": round(initialization, 4),
                                          "peak_rss_mb": round(peak[0], 1), "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"provider": args.provider, "id": request["id"], "seconds": record["seconds"], "error": bool(record["error"])}), flush=True)
    stop.set()
    thread.join(timeout=1)


if __name__ == "__main__":
    main()
