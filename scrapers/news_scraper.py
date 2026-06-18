"""
News RSS scraper.

Ingests ONLY RSS metadata (headline, link, publish date, short summary) from
Economic Times, Mint, and Hindustan Times — never full copyrighted article
bodies. Each item links back to and cites the original source.

Returns a list of dicts:
    {title, source, url, date (YYYY-MM-DD), summary}
"""

from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import NEWS_FEEDS, NEWS_MAX_PER_SOURCE  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
REQUEST_TIMEOUT = 20
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(raw: str | None, max_chars: int = 320) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)          # strip any inline HTML
    text = html.unescape(text)             # unescape entities revealed after strip
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return text


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)  # handles RFC-822 (standard RSS pubDate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first_text(item: ET.Element, *names: str) -> str | None:
    for child in item:
        if _localname(child.tag) in names:
            if child.text and child.text.strip():
                return child.text
            # Some feeds use attributes (e.g. <link href="...">) or nested text.
            href = child.attrib.get("href")
            if href:
                return href
    return None


def _parse_feed(content: bytes, source: str) -> list[dict]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    items: list[dict] = []
    for el in root.iter():
        if _localname(el.tag) not in {"item", "entry"}:
            continue
        title = _clean_text(_first_text(el, "title"), max_chars=240)
        link = _first_text(el, "link", "guid")
        date = _parse_date(_first_text(el, "pubDate", "published", "updated", "date"))
        summary = _clean_text(_first_text(el, "description", "summary", "content"))
        if not title or not link:
            continue
        items.append({
            "title": title,
            "source": source,
            "url": link.strip(),
            "date": date,
            "summary": summary,
        })
    return items


def scrape_news(feeds: dict[str, list[str]] | None = None) -> list[dict]:
    feeds = feeds or NEWS_FEEDS
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    collected: list[dict] = []
    for source, urls in feeds.items():
        source_items: list[dict] = []
        for url in urls:
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as e:
                print(f"  [WARN] {source} feed failed ({url}): {e}")
                continue
            parsed = _parse_feed(resp.content, source)
            print(f"  {source}: {len(parsed)} items from {url.rsplit('/', 1)[-1]}")
            source_items.extend(parsed)
        collected.extend(source_items[:NEWS_MAX_PER_SOURCE])

    return _dedupe(collected)


def _dedupe(items: list[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[dict] = []
    for item in items:
        url = item["url"].split("?")[0]
        title_key = re.sub(r"\W+", "", item["title"].lower())[:80]
        if url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(url)
        seen_titles.add(title_key)
        out.append(item)
    return out


if __name__ == "__main__":
    results = scrape_news()
    print(f"\nTotal deduped news items: {len(results)}")
    for r in results[:5]:
        print(f"  [{r['date']}] ({r['source']}) {r['title'][:80]}")
