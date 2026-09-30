from __future__ import annotations

import json
import hashlib
import sqlite3
import stat
import subprocess
import sys
import zipfile

from scripts.package_delivery import build_package
from scripts.verify_package import verify_package


def test_delivery_package_is_reconstructable_and_excludes_runtime_artifacts(tmp_path):
    output = tmp_path / "ict8.zip"
    report = build_package(output)
    assert report["file_count"] > 20
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("MANIFEST.json"))
    assert "ict-track8/backend/app.py" in names
    assert "docs/ICT_TRACK8_REQUIREMENTS.md" in names
    assert "MANIFEST.json" in names
    assert not any(name.endswith((".sqlite", ".log", ".pyc")) for name in names)
    assert not any(".ruff_cache/" in name or name.startswith(".ruff_cache/") for name in names)
    assert not any(name.endswith(".zip") or "/dist/" in name for name in names)
    assert not any("current-eval-" in name or "/round3-" in name for name in names)
    assert not any(name.endswith("-after.json") or name.endswith("-compare.md") for name in names)
    assert manifest["file_count"] == report["file_count"]
    assert manifest["generated_from"] == "ict-track8-source-tree"
    assert verify_package(output)["ok"]


def test_package_verifier_rejects_tampered_member(tmp_path):
    output = tmp_path / "ict8.zip"
    build_package(output)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "ict-track8/README.md":
                data += b"\nTAMPERED"
            target.writestr(info, data)
    result = verify_package(tampered)
    assert not result["ok"]
    assert any("sha256 mismatch" in error for error in result["errors"])


def test_package_verifier_rejects_duplicate_manifest_path(tmp_path):
    output = tmp_path / "ict8.zip"
    build_package(output)
    tampered = tmp_path / "duplicate-manifest.zip"
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "MANIFEST.json":
                manifest = json.loads(data)
                manifest["files"].append(dict(manifest["files"][0]))
                data = json.dumps(manifest).encode()
            target.writestr(info, data)
    result = verify_package(tampered)
    assert not result["ok"]
    assert any("duplicate manifest path" in error for error in result["errors"])


def test_package_verifier_rejects_symlink_member(tmp_path):
    output = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, "target")
        archive.writestr("MANIFEST.json", json.dumps({"files": []}))
    result = verify_package(output)
    assert not result["ok"]
    assert any("symlink member" in error for error in result["errors"])


def test_package_verifier_rejects_secret_like_text(tmp_path):
    output = tmp_path / "secret.zip"
    payload = b"OPENAI_API_KEY=" + b"sk-" + b"123456789012345678901234567890"
    manifest = {
        "format": 1,
        "package": "ict-track8-source",
        "file_count": 1,
        "files": [{
            "path": "ict-track8/README.md",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("ict-track8/README.md", payload)
        archive.writestr("MANIFEST.json", json.dumps(manifest))
    result = verify_package(output)
    assert not result["ok"]
    assert any("secret-like content" in error for error in result["errors"])


def test_delivery_package_rebuilds_demo_database_and_executes_query(tmp_path, monkeypatch):
    output = tmp_path / "ict8.zip"
    build_package(output)
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(output) as archive:
        archive.extractall(unpacked)

    package_root = unpacked / "ict-track8"
    monkeypatch.syspath_prepend(str(package_root))
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.nl2sql.seed import initialize_database

    database = initialize_database(package_root / "data" / "demo_sales.sqlite")
    result = Nl2SqlEngine(database).answer("2025年华东地区的销售额是多少")
    assert result.status == "ok"
    assert result.rows and result.rows[0]["销售额"] == 29584.0
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0] > 0


def test_delivery_package_runs_evaluator_from_extracted_tree(tmp_path):
    output = tmp_path / "ict8.zip"
    build_package(output)
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(output) as archive:
        archive.extractall(unpacked)

    package_root = unpacked / "ict-track8"
    cases = json.loads((package_root / "eval/cases/nl2sql_v2.json").read_text(encoding="utf-8"))
    selected = []
    selected_categories = set()
    wanted = {"legacy", "ambiguity", "complex"}
    for case in cases["cases"]:
        category = case["category"]
        is_complex = bool({"join", "nested", "compare", "window", "multi_table"} & set(case.get("tags", [])))
        bucket = "complex" if is_complex else category
        if bucket in wanted - selected_categories and case["expected"] in {"ok", "clarification"}:
            selected.append(case)
            selected_categories.add(bucket)
        if selected_categories == wanted:
            break
    assert selected_categories == wanted
    smoke_cases = package_root / "eval" / "smoke-cases.json"
    smoke_cases.write_text(json.dumps({**cases, "cases": selected}, ensure_ascii=False), encoding="utf-8")
    report_path = tmp_path / "smoke-report.json"
    completed = subprocess.run(
        [sys.executable, str(package_root / "eval/run_eval.py"), "--repo", str(package_root),
         "--cases", str(smoke_cases), "--out", str(report_path)],
        cwd=unpacked,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]
    assert summary["units"] >= 3
    assert len(report["records"]) == summary["units"]
    assert summary["EX_answerable"] == 1.0
    assert summary["silent_error_rate"] == 0
    assert summary["safety_violations"] == 0
    assert summary["database_files_modified"] == []
