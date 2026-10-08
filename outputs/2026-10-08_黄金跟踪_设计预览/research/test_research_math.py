"""Independent behavior checks for descriptive monthly relationship research."""
import math
import random
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import build_research as model


def reference_correlation(a, b):
    """Independent raw-moment implementation of Pearson correlation."""
    n = len(a)
    covariance = sum(x * y for x, y in zip(a, b)) - sum(a) * sum(b) / n
    vx = sum(x * x for x in a) - sum(a) ** 2 / n
    vy = sum(y * y for y in b) - sum(b) ** 2 / n
    return covariance / math.sqrt(vx * vy)


def reference_partial(points):
    x, y, z = ([point[column] for point in points] for column in (1, 2, 3))
    xy, xz, yz = (reference_correlation(a, b) for a, b in ((x, y), (x, z), (y, z)))
    return (xy - xz * yz) / math.sqrt((1 - xz ** 2) * (1 - yz ** 2))


def sample_points(n=120):
    rng = random.Random(1457)
    points = []
    for i in range(n):
        month = model.shift_month("2010-01", i)
        z, e1, e2 = [rng.gauss(0, 1) for _ in range(3)]
        points.append((month, .8 * z + e1, -.6 * z + .35 * e1 + e2, z))
    return points


class ResearchMathTests(unittest.TestCase):
    def test_log_changes_does_not_bridge_missing_month(self):
        changes = model.log_changes({"2020-01": 100, "2020-03": 121, "2020-04": 133.1})
        self.assertNotIn("2020-03", changes)
        self.assertAlmostEqual(changes["2020-04"], math.log(1.1))

    def test_monthly_prices_excludes_current_month_and_stale_month_end(self):
        rows = [{"date": "2026-08-28", "price": 100}, {"date": "2026-08-31", "price": 101},
                {"date": "2026-09-18", "price": 105}, {"date": "2026-10-07", "price": 110},
                {"date": "2027-01-31", "price": 120}]
        prices = model.monthly_prices(rows, "price", date(2026, 10, 8))
        self.assertEqual(prices, {"2026-08": {"value": 101, "date": "2026-08-31"}})

    def test_partial_dollar_matches_analytical_formula_and_units(self):
        points = sample_points()
        actual = model.partial_corr(points)
        self.assertAlmostEqual(actual, reference_partial(points), places=12)
        shifted = [(month, x + 9 * z + 200, 4 * y - 3 * z - 50, z * 7 + 18)
                   for month, x, y, z in points]
        self.assertAlmostEqual(model.partial_corr(shifted), actual, places=12)

    def test_calendar_sensitivity_controls_all_three_variables(self):
        points = sample_points()
        seasonal = [(month, x + int(month[5:7]) * 4, y - int(month[5:7]) * 7,
                     z + int(month[5:7]) * 3) for month, x, y, z in points]
        self.assertAlmostEqual(model.partial_corr(points, seasonal=True),
                               model.partial_corr(seasonal, seasonal=True), places=12)
        self.assertGreater(abs(model.partial_corr(seasonal) - model.partial_corr(seasonal, seasonal=True)), .1)

    def test_repeated_months_keep_bootstrap_sample_weights(self):
        points = sample_points(36)
        sampled = []

        def observe_sample(batch):
            sampled.append(batch)
            return reference_partial(batch)

        with patch.object(model, "partial_corr", side_effect=observe_sample):
            interval = model.block_interval(points, repetitions=30)
        self.assertEqual(len(sampled), 30)
        self.assertTrue(all(len(batch) == 36 for batch in sampled))
        self.assertTrue(any(len({p[0] for p in batch}) < len(batch) for batch in sampled))
        batch = next(batch for batch in sampled if len({p[0] for p in batch}) < len(batch))
        self.assertAlmostEqual(model.partial_corr(batch), reference_partial(batch), places=12)
        unique = list({p[0]: p for p in batch}.values())
        self.assertGreater(abs(model.partial_corr(batch) - model.partial_corr(unique)), 1e-5)
        estimates = sorted(reference_partial(batch) for batch in sampled)
        self.assertEqual(interval, [estimates[0], estimates[29]])
        self.assertIsNone(model.block_interval(points[:35], repetitions=30))

    def test_monthly_fx_translation_is_exact_symmetric_component(self):
        # Only CNY changes in balance and FX; EUR and USD are held constant.
        previous = {"date": "2020-01-31", "cn_m2_cny_bn": 100, "usd_per_cny": .2,
                    "ea_m2_eur_bn": 50, "usd_per_eur": 1, "total_usd_bn": 100}
        current = {"date": "2020-02-29", "cn_m2_cny_bn": 120, "usd_per_cny": .25,
                   "ea_m2_eur_bn": 50, "usd_per_eur": 1, "total_usd_bn": 110}
        contribution = model.monthly_fx_contribution([previous, current])["2020-02"]
        self.assertAlmostEqual(contribution, .055)
        local_contribution = (.2 + .25) / 2 * (120 - 100) / 100
        self.assertAlmostEqual(local_contribution + contribution, 110 / 100 - 1)
        missing_month = {**current, "date": "2020-03-31"}
        self.assertEqual(model.monthly_fx_contribution([previous, missing_month]), {})

    def test_current_inputs_and_preselected_lags_use_identical_target_months(self):
        today = date(2026, 10, 8)
        money_rows = model.read_rows(model.HERE / "liquidity_monthly.csv", today)
        gold_rows = model.read_rows(model.ROOT / "data/market/wind_daily.csv", today)
        asset_rows = model.read_rows(model.HERE / "assets_daily.csv", today)
        prices = {"gold": model.monthly_prices(gold_rows, "gold_price", today),
                  **{key: model.monthly_prices(asset_rows, key, today)
                     for key in model.ASSETS if key != "gold"}}
        dollar = model.log_changes({m: p["value"] for m, p in model.monthly_prices(gold_rows, "dollar_index", today).items()})
        changes = {kind: model.log_changes({r["date"][:7]: r[field] for r in money_rows if r.get(field)})
                   for kind, field in (("usd", "total_usd_bn"), ("fixed", "total_fixed_usd_bn"))}
        for name, source in prices.items():
            returns = model.log_changes({m: p["value"] for m, p in source.items()})
            contemporaneous = {kind: model.points_for(returns, values, dollar) for kind, values in changes.items()}
            targets = set(p[0] for p in contemporaneous["usd"]) & set(p[0] for p in contemporaneous["fixed"])
            self.assertTrue(targets, name)
            ordered = sorted(targets)
            self.assertTrue(all(model.shift_month(a, 1) == b for a, b in zip(ordered, ordered[1:])), name)
            for lag in (0, 1, 3, 6):
                for kind, values in changes.items():
                    points = [p for p in model.points_for(returns, values, dollar, lag) if p[0] in targets]
                    self.assertEqual({p[0] for p in points}, targets, (name, kind, lag))
                    self.assertTrue(all(p[1] == values[model.shift_month(p[0], -lag)] and p[3] == dollar[p[0]] for p in points))

    def test_lag_summary_intersects_every_lag_and_fx_method_with_gaps(self):
        months = [model.shift_month("2015-01", i) for i in range(50)]
        macro, gold, assets = [], [], []
        for i, month in enumerate(months):
            when = model.end_month(month)
            cny, eur = 1000 + 17 * i, 700 + 6 * i
            cnfx, eurfx = .16 + .001 * math.sin(i), 1.15 + .02 * math.cos(i)
            if i != 20:  # One completely absent macro month.
                macro.append({"date": when, "cn_m2_cny_bn": cny, "ea_m2_eur_bn": eur,
                              "usd_per_cny": cnfx, "usd_per_eur": eurfx,
                              "total_usd_bn": 100 + cny * cnfx + eur * eurfx,
                              "total_fixed_usd_bn": None if i == 8 else 100 + cny * .16 + eur * 1.15})
            gold.append({"date": when, "gold_price": 300 + i ** 2,
                         "dollar_index": 100 + .1 * i + .05 * math.cos(i)})
            assets.append({"date": when, "silver": 500 + i ** 1.3, "bitcoin": 1000 + 20 * i + 5 * math.sin(i)})

        def source(path, today):
            return {"liquidity_monthly.csv": macro, "wind_daily.csv": gold,
                    "assets_daily.csv": assets}[path.name]

        # make_research is evaluated without writing its normal JSON/report files.
        with patch.object(model, "read_rows", side_effect=source), \
             patch.object(model, "block_interval", return_value=None), \
             patch.object(Path, "write_text", return_value=0):
            result = model.make_research(date(2026, 10, 8))
        levels = {"usd": {row["date"][:7]: row["total_usd_bn"] for row in macro},
                  "fixed": {row["date"][:7]: row["total_fixed_usd_bn"] for row in macro if row["total_fixed_usd_bn"] is not None}}
        # Independently require current/previous source months for every lag.
        expected_indices = [i for i in range(1, 50)
                            if all(i - lag >= 1 and months[i-lag] in by_month and months[i-lag-1] in by_month
                                   for lag in (0, 1, 3, 6) for by_month in levels.values())]
        self.assertTrue(expected_indices)
        for entry in result["lags"]["gold"]:
            lag = entry["lag"]
            for kind, by_month in levels.items():
                expected = [(months[i], math.log(by_month[months[i-lag]] / by_month[months[i-lag-1]]),
                             math.log(gold[i]["gold_price"] / gold[i-1]["gold_price"]),
                             math.log(gold[i]["dollar_index"] / gold[i-1]["dollar_index"])) for i in expected_indices]
                self.assertEqual(entry[kind]["n"], len(expected))
                self.assertEqual(entry[kind]["start"], expected[0][0])
                self.assertEqual(entry[kind]["end"], expected[-1][0])
                self.assertAlmostEqual(entry[kind]["correlation"], reference_correlation([p[1] for p in expected], [p[2] for p in expected]), places=11)
                self.assertAlmostEqual(entry[kind]["partial_dollar"], reference_partial(expected), places=11)


if __name__ == "__main__":
    unittest.main()
