import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import refresh_wind_data as rwd


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


class CompareSeriesTest(unittest.TestCase):
    def test_flags_only_real_mismatches_on_shared_dates(self):
        wind = {"2026-06-24": 3991.7, "2026-06-23": 4112.07}
        excel = {"2026-06-24": 3991.7, "2026-06-23": 4000.0, "2026-06-20": 4180.87}
        self.assertEqual(rwd.compare_series(wind, excel), [("2026-06-23", 4112.07, 4000.0)])

    def test_ignores_blank_excel_values(self):
        self.assertEqual(rwd.compare_series({"2026-06-24": 3991.7}, {"2026-06-24": ""}), [])


if __name__ == "__main__":
    unittest.main()
