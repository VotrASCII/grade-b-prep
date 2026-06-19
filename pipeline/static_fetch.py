"""
Download + extract Economic Survey (yearly) and Yojana (monthly) PDFs, then feed
them to the static_runner so their facts rotate into weekly questions.

Robust by design: the download step is best-effort (gov PDF URLs are flaky/JS-
heavy). Whatever it manages to fetch is saved into ``data/static/<exam>/pdfs/``;
if a download fails you can simply drop the PDF there yourself (named so the key
is recognisable, e.g. ``econsurvey-2025.pdf`` or ``yojana-2026-06.pdf``) and the
extractor still ingests it. The extractor reads every matching PDF with
pdfplumber, concatenates the text, writes it to ``sources/<kind>-<key>.txt``, and
calls ``static_runner`` to build the segmented summary.

CLI:
    python pipeline/static_fetch.py --exam upsc-banking                 # ES + this month's Yojana
    python pipeline/static_fetch.py --exam upsc-banking --economic-survey 2025
    python pipeline/static_fetch.py --exam upsc-banking --yojana 2026-06
    python pipeline/static_fetch.py --exam upsc-banking --extract-only  # skip download; use dropped PDFs
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import requests

# pdfminer (under pdfplumber) is very chatty about malformed font descriptors in
# government PDFs; those warnings are harmless for text extraction.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import (  # noqa: E402
    ECON_SURVEY_PAGE,
    ECON_SURVEY_PDF_CANDIDATES,
    EXAMS,
    STATIC_MAX_PAGES_PER_PDF,
    STATIC_MAX_WORDS,
    YOJANA_PAGE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def pdf_dir(exam: str) -> Path:
    return BASE_DIR / "data" / "static" / exam / "pdfs"


def sources_dir(exam: str) -> Path:
    return BASE_DIR / "data" / "static" / exam / "sources"


# ── PDF text extraction ─────────────────────────────────────────────────────

def extract_pdf_text(path: Path, max_pages: int = STATIC_MAX_PAGES_PER_PDF) -> str:
    import pdfplumber

    out: list[str] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= max_pages:
                    break
                txt = page.extract_text() or ""
                if txt.strip():
                    out.append(txt)
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] could not read {path.name}: {e}")
        return ""
    return "\n".join(out)


# ── download (best-effort) ──────────────────────────────────────────────────

def _download(url: str, dest: Path, timeout: int = 60) -> bool:
    try:
        with requests.get(url, headers=HEADERS, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "")
            if "pdf" not in ctype.lower() and not url.lower().endswith(".pdf"):
                print(f"  [skip] not a PDF ({ctype}): {url}")
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        print(f"  Downloaded {dest.name} ({dest.stat().st_size // 1024} KB)")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] download failed ({url}): {e}")
        return False


def _scrape_pdf_links(page_url: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup

        r = requests.get(page_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                links.append(requests.compat.urljoin(page_url, href))
        return list(dict.fromkeys(links))
    except Exception as e:  # noqa: BLE001
        print(f"  [WARN] could not scrape PDF links from {page_url}: {e}")
        return []


def fetch_economic_survey(exam: str, year: int) -> list[Path]:
    d = pdf_dir(exam)
    saved: list[Path] = []
    # 1) try the stable direct full-document URLs
    for tmpl in ECON_SURVEY_PDF_CANDIDATES:
        url = tmpl.format(year=year)
        dest = d / f"econsurvey-{year}.pdf"
        if _download(url, dest):
            saved.append(dest)
            break
    # 2) fall back to scraping the survey landing page for chapter PDFs
    if not saved:
        for i, url in enumerate(_scrape_pdf_links(ECON_SURVEY_PAGE)[:40], 1):
            dest = d / f"econsurvey-{year}-{i:02d}.pdf"
            if _download(url, dest):
                saved.append(dest)
    return saved


def fetch_yojana(exam: str, year: int, month: int) -> list[Path]:
    d = pdf_dir(exam)
    saved: list[Path] = []
    # Yojana's site is JS-heavy; best-effort scrape for any PDF links.
    for i, url in enumerate(_scrape_pdf_links(YOJANA_PAGE)[:20], 1):
        dest = d / f"yojana-{year}-{month:02d}-{i:02d}.pdf"
        if _download(url, dest):
            saved.append(dest)
    return saved


# ── ingest: PDFs → text → static_runner ─────────────────────────────────────

def _matching_pdfs(exam: str, kind: str, key: str) -> list[Path]:
    d = pdf_dir(exam)
    if not d.exists():
        return []
    return sorted(d.glob(f"{kind}-{key}*.pdf"))


def ingest(exam: str, kind: str, key: str) -> bool:
    """Extract text from this source's PDFs and run static_runner on it."""
    pdfs = _matching_pdfs(exam, kind, key)
    if not pdfs:
        print(
            f"  No PDFs for {kind} {key} in {pdf_dir(exam).relative_to(BASE_DIR)} — "
            f"download failed or none dropped. Skipping."
        )
        return False

    print(f"  Extracting text from {len(pdfs)} PDF(s) for {kind} {key} ...")
    words: list[str] = []
    for p in pdfs:
        text = extract_pdf_text(p)
        words.extend(text.split())
        if len(words) >= STATIC_MAX_WORDS:
            break
    if len(words) < 50:
        print(f"  [WARN] extracted too little text for {kind} {key}; skipping.")
        return False

    if len(words) > STATIC_MAX_WORDS:
        print(f"  Capping extracted text at {STATIC_MAX_WORDS} words (was {len(words)}).")
        words = words[:STATIC_MAX_WORDS]

    sources_dir(exam).mkdir(parents=True, exist_ok=True)
    txt_path = sources_dir(exam) / f"{kind}-{key}.txt"
    txt_path.write_text(" ".join(words), encoding="utf-8")
    print(f"  Wrote extracted text → {txt_path.relative_to(BASE_DIR)} ({len(words)} words)")

    from pipeline.static_runner import run as static_run

    static_run(exam, kind, key, from_file=str(txt_path))
    return True


def _already_ingested(exam: str, kind: str, key: str) -> bool:
    return (sources_dir(exam) / f"{kind}-{key}.txt").exists()


def refresh(exam: str, extract_only: bool = False, force: bool = False) -> None:
    """Refresh the latest Economic Survey + current month's Yojana for an exam."""
    today = date.today()
    # Economic Survey edition: released ~Jan/Feb; before Feb the latest is last year's.
    es_year = today.year if today.month >= 2 else today.year - 1
    yojana_key = f"{today:%Y-%m}"

    print("=" * 60)
    print(f"Static Fetch [{EXAMS[exam]['name']}] — ES {es_year}, Yojana {yojana_key}")
    print("=" * 60)

    # Economic Survey (yearly)
    if force or not _already_ingested(exam, "economic-survey", str(es_year)):
        print(f"\n[Economic Survey {es_year}]")
        if not extract_only:
            fetch_economic_survey(exam, es_year)
        ingest(exam, "economic-survey", str(es_year))
    else:
        print(f"\n[Economic Survey {es_year}] already ingested — skipping.")

    # Yojana (monthly)
    if force or not _already_ingested(exam, "yojana", yojana_key):
        print(f"\n[Yojana {yojana_key}]")
        if not extract_only:
            fetch_yojana(exam, today.year, today.month)
        ingest(exam, "yojana", yojana_key)
    else:
        print(f"\n[Yojana {yojana_key}] already ingested — skipping.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download + extract ES/Yojana PDFs → static_runner")
    ap.add_argument("--exam", default="upsc-banking", choices=list(EXAMS.keys()))
    ap.add_argument("--economic-survey", metavar="YEAR", help="ingest a specific ES year")
    ap.add_argument("--yojana", metavar="YYYY-MM", help="ingest a specific Yojana month")
    ap.add_argument("--extract-only", action="store_true", help="skip download; use PDFs already in pdfs/")
    ap.add_argument("--force", action="store_true", help="re-ingest even if already done")
    args = ap.parse_args()

    if args.economic_survey:
        if not args.extract_only:
            fetch_economic_survey(args.exam, int(args.economic_survey))
        ingest(args.exam, "economic-survey", args.economic_survey)
    elif args.yojana:
        y, m = args.yojana.split("-")
        if not args.extract_only:
            fetch_yojana(args.exam, int(y), int(m))
        ingest(args.exam, "yojana", args.yojana)
    else:
        refresh(args.exam, extract_only=args.extract_only, force=args.force)
