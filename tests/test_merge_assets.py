import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import merge_assets
from scripts import update_data_and_commit as updater


def valid_snapshot(asof="2026-09-21"):
    etfs = []
    for position in range(16):
        group = "纳指100" if position < 12 else "标普500"
        code = f"{159000 + position:06d}.SZ"
        etfs.append({
            "code": code,
            "group": group,
            "name": f"测试ETF{position + 1}",
            "history": [{"date": asof, "close": 1.0}],
            "latest": {
                "date": asof,
                "valid": True,
                "ann_date": "2026-09-18",
                "index_date": "2026-09-18",
                "fx_date": "2026-09-18",
                "nav_estimate": 1.0,
                "close": 1.0,
                "premium": 0.0,
                "amount": 1.0,
                "volume": 1.0,
                "amount_1y": 1.0,
                "amount_20d": 1.0,
            },
        })
    return {
        "meta": {"schema_version": 1, "asof": asof},
        "indices": [
            {"group": "纳指100", "code": "NDX.GI"},
            {"group": "标普500", "code": "SPX.GI"},
        ],
        "etfs": etfs,
    }


def tracking_html(snapshot=None, marker="ETF_PAGE_MARKER"):
    payload = json.dumps(snapshot or valid_snapshot(), ensure_ascii=False)
    return (
        "<!doctype html><html><head><title>ETF跟踪</title></head>"
        f"<body><main>{marker}</main>"
        f'<script id="tracking-data" type="application/json">{payload}</script>'
        "</body></html>"
    )


GOLD_HTML = "<!doctype html><html><head><title>黄金</title></head><body><main>GOLD_PAGE_MARKER</main></body></html>"


class ReadEtfHtmlTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "etf.html"

    def tearDown(self):
        self.tempdir.cleanup()

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_accepts_valid_16_fund_snapshot_without_recalculation(self):
        source = tracking_html()
        result = merge_assets.read_etf_html(
            self.write(source), today=date(2026, 9, 22)
        )
        self.assertEqual(result, source)

    def test_rejects_missing_tracking_data_and_bad_json(self):
        cases = [
            "<html><body>missing</body></html>",
            '<script id="tracking-data" type="application/json">{bad json</script>',
        ]
        for html in cases:
            with self.subTest(html=html):
                with self.assertRaises(ValueError):
                    merge_assets.read_etf_html(
                        self.write(html), today=date(2026, 9, 22)
                    )

    def test_rejects_duplicate_etf_code(self):
        snapshot = valid_snapshot()
        snapshot["etfs"][1]["code"] = snapshot["etfs"][0]["code"]
        with self.assertRaises(ValueError):
            merge_assets.read_etf_html(
                self.write(tracking_html(snapshot)), today=date(2026, 9, 22)
            )

    def test_rejects_wrong_group_counts(self):
        snapshot = valid_snapshot()
        snapshot["etfs"][11]["group"] = "标普500"
        with self.assertRaises(ValueError):
            merge_assets.read_etf_html(
                self.write(tracking_html(snapshot)), today=date(2026, 9, 22)
            )

    def test_rejects_future_asof(self):
        with self.assertRaises(ValueError):
            merge_assets.read_etf_html(
                self.write(tracking_html(valid_snapshot("2026-09-23"))),
                today=date(2026, 9, 22),
            )

    def test_rejects_missing_required_structure(self):
        for missing in ("meta", "indices", "etfs"):
            with self.subTest(missing=missing):
                snapshot = valid_snapshot()
                del snapshot[missing]
                with self.assertRaises(ValueError):
                    merge_assets.read_etf_html(
                        self.write(tracking_html(snapshot)),
                        today=date(2026, 9, 22),
                    )


class WriteMergedSiteTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        base = Path(self.tempdir.name)
        self.cache = base / "data" / "etf_tracking.html"
        self.output = base / "site" / "index.html"
        self.source = base / "incoming.html"
        self.cache.parent.mkdir(parents=True)
        self.output.parent.mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_without_source_reads_cache_and_does_not_modify_it(self):
        cached = tracking_html(marker="CACHED_ETF_MARKER")
        self.cache.write_text(cached, encoding="utf-8")
        self.output.write_text("old output", encoding="utf-8")

        result = merge_assets.write_merged_site(GOLD_HTML, self.cache, self.output)

        self.assertEqual(self.cache.read_text(encoding="utf-8"), cached)
        self.assertEqual(self.output.read_text(encoding="utf-8"), result)
        self.assertIn("GOLD_PAGE_MARKER", result)
        self.assertIn("CACHED_ETF_MARKER", result)

    def test_source_successfully_replaces_cache_and_builds_matching_output(self):
        old_cache = tracking_html(marker="OLD_ETF_MARKER")
        new_source = tracking_html(marker="NEW_ETF_MARKER")
        self.cache.write_text(old_cache, encoding="utf-8")
        self.source.write_text(new_source, encoding="utf-8")

        result = merge_assets.write_merged_site(
            GOLD_HTML, self.cache, self.output, etf_source=self.source
        )

        self.assertEqual(self.cache.read_text(encoding="utf-8"), new_source)
        self.assertEqual(self.output.read_text(encoding="utf-8"), result)
        self.assertIn("NEW_ETF_MARKER", result)
        self.assertNotIn("OLD_ETF_MARKER", result)

    def test_replace_failure_restores_both_original_files(self):
        old_cache = tracking_html(marker="OLD_ETF_MARKER")
        old_output = "ORIGINAL_MERGED_OUTPUT"
        self.cache.write_text(old_cache, encoding="utf-8")
        self.output.write_text(old_output, encoding="utf-8")
        self.source.write_text(
            tracking_html(marker="NEW_ETF_MARKER"), encoding="utf-8"
        )
        real_replace = os.replace
        replacement_count = 0

        def fail_second_replacement(source, target):
            nonlocal replacement_count
            replacement_count += 1
            if replacement_count == 2:
                raise OSError("simulated second replace failure")
            return real_replace(source, target)

        with mock.patch.object(
            merge_assets.os, "replace", side_effect=fail_second_replacement
        ):
            with self.assertRaises(OSError):
                merge_assets.write_merged_site(
                    GOLD_HTML, self.cache, self.output, etf_source=self.source
                )

        self.assertEqual(self.cache.read_text(encoding="utf-8"), old_cache)
        self.assertEqual(self.output.read_text(encoding="utf-8"), old_output)


class RefreshPipelineModeTest(unittest.TestCase):
    def commands_for(self, etf_page=None):
        commands = []

        def record(command, **kwargs):
            commands.append(command)
            return mock.Mock(returncode=0, stdout="")

        with mock.patch.object(updater, "run_command", side_effect=record), \
             mock.patch.object(updater, "tracked_status", return_value=[]):
            updater.run_refresh_pipeline(etf_page=etf_page)
        return commands

    def test_etf_mode_only_builds_with_supplied_page_before_checks(self):
        page = Path("/tmp/incoming-etf.html")
        commands = self.commands_for(page)

        self.assertIn(
            [
                sys.executable,
                "scripts/build_site.py",
                "--etf-page",
                str(page.resolve()),
            ],
            commands,
        )
        flattened = [item for command in commands for item in command]
        self.assertNotIn("scripts/refresh_wind_data.py", flattened)
        self.assertNotIn("scripts/refresh_market_data.py", flattened)

    def test_etf_mode_rejects_tracked_gold_market_change(self):
        def succeed(command, **kwargs):
            return mock.Mock(returncode=0, stdout="")

        market_change = [(" M", "data/market/wind_daily.csv")]
        with mock.patch.object(updater, "run_command", side_effect=succeed), \
             mock.patch.object(updater, "tracked_status", return_value=market_change):
            with self.assertRaisesRegex(SystemExit, "data/market/wind_daily.csv"):
                updater.run_refresh_pipeline(etf_page=Path("/tmp/incoming-etf.html"))

    def test_default_mode_keeps_gold_refresh_commands(self):
        commands = self.commands_for()

        self.assertEqual(commands[0], [sys.executable, "scripts/refresh_wind_data.py"])
        self.assertEqual(commands[1], [sys.executable, "scripts/refresh_market_data.py"])
        self.assertEqual(commands[2], [sys.executable, "scripts/build_site.py"])


if __name__ == "__main__":
    unittest.main()
