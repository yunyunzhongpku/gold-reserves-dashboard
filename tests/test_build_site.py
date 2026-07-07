import sys
import unittest
from datetime import date as _date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_site


class GoldDashboardDataTest(unittest.TestCase):
    TODAY = _date(2026, 7, 7)

    def test_uses_latest_workbook_but_excludes_future_dated_excel_rows(self):
        original_data_file = build_site.DATA_FILE
        build_site.DATA_FILE = ROOT / "data" / "招商证券：黄金图表整理2607.xlsx"
        try:
            dashboard = build_site.read_dashboard_data(today=self.TODAY)
        finally:
            build_site.DATA_FILE = original_data_file

        self.assertIn("data/招商证券：黄金图表整理2607.xlsx", dashboard["source_file"])
        self.assertIn("data/market/official_reserves_manual.csv", dashboard["source_file"])
        layers = {layer["id"]: layer for layer in dashboard["layers"]}

        for layer_id in ["official_reserves", "epu", "gpr"]:
            latest_date = _date.fromisoformat(layers[layer_id]["latest"]["date"])
            self.assertLessEqual(latest_date, self.TODAY)

        self.assertEqual(layers["official_reserves"]["latest"]["date"], "2026-06-30")
        self.assertEqual(layers["epu"]["latest"]["date"], "2026-06-30")
        self.assertEqual(layers["gpr"]["latest"]["date"], "2026-06-30")
        self.assertEqual(layers["positioning_technical"]["latest"]["date"], "2026-07-06")

    def test_official_reserves_use_manual_china_update_without_future_excel_rows(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        layer = {layer["id"]: layer for layer in dashboard["layers"]}["official_reserves"]

        self.assertEqual(layer["latest"]["date"], "2026-06-30")
        self.assertAlmostEqual(layer["latest"]["china_reserves"], 2346.446)
        self.assertAlmostEqual(layer["latest"]["china_change"], 14.93, places=2)
        self.assertIsNone(layer["latest"]["global_reserves"])
        self.assertIn("Wind 通讯社", layer["source"])

        html = build_site.build_html(dashboard)
        self.assertIn("中国官方黄金储备 2346.45 吨", html)
        self.assertIn("全球官方黄金储备本期未更新", html)
        self.assertNotIn("2026-07-31", html)

    def test_reads_refreshed_workbook_into_driver_layers(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        self.assertIn("data/招商证券：黄金图表整理2607.xlsx", dashboard["source_file"])
        layers = {layer["id"]: layer for layer in dashboard["layers"]}

        self.assertEqual(
            set(layers),
            {
                "real_rate",
                "dollar",
                "inflation_expectation",
                "official_reserves",
                "positioning_technical",
                "price_trend",
                "epu",
                "gpr",
            },
        )

        self.assertIn("DFII10", layers["real_rate"]["source"])
        self.assertIsNotNone(layers["real_rate"]["latest"]["value"])
        self.assertIn(layers["real_rate"]["data_quality"], {"fresh", "stale", "very-stale"})

        self.assertIn("USDX.FX", layers["dollar"]["source"])
        self.assertIsNotNone(layers["dollar"]["latest"]["value"])
        self.assertIn("lag_days", layers["dollar"])
        self.assertIn(layers["dollar"]["data_quality"], {"fresh", "stale", "very-stale"})

        self.assertEqual(layers["official_reserves"]["latest"]["date"], "2026-06-30")
        self.assertAlmostEqual(layers["official_reserves"]["latest"]["china_reserves"], 2346.446)

        self.assertIn(layers["inflation_expectation"]["data_quality"], {"fresh", "stale", "very-stale"})
        self.assertIsNotNone(layers["inflation_expectation"]["latest"]["value"])
        self.assertIn("FRED: T10YIE", layers["inflation_expectation"]["source"])

        positioning = layers["positioning_technical"]
        self.assertIn(positioning["data_quality"], {"fresh", "stale", "very-stale"})
        self.assertIn("CFTC", positioning["source"])
        self.assertIsNotNone(positioning["latest"]["managed_money_net"])
        self.assertIsNotNone(positioning["latest"]["managed_money_net_percentile"])

        self.assertTrue({"price_trend", "epu", "gpr"} <= set(layers))

    def test_html_is_data_driven_and_excludes_opinion_summary_text(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
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

        self.assertIn("价格与趋势", html)
        self.assertIn("经济政策不确定性", html)
        self.assertIn("地缘政治风险", html)
        self.assertIn("滞后", html)
        self.assertIn("tendency", html)
        self.assertIn("GVZ 数据", html)      # 仓位层 GVZ 组件 staleness 被显式呈现

        self.assertNotIn("观点汇总", html)
        self.assertNotIn("短期金价仍偏向震荡行情", html)
        self.assertNotIn("整体偏谨慎", html)

    def test_relationships_explain_factor_usefulness_by_phase(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        relationships = {item["id"]: item for item in dashboard["relationships"]}

        self.assertEqual(
            set(relationships),
            {
                "central_bank_purchases",
                "real_rate",
                "dollar",
                "inflation_expectation",
                "positioning_technical",
                "price_trend",
                "epu",
                "gpr",
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
        self.assertIn("趋势→未来1月", html)
        self.assertIn("勿当稳定因果", html)   # EPU/GPR 关系卡的克制文案

    def test_charts_show_axes_and_underlying_series(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
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

    def test_forward_pairs_use_future_return_not_past(self):
        # 信号锚在 t,收益必须取 t→t+horizon(未来);若误用同期/过去收益,符号会相反。
        from datetime import date, timedelta
        d0 = date(2025, 1, 1)
        prices = [10.0, 10.0, 5.0, 5.0, 20.0]
        rows = [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
                 "_date": d0 + timedelta(days=i), "gold_price": p}
                for i, p in enumerate(prices)]
        pairs = build_site.build_trend_forward_pairs(rows, ma_window=2, horizon=2)
        # 最后一对锚在 i=2(price=5):未来收益 = 20/5 - 1 = +3.0;若误用过去收益会是 5/10 - 1 = -0.5
        self.assertEqual(pairs[-1]["_date"], d0 + timedelta(days=2))
        self.assertAlmostEqual(pairs[-1]["gold_return"], 3.0)


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


class MacroRiskLayerTest(unittest.TestCase):
    def _monthly(self, values):
        from datetime import date
        rows = []
        for i, v in enumerate(values):
            y, m = 2025 + (i // 12), (i % 12) + 1
            rows.append({"date": f"{y}-{m:02d}-28", "_date": date(y, m, 28), "epu": v, "gpr": v})
        return rows

    def test_rising_uncertainty_is_supportive(self):
        rows = self._monthly([100, 110, 130, 170, 240, 360])
        gold = [{"date": r["date"], "_date": r["_date"], "gold_price": 1000 + 10 * i}
                for i, r in enumerate(rows)]
        epu = build_site.make_epu_layer(rows, gold)
        self.assertEqual(epu["id"], "epu")
        self.assertEqual(epu["frequency"], "monthly")
        self.assertEqual(epu["state"], "supportive")

    def test_gpr_layer_shape(self):
        rows = self._monthly([100, 105, 110, 108, 112, 109])
        gold = [{"date": r["date"], "_date": r["_date"], "gold_price": 1000.0} for r in rows]
        layer = build_site.make_gpr_layer(rows, gold)
        self.assertEqual(layer["id"], "gpr")
        self.assertIn(layer["state"], {"supportive", "neutral", "headwind"})


class PostureTest(unittest.TestCase):
    def _layers(self, states):
        return [{"state": s} for s in states]

    def test_tendency_normalizes_by_active_layers(self):
        score, posture, state, tendency, active = build_site.score_layers(
            self._layers(["supportive"] * 3 + ["neutral"] * 5))
        self.assertEqual(active, 8)
        self.assertAlmostEqual(tendency, 3 / 8)
        self.assertEqual(posture, "偏多")

    def test_neutral_band(self):
        score, posture, state, tendency, active = build_site.score_layers(
            self._layers(["supportive", "headwind", "neutral"]))
        self.assertEqual(posture, "中性")

    def test_boundary_headwind(self):
        # 1 headwind + 3 neutral → tendency = -0.25(承压含 -0.25 边界)
        score, posture, state, tendency, active = build_site.score_layers(
            self._layers(["headwind"] + ["neutral"] * 3))
        self.assertEqual(active, 4)
        self.assertAlmostEqual(tendency, -0.25)
        self.assertEqual(posture, "承压")

    def test_boundary_supportive(self):
        # 1 supportive + 3 neutral → tendency = +0.25(偏多含 +0.25 边界)
        score, posture, state, tendency, active = build_site.score_layers(
            self._layers(["supportive"] + ["neutral"] * 3))
        self.assertEqual(active, 4)
        self.assertAlmostEqual(tendency, 0.25)
        self.assertEqual(posture, "偏多")


if __name__ == "__main__":
    unittest.main()
