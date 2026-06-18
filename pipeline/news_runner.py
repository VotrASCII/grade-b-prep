"""
News pipeline runner.

Fetches RSS metadata from ET / Mint / Hindustan Times, screens each item for
relevance to RBI Grade B, SEBI Grade A, NABARD Grade A and UPSC/Banking, writes
a one-line "why it matters" note, and saves a cited digest to data/news/.

Exam tagging + the relevance note use a fast keyword heuristic as a baseline,
refined by Ollama when it is reachable. Only RSS metadata is stored — never the
full copyrighted article body. Every item links back to its source.

Usage:
    python pipeline/news_runner.py            # fetch, tag (LLM if available), save
    python pipeline/news_runner.py --no-llm   # heuristic tagging only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import NEWS_EXAMS, NEWS_LOOKBACK_DAYS  # noqa: E402
from scrapers.news_scraper import scrape_news       # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
NEWS_DIR = BASE_DIR / "data" / "news"

EXAM_NAMES = list(NEWS_EXAMS.keys())

TOPIC_KEYWORDS = {
    "RBI & Monetary Policy": ["rbi", "repo", "monetary", "inflation", "mpc", "liquidity", "rupee"],
    "Banking & Finance": ["bank", "npa", "loan", "credit", "deposit", "lender", "nbfc"],
    "Markets & Securities": ["sebi", "market", "sensex", "nifty", "ipo", "stock", "mutual fund", "bond"],
    "Agriculture & Rural": ["agri", "rural", "farmer", "crop", "kisan", "nabard", "msme"],
    "Govt Schemes & Policy": ["scheme", "budget", "gst", "subsidy", "ministry", "policy", "government"],
    "Economy & Trade": ["gdp", "trade", "export", "import", "economy", "fiscal", "tax", "growth"],
    "International": ["global", "world", "us ", "china", "imf", "world bank", "brics", "foreign"],
}


# ---------------------------------------------------------------------------
# Heuristic tagging (baseline / fallback)
# ---------------------------------------------------------------------------

def _haystack(item: dict) -> str:
    return f"{item.get('title', '')} {item.get('summary', '')}".lower()

def heuristic_exams(item: dict) -> list[str]:
    text = _haystack(item)
    tags = [exam for exam, kws in NEWS_EXAMS.items() if any(k in text for k in kws)]
    # Economy/finance news is broadly relevant; default to the two general exams.
    if not tags:
        tags = ["RBI Grade B", "UPSC / Banking"]
    return [e for e in EXAM_NAMES if e in tags]  # stable canonical order

def heuristic_topic(item: dict) -> str:
    text = _haystack(item)
    best, best_hits = "Economy & Trade", 0
    for topic, kws in TOPIC_KEYWORDS.items():
        hits = sum(1 for k in kws if k in text)
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


# ---------------------------------------------------------------------------
# LLM refinement (optional)
# ---------------------------------------------------------------------------

def _ollama_available() -> bool:
    try:
        import requests
        from config import OLLAMA_URL
        base = OLLAMA_URL.rsplit("/api/", 1)[0]
        r = requests.get(f"{base}/api/tags", timeout=4)
        return r.status_code == 200
    except Exception:
        return False

def _build_batch_prompt(batch: list[dict]) -> str:
    lines = []
    for i, it in enumerate(batch, 1):
        summary = (it.get("summary") or "")[:240]
        lines.append(f"{i}. {it['title']} — {summary}")
    items_block = "\n".join(lines)
    exams = ", ".join(EXAM_NAMES)
    return f"""\
You are writing self-contained study notes from Indian business/finance news for
competitive-exam aspirants. Each item gives a headline and a short blurb.

For EACH numbered item below, produce:
- summary: 2-3 sentences IN YOUR OWN WORDS that explain what happened, the key
  facts/figures/names, and why it matters for the exam. This is the only thing the
  student will read — make it complete and standalone. Do NOT copy the blurb
  verbatim and do NOT tell the reader to "read more" anywhere.
- exams: which of these it is relevant to (subset, may be several): {exams}
- topic: a short label, one of: RBI & Monetary Policy, Banking & Finance,
  Markets & Securities, Agriculture & Rural, Govt Schemes & Policy,
  Economy & Trade, International

Return ONLY a JSON array, one object per item, no prose, no markdown fences:
[{{"i": 1, "summary": "...", "exams": ["RBI Grade B"], "topic": "Economy & Trade"}}]

ITEMS:
{items_block}
"""

def _parse_llm_json(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []

def llm_refine(items: list[dict], batch_size: int = 12) -> None:
    """Annotate items in place with exams/relevance/topic via Ollama."""
    from pipeline.daily_runner import call_ollama_with_fallback

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        prompt = _build_batch_prompt(batch)
        print(f"  LLM tagging items {start + 1}-{start + len(batch)} of {len(items)} ...")
        try:
            raw = call_ollama_with_fallback(prompt)
        except Exception as e:
            print(f"  [WARN] LLM batch failed, keeping heuristics: {e}")
            continue
        parsed = _parse_llm_json(raw)
        by_index = {obj.get("i"): obj for obj in parsed if isinstance(obj, dict)}
        for offset, item in enumerate(batch, 1):
            obj = by_index.get(offset)
            if not obj:
                continue
            exams = [e for e in EXAM_NAMES if e in (obj.get("exams") or [])]
            if exams:
                item["exams"] = exams
            if obj.get("summary"):
                # Replace the RSS blurb with our own original, exam-focused summary.
                item["summary"] = str(obj["summary"]).strip()
            if obj.get("topic"):
                item["topic"] = str(obj["topic"]).strip()


# ---------------------------------------------------------------------------
# Build digest
# ---------------------------------------------------------------------------

def _within_lookback(item: dict, cutoff: date) -> bool:
    raw = item.get("date")
    if not raw:
        return True  # undated items kept (RSS sometimes omits pubDate)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() >= cutoff
    except ValueError:
        return True

def run(use_llm: bool = True, lookback_days: int | None = None) -> Path:
    lookback = lookback_days if lookback_days is not None else NEWS_LOOKBACK_DAYS
    cutoff = date.today() - timedelta(days=lookback)

    print("=" * 60)
    print(f"News Runner — last {lookback} days (cutoff {cutoff})")
    print("=" * 60)
    print("\n[Step 1] Fetching RSS feeds ...")
    items = scrape_news()
    items = [it for it in items if _within_lookback(it, cutoff)]
    print(f"  {len(items)} items within lookback window")

    print("\n[Step 2] Heuristic exam tagging ...")
    for it in items:
        it["exams"] = heuristic_exams(it)
        it["topic"] = heuristic_topic(it)
        # Without the LLM, fall back to the RSS blurb as the summary.
        it["summary"] = it.get("summary") or ""

    if use_llm and _ollama_available():
        print("\n[Step 3] Refining tags with Ollama ...")
        llm_refine(items)
    else:
        print("\n[Step 3] Skipping LLM (unavailable or --no-llm); using heuristics.")

    items.sort(key=lambda it: (it.get("date") or "0000-00-00"), reverse=True)

    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    digest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "lookback_days": lookback,
        "count": len(items),
        "sources": sorted({it["source"] for it in items}),
        "exams": EXAM_NAMES,
        "items": items,
    }
    out_path = NEWS_DIR / "latest.json"
    out_path.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")
    # Also keep a dated snapshot for history.
    snapshot = NEWS_DIR / f"{date.today():%Y-%m-%d}.json"
    snapshot.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nSaved {len(items)} items → {out_path.relative_to(BASE_DIR)}")
    print(f"  Sources: {', '.join(digest['sources'])}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="News digest runner")
    parser.add_argument("--no-llm", action="store_true", help="Heuristic tagging only")
    parser.add_argument("--days", type=int, default=None, help="Lookback window in days")
    args = parser.parse_args()
    run(use_llm=not args.no_llm, lookback_days=args.days)
