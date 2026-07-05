from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ALLOWED_UPDATE_PATHS = [
    "data/market/wind_daily.csv",
    "data/market/fred_t10yie.csv",
    "data/market/fred_dfii10.csv",
    "data/market/cftc_gold_cot.csv",
    "site/index.html",
]

TEST_COMMAND = [
    sys.executable,
    "-m",
    "unittest",
    "tests.test_build_site",
    "tests.test_refresh_wind_data",
    "tests.test_update_data_and_commit",
    "-v",
]


def run_command(command, *, capture_output=False, env=None, check=True):
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture_output,
        env=env,
    )


def tracked_status_entries(status_text):
    entries = []
    for line in status_text.splitlines():
        if not line or line.startswith("?? "):
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((line[:2], path))
    return entries


def unexpected_paths(entries, allowed_paths=None):
    allowed = set(allowed_paths or ALLOWED_UPDATE_PATHS)
    return sorted({path for _, path in entries if path not in allowed})


def git_add_command():
    return ["git", "add", "--", *ALLOWED_UPDATE_PATHS]


def tracked_status(paths=None):
    command = ["git", "status", "--porcelain", "--untracked-files=no"]
    if paths:
        command.extend(["--", *paths])
    result = run_command(command, capture_output=True)
    return tracked_status_entries(result.stdout)


def require_clean_tracked_worktree():
    entries = tracked_status()
    if entries:
        details = "\n".join(f"{status} {path}" for status, path in entries)
        raise SystemExit(f"Tracked worktree is dirty before refresh; aborting.\n{details}")


def run_refresh_pipeline():
    run_command([sys.executable, "scripts/refresh_wind_data.py"])
    run_command([sys.executable, "scripts/refresh_market_data.py"])
    run_command([sys.executable, "scripts/build_site.py"])

    env = os.environ.copy()
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/gold-dashboard-pycache")
    run_command(TEST_COMMAND, env=env)
    run_command(["git", "diff", "--check", "--", *ALLOWED_UPDATE_PATHS])

    entries = tracked_status()
    blocked = unexpected_paths(entries)
    if blocked:
        raise SystemExit("Unexpected tracked paths changed; aborting.\n" + "\n".join(blocked))


def commit_allowed_changes():
    if not tracked_status(ALLOWED_UPDATE_PATHS):
        print("No data changes to commit.")
        return False

    run_command(git_add_command())
    staged = run_command(["git", "diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        print("No staged data changes to commit.")
        return False
    if staged.returncode not in (0, 1):
        raise SystemExit(f"git diff --cached --quiet failed with exit code {staged.returncode}")

    run_command([
        "git",
        "commit",
        "-m",
        "data: refresh gold dashboard market data",
        "-m",
        "Co-Authored-By: Codex <noreply@openai.com>",
    ])
    return True


def main():
    parser = argparse.ArgumentParser(description="Refresh dashboard data locally and optionally commit generated outputs.")
    parser.add_argument("--no-commit", action="store_true", help="Run refresh, build, and checks without creating a commit.")
    args = parser.parse_args()

    require_clean_tracked_worktree()
    run_refresh_pipeline()
    if args.no_commit:
        print("Refresh completed; commit skipped by --no-commit.")
        return
    commit_allowed_changes()


if __name__ == "__main__":
    main()
