"""
Module 5 — Scheduler
Manages state and schedules weekly pipeline runs every 6 hours.
Usage:
  python run.py           # auto-scheduled mode
  python run.py --run-now # immediately process the current weekly slot
"""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import schedule
import time as time_mod

from config import SCHEDULER_INTERVAL_HOURS, WEEK_RANGE_END
from pipeline.daily_runner import get_week_period, total_configured_weeks

OPEN_ENDED_SCHEDULE = not WEEK_RANGE_END.strip()

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "data" / "state.json"

# Ensure data dir exists
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"current_week": 1}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _rebuild_site(publish: bool = False) -> None:
    """Regenerate the static site in docs/ and optionally commit & push it."""
    build_script = BASE_DIR / "scripts" / "build_site.py"
    if not build_script.exists():
        return
    print("  Rebuilding static site ...")
    result = subprocess.run([sys.executable, str(build_script)], cwd=str(BASE_DIR))
    if result.returncode != 0:
        print(f"  [WARN] Site build exited with code {result.returncode}")
        return
    if publish:
        _publish_site()


def _publish_site() -> None:
    """Commit and push the regenerated docs/ folder to the default branch."""
    docs_dir = BASE_DIR / "docs"
    subprocess.run(["git", "add", str(docs_dir)], cwd=str(BASE_DIR))
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", str(docs_dir)],
        cwd=str(BASE_DIR),
    )
    if diff.returncode == 0:
        print("  No site changes to publish.")
        return
    stamp = datetime.now().strftime("%Y-%m-%d")
    subprocess.run(
        ["git", "commit", "-m", f"site: publish weekly update ({stamp})"],
        cwd=str(BASE_DIR),
    )
    push = subprocess.run(["git", "push"], cwd=str(BASE_DIR))
    if push.returncode == 0:
        print("  Site published to remote.")
    else:
        print(f"  [WARN] git push exited with code {push.returncode}")


def _run_week(week: int) -> None:
    start_date, end_date = get_week_period(week)
    print(
        f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Running Week {week}: {start_date:%Y-%m-%d} to {end_date:%Y-%m-%d} ..."
    )
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "pipeline" / "daily_runner.py"), "--week", str(week)],
        cwd=str(BASE_DIR),
    )
    if result.returncode != 0:
        print(f"  [ERROR] daily_runner.py exited with code {result.returncode}")
    else:
        print(f"  Week {week} complete.")


def run_current_week(publish: bool = False) -> bool:
    """Run the current weekly period, then increment state if it completed."""
    state = _load_state()
    week = state.get("current_week", 1)
    total_weeks = total_configured_weeks()

    if week > total_weeks:
        if OPEN_ENDED_SCHEDULE:
            print(
                f"Week {week} has not completed yet. "
                "Waiting for the next weekly period to elapse."
            )
            return True
        print("All configured weekly periods processed.")
        return False

    start_date, end_date = get_week_period(week)
    if end_date > date.today():
        print(
            f"Week {week} ({start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}) "
            "is not complete yet. Waiting for the next scheduler tick."
        )
        return True

    _run_week(week)

    state["current_week"] = week + 1
    _save_state(state)

    # Rebuild the published site after each completed week so new content goes live.
    _rebuild_site(publish=publish)

    if state["current_week"] > total_weeks:
        if OPEN_ENDED_SCHEDULE:
            print("\nUp to date. Waiting for the next weekly period to elapse.")
            return True
        print("\nAll configured weekly periods processed.")
        return False

    next_start, next_end = get_week_period(state["current_week"])
    print(
        f"  Next scheduled run: Week {state['current_week']} "
        f"({next_start:%Y-%m-%d} to {next_end:%Y-%m-%d})"
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="RBI Grade B prep scheduler")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Skip the scheduler and immediately run the current weekly period",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="After each completed week, commit and push the rebuilt docs/ site",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Just rebuild the static site from existing data and exit",
    )
    args = parser.parse_args()

    if args.build_only:
        _rebuild_site(publish=args.publish)
        return

    if args.run_now:
        run_current_week(publish=args.publish)
        return

    # Auto-scheduled mode: run immediately once, then schedule every N hours.
    state = _load_state()
    if (
        not OPEN_ENDED_SCHEDULE
        and state.get("current_week", 1) > total_configured_weeks()
    ):
        print("All configured weekly periods processed.")
        return

    print("RBI Grade B Prep - Weekly Auto Scheduler")
    print(f"State: {state}")
    print(
        "Running the current weekly period immediately, then scheduling "
        f"every {SCHEDULER_INTERVAL_HOURS} hours..."
    )

    more = run_current_week(publish=args.publish)
    if not more:
        return

    def scheduled_job() -> None:
        still_more = run_current_week(publish=args.publish)
        if not still_more:
            return schedule.CancelJob

    schedule.every(SCHEDULER_INTERVAL_HOURS).hours.do(scheduled_job)

    print("Scheduler active. Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time_mod.sleep(30)


if __name__ == "__main__":
    main()
