import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_site


class GoldDashboardDataTest(unittest.TestCase):
    def test_reads_refreshed_workbook_into_driver_layers(self):
        dashboard = build_site.read_dashboard_data()
        layers = {layer["id"]: layer for layer in dashboard["layers"]}

        self.assertEqual(
            set(layers),
            {
                "real_rate",
                "dollar",
                "inflation_expectation",
                "official_reserves",
                "positioning_technical",
            },
        )

        self.assertEqual(layers["real_rate"]["latest"]["date"], "2026-06-24")
        self.assertAlmostEqual(layers["real_rate"]["latest"]["value"], 2.23)
        self.assertEqual(layers["real_rate"]["data_quality"], "fresh")

        self.assertEqual(layers["dollar"]["latest"]["date"], "2026-06-24")
        self.assertAlmostEqual(layers["dollar"]["latest"]["value"], 101.5745)

        self.assertEqual(layers["official_reserves"]["latest"]["date"], "2026-06-30")
        self.assertAlmostEqual(
            layers["official_reserves"]["latest"]["china_reserves"],
            2321.5452,
        )

        self.assertEqual(layers["inflation_expectation"]["data_quality"], "fresh")
        self.assertIsNotNone(layers["inflation_expectation"]["latest"]["value"])
        self.assertIn("FRED: T10YIE", layers["inflation_expectation"]["source"])

        positioning = layers["positioning_technical"]
        self.assertEqual(positioning["data_quality"], "fresh")
        self.assertIn("CFTC", positioning["source"])
        self.assertIsNotNone(positioning["latest"]["managed_money_net"])
        self.assertIsNotNone(positioning["latest"]["managed_money_net_percentile"])

    def test_html_is_data_driven_and_excludes_opinion_summary_text(self):
        dashboard = build_site.read_dashboard_data()
        html = build_site.build_html(dashboard)

        self.assertIn("黄金数据驱动跟踪", html)
        self.assertIn("实际利率", html)
        self.assertIn("美元", html)
        self.assertIn("通胀预期", html)
        self.assertIn("央行购金", html)
        self.assertIn("仓位与技术", html)
        self.assertIn("Managed Money", html)
        self.assertIn("失效条件", html)
        self.assertIn("下一观察点", html)
        self.assertIn("数据质量", html)
        self.assertNotIn("后续接入", html)

        self.assertNotIn("观点汇总", html)
        self.assertNotIn("短期金价仍偏向震荡行情", html)
        self.assertNotIn("整体偏谨慎", html)

    def test_relationships_explain_factor_usefulness_by_phase(self):
        dashboard = build_site.read_dashboard_data()
        relationships = {item["id"]: item for item in dashboard["relationships"]}

        self.assertEqual(
            set(relationships),
            {
                "central_bank_purchases",
                "real_rate",
                "dollar",
                "inflation_expectation",
                "positioning_technical",
            },
        )

        central_bank = relationships["central_bank_purchases"]
        self.assertIsNotNone(central_bank["start_month"])
        self.assertGreaterEqual(len(central_bank["phases"]), 3)
        self.assertIsNotNone(central_bank["latest_corr"])
        self.assertTrue(central_bank["rolling_corr"])

        real_rate = relationships["real_rate"]
        self.assertIn("short_term", real_rate)
        self.assertIn("medium_term", real_rate)
        self.assertIsNotNone(real_rate["short_term"]["latest_corr"])
        self.assertIsNotNone(real_rate["medium_term"]["latest_corr"])
        self.assertGreaterEqual(len(real_rate["phases"]), 3)

        for relationship_id in ["dollar", "inflation_expectation"]:
            relationship = relationships[relationship_id]
            self.assertIn("short_term", relationship)
            self.assertIn("medium_term", relationship)
            self.assertIsNotNone(relationship["short_term"]["latest_corr"])
            self.assertIsNotNone(relationship["medium_term"]["latest_corr"])
            self.assertGreaterEqual(len(relationship["phases"]), 3)

        positioning = relationships["positioning_technical"]
        sub_metrics = {item["id"]: item for item in positioning["sub_metrics"]}
        self.assertEqual(set(sub_metrics), {"etf_holdings", "managed_money", "gvz"})
        for sub_metric in sub_metrics.values():
            self.assertIsNotNone(sub_metric["latest_corr"])
            self.assertTrue(sub_metric["rolling_corr"])
            self.assertGreaterEqual(len(sub_metric["phases"]), 3)

        html = build_site.build_html(dashboard)
        self.assertIn("因子关系检验", html)
        self.assertIn("什么时候开始显著推动", html)
        self.assertIn("滚动相关", html)
        self.assertIn("阶段表现", html)
        self.assertIn("实际利率短期", html)
        self.assertIn("实际利率中期", html)
        self.assertIn("美元短期", html)
        self.assertIn("通胀预期短期", html)
        self.assertIn("ETF 持仓", html)
        self.assertIn("Managed Money 净多", html)
        self.assertIn("GVZ 波动率", html)

    def test_charts_show_axes_and_underlying_series(self):
        dashboard = build_site.read_dashboard_data()
        html = build_site.build_html(dashboard)

        self.assertIn('class="y-axis"', html)
        self.assertIn('class="x-axis"', html)
        self.assertIn('aria-label="央行购金环比柱状图"', html)
        self.assertIn('class="mini-bar-chart"', html)

        self.assertIn("双轴走势", html)
        self.assertIn('class="dual-axis-chart"', html)
        self.assertIn('class="right-axis"', html)
        self.assertIn("黄金价格", html)
        self.assertIn("滚动相关", html)


from datetime import date as _date


class PriceTrendTest(unittest.TestCase):
    def _rising(self, n=700, start=1000.0, step=5.0):  # 需 > 200(MA)+63(前瞻)+252(滚动) 才有非空相关
        from datetime import date, timedelta
        d0 = date(2025, 1, 1)
        return [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
                 "_date": d0 + timedelta(days=i),
                 "gold_price": start + step * i} for i in range(n)]

    def test_uptrend_is_supportive(self):
        layer = build_site.make_price_trend_layer(self._rising())
        self.assertEqual(layer["id"], "price_trend")
        self.assertEqual(layer["frequency"], "daily")
        self.assertEqual(layer["state"], "supportive")

    def test_trend_relationship_uses_forward_returns(self):
        rel = build_site.make_trend_relationship(self._rising())
        self.assertIn("short_term", rel)
        self.assertIn("medium_term", rel)
        self.assertGreater(rel["short_term"]["latest_corr"], 0)


class StalenessTest(unittest.TestCase):
    def test_daily_boundaries(self):
        today = _date(2026, 6, 25)
        self.assertEqual(build_site.classify_staleness("2026-06-23", today, "daily"), ("fresh", 2))
        self.assertEqual(build_site.classify_staleness("2026-06-15", today, "daily"), ("stale", 10))
        self.assertEqual(build_site.classify_staleness("2026-05-29", today, "daily"), ("very-stale", 27))

    def test_monthly_and_missing(self):
        today = _date(2026, 6, 25)
        self.assertEqual(build_site.classify_staleness("2026-05-31", today, "monthly")[0], "fresh")
        self.assertEqual(build_site.classify_staleness(None, today, "monthly"), ("missing", None))

    def test_future_dated_is_clamped_to_zero(self):
        # 月频常盖到月末(未来日期),滞后不应为负
        self.assertEqual(
            build_site.classify_staleness("2026-06-30", _date(2026, 6, 25), "monthly"), ("fresh", 0))


if __name__ == "__main__":
    unittest.main()
