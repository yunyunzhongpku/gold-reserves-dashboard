"""Behavior checks for research-only currency conversions and data boundaries."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import research_liquidity_data as model


class LiquidityDataTests(unittest.TestCase):
    def test_calendar_month_end_and_missing_are_not_zero(self):
        self.assertEqual(model.month_end("2020-02"), "2020-02-29")
        self.assertEqual(model.month_end("2021-02"), "2021-02-28")
        self.assertIsNone(model.number("."))
        self.assertIsNone(model.number("nan"))
        self.assertEqual(model.number("0"), 0)

    def test_daily_fx_uses_last_valid_observation_without_month_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            (raw / "cny_per_usd.csv").write_text("observation_date,DEXCHUS\n2020-01-30,7\n2020-01-31,.\n2020-03-31,8\n2026-10-20,9\n")
            with patch.object(model, "RAW", raw):
                result = model.parse_fred("cny_per_usd", daily=True, invert=True)
            self.assertEqual(result["2020-01"], (1 / 7, "2020-01-30"))
            self.assertNotIn("2020-02", result)
            self.assertNotIn("2026-10", result)

    def test_china_duplicate_month_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            (raw / "china_m2_wind.csv").write_text("date,m2_cny_100m\n2020-01-31,100\n2020-01-30,200\n")
            with patch.object(model, "RAW", raw):
                with self.assertRaises(ValueError):
                    model.parse_china()

    def test_ecb_aggregate_or_unit_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            fields = ["TIME_PERIOD", "OBS_VALUE", "ADJUSTMENT", "UNIT", "UNIT_MULT", "DATA_TYPE", "BS_ITEM"]
            with (raw / "ea_m2.csv").open("w", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=fields)
                writer.writeheader()
                writer.writerow(dict(zip(fields, ["2020-01", "1000", "N", "EUR", "6", "1", "M30"])))
            with patch.object(model, "RAW", raw):
                with self.assertRaises(ValueError):
                    model.parse_ecb()

    def test_saved_panel_currency_totals_and_exact_yoy_decomposition(self):
        with (model.HERE / "liquidity_monthly.csv").open() as source:
            rows = list(csv.DictReader(source))
        manifest = json.loads((model.HERE / "liquidity_sources.json").read_text())
        self.assertEqual(len(rows), 320)
        self.assertEqual(len({row["date"] for row in rows}), len(rows))
        for row in rows:
            usd, cny, eur = (float(row[name]) for name in ("us_m2_usd_bn", "cn_m2_cny_bn", "ea_m2_eur_bn"))
            self.assertAlmostEqual(float(row["total_usd_bn"]), usd + cny * float(row["usd_per_cny"]) + eur * float(row["usd_per_eur"]), places=7)
            self.assertAlmostEqual(float(row["total_fixed_usd_bn"]), usd + cny * manifest["fixed_usd_per_cny"] + eur * manifest["fixed_usd_per_eur"], places=7)
            self.assertLessEqual(row["date"], model.AS_OF.isoformat())
            if row["m2_current_fx_yoy_pct"]:
                self.assertAlmostEqual(float(row["m2_current_fx_yoy_pct"]), float(row["local_contribution_pct"]) + float(row["fx_contribution_pct"]), places=9)
            else:
                self.assertEqual(row["local_contribution_pct"], "")
                self.assertEqual(row["fx_contribution_pct"], "")


if __name__ == "__main__":
    unittest.main()
