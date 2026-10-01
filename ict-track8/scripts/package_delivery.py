"""构建赛题八的可重建源码交付包。

包只包含代码、测试、文档和小型可复现夹具；运行时 SQLite、缓存、日志、备份、
报告和密钥均被排除。包内 ``MANIFEST.json`` 保存 SHA-256，便于下载后验收。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "ict-track8/README.md",
    "ict-track8/requirements.txt",
    "ict-track8/backend/app.py",
    "ict-track8/backend/nl2sql/engine.py",
    "ict-track8/backend/nl2sql/model_contract.py",
    "ict-track8/scripts/create_demo_db.py",
    "ict-track8/scripts/run_industry_eval.py",
    "ict-track8/scripts/verify_package.py",
    "ict-track8/eval/run_all.ps1",
    "ict-track8/tests/test_industry_evaluation.py",
    "docs/ICT_TRACK8_REQUIREMENTS.md",
    "docs/ICT_TRACK8_TECHNICAL_SPEC.md",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", ".claude", "dist", "runtime", "backups", ".venv", "node_modules"}
EXCLUDED_SUFFIXES = {".sqlite", ".sqlite3", ".log", ".pyc", ".zip"}
EXCLUDED_NAMES = {"industry-eval-report.json", "eval-report.json"}
EXCLUDED_REPORT_PATTERNS = [
    "current-eval-*",
    "generated_seed*_after_current.json",
    "round3-*",
    "*-after.json",
    "*-after-current.json",
    "*-after-final.json",
    "*-compare.md",
    "*-security.json",
    "*-security-round.json",
]


def _is_runtime_report(path: Path) -> bool:
    """Exclude generated evaluation output while retaining reproducible inputs."""

    name = path.name.lower()
    return (
        name.startswith("current-eval-")
        or name.startswith("generated_seed") and name.endswith("_after_current.json")
        or name.startswith("round3-")
        or name.endswith("-after.json")
        or name.endswith("-after-current.json")
        or name.endswith("-after-final.json")
        or name.endswith("-compare.md")
        or name.endswith("-security.json")
        or name.endswith("-security-round.json")
    )


def collect_files(root: Path = ROOT, *, include_public_assets: bool = False) -> list[Path]:
    files: list[Path] = []
    directories = [root / name for name in ("ict-track8", "docs", "tools", "samples", "specification", "scripts")]
    if include_public_assets:
        directories.extend([root / "models", root / "benchmarks"])
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            public_database = include_public_assets and relative.as_posix() == 'benchmarks/chinook/Chinook.sqlite'
            if (path.suffix.lower() in EXCLUDED_SUFFIXES and not public_database) or path.name in EXCLUDED_NAMES:
                continue
            if path.name.startswith(".env") or path.name in {"auth.json", "config.toml"} or path.suffix.lower() in {".pem", ".key"}:
                continue
            if _is_runtime_report(path):
                continue
            files.append(path)
    for name in ("README.md", "AGENTS.md", ".gitignore"):
        if (root / name).is_file():
            files.append(root / name)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def build_package(output: Path, *, root: Path = ROOT, include_public_assets: bool = False) -> dict[str, object]:
    files = collect_files(root, include_public_assets=include_public_assets)
    if include_public_assets:
        for directory in ('models/bge-small-zh-v1.5', 'benchmarks/chinook'):
            asset_manifest = json.loads((root / directory / 'ASSET_MANIFEST.json').read_text(encoding='utf-8'))
            for asset in asset_manifest['files']:
                path = root / asset['path']
                if path not in files or hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
                    raise ValueError('公开资产缺失或哈希不符：' + asset['path'])
    missing = [item for item in REQUIRED if not (root / item).is_file()]
    if missing:
        raise FileNotFoundError("交付包缺少必需文件: " + ", ".join(missing))
    output.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    # Publish only a closed archive: concurrent readers must not see a partial ZIP.
    staging = output.with_name(output.name + '.' + uuid.uuid4().hex + '.building')
    with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            archive.writestr(relative, data)
            entries.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {
            "format": 1,
            "package": "ict-track8-source",
            "public_assets_included": include_public_assets,
            # 不写入本机绝对路径，避免交付包泄露工作区布局并保持可复现。
            "generated_from": "ict-track8-source-tree",
            "file_count": len(entries),
            "files": entries,
            "excluded": [
                "*.sqlite", "*.log", "*.zip", "__pycache__", ".pytest_cache", ".ruff_cache", "dist/",
                "industry-eval-report.json", *EXCLUDED_REPORT_PATTERNS,
            ],
        }
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    staging.replace(output)
    return {"output": str(output), "file_count": len(entries), "bytes": output.stat().st_size, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "ict-track8-source.zip")
    parser.add_argument('--with-public-assets', action='store_true', help='包含本地Dense模型权重、公开Chinook库与许可，不包含任何运行凭据')
    args = parser.parse_args()
    report = build_package(args.output, include_public_assets=args.with_public_assets)
    print(json.dumps({key:value for key,value in report.items() if key != 'manifest'}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
