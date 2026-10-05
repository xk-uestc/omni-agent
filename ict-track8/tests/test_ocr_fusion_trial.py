"""Failure-focused tests for the offline OCR fusion prototype."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from ocr_fusion_trial import field_present, needs_quality_pass, table_arithmetic, verify_rows


def node(text, x, y, confidence=0.99):
    return {"text": text, "bbox": [x, y, x + 80, y + 20], "confidence": confidence}


class FusionTrialTests(unittest.TestCase):
    def test_line_boundary_does_not_hide_existing_currency(self):
        self.assertTrue(field_present("$47,000", "November 2016\n$47,000\nPersonnel"))
        self.assertFalse(field_present("$47,000", "November 2016\n$47,001"))

    def test_router_uses_observable_uncertainty(self):
        self.assertEqual(needs_quality_pass({"nodes": [node("S100,000", 200, 20)]})[1], ["ambiguous_currency_glyph"])
        self.assertEqual(needs_quality_pass({"nodes": [node("$0", 200, 20, 0.55)]})[1], ["low_line_confidence"])
        self.assertFalse(needs_quality_pass({"nodes": [node("$100,000", 200, 20)]})[0])

    def test_row_requires_distinct_correctly_aligned_boxes(self):
        rows = [["人员工资", "47,000.00"]]
        good = [node("人员工资", 20, 100), node("47,000.00", 240, 100)]
        self.assertEqual(verify_rows(rows, good)[0]["status"], "observed_pair")
        self.assertEqual(verify_rows(rows, [good[0], node("47,000.00", 240, 180)])[0]["status"], "unverified")
        self.assertEqual(verify_rows(rows, [good[0], node("47,000.00", 0, 100)])[0]["status"], "unverified")
        self.assertEqual(verify_rows(rows, [good[0]])[0]["status"], "unverified")

    def test_arithmetic_does_not_promote_unsupported_cells(self):
        rows = [{"cells": ["A", "47,000.00"], "status": "observed_pair"},
                {"cells": ["B", "-500.00"], "status": "observed_pair"},
                {"cells": ["合计", "46,500.00"], "status": "observed_pair"}]
        self.assertEqual(table_arithmetic(rows)["status"], "consistent")
        rows[-1]["cells"][1] = "47,500.00"
        self.assertEqual(table_arithmetic(rows)["status"], "conflict")
        rows[-1]["status"] = "unverified"
        self.assertEqual(table_arithmetic(rows)["status"], "unverified_rows")
        rows[-1]["status"] = "observed_pair"
        rows[-1]["cells"][1] = "46,500.00"
        rows.insert(0, {"cells": ["项目名称", "金额（元）"], "status": "observed_pair"})
        self.assertEqual(table_arithmetic(rows)["status"], "consistent")
        rows.insert(2, {"cells": ["unknown", "not a number"], "status": "observed_pair"})
        self.assertEqual(table_arithmetic(rows)["status"], "non_numeric_cell")


if __name__ == "__main__":
    unittest.main()
