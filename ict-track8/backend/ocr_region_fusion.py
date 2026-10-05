"""Budgeted OCR region review with source-bound evidence and abstention.

This experimental module is intentionally independent of the production pipeline.
Numbers are replaced only when two readers agree on the same observed crop;
arithmetic can request review but never manufacture a replacement.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
import unicodedata

from .image_geometry import compose, inverse, point, projective, rotation_affine


def clean(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).replace("−", "-")


def amount_key(value):
    raw = unicodedata.normalize("NFKC", str(value)).strip()
    # Do not glue two neighboring numeric cells: "6.12 5" is not 6.125.
    if re.search(r"\d\s+\d", raw):
        return None
    text = clean(value)
    # An ungrouped integer or correctly grouped thousands; no digit guessing.
    match = re.fullmatch(r"(?P<currency>[$¥￥]?)(?P<number>-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)(?:元)?", text)
    negative_parentheses = False
    if not match and text.startswith("(") and text.endswith(")"):
        negative_parentheses = True
        text = text[1:-1]
        match = re.fullmatch(r"(?P<currency>[$¥￥]?)(?P<number>(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)", text)
    if not match:
        return None
    number = Decimal(match["number"].replace(",", ""))
    if negative_parentheses:
        number = -number
    currency = "¥" if match["currency"] in {"¥", "￥"} else match["currency"]
    # Decimal equality preserves values without mistaking 1,000 for 10,000.
    return currency, number


def numeric_like(value):
    text = clean(value)
    return bool(len(text) <= 32 and re.search(r"\d", text)
                and re.fullmatch(r"[$¥￥SsOo()\-+\d,.元]+", text)
                and (re.search(r"[$¥￥SsOo,.()元]", text) or text.startswith("-")))


def plan_regions(nodes, *, maximum=10):
    requests = []
    for index, node in enumerate(nodes):
        text = node.get("text", "")
        if not numeric_like(text):
            continue
        reasons = []
        if amount_key(text) is None:
            reasons.append("invalid_amount_literal")
        if float(node.get("confidence", 1)) < .94:
            reasons.append("uncertain_amount_line")
        if reasons:
            requests.append({"node_index": index, "original_text": text, "reasons": reasons,
                             "priority": (2 if "invalid_amount_literal" in reasons else 0)
                                         + 1 - float(node.get("confidence", 1))})
    requests.sort(key=lambda item: item["priority"], reverse=True)
    return {"requests": requests[:maximum], "deferred": requests[maximum:], "maximum": maximum}


def reconcile(original_text, observations, *, crop_sha256):
    grouped = defaultdict(dict)
    rejected = []
    for observation in observations:
        key = amount_key(observation.get("text", ""))
        if observation.get("crop_sha256") != crop_sha256:
            rejected.append({**observation, "reason": "crop_identity_mismatch"})
            continue
        minimum = .5 if observation.get("engine") == "tesseract" else .8
        if key is None or observation.get("error") or float(observation.get("confidence", 0)) < minimum:
            rejected.append({**observation, "reason": "not_reliable_numeric_observation"})
            continue
        engine = observation["engine"]
        previous = grouped[key].get(engine)
        if previous is None or observation["confidence"] > previous["confidence"]:
            grouped[key][engine] = observation
    supported = [(key, readers) for key, readers in grouped.items() if len(readers) >= 2]
    result = {"status": "needs_review", "original_text": original_text, "selected_text": original_text,
              "crop_sha256": crop_sha256, "observations": observations, "rejected_observations": rejected,
              "confidence_kind": "reader_agreement_not_calibrated_accuracy"}
    if len(supported) != 1:
        result["reason"] = "no_unique_two_reader_agreement"
        return result
    key, readers = supported[0]
    # A high confidence dissent is visible even if two other readers agree.
    dissent = [obs for other, values in grouped.items() if other != key for obs in values.values()]
    if any(float(obs.get("confidence", 0)) >= .95 for obs in dissent):
        result["reason"] = "confident_reader_conflict"
        return result
    preferred = readers.get("paddle") or next(iter(readers.values()))
    original_key = amount_key(original_text)
    if original_key is not None and original_key != key:
        result["reason"] = "valid_amount_changed_requires_context"
        result["proposed_text"] = preferred["text"]
        return result
    result.update(status="confirmed" if amount_key(original_text) == key else "corrected",
                  selected_text=preferred["text"], reason="two_reader_numeric_agreement",
                  supporting_engines=sorted(readers), dissent=dissent)
    return result


def spatial_consensus(original_text, first, second, *, crop_sha256, geometry):
    """Split a merged OCR region only with agreeing text AND source positions."""
    result = {"status": "needs_review", "reason": "no_supported_local_split", "nodes": []}
    if first.get("sha256") != crop_sha256 or second.get("sha256") != crop_sha256:
        result["reason"] = "crop_identity_mismatch"
        return result
    if first.get("error") or second.get("error"):
        result["reason"] = "region_reader_failed"
        return result
    a_nodes = [node for node in first.get("nodes", []) if amount_key(node.get("text", "")) is not None]
    b_nodes = [node for node in second.get("nodes", []) if amount_key(node.get("text", "")) is not None]
    claimed = set()
    supported = []
    for a in a_nodes:
        for index, b in enumerate(b_nodes):
            if index in claimed or amount_key(a["text"]) != amount_key(b["text"]):
                continue
            x0, y0 = max(a["bbox"][0], b["bbox"][0]), max(a["bbox"][1], b["bbox"][1])
            x1, y1 = min(a["bbox"][2], b["bbox"][2]), min(a["bbox"][3], b["bbox"][3])
            intersection = max(0., x1 - x0) * max(0., y1 - y0)
            smaller = min((a["bbox"][2]-a["bbox"][0]) * (a["bbox"][3]-a["bbox"][1]),
                          (b["bbox"][2]-b["bbox"][0]) * (b["bbox"][3]-b["bbox"][1]))
            if smaller <= 0 or intersection / smaller < .6 or min(a.get("confidence", 0), b.get("confidence", 0)) < .8:
                continue
            supported.append(b)
            claimed.add(index)
            break
    if not supported or len(supported) != len(a_nodes) or len(supported) != len(b_nodes):
        result["reason"] = "local_numeric_detection_or_value_conflict"
        return result
    if amount_key(original_text) is not None:
        result["reason"] = "valid_amount_changed_requires_context"
        return result
    if len(supported) > 3:
        result["reason"] = "too_many_cells_for_local_repair"
        return result
    mapped = map_nodes(supported, geometry["crop_to_source"], geometry["source_sha256"])
    for node in mapped:
        quad = node.get("polygon")
        if not quad:
            x0, y0, x1, y1 = node["bbox"]
            quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        work = [point(geometry["crop_to_working"], xy) for xy in quad]
        node.update(polygon=work, bbox=[min(p[0] for p in work), min(p[1] for p in work),
                                       max(p[0] for p in work), max(p[1] for p in work)])
    result.update(status="split_corrected", reason="two_readers_agree_on_local_values_and_boxes", nodes=mapped)
    return result


def map_nodes(nodes, working_to_source, source_sha256):
    result = deepcopy(nodes)
    for index, node in enumerate(result):
        quad = node.get("polygon")
        if not quad:
            x0, y0, x1, y1 = node["bbox"]
            quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        mapped = [point(working_to_source, xy) for xy in quad]
        node["source_geometry"] = {"source_sha256": source_sha256, "polygon_px": mapped,
            "bbox_px": [min(p[0] for p in mapped), min(p[1] for p in mapped),
                        max(p[0] for p in mapped), max(p[1] for p in mapped)]}
        node["node_index"] = index
    return result


def rectify_region(image, node, *, working_to_source, source_sha256):
    """Create a padded crop and the exact inverse transform to stored pixels."""
    import cv2
    import numpy as np
    from PIL import Image

    quad = node.get("polygon")
    if not quad:
        x0, y0, x1, y1 = node["bbox"]
        quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    quad = np.asarray(quad, dtype=np.float32)
    width = max(math.dist(quad[0], quad[1]), math.dist(quad[2], quad[3]))
    height = max(math.dist(quad[0], quad[3]), math.dist(quad[1], quad[2]))
    if min(width, height) < 2 or width * height > 300_000:
        raise ValueError("invalid_or_oversized_region")
    # Small expansion retains punctuation near the detector boundary.
    center = quad.mean(axis=0)
    expanded = center + (quad - center) * 1.12
    w, h = max(2, round(width * 1.12)), max(2, round(height * 1.12))
    destination = np.asarray([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    forward = cv2.getPerspectiveTransform(expanded, destination)
    array = cv2.warpPerspective(np.asarray(image.convert("RGB")), forward, (w, h),
                               flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    transform = forward.reshape(-1).tolist()
    if h / w >= 1.5:
        rotated = Image.fromarray(array).rotate(90, expand=True)
        transform = compose(rotation_affine(90, [w, h], list(rotated.size)), transform)
        image_crop = rotated
    else:
        image_crop = Image.fromarray(array)
    # Recognition-only models receive adequate line height, not invented pixels.
    if image_crop.height < 48:
        scale = 48 / image_crop.height
        resized = image_crop.resize((round(image_crop.width * scale), 48), Image.Resampling.LANCZOS)
        transform = compose([resized.width / image_crop.width, 0., 0., 0., 48 / image_crop.height, 0.], transform)
        image_crop = resized
    reverse = compose(working_to_source, inverse(transform))
    geometry = {"source_sha256": source_sha256, "crop_size_px": list(image_crop.size),
                "crop_to_source": reverse, "crop_to_working": inverse(transform), "scope": "stored_original_image_pixels",
                "source_polygon_px": [point(reverse, xy) for xy in
                    [[0, 0], [image_crop.width, 0], [image_crop.width, image_crop.height], [0, image_crop.height]]]}
    return image_crop, geometry


def apply_decisions(nodes, decisions):
    result = []
    for index, original in enumerate(nodes):
        node = deepcopy(original)
        decision = decisions.get(index)
        if decision is not None:
            node["region_review"] = deepcopy({k: v for k, v in decision.items() if k != "nodes"})
            if decision["status"] == "corrected":
                node["original_text"] = node["text"]
                node["text"] = decision["selected_text"]
            elif decision["status"] == "split_corrected":
                for replacement in decision["nodes"]:
                    replacement = deepcopy(replacement)
                    replacement.update(parent_node_index=index, original_text=node["text"], region_review=node["region_review"])
                    result.append(replacement)
                continue
        result.append(node)
    return result


def observed_column_totals(nodes):
    """Advisory geometry checks, never semantic confirmation or a repair oracle."""
    checks = []
    for label in nodes:
        if not re.fullmatch(r"(?:total|合计|总计)[:：]?", clean(label.get("text", "")), re.I):
            continue
        ly = (label["bbox"][1] + label["bbox"][3]) / 2
        lh = label["bbox"][3] - label["bbox"][1]
        totals = [node for node in nodes if amount_key(node.get("text", "")) is not None
                  and node["bbox"][0] > label["bbox"][2]
                  and abs((node["bbox"][1] + node["bbox"][3]) / 2 - ly) <= .65 * lh]
        if len(totals) != 1:
            checks.append({"status": "total_cell_ambiguous_or_missing", "label": label["text"]})
            continue
        total = totals[0]
        cx = (total["bbox"][0] + total["bbox"][2]) / 2
        candidates = [node for node in nodes if node is not total and amount_key(node.get("text", "")) is not None
                      and node["bbox"][3] < total["bbox"][3]
                      and node["bbox"][0] - 4 <= cx <= node["bbox"][2] + 4]
        candidates.sort(key=lambda node: node["bbox"][1])
        if len(candidates) < 3:
            checks.append({"status": "insufficient_column_evidence", "label": label["text"]})
            continue
        values = [amount_key(node["text"])[1] for node in candidates]
        reported = amount_key(total["text"])[1]
        checks.append({"status": "internally_consistent" if sum(values) == reported else "observed_sum_conflict",
                       "scope": "observed_column_only_not_verified_business_formula", "label": label["text"],
                       "reported_total": str(reported), "observed_sum": str(sum(values)),
                       "operand_node_indices": [node.get("node_index") for node in candidates],
                       "total_node_index": total.get("node_index")})
    return checks
