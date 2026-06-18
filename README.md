# RBI Grade B Phase 1 — GA Prep Tool

Automated scraper and AI study assistant for RBI Grade B Phase 1 General Awareness.

**Live site:** https://votrascii.github.io/grade-b-prep/

## What it does

1. **Scrapes past GA questions** (2023–2025) from EduTap, AffairsCloud, and Oliveboard
2. **Scrapes PIB press releases** and **RBI circulars** with local caching
3. **Generates AI summaries + practice MCQs** for weekly or monthly periods using Ollama
4. **Schedules itself** to process one completed week every 6 hours
5. **Publishes a minimalist weekly website** ([live here](https://votrascii.github.io/grade-b-prep/)) with descriptive summaries and an in-browser practice quiz
6. **Curates exam-relevant news** from ET / Mint / Hindustan Times (via RSS), tagged by exam and cited back to the source

## Setup

### 1. Install Ollama and pull the model

```bash
# Install from https://ollama.com
ollama pull qwen3.5:9b
ollama pull gemma3:12b
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browser

```bash
playwright install chromium
```

## Usage

### Step 1 — Scrape past GA questions (do this once)

```bash
python scrapers/question_scraper.py
```

This will:
- Download the EduTap GA PDF and extract MCQs
- Fall back to web scraping if the PDF yields fewer than 30 questions
- Scrape AffairsCloud and Oliveboard as additional sources
- Deduplicate and save everything to `data/questions/all_ga.json`

### Step 2 — Run the pipeline

**Auto mode** (runs the current weekly slot immediately, then schedules every 6 hours):

```bash
python run.py
```

**Manual weekly mode**:

```bash
python pipeline/daily_runner.py --week 1
```

**Backfill missing weekly outputs from available cached content**:

Use this when the strict weekly runner skipped a week because one source did
not have enough detail coverage, but there are still usable PIB/RBI detail
items in `data/scraped/`.

```bash
python scripts/backfill_available_weeks.py --all-missing
python scripts/backfill_available_weeks.py --week 12 --week 16
python scripts/backfill_available_weeks.py --all-missing --dry-run
```

The backfill script writes to the same weekly summary/question paths, but it
filters out title-only or weak-content items and asks the model to generate
questions only from the facts available in the remaining detail content.

**Manual monthly mode**:

```bash
python pipeline/daily_runner.py --day 1
```

**Force-run the scheduler's current weekly slot immediately**:

```bash
python run.py --run-now
```

## Weekly Range

Weekly runs start at **December 1, 2025** and continue **open-ended** in 7-day
blocks — a new week is published every week with no end date.

| Week | Date range |
|------|------------|
| 1 | 2025-12-01 to 2025-12-07 |
| 2 | 2025-12-08 to 2025-12-14 |
| ... | ... |
| N | rolling — the most recent fully-completed week |

This is controlled by `WEEK_RANGE_START` / `WEEK_RANGE_END` in `config.py`. Leave
`WEEK_RANGE_END = ""` (the default) for the open-ended rolling schedule; set it to
a date (e.g. `"2026-05-31"`) to cap the schedule at a fixed final week.

The scheduler only advances after a week is complete. When it has caught up to the
most recent completed week it simply waits for the next 7-day block to elapse, then
processes and publishes it on the next 6-hour scheduler tick.

## Day → Month Mapping

| Day | Month      |
|-----|------------|
| 1   | May 2025   |
| 2   | June 2025  |
| 3   | July 2025  |
| 4   | Aug 2025   |
| 5   | Sep 2025   |
| 6   | Oct 2025   |
| 7   | Nov 2025   |
| 8   | Dec 2025   |
| 9   | Jan 2026   |
| 10  | Feb 2026   |
| 11  | Mar 2026   |
| 12  | Apr 2026   |
| 13  | May 2026   |

## Output files

| Path | Contents |
|------|----------|
| `data/questions/all_ga.json` | All scraped GA MCQs (deduplicated) |
| `data/questions/raw/` | Raw per-source question JSONs |
| `data/questions/generated/YYYY-MM-DD_to_YYYY-MM-DD-qs.json` | AI-generated weekly MCQs |
| `data/questions/pdf/YYYY-MM-DD_to_YYYY-MM-DD-qs.pdf` | Shareable weekly MCQ PDF with answer key |
| `data/summaries/YYYY-MM-DD_to_YYYY-MM-DD.md` | Weekly GA summary in markdown |
| `data/summaries/pdf/YYYY-MM-DD_to_YYYY-MM-DD-summary.pdf` | Shareable weekly summary PDF |
| `data/questions/generated/YYYY-MM-qs.json` | AI-generated monthly MCQs when using `--day` |
| `data/questions/pdf/YYYY-MM-qs.pdf` | Shareable monthly MCQ PDF with answer key |
| `data/summaries/YYYY-MM.md` | Monthly GA summary in markdown |
| `data/summaries/pdf/YYYY-MM-summary.pdf` | Shareable monthly summary PDF |
| `data/scraped/YYYY-MM/pib.json` | Cached PIB scrape data for the month |
| `data/scraped/YYYY-MM/rbi.json` | Cached RBI scrape data for the month |
| `data/chunk_notes/<period-key>/chunk-NNN.json` | Cached condensed notes for oversized periods |
| `data/raw/edutap_ga.pdf` | Downloaded EduTap GA PDF |
| `data/state.json` | Scheduler state (current day) |

The daily runner checks `data/scraped/YYYY-MM/` before scraping. If cached PIB/RBI data exists, it skips that scraper and goes straight to prompt building and Ollama. To force a fresh scrape:

```bash
python pipeline/daily_runner.py --week 1 --refresh-cache
```

If the scraped period is too large for the model context, the runner automatically splits the source material into chunks, summarizes each chunk into exam-focused notes, caches those notes in `data/chunk_notes/`, and builds the final summary/MCQ prompt from the condensed notes. This lets large periods use the whole scrape without silently truncating the tail.

To email the generated summary and question PDFs after a run, copy `.env.example` to `.env` and fill in your SMTP credentials:

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_16_character_gmail_app_password
SMTP_FROM=your_email@gmail.com
QUESTIONS_EMAIL_TO=recipient@example.com
```

Run normally to use `QUESTIONS_EMAIL_TO`, or override recipients with `--email-to`. Use commas for multiple recipients. The email includes both PDFs as attachments:

```bash
python pipeline/daily_runner.py --week 1
python pipeline/daily_runner.py --week 1 --email-to "one@example.com,two@example.com"
```

To email already-generated PDFs without rerunning scraping or Ollama:

```bash
python pipeline/daily_runner.py --week 1 --email-existing
python pipeline/daily_runner.py --week 1 --email-existing --email-to "one@example.com,two@example.com"
```

## News pipeline (In the news)

A second, parallel pipeline ingests **exam-relevant business/economy news** and
publishes it as an "In the news" page on the site, with cited sources and per-exam
relevance tags.

- **Sources:** Economic Times, Mint, Hindustan Times — read via their public **RSS
  feeds**. Business Standard is excluded (it blocks automated access and is hard
  paywalled).
- **What is stored:** only RSS metadata — headline, source, date, and a short blurb.
  Full copyrighted article bodies are **never** scraped or republished.
- **Self-contained summaries:** Ollama rewrites each item into an original 2–3
  sentence, exam-focused summary shown inline on the site (no outbound links) and
  credited to the originating outlet. Without Ollama, the RSS blurb is used as-is.
- **Exam tagging:** each item is screened for relevance to **RBI Grade B, SEBI
  Grade A, NABARD Grade A, and UPSC / Banking**. A keyword heuristic provides the
  baseline; Ollama refines the tags and topic when reachable.

Run it:

```bash
python pipeline/news_runner.py            # fetch, tag (LLM if available), save
python pipeline/news_runner.py --no-llm   # heuristic tagging only (fast, no Ollama)
python pipeline/news_runner.py --days 14  # widen the lookback window
```

Output:

| Path | Contents |
|------|----------|
| `data/news/latest.json` | Current digest consumed by the site build |
| `data/news/YYYY-MM-DD.json` | Dated snapshot for history |

The site build renders `data/news/latest.json` into `docs/news.html` (filterable by
exam). `run.py` refreshes the news digest automatically after each weekly cycle; use
`python run.py --build-only --with-news` to refresh it on demand. Configure feeds,
exams, and lookback in `config.py` (`NEWS_FEEDS`, `NEWS_EXAMS`, `NEWS_LOOKBACK_DAYS`).

## Website

The weekly summaries and MCQs are published as a minimalist static site in `docs/`,
served by GitHub Pages. The site reads directly from `data/summaries/*_to_*.md` and
`data/questions/generated/*-qs.json` — no database, no framework, stdlib Python only.

Build it from existing data:

```bash
python scripts/build_site.py
# or, equivalently, via the scheduler entrypoint:
python run.py --build-only
```

This writes a fast, fully-static site to `docs/`:

| Path | Contents |
|------|----------|
| `docs/index.html` | Landing page + chronological index of all weeks (newest first) |
| `docs/weeks/<key>.html` | Per-week page: descriptive summary + interactive MCQ quiz |
| `docs/assets/style.css`, `docs/assets/app.js` | Minimalist styling + quiz logic |

Each week page renders the summary with topic sections, highlighted figures/dates,
and ⭐ priority markers, followed by an in-browser practice quiz (pick an option to
see the correct answer, with a running score).

### Automatic publishing

`run.py` rebuilds the site after every completed week. Pass `--publish` to also
commit and push the regenerated `docs/` so the live site updates each week:

```bash
python run.py --publish            # auto-scheduled: process + rebuild + push each week
python run.py --run-now --publish  # process the current week now, then push
```

Raw scraped content in `data/` stays gitignored (private); only the rendered `docs/`
site is committed.

### Hosting (GitHub Pages)

The site is already live at **https://votrascii.github.io/grade-b-prep/**, served by
GitHub Pages from `docs/`. The included `.github/workflows/pages.yml` redeploys it
automatically on every push that touches `docs/` (typically `run.py --publish`).

Pages is configured with **Source: GitHub Actions** (`build_type: workflow`). This
was a one-time setup; you only need to redo it if you recreate the repo:

- **Public repo:** Pages is free. Enable it via **Settings → Pages → Build and
  deployment → Source: GitHub Actions**, or once with the API:
  `gh api -X POST /repos/<user>/<repo>/pages -f build_type=workflow`.
- **Private repo:** GitHub Pages requires a paid plan (GitHub Pro). Either upgrade,
  make the repo public, or deploy `docs/` to an external static host
  (Cloudflare Pages / Netlify support private repos for free — set the output
  directory to `docs/` with no build command).

> Note: the Actions token cannot *create* a Pages site (it returns 403), so Pages
> must be enabled by a repo admin once before the workflow can deploy. After that
> the workflow just publishes the existing site.

## Configuration

Edit `config.py` to change:
- Ollama URL / model names
- Ollama model fallback order, context window, and read timeout
- Large-period chunking via `CHUNK_CONTENT_WORDS` and `CHUNK_SUMMARY_WORDS`
- Weekly range via `WEEK_RANGE_START` and `WEEK_RANGE_END`
- Scheduler frequency via `SCHEDULER_INTERVAL_HOURS`
- Source URLs
- Fuzzy dedup threshold
- Max content word limit for Ollama prompt

You can also override the model order without editing files:

```bash
OLLAMA_MODELS="llama3.1:latest,qwen3.5:9b" python pipeline/daily_runner.py --day 1
```
