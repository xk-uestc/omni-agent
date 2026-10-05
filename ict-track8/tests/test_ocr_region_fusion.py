from copy import deepcopy

import pytest
from PIL import Image

from backend.image_geometry import IDENTITY, inverse, point, rotation_affine
from backend.ocr_region_fusion import (amount_key, map_nodes, observed_column_totals,
    plan_regions, reconcile, rectify_region, spatial_consensus)


def node(text, x=100, y=100, confidence=.99):
    return {"text": text, "bbox": [x, y, x + 90, y + 25], "confidence": confidence}


def readers(text, sha="a", engines=("rapid", "paddle")):
    return [{"engine": engine, "text": text, "confidence": .99, "crop_sha256": sha} for engine in engines]


def test_currency_sign_and_thousands_do_not_collapse():
    assert amount_key("￥ -1,234.50")[1] < 0
    assert amount_key("(1,234.50)")[1] < 0
    assert amount_key("1,000") != amount_key("10,000")
    assert amount_key("1,00") is None
    assert amount_key("6.12 5") is None


def test_three_agreeing_readers_cannot_turn_thousands_into_decimals():
    result = reconcile("(2,298)", readers("(2.298)", engines=("rapid", "paddle", "tesseract")), crop_sha256="a")
    assert result["status"] == "needs_review"
    assert result["selected_text"] == "(2,298)"


def test_two_same_engine_views_are_not_two_votes():
    result = reconcile("S100,000", readers("$100,000", engines=("paddle", "paddle")), crop_sha256="a")
    assert result["status"] == "needs_review"


def test_bound_crop_consensus_can_repair_malformed_currency():
    result = reconcile("S100,000", readers("$100,000", engines=("paddle", "tesseract")), crop_sha256="a")
    assert result["status"] == "corrected"
    wrong_crop = reconcile("S100,000", readers("$100,000", sha="b"), crop_sha256="a")
    assert wrong_crop["status"] == "needs_review"


def test_confident_dissent_preserves_source_literal():
    observations = readers("$100,000") + readers("$10,000", engines=("tesseract",))
    result = reconcile("S100,000", observations, crop_sha256="a")
    assert result["status"] == "needs_review"


def test_spatially_different_cells_cannot_support_a_split():
    geometry = {"crop_to_source": IDENTITY, "crop_to_working": IDENTITY, "source_sha256": "source"}
    first = {"sha256": "a", "nodes": [node("52,774", y=10)]}
    second = {"sha256": "a", "nodes": [node("52,774", y=90)]}
    assert spatial_consensus("52,774 $", first, second, crop_sha256="a", geometry=geometry)["status"] == "needs_review"
    second["nodes"] = [node("52,774", y=11)]
    result = spatial_consensus("52,774 $", first, second, crop_sha256="a", geometry=geometry)
    assert result["status"] == "split_corrected"
    assert result["nodes"][0]["source_geometry"]["source_sha256"] == "source"


def test_rotation_and_crop_round_trip_original_pixels():
    transform = inverse(rotation_affine(90, [600, 400], [400, 600]))
    crop, geometry = rectify_region(Image.new("RGB", (400, 600), "white"), node("$100", x=80, y=90),
                                   working_to_source=transform, source_sha256="a" * 64)
    assert geometry["source_sha256"] == "a" * 64
    for xy in ([0, 0], [crop.width, crop.height]):
        original = point(geometry["crop_to_source"], xy)
        recovered = point(inverse(geometry["crop_to_source"]), original)
        assert recovered == pytest.approx(xy, abs=1e-5)


def test_review_budget_keeps_unprocessed_items_visible():
    plan = plan_regions([node("S100,000", y=i * 30) for i in range(13)], maximum=4)
    assert len(plan["requests"]) == 4
    assert len(plan["deferred"]) == 9


def test_incorrect_total_is_flagged_without_changing_numbers():
    nodes = [node("100.00", 300, 20), node("200.00", 300, 70), node("-50.00", 300, 120),
             node("合计", 10, 180), node("999.00", 300, 180)]
    nodes = map_nodes(nodes, IDENTITY, "source")
    before = deepcopy(nodes)
    checks = observed_column_totals(nodes)
    assert checks[0]["status"] == "observed_sum_conflict"
    assert nodes == before
