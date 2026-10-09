"""Allowlisted image references for the imported Raysource manual samples."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IMAGE_ROOT = ROOT / "ict-track8" / "data" / "raysource-images"
_IMAGE_NAME = re.compile(r"^Manual\d+_\d+\.jpg$", re.IGNORECASE)
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\((?:[^)]*[/\\])?([^/\\)]+)\)")
_PIC_ANCHOR = re.compile(r"\[\[PIC:([^\]]+)\]\]", re.IGNORECASE)


@lru_cache(maxsize=1)
def _catalog():
    manifest_path = ROOT / "samples" / "raysource-rag-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    catalog = {}
    sample_root = ROOT / "samples" / "raysource-rag"
    for item in manifest.get("files", []):
        document_id = item.get("document_id")
        filename = item.get("filename")
        if not isinstance(document_id, str) or not isinstance(filename, str) or Path(filename).name != filename:
            continue
        try:
            text = (sample_root / filename).read_text(encoding="utf-8")
        except OSError:
            continue
        refs = {}
        for match in _MARKDOWN_IMAGE.finditer(text):
            name = match.group(2)
            if not _IMAGE_NAME.fullmatch(name):
                continue
            image_id = Path(name).stem
            image_path = IMAGE_ROOT / name
            if image_path.is_file() and not image_path.is_symlink() and image_path.resolve().parent == IMAGE_ROOT.resolve():
                alt = match.group(1).strip()
                if not alt or _IMAGE_NAME.fullmatch(alt + ".jpg"):
                    alt = "手册配图"
                refs[image_id] = {"filename": name, "alt": alt}
        if refs:
            catalog[document_id] = refs
    return catalog


def image_refs_for_chunk(document_id: str, text: str) -> list[dict[str, str]]:
    allowed = _catalog().get(document_id, {})
    if not allowed or not isinstance(text, str):
        return []
    referenced = []
    for match in _MARKDOWN_IMAGE.finditer(text):
        name = match.group(2)
        image_id = Path(name).stem if _IMAGE_NAME.fullmatch(name) else ""
        if image_id in allowed and image_id not in referenced:
            referenced.append(image_id)
    for match in _PIC_ANCHOR.finditer(text):
        image_id = Path(match.group(1)).stem
        if _IMAGE_NAME.fullmatch(image_id + ".jpg") and image_id in allowed and image_id not in referenced:
            referenced.append(image_id)
    return [
        {"image_id": image_id,
         "url": f"/api/v1/knowledge/raysource-images/{image_id}",
         "alt": allowed[image_id]["alt"] or "手册原图"}
        for image_id in referenced[:8]
    ]


def image_path(image_id: str) -> Path | None:
    if not isinstance(image_id, str) or not re.fullmatch(r"Manual\d+_\d+", image_id, re.IGNORECASE):
        return None
    filename = image_id + ".jpg"
    if not any(image_id in refs for refs in _catalog().values()):
        return None
    path = IMAGE_ROOT / filename
    if not path.is_file() or path.is_symlink() or path.resolve().parent != IMAGE_ROOT.resolve():
        return None
    return path
