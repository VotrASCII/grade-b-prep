# RBI Grade B Phase 1 — GA Prep Tool

Automated scraper and AI study assistant for RBI Grade B Phase 1 General Awareness.

## What it does

1. **Scrapes past GA questions** (2023–2025) from EduTap, AffairsCloud, and Oliveboard
2. **Scrapes PIB press releases** and **RBI circulars** with local caching
3. **Generates AI summaries + practice MCQs** for weekly or monthly periods using Ollama
4. **Schedules itself** to process one completed week every 6 hours

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

**Manual monthly mode**:

```bash
python pipeline/daily_runner.py --day 1
```

**Force-run the scheduler's current weekly slot immediately**:

```bash
python run.py --run-now
```

## Weekly Range

Weekly runs are configured from **December 1, 2025** through **May 31, 2026** in 7-day blocks. This creates 26 weekly periods:

| Week | Date range |
|------|------------|
| 1 | 2025-12-01 to 2025-12-07 |
| 2 | 2025-12-08 to 2025-12-14 |
| ... | ... |
| 26 | 2026-05-25 to 2026-05-31 |

The scheduler only advances after a week is complete. If the next configured week has not ended yet, it waits for the next 6-hour scheduler tick.

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
