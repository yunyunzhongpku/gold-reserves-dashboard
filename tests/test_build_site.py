import json
import re
import sys
import tempfile
import unittest
from datetime import date, date as _date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_site


class OfficialReserveOverrideTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "official.csv"

    def tearDown(self):
        self.tempdir.cleanup()

    def write_csv(self, rows, header="date,china_reserves_10k_oz,global_reserves,source"):
        self.path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        return self.path

    def test_missing_safe_series_fails_closed(self):
        missing = Path(self.tempdir.name) / "missing-official.csv"

        with self.assertRaisesRegex(
            FileNotFoundError, "SAFE 权威序列.*missing-official.csv"
        ):
            build_site.read_official_reserve_rows(missing)

    def test_requires_current_and_previous_safe_anchor_after_cutoff(self):
        single = self.write_csv(["2026-06-30,7544,,SAFE"])
        with self.assertRaisesRegex(ValueError, "至少需要当前月和前一个月"):
            build_site.read_official_reserve_rows(single)

        two_months = self.write_csv([
            "2026-05-31,7496,,SAFE",
            "2026-06-30,7544,,SAFE",
        ])
        with self.assertRaisesRegex(ValueError, "至少需要当前月和前一个月"):
            build_site.read_official_reserve_rows(
                two_months, max_date=_date(2026, 5, 31)
            )

    def test_requires_full_safe_prefix_from_2025_december_anchor(self):
        truncated = self.write_csv([
            "2026-05-31,7496,,SAFE",
            "2026-06-30,7544,,SAFE",
        ])

        with self.assertRaisesRegex(ValueError, "必须从 2025-12-31 锚点开始"):
            build_site.read_official_reserve_rows(truncated)

    def test_rejects_missing_extra_or_reordered_headers(self):
        cases = [
            ("date,china_reserves_10k_oz,source", "2026-01-31,7419,SAFE"),
            (
                "date,china_reserves_10k_oz,global_reserves,source,notes",
                "2026-01-31,7419,,SAFE,unexpected",
            ),
            (
                "date,global_reserves,china_reserves_10k_oz,source",
                "2026-01-31,,7419,SAFE",
            ),
        ]
        for header, row in cases:
            with self.subTest(header=header):
                path = self.write_csv([row], header=header)
                with self.assertRaisesRegex(ValueError, "表头必须严格为"):
                    build_site.read_official_reserve_rows(path)

    def test_rejects_empty_china_source(self):
        path = self.write_csv(["2026-01-31,7419,,"])
        with self.assertRaisesRegex(ValueError, "来源不能为空"):
            build_site.read_official_reserve_rows(path)

    def test_reads_complete_safe_series_and_converts_tonnes(self):
        path = self.write_csv([
            "2025-12-31,7415,,SAFE: 官方储备资产（2025）",
            "2026-01-31,7419,,SAFE: 官方储备资产（2026）",
            "2026-02-28,7422,,SAFE: 官方储备资产（2026）",
            "2026-03-31,7438,,SAFE: 官方储备资产（2026）",
            "2026-04-30,7464,,SAFE: 官方储备资产（2026）",
            "2026-05-31,7496,,SAFE: 官方储备资产（2026）",
            "2026-06-30,7544,,SAFE: 官方储备资产（2026）",
        ])
        rows = build_site.read_official_reserve_rows(path, max_date=_date(2026, 7, 7))
        self.assertEqual(rows[0]["date"], "2025-12-31")
        self.assertAlmostEqual(rows[-2]["china_reserves"], 2331.517, places=3)
        self.assertAlmostEqual(rows[-1]["china_reserves"], 2346.446, places=3)
        self.assertAlmostEqual(
            rows[-1]["china_reserves"] - rows[-2]["china_reserves"], 14.930, places=3)

    def test_tracked_safe_file_keeps_verified_prefix(self):
        rows = build_site.read_official_reserve_rows(
            build_site.OFFICIAL_RESERVES_MANUAL_FILE
        )
        expected_dates = [
            "2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31",
            "2026-04-30", "2026-05-31", "2026-06-30",
        ]
        expected_ounces = [7415, 7419, 7422, 7438, 7464, 7496, 7544]

        self.assertGreaterEqual(len(rows), len(expected_dates))
        self.assertEqual(
            [row["date"] for row in rows[:len(expected_dates)]],
            expected_dates,
        )
        self.assertEqual(
            [row["china_reserves_10k_oz"] for row in rows[:len(expected_ounces)]],
            expected_ounces,
        )

    def test_rejects_duplicate_gap_out_of_order_and_non_positive_values(self):
        cases = {
            "重复": ["2026-01-15,7419,,SAFE", "2026-01-31,7422,,SAFE"],
            "连续": ["2025-12-31,7415,,SAFE", "2026-02-28,7422,,SAFE"],
            "升序": ["2026-02-28,7422,,SAFE", "2026-01-31,7419,,SAFE"],
            "正数": ["2026-01-31,0,,SAFE"],
        }
        for message, rows in cases.items():
            with self.subTest(message=message):
                path = self.write_csv(rows)
                with self.assertRaisesRegex(ValueError, message):
                    build_site.read_official_reserve_rows(path)

    def test_rejects_non_finite_china_values(self):
        for value in ["NaN", "inf", "-inf"]:
            with self.subTest(value=value):
                path = self.write_csv([f"2026-01-31,{value},,SAFE"])
                with self.assertRaisesRegex(ValueError, "有限正数"):
                    build_site.read_official_reserve_rows(path)

    def test_rejects_non_finite_global_values(self):
        for value in ["NaN", "inf", "-inf"]:
            with self.subTest(value=value):
                path = self.write_csv([f"2026-01-31,7419,{value},SAFE"])
                with self.assertRaisesRegex(ValueError, "全球官方黄金储备必须为有限正数"):
                    build_site.read_official_reserve_rows(path)

    def test_rejects_non_positive_global_values(self):
        for value in ["0", "-1"]:
            with self.subTest(value=value):
                path = self.write_csv([f"2026-01-31,7419,{value},SAFE"])
                with self.assertRaisesRegex(ValueError, "全球官方黄金储备必须为有限正数"):
                    build_site.read_official_reserve_rows(path)

    def test_future_month_is_validated_but_excluded_from_dashboard_cutoff(self):
        path = self.write_csv([
            "2025-12-31,7415,,SAFE",
            "2026-01-31,7419,,SAFE",
            "2026-02-28,7422,,SAFE",
            "2026-03-31,7438,,SAFE",
            "2026-04-30,7464,,SAFE",
            "2026-05-31,7496,,SAFE",
            "2026-06-30,7544,,SAFE",
            "2026-07-31,7550,,SAFE",
        ])
        rows = build_site.read_official_reserve_rows(path, max_date=_date(2026, 7, 7))
        self.assertEqual(
            [row["date"] for row in rows[-2:]],
            ["2026-05-31", "2026-06-30"],
        )
        self.assertNotIn("2026-07-31", [row["date"] for row in rows])

    def test_invalid_future_month_is_rejected_before_dashboard_cutoff(self):
        path = self.write_csv([
            "2025-12-31,7415,,SAFE",
            "2026-01-31,7419,,SAFE",
            "2026-02-28,7422,,SAFE",
            "2026-03-31,7438,,SAFE",
            "2026-04-30,7464,,SAFE",
            "2026-05-31,7496,,SAFE",
            "2026-06-30,7544,,SAFE",
            "2026-07-31,NaN,,SAFE",
        ])
        with self.assertRaisesRegex(ValueError, "有限正数"):
            build_site.read_official_reserve_rows(path, max_date=_date(2026, 7, 7))

    def test_field_level_merge_preserves_global_reserve(self):
        base = [{
            "date": "2026-06-30", "_date": _date(2026, 6, 30),
            "china_reserves": 2321.5452, "global_reserves": 36558.5405,
        }]
        override = [{
            "date": "2026-06-30", "_date": _date(2026, 6, 30),
            "china_reserves_10k_oz": 7544.0, "china_reserves": 2346.446,
            "global_reserves": None, "china_source": "SAFE", "global_source": None,
        }]
        row = build_site.merge_reserve_rows(base, override)[0]
        self.assertAlmostEqual(row["china_reserves"], 2346.446)
        self.assertAlmostEqual(row["global_reserves"], 36558.5405)
        self.assertEqual(row["china_source"], "SAFE")
        self.assertIn("Excel", row["global_source"])

    def test_manual_series_caps_later_excel_reserve_rows(self):
        base = [
            {
                "date": "2026-06-30", "_date": _date(2026, 6, 30),
                "china_reserves": 2321.5452, "global_reserves": 36558.5405,
            },
            {
                "date": "2026-07-31", "_date": _date(2026, 7, 31),
                "china_reserves": 2360.0, "global_reserves": 36580.0,
            },
        ]
        override = [{
            "date": "2026-06-30", "_date": _date(2026, 6, 30),
            "china_reserves_10k_oz": 7544.0, "china_reserves": 2346.446,
            "global_reserves": None, "china_source": "SAFE", "global_source": None,
        }]

        rows = build_site.merge_reserve_rows(base, override)

        self.assertEqual([row["date"] for row in rows], ["2026-06-30"])
        self.assertEqual(rows[0]["china_source"], "SAFE")
        self.assertAlmostEqual(rows[0]["global_reserves"], 36558.5405)

    def test_excel_only_merge_row_marks_raw_china_ounces_unavailable(self):
        base = [{
            "date": "2026-04-30", "_date": _date(2026, 4, 30),
            "china_reserves": 2321.5452, "global_reserves": 36501.0,
        }]
        row = build_site.merge_reserve_rows(base, [])[0]
        self.assertIn("china_reserves_10k_oz", row)
        self.assertIsNone(row["china_reserves_10k_oz"])


class InteractiveGoldChartTest(unittest.TestCase):
    TODAY = _date(2026, 7, 7)

    @classmethod
    def setUpClass(cls):
        cls.dashboard = build_site.read_dashboard_data(today=cls.TODAY)

    def test_shift_months_clamps_to_calendar_month_end(self):
        self.assertEqual(
            build_site.shift_months(_date(2024, 3, 31), -1),
            _date(2024, 2, 29),
        )
        self.assertEqual(
            build_site.shift_months(_date(2026, 3, 31), -1),
            _date(2026, 2, 28),
        )

    def test_calendar_ranges_end_at_latest_observation(self):
        rows = self.dashboard["technical_layer"]["chart_rows"]
        ranges = build_site.build_gold_chart_ranges(rows)

        self.assertEqual(set(ranges), {"3m", "1y", "3y"})
        self.assertEqual(ranges["1y"][-1]["date"], rows[-1]["date"])
        self.assertGreaterEqual(ranges["3m"][0]["_date"], _date(2026, 4, 6))

    def test_geometry_uses_one_scale_for_price_and_moving_averages(self):
        rows = [
            {
                "date": "2026-01-01",
                "_date": _date(2026, 1, 1),
                "gold_price": 100.0,
                "ma20": 100.0,
                "ma60": 90.0,
                "ma200": None,
            },
            {
                "date": "2026-01-02",
                "_date": _date(2026, 1, 2),
                "gold_price": 110.0,
                "ma20": 105.0,
                "ma60": 95.0,
                "ma200": 90.0,
            },
        ]

        points = build_site.make_gold_chart_geometry(rows)

        self.assertEqual(points[0]["gold_price_y"], points[0]["ma20_y"])
        self.assertLess(points[1]["gold_price_y"], points[1]["ma20_y"])
        self.assertEqual(points[0]["ma200_y"], None)

    def test_empty_ranges_and_geometry_are_safe(self):
        self.assertEqual(
            build_site.build_gold_chart_ranges([]),
            {"3m": [], "1y": [], "3y": []},
        )
        self.assertEqual(build_site.make_gold_chart_geometry([]), [])
        svg = build_site.make_gold_chart_svg([], "1y", hidden=False)
        self.assertIn('data-range="1y"', svg)
        self.assertIn("暂无价格数据", svg)

    def test_safe_json_escapes_script_breakout_and_remains_parseable(self):
        value = {"label": "</script><b>黄金</b>"}

        raw = build_site.safe_json(value)

        self.assertNotIn("<", raw)
        self.assertEqual(json.loads(raw), value)

    def test_html_embeds_static_fallback_controls_and_safe_payload(self):
        html = build_site.build_html(self.dashboard)

        self.assertIn('data-chart-range="3m"', html)
        self.assertIn('data-chart-range="1y" aria-pressed="true"', html)
        self.assertIn('data-chart-range="3y"', html)
        for series in ["ma20", "ma60", "ma200"]:
            self.assertIn(f'data-chart-series="{series}"', html)
            self.assertIn(f'class="chart-series series-{series}"', html)
            self.assertIn(f'data-tooltip-{series}', html)

        self.assertEqual(html.count('class="gold-chart"'), 3)
        one_year_svg = re.search(
            r'<svg class="gold-chart" data-range="1y"[^>]*>', html, re.S
        ).group(0)
        self.assertNotIn(" hidden", one_year_svg)
        for range_id in ["3m", "3y"]:
            svg = re.search(
                rf'<svg class="gold-chart" data-range="{range_id}"[^>]*>',
                html,
                re.S,
            ).group(0)
            self.assertIn(" hidden", svg)

        raw = re.search(
            r'<script type="application/json" id="gold-chart-data">(.*?)</script>',
            html,
            re.S,
        ).group(1)
        self.assertNotIn("<", raw)
        payload = json.loads(raw)
        self.assertEqual(set(payload["ranges"]), {"3m", "1y", "3y"})
        latest = payload["ranges"]["1y"][-1]
        self.assertTrue(
            {
                "date",
                "gold_price",
                "ma20",
                "ma60",
                "ma200",
                "x",
                "gold_price_y",
            }
            <= set(latest)
        )

        self.assertIn("pointermove", html)
        self.assertIn("pointerdown", html)
        self.assertIn("touch-action: pan-y", html)
        self.assertIn("stroke-dasharray", html)
        self.assertNotIn("linearGradient", html)
        self.assertNotIn("<script src=", html)
        self.assertIn(build_site.GOLD_CHART_SCRIPT, html)
        self.assertLess(html.rfind(build_site.GOLD_CHART_SCRIPT), html.rfind("</body>"))

    def test_script_guards_missing_elements_and_empty_ranges(self):
        script = build_site.GOLD_CHART_SCRIPT

        self.assertIn("if (!root) return;", script)
        self.assertIn("if (!payloadElement) return;", script)
        self.assertIn("if (!points || !points.length) return;", script)
        for field in ["date", "price", "ma20", "ma60", "ma200"]:
            self.assertIn(f"[data-tooltip-{field}]", script)

    def test_script_toggles_svg_hidden_attributes(self):
        script = build_site.GOLD_CHART_SCRIPT

        self.assertIn('svg.toggleAttribute("hidden"', script)
        self.assertIn('line.removeAttribute("hidden")', script)
        self.assertIn('marker.removeAttribute("hidden")', script)

    def test_pointerleave_restores_latest_active_range_value(self):
        script = build_site.GOLD_CHART_SCRIPT

        self.assertIn("const renderLatest = (svg)", script)
        self.assertIn('line.setAttribute("hidden", "")', script)
        self.assertIn('marker.setAttribute("hidden", "")', script)
        self.assertIn("showValues(points[points.length - 1]);", script)
        self.assertIn("renderLatest(activeSvg);", script)
        self.assertIn(
            'svg.addEventListener("pointerleave", (event) => {',
            script,
        )
        self.assertIn(
            'if (event.pointerType === "touch" || event.pointerType === "pen") return;',
            script,
        )
        self.assertIn("renderLatest(svg);", script)

    def test_disables_ma_controls_without_enough_history(self):
        from datetime import timedelta

        start = _date(2026, 1, 1)
        rows = [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "_date": start + timedelta(days=i),
                "gold_price": 100.0 + i,
            }
            for i in range(30)
        ]

        panel = build_site.make_gold_chart_panel(
            build_site.make_price_trend_layer(rows))

        self.assertIn('data-chart-series="ma20" aria-pressed="true"', panel)
        self.assertIn(
            'data-chart-series="ma60" aria-pressed="false" disabled', panel)
        self.assertIn(
            'data-chart-series="ma200" aria-pressed="false" disabled', panel)


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

        for layer_id in ["official_reserves", "epu", "gpr", "positioning_technical"]:
            latest_date = _date.fromisoformat(layers[layer_id]["latest"]["date"])
            self.assertLessEqual(latest_date, self.TODAY)

        self.assertEqual(layers["official_reserves"]["latest"]["date"], "2026-06-30")
        self.assertEqual(layers["epu"]["latest"]["date"], "2026-06-30")
        self.assertEqual(layers["gpr"]["latest"]["date"], "2026-06-30")

    def test_official_reserves_use_manual_china_update_without_future_excel_rows(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        layer = {layer["id"]: layer for layer in dashboard["layers"]}["official_reserves"]

        self.assertEqual(layer["latest"]["date"], "2026-06-30")
        self.assertAlmostEqual(layer["latest"]["china_reserves"], 2346.446, places=3)
        self.assertAlmostEqual(layer["latest"]["china_change"], 14.93, places=2)
        self.assertAlmostEqual(layer["latest"]["global_reserves"], 36558.5405)
        self.assertIn("SAFE", layer["source"])
        self.assertIn("SAFE", layer["latest"]["china_source"])
        self.assertIn("Excel", layer["latest"]["global_source"])
        self.assertEqual(layer["state"], "supportive")

        html = build_site.build_html(dashboard)
        self.assertIn("中国官方黄金储备 2346.45 吨", html)
        self.assertIn("全球官方黄金储备 36558.54 吨", html)
        self.assertNotIn("全球官方黄金储备本期未更新", html)
        self.assertNotIn("Wind 通讯社", html)
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
        self.assertAlmostEqual(
            layers["official_reserves"]["latest"]["china_reserves"], 2346.446, places=3)

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
        self.assertIn("黄金决策看板", html)
        self.assertIn("驱动合成", html)
        self.assertIn(dashboard["posture"], html)
        self.assertIn("实际利率", html)
        self.assertIn("美元", html)
        self.assertIn("通胀预期", html)
        self.assertIn("央行购金", html)
        self.assertIn("仓位与技术", html)
        self.assertIn("Managed Money", html)
        self.assertIn("数据质量与来源", html)
        self.assertNotIn("后续接入", html)

        self.assertIn("价格与趋势", html)
        self.assertIn("经济政策不确定性", html)
        self.assertIn("地缘政治风险", html)
        self.assertIn("滞后", html)
        self.assertIn("新鲜", html)
        self.assertNotIn(">fresh<", html)
        self.assertIn("GVZ 数据", html)      # 仓位层 GVZ 组件 staleness 被显式呈现

        self.assertNotIn("Gold decision dashboard", html)
        self.assertNotIn("tendency", html)
        self.assertNotIn("观点汇总", html)
        self.assertNotIn("短期金价仍偏向震荡行情", html)
        self.assertNotIn("整体偏谨慎", html)

    def test_html_uses_decision_first_structure(self):
        html = build_site.build_html(
            build_site.read_dashboard_data(today=self.TODAY))

        self.assertIn('id="gold-price-section"', html)
        self.assertIn("近 5 个有效观测日", html)
        self.assertIn("技术状态", html)
        self.assertIn("MA20", html)
        self.assertIn("MA60", html)
        self.assertIn("MA200", html)
        self.assertIn("判断改变条件", html)
        self.assertIn('id="driver-table"', html)
        self.assertEqual(html.count('class="driver-row"'), 6)
        self.assertEqual(html.count('class="driver-state state-'), 6)
        self.assertIn('id="recent-changes"', html)
        self.assertEqual(html.count('class="recent-change '), 3)
        self.assertEqual(html.count('class="change-state state-'), 3)
        self.assertEqual(html.count('<details class="evidence-unit"'), 7)
        self.assertIn("研究依据", html)
        self.assertNotIn('<details class="evidence-unit" open', html)
        self.assertNotIn('<section class="cards">', html)
        self.assertNotIn("<h2>What Changed</h2>", html)
        self.assertNotIn("<h2>失效条件</h2>", html)
        self.assertNotIn("<h2>下一观察点</h2>", html)

    def test_generated_html_has_no_trailing_whitespace(self):
        html = build_site.build_html(
            build_site.read_dashboard_data(today=self.TODAY))

        trailing_lines = [
            line_number
            for line_number, line in enumerate(html.splitlines(), start=1)
            if line != line.rstrip()
        ]
        self.assertEqual([], trailing_lines)

    def test_research_details_include_scoring_method_and_limitations(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        html = build_site.build_html(dashboard)

        total = len(dashboard["driver_layers"])
        stale = sum(
            layer["data_quality"] == "stale"
            for layer in dashboard["driver_layers"]
        )
        excluded = total - dashboard["active_layers"]
        self.assertIn(
            f"{dashboard['active_layers']} / {total} 组驱动计入",
            html,
        )
        self.assertIn(f"{stale} 组滞后", html)
        self.assertIn(f"{excluded} 组未计入", html)

        self.assertIn("计算方法与已知局限", html)
        self.assertIn(
            f"驱动净分 {dashboard['score']:+d} / 有效驱动 {dashboard['active_layers']} 组",
            html,
        )
        self.assertIn(f"归一化倾向 {dashboard['tendency']:+.2f}", html)
        self.assertIn("+0.25 及以上为偏多", html)
        self.assertIn("-0.25 及以下为承压", html)
        self.assertIn("缺失和严重滞后不计分，滞后仍计分", html)
        self.assertIn("少于 3 组时显示数据不足", html)
        self.assertIn("ETF 与 CFTC 合为一组", html)
        self.assertIn("严格多头或空头排列", html)
        self.assertIn("等权启发式框架", html)
        self.assertIn("相关性不代表因果", html)
        self.assertIn("滚动相关使用重叠窗口", html)
        self.assertIn("EPU 与 GPR 只保留在研究依据", html)
        self.assertIn("SAFE 官方序列仍需手工维护", html)

        threshold_contracts = [
            "实际利率：21 个有效观测，阈值 ±0.05 个百分点；变化低于 -0.05 个百分点为支持，高于 +0.05 个百分点为压力，其余为中性。",
            "美元：21 个有效观测，阈值 ±0.5；变化低于 -0.5 为支持，高于 +0.5 为压力，其余为中性。",
            "通胀预期：21 个有效观测，阈值 ±0.05 个百分点；变化高于 +0.05 个百分点为支持，低于 -0.05 个百分点为压力，其余为中性。",
            "中国购金：月度净增为支持、净减为压力、零变化为中性。",
            "黄金 ETF：21 个有效观测，阈值 ±3 吨；变化高于 +3 吨为支持，低于 -3 吨为压力，其余为中性。",
            "CFTC：近 4 周净多变化阈值 ±15,000 张；高于 +15,000 张为支持、低于 -15,000 张为压力；三年（156 周）分位高于 90% 时优先判为压力。",
            "资金与仓位：ETF 与 CFTC 可用子信号分别投 +1 / 0 / -1，平均票阈值 ±0.5；不低于 +0.5 为支持、不高于 -0.5 为压力，其余为中性。",
            "数据时效：日频 0–4 天新鲜、5–14 天滞后、超过 14 天严重滞后；周频 0–10 天新鲜、11–21 天滞后、超过 21 天严重滞后；月频 0–45 天新鲜、46–75 天滞后、超过 75 天严重滞后。",
            "严格多头排列：价格 ≥ MA20 > MA60 > MA200；严格空头排列：价格 ≤ MA20 < MA60 < MA200。",
        ]
        for contract in threshold_contracts:
            with self.subTest(contract=contract):
                self.assertIn(build_site.escape(contract), html)

    def test_price_and_proxy_layers_remain_in_research_but_not_headline_score(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        self.assertEqual(
            [layer["id"] for layer in dashboard["driver_layers"]],
            [
                "real_rate",
                "dollar",
                "inflation_expectation",
                "official_reserves",
                "positioning_technical",
            ],
        )

        html = build_site.build_html(dashboard)
        self.assertIn("经济政策不确定性", html)
        self.assertIn("地缘政治风险", html)
        self.assertIn('id="evidence-auxiliary"', html)
        self.assertIn("不参与首页姿态", html)
        self.assertIn("中国：SAFE", html)
        self.assertIn("全球：Excel: 官方黄金储备", html)
        self.assertIn("+14.93 吨", html)

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

        tone_words = {"有效", "偏弱", "失效", "样本不足"}

        central_bank = relationships["central_bank_purchases"]
        self.assertIsNotNone(central_bank["start_month"])
        self.assertTrue(central_bank["rolling_corr"])
        self.assertIn(central_bank["latest_tone"], tone_words)
        self.assertIsNotNone(central_bank["latest_corr"])

        real_rate = relationships["real_rate"]
        self.assertIn("short_term", real_rate)
        self.assertIn("medium_term", real_rate)
        self.assertIsNotNone(real_rate["short_term"]["latest_corr"])
        self.assertIsNotNone(real_rate["medium_term"]["latest_corr"])
        self.assertTrue(real_rate["rolling_corr"])
        self.assertIn(real_rate["latest_tone"], tone_words)

        for relationship_id in ["dollar", "inflation_expectation"]:
            relationship = relationships[relationship_id]
            self.assertIn("short_term", relationship)
            self.assertIn("medium_term", relationship)
            self.assertIsNotNone(relationship["short_term"]["latest_corr"])
            self.assertIsNotNone(relationship["medium_term"]["latest_corr"])
            self.assertTrue(relationship["rolling_corr"])
            self.assertIn(relationship["latest_tone"], tone_words)

        positioning = relationships["positioning_technical"]
        sub_metrics = {item["id"]: item for item in positioning["sub_metrics"]}
        self.assertEqual(set(sub_metrics), {"etf_holdings", "managed_money", "gvz"})
        for sub_metric in sub_metrics.values():
            self.assertIsNotNone(sub_metric["latest_corr"])
            self.assertTrue(sub_metric["rolling_corr"])
            self.assertIn(sub_metric["latest_tone"], tone_words)

        html = build_site.build_html(dashboard)
        self.assertIn('id="research"', html)
        self.assertIn("什么时候开始显著推动", html)
        self.assertIn("滚动相关", html)
        self.assertIn("关系演变", html)
        self.assertIn("实际利率短期", html)
        self.assertIn("252日滚动相关(中期)", html)   # 中期窗现由 corr_label 承接,不再走 metric-strip 的因子专属标签(E11 退役)
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
        self.assertIn('aria-label="中国央行黄金储备环比变化柱状图"', html)
        self.assertIn('aria-label="全球央行黄金储备环比变化柱状图"', html)
        self.assertIn('class="bar-chart"', html)

        self.assertIn("双轴走势", html)
        self.assertIn('class="dual-axis-chart"', html)
        self.assertIn('class="right-axis"', html)
        self.assertIn("黄金价格", html)
        self.assertIn("滚动相关", html)
        self.assertIn('class="grid-line"', html)
        self.assertIn("已反转", html)
        self.assertIn('class="tone-band"', html)


class DriverPostureTest(unittest.TestCase):
    TODAY = _date(2026, 7, 7)

    def layer(self, layer_id, state, quality="fresh"):
        return {"id": layer_id, "state": state, "data_quality": quality}

    def positioning_rows(self, etf_values, cot_values, cot_start=_date(2026, 5, 26)):
        from datetime import timedelta

        etf_start = self.TODAY - timedelta(days=len(etf_values))
        etf_rows = [
            {
                "date": (etf_start + timedelta(days=i + 1)).isoformat(),
                "spdr_holdings": value * 0.6,
                "ishares_holdings": value * 0.4,
            }
            for i, value in enumerate(etf_values)
        ]
        cot_rows = [
            {
                "date": (cot_start + timedelta(days=7 * i)).isoformat(),
                "managed_money_net": value,
                "managed_money_long": value + 20_000,
                "managed_money_short": 20_000,
            }
            for i, value in enumerate(cot_values)
        ]
        return etf_rows, cot_rows

    def recent_change_layers(
        self, etf_state, cot_state,
        real_state="headwind", dollar_state="supportive",
        real_quality="fresh", dollar_quality="fresh",
        current_purchase=14.93, previous_purchase=9.95,
    ):
        return [
            {
                "id": "real_rate", "state": real_state,
                "data_quality": real_quality, "change_label": "+0.10pct",
            },
            {
                "id": "dollar", "state": dollar_state,
                "data_quality": dollar_quality, "change_label": "-1.00",
            },
            {
                "id": "official_reserves", "state": "supportive",
                "latest": {"china_change": current_purchase},
                "chart_rows": [
                    {"china_mom_change": previous_purchase},
                    {"china_mom_change": current_purchase},
                ],
            },
            {
                "id": "positioning_technical", "state": "neutral",
                "sub_states": {"etf": etf_state, "cot": cot_state},
                "latest": {"etf_change": -12.0, "managed_money_net_change": 18_000},
            },
        ]

    def test_scores_only_five_driver_groups(self):
        layers = [
            self.layer("real_rate", "headwind"),
            self.layer("dollar", "headwind"),
            self.layer("inflation_expectation", "headwind"),
            self.layer("official_reserves", "supportive"),
            self.layer("positioning_technical", "neutral"),
            self.layer("price_trend", "supportive"),
            self.layer("epu", "supportive"),
            self.layer("gpr", "supportive"),
        ]

        score, posture, _, tendency, active = build_site.score_driver_layers(layers)

        self.assertEqual((score, posture, active), (-2, "承压", 5))
        self.assertAlmostEqual(tendency, -0.4)

    def test_excludes_missing_and_very_stale_but_counts_stale(self):
        layers = [
            self.layer("real_rate", "headwind", "stale"),
            self.layer("dollar", "headwind", "very-stale"),
            self.layer("inflation_expectation", "missing", "missing"),
            self.layer("official_reserves", "supportive"),
        ]

        score, posture, state, tendency, active = build_site.score_driver_layers(layers)

        self.assertEqual((score, posture, state, tendency, active), (0, "数据不足", "missing", 0.0, 2))

    def test_combines_available_etf_and_cot_subsignals(self):
        self.assertEqual(
            build_site.combine_subsignal_states(["headwind", "supportive"]), "neutral")
        self.assertEqual(
            build_site.combine_subsignal_states(["supportive", "neutral"]), "supportive")
        self.assertEqual(
            build_site.combine_subsignal_states(["missing", "headwind"]), "headwind")
        self.assertEqual(
            build_site.combine_subsignal_states(["missing", "missing"]), "missing")

    def test_positioning_ignores_gold_and_gvz_votes_and_exposes_subsignal_quality(self):
        from datetime import timedelta

        etf_rows, cot_rows = self.positioning_rows(
            list(range(100, 122)), [10_000, 100_000, 15_000, 20_000, 40_000])
        gold_rows = [
            {
                "date": (self.TODAY - timedelta(days=60 - i)).isoformat(),
                "gold_price": 2_000 - i * 10,
            }
            for i in range(61)
        ]
        vol_rows = [{"date": self.TODAY.isoformat(), "gvz": 99.0}]

        layer = build_site.make_positioning_layer(
            etf_rows, vol_rows, gold_rows, cot_rows, today=self.TODAY)

        self.assertEqual(layer["state"], "supportive")
        self.assertEqual(layer["sub_states"], {"etf": "supportive", "cot": "supportive"})
        self.assertEqual(layer["latest"]["etf_date"], etf_rows[-1]["date"])
        self.assertEqual(layer["latest"]["cot_date"], cot_rows[-1]["date"])
        self.assertEqual(layer["latest"]["etf_quality"], "fresh")
        self.assertEqual(layer["latest"]["cot_quality"], "stale")

    def test_positioning_treats_very_stale_cot_as_missing_vote(self):
        etf_rows, cot_rows = self.positioning_rows(
            list(range(121, 99, -1)),
            [10_000, 100_000, 15_000, 20_000, 40_000],
            cot_start=_date(2026, 4, 28),
        )

        layer = build_site.make_positioning_layer(
            etf_rows, [], [], cot_rows, today=self.TODAY)

        self.assertEqual(layer["latest"]["cot_quality"], "very-stale")
        self.assertEqual(layer["sub_states"], {"etf": "headwind", "cot": "missing"})
        self.assertEqual(layer["state"], "headwind")

    def test_positioning_marks_both_missing_when_no_etf_or_cot_data(self):
        layer = build_site.make_positioning_layer([], [], [], [], today=self.TODAY)

        self.assertEqual(layer["sub_states"], {"etf": "missing", "cot": "missing"})
        self.assertEqual(layer["state"], "missing")
        self.assertEqual(layer["latest"]["etf_quality"], "missing")
        self.assertEqual(layer["latest"]["cot_quality"], "missing")

    def test_dashboard_exposes_six_rows_with_each_flow_source_and_date(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        layer_ids = [layer["id"] for layer in dashboard["driver_layers"]]
        row_ids = [row["id"] for row in dashboard["driver_rows"]]

        self.assertEqual(layer_ids, [
            "real_rate", "dollar", "inflation_expectation",
            "official_reserves", "positioning_technical",
        ])
        self.assertEqual(row_ids, [
            "real_rate", "dollar", "official_reserves",
            "inflation_expectation", "etf", "cot",
        ])
        self.assertEqual(dashboard["technical_layer"]["id"], "price_trend")
        self.assertEqual(
            [item["id"] for item in dashboard["recent_changes"]],
            ["macro", "official", "flows"],
        )

        positioning = dashboard["driver_layers"][-1]
        rows = {row["id"]: row for row in dashboard["driver_rows"]}
        self.assertEqual(rows["etf"]["date"], positioning["latest"]["etf_date"])
        self.assertEqual(rows["cot"]["date"], positioning["latest"]["cot_date"])
        self.assertNotEqual(rows["etf"]["date"], rows["cot"]["date"])
        self.assertEqual(rows["etf"]["quality"], positioning["latest"]["etf_quality"])
        self.assertEqual(rows["cot"]["quality"], positioning["latest"]["cot_quality"])
        self.assertIn("Wind", rows["etf"]["source"])
        self.assertNotIn("CFTC", rows["etf"]["source"])
        self.assertIn("CFTC", rows["cot"]["source"])

    def test_driver_html_shows_each_observation_date_with_quality(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        section = build_site.make_driver_section(dashboard["driver_rows"])

        self.assertIn('role="table" aria-label="当前主要驱动"', section)
        self.assertEqual(section.count('role="row"'), 7)
        self.assertEqual(section.count('role="columnheader"'), 7)
        self.assertEqual(section.count('role="cell"'), 42)
        self.assertNotIn('aria-hidden="true"', section)

        for row in dashboard["driver_rows"]:
            with self.subTest(driver=row["id"]):
                self.assertIn(
                    f'<div class="driver-row" data-driver-id="{row["id"]}" role="row">',
                    section,
                )
                self.assertIn(
                    f'<small>观测日 {row["date"] or "—"}</small>',
                    section,
                )
                self.assertIn(build_site.quality_badge(row["quality"]), section)
                self.assertNotIn(row["source"], section)

    def test_dashboard_uses_corrected_china_purchase_change(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        rows = {row["id"]: row for row in dashboard["driver_rows"]}
        changes = {item["id"]: item for item in dashboard["recent_changes"]}

        self.assertEqual(rows["official_reserves"]["value"], "+14.93 吨")
        self.assertIn("购金加速", changes["official"]["headline"])
        self.assertIn("本月 +14.93 吨", changes["official"]["detail"])
        self.assertIn("上月 +9.95 吨", changes["official"]["detail"])

    def test_flows_change_reports_incomplete_if_either_subsignal_is_missing(self):
        for states in [("missing", "headwind"), ("supportive", "missing")]:
            with self.subTest(states=states):
                changes = build_site.make_recent_changes(
                    self.recent_change_layers(*states))
                flows = changes[-1]
                self.assertIn("数据不完整", flows["headline"])
                self.assertNotIn("分化", flows["headline"])

    def test_flows_change_reports_divergence_only_when_both_subsignals_are_valid(self):
        changes = build_site.make_recent_changes(
            self.recent_change_layers("headwind", "supportive"))

        self.assertIn("分化", changes[-1]["headline"])

    def test_flows_change_does_not_call_matching_neutral_signals_divergent(self):
        changes = build_site.make_recent_changes(
            self.recent_change_layers("neutral", "neutral"))

        self.assertNotIn("分化", changes[-1]["headline"])

    def test_macro_change_calls_two_neutral_signals_stable_not_divergent(self):
        changes = build_site.make_recent_changes(self.recent_change_layers(
            "headwind", "supportive",
            real_state="neutral", dollar_state="neutral",
        ))

        macro = changes[0]
        self.assertEqual(macro["tone"], "neutral")
        self.assertIn("均偏稳", macro["headline"])
        self.assertNotIn("分化", macro["headline"])

    def test_macro_change_reports_incomplete_for_missing_or_very_stale_signal(self):
        cases = [
            {"real_state": "missing", "real_quality": "missing"},
            {"real_state": "headwind", "real_quality": "very-stale"},
        ]
        for case in cases:
            with self.subTest(case=case):
                changes = build_site.make_recent_changes(self.recent_change_layers(
                    "headwind", "supportive", dollar_state="headwind", **case,
                ))
                macro = changes[0]
                self.assertEqual(macro["tone"], "missing")
                self.assertIn("数据不完整", macro["headline"])
                self.assertNotIn("分化", macro["headline"])

    def test_official_change_calls_zero_purchase_flat(self):
        for previous_purchase in [9.95, -9.95]:
            with self.subTest(previous_purchase=previous_purchase):
                changes = build_site.make_recent_changes(self.recent_change_layers(
                    "headwind", "supportive",
                    current_purchase=0.0, previous_purchase=previous_purchase,
                ))
                official = changes[1]
                self.assertEqual(official["tone"], "neutral")
                self.assertIn("本月未继续增持", official["headline"])
                self.assertNotIn("购金加速", official["headline"])
                self.assertNotIn("净买入", official["headline"])


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

    def test_classifies_strict_bullish_and_bearish_alignment(self):
        bullish = build_site.classify_technical_state(120, 115, 110, 100, 0.03)
        bearish = build_site.classify_technical_state(80, 85, 90, 100, -0.03)
        self.assertEqual(bullish, {
            "short_term": "短期反弹", "medium_term": "中期偏多", "alignment": "多头排列"})
        self.assertEqual(bearish, {
            "short_term": "短期走弱", "medium_term": "中期偏空", "alignment": "空头排列"})

    def test_does_not_call_mixed_mas_a_complete_alignment(self):
        state = build_site.classify_technical_state(107, 106, 110, 105, 0.02)
        self.assertEqual(state["short_term"], "短期反弹")
        self.assertEqual(state["medium_term"], "中期修复")
        self.assertEqual(state["alignment"], "未形成完整排列")

    def test_alignment_allows_price_to_equal_ma20(self):
        bullish = build_site.classify_technical_state(115, 115, 110, 100, 0.03)
        bearish = build_site.classify_technical_state(85, 85, 90, 100, -0.03)
        self.assertEqual(bullish["alignment"], "多头排列")
        self.assertEqual(bearish["alignment"], "空头排列")

    def test_equal_adjacent_mas_are_not_complete_alignment(self):
        bullish_side = build_site.classify_technical_state(120, 110, 110, 100, 0.03)
        bearish_side = build_site.classify_technical_state(80, 90, 90, 100, -0.03)
        self.assertEqual(bullish_side["alignment"], "未形成完整排列")
        self.assertEqual(bearish_side["alignment"], "未形成完整排列")

    def test_reports_insufficient_history_without_inventing_a_trend(self):
        state = build_site.classify_technical_state(101, None, None, None, None)
        self.assertEqual(state, {
            "short_term": "样本不足", "medium_term": "样本不足", "alignment": "样本不足"})

    def test_layer_exposes_all_mas_and_current_contract(self):
        layer = build_site.make_price_trend_layer(self._rising())
        self.assertTrue({"ma20", "ma60", "ma200", "return_5d"} <= set(layer["latest"]))
        self.assertEqual(layer["technical"]["alignment"], "多头排列")
        self.assertEqual(layer["technical"]["medium_term"], "中期偏多")
        self.assertTrue({"gap_ma20", "gap_ma60", "gap_ma200"} <= set(layer["latest"]))
        self.assertIn("跌破", layer["technical"]["trigger"])

    def test_bearish_trigger_matches_medium_term_transition(self):
        layer = build_site.make_price_trend_layer(self._rising(start=5000.0, step=-5.0))
        self.assertEqual(layer["technical"]["medium_term"], "中期偏空")
        self.assertIn("转为中期偏多", layer["technical"]["trigger"])

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


class EvidenceChartRenderTests(unittest.TestCase):
    @staticmethod
    def _rolling(corr_values):
        rows = []
        day = date(2020, 1, 31)
        for corr in corr_values:
            rows.append({"date": day.isoformat(), "_date": day, "corr": corr})
            day = build_site.shift_months(day, 1)
        return rows

    def test_corr_tone_bands_translate_thresholds(self):
        self.assertEqual(
            build_site.corr_tone_bands("positive"),
            [(-1.0, -0.1, "失效"), (-0.1, 0.35, "偏弱"), (0.35, 1.0, "有效")])
        self.assertEqual(
            build_site.corr_tone_bands("negative"),
            [(-1.0, -0.35, "有效"), (-0.35, 0.1, "偏弱"), (0.1, 1.0, "失效")])
        self.assertEqual(
            [tone for _, _, tone in build_site.corr_tone_bands("absolute")],
            ["有效", "偏弱", "失效", "偏弱", "有效"])

    def test_bands_agree_with_corr_tone_at_probe_points(self):
        # 合同锁定:带只是 corr_tone 的可视化,任何探针点上两者不得矛盾。
        # 探针落在共享边界时属于相邻两带,断言 corr_tone 结果在其中即可。
        probes = [-0.99, -0.36, -0.35, -0.2, -0.1, -0.05, 0.0,
                  0.05, 0.1, 0.2, 0.35, 0.36, 0.99]
        for expected in ("positive", "negative", "absolute"):
            for probe in probes:
                tone = build_site.corr_tone(probe, expected=expected)
                hit = [t for lo, hi, t in build_site.corr_tone_bands(expected)
                       if lo <= probe <= hi]
                self.assertIn(tone, hit, msg=f"{expected} @ {probe}")

    def test_corr_chart_draws_bands_only_with_expected(self):
        rolling = self._rolling([0.6] * 30 + [0.0] * 30)
        series = [{"label": "24个月滚动相关", "rows": rolling, "color": "#237a57"}]
        with_bands = build_site.make_corr_chart(series, "测试滚动相关", expected="positive")
        without = build_site.make_corr_chart(series, "测试滚动相关")
        self.assertEqual(with_bands.count('class="tone-band"'), 3)
        self.assertEqual(with_bands.count('class="band-label"'), 3)
        for tone in ("有效", "偏弱", "失效"):
            self.assertIn(f">{tone}</text>", with_bands)
        self.assertNotIn("tone-band", without)
        self.assertIn('class="grid-line"', without)
        self.assertIn(">+0.5<", without)
        self.assertIn(">-0.5<", without)

    def test_absolute_bands_render_five_rects(self):
        rolling = self._rolling([0.6] * 30)
        html = build_site.make_corr_chart(
            [{"label": "x", "rows": rolling, "color": "#237a57"}], "t", expected="absolute")
        self.assertEqual(html.count('class="tone-band"'), 5)
        self.assertEqual(html.count(">有效</text>"), 2)

    def test_bands_paint_below_series_paths(self):
        rolling = self._rolling([0.6] * 30)
        html = build_site.make_corr_chart(
            [{"label": "x", "rows": rolling, "color": "#237a57"}], "t", expected="positive")
        self.assertLess(html.index('class="tone-band"'), html.index("<path"))

    @staticmethod
    def _trend_rows(count=80):
        rows = []
        day = date(2024, 1, 1)
        for i in range(count):
            rows.append({
                "date": day.isoformat(), "_date": day,
                "factor": 1.0 + i * 0.01, "gold_price": 2000.0 + i * 5,
            })
            day = day + timedelta(days=1)
        return rows

    def test_dual_axis_marks_inverted_axis_only_when_requested(self):
        rows = self._trend_rows()
        normal = build_site.make_dual_axis_chart(rows, "factor", "因子X")
        inverted = build_site.make_dual_axis_chart(
            rows, "factor", "因子X", invert_factor=True)
        self.assertNotIn("已反转", normal)
        self.assertIn("已反转", inverted)
        self.assertIn('class="grid-line"', normal)
        self.assertNotIn("data-hover-chart", normal)      # E14 暂缓,不得输出 hover 装置

    def test_dual_axis_inversion_flips_geometry_not_axis_values(self):
        rows = self._trend_rows()
        normal = build_site.make_dual_axis_chart(rows, "factor", "因子X")
        inverted = build_site.make_dual_axis_chart(
            rows, "factor", "因子X", invert_factor=True)

        def first_factor_y(html):
            match = re.search(r'<path d="M ([0-9.]+) ([0-9.]+)', html)
            return float(match.group(2))

        # 因子序列单调上升:正常轴首点在下(y 大),反转轴首点在上(y 小)
        self.assertGreater(first_factor_y(normal), first_factor_y(inverted))
        # 轴刻度是原始数值:正常轴顶端是最大值 1.79,反转轴顶端是最小值 1.00,
        # 两个数值在两种模式下都存在(只是位置对调),模板先渲染顶端刻度。
        for html in (normal, inverted):
            self.assertIn(">1.79<", html)
            self.assertIn(">1.00<", html)
        self.assertLess(normal.index(">1.79<"), normal.index(">1.00<"))
        self.assertLess(inverted.index(">1.00<"), inverted.index(">1.79<"))

    def test_bar_chart_shows_y_axis_scale(self):
        rows = [
            {"date": f"2025-{month:02d}-28", "_date": date(2025, month, 28), "change": value}
            for month, value in [(1, 10.0), (2, -5.0), (3, 15.0)]
        ]
        html = build_site.make_bar_chart(rows, "change", "测试柱状图", "china-bar")
        self.assertIn(">+15.0<", html)
        self.assertIn(">0<", html)
        self.assertIn(">-15.0<", html)


class EvidenceHookTests(unittest.TestCase):
    def test_real_rate_hook_explains_threshold_and_verdict(self):
        layer = {"id": "real_rate", "state": "headwind", "data_quality": "fresh",
                 "change": 0.18, "change_label": "+0.18pct", "lag_days": 1}
        html = build_site.make_evidence_hook(layer)
        self.assertIn("+0.18pct", html)          # 变化值
        self.assertIn("±0.05pct", html)          # REAL_RATE_CHANGE_THRESHOLD
        self.assertIn("判『压力』", html)          # 判定结论
        self.assertIn("#evidence-method", html)  # 完整规则链接

    def test_reserve_hook_uses_monthly_wording(self):
        layer = {"id": "official_reserves", "state": "supportive", "data_quality": "fresh",
                 "change": 5.0, "change_label": "+5.0 吨", "lag_days": 10}
        html = build_site.make_evidence_hook(layer)
        self.assertIn("+5.0 吨", html)
        self.assertIn("净买入判支持", html)
        self.assertIn("判『支持』", html)

    def test_missing_layer_hook_says_excluded_from_posture(self):
        layer = {"id": "dollar", "state": "missing", "data_quality": "missing",
                 "change": None, "change_label": "—", "lag_days": None}
        html = build_site.make_evidence_hook(layer)
        self.assertIn("首屏当前：未计入", html)
        self.assertIn("未计入首屏姿态", html)
        self.assertNotIn("判『", html)

    def test_very_stale_layer_hook_reports_lag_days_and_hides_state(self):
        layer = {"id": "real_rate", "state": "headwind", "data_quality": "very-stale",
                 "change": 0.18, "change_label": "+0.18pct", "lag_days": 30}
        html = build_site.make_evidence_hook(layer)
        self.assertIn("严重滞后", html)
        self.assertIn("30 天", html)
        self.assertIn("未计入首屏姿态", html)
        self.assertIn("首屏当前：未计入", html)
        self.assertNotIn("压力", html)   # 标题不得显示未参与投票的 state 徽章

    def test_positioning_hook_lists_sub_rules_and_vote(self):
        layer = {"id": "positioning_technical", "state": "neutral", "data_quality": "fresh",
                 "sub_states": {"etf": "supportive", "cot": "headwind"},
                 "latest": {"etf_change": 12.4, "managed_money_net_change": -18000},
                 "lag_days": 0}
        html = build_site.make_evidence_hook(layer)
        self.assertIn("±3 吨", html)             # ETF_CHANGE_THRESHOLD_TONNES
        self.assertIn("±15,000 张", html)        # COT_CHANGE_THRESHOLD_CONTRACTS
        self.assertIn("90%", html)               # COT_CROWDING_PERCENTILE
        self.assertIn("GVZ 不投票", html)
        self.assertIn("判『中性』", html)


class EvidenceUnitTests(unittest.TestCase):
    @staticmethod
    def _relationship(expected="negative"):
        rows = []
        day = date(2023, 1, 1)
        rolling = []
        for i in range(400):
            rows.append({
                "date": day.isoformat(), "_date": day,
                "real_rate": 1.0 + i * 0.002, "gold_price": 2000.0 + i * 3,
            })
            if i >= 100:
                rolling.append({
                    "date": day.isoformat(), "_date": day,
                    "corr": -0.6 if i < 250 else 0.3,
                })
            day = day + timedelta(days=1)
        return {
            "id": "real_rate", "name": "实际利率", "expected": expected,
            "latest_corr": rolling[-1]["corr"],
            "latest_tone": build_site.corr_tone(rolling[-1]["corr"], expected=expected),
            "rolling_corr": rolling, "trend_rows": rows,
            "factor_key": "real_rate", "factor_label": "实际利率",
            "gold_key": "gold_price",
            "read": "实际利率是持金机会成本,预期负相关。",
        }

    @staticmethod
    def _layer():
        return {"id": "real_rate", "state": "headwind", "data_quality": "fresh",
                "change": 0.18, "change_label": "+0.18pct", "lag_days": 1}

    def test_evidence_unit_assembles_summary_hook_charts_and_bands(self):
        html = build_site.make_evidence_unit(
            "evidence-real-rate", "实际利率", "FRED DFII10",
            self._relationship(), self._layer(),
            chart_limit=180, spark_limit=504, corr_label="252日滚动相关(中期)",
        )
        self.assertIn('<details class="evidence-unit" id="evidence-real-rate">', html)
        self.assertIn('class="evidence-summary"', html)
        self.assertIn("FRED DFII10", html)
        self.assertIn('class="evidence-hook"', html)
        self.assertIn("±0.05pct", html)                  # 钩子含阈值推导
        self.assertIn("已反转", html)                     # expected=negative → 主图反转
        self.assertIn('class="tone-band"', html)          # 演变图阈值带
        self.assertIn('class="evidence-spark"', html)
        self.assertIn('class="sparkline"', html)
        self.assertIn("滚动相关", html)
        self.assertIn('class="evidence-body"', html)
        self.assertIn("重叠样本", html)                    # 尾注 n 说明(E9)

    def test_positive_expected_unit_does_not_invert(self):
        relationship = self._relationship(expected="positive")
        html = build_site.make_evidence_unit(
            "evidence-x", "测试因子", "来源", relationship, self._layer(),
            chart_limit=84, spark_limit=24, corr_label="24个月滚动相关",
        )
        self.assertNotIn("已反转", html)

    def test_unit_without_layer_has_no_hook(self):
        html = build_site.make_evidence_unit(
            "evidence-aux", "辅助", "来源", self._relationship(), None,
            chart_limit=180, spark_limit=504, corr_label="252日滚动相关(中期)",
        )
        self.assertNotIn("evidence-hook", html)


class ResearchSectionTests(unittest.TestCase):
    TODAY = _date(2026, 7, 7)   # 与现有集成测试类一致(避免两套快照日期)

    def test_tone_counts_text_orders_and_skips_zero(self):
        self.assertEqual(
            build_site.tone_counts_text(["有效", "失效", "有效"]), "2 有效 · 1 失效")
        self.assertEqual(build_site.tone_counts_text(["偏弱"]), "1 偏弱")
        # 「样本不足」必须显式计入,不得静默省略(缺失显式化红线)
        self.assertEqual(
            build_site.tone_counts_text(["有效", "失效", "样本不足"]),
            "1 有效 · 1 失效 · 1 样本不足")
        self.assertEqual(build_site.tone_counts_text(["样本不足"]), "1 样本不足")

    def test_research_section_exposes_seven_evidence_units(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        html = build_site.build_html(dashboard)
        self.assertEqual(html.count('<details class="evidence-unit"'), 7)
        for anchor in [
            "evidence-real-rate", "evidence-dollar", "evidence-reserve",
            "evidence-inflation", "evidence-positioning",
            "evidence-auxiliary", "evidence-method",
        ]:
            self.assertIn(f'id="{anchor}"', html)
        self.assertNotIn('<details class="research-evidence">', html)
        self.assertNotIn("阶段表现", html)
        self.assertNotIn('class="phase-table"', html)
        self.assertNotIn('class="metric-strip"', html)
        self.assertIn("研究依据", html)
        self.assertIn("不参与首页姿态", html)          # 辅助观察提示
        self.assertIn("官方购金历史", html)             # 柱状图并入购金单元
        self.assertIn("数据质量与来源", html)
        self.assertIn("计算方法与已知局限", html)

    def test_driver_rows_link_to_evidence_units(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        html = build_site.build_html(dashboard)
        self.assertIn(
            'href="#evidence-real-rate" data-evidence-target="evidence-real-rate"', html)
        self.assertIn(
            'href="#evidence-reserve" data-evidence-target="evidence-reserve"', html)
        self.assertEqual(html.count('data-evidence-target="evidence-positioning"'), 2)

    def test_positioning_summary_shows_tone_counts_not_average(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        html = build_site.build_html(dashboard)
        start = html.index('id="evidence-positioning"')
        body_start = html.index('<div class="evidence-body"', start)
        summary_html = html[start:body_start]
        self.assertIn('class="evidence-count"', summary_html)
        self.assertRegex(summary_html, r"\d+ (有效|偏弱|失效)")
        self.assertNotIn("滚动相关", summary_html)      # summary 无任何合成相关值
        self.assertNotIn("sparkline", summary_html)     # summary 无 sparkline(E16)

    def test_gvz_sub_block_is_not_inverted_and_positioning_has_sub_blocks(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        html = build_site.build_html(dashboard)
        start = html.index('id="evidence-positioning"')
        end = html.index('id="evidence-auxiliary"')
        positioning_html = html[start:end]
        self.assertIn("ETF 持仓", positioning_html)
        self.assertIn("Managed Money 净多", positioning_html)
        self.assertIn("GVZ 波动率", positioning_html)
        gvz_part = positioning_html[positioning_html.index("GVZ 波动率"):]
        self.assertNotIn("已反转", gvz_part)

    def test_positioning_relationship_has_no_composite_corr(self):
        dashboard = build_site.read_dashboard_data(today=self.TODAY)
        positioning = next(
            item for item in dashboard["relationships"]
            if item["id"] == "positioning_technical")
        self.assertNotIn("latest_corr", positioning)
        self.assertNotIn("latest_tone", positioning)


class ResearchInteractionScriptTests(unittest.TestCase):
    TODAY = _date(2026, 7, 7)   # 与 ResearchSectionTests 一致

    def _html(self):
        return build_site.build_html(build_site.read_dashboard_data(today=self.TODAY))

    def test_html_embeds_evidence_link_script_without_hover_machinery(self):
        html = self._html()
        self.assertIn("link.dataset.evidenceTarget", html)
        self.assertIn('target.tagName === "DETAILS"', html)
        self.assertNotIn("data-hover-chart", html)   # E14 暂缓:图表与 JS 均无 hover 装置

    def test_styles_give_corr_chart_more_room_and_hide_spark_chrome(self):
        html = self._html()
        self.assertIn(".evidence-body { max-width: 760px;", html)
        self.assertIn(".evidence-body svg { max-width: 520px;", html)
        self.assertIn(".evidence-body .corr-chart { max-width: 720px;", html)
        self.assertIn(".evidence-spark svg text", html)
        self.assertNotIn(".phase-table", html)
        self.assertNotIn(".metric-strip", html)
        self.assertNotIn(".relationship-grid", html)
        self.assertNotIn(".research-evidence", html)

    def test_wide_decision_grid_reserves_room_for_evidence_column(self):
        html = self._html()
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) 290px;", html)
        self.assertIn("@media (max-width: 1160px)", html)

    def test_compact_sparkline_uses_the_whole_tiny_viewbox(self):
        rows = [
            {"date": f"2026-01-{day:02d}", "corr": value}
            for day, value in enumerate([-0.5, 0.0, 0.5], start=1)
        ]
        html = build_site.make_sparkline(
            rows, "corr", width=64, height=16, compact=True)

        self.assertIn('viewBox="0 0 64 16"', html)
        self.assertNotIn("<line", html)
        self.assertNotIn("<text", html)
        path = re.search(r'<path d="([^"]+)"', html).group(1)
        coords = [
            (float(x), float(y))
            for x, y in re.findall(r"(?:M|L) ([0-9.]+) ([0-9.]+)", path)
        ]
        self.assertGreater(coords[-1][0] - coords[0][0], 50)
        self.assertTrue(all(0 <= x <= 64 and 0 <= y <= 16 for x, y in coords))


if __name__ == "__main__":
    unittest.main()
