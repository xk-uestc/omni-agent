"""Protect paired ablation evidence against misleading aggregate gains."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from ocr_gap_ablation import comparison, index_result, bootstrap_page_difference, metrics, verify_freeze


class GapAblationTests(unittest.TestCase):
    def test_equal_total_can_hide_a_recovery_and_a_regression(self):
        before = {"value": {"matched_reference_indices": [0, 1]}}
        after = {"value": {"matched_reference_indices": [1, 2]}}
        self.assertEqual(comparison(before, after), {"recovered": [2], "regressed": [0], "net": 0})

    def test_duplicate_same_value_at_different_location_remains_distinct(self):
        # Two reference cells may both contain 0; their indices must not be collapsed.
        before = {"value": {"matched_reference_indices": [0]}}
        after = {"value": {"matched_reference_indices": [0, 1]}}
        self.assertEqual(comparison(before, after)["recovered"], [1])

    def test_reject_wrong_hash_and_duplicate_pages_even_at_same_page_count(self):
        manifest = {"cases": [{"id": "a", "sha256": "one"}, {"id": "b", "sha256": "two"}]}
        for records in ([{"id": "a", "sha256": "one"}] * 2,
                        [{"id": "a", "sha256": "one"}, {"id": "b", "sha256": "wrong"}]):
            with self.assertRaisesRegex(RuntimeError, "incomplete_or_wrong_inputs"):
                index_result({"cases": records}, manifest)

    def test_zero_effect_cannot_produce_positive_interval(self):
        rows = [{"expected": n, "paired_local_effect": {"net": 0}} for n in (1, 20, 3)]
        self.assertEqual(bootstrap_page_difference(rows), [0, 0])
        self.assertIsNone(bootstrap_page_difference([]))

    def test_precision_is_limited_to_annotated_scope(self):
        score = {"candidate_count_in_annotated_numeric_scope": 2,
                 "ignored_candidates_outside_numeric_scope": 30}
        for key in ("value", "literal", "tight_value"):
            score[key] = {"correct": 1}
        result = metrics([{"numeric_cells": [{}, {}, {}, {}], "scores": {"fast": score}}], "fast")
        self.assertEqual(result["value"]["recall"], .25)
        self.assertEqual(result["value"]["precision_in_annotated_numeric_scope"], .5)
        self.assertEqual(result["outside_scope_candidates"], 30)

    def test_resume_rejects_changed_image_even_if_manifest_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "image.png"
            image.write_bytes(b"original image")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"cases": [{"path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}]}), encoding="utf-8")
            (root / "ablation-freeze.json").write_text(json.dumps({"manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "implementation_sha256": {}}), encoding="utf-8")
            verify_freeze(root)
            image.write_bytes(b"different image")
            with self.assertRaisesRegex(RuntimeError, "input_changed"):
                verify_freeze(root)


if __name__ == "__main__":
    unittest.main()
