import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import refresh_wind_data as rwd
from scripts import refresh_market_data as rmd


class BuildWindDailyRowsTest(unittest.TestCase):
    def test_merges_keys_by_date_and_keeps_partial_rows(self):
        series = {
            "gold_price": {"2026-06-23": 4112.07, "2026-06-24": 3991.7},
            "dollar_index": {"2026-06-24": 101.5745},
            "gvz": {"2026-05-29": 24.91},
        }
        rows = rwd.build_wind_daily_rows(series)
        self.assertEqual([r["date"] for r in rows], ["2026-05-29", "2026-06-23", "2026-06-24"])
        self.assertEqual(rows[0], {"date": "2026-05-29", "gold_price": "", "dollar_index": "", "gvz": 24.91})
        self.assertEqual(rows[-1], {"date": "2026-06-24", "gold_price": 3991.7, "dollar_index": 101.5745, "gvz": ""})


class OverseasMarketCalendarTest(unittest.TestCase):
    def test_daily_cutoff_waits_until_us_close_and_publication_buffer(self):
        cases = [
            (datetime(2026, 10, 7, 12, tzinfo=timezone.utc), date(2026, 10, 6)),
            (datetime(2026, 10, 7, 21, 59, tzinfo=timezone.utc), date(2026, 10, 6)),
            (datetime(2026, 10, 7, 22, tzinfo=timezone.utc), date(2026, 10, 7)),
            (datetime(2026, 1, 7, 23, tzinfo=timezone.utc), date(2026, 1, 7)),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(rwd.latest_closed_market_date(now), expected)

    def test_keeps_overseas_observations_during_china_holiday(self):
        response = SimpleNamespace(
            ErrorCode=0,
            Times=[date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 5),
                   date(2026, 10, 6), date(2026, 10, 7)],
            Data=[[4177.32, 4142.75, float("nan"), 4164.29, 4164.29]],
        )
        wind = SimpleNamespace(start=Mock(), wsd=Mock(return_value=response))
        with patch.dict(sys.modules, {"tkf_wind": SimpleNamespace(w=wind)}), \
                patch.object(rwd, "latest_closed_market_date", return_value=date(2026, 10, 6)):
            result = rwd.fetch_wind_series(
                {"gold_price": "SPTAUUSDOZ.IDC"}, "2026-10-01", "2026-10-07"
            )
        self.assertEqual(result, {"gold_price": {"2026-10-01": 4177.32, "2026-10-06": 4164.29}})
        wind.wsd.assert_called_once_with(
            "SPTAUUSDOZ.IDC", "close", "2026-10-01", "2026-10-06", rwd.MARKET_OPTIONS
        )
        self.assertIn("TradingCalendar=NYSE", wind.wsd.call_args.args[-1])

    def test_historical_request_is_not_extended_to_current_market_day(self):
        response = SimpleNamespace(ErrorCode=0, Times=[date(2026, 7, 2)], Data=[[4000.0]])
        wind = SimpleNamespace(start=Mock(), wsd=Mock(return_value=response))
        with patch.dict(sys.modules, {"tkf_wind": SimpleNamespace(w=wind)}), \
                patch.object(rwd, "latest_closed_market_date", return_value=date(2026, 10, 6)):
            result = rwd.fetch_wind_series(
                {"gold_price": "SPTAUUSDOZ.IDC"}, "2026-07-01", "2026-07-02"
            )
        self.assertEqual(result["gold_price"], {"2026-07-02": 4000.0})
        self.assertEqual(wind.wsd.call_args.args[3], "2026-07-02")

    def test_unfinished_only_range_stops_before_querying_wind(self):
        wind = SimpleNamespace(start=Mock(), wsd=Mock())
        with patch.dict(sys.modules, {"tkf_wind": SimpleNamespace(w=wind)}), \
                patch.object(rwd, "latest_closed_market_date", return_value=date(2026, 10, 6)):
            with self.assertRaisesRegex(ValueError, "No completed overseas market day"):
                rwd.fetch_wind_series({"gold_price": "SPTAUUSDOZ.IDC"}, "2026-10-07", "2026-10-07")
        wind.start.assert_not_called()
        wind.wsd.assert_not_called()


class CompareSeriesTest(unittest.TestCase):
    def test_excel_anchor_uses_latest_workbook(self):
        self.assertEqual(rwd.DATA_FILE.name, "招商证券：黄金图表整理2607.xlsx")
        anchors = rwd.load_excel_anchor_series()
        self.assertAlmostEqual(anchors["gold_price"]["2026-07-06"], 4164.383)
        self.assertAlmostEqual(anchors["dollar_index"]["2026-07-06"], 100.8721)
        self.assertAlmostEqual(anchors["gvz"]["2026-07-06"], 25.33)

    def test_flags_only_real_mismatches_on_shared_dates(self):
        wind = {"2026-06-24": 3991.7, "2026-06-23": 4112.07}
        excel = {"2026-06-24": 3991.7, "2026-06-23": 4000.0, "2026-06-20": 4180.87}
        self.assertEqual(rwd.compare_series(wind, excel), [("2026-06-23", 4112.07, 4000.0)])

    def test_ignores_blank_excel_values(self):
        self.assertEqual(rwd.compare_series({"2026-06-24": 3991.7}, {"2026-06-24": ""}), [])


class CsvWriterTest(unittest.TestCase):
    def test_wind_writer_uses_lf_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wind_daily.csv"
            rwd.write_csv(
                path,
                [{"date": "2026-07-03", "gold_price": 4174.189, "dollar_index": 100.8749, "gvz": 26.0}],
                rwd.FIELDNAMES,
            )
            self.assertNotIn(b"\r", path.read_bytes())

    def test_market_writer_uses_lf_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fred_t10yie.csv"
            rmd.write_csv(path, [{"date": "2026-07-02", "breakeven_10y": 2.23}], ["date", "breakeven_10y"])
            self.assertNotIn(b"\r", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
