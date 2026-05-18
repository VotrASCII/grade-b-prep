"""
Module 2 — PIB Scraper
Scrapes PIB press releases for a given month/year.
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
from config import PIB_DETAIL_BASE, PIB_RELEASES_URL

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.pib.gov.in/",
}

PIB_BASE = "https://www.pib.gov.in"
PRID_RE = re.compile(r"PRID=(\d+)", re.IGNORECASE)
DATE_RE = re.compile(
    r"(?:Posted\s+on|Date)\s*:?\s*(\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})",
    re.IGNORECASE,
)
GENERIC_DATE_RE = re.compile(r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})\b")


def _get(url: str, params: "dict | None" = None, timeout: int = 20) -> "requests.Response | None":
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [WARN] GET {url} failed: {e}")
        return None


def _parse_pib_date(text: str) -> "date | None":
    text = text.strip()
    for fmt in ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").date()
        except ValueError:
            pass
    return None


def _in_date_range(release_date: "date | None", start_date: "date | None", end_date: "date | None") -> bool:
    if start_date is None or end_date is None:
        return True
    if release_date is None:
        return False
    return start_date <= release_date <= end_date


def _extract_listing_date(link) -> "date | None":
    candidates = []
    node = link
    for _ in range(4):
        if not node:
            break
        text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else ""
        if text:
            candidates.append(text)
        node = node.parent

    parent = link.find_parent(["li", "tr", "div"])
    if parent:
        for sibling in parent.find_previous_siblings(limit=3):
            text = sibling.get_text(" ", strip=True)
            if text:
                candidates.append(text)

    for text in candidates:
        match = DATE_RE.search(text) or GENERIC_DATE_RE.search(text)
        if match:
            parsed = _parse_pib_date(match.group(1))
            if parsed:
                return parsed
    return None


def _fetch_detail_page(prid: str) -> str:
    url = f"{PIB_DETAIL_BASE}{prid}&lang=1"
    r = _get(url)
    if r is None:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")

    content_div = (
        soup.find("div", class_=lambda c: c and "innner-page-main-about-us-content-right-part" in c)
        or soup.find("div", id="PCmsContent")
        or soup.find("div", class_="pcms_detail")
        or soup.find("div", id="ContentPlaceHolder1_lblPRContent")
        or soup.find("div", class_=lambda c: c and "content" in c.lower() if c else False)
    )
    if content_div:
        return content_div.get_text(" ", strip=True)

    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:5000]


def _parse_releases_from_html(
    html: str,
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
    assumed_date: "date | None" = None,
) -> list[dict]:
    """Extract PRID links from PIB listing HTML, filtering to the target month/year."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    releases = []
    skipped_without_date = 0

    for link in soup.find_all("a", href=PRID_RE):
        href = link.get("href", "")
        m = PRID_RE.search(href)
        if not m:
            continue
        prid = m.group(1)
        if prid in seen:
            continue

        release_date = _extract_listing_date(link) or assumed_date
        if start_date is not None and end_date is not None:
            if not _in_date_range(release_date, start_date, end_date):
                if release_date is None:
                    skipped_without_date += 1
                continue
        else:
            if release_date and (release_date.year != year or release_date.month != month):
                continue

        seen.add(prid)
        releases.append({
            "title": link.get_text(strip=True),
            "date": str(release_date) if release_date else f"{year}-{month:02d}",
            "url": urljoin(PIB_BASE, href),
            "prid": prid,
        })

    if skipped_without_date:
        print(f"  Skipped {skipped_without_date} PIB listing rows without parseable dates.")
    return releases


# ---------------------------------------------------------------------------
# HTTP scraper — uses the ASP.NET form postback fields for month/year filtering
# ---------------------------------------------------------------------------

def _extract_form_value(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("input", {"name": name})
    return tag.get("value", "") if tag else ""


def _selected_option_value(select_tag) -> str:
    option = select_tag.find("option", selected=True) or select_tag.find("option")
    return option.get("value", "") if option else ""


def _has_select_option(soup: BeautifulSoup, select_name: str, value: str) -> bool:
    select_tag = soup.find("select", {"name": select_name})
    if not select_tag:
        return False
    return any(option.get("value") == value for option in select_tag.find_all("option"))


def _build_pib_post_data(
    soup: BeautifulSoup,
    year: int,
    month: int,
    day: int = 0,
) -> "dict[str, str]":
    post_data = {
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlMonth",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": _extract_form_value(soup, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _extract_form_value(soup, "__VIEWSTATEGENERATOR"),
        "__VIEWSTATEENCRYPTED": _extract_form_value(soup, "__VIEWSTATEENCRYPTED"),
        "__EVENTVALIDATION": _extract_form_value(soup, "__EVENTVALIDATION"),
    }

    for select_tag in soup.find_all("select"):
        name = select_tag.get("name")
        if name:
            post_data[name] = _selected_option_value(select_tag)

    post_data["ctl00$ContentPlaceHolder1$ddlday"] = str(day)
    post_data["ctl00$ContentPlaceHolder1$ddlMinistry"] = "0"
    post_data["ctl00$ContentPlaceHolder1$ddlYear"] = str(year)
    post_data["ctl00$ContentPlaceHolder1$ddlMonth"] = str(month)
    return post_data


def _scrape_pib_http(
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
) -> list[dict]:
    """
    Scrape the PIB listing page with a direct ASP.NET postback.
    PIB keeps the filter state in hidden form fields, so a GET with query
    params is not enough for historical months.
    """
    r = _get(PIB_RELEASES_URL)
    if r is None:
        print("  [ERROR] Could not fetch PIB listing page.")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    year_name = "ctl00$ContentPlaceHolder1$ddlYear"
    month_name = "ctl00$ContentPlaceHolder1$ddlMonth"
    if not _has_select_option(soup, year_name, str(year)):
        print(f"  [WARN] PIB year {year} is not available in the listing dropdown.")
        return []
    if not _has_select_option(soup, month_name, str(month)):
        print(f"  [WARN] PIB month {month} is not available in the listing dropdown.")
        return []

    releases = []
    seen: set[str] = set()
    if start_date is not None and end_date is not None:
        cursor = start_date
        while cursor <= end_date:
            try:
                r2 = requests.post(
                    PIB_RELEASES_URL,
                    headers=HEADERS,
                    data=_build_pib_post_data(soup, year, month, cursor.day),
                    timeout=30,
                )
                r2.raise_for_status()
            except Exception as e:
                print(f"  [WARN] PIB form POST failed for {cursor}: {e}")
                cursor += date.resolution
                continue

            day_releases = _parse_releases_from_html(
                r2.text,
                year,
                month,
                start_date,
                end_date,
                assumed_date=cursor,
            )
            for release in day_releases:
                prid = release.get("prid", "")
                if prid and prid not in seen:
                    seen.add(prid)
                    releases.append(release)
            cursor += date.resolution
    else:
        try:
            r2 = requests.post(
                PIB_RELEASES_URL,
                headers=HEADERS,
                data=_build_pib_post_data(soup, year, month),
                timeout=30,
            )
            r2.raise_for_status()
        except Exception as e:
            print(f"  [WARN] PIB form POST failed: {e}")
            return []

        releases = _parse_releases_from_html(r2.text, year, month)

    print(f"  HTTP form POST: {len(releases)} PIB releases found.")
    return releases


# ---------------------------------------------------------------------------
# Playwright-based fallback
# ---------------------------------------------------------------------------

async def _select_and_wait(page, sel: str, value: str, label: str) -> bool:
    """Set a <select> dropdown value and wait for PIB's postback to settle."""
    # Build JS using double-quoted strings so CSS attribute selectors
    # like select[id$='ddlYear'] don't break the JS string literal.
    js = (
        "(function() {"
        f'  var s = document.querySelector("{sel}");'
        "  if (!s) return false;"
        f'  s.value = "{value}";'
        '  var ev = document.createEvent("HTMLEvents");'
        '  ev.initEvent("change", true, true);'
        "  s.dispatchEvent(ev);"
        "  return true;"
        "})()"
    )
    try:
        ok = await page.evaluate(js)
        if not ok:
            print(f"  [WARN] Element not found on page for {label} ({sel})")
            return False
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        return True
    except Exception as e:
        print(f"  [WARN] Could not set {label}: {e}")
        return False


async def _scrape_pib_playwright(
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  [WARN] playwright not installed.")
        raise

    YEAR_SEL  = "select[id$='ddlYear']"   # ends-with match, handles any prefix
    MONTH_SEL = "select[id$='ddlMonth']"

    print(f"  Launching Playwright for PIB {year}-{month:02d} ...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use a real browser UA so PIB doesn't serve a stripped-down page
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        # Block images/fonts/media to speed up page loading
        await page.route(
            "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,mp4,webp}",
            lambda r: r.abort(),
        )
        try:
            await page.goto(
                PIB_RELEASES_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            print(f"  PIB page loaded: {page.url}")

            # Select year (triggers ASP.NET __doPostBack)
            await _select_and_wait(page, YEAR_SEL, str(year), "year")

            # Select month (triggers another ASP.NET __doPostBack)
            await _select_and_wait(page, MONTH_SEL, str(month), "month")

            # Give the results list a moment to render after the final redirect
            try:
                await page.wait_for_selector("li a[href*='PRID']", timeout=10000)
            except Exception:
                pass  # results might use a different structure; take what we have

            html = await page.content()
        except Exception as e:
            print(f"  [ERROR] Playwright PIB: {e}")
            html = await page.content()
        finally:
            await context.close()
            await browser.close()

    releases = _parse_releases_from_html(html, year, month, start_date, end_date)
    print(f"  Playwright: {len(releases)} PIB releases found.")
    return releases


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_pib(
    year: int,
    month: int,
    start_date: "date | None" = None,
    end_date: "date | None" = None,
) -> list[dict]:
    """
    Scrape PIB press releases for the given year and month.
    Returns list of {title, date, url, content} dicts.
    """
    if start_date and end_date:
        print(f"  Fetching PIB releases for {start_date} to {end_date} ...")
    else:
        print(f"  Fetching PIB releases for {year}-{month:02d} ...")

    releases = _scrape_pib_http(year, month, start_date, end_date)
    if not releases:
        try:
            releases = asyncio.run(_scrape_pib_playwright(year, month, start_date, end_date))
        except (ImportError, Exception) as e:
            print(f"  [WARN] Playwright fallback failed ({e}).")

    print(f"  Found {len(releases)} releases. Fetching detail pages...")

    results = []
    for i, rel in enumerate(releases, 1):
        print(f"    [{i}/{len(releases)}] {rel['title'][:70]}")
        content = _fetch_detail_page(rel["prid"])
        results.append({
            "title": rel["title"],
            "date": rel["date"],
            "url": rel["url"],
            "content": content,
        })
        if i < len(releases):
            time.sleep(0.5)

    print(f"  PIB: {len(results)} releases fetched for {year}-{month:02d}.")
    return results


def scrape_pib_range(start_date: date, end_date: date) -> list[dict]:
    results = []
    cursor = date(start_date.year, start_date.month, 1)
    last = date(end_date.year, end_date.month, 1)
    while cursor <= last:
        month_start = max(start_date, cursor)
        next_month = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        month_end = min(end_date, next_month - date.resolution)
        results.extend(scrape_pib(cursor.year, cursor.month, month_start, month_end))
        cursor = next_month
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape PIB press releases")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    args = parser.parse_args()

    releases = scrape_pib(args.year, args.month)
    for r in releases:
        print(f"\n{r['date']} — {r['title']}")
        print(r["content"][:300])
