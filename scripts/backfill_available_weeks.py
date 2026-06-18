"""
Backfill weekly GA summaries and MCQs from whatever usable cached content exists.

This script is intentionally separate from pipeline/daily_runner.py. The main
runner is strict about per-source detail coverage so it can catch scraper
failures. This backfill runner is for weeks where one source, commonly RBI,
does not meet that threshold but still has some usable detail content.

Usage:
  python scripts/backfill_available_weeks.py --all-missing
  python scripts/backfill_available_weeks.py --week 12 --week 16
  python scripts/backfill_available_weeks.py --all-missing --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from pipeline.daily_runner import (  # noqa: E402
    GEN_Q_DIR,
    SUMMARIES_DIR,
    _detail_content_status,
    _period_key,
    _save_outputs,
    _save_raw_ollama_response,
    _usable_detail_items,
    call_ollama_with_fallback,
    clean_summary_markdown,
    get_week_period,
    parse_questions,
    prepare_prompt_content,
    split_response,
    total_configured_weeks,
)


MAX_BACKFILL_QUESTIONS = 80
MIN_BACKFILL_QUESTIONS = 8


def _summary_path(output_key: str) -> Path:
    return SUMMARIES_DIR / f"{output_key}.md"


def _questions_path(output_key: str) -> Path:
    return GEN_Q_DIR / f"{output_key}-qs.json"


def _outputs_exist(output_key: str) -> bool:
    return _summary_path(output_key).exists() and _questions_path(output_key).exists()


def _completed_week_numbers(include_incomplete: bool) -> list[int]:
    today = date.today()
    weeks = []
    for week in range(1, total_configured_weeks() + 1):
        _start_date, end_date = get_week_period(week)
        if include_incomplete or end_date <= today:
            weeks.append(week)
    return weeks


def _missing_week_numbers(include_incomplete: bool) -> list[int]:
    missing = []
    for week in _completed_week_numbers(include_incomplete):
        start_date, end_date = get_week_period(week)
        output_key = _period_key(start_date, end_date)
        if not _outputs_exist(output_key):
            missing.append(week)
    return missing


def _load_cached_week_items(output_key: str, source: str) -> list[dict]:
    path = BASE_DIR / "data" / "scraped" / output_key / f"{source}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError(f"Expected a JSON list in {path}")
    return data


def _auto_question_count(usable_count: int, word_count: int) -> int:
    if usable_count <= 0 or word_count <= 0:
        return 0

    by_items = usable_count * 2
    by_words = max(MIN_BACKFILL_QUESTIONS, word_count // 250)
    return max(MIN_BACKFILL_QUESTIONS, min(MAX_BACKFILL_QUESTIONS, by_items, by_words))


def _build_available_prompt(
    content: str,
    period_name: str,
    target_questions: int,
    source_note: str,
) -> str:
    return f"""\
You are an expert RBI Grade B Phase 1 General Awareness exam coach.

Create exam material for {period_name} using only the raw source content below.
Some source items for this week were missing usable detail content, so the
material is intentionally based only on available verified details.

Available-source note:
{source_note}

Do not invent facts. Do not pad the output to force coverage.

PART 1 - GA SUMMARY
Write a structured markdown summary of the most exam-relevant facts.
Use these headings where applicable:
## RBI & Monetary Policy
## Banking & Financial Sector
## Government Schemes & Budget
## Economy & Trade
## International Affairs & Organizations
## Awards, Rankings & Appointments
## Sports / Environment / Science & Tech

Rules for the summary:
- Include specific dates, names, numbers, reports, schemes, rates, penalties,
  and institutions wherever the source provides them.
- Omit sections that have no support in the available content.
- If RBI source material is thin, keep RBI sections concise instead of filling
  them with assumptions.

PART 2 - PRACTICE MCQs
Generate up to {target_questions} RBI Grade B Phase 1 style MCQs.
If the available content does not support {target_questions} distinct questions,
generate fewer questions rather than repeating or inventing facts.

Question rules:
- Use exactly 5 options for every question: A, B, C, D, E.
- Every question must include exactly one answer line immediately after options.
- Format every answer line exactly as: Answer: [letter]
- Wrong options must be plausible entities from the same category.
- Start current-affairs stems with "Recently," where natural.
- Do not use markdown bold/italic formatting in PART 2.

Output format:
Q1. [Question text]
A. [option]  B. [option]  C. [option]  D. [option]  E. [option]
Answer: [letter]

RAW CONTENT - {period_name.upper()}
{content}
"""


def _build_more_questions_prompt(
    content: str,
    summary: str,
    period_name: str,
    start_number: int,
    end_number: int,
) -> str:
    return f"""\
You generated fewer practice MCQs than requested for {period_name}.

Generate additional MCQs numbered Q{start_number} through Q{end_number}, but
only if the available content supports distinct, factual questions. If it does
not, generate as many as the content supports and stop.

Rules:
- Use exactly 5 options for every question: A, B, C, D, E.
- Every question must include exactly one answer line immediately after options.
- Format every answer line exactly as: Answer: [letter]
- Do not repeat earlier questions.
- Do not invent facts.
- Do not write a summary or explanation.

PERIOD SUMMARY:
{summary}

RAW CONTENT:
{content}
"""


def _valid_questions(questions: list[dict], limit: int) -> list[dict]:
    cleaned = []
    for question in questions[:limit]:
        if not question.get("question"):
            continue
        if len(question.get("options", [])) != 5:
            continue
        if not question.get("answer"):
            continue
        cleaned.append(question)
    return cleaned


def _complete_some_questions(
    questions: list[dict],
    content: str,
    summary: str,
    period_name: str,
    target_questions: int,
    output_key: str,
) -> list[dict]:
    questions = _valid_questions(questions, target_questions)
    if len(questions) >= target_questions:
        return questions

    if len(questions) == 0:
        print("  [WARN] No complete MCQs parsed from initial response.")
        return questions

    start_number = len(questions) + 1
    print(
        f"  Initial response had {len(questions)} complete MCQs; "
        f"requesting up to Q{target_questions}."
    )
    prompt = _build_more_questions_prompt(
        content,
        summary,
        period_name,
        start_number,
        target_questions,
    )
    continuation = call_ollama_with_fallback(prompt)
    path = _save_raw_ollama_response(
        None,
        None,
        continuation,
        suffix="available-continuation",
        output_key=output_key,
    )
    print(f"  Raw continuation saved -> {path}")

    needed = target_questions - len(questions)
    extra_questions = _valid_questions(parse_questions(continuation), needed)
    print(f"  Continuation parsed: {len(extra_questions)} complete MCQs")
    return questions + extra_questions


def backfill_week(
    week: int,
    target_questions: int | None,
    overwrite: bool,
    dry_run: bool,
) -> None:
    start_date, end_date = get_week_period(week)
    output_key = _period_key(start_date, end_date)
    period_name = f"Week {week} ({start_date:%d %b %Y} - {end_date:%d %b %Y})"

    if _outputs_exist(output_key) and not overwrite:
        print(f"Week {week}: outputs already exist; skipping {output_key}.")
        return

    pib_releases = _load_cached_week_items(output_key, "pib")
    rbi_items = _load_cached_week_items(output_key, "rbi")
    usable_pib = _usable_detail_items(pib_releases)
    usable_rbi = _usable_detail_items(rbi_items)
    usable_count = len(usable_pib) + len(usable_rbi)
    word_count = sum(len((item.get("content") or "").split()) for item in usable_pib + usable_rbi)
    question_count = target_questions or _auto_question_count(usable_count, word_count)

    print("=" * 60)
    print(f"Available-content backfill - {period_name}")
    print("=" * 60)
    print(f"  PIB: {_detail_content_status('pib', pib_releases)}")
    print(f"  RBI: {_detail_content_status('rbi', rbi_items)}")
    print(f"  Usable available content: {usable_count} items, {word_count:,} words")
    print(f"  Target questions: {question_count}")

    if usable_count == 0 or question_count == 0:
        raise RuntimeError(f"Week {week} has no usable detail content to backfill.")

    if dry_run:
        return

    t_start = time.time()
    combined = prepare_prompt_content(
        usable_pib,
        usable_rbi,
        period_name,
        None,
        None,
        output_key,
    )
    source_note = (
        f"Using {len(usable_pib)}/{len(pib_releases)} PIB items and "
        f"{len(usable_rbi)}/{len(rbi_items)} RBI items with at least 25 detail words."
    )
    prompt = _build_available_prompt(combined, period_name, question_count, source_note)

    response = call_ollama_with_fallback(prompt)
    raw_path = _save_raw_ollama_response(
        None,
        None,
        response,
        suffix="available-response",
        output_key=output_key,
    )
    print(f"  Raw response saved -> {raw_path}")

    summary, questions_raw = split_response(response)
    summary = clean_summary_markdown(summary)
    questions = parse_questions(questions_raw)
    questions = _complete_some_questions(
        questions,
        combined,
        summary,
        period_name,
        question_count,
        output_key,
    )
    questions = _valid_questions(questions, question_count)
    if not questions:
        raise RuntimeError("No complete MCQs were generated; refusing to save empty questions.")

    _save_outputs(
        summary,
        questions,
        period_name,
        None,
        None,
        output_key,
        f"Week {week} available-content backfill",
        None,
    )
    print(
        f"Week {week} backfilled in {time.time() - t_start:.1f}s "
        f"with {len(questions)} MCQs."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill weekly summaries/questions using available cached content only."
    )
    parser.add_argument(
        "--week",
        type=int,
        action="append",
        help="Week number to backfill. May be passed multiple times.",
    )
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help="Backfill every completed configured week missing summary/questions.",
    )
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Include configured weeks whose end date is after today.",
    )
    parser.add_argument(
        "--questions",
        type=int,
        help="Override automatic question count for every selected week.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate outputs even when summary/questions already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected weeks and usable-content counts without calling Ollama.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.week and not args.all_missing:
        raise SystemExit("Pass --all-missing or at least one --week.")

    if args.questions is not None and not (1 <= args.questions <= MAX_BACKFILL_QUESTIONS):
        raise SystemExit(f"--questions must be between 1 and {MAX_BACKFILL_QUESTIONS}.")

    weeks = list(args.week or [])
    if args.all_missing:
        weeks.extend(_missing_week_numbers(args.include_incomplete))

    selected_weeks = []
    for week in weeks:
        if week < 1 or week > total_configured_weeks():
            raise SystemExit(f"Week {week} is outside configured range 1-{total_configured_weeks()}.")
        if week not in selected_weeks:
            selected_weeks.append(week)

    if not selected_weeks:
        print("No weeks selected for backfill.")
        return

    for week in selected_weeks:
        backfill_week(
            week,
            target_questions=args.questions,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
