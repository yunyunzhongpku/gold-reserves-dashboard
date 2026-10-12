import calendar
import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import refresh_gold_context as r


class GoldContextTest(unittest.TestCase):
    def test_revision_guard_reports_all_changes_and_allows_new_disclosures(self):
        old = [{"date": "2026-07-31", "us": 1., "cn": None}, {"date": "2026-08-31", "us": 2.}]
        r.reconcile(old, [old[0] | {"cn": 3.}, old[1]])
        with self.assertRaisesRegex(ValueError, "2026-07-31 us: 1.0 -> 3.0[\s\S]*2026-08-31 us: 2.0 -> 4.0"):
            r.reconcile(old, [old[0] | {"us": 3.}, old[1] | {"us": 4.}])

    def test_copper_month_end_and_future_exclusion(self):
        raw = b"observation_date,PCOPPUSDM\n2026-08-01,100\n2026-09-01,200\n2026-10-01,999\n"
        self.assertEqual(r.parse_fred(raw, "PCOPPUSDM", date(2026, 9, 30), True), {"2026-08-31": 100., "2026-09-30": 200.})

    def test_missing_prices_remain_null_and_invalid_quotes_fail(self):
        self.assertEqual(r.parse_fred(b"observation_date,BTC\n2026-10-08,.\n2026-10-09,10\n", "BTC", date(2026, 10, 9))["2026-10-08"], None)
        for value in ("NaN", "inf", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                r.parse_fred(f"observation_date,BTC\n2026-10-09,{value}\n".encode(), "BTC", date(2026, 10, 9))

    def test_duplicate_quotes_fail(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            r.parse_fred(b"observation_date,BTC\n2026-10-09,1\n2026-10-09,2\n", "BTC", date(2026, 10, 9))

    def test_first_baseline_and_partial_baseline_fail_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / name for name in ("money.csv", "assets.csv", "sources.json")]
            with mock.patch.multiple(r, MONTHLY=paths[0], ASSETS=paths[1], SOURCES=paths[2]), mock.patch.object(r.m2, "refresh_public") as fetch:
                with self.assertRaisesRegex(ValueError, "requires reviewed"):
                    r.refresh()
                paths[0].write_text("old")
                with self.assertRaisesRegex(ValueError, "Partial"):
                    r.refresh(initialize_reviewed=True)
                fetch.assert_not_called()
                self.assertEqual(paths[0].read_text(), "old")

    def test_partial_install_restores_original_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / str(i) for i in range(3)]
            for p in paths:
                p.write_bytes(b"old")
            real_replace = r.os.replace
            calls = 0
            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated disk failure")
                return real_replace(source, target)
            with mock.patch.object(r.os, "replace", side_effect=fail_second), self.assertRaises(OSError):
                r.install_files([(p, b"new") for p in paths])
            self.assertEqual([p.read_bytes() for p in paths], [b"old"] * 3)

    def test_native_units_totals_anchor_and_incomplete_basket(self):
        rows = []
        for year in range(2000, 2021):
            for month in range(1, 13):
                day = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
                rows.append({"date": day, "us_m2_usd_bn": 10., "cn_m2_cny_bn": 20., "ea_m2_eur_bn": 30., "usd_per_cny": .2, "usd_per_eur": 2., "total_usd_bn": 74., "total_fixed_usd_bn": 57., "cny_fx_date": day, "eur_fx_date": day})
        meta = {"fixed_usd_per_cny": .1, "fixed_usd_per_eur": 1.5, "fixed_fx_month": "2020-12"}
        self.assertEqual(len(r.validate_monthly(rows, meta)), 252)
        broken = copy.deepcopy(rows)
        broken[-1]["total_fixed_usd_bn"] = 0
        with self.assertRaisesRegex(ValueError, "calculation mismatch"):
            r.validate_monthly(broken, meta)
        broken[-1].update(cn_m2_cny_bn=None, total_fixed_usd_bn=None, total_usd_bn=None)
        self.assertEqual(len(r.validate_monthly(broken, meta)), 251)
        broken[-2].update(cn_m2_cny_bn=None, total_fixed_usd_bn=None, total_usd_bn=None)
        # A missing latest tail is honest; a hole followed by a valid month is not.
        broken[-1] = rows[-1]
        with self.assertRaisesRegex(ValueError, "Internal M2 gap"):
            r.validate_monthly(broken, meta)

    def test_last_complete_month_has_no_fixed_date(self):
        self.assertEqual(r.previous_month(date(2026, 10, 12)), date(2026, 9, 30))
        self.assertEqual(r.previous_month(date(2028, 3, 1)), date(2028, 2, 29))


if __name__ == "__main__":
    unittest.main()
