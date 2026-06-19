"""
Static-source runner — build stored, segmented summaries of the Economic Survey
(yearly) and Yojana (monthly) for an exam, for weaving into weekly questions.

The Economic Survey is processed once a year and Yojana once a month; the output
(a section-wise summary + a list of topic-tagged fact segments) is stored under
data/static/<exam>/ and later rotated into weekly papers by daily_runner so each
fact is asked at most once (see pipeline/static_sources.py).

Source text can be scraped (best-effort) or supplied locally — Economic Survey /
Yojana are largely PDF/JS, so passing your own extracted text is the reliable path:

    # Economic Survey (yearly)
    python pipeline/static_runner.py --exam upsc-banking --economic-survey 2025 \
        --from-file data/static/upsc-banking/sources/econsurvey-2025.txt

    # Yojana (monthly)
    python pipeline/static_runner.py --exam upsc-banking --yojana 2026-06 \
        --from-file data/static/upsc-banking/sources/yojana-2026-06.txt

Without --from-file it attempts the scraper and exits gracefully if nothing usable
is reachable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import DEFAULT_EXAM, EXAMS  # noqa: E402
from pipeline.static_sources import save_source  # noqa: E402


def _load_taxonomy_topics(exam: str) -> list[str]:
    cfg = EXAMS.get(exam) or EXAMS[DEFAULT_EXAM]
    tax = json.loads((BASE_DIR / cfg["taxonomy"]).read_text())
    return tax["prompt_profile"]["summary_sections"]


def _build_prompt(exam: str, kind: str, key: str, topics: list[str], source_text: str) -> str:
    label = "Economic Survey" if kind == "economic-survey" else "Yojana"
    topics_str = ", ".join(topics)
    return f"""\
You are preparing General Awareness study material for the {EXAMS[exam]['name']} exam
from the {label} ({key}). Read the source text and produce TWO things.

1) A section-wise markdown summary under '## ' headings drawn from these GA topics:
   {topics_str}
   Each bullet must keep exact figures, names, schemes, years and definitions.

2) A JSON array of "segments". Each segment is a self-contained chunk of facts that a
   few MCQs could be built from, tagged with one of the topics above. Aim for 8–20
   segments. Each object:
     {{"title": "...", "topic": "<one of the topics>",
       "summary": "1-2 sentence gist",
       "facts": ["exact fact 1", "exact fact 2", "..."]}}

Output EXACTLY in this form, nothing else:
<<<SUMMARY>>>
## <topic>
- ...
<<<SEGMENTS>>>
[ {{...}}, {{...}} ]

SOURCE TEXT ({label} {key}):
{source_text}
"""


def _parse_output(raw: str) -> tuple[str, list[dict]]:
    summary, segments = "", []
    sm = re.search(r"<<<SUMMARY>>>(.*?)<<<SEGMENTS>>>", raw, re.S)
    if sm:
        summary = sm.group(1).strip()
    seg_part = raw.split("<<<SEGMENTS>>>", 1)[1] if "<<<SEGMENTS>>>" in raw else ""
    start, end = seg_part.find("["), seg_part.rfind("]")
    if start != -1 and end != -1:
        try:
            data = json.loads(seg_part[start : end + 1])
            if isinstance(data, list):
                segments = [s for s in data if isinstance(s, dict)]
        except json.JSONDecodeError:
            pass
    if not summary:
        summary = raw.strip()
    return summary, segments


def _get_source_text(kind: str, key: str, from_file: str | None) -> str:
    if from_file:
        path = Path(from_file)
        if not path.exists():
            sys.exit(f"--from-file not found: {from_file}")
        return path.read_text(encoding="utf-8")

    # Best-effort scrape fallback.
    if kind == "economic-survey":
        from scrapers.econsurvey_scraper import scrape_econsurvey_year

        items = scrape_econsurvey_year(int(key))
        return "\n\n".join(it.get("content", "") for it in items)
    return ""  # Yojana has no reliable scraper; require --from-file


def run(exam: str, kind: str, key: str, from_file: str | None) -> None:
    if exam not in EXAMS:
        sys.exit(f"Unknown exam '{exam}'. Known: {', '.join(EXAMS)}")

    print("=" * 60)
    print(f"Static Runner [{EXAMS[exam]['name']}] — {kind} {key}")
    print("=" * 60)

    source_text = _get_source_text(kind, key, from_file).strip()
    if len(source_text.split()) < 50:
        sys.exit(
            "Not enough source text to summarise. Economic Survey / Yojana are mostly "
            "PDF/JS — extract the text and pass it with --from-file."
        )
    print(f"  Source text: {len(source_text.split())} words")

    from pipeline.daily_runner import call_ollama_with_fallback

    topics = _load_taxonomy_topics(exam)
    prompt = _build_prompt(exam, kind, key, topics, source_text)
    print("  Generating section-wise summary + segments via Ollama ...")
    raw = call_ollama_with_fallback(prompt)
    summary_md, segments = _parse_output(raw)
    if not segments:
        print("  [WARN] No segments parsed; saving summary only (no weekly rotation material).")

    out = save_source(exam, kind, key, summary_md, segments)
    print(f"  Saved {len(segments)} segments + summary → {out.relative_to(BASE_DIR)}")
    print("  These will rotate into weekly papers automatically (no repeats).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Economic Survey / Yojana static-source runner")
    ap.add_argument("--exam", default="upsc-banking", choices=list(EXAMS.keys()))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--economic-survey", metavar="YEAR", help="e.g. 2025")
    g.add_argument("--yojana", metavar="YYYY-MM", help="e.g. 2026-06")
    ap.add_argument("--from-file", help="local text file with the source content")
    args = ap.parse_args()

    if args.economic_survey:
        run(args.exam, "economic-survey", args.economic_survey, args.from_file)
    else:
        run(args.exam, "yojana", args.yojana, args.from_file)
