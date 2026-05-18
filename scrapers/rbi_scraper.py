"""
Module 3 — RBI Scraper
Scrapes RBI circulars and press releases for a given month/year.
Uses Playwright (async, headless Chromium) for circulars.
"""

import asyncio
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import RBI_CIRCULARS_URL, RBI_PRESS_URL

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

RBI_BASE = "https://www.rbi.org.in"
RBI_SCRIPTS_BASE = "https://www.rbi.org.in/Scripts/"

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


def _parse_rbi_date(raw_date: str) -> "date | None":
    raw_date = (raw_date or "").strip()
    for fmt in SOURCE_DATE_FORMATS:
        try:
            return datetime.strptime(raw_date, fmt).date()
        except ValueError:
            pass
    return None


def _in_date_range(item: dict, start_date: "date | None", end_date: "date | None") -> bool:
    if start_date is None or end_date is None:
        return True
    item_date = _parse_rbi_date(item.get("date", ""))
    return item_date is not None and start_date <= item_date <= end_date


def _filter_items_by_date(
    items: list[dict],
    start_date: "date | None",
    end_date: "date | None",
    source_name: str,
) -> list[dict]:
    if start_date is None or end_date is None:
        return items
    filtered = [item for item in items if _in_date_range(item, start_date, end_date)]
    skipped = len(items) - len(filtered)
    if skipped:
        print(f"  Skipped {skipped} {source_name} rows outside {start_date} to {end_date}.")
    return filtered


def _get(url: str, timeout: int = 20) -> "requests.Response | None":
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [WARN] GET {url} failed: {e}")
        return None


def _normalise_rbi_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return urljoin(RBI_BASE, href)
    return urljoin(RBI_SCRIPTS_BASE, href)


def _fetch_detail_page(url: str) -> str:
    if not url:
        return ""

    fixed_url = _normalise_rbi_url(url)
    r = _get(fixed_url, timeout=30)
    if r is None:
        return ""

    soup = BeautifulSoup(r.text, "html.parser")
    content = (
        soup.select_one("table.tablebg")
        or soup.find("td", class_=lambda c: c and "tablecontent" in c.lower())
        or soup.find("div", class_=lambda c: c and "content" in c.lower())
    )
    if content:
        text = content.get_text(" ", strip=True)
    else:
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)

    # Drop obvious site chrome if a fallback selector was used.
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


# ---------------------------------------------------------------------------
# Playwright-based circular scraper
# ---------------------------------------------------------------------------

async def _scrape_circulars_playwright(year: int, month: int) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  [WARN] playwright not installed — skipping circular scrape via browser.")
        return []

    month_name = MONTH_NAMES[month]
    results = []

    print(f"  Launching Playwright for RBI circulars {year}-{month:02d} ...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(RBI_CIRCULARS_URL, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=20000)

            # Click the year accordion — look for a link/button containing the year
            year_selector = f"text={year}"
            try:
                await page.click(year_selector, timeout=5000)
                await page.wait_for_timeout(1500)
            except Exception:
                # Try finding by partial text match
                try:
                    await page.click(f"a:has-text('{year}')", timeout=5000)
                    await page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"  [WARN] Could not click year {year}: {e}")

            # Click the month link
            try:
                await page.click(f"text={month_name}", timeout=5000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"  [WARN] Could not click month {month_name}: {e}")

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Extract table rows from the circular listing table
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")[1:]  # skip header
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 3:
                        continue
                    circular_number = cells[0].get_text(strip=True)
                    date_text = cells[1].get_text(strip=True)
                    department = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    subject = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    link = row.find("a")
                    url = ""
                    if link and link.get("href"):
                        url = _normalise_rbi_url(link["href"])
                    results.append({
                        "circular_number": circular_number,
                        "date": date_text,
                        "department": department,
                        "subject": subject,
                        "url": url,
                        "type": "circular",
                    })
        except Exception as e:
            print(f"  [ERROR] Playwright circulars: {e}")
        finally:
            await browser.close()

    print(f"  Playwright: {len(results)} circulars found.")
    return results


def scrape_rbi_circulars(
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
) -> list[dict]:
    items = asyncio.run(_scrape_circulars_playwright(year, month))
    return _filter_items_by_date(items, start_date, end_date, "RBI circular")


# ---------------------------------------------------------------------------
# HTTP-based press release scraper
# ---------------------------------------------------------------------------

def scrape_rbi_press_releases(
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
) -> list[dict]:
    print(f"  Fetching RBI press releases for {year}-{month:02d} ...")
    results = []

    # Step 1: GET the page to obtain ASP.NET form tokens
    r = _get(RBI_PRESS_URL)
    if r is None:
        return []
    soup = BeautifulSoup(r.text, "html.parser")

    def _val(name: str) -> str:
        tag = soup.find("input", {"name": name})
        return tag["value"] if tag and tag.get("value") else ""

    # Step 2: POST with hdnYear/hdnMonth — this is how the RBI site filters by month
    post_data = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _val("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _val("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _val("__EVENTVALIDATION"),
        "hdnYear": str(year),
        "hdnMonth": str(month),
    }
    try:
        r2 = requests.post(RBI_PRESS_URL, headers=HEADERS, data=post_data, timeout=20)
        r2.raise_for_status()
    except Exception as e:
        print(f"  [WARN] RBI press POST failed: {e}")
        return []

    soup2 = BeautifulSoup(r2.text, "html.parser")
    table = soup2.find("table")
    if not table:
        print("  [WARN] No table found in RBI press release response.")
        return []

    # Table structure: date rows have 1 <td> (e.g. "Apr 30, 2026"),
    # content rows have 2 <td>s (title + size) with a link.
    current_date = ""
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) == 1:
            # Date separator row
            current_date = cells[0].get_text(strip=True)
        elif len(cells) >= 2:
            link = row.find("a")
            if not link:
                continue
            subject = cells[0].get_text(strip=True)
            href = link.get("href", "")
            url = _normalise_rbi_url(href)
            item = {
                "circular_number": "",
                "date": current_date,
                "department": "Press Release",
                "subject": subject,
                "url": url,
                "type": "press_release",
            }
            if _in_date_range(item, start_date, end_date):
                results.append(item)

    print(f"  RBI press releases: {len(results)} found.")
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_rbi(
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
) -> list[dict]:
    """Scrape and merge RBI circulars + press releases for the given month."""
    if start_date and end_date:
        print(f"  Fetching RBI items for {start_date} to {end_date} ...")
    circulars = scrape_rbi_circulars(year, month, start_date, end_date)
    press = scrape_rbi_press_releases(year, month, start_date, end_date)

    seen_urls: set[str] = set()
    merged = []
    for item in circulars + press:
        key = item.get("url") or item.get("subject", "")
        if key and key not in seen_urls:
            seen_urls.add(key)
            item["content"] = _fetch_detail_page(item.get("url", ""))
            merged.append(item)
            time.sleep(0.2)

    print(f"  RBI total (merged): {len(merged)} items for {year}-{month:02d}.")
    return merged


def scrape_rbi_range(start_date: date, end_date: date) -> list[dict]:
    results = []
    cursor = date(start_date.year, start_date.month, 1)
    last = date(end_date.year, end_date.month, 1)
    while cursor <= last:
        month_start = max(start_date, cursor)
        next_month = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        month_end = min(end_date, next_month - date.resolution)
        results.extend(scrape_rbi(cursor.year, cursor.month, month_start, month_end))
        cursor = next_month
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape RBI circulars & press releases")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    args = parser.parse_args()

    items = scrape_rbi(args.year, args.month)
    for item in items:
        print(f"\n[{item['type']}] {item['date']} — {item['subject']}")
        if item.get("url"):
            print(f"  URL: {item['url']}")
