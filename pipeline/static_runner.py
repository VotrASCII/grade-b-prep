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
import hashlib
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import (  # noqa: E402
    CHUNK_CONTENT_WORDS,
    CHUNK_SUMMARY_WORDS,
    DEFAULT_EXAM,
    ECON_SURVEY_SECTIONS,
    EXAMS,
)
from pipeline.static_sources import save_quiz, save_source, static_dir  # noqa: E402

# How many dedicated MCQs to curate from a static source by default.
DEFAULT_STATIC_QUESTIONS = 30


def _load_profile(exam: str) -> dict:
    cfg = EXAMS.get(exam) or EXAMS[DEFAULT_EXAM]
    tax = json.loads((BASE_DIR / cfg["taxonomy"]).read_text())
    return tax["prompt_profile"]


def _load_taxonomy_topics(exam: str) -> list[str]:
    return _load_profile(exam)["summary_sections"]


# ── map-reduce condensation for long sources (e.g. 700+ page Economic Survey) ──

def _chunk_words(text: str, max_words: int) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def _condense_chunk_prompt(
    exam: str, kind: str, key: str, chunk: str, index: int, total: int, topics: list[str]
) -> str:
    label = "Economic Survey" if kind == "economic-survey" else "Yojana"
    headings = "\n".join(f"## {t}" for t in topics)
    return f"""\
You are preparing General Awareness notes for the {EXAMS[exam]['name']} exam from the
{label} ({key}). This is chunk {index} of {total} of the source document.

Extract ONLY exam-relevant facts from the raw text below. Output concise markdown
notes, maximum {CHUNK_SUMMARY_WORDS} words, grouped under these headings where they
apply (drop headings with nothing to say):
{headings}

Preserve exact figures, percentages, scheme names, targets, years, rankings,
institutions and definitions. Do NOT invent facts not present in the text. Do NOT
write MCQs.

RAW CHUNK {index}/{total}:
{chunk}
"""


def _condense_large_source(exam: str, kind: str, key: str, source_text: str, topics: list[str]) -> str:
    """Map-reduce a long source into combined condensed notes (cached per chunk).

    The full Economic Survey is far too large for one prompt, so each chunk is
    summarised into exam-focused notes and the notes are concatenated. The caller
    then feeds the combined notes to the section-wise summary prompt.
    """
    from pipeline.daily_runner import call_ollama_with_fallback, clean_summary_markdown

    cache_dir = static_dir(exam) / "chunk_notes" / f"{kind}-{key}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    chunks = _chunk_words(source_text, CHUNK_CONTENT_WORDS)
    print(f"  Source is large — condensing {len(chunks)} chunk(s) "
          f"(~{CHUNK_CONTENT_WORDS} words each) before summarising ...")

    notes: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        chash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        cpath = cache_dir / f"chunk-{index:03d}.json"
        if cpath.exists():
            try:
                cached = json.loads(cpath.read_text(encoding="utf-8"))
                if cached.get("hash") == chash and cached.get("notes"):
                    print(f"    [{index}/{len(chunks)}] cached")
                    notes.append(cached["notes"])
                    continue
            except Exception:  # noqa: BLE001
                pass
        print(f"    [{index}/{len(chunks)}] condensing ({len(chunk.split())} words) ...")
        prompt = _condense_chunk_prompt(exam, kind, key, chunk, index, len(chunks), topics)
        note = clean_summary_markdown(call_ollama_with_fallback(prompt))
        cpath.write_text(json.dumps({"hash": chash, "notes": note}, ensure_ascii=False), encoding="utf-8")
        notes.append(note)

    return "\n\n".join(f"=== Notes: chunk {i} ===\n{n}" for i, n in enumerate(notes, 1))


def _build_prompt(exam: str, kind: str, key: str, topics: list[str], source_text: str) -> str:
    label = "Economic Survey" if kind == "economic-survey" else "Yojana"
    topics_str = ", ".join(topics)
    return f"""\
You are preparing General Awareness study material for the {EXAMS[exam]['name']} exam
from the {label} ({key}). The source below already condenses the FULL document, so be
comprehensive — cover every theme present, not just the first few. Produce TWO things.

1) A thorough, section-wise markdown summary under '## ' headings drawn from these GA
   topics: {topics_str}
   Use as many bullets per section as the material supports (this is a whole {label},
   so aim for depth and breadth — do not over-compress). Every bullet must keep exact
   figures, percentages, names, schemes, targets, years and definitions.

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


def _quiz_prompt(exam: str, kind: str, key: str, summary_md: str, n: int) -> str:
    profile = _load_profile(exam)
    label = "Economic Survey" if kind == "economic-survey" else "Yojana"
    opts = int(profile.get("options_count", 4))
    letters = "ABCDE"[:opts]
    last = letters[-1]
    topics = ", ".join(profile["summary_sections"])
    return f"""\
You are an expert question-setter for the {EXAMS[exam]['name']} exam. From the
{label} ({key}) study notes below, write {n} original General-Awareness MCQs.

Rules:
- Spread the questions across these topic areas as the material allows: {topics}.
- Test exact facts: figures, scheme names, targets, years, definitions, rankings.
- Each question has EXACTLY {opts} options ({letters[0]}–{last}); exactly one correct.
- No duplicates, no "all/none of the above", no opinion questions.
- Base every question only on facts present in the notes below.

Output ONLY the questions in EXACTLY this format, nothing else:
Q1. <question text>
{chr(10).join(f"{l}. <option>" for l in letters)}
Answer: <{letters[0]}-{last}>

Q2. ...

{label} {key} — STUDY NOTES:
{summary_md}
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


def _section_summary_prompt(exam: str, key: str, section: str, notes: str) -> str:
    return f"""\
You are preparing {EXAMS[exam]['name']} study material from the Economic Survey ({key}).
Write a thorough, standalone summary of ONLY the **{section}** part of the Survey.

- Use only facts in the notes below that relate to {section}.
- Be comprehensive and specific: keep exact figures, percentages, scheme names,
  targets, years, rankings, institutions, committees and definitions.
- Markdown bullet points (use short sub-bullets where useful). This is ONE chapter of
  a full Economic Survey — aim for depth: many points, not a handful.
- Do NOT write MCQs. Do NOT repeat the section title as a heading (it is added for you).
- If the notes hold little on this section, summarise what is present (don't invent).

NOTES (condensed from the full Economic Survey {key}):
{notes}
"""


def _section_wise_summary(exam: str, key: str, notes: str, call_ollama) -> list[tuple[str, str]]:
    """Summarise the Economic Survey one section at a time (independent passes).
    Returns [(section_title, body_markdown), ...] — each section gets its own depth
    instead of collapsing 700+ pages into a single over-compressed summary."""
    from pipeline.daily_runner import clean_summary_markdown

    pairs: list[tuple[str, str]] = []
    total = len(ECON_SURVEY_SECTIONS)
    for i, section in enumerate(ECON_SURVEY_SECTIONS, 1):
        print(f"    [summary {i}/{total}] {section} ...")
        body = clean_summary_markdown(call_ollama(_section_summary_prompt(exam, key, section, notes))).strip()
        if body:
            pairs.append((section, body))
    return pairs


def _section_quiz_prompt(exam: str, key: str, section: str, body: str, n: int) -> str:
    profile = _load_profile(exam)
    opts = int(profile.get("options_count", 4))
    letters = "ABCDE"[:opts]
    last = letters[-1]
    return f"""\
You are an expert question-setter for the {EXAMS[exam]['name']} exam. From the
Economic Survey ({key}) — section **{section}** — write {n} original GA MCQs.

Rules:
- Base every question ONLY on facts in the section summary below.
- Test exact facts: figures, percentages, scheme names, targets, years, rankings,
  committees, definitions. Spread across the whole section; no duplicates.
- Each question has EXACTLY {opts} options ({letters[0]}–{last}); exactly one correct.
- No "all/none of the above", no opinion questions.

Output ONLY the questions in EXACTLY this format, nothing else:
Q1. <question text>
{chr(10).join(f"{l}. <option>" for l in letters)}
Answer: <{letters[0]}-{last}>

Q2. ...

SECTION SUMMARY — {section} (Economic Survey {key}):
{body}
"""


def run(
    exam: str,
    kind: str,
    key: str,
    from_file: str | None,
    n_questions: int = DEFAULT_STATIC_QUESTIONS,
) -> None:
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

    from pipeline.daily_runner import call_ollama_with_fallback, parse_questions

    topics = _load_taxonomy_topics(exam)

    # Long sources (the Economic Survey runs 700+ pages) are map-reduced into
    # condensed notes first, so the whole document is covered rather than truncated.
    if len(source_text.split()) > int(CHUNK_CONTENT_WORDS * 1.3):
        summary_basis = _condense_large_source(exam, kind, key, source_text, topics)
        print(f"  Condensed notes: {len(summary_basis.split())} words "
              f"(from {len(source_text.split())} words of source).")
    else:
        summary_basis = source_text

    if kind == "economic-survey":
        # Independent per-section summaries (depth per chapter, not consolidated),
        # then a dedicated quiz of n_questions MCQs PER SECTION.
        print(f"  [1/2] Summarising {len(ECON_SURVEY_SECTIONS)} sections independently ...")
        pairs = _section_wise_summary(exam, key, summary_basis, call_ollama_with_fallback)
        summary_md = "\n\n".join(f"## {sec}\n{body}" for sec, body in pairs)
        save_source(exam, kind, key, summary_md, [])
        print(f"  Saved section-wise summary ({len(summary_md.split())} words, {len(pairs)} sections).")

        print(f"  [2/2] Curating {n_questions} MCQs PER SECTION "
              f"(~{n_questions * len(pairs)} total) ...")
        questions: list[dict] = []
        for i, (section, body) in enumerate(pairs, 1):
            print(f"    [quiz {i}/{len(pairs)}] {section} ...")
            qs = parse_questions(
                call_ollama_with_fallback(_section_quiz_prompt(exam, key, section, body, n_questions))
            )
            for q in qs:
                q["section"] = section  # tag for grouping/clarity on the site
            questions.extend(qs)
    else:
        prompt = _build_prompt(exam, kind, key, topics, summary_basis)
        print("  [1/2] Generating section-wise summary + segments via Ollama ...")
        summary_md, segments = _parse_output(call_ollama_with_fallback(prompt))
        save_source(exam, kind, key, summary_md, segments)
        print(f"  Saved summary ({len(summary_md.split())} words, {len(segments)} segments).")
        print(f"  [2/2] Curating {n_questions} dedicated MCQs from the summary ...")
        quiz_basis = summary_md if len(summary_md.split()) > 120 else source_text
        questions = parse_questions(_quiz_prompt(exam, kind, key, quiz_basis, n_questions))

    if questions:
        qpath = save_quiz(exam, kind, key, questions)
        print(f"  Saved {len(questions)} dedicated MCQs → {qpath.relative_to(BASE_DIR)}")
    else:
        print("  [WARN] No MCQs parsed; skipping quiz save.")
    print("  This source now has its own section + quiz (not folded into weekly papers).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Economic Survey / Yojana static-source runner")
    ap.add_argument("--exam", default="upsc-banking", choices=list(EXAMS.keys()))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--economic-survey", metavar="YEAR", help="e.g. 2025")
    g.add_argument("--yojana", metavar="YYYY-MM", help="e.g. 2026-06")
    ap.add_argument("--from-file", help="local text file with the source content")
    ap.add_argument(
        "--questions", type=int, default=DEFAULT_STATIC_QUESTIONS,
        help=f"number of dedicated MCQs to curate (default {DEFAULT_STATIC_QUESTIONS})",
    )
    args = ap.parse_args()

    if args.economic_survey:
        run(args.exam, "economic-survey", args.economic_survey, args.from_file, args.questions)
    else:
        run(args.exam, "yojana", args.yojana, args.from_file, args.questions)
