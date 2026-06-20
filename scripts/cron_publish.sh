#!/bin/bash
#
# Cron wrapper for the Govt Exams Prep publish pipeline.
#
# cron runs with a minimal environment (no conda activation, sparse PATH, no
# SSH agent), so this script makes the run reproducible:
#   - pins the conda python that has the project's deps
#   - puts ollama / git / ssh on PATH
#   - cd's into the repo so all the BASE_DIR-relative paths resolve
#   - serialises runs with a lock so a slow weekly run can't overlap a daily one
#   - appends timestamped output to data/logs/ (gitignored)
#
# Usage:
#   scripts/cron_publish.sh news     # fetch -> summarise new items -> rebuild -> push
#   scripts/cron_publish.sh weekly   # ingest completed week(s) for all exams -> push
#
set -euo pipefail

MODE="${1:-news}"

REPO="/Users/votrascii/Govt Exams/govt_exam_prep"
PYTHON="/Users/votrascii/anaconda3/envs/agenticai/bin/python"

# ollama lives in /usr/local/bin; git/ssh in /usr/bin. Keep the default PATH too.
export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

cd "$REPO"

LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron-${MODE}.log"

# Prevent a long weekly run from overlapping the daily news run (and vice versa).
# macOS has no flock, so use an atomic mkdir lock with a stale-lock guard.
LOCK="$REPO/data/logs/.cron.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  # Reclaim a lock older than 2h (a crashed run that never cleaned up).
  if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin -120 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { echo "[$(date '+%Y-%m-%d %H:%M:%S')] lock busy; skipping ${MODE}" >>"$LOG"; exit 0; }
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] another cron_publish run holds the lock; skipping ${MODE}" >>"$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === cron_publish ${MODE} start ===" >>"$LOG"

case "$MODE" in
  news)
    "$PYTHON" run.py --news-now --publish >>"$LOG" 2>&1
    ;;
  weekly)
    "$PYTHON" run.py --run-now --publish >>"$LOG" 2>&1
    ;;
  *)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] unknown mode '${MODE}' (use: news | weekly)" >>"$LOG"
    exit 2
    ;;
esac

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === cron_publish ${MODE} done (exit $?) ===" >>"$LOG"
