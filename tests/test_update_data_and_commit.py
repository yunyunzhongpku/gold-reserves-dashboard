import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import update_data_and_commit as updater


class UpdateDataAndCommitSafetyTest(unittest.TestCase):
    def test_unexpected_tracked_paths_are_blocked(self):
        entries = updater.tracked_status_entries(
            " M data/market/wind_daily.csv\n"
            " M site/index.html\n"
            " M scripts/build_site.py\n"
        )
        self.assertEqual(updater.unexpected_paths(entries), ["scripts/build_site.py"])

    def test_allowed_paths_are_staged_explicitly(self):
        self.assertEqual(
            updater.git_add_command(),
            ["git", "add", "--", *updater.ALLOWED_UPDATE_PATHS],
        )

    def test_untracked_notes_do_not_count_as_tracked_changes(self):
        entries = updater.tracked_status_entries("?? docs/pantheon_research_dashboard_notes.md\n")
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
