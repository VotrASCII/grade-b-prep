from datetime import date as _date, timedelta as _timedelta

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL_PRIMARY = "gpt-oss:120b-cloud"
OLLAMA_MODEL_FALLBACK = "qwen3.5:2b"
OLLAMA_MODELS = [
    OLLAMA_MODEL_PRIMARY,
    OLLAMA_MODEL_FALLBACK,
    "qwen3.5:0.8b",
]
OLLAMA_CONNECT_TIMEOUT_SECONDS = 10
OLLAMA_READ_TIMEOUT_SECONDS = 180
OLLAMA_NUM_CTX = 1024 * 64
OLLAMA_NUM_PREDICT = 1024 * 24
OLLAMA_DISABLE_THINKING = False

DAY_MAP = {
    1:  (2025, 5),   2:  (2025, 6),   3:  (2025, 7),   4:  (2025, 8),
    5:  (2025, 9),   6:  (2025, 10),  7:  (2025, 11),  8:  (2025, 12),
    9:  (2026, 1),  10:  (2026, 2),  11:  (2026, 3),  12:  (2026, 4),
    13:  (2026, 5),
}

EDUTAP_GA_PDF_URL = (
    "https://edutap.in/wp-content/uploads/2026/04/"
    "RBI-Grade-B-Phase-1-PYQs-2021-2025-Genera-Awareness-book.pdf"
)

EDUTAP_YEAR_URLS = {
    2023: "https://edutap.in/rbi-grade-b/previous-year-questions/rbi-grade-b-2023-ga-pyqs/",
    2024: "https://edutap.in/rbi-grade-b/previous-year-questions/rbi-grade-b-2024-pyqs/",
    2025: "https://edutap.in/rbi-grade-b/previous-year-questions/rbi-grade-b-2025-pyqs/",
}

AFFAIRSCLOUD_BASE = "https://affairscloud.com"
OLIVEBOARD_BLOG_BASE = "https://www.oliveboard.in/blog/"

PIB_RELEASES_URL = (
    "https://www.pib.gov.in/AllRelease.aspx?MenuId=286&reg=6&lang=1"
)
PIB_DETAIL_BASE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID="

RBI_CIRCULARS_URL = (
    "https://www.rbi.org.in/scripts/bs_circularindexdisplay.aspx"
)
RBI_PRESS_URL = (
    "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
)

# ── News pipeline (RSS-based) ──────────────────────────────────────────────
# Only RSS metadata (headline, link, date, short summary) is ingested — never
# full copyrighted article bodies. Business Standard is excluded: it blocks
# automated access (Akamai 403) and is hard-paywalled.
NEWS_FEEDS = {
    "Economic Times": [
        "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://economictimes.indiatimes.com/industry/banking/finance/banking/rssfeeds/13358259.cms",
    ],
    "Mint": [
        "https://www.livemint.com/rss/economy",
        "https://www.livemint.com/rss/money",
        "https://www.livemint.com/rss/markets",
    ],
    "Hindustan Times": [
        "https://www.hindustantimes.com/feeds/rss/business/rssfeed.xml",
    ],
}

# Exams each news item is screened against, with keyword hints used for the
# heuristic fallback tagger (the LLM refines these when Ollama is available).
NEWS_EXAMS = {
    "RBI Grade B": [
        "rbi", "monetary policy", "repo rate", "inflation", "cpi", "wpi", "gdp",
        "bank", "npa", "liquidity", "rupee", "forex", "mpc", "basel", "credit",
    ],
    "SEBI Grade A": [
        "sebi", "stock market", "sensex", "nifty", "ipo", "mutual fund",
        "capital market", "securities", "demat", "fpi", "fii", "bond",
    ],
    "NABARD Grade A": [
        "agriculture", "rural", "farmer", "crop", "msme", "cooperative",
        "priority sector", "kisan", "nabard", "irrigation", "agri",
    ],
    "UPSC / Banking": [
        "economy", "budget", "scheme", "gst", "trade", "export", "import",
        "fiscal", "subsidy", "policy", "government", "ministry", "world bank",
    ],
}
# ── Static-source PDFs (Economic Survey — yearly; Yojana — monthly) ────────
# These are published as PDFs, so they are downloaded, text-extracted, and fed to
# the static_runner. URLs/patterns are best-effort and overridable; if a download
# fails you can drop the PDF into data/static/<exam>/pdfs/ and the extractor still
# picks it up. {year} is substituted (e.g. 2025) for the relevant edition.
ECON_SURVEY_PAGE = "https://www.indiabudget.gov.in/economicsurvey/"
ECON_SURVEY_PDF_CANDIDATES = [
    "https://www.indiabudget.gov.in/economicsurvey/doc/echapter.pdf",
    "https://www.indiabudget.gov.in/budget{year}-26/economicsurvey/doc/echapter.pdf",
]
YOJANA_PAGE = "https://www.yojana.gov.in/"
# Cap on extracted words fed to the LLM per static source (keeps prompts sane).
STATIC_MAX_WORDS = 16_000
STATIC_MAX_PAGES_PER_PDF = 220

# How many days back to include in a news digest, and the cap per source.
# 1 => today + yesterday only (a rolling 2-day window): anything that didn't
# make yesterday's digest surfaces today, and stale items roll off the next day.
NEWS_LOOKBACK_DAYS = 1
NEWS_MAX_PER_SOURCE = 40

FUZZY_THRESHOLD = 0.85
# Raised from 45k → 90k so far more source material reaches the model before any
# truncation, producing richer, more comprehensive weekly summaries. Oversized
# periods are still split into condensed chunk-notes (see CHUNK_* below) so the
# whole window is used rather than silently dropping the tail.
MAX_CONTENT_WORDS = 90_000
CHUNK_CONTENT_WORDS = 14_000
CHUNK_SUMMARY_WORDS = 2_200
MIN_DETAIL_CONTENT_WORDS = 25
MIN_DETAIL_CONTENT_COVERAGE = 0.80
WEEK_RANGE_START = "2025-12-01"
# Leave WEEK_RANGE_END blank ("") to keep the weekly schedule open-ended: weeks
# automatically extend in 7-day blocks up to the most recently completed week,
# so the pipeline keeps publishing a new week every week with no end date.
WEEK_RANGE_END = ""
SCHEDULER_INTERVAL_HOURS = 6

# ── Multi-exam registry ────────────────────────────────────────────────────
# Each exam declares its own relevant sources, GA-pattern taxonomy, and display
# metadata. The pipeline, site, and prompt builder all read this registry so a
# new exam is added by appending an entry here (+ its scraper + taxonomy) rather
# than by editing the pipeline. `sources` are scraper keys resolved in
# daily_runner; `taxonomy` drives the summary sections + GA topic/style mix and
# is derived empirically from that exam's previous-year GA papers.
EXAMS = {
    "rbi-grade-b": {
        "name": "RBI Grade B",
        "slug": "rbi-grade-b",
        "order": 1,
        "active": True,
        "sources": ["pib", "rbi"],
        "taxonomy": "data/patterns/rbi-grade-b.json",
        "blurb": "Phase 1 General Awareness, built from PIB press releases and "
                 "RBI circulars — heavy on monetary policy, banking and the economy.",
    },
    "upsc-banking": {
        "name": "UPSC / Banking",
        "slug": "upsc-banking",
        "order": 2,
        "active": True,
        # Broad current affairs: all-ministry PIB plus Economic Survey / Yojana.
        "sources": ["pib_all", "econsurvey"],
        "taxonomy": "data/patterns/upsc-banking.json",
        "blurb": "Broad general studies current affairs — polity, economy, "
                 "international relations, environment, schemes and science & tech.",
    },
    # ── Scaffolded; activate once their scrapers + PYQ weightage are ready ──
    "sebi-grade-a": {
        "name": "SEBI Grade A",
        "slug": "sebi-grade-a",
        "order": 3,
        "active": False,
        "sources": ["sebi", "pib", "rbi"],
        "taxonomy": "data/patterns/sebi-grade-a.json",
        "blurb": "Securities markets, SEBI regulation and the wider economy.",
    },
    "nabard-grade-a": {
        "name": "NABARD Grade A",
        "slug": "nabard-grade-a",
        "order": 4,
        "active": False,
        "sources": ["nabard", "pib", "rbi"],
        "taxonomy": "data/patterns/nabard-grade-a.json",
        "blurb": "Agriculture, rural development and the rural financial system.",
    },
}

DEFAULT_EXAM = "rbi-grade-b"


def active_exams() -> dict:
    """Exams currently published, in display order."""
    return {
        k: v
        for k, v in sorted(EXAMS.items(), key=lambda kv: kv[1].get("order", 99))
        if v.get("active")
    }


# ── Study-cycle / archive logic ────────────────────────────────────────────
# A "study cycle" (the set of weeks shown on the main listing) starts on the
# LAST MONDAY OF DECEMBER and runs ~52 weeks to the next one. So the 2025–26
# cycle begins Mon 29 Dec 2025 = Week 1. When a new cycle begins (next late
# December), the previous cycle's weeks automatically move to the Archive
# section — no manual migration. Weeks before the current cycle's start (e.g.
# the Dec 1–28 2025 blocks) are archived, not shown in the main list.

def last_monday_of_december(year: int) -> _date:
    d = _date(year, 12, 31)
    while d.weekday() != 0:  # 0 = Monday
        d -= _timedelta(days=1)
    return d


def cycle_start_for(d: _date) -> _date:
    """The study-cycle anchor (start date) that the date ``d`` belongs to."""
    anchor = last_monday_of_december(d.year)
    return anchor if d >= anchor else last_monday_of_december(d.year - 1)


def current_cycle_start(today: _date | None = None) -> _date:
    return cycle_start_for(today or _date.today())


def cycle_label(anchor: _date) -> str:
    """Human label for a cycle, e.g. anchor in Dec 2025 → '2025–26'."""
    return f"{anchor.year}–{str(anchor.year + 1)[-2:]}"
