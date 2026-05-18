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

from config import SCHEDULER_INTERVAL_HOURS
from pipeline.daily_runner import get_week_period, total_configured_weeks

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


def run_current_week() -> bool:
    """Run the current weekly period, then increment state if it completed."""
    state = _load_state()
    week = state.get("current_week", 1)
    total_weeks = total_configured_weeks()

    if week > total_weeks:
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

    if state["current_week"] > total_weeks:
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
    args = parser.parse_args()

    if args.run_now:
        run_current_week()
        return

    # Auto-scheduled mode: run immediately once, then schedule every N hours.
    state = _load_state()
    if state.get("current_week", 1) > total_configured_weeks():
        print("All configured weekly periods processed.")
        return

    print("RBI Grade B Prep - Weekly Auto Scheduler")
    print(f"State: {state}")
    print(
        "Running the current weekly period immediately, then scheduling "
        f"every {SCHEDULER_INTERVAL_HOURS} hours..."
    )

    more = run_current_week()
    if not more:
        return

    def scheduled_job() -> None:
        still_more = run_current_week()
        if not still_more:
            return schedule.CancelJob

    schedule.every(SCHEDULER_INTERVAL_HOURS).hours.do(scheduled_job)

    print("Scheduler active. Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time_mod.sleep(30)


if __name__ == "__main__":
    main()
