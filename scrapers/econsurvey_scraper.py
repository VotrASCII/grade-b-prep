"""
Economic Survey / Yojana scraper (UPSC / Banking static-enrichment source).

Unlike PIB or RBI, the Economic Survey is an *annual* document and Yojana is a
*monthly* magazine — neither produces weekly items. So for the weekly current-
affairs pipeline this scraper is a static-enrichment source: ``scrape_*_range``
returns only the document(s) whose publication date falls inside the requested
window (normally nothing for a 7-day week), and the heavy lifting for a UPSC week
comes from all-ministry PIB.

The yearly ingestion (``scrape_econsurvey_year``) pulls the chapter list + summary
of a given Economic Survey edition so it can be folded into static study material
later. Network access is required; callers should treat failures as non-fatal.

Returns the same item shape as the other scrapers:
    {"title", "date", "url", "content"}
"""

from __future__ import annotations

from datetime import date

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Official landing page for the latest Economic Survey.
ECON_SURVEY_HOME = "https://www.indiabudget.gov.in/economicsurvey/"
# Yojana (publications division) archive.
YOJANA_HOME = "https://www.yojana.gov.in/"

# Approximate annual publication dates (Economic Survey is tabled a day before the
# Union Budget — late Jan / Feb). Used to decide whether an edition falls inside a
# requested weekly window.
KNOWN_EDITIONS = {
    2023: date(2023, 1, 31),
    2024: date(2024, 7, 22),  # interim-year survey tabled with the full budget
    2025: date(2025, 1, 31),
    2026: date(2026, 1, 31),
}


def _get(url: str, timeout: int = 20) -> "requests.Response | None":
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] Economic Survey fetch failed for {url}: {e}")
        return None


def scrape_econsurvey_year(year: int) -> list[dict]:
    """Best-effort fetch of an Economic Survey edition landing page.

    Returns a single item with whatever overview text is reachable, or an empty
    list if the page cannot be retrieved. Kept intentionally light — the goal is
    exam-relevant macro context, not the full multi-hundred-page document.
    """
    pub_date = KNOWN_EDITIONS.get(year, date(year, 1, 31))
    r = _get(ECON_SURVEY_HOME)
    if r is None:
        return []
    # The landing page is JS-heavy; we keep only a trimmed text snapshot as
    # context rather than attempting to parse the full chaptered PDF here.
    text = r.text
    # Crude tag strip so the model sees prose, not markup.
    import re

    prose = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    prose = re.sub(r"<style.*?</style>", " ", prose, flags=re.S | re.I)
    prose = re.sub(r"<[^>]+>", " ", prose)
    prose = re.sub(r"\s+", " ", prose).strip()
    if len(prose) < 40:
        return []
    return [
        {
            "title": f"Economic Survey {year} — overview",
            "date": pub_date.strftime("%d %b %Y"),
            "url": ECON_SURVEY_HOME,
            "content": prose[:8000],
        }
    ]


def scrape_econsurvey_range(start_date: date, end_date: date) -> list[dict]:
    """Return Economic Survey editions whose publication date is in [start, end].

    For a normal 7-day week this is empty; it only fires in the window around the
    annual survey's tabling. This keeps the weekly UPSC pipeline driven by PIB
    while still folding the survey in during the week it is published.
    """
    items: list[dict] = []
    for year, pub in KNOWN_EDITIONS.items():
        if start_date <= pub <= end_date:
            print(f"  Economic Survey {year} falls in window; ingesting overview ...")
            items.extend(scrape_econsurvey_year(year))
    if not items:
        print("  Economic Survey: no edition published in this window (expected for most weeks).")
    return items


if __name__ == "__main__":
    import sys

    yr = int(sys.argv[1]) if len(sys.argv) > 1 else date.today().year
    out = scrape_econsurvey_year(yr)
    print(f"Fetched {len(out)} Economic Survey item(s) for {yr}.")
