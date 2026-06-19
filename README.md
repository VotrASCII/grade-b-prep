# Govt Exams — GA Prep Tool

Automated scraper and AI study assistant for the General Awareness papers of
India's top government exams. Built first for **RBI Grade B Phase 1**, now being
generalised to a **multi-exam** platform (UPSC / Banking next, then SEBI Grade A
and NABARD Grade A).

**Live site:** https://votrascii.github.io/govt-exam-prep/

## What it does

1. **Scrapes past GA questions** (2023–2025) from EduTap, AffairsCloud, and Oliveboard
2. **Scrapes each exam's own sources** — PIB press releases and RBI circulars for RBI
   Grade B; broad all-ministry PIB and the Economic Survey for UPSC / Banking — with
   local caching
3. **Generates AI summaries + practice MCQs** for weekly or monthly periods using Ollama,
   with the topic and question-type mix tuned per exam (see *Multi-exam architecture*)
4. **Schedules itself** to process one completed week every 6 hours
5. **Publishes a minimalist website** ([live here](https://votrascii.github.io/govt-exam-prep/))
   **categorised by exam**, each with descriptive summaries and an in-browser practice quiz
6. **Curates exam-relevant news** from ET / Mint / Hindustan Times (via RSS), tagged by
   exam and shown as self-contained summaries (rolling 2-day window)

## Multi-exam architecture

Every exam is declared once in `config.py` under the `EXAMS` registry — its display
name, the **sources** it draws on, its **taxonomy** file, and an `active` flag. The
pipeline, prompt builder, and website all read this registry, so adding an exam means
appending an entry (plus its scraper and taxonomy) rather than editing the pipeline.

| Exam | Status | Sources | Taxonomy |
|------|--------|---------|----------|
| RBI Grade B | **Active** | PIB + RBI circulars | `data/patterns/rbi-grade-b.json` |
| UPSC / Banking | **Active (content in progress)** | all-ministry PIB + Economic Survey/Yojana | `data/patterns/upsc-banking.json` |
| SEBI Grade A | Scaffolded | SEBI + PIB + RBI | `data/patterns/sebi-grade-a.json` |
| NABARD Grade A | Scaffolded | NABARD + PIB + RBI | `data/patterns/nabard-grade-a.json` |

**Per-exam GA weightage (#question pattern).** Each taxonomy carries a `prompt_profile`
that drives the summary sections and the **topic + question-style distribution** for
that exam. RBI Grade B uses 5 options (A–E) and is current-affairs/banking heavy; UPSC
uses 4 options (A–D) and is dominated by "Consider the following statements" items. The
weightage is derived empirically from each exam's previous-year GA papers:

```bash
# Tally PYQ topics → recompute the exam's topic distribution in its taxonomy
python scripts/derive_weightage.py --exam rbi-grade-b --dry-run
python scripts/derive_weightage.py --exam upsc-banking
```

Drop an exam's previous-year GA papers (as `{"question","options",...}` JSON) into
`data/questions/pyq/<exam-slug>/` and re-run `derive_weightage.py` to refresh its mix.
Until that is done, an exam uses the documented research-default weightage in its taxonomy.

### Static sources — Economic Survey (yearly) & Yojana (monthly)

The Economic Survey is processed **once a year** and Yojana **once a month**, but
questions are generated **weekly**. To fold this material into weekly papers *without
repeating facts and while respecting GA weightage*, each static source is split into
topic-tagged **segments**, and a **rotation ledger** hands each segment to exactly one
week — so a fact is asked at most once.

```bash
# Build a stored, segmented summary (Economic Survey / Yojana are mostly PDF/JS,
# so pass extracted text with --from-file; scraping is attempted otherwise)
python pipeline/static_runner.py --exam upsc-banking --economic-survey 2025 \
    --from-file data/static/upsc-banking/sources/econsurvey-2025.txt
python pipeline/static_runner.py --exam upsc-banking --yojana 2026-06 \
    --from-file data/static/upsc-banking/sources/yojana-2026-06.txt
```

Output is stored under `data/static/<exam>/` (a section-wise `.md` summary + a `.json`
of segments). On each weekly run, `daily_runner` calls `select_for_week()` to claim the
next unused segments (preferring the current month's Yojana), marks them consumed in
`data/static/<exam>/rotation.json`, and the weekly prompt builds a fixed quota
(`DEFAULT_WEEKLY_STATIC_QUOTA`, default 6) of MCQs from them — folded **into** the topic
distribution (they replace current-affairs questions in the same topics, not add to
them). Re-running a week is idempotent (it keeps its already-assigned segments).

### Study cycles & archive

The site shows one **study cycle** at a time. A cycle starts on the **last Monday of
December** (29 Dec 2025 = Week 1 of the 2025–26 cycle) and runs ~52 weeks. Weeks before
the current cycle move into a per-exam **Archive** section, grouped by cycle. This
recurs automatically: when the next cycle begins each December, the previous cycle rolls
into the archive with no manual migration (`config.current_cycle_start` /
`cycle_start_for` / `cycle_label`).

### Repository security

`main` is protected: every change requires a pull request with an approving review from
the code owner (`.github/CODEOWNERS` → the repo owner), stale reviews are dismissed, and
force-pushes/deletions are blocked. The repo is owner-only (no other collaborators), so
no other account can push; any future collaborator must open a PR the owner approves.

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
python pipeline/daily_runner.py --week 1                       # RBI Grade B (default)
python pipeline/daily_runner.py --week 1 --exam upsc-banking   # UPSC / Banking
```

`--exam` selects which exam's pipeline to run (weekly mode only). Each exam pulls
its own sources (see *Multi-exam architecture*), uses its own GA weightage, and
writes to `data/summaries/<exam-slug>/` and `data/questions/generated/<exam-slug>/`,
which the site then renders under that exam's tab. The default exam (`rbi-grade-b`)
keeps the original flat output layout. Run `scripts/build_site.py` afterwards to
publish the new content.

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

## Week numbering

Content is organised into **weekly** 7-day blocks, numbered within a yearly
**study cycle**. A cycle starts on the **last Monday of December** and runs ~52
weeks, so **Week 1 of the 2025–26 cycle is the week of 29 Dec 2025**. Weeks before
the current cycle's start roll into each exam's **Archive** automatically, and a
fresh Week 1 begins every December with no manual migration. See
*Study cycles & archive* above for the underlying helpers.

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
- **Rolling 2-day window:** `NEWS_LOOKBACK_DAYS = 1` keeps only today + yesterday,
  so anything that didn't make yesterday's digest surfaces today and stale items
  roll off the next day. Widen with `--days` for a one-off catch-up.
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
| `docs/index.html` | Landing page, **categorised by exam**, with each exam's chronological week index |
| `docs/weeks/<exam-slug>/<key>.html` | Per-week page: descriptive summary + interactive MCQ quiz |
| `docs/assets/style.css`, `docs/assets/app.js` | Minimalist styling + quiz logic |

The landing page groups weeks **per exam** (RBI Grade B, UPSC / Banking, …) with a
jump-pill nav; an exam with no published weeks yet shows a "coming soon" panel. Each
week page renders the summary with topic sections, highlighted figures/dates, and ⭐
priority markers, followed by an in-browser practice quiz (pick an option to see the
correct answer, with a running score).

The site reads each exam's content from `data/summaries/<exam-slug>/` and
`data/questions/generated/<exam-slug>/` (RBI Grade B keeps the original flat layout for
backward compatibility).

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

The site is already live at **https://votrascii.github.io/govt-exam-prep/**, served by
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
- Source-material budget via `MAX_CONTENT_WORDS` (raised to **90,000** so far more
  source text reaches the model before any truncation → richer, fuller summaries)
- The exam registry via `EXAMS` / `DEFAULT_EXAM` (see *Multi-exam architecture*)
- Weekly range via `WEEK_RANGE_START` and `WEEK_RANGE_END`
- Scheduler frequency via `SCHEDULER_INTERVAL_HOURS`
- Source URLs
- Fuzzy dedup threshold
- Max content word limit for Ollama prompt

You can also override the model order without editing files:

```bash
OLLAMA_MODELS="llama3.1:latest,qwen3.5:9b" python pipeline/daily_runner.py --day 1
```
