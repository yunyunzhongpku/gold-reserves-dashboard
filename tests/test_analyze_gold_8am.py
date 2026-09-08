import datetime as dt
import lzma
import struct
import unittest

import pandas as pd

from scripts import analyze_gold_8am as analysis


class DukascopyDataTest(unittest.TestCase):
    def test_candle_url_uses_zero_based_month(self):
        url = analysis.candle_url(dt.date(2026, 7, 22), "BID")

        self.assertEqual(
            url,
            "https://datafeed.dukascopy.com/datafeed/"
            "XAUUSD/2026/06/22/BID_candles_min_1.bi5",
        )

    def test_decode_candles_reads_open_close_low_high_layout(self):
        raw = b"".join(
            [
                struct.pack(
                    ">IIIIIf",
                    0,
                    4_080_485,
                    4_076_885,
                    4_076_365,
                    4_081_545,
                    0.04,
                ),
                struct.pack(
                    ">IIIIIf",
                    60,
                    4_076_675,
                    4_077_435,
                    4_076_325,
                    4_077_725,
                    0.03,
                ),
            ]
        )

        rows = analysis.decode_candles(
            lzma.compress(raw), dt.date(2026, 7, 22)
        )

        self.assertEqual(rows[0]["timestamp_utc"], dt.datetime(2026, 7, 22))
        self.assertAlmostEqual(rows[0]["open"], 4080.485)
        self.assertAlmostEqual(rows[0]["close"], 4076.885)
        self.assertAlmostEqual(rows[0]["low"], 4076.365)
        self.assertAlmostEqual(rows[0]["high"], 4081.545)
        self.assertAlmostEqual(rows[1]["close"], 4077.435)

    def test_decode_candles_rejects_impossible_ohlc(self):
        raw = struct.pack(
            ">IIIIIf",
            0,
            4_080_000,
            4_090_000,
            4_070_000,
            4_085_000,
            0.04,
        )

        with self.assertRaisesRegex(ValueError, "OHLC"):
            analysis.decode_candles(
                lzma.compress(raw), dt.date(2026, 7, 22)
            )


class EventReturnTest(unittest.TestCase):
    def test_event_returns_use_0759_close_as_boundary(self):
        index = pd.to_datetime(
            [
                "2026-07-21 23:29:00",
                "2026-07-21 23:59:00",
                "2026-07-22 00:00:00",
                "2026-07-22 00:04:00",
                "2026-07-22 00:14:00",
                "2026-07-22 00:29:00",
                "2026-07-22 00:59:00",
            ],
            utc=True,
        )
        prices = pd.Series(
            [99.0, 100.0, 100.1, 100.5, 101.0, 102.0, 104.0],
            index=index,
        )

        row = analysis.event_returns_for_date(
            prices, dt.date(2026, 7, 22)
        )

        self.assertAlmostEqual(row["pre_30m_bps"], 10_000 * analysis.log(100 / 99))
        self.assertAlmostEqual(row["return_1m_bps"], 10_000 * analysis.log(100.1 / 100))
        self.assertAlmostEqual(row["return_5m_bps"], 10_000 * analysis.log(100.5 / 100))
        self.assertAlmostEqual(row["return_30m_bps"], 10_000 * analysis.log(102 / 100))
        self.assertAlmostEqual(row["return_60m_bps"], 10_000 * analysis.log(104 / 100))

    def test_event_returns_require_all_prespecified_horizons(self):
        index = pd.to_datetime(
            [
                "2026-07-21 23:29:00",
                "2026-07-21 23:59:00",
                "2026-07-22 00:00:00",
            ],
            utc=True,
        )
        prices = pd.Series([99.0, 100.0, 100.1], index=index)

        self.assertIsNone(
            analysis.event_returns_for_date(prices, dt.date(2026, 7, 22))
        )


if __name__ == "__main__":
    unittest.main()
