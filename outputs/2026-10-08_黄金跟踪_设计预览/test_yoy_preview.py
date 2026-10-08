"""Behavior checks for the common-month gold and fixed-FX M2 YoY overlay."""
import calendar
import copy
import csv
import math
import sys
import unittest
from datetime import date

import extend_preview as preview

sys.path.insert(0, str(preview.HERE.parents[1]))
from scripts import build_site as builder


def fixtures(length=20):
    money, gold = [], []
    for i in range(length):
        total = 2025 * 12 + i
        year, month = total // 12, total % 12 + 1
        when = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        money.append({"date": when, "total_fixed_usd_bn": 1000 + 10 * i})
        gold.append({"date": when, "gold_price": 100 + 2 * i})
    return money, gold


class YoyPreviewTests(unittest.TestCase):
    def test_same_month_arithmetic_percent_without_artificial_baseline(self):
        money, gold = fixtures()
        rows, comparison = preview.liquidity_yoy_comparison(money, gold, date(2026, 10, 8))
        self.assertEqual(comparison["start"], "2026-01-31")
        self.assertEqual(comparison["date"], "2026-08-31")
        self.assertEqual(comparison["n"], 8)
        self.assertAlmostEqual(rows[0][1], 12)
        self.assertAlmostEqual(comparison["goldRows"][0][1], 24)
        self.assertEqual([r[0] for r in rows], [r[0] for r in comparison["goldRows"]])
        self.assertAlmostEqual(comparison["moneyValue"], (1190 / 1070 - 1) * 100)
        self.assertAlmostEqual(comparison["goldValue"], (138 / 114 - 1) * 100)
        # No 2020-12 or 100-index baseline is needed for a YoY percentage.
        scaled = [{**row, "total_fixed_usd_bn": row["total_fixed_usd_bn"] * 1000} for row in money]
        other_rows, _ = preview.liquidity_yoy_comparison(scaled, gold, date(2026, 10, 8))
        for original, other in zip(rows, other_rows):
            self.assertEqual(original[0], other[0])
            self.assertAlmostEqual(original[1], other[1], places=12)

    def test_missing_interior_month_prevents_endpoint_only_yoy(self):
        money, gold = fixtures()
        missing_gold = copy.deepcopy(gold)
        missing_gold[10]["gold_price"] = None
        rows, comparison = preview.liquidity_yoy_comparison(money, missing_gold, date(2026, 10, 8))
        self.assertEqual(rows, [])
        self.assertEqual(comparison["goldRows"], [])
        self.assertIsNone(comparison["goldValue"])
        self.assertIsNone(comparison["moneyValue"])
        missing_money = [row for i, row in enumerate(money) if i != 10]
        self.assertEqual(preview.liquidity_yoy_comparison(missing_money, gold, date(2026, 10, 8))[0], [])
        invalid_money = copy.deepcopy(money)
        invalid_money[10]["total_fixed_usd_bn"] = float("nan")
        self.assertEqual(preview.liquidity_yoy_comparison(invalid_money, gold, date(2026, 10, 8))[0], [])

    def test_month_end_quote_cutoff_and_future_months(self):
        money, gold = fixtures(22)
        for row in gold:
            if row["date"] == "2026-08-31":
                row["date"] = "2026-08-23"  # Eight days before month end is stale.
        rows, comparison = preview.liquidity_yoy_comparison(money, gold, date(2026, 10, 8))
        self.assertEqual(comparison["date"], "2026-07-31")
        self.assertTrue(all(when < "2026-08-01" and math.isfinite(value) for when, value in rows))

    def test_snapshot_field_metadata_and_real_data_reconciliation(self):
        snapshot = {"snapshotDate": "2026-10-08", "metrics": [{"id": f"base_{i}"} for i in range(12)]}
        result = preview.extend_snapshot(snapshot, builder)
        self.assertEqual(len(result["metrics"]), 20)
        metric = next(m for m in result["metrics"] if m["id"] == "liquidity_yoy")
        self.assertEqual((metric["unit"], metric["frequency"], metric["state"], metric["researchId"]), ("%", "monthly", "context", "liquidity"))
        self.assertEqual(metric["key"], "m2_fixed_fx_yoy_pct")
        self.assertEqual(metric["sourceKey"], "total_fixed_usd_bn")
        comparison = result["liquidityComparison"]
        self.assertEqual(comparison["date"], "2026-08-31")
        self.assertEqual(comparison["start"], "2004-01-31")
        self.assertEqual(comparison["n"], 272)
        self.assertEqual([r[0] for r in metric["rows"]], [r[0] for r in comparison["goldRows"]])
        with (preview.RESEARCH / "liquidity_monthly.csv").open() as source:
            money = {r["date"]: float(r["total_fixed_usd_bn"]) for r in csv.DictReader(source)}
        expected = (money["2026-08-31"] / money["2025-08-31"] - 1) * 100
        self.assertAlmostEqual(metric["value"], expected, places=12)
        self.assertAlmostEqual(comparison["moneyValue"], expected, places=12)
        self.assertAlmostEqual(comparison["goldValue"], 29.022384498107655, places=10)
        self.assertEqual(comparison["goldFieldMeta"]["key"], "gold_yoy_pct")
        self.assertEqual(comparison["goldFieldMeta"]["sourceKey"], "gold_price")
        self.assertTrue(all(math.isfinite(value) for _, value in metric["rows"] + comparison["goldRows"]))


if __name__ == "__main__":
    unittest.main()
