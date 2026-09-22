import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import update_data_and_commit as updater


class CombinedRefreshPipelineTest(unittest.TestCase):
    @staticmethod
    def successful_command(command, **kwargs):
        return mock.Mock(returncode=0, stdout="")

    def test_combined_mode_refreshes_gold_then_builds_with_absolute_etf_page(self):
        commands = []

        def record(command, **kwargs):
            commands.append(command)
            return self.successful_command(command, **kwargs)

        page = Path("incoming/etf.html")
        with mock.patch.object(updater, "run_command", side_effect=record), \
             mock.patch.object(updater, "tracked_status", return_value=[]):
            updater.run_refresh_pipeline(etf_page=page, refresh_gold=True)

        self.assertEqual(
            commands[:3],
            [
                [sys.executable, "scripts/refresh_wind_data.py"],
                [sys.executable, "scripts/refresh_market_data.py"],
                [
                    sys.executable,
                    "scripts/build_site.py",
                    "--etf-page",
                    str(page.resolve()),
                ],
            ],
        )
        self.assertEqual(commands[3], updater.TEST_COMMAND)

    def test_combined_mode_allows_gold_and_etf_outputs_but_rejects_other_changes(self):
        allowed_entries = [
            (" M", "data/market/wind_daily.csv"),
            (" M", "data/etf_tracking.html"),
            (" M", "site/index.html"),
        ]
        blocked_entries = allowed_entries + [(" M", "scripts/build_site.py")]

        with mock.patch.object(
            updater, "run_command", side_effect=self.successful_command
        ), mock.patch.object(
            updater, "tracked_status", side_effect=[allowed_entries, blocked_entries]
        ):
            updater.run_refresh_pipeline(
                etf_page=Path("/tmp/incoming-etf.html"), refresh_gold=True
            )
            with self.assertRaisesRegex(SystemExit, "scripts/build_site.py"):
                updater.run_refresh_pipeline(
                    etf_page=Path("/tmp/incoming-etf.html"), refresh_gold=True
                )


class CombinedRefreshCliTest(unittest.TestCase):
    def test_refresh_gold_requires_etf_page(self):
        with mock.patch.object(sys, "argv", ["update_data_and_commit.py", "--refresh-gold"]), \
             mock.patch.object(updater, "require_clean_tracked_worktree") as clean, \
             mock.patch.object(updater, "run_refresh_pipeline") as refresh:
            with self.assertRaises(SystemExit):
                updater.main()

        clean.assert_not_called()
        refresh.assert_not_called()

    def test_combined_mode_commits_full_allowed_set_while_etf_only_stays_narrow(self):
        cases = [
            (["--etf-page", "/tmp/etf.html", "--refresh-gold", "--no-push"], True, False),
            (["--etf-page", "/tmp/etf.html", "--no-push"], False, True),
        ]

        for arguments, expected_refresh_gold, expected_etf_only in cases:
            with self.subTest(arguments=arguments), \
                 mock.patch.object(sys, "argv", ["update_data_and_commit.py", *arguments]), \
                 mock.patch.object(updater, "require_clean_tracked_worktree"), \
                 mock.patch.object(updater, "run_refresh_pipeline") as refresh, \
                 mock.patch.object(
                     updater, "commit_allowed_changes", return_value=True
                 ) as commit:
                updater.main()

            refresh.assert_called_once_with(
                etf_page=Path("/tmp/etf.html"),
                refresh_gold=expected_refresh_gold,
            )
            commit.assert_called_once_with(etf_only=expected_etf_only)


if __name__ == "__main__":
    unittest.main()
