"""
Module 4 — Daily Pipeline Runner
Processes monthly or weekly PIB + RBI data, generates a GA summary and MCQs via Ollama.
Usage: python pipeline/daily_runner.py --day N OR python pipeline/daily_runner.py --week N
"""

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import smtplib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    CHUNK_CONTENT_WORDS,
    CHUNK_SUMMARY_WORDS,
    DAY_MAP,
    DEFAULT_EXAM,
    EXAMS,
    MAX_CONTENT_WORDS,
    MIN_DETAIL_CONTENT_COVERAGE,
    MIN_DETAIL_CONTENT_WORDS,
    OLLAMA_CONNECT_TIMEOUT_SECONDS,
    OLLAMA_DISABLE_THINKING,
    OLLAMA_MODEL_FALLBACK,
    OLLAMA_MODEL_PRIMARY,
    OLLAMA_MODELS,
    OLLAMA_NUM_CTX,
    OLLAMA_NUM_PREDICT,
    OLLAMA_READ_TIMEOUT_SECONDS,
    OLLAMA_URL,
    WEEK_RANGE_END,
    WEEK_RANGE_START,
)
from pipeline.prompt_builder import build_prompt   # imported but called inside run_day()

BASE_DIR = Path(__file__).resolve().parent.parent
SUMMARIES_DIR = BASE_DIR / "data" / "summaries"
SUMMARY_PDF_DIR = BASE_DIR / "data" / "summaries" / "pdf"
GEN_Q_DIR = BASE_DIR / "data" / "questions" / "generated"
PDF_Q_DIR = BASE_DIR / "data" / "questions" / "pdf"
SCRAPED_DIR = BASE_DIR / "data" / "scraped"
LLM_RAW_DIR = BASE_DIR / "data" / "llm_raw"
CHUNK_NOTES_DIR = BASE_DIR / "data" / "chunk_notes"
EXPECTED_QUESTION_COUNT = 80

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

SOURCE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _period_display(period_name: str, year: int | str | None = None) -> str:
    return f"{period_name} {year}".strip() if year else period_name


def _period_key(start_date: date, end_date: date) -> str:
    return f"{start_date:%Y-%m-%d}_to_{end_date:%Y-%m-%d}"


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def _configured_week_periods() -> list[tuple[date, date]]:
    start = _parse_iso_date(WEEK_RANGE_START)

    if WEEK_RANGE_END.strip():
        # Fixed end date: enumerate 7-day blocks up to (and clamped at) the end.
        end = _parse_iso_date(WEEK_RANGE_END)
        if end < start:
            raise RuntimeError(
                f"Invalid weekly date range: {WEEK_RANGE_START} to {WEEK_RANGE_END}."
            )
        clamp_last_block = True
    else:
        # Open-ended schedule: extend in full 7-day blocks up to the most recent
        # week that has already completed, so a fresh week appears every week.
        end = date.today()
        clamp_last_block = False

    periods = []
    cursor = start
    while cursor <= end:
        period_end = cursor + timedelta(days=6)
        if clamp_last_block:
            period_end = min(period_end, end)
        elif period_end > end:
            # Open-ended: only publish weeks whose full 7-day block has elapsed.
            break
        periods.append((cursor, period_end))
        cursor = period_end + timedelta(days=1)
    return periods


def get_week_period(week: int) -> tuple[date, date]:
    periods = _configured_week_periods()
    if week < 1 or week > len(periods):
        raise RuntimeError(f"Week {week} is not configured. Valid weeks: 1-{len(periods)}.")
    return periods[week - 1]


def total_configured_weeks() -> int:
    return len(_configured_week_periods())


def _months_in_range(start_date: date, end_date: date) -> list[tuple[int, int]]:
    months = []
    cursor = date(start_date.year, start_date.month, 1)
    last = date(end_date.year, end_date.month, 1)
    while cursor <= last:
        months.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return months


def _parse_source_date(item: dict) -> date | None:
    raw_date = str(item.get("date", "")).strip()
    for fmt in SOURCE_DATE_FORMATS:
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            pass

    content = str(item.get("content", ""))
    match = re.search(r"Posted\s+On:\s*(\d{1,2}\s+[A-Z]{3,9}\s+20\d{2})", content, re.I)
    if match:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(match.group(1).title(), fmt).date()
            except ValueError:
                pass
    return None


def _filter_items_by_date(items: list[dict], start_date: date, end_date: date) -> list[dict]:
    filtered = []
    skipped_unparsed = 0
    for item in items:
        item_date = _parse_source_date(item)
        if item_date is None:
            skipped_unparsed += 1
            continue
        if start_date <= item_date <= end_date:
            filtered.append(item)
    if skipped_unparsed:
        print(f"  Skipped {skipped_unparsed} items with unparseable dates for weekly filtering.")
    return filtered


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(BASE_DIR / ".env")

# FIX 1 — removed module-level `OLLAMA_PROMPT_TEMPLATE = build_prompt(content, month_name, year)`
#          content / month_name / year don't exist at import time → moved inside run_day()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n... [truncated]"


def _token_budget_words() -> int:
    # A conservative char/token approximation keeps prompts inside Ollama's
    # context window while leaving room for instructions and generation state.
    context_limited_words = int((OLLAMA_NUM_CTX or 32768) * 0.62)
    return min(MAX_CONTENT_WORDS, context_limited_words)


def _format_pib_content(releases: list[dict]) -> str:
    parts = []
    for r in releases:
        parts.append(f"[PIB] {r.get('date', '')} — {r.get('title', '')}")
        if r.get("url"):
            parts.append(f"  URL: {r['url']}")
        if r.get("content"):
            parts.append("  DETAIL CONTENT:")
            parts.append(r["content"])
        parts.append("")
    return "\n".join(parts)


def _detail_content_word_count(item: dict) -> int:
    content = item.get("content") if isinstance(item, dict) else ""
    return len((content or "").split())


def _detail_content_stats(items: list[dict]) -> tuple[int, int, float, int]:
    total = len([item for item in items if isinstance(item, dict)])
    if not total:
        return 0, 0, 0.0, 0

    content_word_counts = [
        _detail_content_word_count(item)
        for item in items
        if isinstance(item, dict)
    ]
    usable = sum(1 for count in content_word_counts if count >= MIN_DETAIL_CONTENT_WORDS)
    total_words = sum(content_word_counts)
    return total, usable, usable / total, total_words


def _detail_content_status(source: str, items: list[dict]) -> str:
    total, usable, coverage, total_words = _detail_content_stats(items)
    return (
        f"{source.upper()} detail content: {usable}/{total} items usable "
        f"({coverage:.0%}, {total_words:,} words)"
    )


def _has_enough_detail_content(items: list[dict]) -> bool:
    total, _usable, coverage, _total_words = _detail_content_stats(items)
    return bool(total) and coverage >= MIN_DETAIL_CONTENT_COVERAGE


def _has_usable_detail_content(item: dict) -> bool:
    return _detail_content_word_count(item) >= MIN_DETAIL_CONTENT_WORDS


def _usable_detail_items(items: list[dict]) -> list[dict]:
    return [
        item for item in items
        if isinstance(item, dict) and _has_usable_detail_content(item)
    ]


def _validate_detail_content(source: str, items: list[dict]) -> None:
    if _has_enough_detail_content(items):
        print(f"  {_detail_content_status(source, items)}")
        return

    total, usable, coverage, total_words = _detail_content_stats(items)
    raise RuntimeError(
        f"{source.upper()} scrape data is not usable for exam generation: "
        f"only {usable}/{total} items have at least {MIN_DETAIL_CONTENT_WORDS} "
        f"detail-content words ({coverage:.0%}, {total_words:,} total words). "
        "Refusing to generate summaries/questions from titles alone. "
        "Run again with --refresh-cache after fixing the scraper or network issue."
    )


def _ensure_some_usable_content(
    named_sources: list[tuple[str, list[dict]]], period_label: str
) -> None:
    """Proceed as long as AT LEAST ONE source has usable detail content.

    Sources (e.g. PIB and RBI) are combined, but a given week may be thin on one
    of them. Rather than refusing the whole week when a single source is weak, we
    print each source's status and only raise if *no* source has usable content —
    in which case there is genuinely nothing but titles to work with. Downstream
    content building already filters to usable items, so generation proceeds from
    whatever is available.
    """
    total_usable = 0
    for name, items in named_sources:
        if not items:
            print(f"  [WARN] No {name.upper()} items for {period_label}.")
            continue
        _t, usable, _cov, _w = _detail_content_stats(items)
        print(f"  {_detail_content_status(name, items)}")
        if usable == 0:
            print(f"  [WARN] {name.upper()} has no usable detail content — "
                  f"skipping it; questions will use the other source(s).")
        total_usable += usable
    if total_usable == 0:
        raise RuntimeError(
            f"No usable detail content from any source for {period_label} "
            "(only titles/empty items). Refusing to generate from titles alone — "
            "re-run with --refresh-cache after fixing the scraper or network issue."
        )


def _format_rbi_content(items: list[dict]) -> str:
    parts = []
    for item in items:
        tag = "[RBI Circular]" if item.get("type") == "circular" else "[RBI Press Release]"
        parts.append(f"{tag} {item.get('date', '')} — {item.get('subject', '')}")
        if item.get("department"):
            parts.append(f"  Department: {item['department']}")
        if item.get("url"):
            parts.append(f"  URL: {item['url']}")
        if item.get("content"):
            parts.append("  DETAIL CONTENT:")
            parts.append(item["content"])
        parts.append("")
    return "\n".join(parts)


def _pib_block(release: dict) -> str:
    parts = [f"[PIB] {release.get('date', '')} — {release.get('title', '')}"]
    if release.get("url"):
        parts.append(f"  URL: {release['url']}")
    if release.get("content"):
        parts.append("  DETAIL CONTENT:")
        parts.append(release["content"])
    return "\n".join(parts)


def _rbi_block(item: dict) -> str:
    tag = "[RBI Circular]" if item.get("type") == "circular" else "[RBI Press Release]"
    parts = [f"{tag} {item.get('date', '')} — {item.get('subject', '')}"]
    if item.get("department"):
        parts.append(f"  Department: {item['department']}")
    if item.get("url"):
        parts.append(f"  URL: {item['url']}")
    if item.get("content"):
        parts.append("  DETAIL CONTENT:")
        parts.append(item["content"])
    return "\n".join(parts)


def _format_block_for(canon: str, item: dict) -> str:
    """Format one scraped item identically regardless of which exam is asking, so a
    shared source produces byte-identical blocks → its condensation cache is reused
    across exams (the cache keys on content hash). Falls back to a generic format."""
    if canon == "pib":
        return _pib_block(item)
    if canon == "rbi":
        return _rbi_block(item)
    return _generic_block(item, canon.upper())


def _source_blocks(pib_releases: list[dict], rbi_items: list[dict]) -> list[str]:
    blocks = ["=== PIB Press Releases ==="]
    blocks.extend(_pib_block(release) for release in _usable_detail_items(pib_releases))
    blocks.append("=== RBI Circulars & Press Releases ===")
    blocks.extend(_rbi_block(item) for item in _usable_detail_items(rbi_items))
    return blocks


def _chunk_blocks(blocks: list[str], max_words: int) -> list[str]:
    chunks = []
    current = []
    current_words = 0

    for block in blocks:
        block_words = len(block.split())
        if current and current_words + block_words > max_words:
            chunks.append("\n\n".join(current))
            current = []
            current_words = 0
        current.append(block)
        current_words += block_words

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _scrape_cache_dir(year: int, month: int) -> Path:
    return SCRAPED_DIR / f"{year}-{month:02d}"


def _scrape_cache_path(source: str, year: int, month: int) -> Path:
    return _scrape_cache_dir(year, month) / f"{source}.json"


# Several exams draw on the SAME upstream source — e.g. RBI Grade B's "pib" and
# UPSC's "pib_all" are both the all-ministry PIB scrape (scrape_pib_range queries
# every ministry regardless). Cache by a canonical source key so a source+period is
# scraped at most once and shared across exams; only question generation differs per
# exam (by weightage). Period scrape cache is therefore exam-INDEPENDENT.
_CANONICAL_SOURCE = {
    "pib": "pib",
    "pib_all": "pib",
    "rbi": "rbi",
    "econsurvey": "econsurvey",
}


def _canonical_source(source: str) -> str:
    return _CANONICAL_SOURCE.get(source, source)


# (canonical_source, output_key) pairs scraped during THIS process. Lets a single
# --all-exams / --refresh-cache run scrape each source once even across exams.
_SCRAPED_THIS_RUN: set[tuple[str, str]] = set()


def _use_period_cache(source: str, output_key: str, refresh_cache: bool) -> bool:
    """Use the cache unless a refresh was asked — but still reuse a scrape we
    already did this run, so shared sources aren't re-fetched per exam."""
    if not refresh_cache:
        return True
    return (_canonical_source(source), output_key) in _SCRAPED_THIS_RUN


def _mark_scraped(source: str, output_key: str) -> None:
    _SCRAPED_THIS_RUN.add((_canonical_source(source), output_key))


def _period_scrape_cache_path(source: str, output_key: str) -> Path:
    # Canonical source name → "pib" and "pib_all" share one file across exams.
    return SCRAPED_DIR / output_key / f"{_canonical_source(source)}.json"


def _load_scrape_cache(source: str, year: int, month: int) -> list[dict] | None:
    path = _scrape_cache_path(source, year, month)
    if not path.exists():
        return None

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [WARN] Could not read cached {source.upper()} data at {path}: {e}")
        return None

    if isinstance(data, list):
        if data and not _has_enough_detail_content(data):
            print(
                f"  [WARN] Cached {source.upper()} data has insufficient detail-page content "
                f"({_detail_content_status(source, data)}); "
                "scraping again."
            )
            return None
        print(
            f"  Loaded cached {source.upper()} data: {len(data)} items "
            f"({_detail_content_status(source, data)}) ({path})"
        )
        return data

    print(f"  [WARN] Ignoring cached {source.upper()} data at {path}: expected a JSON list.")
    return None


def _load_period_scrape_cache(source: str, output_key: str) -> list[dict] | None:
    path = _period_scrape_cache_path(source, output_key)
    if not path.exists():
        return None

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [WARN] Could not read cached weekly {source.upper()} data at {path}: {e}")
        return None

    if isinstance(data, list):
        if data and not _has_enough_detail_content(data):
            print(
                f"  [WARN] Cached weekly {source.upper()} data has insufficient detail-page content "
                f"({_detail_content_status(source, data)}); scraping again."
            )
            return None
        print(
            f"  Loaded cached weekly {source.upper()} data: {len(data)} items "
            f"({path})"
        )
        return data

    print(f"  [WARN] Ignoring cached weekly {source.upper()} data at {path}: expected a JSON list.")
    return None


def _save_scrape_cache(source: str, year: int, month: int, items: list[dict]) -> None:
    if not items:
        print(f"  [WARN] Not caching empty {source.upper()} scrape result.")
        return

    path = _scrape_cache_path(source, year, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)
    print(f"  Cached {source.upper()} data: {len(items)} items ({path})")


def _save_period_scrape_cache(source: str, output_key: str, items: list[dict]) -> None:
    path = _period_scrape_cache_path(source, output_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)
    _mark_scraped(source, output_key)
    print(f"  Cached weekly {source.upper()} data: {len(items)} items ({path})")


def _clean_generated_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def clean_summary_markdown(markdown: str) -> str:
    cleaned_lines = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        # Models sometimes emit hybrid headings like "### ## RBI & Monetary Policy".
        heading_match = re.match(r"^(#{1,6})\s+(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            stripped = f"{heading_match.group(2)} {heading_match.group(3)}"

        if stripped == "**":
            continue

        cleaned_lines.append(stripped if stripped.startswith("#") else line)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _save_raw_ollama_response(
    year: int | str | None,
    month: int | None,
    response: str,
    suffix: str = "response",
    output_key: str | None = None,
) -> Path:
    LLM_RAW_DIR.mkdir(parents=True, exist_ok=True)
    key = output_key or _month_key(int(year), int(month))
    path = LLM_RAW_DIR / f"{key}-{suffix}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(response)
    return path


def _question_pdf_path_for_key(output_key: str) -> Path:
    return PDF_Q_DIR / f"{output_key}-qs.pdf"


def _summary_pdf_path_for_key(output_key: str) -> Path:
    return SUMMARY_PDF_DIR / f"{output_key}-summary.pdf"


def _question_pdf_path(year: int, month: int) -> Path:
    return _question_pdf_path_for_key(_month_key(year, month))


def _summary_pdf_path(year: int, month: int) -> Path:
    return _summary_pdf_path_for_key(_month_key(year, month))


def _markdown_inline_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def _summary_markdown_to_html(markdown: str, month_name: str, year: int | str | None) -> str:
    period_label = _period_display(month_name, year)
    body_parts = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body_parts.append("</ul>")
            in_list = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            close_list()
            continue
        if line in {"---", "----", "-----"}:
            close_list()
            body_parts.append("<hr>")
            continue
        if line.startswith("### "):
            close_list()
            body_parts.append(f"<h3>{_markdown_inline_to_html(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            close_list()
            body_parts.append(f"<h2>{_markdown_inline_to_html(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            close_list()
            body_parts.append(f"<h1>{_markdown_inline_to_html(line[2:])}</h1>")
            continue
        if line.startswith("- "):
            if not in_list:
                body_parts.append("<ul>")
                in_list = True
            body_parts.append(f"<li>{_markdown_inline_to_html(line[2:])}</li>")
            continue
        close_list()
        body_parts.append(f"<p>{_markdown_inline_to_html(line)}</p>")

    close_list()
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RBI Grade B GA Summary - {html.escape(period_label)}</title>
  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm;
    }}
    body {{
      color: #111827;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 11.2pt;
      line-height: 1.48;
    }}
    header {{
      border-bottom: 1px solid #d1d5db;
      margin-bottom: 18px;
      padding-bottom: 10px;
    }}
    h1 {{
      font-size: 20pt;
      margin: 0 0 8px;
    }}
    h2 {{
      break-after: avoid;
      border-bottom: 1px solid #e5e7eb;
      font-size: 15pt;
      margin: 22px 0 8px;
      padding-bottom: 4px;
    }}
    h3 {{
      font-size: 12.5pt;
      margin: 16px 0 6px;
    }}
    .meta {{
      color: #4b5563;
      font-size: 10pt;
    }}
    ul {{
      margin: 6px 0 12px 18px;
      padding: 0;
    }}
    li {{
      margin: 4px 0;
    }}
    p {{
      margin: 7px 0;
    }}
    code {{
      background: #f3f4f6;
      border-radius: 3px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      padding: 1px 3px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>RBI Grade B GA Summary</h1>
    <div class="meta">{html.escape(period_label)}</div>
  </header>
  {''.join(body_parts)}
</body>
</html>"""


def _questions_to_html(questions: list[dict], month_name: str, year: int | str | None) -> str:
    period_label = _period_display(month_name, year)
    question_blocks = []
    answer_key_items = []

    for index, question in enumerate(questions, 1):
        options_html = "\n".join(
            f"<li>{html.escape(option)}</li>"
            for option in question.get("options", [])
        )
        answer = html.escape(question.get("answer", ""))
        answer_key_items.append(f"<div><strong>{index}.</strong> {answer}</div>")
        question_blocks.append(
            f"""
            <section class="question">
              <h2>Q{index}. {html.escape(question.get("question", ""))}</h2>
              <ul class="options">
                {options_html}
              </ul>
            </section>
            """
        )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RBI Grade B GA MCQs - {html.escape(period_label)}</title>
  <style>
    @page {{
      size: A4;
      margin: 18mm 16mm;
    }}
    body {{
      color: #111827;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 11.5pt;
      line-height: 1.45;
    }}
    header {{
      border-bottom: 1px solid #d1d5db;
      margin-bottom: 18px;
      padding-bottom: 10px;
    }}
    h1 {{
      font-size: 20pt;
      margin: 0 0 4px;
    }}
    .meta {{
      color: #4b5563;
      font-size: 10pt;
    }}
    .question {{
      break-inside: avoid;
      border-bottom: 1px solid #e5e7eb;
      padding: 10px 0 12px;
    }}
    .question h2 {{
      font-size: 12.5pt;
      margin: 0 0 8px;
    }}
    .options {{
      list-style: none;
      margin: 0;
      padding-left: 0;
    }}
    .options li {{
      margin: 3px 0;
    }}
    .answer-key {{
      break-before: page;
    }}
    .answer-grid {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 8px 14px;
      font-size: 11pt;
    }}
  </style>
</head>
<body>
  <header>
    <h1>RBI Grade B GA Practice MCQs</h1>
    <div class="meta">{html.escape(period_label)} · {len(questions)} questions</div>
  </header>
  {''.join(question_blocks)}
  <section class="answer-key">
    <h1>Answer Key</h1>
    <div class="answer-grid">
      {''.join(answer_key_items)}
    </div>
  </section>
</body>
</html>"""


def render_questions_pdf(
    questions: list[dict],
    month_name: str,
    year: int | str | None,
    month: int | None,
    output_key: str | None = None,
) -> Path:
    PDF_Q_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = _question_pdf_path_for_key(output_key or _month_key(int(year), int(month)))
    html_content = _questions_to_html(questions, month_name, year)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is required for PDF export. Run: python -m pip install playwright && "
            "python -m playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content, wait_until="load")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                "<div style='width:100%;font-size:8px;color:#6b7280;"
                "padding:0 16mm;text-align:right;'>"
                "Page <span class='pageNumber'></span> of <span class='totalPages'></span>"
                "</div>"
            ),
        )
        browser.close()

    return pdf_path


def render_summary_pdf(
    summary_markdown: str,
    month_name: str,
    year: int | str | None,
    month: int | None,
    output_key: str | None = None,
) -> Path:
    SUMMARY_PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = _summary_pdf_path_for_key(output_key or _month_key(int(year), int(month)))
    html_content = _summary_markdown_to_html(summary_markdown, month_name, year)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is required for PDF export. Run: python -m pip install playwright && "
            "python -m playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content, wait_until="load")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                "<div style='width:100%;font-size:8px;color:#6b7280;"
                "padding:0 16mm;text-align:right;'>"
                "Page <span class='pageNumber'></span> of <span class='totalPages'></span>"
                "</div>"
            ),
        )
        browser.close()

    return pdf_path


def parse_recipients(raw_recipients: str) -> list[str]:
    return [
        recipient.strip()
        for recipient in re.split(r"[,;]", raw_recipients or "")
        if recipient.strip()
    ]


def send_pdf_email(
    pdf_paths: list[Path],
    recipients: list[str],
    month_name: str,
    year: int | str | None,
) -> None:
    period_label = _period_display(month_name, year)
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_from = os.getenv("SMTP_FROM") or smtp_user

    def _is_placeholder(value: str) -> bool:
        lowered = value.lower()
        return (
            not value
            or "your_" in lowered
            or "example.com" in lowered
            or "app_password" in lowered
        )

    missing = [
        name
        for name, value in {
            "SMTP_HOST": smtp_host,
            "SMTP_USER": smtp_user,
            "SMTP_PASSWORD": smtp_password,
            "SMTP_FROM or SMTP_USER": smtp_from,
            "recipient email": ",".join(recipients),
        }.items()
        if _is_placeholder(value)
    ]
    if missing:
        raise RuntimeError(
            "Email requested, but SMTP config is incomplete. Missing: "
            + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = f"RBI Grade B GA Prep PDFs - {period_label}"
    message["From"] = smtp_from
    message["To"] = ", ".join(recipients)
    message.set_content(
        f"Attached are the RBI Grade B GA summary and practice MCQ PDFs for {period_label}.\n"
    )

    for pdf_path in pdf_paths:
        mime_type, _ = mimetypes.guess_type(pdf_path)
        maintype, subtype = (mime_type or "application/pdf").split("/", 1)
        with open(pdf_path, "rb") as f:
            message.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=pdf_path.name,
            )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(message)


def email_existing_pdfs(day: int, raw_recipients: str) -> None:
    if day not in DAY_MAP:
        print(f"[ERROR] Day {day} not in DAY_MAP (valid: {sorted(DAY_MAP)}).")
        sys.exit(1)

    year, month = DAY_MAP[day]
    month_name = MONTH_NAMES[month]
    recipients = parse_recipients(raw_recipients)
    summary_pdf_path = _summary_pdf_path(year, month)
    questions_pdf_path = _question_pdf_path(year, month)
    missing_paths = [
        path for path in [summary_pdf_path, questions_pdf_path]
        if not path.exists()
    ]
    if missing_paths:
        missing = ", ".join(str(path) for path in missing_paths)
        raise RuntimeError(f"Cannot email existing PDFs because these files are missing: {missing}")

    send_pdf_email([summary_pdf_path, questions_pdf_path], recipients, month_name, year)
    print(f"  Existing summary and questions PDFs emailed → {', '.join(recipients)}")


def email_existing_week_pdfs(week: int, raw_recipients: str) -> None:
    start_date, end_date = get_week_period(week)
    period_name = f"Week {week} ({start_date:%d %b %Y} - {end_date:%d %b %Y})"
    output_key = _period_key(start_date, end_date)
    recipients = parse_recipients(raw_recipients)
    summary_pdf_path = _summary_pdf_path_for_key(output_key)
    questions_pdf_path = _question_pdf_path_for_key(output_key)
    missing_paths = [
        path for path in [summary_pdf_path, questions_pdf_path]
        if not path.exists()
    ]
    if missing_paths:
        missing = ", ".join(str(path) for path in missing_paths)
        raise RuntimeError(f"Cannot email existing PDFs because these files are missing: {missing}")

    send_pdf_email([summary_pdf_path, questions_pdf_path], recipients, period_name, None)
    print(f"  Existing weekly summary and questions PDFs emailed → {', '.join(recipients)}")


def _configured_ollama_models() -> list[str]:
    env_models = os.getenv("OLLAMA_MODELS", "")
    if env_models.strip():
        candidates = [model.strip() for model in env_models.split(",")]
    else:
        candidates = list(OLLAMA_MODELS or [OLLAMA_MODEL_PRIMARY, OLLAMA_MODEL_FALLBACK])

    models = []
    for model in candidates:
        if model and model not in models:
            models.append(model)
    return models


def _ollama_options(num_predict: int | None = None) -> dict:
    options = {}
    if OLLAMA_NUM_CTX:
        options["num_ctx"] = OLLAMA_NUM_CTX
    predict_budget = num_predict or OLLAMA_NUM_PREDICT
    if predict_budget:
        options["num_predict"] = predict_budget
    return options


def _call_ollama(prompt: str, model: str, num_predict: int | None = None) -> str | None:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "think": not OLLAMA_DISABLE_THINKING,
        "system": (
            "You are an expert assistant helping a student prepare for the "
            "RBI Grade B Phase 1 General Awareness exam. Be concise and exam-focused."
        ),
    }
    options = _ollama_options(num_predict)
    if options:
        payload["options"] = options

    try:
        chunks = []
        thinking_chunks = 0
        done_reason = ""
        with requests.post(
            OLLAMA_URL,
            json=payload,
            stream=True,
            timeout=(OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_READ_TIMEOUT_SECONDS),
        ) as r:
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                body = ""
                try:
                    body = r.text[:1000]
                except Exception:
                    pass
                raise RuntimeError(f"{e}; response body: {body}") from e
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise RuntimeError(data["error"])
                if data.get("response"):
                    chunks.append(data["response"])
                if data.get("thinking"):
                    thinking_chunks += 1
                if data.get("done"):
                    done_reason = data.get("done_reason", "")
                    break
        response = "".join(chunks).strip()
        if not response:
            detail = f"thinking chunks: {thinking_chunks}"
            if done_reason:
                detail += f", done_reason: {done_reason}"
            print(f"  [WARN] Ollama model {model} returned no response text ({detail}).")
            return None
        return response
    except Exception as e:
        print(f"  [WARN] Ollama call with model {model} failed: {e}")
        return None


def call_ollama_with_fallback(prompt: str) -> str:
    models = _configured_ollama_models()
    for index, model in enumerate(models, 1):
        prefix = "Calling" if index == 1 else "Falling back to"
        print(f"  {prefix} Ollama ({model}) ...")
        response = _call_ollama(prompt, model)
        if response:
            return response

        if "cloud" in model or "gpt-oss" in model:
            retry_budget = max(OLLAMA_NUM_PREDICT * 2, 24000)
            final_only_prompt = (
                "Return the final answer only. Do not spend output budget on analysis, "
                "reasoning traces, or thinking text. Start the requested response immediately.\n\n"
                + prompt
            )
            print(f"  Retrying {model} with a larger final-answer budget ({retry_budget}) ...")
            response = _call_ollama(final_only_prompt, model, num_predict=retry_budget)
            if response:
                return response
    raise RuntimeError(f"All Ollama models failed: {', '.join(models)}")


def _chunk_notes_cache_dir(year: int | str, month: int | None, output_key: str | None = None) -> Path:
    key = output_key or _month_key(int(year), int(month))
    return CHUNK_NOTES_DIR / key


def _chunk_notes_cache_path(
    year: int | str,
    month: int | None,
    index: int,
    output_key: str | None = None,
) -> Path:
    return _chunk_notes_cache_dir(year, month, output_key) / f"chunk-{index:03d}.json"


def _chunk_manifest_path(year: int | str, month: int | None, output_key: str | None = None) -> Path:
    return _chunk_notes_cache_dir(year, month, output_key) / "manifest.json"


def _source_fingerprint(blocks: list[str]) -> str:
    payload = {
        "chunk_content_words": CHUNK_CONTENT_WORDS,
        "chunk_summary_words": CHUNK_SUMMARY_WORDS,
        "blocks": blocks,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_chunk_note(path: Path, content_hash: str) -> str | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if data.get("hash") == content_hash and data.get("notes"):
        return data["notes"]
    return None


def _save_chunk_note(path: Path, content_hash: str, notes: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"hash": content_hash, "notes": notes}, f, indent=2, ensure_ascii=False)
    tmp_path.replace(path)


def _load_chunk_manifest(
    year: int | str,
    month: int | None,
    source_hash: str,
    output_key: str | None = None,
) -> str | None:
    path = _chunk_manifest_path(year, month, output_key)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if (
        data.get("source_hash") == source_hash
        and data.get("chunk_content_words") == CHUNK_CONTENT_WORDS
        and data.get("chunk_summary_words") == CHUNK_SUMMARY_WORDS
        and data.get("combined_notes")
    ):
        return data["combined_notes"]
    return None


def _save_chunk_manifest(
    year: int | str,
    month: int | None,
    source_hash: str,
    combined_notes: str,
    chunk_count: int,
    output_key: str | None = None,
) -> None:
    path = _chunk_manifest_path(year, month, output_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "source_hash": source_hash,
                "chunk_content_words": CHUNK_CONTENT_WORDS,
                "chunk_summary_words": CHUNK_SUMMARY_WORDS,
                "chunk_count": chunk_count,
                "combined_notes": combined_notes,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    tmp_path.replace(path)


def _chunk_summary_prompt(
    chunk: str,
    month_name: str,
    year: int | str | None,
    index: int,
    total: int,
) -> str:
    period_label = _period_display(month_name, year)
    return f"""\
You are preparing RBI Grade B Phase 1 General Awareness notes from source material.

This is chunk {index} of {total} for {period_label}.
Extract only exam-relevant facts from the raw PIB/RBI material below.

Output concise markdown notes, maximum {CHUNK_SUMMARY_WORDS} words.
Preserve exact dates, names, numbers, schemes, reports, institutions, rates, penalties, and RBI circular names.
Group notes under these headings where applicable:
## RBI & Monetary Policy
## Banking & Financial Sector
## Government Schemes & Budget
## Economy & Trade
## International Affairs & Organizations
## Awards, Rankings & Appointments
## Sports / Environment / Science & Tech

Do not create MCQs. Do not add facts that are not present in the source.

RAW CHUNK:
{chunk}
"""


def _summarize_large_content(
    blocks: list[str],
    month_name: str,
    year: int | str | None,
    month: int | None,
    output_key: str | None = None,
) -> str:
    source_hash = _source_fingerprint(blocks)
    cached_combined_notes = _load_chunk_manifest(year, month, source_hash, output_key)
    if cached_combined_notes:
        print("  Loaded cached period-level chunk notes; skipping chunking.")
        return cached_combined_notes

    chunks = _chunk_blocks(blocks, CHUNK_CONTENT_WORDS)
    print(
        f"  Content exceeds final prompt budget; summarizing {len(chunks)} chunks "
        f"({CHUNK_CONTENT_WORDS} words/chunk target) ..."
    )

    notes = []
    for index, chunk in enumerate(chunks, 1):
        content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        cache_path = _chunk_notes_cache_path(year, month, index, output_key)
        cached_note = _load_chunk_note(cache_path, content_hash)
        if cached_note:
            print(f"    [{index}/{len(chunks)}] Loaded cached chunk notes")
            notes.append(cached_note)
            continue

        print(f"    [{index}/{len(chunks)}] Summarizing chunk ({len(chunk.split())} words)")
        prompt = _chunk_summary_prompt(chunk, month_name, year, index, len(chunks))
        note = call_ollama_with_fallback(prompt)
        note = _truncate_to_words(clean_summary_markdown(note), CHUNK_SUMMARY_WORDS)
        _save_chunk_note(cache_path, content_hash, note)
        notes.append(note)

    combined_notes = []
    for index, note in enumerate(notes, 1):
        combined_notes.append(f"=== Condensed Notes: Chunk {index} ===\n\n{note}")
    result = "\n\n".join(combined_notes)
    _save_chunk_manifest(year, month, source_hash, result, len(chunks), output_key)
    return result


def _neutral_source_chunk_prompt(
    source_label: str, chunk: str, index: int, total: int, period_label: str
) -> str:
    return f"""\
You are extracting General Awareness facts for Indian government competitive exams
from {source_label} for {period_label}. This is chunk {index} of {total}.

Output concise markdown bullet notes, maximum {CHUNK_SUMMARY_WORDS} words. Preserve
EVERY exact date, name, number, scheme, report, institution, rate, penalty, ranking
and place. Do not invent facts not in the text. Do not write MCQs. These are neutral
fact notes that several exams will reuse, so don't tailor them to one exam.

RAW CHUNK {index}/{total}:
{chunk}
"""


def _condense_source(
    canon: str,
    label: str,
    blocks: list[str],
    period_label: str,
    output_key: str | None,
) -> str:
    """Condense ONE source's blocks into neutral notes, cached by (output_key,
    canonical source) so every exam that uses this source reuses the work instead
    of re-summarising the same material. Returns the combined notes."""
    key = output_key or "period"
    cache_dir = CHUNK_NOTES_DIR / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    src_hash = _source_fingerprint(blocks)
    manifest = cache_dir / f"src-{canon}.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("hash") == src_hash and data.get("notes"):
                print(f"  Reusing shared condensed {canon.upper()} notes (already summarised).")
                return data["notes"]
        except Exception:  # noqa: BLE001
            pass

    chunks = _chunk_blocks(blocks, CHUNK_CONTENT_WORDS)
    print(f"  Condensing {canon.upper()} once for all exams: {len(chunks)} chunk(s) ...")
    notes: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        cpath = cache_dir / f"src-{canon}-chunk-{index:03d}.json"
        cached = _load_chunk_note(cpath, content_hash)
        if cached:
            print(f"    [{index}/{len(chunks)}] cached")
            notes.append(cached)
            continue
        print(f"    [{index}/{len(chunks)}] condensing ({len(chunk.split())} words)")
        prompt = _neutral_source_chunk_prompt(label, chunk, index, len(chunks), period_label)
        note = _truncate_to_words(clean_summary_markdown(call_ollama_with_fallback(prompt)), CHUNK_SUMMARY_WORDS)
        _save_chunk_note(cpath, content_hash, note)
        notes.append(note)

    combined = "\n\n".join(f"=== {label} notes (part {i}) ===\n{n}" for i, n in enumerate(notes, 1))
    manifest.write_text(json.dumps({"hash": src_hash, "notes": combined}, ensure_ascii=False), encoding="utf-8")
    return combined


def _assemble_exam_content(
    named_sources: list[tuple[str, str, list[str]]],
    period_label: str,
    output_key: str | None,
) -> str:
    """Build one exam's prompt content from its sources. A source that's large on its
    own is condensed (shared across exams via _condense_source); a small one is used
    raw. Only question generation then differs per exam — the heavy summarising of a
    shared source (e.g. all-ministry PIB) happens exactly once per week."""
    final_budget = _token_budget_words()
    parts: list[str] = []
    for canon, label, blocks in named_sources:
        if not blocks:
            continue
        words = sum(len(b.split()) for b in blocks)
        if words > CHUNK_CONTENT_WORDS:
            parts.append(_condense_source(canon, label, blocks, period_label, output_key))
        else:
            parts.append(f"=== {label} ===\n" + "\n\n".join(blocks))

    combined = "\n\n".join(parts)
    raw_words = len(combined.split())
    if raw_words > final_budget:
        combined = _truncate_to_words(combined, final_budget)
        print(f"  Assembled content: {len(combined.split())} words (truncated to budget {final_budget})")
    else:
        print(f"  Assembled content: {raw_words} words (budget: {final_budget})")
    return combined


def prepare_prompt_content(
    pib_releases: list[dict],
    rbi_items: list[dict],
    month_name: str,
    year: int | str | None,
    month: int | None,
    output_key: str | None = None,
) -> str:
    usable_pib_count = len(_usable_detail_items(pib_releases))
    usable_rbi_count = len(_usable_detail_items(rbi_items))
    skipped_count = (len(pib_releases) - usable_pib_count) + (len(rbi_items) - usable_rbi_count)
    print(
        f"  Using detail content from {usable_pib_count} PIB items and "
        f"{usable_rbi_count} RBI items"
    )
    if skipped_count:
        print(
            f"  Skipping {skipped_count} title-only/weak-content items "
            f"(<{MIN_DETAIL_CONTENT_WORDS} words)"
        )

    # Build PIB and RBI blocks separately so the (shared) PIB scrape is condensed
    # once per week and reused by every exam that draws on PIB.
    period_label = _period_display(month_name, year)
    pib_blocks = [_pib_block(r) for r in _usable_detail_items(pib_releases)]
    rbi_blocks = [_rbi_block(it) for it in _usable_detail_items(rbi_items)]
    return _assemble_exam_content(
        [
            ("pib", "PIB Press Releases", pib_blocks),
            ("rbi", "RBI Circulars & Press Releases", rbi_blocks),
        ],
        period_label,
        output_key,
    )


def split_response(response: str) -> tuple[str, str]:
    """Split Ollama response into (summary, questions) at PART 2."""
    marker_re = re.compile(r"PART\s*2", re.IGNORECASE)
    m = marker_re.search(response)
    if m:
        summary = response[: m.start()].strip()
        questions_raw = response[m.start():].strip()
    else:
        # Best effort: split at the first Q1.
        q1_m = re.search(r"\bQ1\.", response)
        if q1_m:
            summary = response[: q1_m.start()].strip()
            questions_raw = response[q1_m.start():].strip()
        else:
            summary = response
            questions_raw = ""
    return summary, questions_raw


def parse_questions(raw: str) -> list[dict]:
    """
    Parse the structured MCQ block into a list of dicts.

    FIX 2 — extended from A-D to A-E throughout:
      • question regex lookahead now includes E
      • option loop covers A B C D E
      • each option's lookahead uses the correct remaining letters
      • answer regex accepts A-E (and a-e)
    """
    # For each option letter, what comes next (used to stop the regex)
    NEXT_OPT_PATTERN = {
        "A": r"[B-E]\.",
        "B": r"[C-E]\.",
        "C": r"[D-E]\.",
        "D": r"E\.",
        "E": None,          # nothing after E except Answer:
    }

    questions = []
    blocks = re.split(r"(?=Q\d+\.)", raw)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # FIX 2a — lookahead covers A-E (was A-D)
        q_match = re.match(
            r"Q\d+\.\s*(.+?)(?=\n[A-E]\.|\Z)",
            block,
            re.DOTALL,
        )
        if not q_match:
            continue
        question_text = _clean_generated_text(q_match.group(1))

        options = []
        for letter in "ABCDE":                          # FIX 2b — was "ABCD"
            next_pat = NEXT_OPT_PATTERN[letter]
            if next_pat:
                pattern = rf"{letter}\.\s*(.+?)(?={next_pat}|\bAnswer:|\Z)"
            else:
                pattern = rf"{letter}\.\s*(.+?)(?=\bAnswer:|\Z)"

            opt_match = re.search(pattern, block, re.DOTALL)
            if opt_match:
                opt_text = _clean_generated_text(opt_match.group(1))
                options.append(f"{letter}. {opt_text}")

        # FIX 2c — answer regex accepts common variants and A-E.
        answer = ""
        ans_match = re.search(
            r"(?:Answer|Correct\s+Answer|Correct\s+Option)\s*[:\-]\s*(?:Option\s*)?([A-Ea-e])\b",
            block,
        )
        if ans_match:
            answer = ans_match.group(1).upper()

        if question_text and options:
            questions.append({
                "question": question_text,
                "options": options,
                "answer": answer,
            })

    return questions


def normalize_questions(questions: list[dict]) -> list[dict]:
    if len(questions) > EXPECTED_QUESTION_COUNT:
        print(
            f"  [WARN] Ollama generated {len(questions)} MCQs; "
            f"keeping the first {EXPECTED_QUESTION_COUNT}."
        )
        questions = questions[:EXPECTED_QUESTION_COUNT]

    missing_answer_indexes = [
        str(i)
        for i, question in enumerate(questions, 1)
        if not question.get("answer")
    ]
    if missing_answer_indexes:
        preview = ", ".join(missing_answer_indexes[:10])
        if len(missing_answer_indexes) > 10:
            preview += ", ..."
        raise RuntimeError(
            f"Generated MCQs are missing answer keys for {len(missing_answer_indexes)} "
            f"question(s): {preview}. Refusing to save incomplete question JSON."
        )

    if len(questions) < EXPECTED_QUESTION_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_QUESTION_COUNT} generated MCQs, got {len(questions)}. "
            "Refusing to save partial question JSON."
        )

    return questions


def build_missing_questions_prompt(
    content: str,
    summary: str,
    month_name: str,
    year: int | str | None,
    start_number: int,
    end_number: int,
) -> str:
    missing_count = end_number - start_number + 1
    period_label = _period_display(month_name, year)
    return f"""\
You previously generated only {start_number - 1} of {EXPECTED_QUESTION_COUNT} RBI Grade B Phase 1 General Awareness MCQs for {period_label}.

Generate ONLY the missing {missing_count} questions, numbered Q{start_number} through Q{end_number}.
Do not repeat earlier questions. Do not write a summary or any explanation.

Rules:
- Use exactly 5 options for every question: A, B, C, D, E.
- Every question MUST have exactly one answer line immediately after the options.
- Format every answer line exactly as: Answer: [letter]
- Do not use markdown bold/italic formatting. No ** markers.
- Base the questions only on the period summary and raw content below.

Output format:
Q{start_number}. [Question text]
A. [option]  B. [option]  C. [option]  D. [option]  E. [option]
Answer: [letter]

PERIOD SUMMARY:
{summary}

RAW CONTENT:
{content}
"""


def complete_missing_questions(
    questions: list[dict],
    content: str,
    summary: str,
    month_name: str,
    year: int | str | None,
    month: int | None,
    output_key: str | None = None,
) -> list[dict]:
    if len(questions) >= EXPECTED_QUESTION_COUNT:
        return questions

    start_number = len(questions) + 1
    print(
        f"  [WARN] Ollama generated {len(questions)} of {EXPECTED_QUESTION_COUNT} MCQs. "
        f"Requesting Q{start_number}-Q{EXPECTED_QUESTION_COUNT} only ..."
    )
    continuation_prompt = build_missing_questions_prompt(
        content,
        summary,
        month_name,
        year,
        start_number,
        EXPECTED_QUESTION_COUNT,
    )
    continuation = call_ollama_with_fallback(continuation_prompt)
    continuation_path = _save_raw_ollama_response(
        year, month, continuation, "continuation", output_key
    )
    print(f"  Raw Ollama continuation saved → {continuation_path}")

    extra_questions = parse_questions(continuation)
    needed = EXPECTED_QUESTION_COUNT - len(questions)
    if len(extra_questions) > needed:
        extra_questions = extra_questions[:needed]
    print(f"  Continuation parsed: {len(extra_questions)} additional MCQs")
    return questions + extra_questions


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _load_or_scrape_month(
    year: int,
    month: int,
    refresh_cache: bool = False,
) -> tuple[list[dict], list[dict]]:
    pib_releases = None if refresh_cache else _load_scrape_cache("pib", year, month)
    rbi_items = None if refresh_cache else _load_scrape_cache("rbi", year, month)
    if refresh_cache:
        print(f"  Refresh requested for {year}-{month:02d}; ignoring existing scrape cache.")

    missing_sources = []
    if pib_releases is None:
        missing_sources.append("pib")
    if rbi_items is None:
        missing_sources.append("rbi")

    if missing_sources:
        from scrapers.pib_scraper import scrape_pib
        from scrapers.rbi_scraper import scrape_rbi

        scraper_map = {
            "pib": scrape_pib,
            "rbi": scrape_rbi,
        }

        with ThreadPoolExecutor(max_workers=len(missing_sources)) as executor:
            future_to_source = {
                executor.submit(scraper_map[source], year, month): source
                for source in missing_sources
            }

            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    items = future.result()
                    _save_scrape_cache(source, year, month, items)
                except Exception as e:
                    print(f"  [ERROR] {source.upper()} scraper: {e}")
                    items = []

                if source == "pib":
                    pib_releases = items
                else:
                    rbi_items = items

    return pib_releases or [], rbi_items or []


def _load_weekly_source_from_month_cache(
    source: str,
    start_date: date,
    end_date: date,
) -> list[dict] | None:
    items = []
    for source_year, source_month in _months_in_range(start_date, end_date):
        path = _scrape_cache_path(source, source_year, source_month)
        if not path.exists():
            return None
        month_items = _load_scrape_cache(source, source_year, source_month)
        if month_items is None:
            return None
        items.extend(month_items)

    filtered = _filter_items_by_date(items, start_date, end_date)
    print(f"  Built weekly {source.upper()} data from existing monthly cache: {len(filtered)} items")
    return filtered


def _load_or_scrape_week(
    start_date: date,
    end_date: date,
    output_key: str,
    refresh_cache: bool = False,
) -> tuple[list[dict], list[dict]]:
    pib_releases = (
        _load_period_scrape_cache("pib", output_key)
        if _use_period_cache("pib", output_key, refresh_cache) else None
    )
    rbi_items = (
        _load_period_scrape_cache("rbi", output_key)
        if _use_period_cache("rbi", output_key, refresh_cache) else None
    )
    if refresh_cache and (pib_releases is None or rbi_items is None):
        print("  Refresh requested; ignoring existing weekly scrape cache.")

    if pib_releases is None and not refresh_cache:
        pib_releases = _load_weekly_source_from_month_cache("pib", start_date, end_date)
        if pib_releases is not None:
            _save_period_scrape_cache("pib", output_key, pib_releases)

    if rbi_items is None and not refresh_cache:
        rbi_items = _load_weekly_source_from_month_cache("rbi", start_date, end_date)
        if rbi_items is not None:
            _save_period_scrape_cache("rbi", output_key, rbi_items)

    if pib_releases is None or rbi_items is None:
        from scrapers.pib_scraper import scrape_pib_range
        from scrapers.rbi_scraper import scrape_rbi_range

        scraper_map = {
            "pib": scrape_pib_range,
            "rbi": scrape_rbi_range,
        }
        missing_sources = []
        if pib_releases is None:
            missing_sources.append("pib")
        if rbi_items is None:
            missing_sources.append("rbi")

        with ThreadPoolExecutor(max_workers=len(missing_sources)) as executor:
            future_to_source = {
                executor.submit(scraper_map[source], start_date, end_date): source
                for source in missing_sources
            }
            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    items = future.result()
                    _save_period_scrape_cache(source, output_key, items)
                except Exception as e:
                    print(f"  [ERROR] weekly {source.upper()} scraper: {e}")
                    items = []

                if source == "pib":
                    pib_releases = items
                else:
                    rbi_items = items

    return pib_releases or [], rbi_items or []


def _save_outputs(
    summary: str,
    questions: list[dict],
    period_name: str,
    year: int | str | None,
    month: int | None,
    output_key: str,
    generated_label: str,
    email_to: str | None,
) -> tuple[Path, Path, Path, Path]:
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    GEN_Q_DIR.mkdir(parents=True, exist_ok=True)

    period_label = _period_display(period_name, year)
    summary_path = SUMMARIES_DIR / f"{output_key}.md"
    summary_document = (
        f"# RBI Grade B GA Summary — {period_label}\n\n"
        f"_Generated on: {generated_label}_\n\n"
        f"{summary}"
    )
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_document)
    print(f"  Summary saved → {summary_path}")

    summary_pdf_path = render_summary_pdf(
        summary_document,
        period_name,
        year,
        month,
        output_key,
    )
    print(f"  Summary PDF saved → {summary_pdf_path}")

    questions_path = GEN_Q_DIR / f"{output_key}-qs.json"
    with open(questions_path, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2)
    print(f"  Questions saved → {questions_path} ({len(questions)} MCQs)")

    questions_pdf_path = render_questions_pdf(questions, period_name, year, month, output_key)
    print(f"  Questions PDF saved → {questions_pdf_path}")

    recipients = parse_recipients(email_to or "")
    if recipients:
        send_pdf_email([summary_pdf_path, questions_pdf_path], recipients, period_name, year)
        print(f"  Summary and questions PDFs emailed → {', '.join(recipients)}")

    return summary_path, summary_pdf_path, questions_path, questions_pdf_path


# ---------------------------------------------------------------------------
# Generic multi-exam weekly path (additive — the RBI pib+rbi path is unchanged)
# ---------------------------------------------------------------------------

def _exam_source_scrapers() -> dict:
    """Map source keys (from EXAMS[...]['sources']) to (range_scraper, label)."""
    from scrapers.pib_scraper import scrape_pib_range
    from scrapers.rbi_scraper import scrape_rbi_range
    from scrapers.econsurvey_scraper import scrape_econsurvey_range

    return {
        "pib": (scrape_pib_range, "PIB Press Releases"),
        "rbi": (scrape_rbi_range, "RBI Circulars & Press Releases"),
        # All-ministry PIB reuses the same scraper (it already queries every
        # ministry); kept as a distinct key so an exam can opt into broad PIB.
        "pib_all": (scrape_pib_range, "PIB Press Releases (all ministries)"),
        "econsurvey": (scrape_econsurvey_range, "Economic Survey / Yojana"),
        # News is not scraped here — it's read from the daily news ledger, filtered
        # to this exam + week (handled specially in _load_or_scrape_week_exam).
        "news": (None, "Exam-relevant news (this week)"),
    }


def _load_exam_news(exam_name: str, start_date: date, end_date: date) -> list[dict]:
    """This week's exam-tagged news, read from the news dedup ledger (already
    summarised by the news pipeline — no re-fetch, no LLM). Shaped like a scrape
    source so it flows through the normal content path: the in-house summary is the
    detail content."""
    ledger = BASE_DIR / "data" / "news" / "seen.json"
    if not ledger.exists():
        print("  News: no ledger yet (run pipeline/news_runner.py); skipping.")
        return []
    try:
        data = json.loads(ledger.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    items: list[dict] = []
    for v in (data.values() if isinstance(data, dict) else []):
        if not isinstance(v, dict):
            continue
        summary = (v.get("summary") or "").strip()
        if not summary or exam_name not in (v.get("exams") or []):
            continue
        raw = v.get("date")
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            d = None
        if d is None or not (start_date <= d <= end_date):
            continue
        items.append({
            "title": v.get("title", ""),
            "date": raw,
            "url": v.get("url", ""),
            "content": summary,
            "source": v.get("source", "News"),
        })
    return items


def _generic_block(item: dict, label: str) -> str:
    title = item.get("title") or item.get("subject") or ""
    parts = [f"[{label}] {item.get('date', '')} — {title}"]
    if item.get("department"):
        parts.append(f"  Department: {item['department']}")
    if item.get("url"):
        parts.append(f"  URL: {item['url']}")
    if item.get("content"):
        parts.append("  DETAIL CONTENT:")
        parts.append(item["content"])
    return "\n".join(parts)


def _load_or_scrape_week_exam(
    slug: str,
    sources: list[str],
    start_date: date,
    end_date: date,
    output_key: str,
    refresh_cache: bool,
) -> dict[str, list[dict]]:
    reg = _exam_source_scrapers()
    # Cache is exam-INDEPENDENT (keyed by canonical source + period), so a source
    # already scraped for another exam this week is reused, not fetched again.
    result: dict[str, list[dict]] = {}
    to_scrape: dict[str, object] = {}

    from config import EXAMS

    for src in sources:
        if src not in reg:
            print(f"  [WARN] Unknown source '{src}' for {slug}; skipping.")
            continue
        if src == "news":
            news = _load_exam_news(EXAMS[slug]["name"], start_date, end_date)
            print(f"  News: {len(news)} exam-relevant item(s) this week (from ledger, no re-fetch).")
            result[src] = news
            continue
        items = (
            _load_period_scrape_cache(src, output_key)
            if _use_period_cache(src, output_key, refresh_cache) else None
        )
        if items is None:
            to_scrape[src] = reg[src][0]
        else:
            print(f"  Reusing shared {_canonical_source(src)} scrape for {src}.")
            result[src] = items

    if to_scrape:
        with ThreadPoolExecutor(max_workers=len(to_scrape)) as executor:
            future_to_source = {
                executor.submit(fn, start_date, end_date): src
                for src, fn in to_scrape.items()
            }
            for future in as_completed(future_to_source):
                src = future_to_source[future]
                try:
                    items = future.result()
                    if items:
                        _save_period_scrape_cache(src, output_key, items)
                except Exception as e:  # noqa: BLE001
                    print(f"  [ERROR] weekly {src} scraper: {e}")
                    items = []
                result[src] = items or []

    return {src: result.get(src, []) for src in sources if src in reg}


def _prepare_prompt_content_exam(
    exam_cfg: dict,
    source_items: dict[str, list[dict]],
    period_name: str,
    output_key: str,
) -> str:
    reg = _exam_source_scrapers()
    # Build blocks per canonical source using the SAME formatter every exam uses, so
    # a shared source (e.g. all-ministry PIB) is condensed once and reused across
    # exams — only the final summary + questions differ per exam.
    named: list[tuple[str, str, list[str]]] = []
    usable_total = 0
    for src, items in source_items.items():
        canon = _canonical_source(src)
        usable = _usable_detail_items(items)
        usable_total += len(usable)
        print(f"  {reg[src][1]}: {len(usable)}/{len(items)} usable detail items")
        blocks = [_format_block_for(canon, it) for it in usable]
        named.append((canon, reg[src][1], blocks))

    if usable_total == 0:
        raise RuntimeError(
            f"No usable detail content from any source ({', '.join(source_items)}) "
            f"for {period_name}. Nothing to summarise."
        )

    return _assemble_exam_content(named, period_name, output_key)


def _save_outputs_exam(
    exam_cfg: dict,
    summary: str,
    questions: list[dict],
    period_name: str,
    output_key: str,
    generated_label: str,
) -> tuple[Path, Path]:
    slug = exam_cfg["slug"]
    name = exam_cfg["name"]
    summaries_dir = SUMMARIES_DIR / slug
    gen_q_dir = GEN_Q_DIR / slug
    summaries_dir.mkdir(parents=True, exist_ok=True)
    gen_q_dir.mkdir(parents=True, exist_ok=True)

    summary_path = summaries_dir / f"{output_key}.md"
    summary_path.write_text(
        f"# {name} GA Summary — {period_name}\n\n"
        f"_Generated on: {generated_label}_\n\n{summary}",
        encoding="utf-8",
    )
    print(f"  Summary saved → {summary_path}")

    questions_path = gen_q_dir / f"{output_key}-qs.json"
    questions_path.write_text(json.dumps(questions, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Questions saved → {questions_path} ({len(questions)} MCQs)")
    return summary_path, questions_path


def _run_week_exam(
    week: int,
    exam: str,
    refresh_cache: bool = False,
) -> None:
    from config import EXAMS

    cfg = EXAMS[exam]
    start_date, end_date = get_week_period(week)
    period_name = f"Week {week} ({start_date:%d %b %Y} - {end_date:%d %b %Y})"
    output_key = _period_key(start_date, end_date)

    print("=" * 60)
    print(f"Weekly Runner [{cfg['name']}] — Week {week} → {start_date:%d %b %Y} to {end_date:%d %b %Y}")
    print(f"  Sources: {', '.join(cfg['sources'])}")
    print("=" * 60)
    t_start = time.time()

    print("\n[Step 1] Loading/scraping exam sources ...")
    source_items = _load_or_scrape_week_exam(
        cfg["slug"], cfg["sources"], start_date, end_date, output_key, refresh_cache
    )

    print("\n[Step 2] Preparing prompt content ...")
    combined = _prepare_prompt_content_exam(cfg, source_items, period_name, output_key)

    print("\n[Step 3] Building prompt & calling Ollama ...")
    t3 = time.time()
    # Weekly papers are built from the week's current-affairs material ONLY.
    # Static sources (Economic Survey — yearly) are intentionally kept separate:
    # they get their own stored summary + dedicated quiz (see pipeline/
    # static_runner.py), so weekly questions never mix with foundational ES facts.
    prompt = build_prompt(combined, period_name, exam=exam)
    response = call_ollama_with_fallback(prompt)
    print(f"  Ollama response: {len(response)} chars  ({time.time()-t3:.1f}s)")
    raw_key = f"{cfg['slug']}-{output_key}"
    _save_raw_ollama_response(None, None, response, output_key=raw_key)

    print("\n[Step 4] Parsing response ...")
    summary, questions_raw = split_response(response)
    summary = clean_summary_markdown(summary)
    questions = parse_questions(questions_raw)
    questions = complete_missing_questions(
        questions, combined, summary, period_name, None, None, output_key=raw_key
    )
    questions = normalize_questions(questions)
    print(f"  Summary: {len(summary)} chars | Questions parsed: {len(questions)}")

    # Fold a few fresh Economic Survey questions into the weekly paper (only for exams
    # that have an ES reference source). Random sections, distinct from the stored ES
    # quiz, grouped under their own heading on the page.
    from config import WEEKLY_REFERENCE_QUESTIONS
    if WEEKLY_REFERENCE_QUESTIONS:
        try:
            from pipeline.static_runner import weekly_reference_questions
            ref_qs = weekly_reference_questions(exam, WEEKLY_REFERENCE_QUESTIONS)
            if ref_qs:
                print(f"  + {len(ref_qs)} Economic Survey question(s) folded in (random sections).")
                questions = questions + ref_qs
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] Economic Survey weekly questions skipped: {e}")

    print("\n[Step 5] Saving outputs ...")
    _save_outputs_exam(cfg, summary, questions, period_name, output_key, f"Week {week} pipeline")

    print(f"\n{'='*60}")
    print(f"[{cfg['name']}] Week {week} complete in {time.time()-t_start:.1f}s")
    print(f"  Questions gen  : {len(questions)}")
    print(f"{'='*60}")


def run_day(day: int, refresh_cache: bool = False, email_to: str | None = None) -> None:
    if day not in DAY_MAP:
        print(f"[ERROR] Day {day} not in DAY_MAP (valid: {sorted(DAY_MAP)}).")
        sys.exit(1)

    year, month = DAY_MAP[day]
    month_name = MONTH_NAMES[month]
    print("=" * 60)
    print(f"Daily Runner — Day {day} → {month_name} {year}")
    print("=" * 60)

    t_start = time.time()

    # Step 1: Load cached scrape results, then scrape only missing sources
    print("\n[Step 1] Loading scrape cache / scraping missing sources ...")
    t1 = time.time()

    pib_releases = None if refresh_cache else _load_scrape_cache("pib", year, month)
    rbi_items = None if refresh_cache else _load_scrape_cache("rbi", year, month)
    if refresh_cache:
        print("  Refresh requested; ignoring existing scrape cache.")

    missing_sources = []
    if pib_releases is None:
        missing_sources.append("pib")
    if rbi_items is None:
        missing_sources.append("rbi")

    if missing_sources:
        # Lazy imports avoid loading scraper dependencies when cache is complete.
        from scrapers.pib_scraper import scrape_pib
        from scrapers.rbi_scraper import scrape_rbi

        scraper_map = {
            "pib": scrape_pib,
            "rbi": scrape_rbi,
        }

        with ThreadPoolExecutor(max_workers=len(missing_sources)) as executor:
            future_to_source = {
                executor.submit(scraper_map[source], year, month): source
                for source in missing_sources
            }

            for future in as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    items = future.result()
                    _save_scrape_cache(source, year, month, items)
                except Exception as e:
                    print(f"  [ERROR] {source.upper()} scraper: {e}")
                    items = []

                if source == "pib":
                    pib_releases = items
                else:
                    rbi_items = items

    pib_releases = pib_releases or []
    rbi_items = rbi_items or []

    print(f"  PIB: {len(pib_releases)} releases | RBI: {len(rbi_items)} items  ({time.time()-t1:.1f}s)")

    # Step 2: Validate and merge detail-page content
    print("\n[Step 2] Preparing prompt content ...")
    if not pib_releases and not rbi_items:
        raise RuntimeError(f"No PIB/RBI items found for {month_name} {year}.")
    _ensure_some_usable_content(
        [("pib", pib_releases), ("rbi", rbi_items)], f"{month_name} {year}"
    )
    combined = prepare_prompt_content(pib_releases, rbi_items, month_name, year, month)

    # Step 3: Build prompt then call Ollama
    print("\n[Step 3] Building prompt & calling Ollama ...")
    t3 = time.time()

    # FIX 3 — build_prompt called HERE where content/month_name/year all exist.
    #          No .format() needed — build_prompt returns the complete ready string.
    prompt = build_prompt(combined, month_name, year)

    response = call_ollama_with_fallback(prompt)
    print(f"  Ollama response: {len(response)} chars  ({time.time()-t3:.1f}s)")
    raw_response_path = _save_raw_ollama_response(year, month, response)
    print(f"  Raw Ollama response saved → {raw_response_path}")

    # Step 4: Split response
    print("\n[Step 4] Parsing response ...")
    summary, questions_raw = split_response(response)
    summary = clean_summary_markdown(summary)
    questions = parse_questions(questions_raw)
    questions = complete_missing_questions(questions, combined, summary, month_name, year, month)
    questions = normalize_questions(questions)
    print(f"  Summary: {len(summary)} chars | Questions parsed: {len(questions)}")

    # Step 5: Save outputs
    print("\n[Step 5] Saving outputs ...")
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    GEN_Q_DIR.mkdir(parents=True, exist_ok=True)

    summary_path = SUMMARIES_DIR / f"{year}-{month:02d}.md"
    with open(summary_path, "w") as f:
        f.write(f"# RBI Grade B GA Summary — {month_name} {year}\n\n")
        f.write(f"_Generated on: Day {day} of pipeline_\n\n")
        f.write(summary)
    print(f"  Summary saved → {summary_path}")
    summary_pdf_path = render_summary_pdf(
        f"# RBI Grade B GA Summary — {month_name} {year}\n\n"
        f"_Generated on: Day {day} of pipeline_\n\n"
        f"{summary}",
        month_name,
        year,
        month,
    )
    print(f"  Summary PDF saved → {summary_pdf_path}")

    questions_path = GEN_Q_DIR / f"{year}-{month:02d}-qs.json"
    with open(questions_path, "w") as f:
        json.dump(questions, f, indent=2)
    print(f"  Questions saved → {questions_path} ({len(questions)} MCQs)")

    questions_pdf_path = render_questions_pdf(questions, month_name, year, month)
    print(f"  Questions PDF saved → {questions_pdf_path}")

    recipients = parse_recipients(email_to or "")
    if recipients:
        send_pdf_email([summary_pdf_path, questions_pdf_path], recipients, month_name, year)
        print(f"  Summary and questions PDFs emailed → {', '.join(recipients)}")

    t_total = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"Day {day} complete in {t_total:.1f}s | {month_name} {year}")
    print(f"  PIB releases   : {len(pib_releases)}")
    print(f"  RBI items      : {len(rbi_items)}")
    print(f"  Questions gen  : {len(questions)}")
    print(f"  Summary PDF    : {summary_pdf_path}")
    print(f"  Questions PDF  : {questions_pdf_path}")
    print(f"{'='*60}")


def run_week(
    week: int,
    refresh_cache: bool = False,
    email_to: str | None = None,
    exam: str = DEFAULT_EXAM,
) -> None:
    # Non-default exams use the additive generic source path; the default exam
    # (RBI Grade B) keeps the original, proven PIB+RBI path below unchanged.
    if exam != DEFAULT_EXAM:
        _run_week_exam(week, exam, refresh_cache=refresh_cache)
        return

    start_date, end_date = get_week_period(week)
    period_name = f"Week {week} ({start_date:%d %b %Y} - {end_date:%d %b %Y})"
    output_key = _period_key(start_date, end_date)

    print("=" * 60)
    print(f"Weekly Runner — Week {week} → {start_date:%d %b %Y} to {end_date:%d %b %Y}")
    print("=" * 60)

    t_start = time.time()

    print("\n[Step 1] Loading weekly scrape cache / scraping missing week ...")
    t1 = time.time()

    pib_releases, rbi_items = _load_or_scrape_week(
        start_date,
        end_date,
        output_key,
        refresh_cache,
    )

    print(
        f"  PIB: {len(pib_releases)} weekly releases | "
        f"RBI: {len(rbi_items)} weekly items  ({time.time()-t1:.1f}s)"
    )

    print("\n[Step 2] Preparing weekly prompt content ...")
    if not pib_releases and not rbi_items:
        raise RuntimeError(
            f"No PIB/RBI items found for Week {week} "
            f"({start_date:%Y-%m-%d} to {end_date:%Y-%m-%d})."
        )
    # Generate from whatever source(s) have content this week — a thin PIB week
    # still produces a paper from RBI material, and vice versa.
    _ensure_some_usable_content(
        [("pib", pib_releases), ("rbi", rbi_items)], f"Week {week}"
    )
    combined = prepare_prompt_content(
        pib_releases,
        rbi_items,
        period_name,
        None,
        None,
        output_key,
    )

    print("\n[Step 3] Building prompt & calling Ollama ...")
    t3 = time.time()
    prompt = build_prompt(combined, period_name)

    response = call_ollama_with_fallback(prompt)
    print(f"  Ollama response: {len(response)} chars  ({time.time()-t3:.1f}s)")
    raw_response_path = _save_raw_ollama_response(None, None, response, output_key=output_key)
    print(f"  Raw Ollama response saved → {raw_response_path}")

    print("\n[Step 4] Parsing response ...")
    summary, questions_raw = split_response(response)
    summary = clean_summary_markdown(summary)
    questions = parse_questions(questions_raw)
    questions = complete_missing_questions(
        questions,
        combined,
        summary,
        period_name,
        None,
        None,
        output_key,
    )
    questions = normalize_questions(questions)
    print(f"  Summary: {len(summary)} chars | Questions parsed: {len(questions)}")

    print("\n[Step 5] Saving outputs ...")
    _summary_path, summary_pdf_path, _questions_path, questions_pdf_path = _save_outputs(
        summary,
        questions,
        period_name,
        None,
        None,
        output_key,
        f"Week {week} pipeline",
        email_to,
    )

    t_total = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"Week {week} complete in {t_total:.1f}s | {start_date:%d %b %Y} to {end_date:%d %b %Y}")
    print(f"  PIB releases   : {len(pib_releases)}")
    print(f"  RBI items      : {len(rbi_items)}")
    print(f"  Questions gen  : {len(questions)}")
    print(f"  Summary PDF    : {summary_pdf_path}")
    print(f"  Questions PDF  : {questions_pdf_path}")
    print(f"{'='*60}")


def run_week_with_retry(
    week: int,
    exam: str = DEFAULT_EXAM,
    refresh_cache: bool = False,
    email_to: str | None = None,
    retries: int = 3,
    base_delay: float = 5.0,
) -> bool:
    """Run one exam's week, retrying transient failures a few times.

    Returns True on success, False if it still fails after ``retries`` attempts
    (so a caller running the whole pipeline can log it and move on).
    """
    from config import EXAMS

    name = EXAMS.get(exam, {}).get("name", exam)
    for attempt in range(1, retries + 1):
        try:
            run_week(week, refresh_cache=refresh_cache, email_to=email_to, exam=exam)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  [ERROR] {name} Week {week} attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                delay = base_delay * attempt
                print(f"  Retrying in {delay:.0f}s ...")
                time.sleep(delay)
    print(f"  [FAIL] {name} Week {week}: giving up after {retries} attempts; moving on.")
    return False


def run_week_all_exams(
    week: int, refresh_cache: bool = False, retries: int = 3
) -> dict[str, bool]:
    """Run the weekly pipeline for EVERY active exam, independently.

    Each exam is isolated: a failure (after retries) in one exam does not stop the
    others. Returns {exam_slug: succeeded}.
    """
    from config import active_exams

    results: dict[str, bool] = {}
    exams = active_exams()
    print(f"\n{'#' * 60}")
    print(f"# Weekly pipeline — Week {week} — {len(exams)} active exam(s)")
    print(f"{'#' * 60}")
    for slug, cfg in exams.items():
        print(f"\n----- {cfg['name']} ({slug}) -----")
        results[slug] = run_week_with_retry(
            week, exam=slug, refresh_cache=refresh_cache, retries=retries
        )

    ok = [s for s, v in results.items() if v]
    bad = [s for s, v in results.items() if not v]
    print(f"\n=== Week {week} pipeline summary: {len(ok)} succeeded, {len(bad)} failed ===")
    if bad:
        print(f"  Failed (skipped this run): {', '.join(bad)}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Daily pipeline runner for RBI Grade B prep"
    )
    period_group = parser.add_mutually_exclusive_group(required=True)
    period_group.add_argument("--day", type=int, help="Day number from DAY_MAP for monthly runs")
    period_group.add_argument("--week", type=int, help="Week number for weekly runs")
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore cached PIB/RBI scrape data and scrape source month(s) again",
    )
    parser.add_argument(
        "--email-to",
        default=os.getenv("QUESTIONS_EMAIL_TO", ""),
        help="Email generated PDFs to one or more comma-separated addresses",
    )
    parser.add_argument(
        "--email-existing",
        action="store_true",
        help="Email already-generated summary/question PDFs without running the pipeline",
    )
    parser.add_argument(
        "--exam",
        default=DEFAULT_EXAM,
        choices=list(EXAMS.keys()),
        help="Which exam to run (weekly mode only). Default: %(default)s",
    )
    parser.add_argument(
        "--all-exams",
        action="store_true",
        help="Weekly mode: run EVERY active exam for the week, with retry + skip-on-failure",
    )
    args = parser.parse_args()
    if args.exam != DEFAULT_EXAM and args.week is None:
        parser.error("--exam is only supported with --week (weekly mode).")
    if args.all_exams and args.week is None:
        parser.error("--all-exams is only supported with --week (weekly mode).")
    if args.email_existing:
        if args.week is not None:
            email_existing_week_pdfs(args.week, args.email_to)
        else:
            email_existing_pdfs(args.day, args.email_to)
    elif args.week is not None:
        if args.all_exams:
            results = run_week_all_exams(args.week, refresh_cache=args.refresh_cache)
            # Non-zero exit only if EVERY exam failed (lets a scheduler distinguish
            # "nothing generated" from "some generated").
            sys.exit(0 if any(results.values()) else 1)
        run_week(
            args.week,
            refresh_cache=args.refresh_cache,
            email_to=args.email_to or None,
            exam=args.exam,
        )
    else:
        run_day(args.day, refresh_cache=args.refresh_cache, email_to=args.email_to or None)
