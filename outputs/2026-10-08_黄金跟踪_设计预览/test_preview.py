"""Behavior checks for the isolated preview's display-data boundary."""
import math
import unittest
from datetime import date
from build_preview import series, make_snapshot
from unittest.mock import patch


class PreviewDataTest(unittest.TestCase):
    def test_calendar_range_keeps_history_and_rejects_future(self):
        rows = [{"date": value, "x": 1.0} for value in (
            "2016-10-07", "2016-10-08", "2026-10-07", "2026-10-08", "2026-10-09")]
        result = series(rows, "x", date(2026, 10, 8))
        self.assertEqual([row[0] for row in result], ["2016-10-08", "2026-10-07", "2026-10-08"])

    def test_missing_nonfinite_and_zero_remain_distinct(self):
        values = [None, math.nan, math.inf, True, 0.0, -2.0]
        rows = [{"date": f"2026-10-0{i + 1}", "x": value} for i, value in enumerate(values)]
        result = series(rows, "x", date(2026, 10, 8))
        self.assertEqual([row[1] for row in result], [None, None, None, None, 0.0, -2.0])

    def test_leap_day_range_uses_calendar_months(self):
        rows = [{"date": "2024-02-29", "x": 2.0}]
        self.assertEqual(series(rows, "x", date(2026, 2, 28)), [["2024-02-29", 2.0]])

    def test_real_snapshot_uses_own_observations(self):
        snapshot = make_snapshot()
        self.assertGreater(len(snapshot["gold"]["rows"]), 1)
        for metric in snapshot["metrics"]:
            with self.subTest(metric=metric["id"]):
                valid = [row for row in metric["rows"] if row[1] is not None]
                self.assertTrue(valid)
                self.assertEqual([metric["date"], metric["value"]], valid[-1])
                self.assertTrue(all(row[0] <= snapshot["snapshotDate"] for row in metric["rows"]))
        metrics = {metric["id"]: metric for metric in snapshot["metrics"]}
        for mid in ("liquidity_fixed", "gold_liquidity"):
            self.assertAlmostEqual(dict(metrics[mid]["rows"])["2020-12-31"], 100.0)
        latest_money = snapshot["research"]["liquiditySources"]["latest"]
        self.assertAlmostEqual(metrics["liquidity_usd"]["value"], latest_money["total_usd_bn"] / 1000)
        self.assertEqual(metrics["liquidity_usd"]["unit"], "万亿美元")
        for mid in ("liquidity_fixed", "liquidity_usd"):
            self.assertNotEqual(metrics[mid]["key"], metrics[mid]["sourceKey"])
        self.assertNotIn("valuation", snapshot)
        self.assertEqual(len(snapshot["environment"]["groups"]), 5)
        self.assertTrue(all(metrics[mid]["state"] == "context" for mid in (
            "liquidity_fixed", "liquidity_usd", "gold_liquidity", "silver", "gold_silver_ratio", "copper", "bitcoin")))

    def test_future_dated_judgment_fails_closed(self):
        with patch("build_preview.b.read_dashboard_data", return_value={
            "layers": [{"latest": {"date": "2026-10-09"}}]
        }):
            with self.assertRaisesRegex(ValueError, "未来信息"):
                make_snapshot(today=date(2026, 10, 8))


if __name__ == "__main__":
    unittest.main()
