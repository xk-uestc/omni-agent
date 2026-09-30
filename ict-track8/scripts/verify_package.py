"""Verify an ict-track8 source ZIP without extracting or executing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import re
import zipfile
from pathlib import Path, PurePosixPath


_TEXT_SUFFIXES = {".py", ".js", ".json", ".md", ".txt", ".ps1", ".sh", ".yml", ".yaml", ".toml"}
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
)


def verify_package(package: Path) -> dict[str, object]:
    errors: list[str] = []
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            errors.append("ZIP contains duplicate member names")
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                errors.append(f"symlink member is not allowed: {info.filename}")
        if "MANIFEST.json" not in names:
            raise ValueError("MANIFEST.json is missing")
        manifest = json.loads(archive.read("MANIFEST.json"))
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("MANIFEST.json files must be a list")
        expected = set()
        for entry in entries:
            if not isinstance(entry, dict):
                errors.append(f"invalid manifest entry: {entry!r}")
                continue
            relative = entry.get("path")
            if not isinstance(relative, str) or not relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                errors.append(f"invalid manifest path: {relative!r}")
                continue
            if relative in expected:
                errors.append(f"duplicate manifest path: {relative}")
            expected.add(relative)
            if relative not in names:
                errors.append(f"missing member: {relative}")
                continue
            data = archive.read(relative)
            if entry.get("bytes") != len(data):
                errors.append(f"size mismatch: {relative}")
            if entry.get("sha256") != hashlib.sha256(data).hexdigest():
                errors.append(f"sha256 mismatch: {relative}")
            if PurePosixPath(relative).suffix.lower() in _TEXT_SUFFIXES:
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(data):
                        errors.append(f"secret-like content: {relative}")
                        break
        actual = set(names) - {"MANIFEST.json"}
        for extra in sorted(actual - expected):
            errors.append(f"unlisted member: {extra}")
        if manifest.get("file_count") != len(entries):
            errors.append("manifest file_count mismatch")
    return {"ok": not errors, "package": str(package), "file_count": len(entries), "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    result = verify_package(args.package)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
