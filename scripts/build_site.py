#!/usr/bin/env python3
"""
Static site generator for the RBI Grade B GA prep content.

Reads the weekly markdown summaries in ``data/summaries/*_to_*.md`` and their
matching practice MCQs in ``data/questions/generated/*-qs.json`` and renders a
minimalist static study site into ``docs/`` (served by GitHub Pages).

Stdlib only — no third-party dependencies. Run:

    python scripts/build_site.py
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import (  # noqa: E402
    DEFAULT_EXAM,
    NEWS_HEADLINE_DAYS,
    active_exams,
    current_cycle_start,
    cycle_label,
    cycle_start_for,
)
SUMMARIES_DIR = BASE_DIR / "data" / "summaries"
GEN_Q_DIR = BASE_DIR / "data" / "questions" / "generated"
NEWS_DIR = BASE_DIR / "data" / "news"
DOCS_DIR = BASE_DIR / "docs"
ASSETS_DIR = DOCS_DIR / "assets"
WEEKS_DIR = DOCS_DIR / "weeks"
EXAMS_PAGE_DIR = DOCS_DIR / "exams"
STATIC_PAGE_DIR = DOCS_DIR / "static"

STATIC_KIND_LABEL = {
    "economic-survey": "Economic Survey",
    "yojana": "Yojana",
}

SITE_TITLE = "Govt Exams · Weekly GA"
SITE_TAGLINE = "Current affairs for India's top government exams, distilled week by week."


def _exam_dirs(slug: str) -> tuple[Path, Path]:
    """Summary + generated-question directories for an exam.

    The default exam keeps the original flat layout for backward compatibility;
    every other exam is namespaced under its slug.
    """
    if slug == DEFAULT_EXAM:
        return SUMMARIES_DIR, GEN_Q_DIR
    return SUMMARIES_DIR / slug, GEN_Q_DIR / slug

WEEKLY_KEY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})$")
WEEK_NUM_RE = re.compile(r"Week\s+(\d+)", re.IGNORECASE)

MONTHS = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Section:
    title: str
    blocks: list[str] = field(default_factory=list)  # rendered HTML for each item


@dataclass
class Week:
    key: str
    start: date
    end: date
    number: int | None
    sections: list[Section]
    questions: list[dict]
    note: str | None  # trailing italic provenance note, if any

    @property
    def slug(self) -> str:
        return self.key

    @property
    def date_range(self) -> str:
        return f"{self._fmt(self.start)} – {self._fmt(self.end)}"

    @property
    def short_range(self) -> str:
        if self.start.year == self.end.year:
            return f"{self._fmt_short(self.start)} – {self._fmt_short(self.end)}, {self.end.year}"
        return f"{self.date_range}"

    @property
    def label(self) -> str:
        return f"Week {self.number:02d}" if self.number else self.key

    @property
    def topic_count(self) -> int:
        return sum(1 for s in self.sections if s.blocks)

    @staticmethod
    def _fmt(d: date) -> str:
        return f"{d.day:02d} {MONTHS[d.month]} {d.year}"

    @staticmethod
    def _fmt_short(d: date) -> str:
        return f"{d.day:02d} {MONTHS[d.month]}"


@dataclass
class Cycle:
    anchor: date
    label: str
    is_current: bool
    weeks: list[Week]  # newest-first for display; week.number set within cycle


@dataclass
class StaticSource:
    """A yearly/monthly reference source (e.g. Economic Survey) with its own
    section-wise summary and a dedicated quiz, shown separately from weeks."""
    slug: str
    kind: str
    key: str
    stem: str
    sections: list[Section]
    questions: list[dict]
    note: str | None

    @property
    def label(self) -> str:
        return f"{STATIC_KIND_LABEL.get(self.kind, self.kind.title())} {self.key}"

    @property
    def page_name(self) -> str:
        return f"{self.stem}.html"

    @property
    def topic_count(self) -> int:
        return sum(1 for s in self.sections if s.blocks)


@dataclass
class ExamGroup:
    slug: str
    cfg: dict
    weeks: list[Week] = field(default_factory=list)
    cycles: list[Cycle] = field(default_factory=list)
    sources: list[StaticSource] = field(default_factory=list)

    @property
    def current(self) -> "Cycle | None":
        return next((c for c in self.cycles if c.is_current), None)

    @property
    def archived(self) -> list[Cycle]:
        return [c for c in self.cycles if not c.is_current]

    @property
    def current_weeks(self) -> list[Week]:
        cur = self.current
        return cur.weeks if cur else []


def group_into_cycles(weeks: list[Week]) -> list[Cycle]:
    """Partition weeks into study cycles and renumber each cycle from Week 1."""
    cur_anchor = current_cycle_start()
    by_anchor: dict[date, list[Week]] = {}
    for w in weeks:
        by_anchor.setdefault(cycle_start_for(w.start), []).append(w)

    cycles: list[Cycle] = []
    for anchor in sorted(by_anchor, reverse=True):
        ascending = sorted(by_anchor[anchor], key=lambda w: w.start)
        for i, w in enumerate(ascending, 1):
            w.number = i  # renumber within the cycle (Week 1 = first week)
        cycles.append(
            Cycle(
                anchor=anchor,
                label=cycle_label(anchor),
                is_current=(anchor == cur_anchor),
                weeks=list(reversed(ascending)),  # newest-first
            )
        )
    return cycles


# ---------------------------------------------------------------------------
# Markdown → HTML (tailored to the summary format these files use)
# ---------------------------------------------------------------------------

_FIG_PATTERNS = [
    # currency amounts (₹, US$, $) — allow spaced thousands separators + scale words
    re.compile(
        r"(?:₹|US\$|US \$|\$)\s?\d(?:[\d,.\u00a0\u202f ]*\d)?"
        r"(?:\s?(?:cr(?:ore)?|lakh\s*crore|lakh|billion|trillion|bn|mn|MMT|MMPTA|MT))?",
        re.IGNORECASE,
    ),
    # percentages
    re.compile(r"\b\d[\d,.]*\s?%"),
    # explicit dates like 19 Jan 2026 or 19‑23 Jan 2026
    re.compile(r"\b\d{1,2}(?:[‐-―\-]\d{1,2})?\s+[A-Z][a-z]{2,8}\s+20\d{2}\b"),
]


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


_CHUNK_REF_RE = re.compile(
    r"\s*\((?:from |as per )?chunks?\b[^)]*\)"  # "(from chunk 7)", "(chunks 1-16)"
    r"|,?\s*\bchunks?\s+\d+",                    # bare ", chunk 4"
    re.IGNORECASE,
)


def _inline(text: str) -> str:
    """Render inline markdown for one line of summary text."""
    # Strip internal map-reduce artefacts that sometimes leak into the prose,
    # e.g. "(from chunk 7)" / "(chunks 1-16)" — these mean nothing to a reader.
    text = _CHUNK_REF_RE.sub("", text)
    # Pull out **bold** spans first using placeholders so escaping is clean.
    bold_spans: list[str] = []

    def _stash_bold(m: re.Match) -> str:
        bold_spans.append(m.group(1))
        return f"\x00B{len(bold_spans) - 1}\x00"

    staged = re.sub(r"\*\*(.+?)\*\*", _stash_bold, text)
    staged = _escape(staged)

    # Honour explicit line breaks the model emits inside cells/lines (<br>, <br/>,
    # <br />) — after escaping they appear as &lt;br&gt;, so map those back to real
    # breaks instead of leaking the literal tag text.
    staged = re.sub(r"&lt;br\s*/?&gt;", "<br>", staged)

    # Highlight figures (dates, money, percentages) for the data-led look.
    for pattern in _FIG_PATTERNS:
        staged = pattern.sub(lambda m: f'<span class="fig">{m.group(0)}</span>', staged)

    # Star marker for perennial high-priority items. The model emits ⭐, ★ or ☆
    # interchangeably — render them all as the one styled marker.
    staged = re.sub(r"[⭐★☆]", '<span class="star" aria-label="high priority">★</span>', staged)

    # Restore bold spans (escaped, with figure highlighting inside).
    def _restore_bold(m: re.Match) -> str:
        inner = _escape(bold_spans[int(m.group(1))])
        for pattern in _FIG_PATTERNS:
            inner = pattern.sub(lambda mm: f'<span class="fig">{mm.group(0)}</span>', inner)
        inner = re.sub(r"[⭐★☆]", '<span class="star">★</span>', inner)
        return f"<strong>{inner}</strong>"

    staged = re.sub(r"\x00B(\d+)\x00", _restore_bold, staged)
    return staged


_TABLE_SEP_RE = re.compile(r"^\|?[\s:|\-]+\|?$")  # | --- | --- | separator row


def _split_table_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _render_table(rows: list[str]) -> str:
    """Render a markdown pipe-table (the model sometimes emits these) as HTML.

    The model often emits *ragged* tables — some rows carry both columns, others
    fold everything into a single cell. Under a fixed layout a lone cell would be
    crushed into the first column, so a row with fewer cells than the header has
    its last cell span the remaining columns (colspan)."""
    if not rows:
        return ""
    header = _split_table_row(rows[0])
    ncols = max(1, len(header))
    body_start = 1
    if len(rows) > 1 and _TABLE_SEP_RE.match(rows[1].strip()):
        body_start = 2
    thead = "<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr>"
    body = []
    for r in rows[body_start:]:
        cells = _split_table_row(r)
        tds = []
        for j, c in enumerate(cells):
            # Last cell of a short row spans the columns it's missing.
            if j == len(cells) - 1 and len(cells) < ncols:
                span = ncols - len(cells) + 1
                tds.append(f'<td colspan="{span}">{_inline(c)}</td>')
            else:
                tds.append(f"<td>{_inline(c)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return (
        '<div class="table-wrap"><table class="data-table">'
        f"<thead>{thead}</thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _plain_digits(s: str) -> str:
    """Turn keycap emoji digits (1️⃣) and circled numerals (①) into plain "N."."""
    s = re.sub(r"([0-9])️?⃣", r"\1.", s)  # 1️⃣ → 1.
    s = re.sub(
        r"[①-⑳]",
        lambda m: f"{ord(m.group(0)) - 0x2460 + 1}.",  # ①..⑳ → 1...20.
        s,
    )
    return s.replace(" ", " ")  # narrow no-break space the model pairs with keycaps


def _normalize_summary_headings(markdown: str) -> str:
    """Smooth over the two heading styles the model alternates between.

    Some weeks open with a ``## PART 1 – GA SUMMARY`` title and then use ``###``
    for each topic (so every topic would collapse into one section as a sub-heading);
    others skip the banner and use ``##`` for topics directly. Drop any PART-1 banner
    in either heading or bold form, and — when that leaves no ``##`` topic sections but
    there are ``###`` ones — promote the ``###`` topics to ``##`` so both styles render
    as the same flat list of sections.
    """
    def _is_part1(line: str) -> bool:
        s = line.strip().lstrip("#").strip()
        return bool(re.match(r"^\*?\*?PART\s*1", s, re.IGNORECASE))

    # Unwrap headings the model occasionally wraps in bold, e.g. "**## Economy**".
    kept = [
        re.sub(r"^\s*\*\*\s*(#{1,6}\s+.*?)\s*\*\*\s*$", r"\1", l)
        for l in markdown.splitlines()
    ]
    # Some sections number sub-headings with keycap/circled emoji digits
    # (e.g. "### 1️⃣ Health"); others use plain "### 1. Health". Normalise the
    # emoji variants to plain "N." so numbering looks the same across sections.
    kept = [_plain_digits(l) for l in kept]
    kept = [l for l in kept if not _is_part1(l)]
    has_h2 = any(re.match(r"^##\s+\S", l.strip()) for l in kept)
    has_h3 = any(re.match(r"^###\s+\S", l.strip()) for l in kept)
    if not has_h2 and has_h3:
        kept = [re.sub(r"^(\s*)#{3,}(\s+\S)", r"\1##\2", l) for l in kept]
    return "\n".join(kept)


def parse_summary(markdown: str) -> tuple[list[Section], str | None]:
    markdown = _normalize_summary_headings(markdown)
    sections: list[Section] = []
    current: Section | None = None
    note: str | None = None
    pending_sub: str | None = None  # buffered ### heading, emitted only if content follows

    def ensure_section() -> Section:
        nonlocal current
        if current is None:
            current = Section(title="Highlights")
            sections.append(current)
        return current

    def flush_sub() -> None:
        nonlocal pending_sub
        if pending_sub and current is not None:
            current.blocks.append(pending_sub)
        pending_sub = None

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            i += 1
            continue
        # A markdown table: consume all consecutive pipe rows as one block.
        if line.startswith("|"):
            table_rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_rows.append(lines[i].strip())
                i += 1
            flush_sub()
            ensure_section().blocks.append(_render_table(table_rows))
            continue
        i += 1
        if line.startswith("# "):  # document H1 — handled by the page header
            continue
        if line in {"---", "----", "-----", "—"}:
            continue
        # Skip the generated-on line and the "PART 1" banner lines.
        if line.startswith("_Generated on") or line.startswith("_generated on"):
            continue
        if re.match(r"^\*?\*?PART\s*1", line, re.IGNORECASE):
            continue
        if line.startswith("## "):
            pending_sub = None  # discard sub-heading that had no content
            current = Section(title=line[3:].strip())
            sections.append(current)
            continue
        # Any deeper heading (###, ####, …) becomes a sub-heading. Buffer it so it's
        # only emitted when actual content follows (prevents blank headings) and so
        # ####/##### don't leak through as literal "#### ..." paragraph text.
        m_sub = re.match(r"^#{3,}\s+(.*)$", line)
        if m_sub:
            heading = m_sub.group(1).strip()
            pending_sub = (
                f'<h3 class="sub">{_inline(heading)}</h3>'
                if current is not None and heading else None
            )
            continue
        # Trailing provenance note rendered in italics, e.g. _All bullet points…_
        if line.startswith("_") and line.endswith("_") and len(line) > 2:
            note = _inline(line[1:-1].strip())
            continue
        # List item. The model uses -, *, •, – (en-dash) and — (em-dash)
        # interchangeably as bullet markers, and indents sub-bullets by two
        # spaces. Treat an indented item as nested under the previous one.
        m_li = re.match(r"^[-*•–—]\s+(.*)$", line)
        if m_li:
            flush_sub()
            indent = len(raw) - len(raw.lstrip(" "))
            cls = ' class="sub"' if indent >= 2 else ""
            ensure_section().blocks.append(
                f"<li{cls}>{_inline(m_li.group(1).strip())}</li>"
            )
            continue
        # Fallback: stray paragraph text.
        flush_sub()
        ensure_section().blocks.append(f"<p>{_inline(line)}</p>")

    # Drop empty sections (heading with no content).
    sections = [s for s in sections if s.blocks]
    return sections, note


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _parse_key(key: str) -> tuple[date, date] | None:
    m = WEEKLY_KEY_RE.match(key)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        end = datetime.strptime(m.group(2), "%Y-%m-%d").date()
    except ValueError:
        return None
    return start, end


def load_weeks(slug: str = DEFAULT_EXAM) -> list[Week]:
    weeks: list[Week] = []
    summaries_dir, gen_q_dir = _exam_dirs(slug)
    if not summaries_dir.exists():
        return weeks
    for path in sorted(summaries_dir.glob("*_to_*.md")):
        key = path.stem
        parsed = _parse_key(key)
        if not parsed:
            continue
        start, end = parsed

        text = path.read_text(encoding="utf-8")
        number = None
        first_line = text.splitlines()[0] if text.splitlines() else ""
        wm = WEEK_NUM_RE.search(first_line)
        if wm:
            number = int(wm.group(1))

        sections, note = parse_summary(text)

        questions: list[dict] = []
        qpath = gen_q_dir / f"{key}-qs.json"
        if qpath.exists():
            try:
                data = json.loads(qpath.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    questions = [q for q in data if isinstance(q, dict) and q.get("question")]
            except json.JSONDecodeError:
                pass

        if not sections:
            continue

        weeks.append(Week(key, start, end, number, sections, questions, note))

    # Most recent first.
    weeks.sort(key=lambda w: w.start, reverse=True)
    return weeks


def load_static_sources(slug: str) -> list[StaticSource]:
    """Load stored reference sources (Economic Survey, …) for an exam."""
    from pipeline.static_sources import list_sources

    out: list[StaticSource] = []
    for s in list_sources(slug):
        if not s["summary_md"].strip():
            continue
        sections, note = parse_summary(s["summary_md"])
        if not sections:
            continue
        out.append(
            StaticSource(
                slug=slug,
                kind=s["kind"],
                key=s["key"],
                stem=s["stem"],
                sections=sections,
                questions=s["questions"],
                note=note,
            )
        )
    return out


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _head(title: str, root: str, description: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>
<meta name="description" content="{_escape(description)}">
<link rel="icon" type="image/svg+xml" href="{root}assets/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{root}assets/style.css">
</head>"""


def _nav(root: str = "", active: str | None = None) -> str:
    exam_links = "".join(
        f'<a class="{"on" if slug == active else ""}" '
        f'href="{root}exams/{slug}.html">{_escape(cfg["name"])}</a>'
        for slug, cfg in active_exams().items()
    )
    return f"""<header class="nav reveal">
  <a class="brand" href="{root}index.html">Govt&nbsp;Exams<span class="brand-dot">·</span>GA</a>
  <nav class="nav-links">
    {exam_links}
    <a class="{"on" if active == "news" else ""}" href="{root}news.html">News</a>
  </nav>
</header>"""


def _footer(root: str) -> str:
    year = date.today().year
    return f"""<footer class="site-foot reveal">
  <div class="foot-grid">
    <div>
      <div class="foot-mark">Govt Exams · GA</div>
      <p class="foot-note">Weekly General Awareness for government exams, built from primary sources.</p>
    </div>
    <div class="foot-links">
      <a href="https://www.pib.gov.in/" target="_blank" rel="noopener">PIB releases ↗</a>
      <a href="https://www.rbi.org.in/" target="_blank" rel="noopener">RBI circulars ↗</a>
    </div>
  </div>
  <div class="foot-base">© {year} · A study companion. Not affiliated with the RBI or PIB.</div>
</footer>
<script src="{root}assets/app.js" defer></script>
</body>
</html>"""


def _week_row(group_slug: str, w: Week, root: str = "") -> str:
    return f"""
      <a class="week-row reveal" href="{root}weeks/{group_slug}/{w.slug}.html">
        <span class="wr-num">{w.label}</span>
        <span class="wr-title">{_escape(w.short_range)}</span>
        <span class="wr-meta">{w.topic_count} topics<span class="wr-sep">·</span>{len(w.questions)} questions</span>
        <span class="wr-arrow">→</span>
      </a>"""


def _exam_card(group: "ExamGroup") -> str:
    cur = group.current_weeks
    latest = cur[0] if cur else None
    if latest:
        meta = (
            f"{len(cur)} week{'s' if len(cur) != 1 else ''} · "
            f"{sum(len(w.questions) for w in cur):,} questions"
        )
        latest_line = f"Latest · {latest.label} · {_escape(latest.short_range)}"
    else:
        meta = "Coming soon"
        latest_line = "Content being generated"
    arch = f" · {sum(len(c.weeks) for c in group.archived)} archived" if group.archived else ""

    return f"""
      <a class="exam-card reveal" href="exams/{group.slug}.html">
        <h3>{_escape(group.cfg['name'])}</h3>
        <p class="ec-blurb">{_escape(group.cfg.get('blurb', ''))}</p>
        <div class="ec-foot">
          <span class="ec-meta">{meta}{arch}</span>
          <span class="ec-latest">{latest_line}</span>
        </div>
        <span class="ec-go">Open {_escape(group.cfg['name'])} →</span>
      </a>"""


def render_index(groups: "list[ExamGroup]") -> str:
    total_weeks = sum(len(g.current_weeks) for g in groups)
    total_q = sum(len(w.questions) for g in groups for w in g.current_weeks)
    cards = "".join(_exam_card(g) for g in groups)

    head = _head(SITE_TITLE, "", SITE_TAGLINE)
    return f"""{head}
<body>
{_nav("")}
<main>
  <section class="hero">
    <p class="eyebrow reveal">Government exams · General Awareness</p>
    <h1 class="display reveal">Current affairs,<br><em>distilled</em> week by week.</h1>
    <p class="lede reveal">{_escape(SITE_TAGLINE)} Each exam is summarised from its own
    relevant sources, then turned into exam-style practice tuned to that paper's GA pattern.</p>
    <div class="hero-meta reveal">
      <span><b>{len(groups)}</b> exams</span>
      <span><b>{total_weeks}</b> weeks this cycle</span>
      <span><b>{total_q:,}</b> practice questions</span>
    </div>
    <a class="scroll-cue reveal" href="#exams">Choose your exam ↓</a>
  </section>

  <section id="exams" class="exam-grid-wrap">
    <div class="section-head reveal">
      <span class="sh-num">01</span>
      <h2>Pick an exam</h2>
      <p>Each exam has its own page — weekly summaries and practice for the current cycle,
      with earlier cycles tucked into its archive.</p>
    </div>
    <div class="exam-grid">{cards}
    </div>
  </section>

  <section id="about" class="about">
    <div class="section-head reveal">
      <span class="sh-num">02</span>
      <h2>How it works</h2>
    </div>
    <div class="about-grid">
      <div class="about-card reveal">
        <span class="ac-num">i</span>
        <h3>Exam-specific sources</h3>
        <p>Each exam is built from the notifications it actually draws on — PIB and RBI
        for RBI Grade B, broad PIB and the Economic Survey for UPSC, and so on.</p>
      </div>
      <div class="about-card reveal">
        <span class="ac-num">ii</span>
        <h3>Descriptive summaries</h3>
        <p>The raw material is condensed into explanatory notes — what happened, the
        figures that matter, and why it is relevant — grouped by GA topic.</p>
      </div>
      <div class="about-card reveal">
        <span class="ac-num">iii</span>
        <h3>Pattern-tuned practice</h3>
        <p>Every week ships exam-style MCQs whose topic and question-type mix is weighted
        from that exam's recent papers, attempted in the browser with instant scoring.</p>
      </div>
    </div>
  </section>
</main>
{_footer("")}"""


def render_exam_page(group: "ExamGroup") -> str:
    cfg = group.cfg
    cur = group.current_weeks
    cur_cycle = group.current

    if cur:
        # The exam page lives at /exams/<slug>.html, so week links must go up one
        # level ("../weeks/...") — a bare "weeks/..." would resolve to /exams/weeks/.
        rows = "".join(_week_row(group.slug, w, "../") for w in cur)
        main_body = f'<div class="week-list">{rows}\n    </div>'
    else:
        main_body = (
            '<div class="exam-empty reveal">Weekly summaries and practice for this '
            'exam are being generated — check back soon.</div>'
        )

    cycle_label_txt = cur_cycle.label if cur_cycle else cycle_label(current_cycle_start())
    count_line = (
        f"{len(cur)} week{'s' if len(cur) != 1 else ''} · "
        f"{sum(len(w.questions) for w in cur):,} questions"
        if cur
        else "Coming soon"
    )

    archive_html = ""
    if group.archived:
        blocks = []
        for c in group.archived:
            arows = "".join(_week_row(group.slug, w, "../") for w in c.weeks)
            blocks.append(
                f"""
      <details class="archive-cycle">
        <summary>{c.label} cycle <span class="ac-count">{len(c.weeks)} weeks</span></summary>
        <div class="week-list">{arows}
        </div>
      </details>"""
            )
        archive_html = f"""
  <section id="archive" class="archive">
    <div class="section-head reveal">
      <span class="sh-num">↧</span>
      <h2>Archive</h2>
      <p>Earlier study cycles, kept for reference. Each new year these roll over automatically.</p>
    </div>
    {''.join(blocks)}
  </section>"""

    head = _head(
        f"{cfg['name']} · Weekly GA — {SITE_TITLE}",
        "../",
        f"{cfg['name']} weekly General Awareness summaries and practice MCQs.",
    )
    return f"""{head}
<body>
{_nav("../", active=group.slug)}
<main>
  <section class="hero exam-hero">
    <p class="eyebrow reveal">{_escape(cfg['name'])} · General Awareness</p>
    <h1 class="display reveal">{_escape(cfg['name'])}</h1>
    <p class="lede reveal">{_escape(cfg.get('blurb', ''))}</p>
    <div class="hero-meta reveal">
      <span><b>{cycle_label_txt}</b> cycle</span>
      <span>{count_line}</span>
    </div>
  </section>

  <section id="index" class="index">
    <div class="section-head reveal">
      <span class="sh-num">01</span>
      <h2>This cycle</h2>
      <p>Most recent first. Each week pairs a descriptive summary with practice MCQs.</p>
    </div>
    {main_body}
  </section>
{_sources_section(group, "../")}
{archive_html}
</main>
{_footer("../")}"""


def _render_section(index: int, section: Section) -> str:
    # Separate list blocks (wrapped in <ul>) from standalone blocks. Items marked
    # <li class="sub"> are nested into a child <ul> inside the preceding item.
    parts: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        items: list[str] = []
        for li in buffer:
            if li.startswith('<li class="sub">'):
                child = li[len('<li class="sub">'):-len("</li>")]
                if items:
                    parent = items[-1][:-len("</li>")]  # reopen the parent <li>
                    if parent.endswith("</ul>"):
                        parent = parent[:-len("</ul>")] + f"<li>{child}</li></ul>"
                    else:
                        parent += f'<ul class="facts"><li>{child}</li></ul>'
                    items[-1] = parent + "</li>"
                else:  # orphan sub-bullet with no parent — promote to top level
                    items.append(f"<li>{child}</li>")
            else:
                items.append(li)
        parts.append(f'<ul class="facts">{"".join(items)}</ul>')
        buffer.clear()

    for block in section.blocks:
        if block.startswith("<li"):
            buffer.append(block)
        else:
            flush()
            parts.append(block)
    flush()

    return f"""
    <section class="topic reveal">
      <div class="topic-head">
        <span class="topic-num">{index:02d}</span>
        <h2>{_escape(section.title)}</h2>
      </div>
      <div class="topic-body">{''.join(parts)}</div>
    </section>"""


def render_week(
    week: Week,
    prev_w: Week | None,
    next_w: Week | None,
    cfg: dict,
) -> str:
    exam_name = cfg["name"]
    sections_html = "".join(
        _render_section(i, s) for i, s in enumerate(week.sections, 1)
    )

    note_html = (
        f'<p class="provenance reveal">{week.note}</p>' if week.note else ""
    )

    quiz_html = ""
    if week.questions:
        quiz_html = f"""
  <section id="practice" class="practice">
    <div class="section-head reveal">
      <span class="sh-num">★</span>
      <h2>Practice</h2>
      <p>{len(week.questions)} exam-style questions from this week. Pick an answer to check it.</p>
    </div>
    <div class="quiz reveal" id="quiz">
      <div class="quiz-bar">
        <div class="quiz-progress"><span id="quiz-progress-fill"></span></div>
        <div class="quiz-score" id="quiz-score">0 / {len(week.questions)} answered</div>
        <button class="quiz-reset" id="quiz-reset" type="button">Reset</button>
      </div>
      <div id="quiz-questions"></div>
    </div>
  </section>"""

    # Prev / next navigation (chronological).
    pager = []
    if next_w is not None:  # newer week
        pager.append(
            f'<a class="pager newer" href="{next_w.slug}.html"><span>Newer</span>{next_w.label}</a>'
        )
    else:
        pager.append('<span class="pager disabled"><span>Newer</span>—</span>')
    if prev_w is not None:  # older week
        pager.append(
            f'<a class="pager older" href="{prev_w.slug}.html"><span>Older</span>{prev_w.label}</a>'
        )
    else:
        pager.append('<span class="pager disabled"><span>Older</span>—</span>')

    quiz_data = json.dumps(week.questions, ensure_ascii=False)

    root = "../../"
    head = _head(
        f"{exam_name} · {week.label} · {week.short_range} — {SITE_TITLE}",
        root,
        f"{exam_name} weekly GA summary and practice MCQs for {week.date_range}.",
    )
    return f"""{head}
<body class="week-page">
{_nav(root, active=cfg['slug'])}
<main class="week-main">
  <a class="back-link reveal" href="{root}exams/{cfg['slug']}.html">← All {_escape(exam_name)} weeks</a>
  <header class="week-head">
    <p class="eyebrow reveal">{_escape(exam_name)} · {week.label} · General Awareness</p>
    <h1 class="display reveal">{_escape(week.short_range)}</h1>
    <p class="lede reveal">{week.topic_count} topics summarised · {len(week.questions)} practice questions</p>
  </header>

  <div class="summary">{sections_html}
    {note_html}
  </div>
{quiz_html}

  <nav class="week-pager reveal">{''.join(pager)}</nav>
</main>
<script id="quiz-data" type="application/json">{quiz_data}</script>
{_footer(root)}"""


def _source_card(source: StaticSource, root: str) -> str:
    kind_label = STATIC_KIND_LABEL.get(source.kind, source.kind.title())
    return f"""
      <a class="src-card reveal" href="{root}static/{source.slug}/{source.page_name}">
        <span class="src-kind">{_escape(kind_label)}</span>
        <span class="src-key">{_escape(source.key)}</span>
        <span class="src-meta">{source.topic_count} sections · {len(source.questions)} questions</span>
        <span class="src-go">Open →</span>
      </a>"""


def _sources_section(group: "ExamGroup", root: str) -> str:
    if not group.sources:
        return ""
    cards = "".join(_source_card(s, root) for s in group.sources)
    return f"""
  <section id="reference" class="reference">
    <div class="section-head reveal">
      <span class="sh-num">02</span>
      <h2>Reference sources</h2>
      <p>Foundational, exam-critical reading (e.g. the Economic Survey) — each with its
      own section-wise summary and a dedicated quiz, kept separate from the weekly papers.</p>
    </div>
    <div class="src-grid">{cards}
    </div>
  </section>"""


def render_static_page(source: StaticSource, cfg: dict) -> str:
    exam_name = cfg["name"]
    sections_html = "".join(
        _render_section(i, s) for i, s in enumerate(source.sections, 1)
    )
    note_html = (
        f'<p class="provenance reveal">{source.note}</p>' if source.note else ""
    )

    quiz_html = ""
    if source.questions:
        quiz_html = f"""
  <section id="practice" class="practice">
    <div class="section-head reveal">
      <span class="sh-num">★</span>
      <h2>Practice</h2>
      <p>{len(source.questions)} exam-style questions drawn from {_escape(source.label)}.
      Pick an answer to check it.</p>
    </div>
    <div class="quiz reveal" id="quiz">
      <div class="quiz-bar">
        <div class="quiz-progress"><span id="quiz-progress-fill"></span></div>
        <div class="quiz-score" id="quiz-score">0 / {len(source.questions)} answered</div>
        <button class="quiz-reset" id="quiz-reset" type="button">Reset</button>
      </div>
      <div id="quiz-questions"></div>
    </div>
  </section>"""

    quiz_data = json.dumps(source.questions, ensure_ascii=False)
    root = "../../"
    head = _head(
        f"{exam_name} · {source.label} — {SITE_TITLE}",
        root,
        f"{exam_name} {source.label}: section-wise summary and a dedicated practice quiz.",
    )
    return f"""{head}
<body class="week-page">
{_nav(root, active=cfg['slug'])}
<main class="week-main">
  <a class="back-link reveal" href="{root}exams/{cfg['slug']}.html">← All {_escape(exam_name)} material</a>
  <header class="week-head">
    <p class="eyebrow reveal">{_escape(exam_name)} · Reference source</p>
    <h1 class="display reveal">{_escape(source.label)}</h1>
    <p class="lede reveal">{source.topic_count} sections summarised · {len(source.questions)} dedicated questions</p>
  </header>

  <div class="summary">{sections_html}
    {note_html}
  </div>
{quiz_html}
</main>
<script id="quiz-data" type="application/json">{quiz_data}</script>
{_footer(root)}"""


# ---------------------------------------------------------------------------
# News digest page
# ---------------------------------------------------------------------------

EXAM_ABBR = {
    "RBI Grade B": "RBI-B",
    "SEBI Grade A": "SEBI-A",
    "NABARD Grade A": "NABARD-A",
    "UPSC / Banking": "UPSC/Bank",
}


def load_news() -> dict | None:
    path = NEWS_DIR / "latest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("items"):
        return None
    return data


def _fmt_news_date(raw: str | None) -> str:
    if not raw:
        return "—"
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
        return f"{d.day:02d} {MONTHS[d.month]}"
    except ValueError:
        return raw


def _exam_chip(exam: str) -> str:
    abbr = EXAM_ABBR.get(exam, exam)
    return f'<span class="chip" data-exam="{_escape(exam)}">{_escape(abbr)}</span>'


def _news_card(item: dict) -> str:
    exams = item.get("exams") or []
    exam_attr = "|".join(exams)
    chips = "".join(_exam_chip(e) for e in exams)
    return f"""
      <article class="news-card reveal" data-exams="{_escape(exam_attr)}">
        <div class="nc-meta">
          <span class="nc-date">{_fmt_news_date(item.get('date'))}</span>
          <span class="nc-source">{_escape(item.get('source', ''))}</span>
          <span class="nc-topic">{_escape(item.get('topic', ''))}</span>
        </div>
        <h3 class="nc-title">{_escape(item.get('title', ''))}</h3>
        <p class="nc-summary">{_escape(item.get('summary', ''))}</p>
        <div class="nc-foot">
          <div class="nc-chips">{chips}</div>
          <span class="nc-cite">via {_escape(item.get('source', 'source'))}</span>
        </div>
      </article>"""


def _split_news(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition items into (today + yesterday) and (rest of the week)."""
    cutoff = date.today() - timedelta(days=NEWS_HEADLINE_DAYS)
    recent, earlier = [], []
    for it in items:
        raw = it.get("date")
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        except ValueError:
            d = None
        # Undated items are treated as fresh (matches the digest's lookback rule).
        if d is None or d >= cutoff:
            recent.append(it)
        else:
            earlier.append(it)
    return recent, earlier


def render_news(news: dict) -> str:
    items = news.get("items", [])
    exams = news.get("exams", list(EXAM_ABBR.keys()))
    sources = news.get("sources", [])
    generated = news.get("generated", "")[:10]

    recent, earlier = _split_news(items)

    filters = '<button class="filter active" data-exam="all" type="button">All</button>'
    filters += "".join(
        f'<button class="filter" data-exam="{_escape(e)}" type="button">{_escape(e)}</button>'
        for e in exams
    )

    recent_cards = "".join(_news_card(it) for it in recent) or (
        '<p class="news-empty">No fresh items in the last two days — '
        'check <em>In this week</em> below.</p>'
    )

    if earlier:
        earlier_cards = "".join(_news_card(it) for it in earlier)
        week_block = f"""
    <details class="news-week reveal">
      <summary>In this week <span class="nw-count">{len(earlier)}</span></summary>
      <div class="news-list" id="news-week-list">{earlier_cards}
      </div>
    </details>"""
    else:
        week_block = ""

    head = _head(
        f"In the news — {SITE_TITLE}",
        "",
        "Exam-relevant business & economy news, cited and tagged by exam.",
    )
    return f"""{head}
<body class="news-page">
{_nav("")}
<main>
  <section class="hero hero-tight">
    <p class="eyebrow reveal">Current affairs · cited sources</p>
    <h1 class="display reveal">In the<br><em>news</em>.</h1>
    <p class="lede reveal">Concise, exam-focused summaries of the business &amp; economy news that
    matters — each written in-house, tagged with the exams it's relevant to, and credited to
    the outlet that reported it. The headline list shows the last two days; the rest of the
    week is tucked into <em>In this week</em>.</p>
    <div class="hero-meta reveal">
      <span><b>{len(recent)}</b> today &amp; yesterday</span>
      <span><b>{len(earlier)}</b> earlier this week</span>
      <span>{_escape(', '.join(sources))}</span>
      <span class="hero-latest">Updated {_escape(generated)}</span>
    </div>
  </section>

  <section class="news">
    <div class="news-filters reveal" id="news-filters">{filters}</div>
    <div class="news-list" id="news-list">{recent_cards}
    </div>{week_block}
    <p class="news-disclaimer reveal">Summaries are written in-house from facts reported in the
    publishers' public RSS feeds and are credited to the originating outlet. Underlying facts
    belong to their reporting; no full articles are reproduced.</p>
  </section>
</main>
{_footer("")}"""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build() -> None:
    groups: list[ExamGroup] = []
    for slug, cfg in active_exams().items():
        weeks = load_weeks(slug)
        groups.append(
            ExamGroup(
                slug=slug,
                cfg=cfg,
                weeks=weeks,
                cycles=group_into_cycles(weeks),
                sources=load_static_sources(slug),
            )
        )

    total_weeks = sum(len(g.weeks) for g in groups)
    total_sources = sum(len(g.sources) for g in groups)
    if total_weeks == 0 and total_sources == 0:
        print("No weekly summaries or reference sources found — nothing to build.")
        return

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    EXAMS_PAGE_DIR.mkdir(parents=True, exist_ok=True)
    # Fresh weeks + exams + static dirs each build so removed content doesn't linger.
    for d in (WEEKS_DIR, EXAMS_PAGE_DIR, STATIC_PAGE_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    (DOCS_DIR / "index.html").write_text(render_index(groups), encoding="utf-8")

    for group in groups:
        # Per-exam landing page (always built, even when empty → "coming soon").
        (EXAMS_PAGE_DIR / f"{group.slug}.html").write_text(
            render_exam_page(group), encoding="utf-8"
        )
        # Reference-source pages (Economic Survey, …): own summary + dedicated quiz.
        if group.sources:
            exam_static_dir = STATIC_PAGE_DIR / group.slug
            exam_static_dir.mkdir(parents=True, exist_ok=True)
            for source in group.sources:
                (exam_static_dir / source.page_name).write_text(
                    render_static_page(source, group.cfg), encoding="utf-8"
                )
        if not group.weeks:
            continue
        # Week detail pages: build every week (current + archived). Chronological
        # neighbours are taken within each cycle so the pager stays inside a cycle.
        exam_weeks_dir = WEEKS_DIR / group.slug
        exam_weeks_dir.mkdir(parents=True, exist_ok=True)
        for cycle in group.cycles:
            weeks = cycle.weeks  # newest-first
            for i, week in enumerate(weeks):
                newer = weeks[i - 1] if i > 0 else None
                older = weeks[i + 1] if i + 1 < len(weeks) else None
                page = render_week(week, prev_w=older, next_w=newer, cfg=group.cfg)
                (exam_weeks_dir / f"{week.slug}.html").write_text(page, encoding="utf-8")

    news = load_news()
    if news:
        (DOCS_DIR / "news.html").write_text(render_news(news), encoding="utf-8")

    write_assets()
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    summary_bits = ", ".join(
        f"{g.cfg['name']}: {len(g.current_weeks)} this cycle"
        f"{f' (+{sum(len(c.weeks) for c in g.archived)} archived)' if g.archived else ''}"
        for g in groups
    )
    print(
        f"Built {len(groups)} exam pages + {total_weeks} week pages + "
        f"{total_sources} reference-source pages → {DOCS_DIR.relative_to(BASE_DIR)}"
    )
    print(f"  {summary_bits}")
    week_q = sum(len(w.questions) for g in groups for w in g.weeks)
    src_q = sum(len(s.questions) for g in groups for s in g.sources)
    print(f"  Practice questions: {week_q:,} weekly + {src_q:,} reference = {week_q + src_q:,}")
    if news:
        print(f"  News digest: {news['count']} items → docs/news.html")
    else:
        print("  News digest: none found (run pipeline/news_runner.py) — news.html skipped")


def write_assets() -> None:
    (ASSETS_DIR / "style.css").write_text(CSS, encoding="utf-8")
    (ASSETS_DIR / "app.js").write_text(JS, encoding="utf-8")
    (ASSETS_DIR / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")


# CSS and JS live at the bottom for readability; defined in companion module.
from _site_assets import CSS, FAVICON_SVG, JS  # noqa: E402  (build-time asset strings)


if __name__ == "__main__":
    build()
