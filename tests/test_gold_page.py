"""Behavior checks for the isolated preview's display-data boundary."""
import math
import unittest
from datetime import date
from scripts.gold_page.snapshot import series, monthly_changes, make_snapshot, render_html
from unittest.mock import patch


class FormalGoldPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = make_snapshot()

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

    def test_reserve_changes_require_adjacent_valid_months(self):
        rows = [{"date": stamp, "stock": value} for stamp, value in (
            ("2023-12-31", 100.0), ("2024-01-31", 110.0),
            ("2024-02-29", 110.0), ("2024-03-31", 105.0),
            ("2024-05-31", 120.0), ("2024-06-30", None),
            ("2024-07-31", 125.0), ("2024-08-31", math.inf),
            ("2024-09-30", 130.0), ("2024-10-31", 135.0))]
        result = monthly_changes(rows, "stock", date(2024, 9, 30))
        self.assertEqual([row[1] for row in result],
                         [None, 10.0, 0.0, -5.0, None, None, None, None, None])

    def test_reserve_change_uses_month_before_display_boundary(self):
        rows = [{"date": "2016-01-31", "stock": 100.0},
                {"date": "2016-02-29", "stock": 110.0}]
        self.assertEqual(monthly_changes(rows, "stock", date(2026, 2, 28)),
                         [["2016-02-29", 10.0]])

    def test_reserve_dual_axis_payload_reconciles_each_series(self):
        for metric in self.snapshot["metrics"]:
            if metric["id"] not in {"china_stock", "global_stock"}:
                continue
            with self.subTest(metric=metric["id"]):
                changes = dict(metric["monthlyChange"]["rows"])
                for previous, current in zip(metric["rows"], metric["rows"][1:]):
                    prior_date, current_date = map(date.fromisoformat, (previous[0], current[0]))
                    consecutive = (current_date.year * 12 + current_date.month
                                   - prior_date.year * 12 - prior_date.month == 1)
                    if consecutive and previous[1] is not None and current[1] is not None:
                        self.assertAlmostEqual(changes[current[0]], current[1] - previous[1])
                    else:
                        self.assertIsNone(changes[current[0]])
                self.assertEqual(metric["monthlyChange"]["date"], metric["date"])
                self.assertEqual(metric["monthlyChange"]["value"], changes[metric["date"]])
                self.assertEqual(metric["monthlyChange"]["unit"], "吨")

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
        with patch("scripts.gold_page.snapshot.b.read_dashboard_data", return_value={
            "layers": [{"latest": {"date": "2026-10-09"}}]
        }):
            with self.assertRaisesRegex(ValueError, "未来信息"):
                make_snapshot(today=date(2026, 10, 8))

    def test_common_day_ratio_and_yoy_reproduce_current_inputs(self):
        from scripts.refresh_gold_context import load_context
        from scripts import build_site as builder
        snapshot = self.snapshot
        money, assets, _ = load_context(date.today())
        metrics = {row["id"]: row for row in snapshot["metrics"]}
        gold = {row["date"]: row["gold_price"] for row in builder.read_csv_rows(builder.WIND_DAILY_FILE, ["gold_price"], max_date=date.today())}
        silver = {row["date"]: row["silver"] for row in assets if row["silver"] is not None}
        for stamp, ratio in metrics["gold_silver_ratio"]["rows"]:
            self.assertIn(stamp, gold)
            self.assertIn(stamp, silver)
            self.assertAlmostEqual(ratio, gold[stamp] / silver[stamp])
        fixed = {row["date"][:7]: row["total_fixed_usd_bn"] for row in money if row["total_fixed_usd_bn"]}
        for stamp, value in metrics["liquidity_yoy"]["rows"]:
            previous = f"{int(stamp[:4])-1:04d}-{stamp[5:7]}"
            self.assertAlmostEqual(value, (fixed[stamp[:7]] / fixed[previous] - 1) * 100)
        self.assertEqual([r[0] for r in metrics["liquidity_yoy"]["rows"]], [r[0] for r in snapshot["liquidityComparison"]["goldRows"]])

    def test_authoritative_template_is_self_contained_and_dated(self):
        html = render_html()
        for heading in ("核心指标走势", "风险背景", "货币背景", "关联资产参考", "官方储备的长期变化", "方法与数据"):
            self.assertIn(heading, html)
        self.assertNotIn("设计预览", html)
        self.assertNotIn("__DATA__", html)
        self.assertNotIn("__SCRIPT__", html)
        self.assertIn('id="snapshot"', html)
        self.assertIn("研究快照", html)
        self.assertIn('data-range="10y"', html)
        self.assertLessEqual(self.snapshot["research"]["asOf"], self.snapshot["snapshotDate"])



if __name__ == "__main__":
    unittest.main()
