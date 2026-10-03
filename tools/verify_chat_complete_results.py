"""Independent live-HTTP check using a declared synthetic SQLite fixture."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ict-track8"))
DATABASE = ROOT / "runtime/chat-complete-ui/demo_sales.sqlite"

def prepare():
    from backend.nl2sql.seed import initialize_database
    if DATABASE.exists():
        raise FileExistsError("Use the existing declared UI fixture; do not overwrite it")
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    initialize_database(DATABASE)
    with closing(sqlite3.connect(DATABASE)) as db, db:
        for i in range(241):
            db.execute("""INSERT INTO sales_orders
                SELECT ?, '2025-05-01', ?, channel, product_category, product_name,
                quantity, unit_price, ?, customer_id FROM sales_orders ORDER BY order_id LIMIT 1""",
                (f"ui-probe-{i:03}", f"probe-region-{i:03}", 1000.0 + i))
    print(json.dumps({"fixture": str(DATABASE), "scope": "synthetic development fixture, not official benchmark"}))

def verify(base, output):
    from urllib.parse import urlencode
    def request(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data,
                headers={"Content-Type": "application/json"}), timeout=60) as response:
            return json.load(response)
    legacy = request("/api/v1/agent/query", {"question": "2025年各地区销售额排名", "use_context": False})
    result = request("/api/v1/agent/query", {"question": "2025年各地区销售额排名", "complete_results": True, "use_context": False})
    structured = result["structured"]; receipt = structured["provenance"]["complete_result"]
    rows, offset, page_lengths = [], 0, []
    while True:
        page = request("/api/v1/nl2sql/results/" + receipt["artifact_id"] + "?" + urlencode({
            "offset": offset, "binding_sha256": receipt["binding_sha256"], "query_sha256": receipt["query_sha256"]}))
        rows.extend(page["rows"]); page_lengths.append(len(page["rows"]))
        if page["next_offset"] is None: break
        offset = page["next_offset"]
    with closing(sqlite3.connect(DATABASE)) as db:
        reference = [list(row) for row in db.execute(structured["sql"], structured["parameters"])]
    checks = {"legacy_preview_100": len(legacy["structured"]["rows"]) == 100,
        "complete_total_244": receipt["row_count"] == 244,
        "actual_three_pages": page_lengths == [100, 100, 44],
        "every_cell_equals_sql_replay": rows == reference,
        "receipt_eof": receipt["cursor_eof_verified"] is True,
        "summary_distinguishes_preview": "完整查询结果 244 行" in result["answer"]}
    report = {"scope": "synthetic SQLite + rules + live HTTP, not real model accuracy or official benchmark",
        "api_calls_to_model": 0, "fixture_sha256": hashlib.sha256(DATABASE.read_bytes()).hexdigest(),
        "checks": checks, "page_lengths": page_lengths, "columns": structured["columns"],
        "sql": structured["sql"], "parameters": structured["parameters"], "status": "pass" if all(checks.values()) else "fail"}
    if output.exists(): raise FileExistsError("Do not overwrite historical evidence")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "pass": raise SystemExit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8031")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/CHAT_COMPLETE_HTTP_20261003.json")
    args = parser.parse_args()
    if args.prepare: prepare()
    else: verify(args.url.rstrip("/"), args.output)
