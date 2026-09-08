import datetime as dt
import lzma
import struct
import unittest

import pandas as pd

from scripts import analyze_gold_sessions as analysis


class SessionBoundaryTest(unittest.TestCase):
    def test_summer_boundaries_follow_new_york_daylight_time(self):
        trade_date = dt.date(2026, 7, 15)

        asia_start, asia_end = analysis.session_bounds_utc(
            trade_date, analysis.SESSIONS["asia"]
        )
        europe_start, europe_end = analysis.session_bounds_utc(
            trade_date, analysis.SESSIONS["europe"]
        )
        us_start, us_end = analysis.session_bounds_utc(
            trade_date, analysis.SESSIONS["us"]
        )

        self.assertEqual(asia_start, analysis.utc_datetime(2026, 7, 14, 22))
        self.assertEqual(asia_end, analysis.utc_datetime(2026, 7, 15, 7))
        self.assertEqual(europe_start, analysis.utc_datetime(2026, 7, 15, 7))
        self.assertEqual(europe_end, analysis.utc_datetime(2026, 7, 15, 12))
        self.assertEqual(us_start, analysis.utc_datetime(2026, 7, 15, 12))
        self.assertEqual(us_end, analysis.utc_datetime(2026, 7, 15, 21))

    def test_winter_boundaries_follow_new_york_standard_time(self):
        trade_date = dt.date(2026, 1, 15)

        asia_start, asia_end = analysis.session_bounds_utc(
            trade_date, analysis.SESSIONS["asia"]
        )
        europe_start, europe_end = analysis.session_bounds_utc(
            trade_date, analysis.SESSIONS["europe"]
        )
        us_start, us_end = analysis.session_bounds_utc(
            trade_date, analysis.SESSIONS["us"]
        )

        self.assertEqual(asia_start, analysis.utc_datetime(2026, 1, 14, 23))
        self.assertEqual(asia_end, analysis.utc_datetime(2026, 1, 15, 8))
        self.assertEqual(europe_start, analysis.utc_datetime(2026, 1, 15, 8))
        self.assertEqual(europe_end, analysis.utc_datetime(2026, 1, 15, 13))
        self.assertEqual(us_start, analysis.utc_datetime(2026, 1, 15, 13))
        self.assertEqual(us_end, analysis.utc_datetime(2026, 1, 15, 22))


class SelectedCandleDecodeTest(unittest.TestCase):
    def test_decoder_keeps_only_requested_minutes(self):
        raw = b"".join(
            [
                struct.pack(
                    ">IIIIIf",
                    0,
                    2_000_000,
                    2_001_000,
                    1_999_000,
                    2_002_000,
                    1.0,
                ),
                struct.pack(
                    ">IIIIIf",
                    60,
                    2_001_000,
                    2_003_000,
                    2_000_000,
                    2_004_000,
                    2.0,
                ),
            ]
        )

        rows = analysis.decode_selected_candles(
            lzma.compress(raw),
            dt.date(2026, 7, 15),
            {60},
        )

        self.assertEqual(list(rows), [60])
        self.assertAlmostEqual(rows[60]["open"], 2001.0)
        self.assertAlmostEqual(rows[60]["close"], 2003.0)


class SessionReturnTest(unittest.TestCase):
    def test_return_uses_start_open_and_last_minute_close(self):
        trade_date = dt.date(2026, 7, 15)
        session = analysis.SESSIONS["europe"]
        start, end = analysis.session_bounds_utc(trade_date, session)
        points = {}
        for hour in range(session.duration_hours):
            timestamp = start + dt.timedelta(hours=hour)
            points[timestamp] = {
                "bid_open": 1999.0,
                "ask_open": 2001.0,
                "bid_close": 2000.0,
                "ask_close": 2002.0,
                "bid_volume": 1.0,
                "ask_volume": 1.0,
                "period_minutes": 60,
            }
        points[end - dt.timedelta(hours=1)].update(
            {
                "bid_open": 2017.0,
                "ask_open": 2019.0,
                "bid_close": 2018.0,
                "ask_close": 2020.0,
            }
        )

        row = analysis.session_return(trade_date, session, points)

        self.assertAlmostEqual(row["start_mid"], 2000.0)
        self.assertAlmostEqual(row["end_mid"], 2019.0)
        self.assertAlmostEqual(row["gross_return"], 2019.0 / 2000.0 - 1)
        self.assertAlmostEqual(row["net_return"], 2018.0 / 2001.0 - 1)
        self.assertGreater(row["gross_return"], row["net_return"])

    def test_missing_exact_boundary_excludes_session(self):
        trade_date = dt.date(2026, 7, 15)
        session = analysis.SESSIONS["us"]
        start, _ = analysis.session_bounds_utc(trade_date, session)

        self.assertIsNone(analysis.session_return(trade_date, session, {start: {}}))


class CurveTest(unittest.TestCase):
    def test_curves_use_only_dates_complete_for_all_sessions(self):
        rows = pd.DataFrame(
            [
                {
                    "trade_date": "2026-07-14",
                    "session": "asia",
                    "gross_return": 0.10,
                    "net_return": 0.09,
                },
                {
                    "trade_date": "2026-07-14",
                    "session": "europe",
                    "gross_return": 0.00,
                    "net_return": -0.01,
                },
                {
                    "trade_date": "2026-07-14",
                    "session": "us",
                    "gross_return": -0.02,
                    "net_return": -0.03,
                },
                {
                    "trade_date": "2026-07-15",
                    "session": "asia",
                    "gross_return": -0.10,
                    "net_return": -0.11,
                },
                {
                    "trade_date": "2026-07-15",
                    "session": "europe",
                    "gross_return": 0.01,
                    "net_return": 0.00,
                },
                {
                    "trade_date": "2026-07-16",
                    "session": "asia",
                    "gross_return": 0.50,
                    "net_return": 0.49,
                },
                {
                    "trade_date": "2026-07-16",
                    "session": "europe",
                    "gross_return": 0.50,
                    "net_return": 0.49,
                },
                {
                    "trade_date": "2026-07-16",
                    "session": "us",
                    "gross_return": 0.50,
                    "net_return": 0.49,
                },
            ]
        )

        complete, curve = analysis.build_curves(rows)

        self.assertEqual(
            complete["trade_date"].dt.strftime("%Y-%m-%d").unique().tolist(),
            ["2026-07-14", "2026-07-16"],
        )
        asia = curve.loc[curve["session"] == "asia"].reset_index(drop=True)
        self.assertAlmostEqual(asia.loc[0, "gross_nav"], 1.10)
        self.assertAlmostEqual(asia.loc[1, "gross_nav"], 1.65)


if __name__ == "__main__":
    unittest.main()
