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
# How many days back to include in a news digest, and the cap per source.
NEWS_LOOKBACK_DAYS = 7
NEWS_MAX_PER_SOURCE = 40

FUZZY_THRESHOLD = 0.85
MAX_CONTENT_WORDS = 45_000
CHUNK_CONTENT_WORDS = 12_000
CHUNK_SUMMARY_WORDS = 1_500
MIN_DETAIL_CONTENT_WORDS = 25
MIN_DETAIL_CONTENT_COVERAGE = 0.80
WEEK_RANGE_START = "2025-12-01"
# Leave WEEK_RANGE_END blank ("") to keep the weekly schedule open-ended: weeks
# automatically extend in 7-day blocks up to the most recently completed week,
# so the pipeline keeps publishing a new week every week with no end date.
WEEK_RANGE_END = ""
SCHEDULER_INTERVAL_HOURS = 6
