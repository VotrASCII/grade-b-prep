"""
Static study sources (Economic Survey — yearly; Yojana — monthly) and the
rotation ledger that weaves them into weekly question generation without repeats.

The Economic Survey is generated once a year and Yojana once a month, but
questions are generated weekly. To fold the static material into weekly papers
*without repeating facts and while respecting the per-exam GA weightage*, each
stored source is split into **segments** (a coherent chunk of key facts, tagged
with a GA topic). A rotation ledger then hands each segment to exactly ONE week —
so a fact can never be asked twice — and the weekly prompt is told to build a
small fixed quota of questions from that week's segments, folded into the normal
topic distribution (they replace, not add to, current-affairs questions).

This module is pure storage/selection logic (no Ollama). `static_runner.py`
produces the segments; `daily_runner.py` consumes them per week.

Segment schema (one dict):
    {"id", "kind", "source", "topic", "title", "summary", "facts": [...]}
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_ROOT = BASE_DIR / "data" / "static"

# Default number of static-source questions to fold into each weekly paper.
DEFAULT_WEEKLY_STATIC_QUOTA = 6


def static_dir(exam_slug: str) -> Path:
    return STATIC_ROOT / exam_slug


def _segment_file(exam_slug: str, kind: str, key: str) -> Path:
    return static_dir(exam_slug) / f"{kind}-{key}.json"


def save_source(
    exam_slug: str,
    kind: str,
    key: str,
    summary_md: str,
    segments: list[dict],
) -> Path:
    """Persist one static source (its section-wise summary + segments).

    kind: "economic-survey" | "yojana"; key: e.g. "2025" or "2026-06".
    """
    d = static_dir(exam_slug)
    d.mkdir(parents=True, exist_ok=True)

    # Stamp stable ids/metadata onto each segment.
    norm: list[dict] = []
    for i, seg in enumerate(segments, 1):
        norm.append(
            {
                "id": f"{kind}-{key}-{i:03d}",
                "kind": kind,
                "source": f"{kind} {key}",
                "topic": seg.get("topic", ""),
                "title": seg.get("title", f"{kind} {key} #{i}"),
                "summary": seg.get("summary", ""),
                "facts": seg.get("facts", []) or [],
            }
        )

    (d / f"{kind}-{key}.json").write_text(
        json.dumps(
            {"kind": kind, "key": key, "segments": norm, "summary_md_chars": len(summary_md)},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Keep the human-readable section-wise summary alongside for storage/display.
    (d / f"{kind}-{key}.md").write_text(summary_md, encoding="utf-8")
    return d / f"{kind}-{key}.json"


def load_all_segments(exam_slug: str) -> list[dict]:
    d = static_dir(exam_slug)
    if not d.exists():
        return []
    segments: list[dict] = []
    for path in sorted(d.glob("*.json")):
        if path.name == "rotation.json":
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            continue
        segments.extend(data.get("segments", []))
    return segments


# ── rotation ledger ────────────────────────────────────────────────────────

def _rotation_path(exam_slug: str) -> Path:
    return static_dir(exam_slug) / "rotation.json"


def _load_rotation(exam_slug: str) -> dict:
    p = _rotation_path(exam_slug)
    if not p.exists():
        return {"consumed": {}}
    try:
        data = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {"consumed": {}}
    data.setdefault("consumed", {})
    return data


def _save_rotation(exam_slug: str, data: dict) -> None:
    static_dir(exam_slug).mkdir(parents=True, exist_ok=True)
    _rotation_path(exam_slug).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _segment_sort_key(seg: dict, month_key: str) -> tuple:
    """Prefer the current month's Yojana, then everything else by id (stable)."""
    is_current_yojana = seg["kind"] == "yojana" and seg.get("id", "").startswith(f"yojana-{month_key}")
    return (0 if is_current_yojana else 1, seg.get("id", ""))


def select_for_week(
    exam_slug: str,
    week_key: str,
    week_start: date,
    quota: int = DEFAULT_WEEKLY_STATIC_QUOTA,
) -> list[dict]:
    """Return up to ``quota`` unconsumed segments for this week and mark them used.

    Idempotent: re-running the same week returns the same segments (so a
    re-generated week does not silently shift the rotation).
    """
    if quota <= 0:
        return []
    all_segs = load_all_segments(exam_slug)
    if not all_segs:
        return []

    rot = _load_rotation(exam_slug)
    consumed: dict = rot["consumed"]

    # Already assigned to this week? Return those (idempotent).
    mine = [s for s in all_segs if consumed.get(s["id"]) == week_key]
    if mine:
        return mine

    month_key = f"{week_start:%Y-%m}"
    available = [s for s in all_segs if s["id"] not in consumed]
    available.sort(key=lambda s: _segment_sort_key(s, month_key))
    chosen = available[:quota]

    for s in chosen:
        consumed[s["id"]] = week_key
    if chosen:
        _save_rotation(exam_slug, rot)
    return chosen


def format_block(segments: list[dict]) -> str:
    """Render selected segments into the prompt's STATIC SOURCE FOCUS block."""
    parts: list[str] = []
    for seg in segments:
        topic = f" · {seg['topic']}" if seg.get("topic") else ""
        parts.append(f"[{seg['source'].upper()}{topic}] {seg.get('title', '')}")
        if seg.get("summary"):
            parts.append(f"  {seg['summary']}")
        for fact in seg.get("facts", []):
            parts.append(f"  - {fact}")
        parts.append("")
    return "\n".join(parts).strip()
