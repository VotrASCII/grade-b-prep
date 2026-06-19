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


def _refresh_news() -> None:
    """Best-effort refresh of the news digest (RSS + exam tagging)."""
    news_runner = BASE_DIR / "pipeline" / "news_runner.py"
    if not news_runner.exists():
        return
    print("  Refreshing news digest ...")
    result = subprocess.run([sys.executable, str(news_runner)], cwd=str(BASE_DIR))
    if result.returncode != 0:
        print(f"  [WARN] News refresh exited with code {result.returncode}")


def _refresh_static() -> None:
    """Best-effort: download + ingest Economic Survey / Yojana PDFs for any exam
    that uses them. Idempotent — skips editions already ingested, retries daily
    until a PDF is reachable (or dropped into data/static/<exam>/pdfs/)."""
    from config import active_exams

    fetch = BASE_DIR / "pipeline" / "static_fetch.py"
    if not fetch.exists():
        return
    for slug, cfg in active_exams().items():
        if "econsurvey" not in cfg.get("sources", []):
            continue
        print(f"  Refreshing static sources (ES/Yojana) for {cfg['name']} ...")
        result = subprocess.run(
            [sys.executable, str(fetch), "--exam", slug], cwd=str(BASE_DIR)
        )
        if result.returncode != 0:
            print(f"  [WARN] Static refresh for {slug} exited with code {result.returncode}")


def _rebuild_site(publish: bool = False, refresh_news: bool = False) -> None:
    """Regenerate the static site in docs/ and optionally commit & push it."""
    if refresh_news:
        _refresh_news()
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


def _run_week(week: int) -> bool:
    """Run the weekly pipeline for ALL active exams. Returns True if at least one
    exam produced output (so the caller can decide whether to advance the week)."""
    start_date, end_date = get_week_period(week)
    print(
        f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Running Week {week} for all active exams: "
        f"{start_date:%Y-%m-%d} to {end_date:%Y-%m-%d} ..."
    )
    # daily_runner --all-exams runs each active exam independently, retries
    # transient failures, and skips an exam that still fails (exit 0 if any exam
    # succeeded, exit 1 only if every exam failed).
    result = subprocess.run(
        [
            sys.executable,
            str(BASE_DIR / "pipeline" / "daily_runner.py"),
            "--week", str(week), "--all-exams",
        ],
        cwd=str(BASE_DIR),
    )
    if result.returncode != 0:
        print(f"  [ERROR] Week {week}: every exam failed (exit {result.returncode}).")
        return False
    print(f"  Week {week} pipeline complete.")
    return True


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

    produced = _run_week(week)
    if not produced:
        print(
            f"Week {week}: no exam produced output this run (likely a transient "
            "scrape/network issue). Staying on this week; will retry next tick."
        )
        return True

    state["current_week"] = week + 1
    _save_state(state)

    # Rebuild the published site after each completed week so new content goes live.
    # News has its own daily trigger (news_job), so don't refetch it here.
    _rebuild_site(publish=publish, refresh_news=False)

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


def _catch_up_weeks(publish: bool) -> None:
    """Process every completed-but-unprocessed week (all exams), then stop once the
    current week is still in progress. Used at startup and by the weekly trigger."""
    while True:
        before = _load_state().get("current_week", 1)
        cont = run_current_week(publish=publish)
        after = _load_state().get("current_week", 1)
        if after == before:   # nothing processed (week incomplete or transient fail)
            break
        if not cont:          # finite schedule exhausted
            break


def weekly_job(publish: bool = True) -> None:
    """Monday 00:00 trigger: ingest the just-completed week (Mon–Sun) for every active
    exam, refresh yearly reference sources, rebuild, and push so Pages redeploys."""
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Weekly trigger ...")
    _refresh_static()              # Economic Survey (yearly) — idempotent, cheap if done
    _catch_up_weeks(publish=publish)


def news_job(publish: bool = True) -> None:
    """Daily 00:00 trigger: refresh the news digest (only new items are summarised —
    see news_runner dedup), rebuild, and push so the news page redeploys."""
    print(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Daily news trigger ...")
    _rebuild_site(publish=publish, refresh_news=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="RBI Grade B prep scheduler")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Skip the scheduler and immediately run the current completed week (all exams)",
    )
    parser.add_argument(
        "--news-now",
        action="store_true",
        help="Skip the scheduler and immediately refresh + publish the news digest",
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
    parser.add_argument(
        "--with-news",
        action="store_true",
        help="Also refresh the news digest (RSS + exam tagging) before building",
    )
    args = parser.parse_args()

    if args.build_only:
        _rebuild_site(publish=args.publish, refresh_news=args.with_news)
        return

    if args.news_now:
        news_job(publish=args.publish)
        return

    if args.run_now:
        # Manual: process the current completed week now (honours --publish).
        weekly_job(publish=args.publish)
        return

    # ── Auto-scheduled mode ─────────────────────────────────────────────────
    # Weekly: every Monday 00:00 → ingest the just-completed week (Mon–Sun) for all
    #         active exams → rebuild → push (Pages redeploys).
    # News:   every day   00:00 → refresh the digest (only new items summarised) →
    #         rebuild → push.
    # Auto mode always publishes — the whole point is to keep the live site current.
    publish = True

    print("Govt Exams Prep — Scheduler")
    print(f"State: {_load_state()}")
    print("Schedule: weekly = Mon 00:00 (all exams) · news = daily 00:00")

    # Catch up at startup: process any completed weeks missed while we were down,
    # and make the news page current immediately.
    _refresh_static()
    _catch_up_weeks(publish=publish)
    news_job(publish=publish)

    schedule.every().monday.at("00:00").do(weekly_job, publish=publish)
    schedule.every().day.at("00:00").do(news_job, publish=publish)

    print("Scheduler active. Press Ctrl+C to exit.")
    while True:
        schedule.run_pending()
        time_mod.sleep(30)


if __name__ == "__main__":
    main()
