"""Replay an auditable, selective OCR route over saved real-engine outputs.

This is an offline prototype. It never uses reference labels to select an engine,
and it never silently promotes a table model's text into recognized source text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import unicodedata
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from rapidfuzz.distance import Levenshtein


BASE = Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")
MONEY = re.compile(r"^[\$￥¥]?-?\d[\d,]*(?:\.\d+)?(?:元)?$")
SUSPECT_MONEY = re.compile(r"^(?:[sS]\d[\d,]*(?:\.\d+)?|\$[sSoO]\d*)$")


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold()).replace("−", "-")


def field_present(value: str, text: str) -> bool:
    target = normalized(value)
    if MONEY.fullmatch(target):
        # Preserve line boundaries: joining "2016\n$47,000" creates a false miss.
        pattern = re.compile(r"(?<![\d,.$-])" + re.escape(target) + r"(?![\d,]|\.\d)")
        return any(pattern.search(normalized(line)) for line in text.splitlines())
    return target in normalized(text)


def needs_quality_pass(record: dict) -> tuple[bool, list[str]]:
    reasons = []
    nodes = record.get("nodes", [])
    if any(float(node.get("confidence", 1)) < 0.75 for node in nodes):
        reasons.append("low_line_confidence")
    if any(SUSPECT_MONEY.fullmatch(str(node.get("text", "")).strip()) for node in nodes):
        reasons.append("ambiguous_currency_glyph")
    return bool(reasons), reasons


def needs_structure_pass(record: dict) -> bool:
    lines = [str(node.get("text", "")).strip() for node in record.get("nodes", [])]
    amounts = sum(bool(MONEY.fullmatch(line)) and any(c.isdigit() for c in line) for line in lines)
    has_total = any(re.fullmatch(r"(?:total|合计|总计)[:：]?", line, re.I) for line in lines)
    return has_total and amounts >= 4


class TableCells(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def table_candidates(raw_path: str | None) -> list[dict]:
    if not raw_path:
        return []
    payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    candidates = []
    for page in payload.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") != "table":
                continue
            for body in block.get("content", []):
                html = body.get("content", "")
                if not isinstance(html, str) or "<table" not in html.lower():
                    continue
                parser = TableCells()
                parser.feed(html)
                candidates.append({"bbox_normalized": block.get("bbox"), "rows": parser.rows})
    return candidates


def _center_y(node: dict) -> float:
    box = node["bbox"]
    return (float(box[1]) + float(box[3])) / 2


def verify_rows(rows: list[list[str]], nodes: list[dict]) -> list[dict]:
    """Ground each two-cell row in two distinct OCR boxes and their line geometry."""
    offsets = []
    for row in rows:
        if len(row) != 2:
            continue
        left = [n for n in nodes if n.get("bbox") and normalized(n["text"]) == normalized(row[0])]
        right = [n for n in nodes if n.get("bbox") and normalized(n["text"]) == normalized(row[1])]
        if len(left) == len(right) == 1 and left[0] is not right[0]:
            offsets.append(_center_y(right[0]) - _center_y(left[0]))
    offset = statistics.median(offsets) if len(offsets) >= 2 else 0.0
    used_labels: set[int] = set()
    used_amounts: set[int] = set()
    evidence = []
    for row in rows:
        item = {"cells": row, "status": "unverified", "reason": "not_two_cells"}
        if len(row) != 2:
            evidence.append(item)
            continue
        labels = [(i, n) for i, n in enumerate(nodes) if i not in used_labels and n.get("bbox") and normalized(n["text"]) == normalized(row[0])]
        amounts = [(i, n) for i, n in enumerate(nodes) if i not in used_amounts and n.get("bbox") and normalized(n["text"]) == normalized(row[1])]
        if not labels or not amounts:
            item["reason"] = "cell_text_not_observed"
            evidence.append(item)
            continue
        pairs = [(abs(_center_y(a) - _center_y(l) - offset), li, l, ai, a) for li, l in labels for ai, a in amounts
                 if li != ai and float(a["bbox"][0]) > float(l["bbox"][0])]
        if not pairs:
            item["reason"] = "same_source_box"
            evidence.append(item)
            continue
        distance, li, label, ai, amount = min(pairs, key=lambda pair: pair[0])
        height = max(float(label["bbox"][3]) - float(label["bbox"][1]), float(amount["bbox"][3]) - float(amount["bbox"][1]))
        if distance > max(8.0, height * 0.8):
            item["reason"] = "same_row_geometry_not_supported"
            evidence.append(item)
            continue
        used_labels.add(li)
        used_amounts.add(ai)
        item.update(status="observed_pair", reason=None, label_bbox=label["bbox"], amount_bbox=amount["bbox"], residual_px=round(distance, 1))
        evidence.append(item)
    return evidence


def table_arithmetic(rows: list[dict]) -> dict:
    paired = [row for row in rows if len(row["cells"]) == 2]
    totals = [i for i, row in enumerate(paired) if re.fullmatch(r"(?:total|合计|总计)[:：]?", row["cells"][0], re.I)]
    if len(totals) != 1 or totals[0] != len(paired) - 1:
        return {"status": "not_applicable"}
    if any(row["status"] != "observed_pair" for row in paired):
        return {"status": "unverified_rows"}
    values = []
    started = False
    for row in paired:
        amount = normalized(row["cells"][1]).lstrip("$¥￥").removesuffix("元").replace(",", "")
        try:
            value = Decimal(amount)
        except Exception:
            if not started:
                continue
            return {"status": "non_numeric_cell"}
        started = True
        values.append(value)
    if len(values) < 2:
        return {"status": "insufficient_numeric_rows"}
    return {"status": "consistent" if sum(values[:-1]) == values[-1] else "conflict",
            "sum_rows": str(sum(values[:-1])), "reported_total": str(values[-1])}


def evaluate(base: Path = BASE) -> dict:
    cases = {case["name"]: case for case in json.loads((base / "matrix-samples/manifest.json").read_text(encoding="utf-8"))["cases"]}
    report_paths = {name: max(base.glob(f"matrix-{name}-*/report.json"), key=lambda path: path.stat().st_mtime)
                    for name in ("rapid", "paddle", "mineru-standard")}
    providers = {name: {(r["name"], r["round"]): r for r in json.loads(path.read_text(encoding="utf-8"))["records"]}
                 for name, path in report_paths.items()}
    budget_reference_path = base / "matrix-samples/budget-reference.json"
    budget_reference = json.loads(budget_reference_path.read_text(encoding="utf-8"))["reference_text"] if budget_reference_path.exists() else None
    rows = []
    for key, rapid in providers["rapid"].items():
        case = cases[key[0]]
        if rapid["sha256"] != case["sha256"] or hashlib.sha256(Path(case["path"]).read_bytes()).hexdigest() != case["sha256"]:
            raise ValueError(f"Input identity changed: {key[0]}")
        slow, reasons = needs_quality_pass(rapid)
        primary = providers["paddle"][key] if slow else rapid
        structure = needs_structure_pass(primary)
        mineru = providers["mineru-standard"][key] if structure else None
        candidates = table_candidates(mineru.get("raw_path")) if mineru else []
        tables = []
        for table in candidates:
            grounded = verify_rows(table["rows"], primary["nodes"])
            tables.append({"bbox_normalized": table["bbox_normalized"], "rows": grounded,
                           "arithmetic": table_arithmetic(grounded)})
        row = {"name": key[0], "round": key[1], "scenario": case["scenario"], "sha256": case["sha256"],
               "primary": "paddle" if slow else "rapid", "quality_reasons": reasons, "structure_requested": structure,
               "coordinate_frame": "paddle_engine_output_unmapped" if slow else "source_image_pixels",
               "source_raw_paths": {name: providers[name][key].get("raw_path") for name in providers if name in {"rapid", "paddle"} or mineru},
               "seconds_replayed_sum": round(rapid["seconds"] + (primary["seconds"] if slow else 0) + (mineru["seconds"] if mineru else 0), 3),
               "peak_rss_mb_upper_bound": max([rapid["peak_rss_mb"], primary["peak_rss_mb"], mineru["peak_rss_mb"] if mineru else 0]),
               "fields_found": sum(field_present(value, primary["text"]) for value in case["fields"]), "fields_expected": len(case["fields"]),
               "rapid_fields_found": sum(field_present(value, rapid["text"]) for value in case["fields"]),
               "paddle_fields_found": sum(field_present(value, providers["paddle"][key]["text"]) for value in case["fields"]),
               "tables": tables, "errors": {name: providers[name][key]["error"] for name in providers if name in {"rapid", "paddle"} or mineru}}
        if case["reference_text"]:
            reference = normalized(case["reference_text"])
            row["synthetic_CER"] = round(Levenshtein.distance(reference, normalized(primary["text"])) / len(reference), 4)
        if key[0].startswith("budget") and budget_reference:
            reference = normalized(budget_reference)
            row["public_budget_sequence_CER"] = round(Levenshtein.distance(reference, normalized(primary["text"])) / len(reference), 4)
            row["rapid_budget_sequence_CER"] = round(Levenshtein.distance(reference, normalized(rapid["text"])) / len(reference), 4)
            row["paddle_budget_sequence_CER"] = round(Levenshtein.distance(reference, normalized(providers["paddle"][key]["text"])) / len(reference), 4)
        rows.append(row)
    total = lambda field: sum(row[field] for row in rows)
    summary = {"runs": len(rows), "rapid_fields": total("rapid_fields_found"), "paddle_fields": total("paddle_fields_found"),
               "fusion_fields": total("fields_found"), "fields_expected": total("fields_expected"),
               "quality_passes": sum(row["primary"] == "paddle" for row in rows), "structure_passes": sum(row["structure_requested"] for row in rows),
               "rapid_seconds_sum": round(sum(r["seconds"] for r in providers["rapid"].values()), 3),
               "paddle_seconds_sum": round(sum(r["seconds"] for r in providers["paddle"].values()), 3),
               "fusion_quality_only_seconds_replayed_sum": round(sum(providers["rapid"][(row["name"], row["round"])]["seconds"] +
                    (providers["paddle"][(row["name"], row["round"])]["seconds"] if row["primary"] == "paddle" else 0) for row in rows), 3),
               "fusion_seconds_replayed_sum": round(total("seconds_replayed_sum"), 3),
               "observed_table_pairs": sum(item["status"] == "observed_pair" for row in rows for table in row["tables"] for item in table["rows"]),
               "candidate_table_pairs": sum(sum(len(item["cells"]) == 2 for item in table["rows"]) for row in rows for table in row["tables"]),
               "arithmetic_consistent_tables": sum(table["arithmetic"]["status"] == "consistent" for row in rows for table in row["tables"]),
               "arithmetic_conflicts": sum(table["arithmetic"]["status"] == "conflict" for row in rows for table in row["tables"])}
    budget = [row for row in rows if "public_budget_sequence_CER" in row]
    summary["budget_sequence_CER"] = {engine: round(statistics.mean(row[f"{engine}_budget_sequence_CER"] for row in budget), 4)
                                       for engine in ("rapid", "paddle", "public")}
    summary["budget_sequence_CER"]["fusion"] = summary["budget_sequence_CER"].pop("public")
    return {"method": "offline_replay_of_real_engine_outputs", "not_official_benchmark": True,
            "source_reports": {name: str(path) for name, path in report_paths.items()},
            "source_notices": ["No ground truth used for routing", "MinerU table candidates never modify primary OCR text", "Paddle coordinate frame is not mapped to source image", "Time is sum of prior isolated calls, not end-to-end wall clock"],
            "summary": summary, "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=BASE)
    args = parser.parse_args()
    result = evaluate(args.base)
    target = args.base / "fusion-trial.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(target)


if __name__ == "__main__":
    main()
