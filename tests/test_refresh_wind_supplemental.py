import calendar
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import build_site
from scripts import refresh_wind_supplemental as supplemental


def daily_values(year, month, value=1.0, missing_day=None):
    last_day = calendar.monthrange(year, month)[1]
    return {
        date(year, month, day).isoformat(): value
        for day in range(1, last_day + 1)
        if day != missing_day
    }


class MonthlyMeansTest(unittest.TestCase):
    def test_returns_complete_month_with_month_end_key(self):
        values = daily_values(2026, 1)
        values["2026-01-31"] = 32.0

        self.assertEqual(
            supplemental.monthly_means(values, date(2026, 2, 15)),
            {"2026-01-31": 2.0},
        )

    def test_excludes_current_incomplete_calendar_month(self):
        values = daily_values(2026, 1)
        values.update(daily_values(2026, 2))

        self.assertEqual(
            supplemental.monthly_means(values, date(2026, 2, 15)),
            {"2026-01-31": 1.0},
        )

    def test_excludes_month_with_missing_day(self):
        values = daily_values(2026, 1, missing_day=12)

        self.assertEqual(
            supplemental.monthly_means(values, date(2026, 2, 15)),
            {},
        )


class ReconcileSeriesTest(unittest.TestCase):
    def test_accepts_rounding_noise_but_blocks_unregistered_change(self):
        anchors = {"2026-01-31": 100.0}
        supplemental.reconcile_series(
            "spdr_holdings",
            {"2026-01-31": 100.00005},
            anchors,
            {},
        )

        with self.assertRaises(ValueError):
            supplemental.reconcile_series(
                "spdr_holdings",
                {"2026-01-31": 100.001},
                anchors,
                {},
            )

    def test_allows_only_exact_registered_revision_pair(self):
        anchors = {"2026-01-31": 100.0}
        revisions = {
            "spdr_holdings": {
                "2026-01-31": {"old": 100.0, "new": 101.0},
            }
        }
        supplemental.reconcile_series(
            "spdr_holdings",
            {"2026-01-31": 101.0},
            anchors,
            revisions,
        )

        with self.assertRaises(ValueError):
            supplemental.reconcile_series(
                "spdr_holdings",
                {"2026-01-31": 101.00000001},
                anchors,
                revisions,
            )

    def test_blocks_series_without_overlap(self):
        with self.assertRaises(ValueError):
            supplemental.reconcile_series(
                "spdr_holdings",
                {"2026-02-28": 101.0},
                {"2026-01-31": 100.0},
                {},
            )


class MergeSupplementalRowsTest(unittest.TestCase):
    def test_partial_new_row_replaces_group_without_forward_fill(self):
        keys = ["spdr_holdings", "ishares_holdings"]
        base_rows = [{
            "date": "2026-01-31",
            "_date": date(2026, 1, 31),
            "spdr_holdings": 900.0,
            "ishares_holdings": 500.0,
        }]
        new_rows = [
            {"date": "2026-01-31", "spdr_holdings": 901.0},
            {"date": "2026-02-28", "ishares_holdings": 501.0},
            {"date": "2026-03-31", "unrelated": 1.0},
        ]

        rows = build_site.merge_supplemental_rows(base_rows, new_rows, keys)

        self.assertEqual([row["date"] for row in rows], ["2026-01-31", "2026-02-28"])
        self.assertEqual(rows[0]["spdr_holdings"], 901.0)
        self.assertIsNone(rows[0]["ishares_holdings"])
        self.assertIsNone(rows[1]["spdr_holdings"])
        self.assertEqual(rows[1]["ishares_holdings"], 501.0)


class FreshnessAndValuationTest(unittest.TestCase):
    def test_csv_reader_excludes_future_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "observations.csv"
            path.write_text("date,epu\n2026-07-31,123\n2026-08-31,456\n")
            rows = build_site.read_csv_rows(path, ["epu"], max_date=date(2026, 8, 15))
        self.assertEqual([row["date"] for row in rows], ["2026-07-31"])

    def test_latest_incomplete_valuation_does_not_replace_valid_month(self):
        complete = {"date": "2026-07-31", "gold_price": 4000.0,
                    "gold_to_m2": 0.52, "valuation_percentile": 0.66}
        incomplete = {"date": "2026-08-31", "gold_price": 4500.0,
                      "gold_to_m2": None, "valuation_percentile": 0.0}
        self.assertEqual(build_site.make_valuation_snapshot([complete, incomplete]), complete)

    def test_missing_valuation_is_not_zero_percentile(self):
        self.assertIsNone(build_site.make_valuation_snapshot([
            {"date": "2026-08-31", "gold_price": 4500.0,
             "gold_to_m2": None, "valuation_percentile": 0.0}]))


if __name__ == "__main__":
    unittest.main()
