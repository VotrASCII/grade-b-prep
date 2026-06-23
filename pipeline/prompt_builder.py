import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import DEFAULT_EXAM, EXAMS  # noqa: E402


def _taxonomy_path(exam: str) -> Path:
    cfg = EXAMS.get(exam) or EXAMS[DEFAULT_EXAM]
    path = BASE_DIR / cfg["taxonomy"]
    if path.exists():
        return path
    # Backward-compat: fall back to the original single-exam taxonomy.
    return BASE_DIR / "data" / "patterns" / "taxonomy.json"


def _load_taxonomy(exam: str) -> dict:
    return json.loads(_taxonomy_path(exam).read_text())


def build_prompt(
    content: str,
    period_name: str,
    year: int | str | None = None,
    exam: str = DEFAULT_EXAM,
    static_block: str | None = None,
    static_quota: int = 0,
) -> str:
    period_label = f"{period_name} {year}".strip() if year else period_name
    taxonomy = _load_taxonomy(exam)
    profile = taxonomy["prompt_profile"]

    coach_role = profile["coach_role"]
    source_label = profile["source_label"]
    total_questions = profile["total_questions"]
    options_count = profile.get("options_count", 5)

    # ── summary section headings ───────────────────────────────────────────
    sections_str = "\n".join(f"## {s}" for s in profile["summary_sections"])

    # ── hot subtopics from every topic bucket ──────────────────────────────
    hot_subtopics = []
    for topic_data in taxonomy["topic_weights"].values():
        hot_subtopics.extend(topic_data.get("hot_subtopics", []))
    hot_subtopics_str = "\n".join(f"  • {s}" for s in hot_subtopics)

    # ── style writing rules + trap patterns ────────────────────────────────
    style_rules = []
    for style_name, style_data in taxonomy["question_style_distribution"].items():
        style_rules.append(
            f"  [{style_name.upper().replace('_', ' ')}]\n"
            f"  How to write: {style_data['writing_instructions']}\n"
            f"  Trap to use:  {style_data['trap_pattern']}"
        )
    style_rules_str = "\n\n".join(style_rules)

    # ── perennial must-flag topics ─────────────────────────────────────────
    must_prepare = taxonomy["high_priority_topics_for_current_year"]["must_prepare"]
    must_prepare_str = "\n".join(f"  • {t}" for t in must_prepare)

    # ── GA topic + style distribution (empirical weightage per exam) ───────
    topic_dist_str = "\n".join(
        f"  • {label:<34}→ {count} questions"
        for label, count in profile["topic_distribution"]
    )
    style_dist_str = "\n".join(
        f"  • {label:<46}→ {count} questions"
        for label, count in profile["style_distribution"]
    )

    # ── option-E patterns (only meaningful for 5-option exams) ─────────────
    option_e_patterns = ", ".join(
        taxonomy["critical_format_rules"].get("option_e_patterns", [])
    )

    # ── critical format rules, numbered, with option-E substitution ────────
    format_rules_str = "\n".join(
        f"{i}. {rule.replace('{option_e_patterns}', option_e_patterns)}"
        for i, rule in enumerate(profile["format_rules"], 1)
    )

    option_letters = ", ".join(["A", "B", "C", "D", "E"][:options_count])
    option_line_example = "  ".join(
        f"{letter}. [option]" for letter in ["A", "B", "C", "D", "E"][:options_count]
    )

    # ── optional static-source focus (Economic Survey / Yojana) ────────────
    static_section = ""
    if static_block and static_quota > 0:
        static_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STATIC SOURCE FOCUS — Economic Survey / Yojana
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Of the {total_questions} MCQs in PART 2, build EXACTLY {static_quota} from the material
below (the rest from the weekly raw content). Place each of these {static_quota}
questions under its natural topic so the TOPIC DISTRIBUTION above still holds — they
REPLACE an equal number of current-affairs questions in the same topics, they are not
extra. Every fact may back at most ONE question; do not repeat a fact across questions.
This material has not been used in earlier weeks, so none of these questions should
duplicate anything previously asked.

{static_block}
"""


    prompt = f"""\
You are an expert {coach_role}.
Below is raw content from {source_label} for {period_label}.
Do two things strictly as instructed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 1 — GA SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Write a structured, richly descriptive markdown summary of the most exam-relevant
events from this period. This is study material a candidate will read to understand
the news — not just memorise it — so write in clear explanatory prose, not terse
fragments.

Use exactly these sections, each as a level-2 markdown heading (## ), in this order:
{sections_str}

Rules for the summary:
- Begin PART 1 directly with the first "## " section heading. Do NOT add a "PART 1"
  title line, a preamble, or any introductory paragraph before the first section.
- Use "## " (level-2) for every section heading exactly as listed above. Do NOT use
  "###" or deeper, do NOT nest sections, and do NOT add sections of your own.
- Do NOT insert horizontal rules ("---") between or within sections.
- Use the ⭐ emoji to flag priority items — never "★" or "☆", and no separate legend line.
- 50 bullet points per section max
- Each bullet is 2–4 complete sentences. Start with the headline fact in **bold**,
  then explain it: what was announced, the key figures/dates/names, WHO is involved
  (issuing body, ministry, regulator), and WHY it matters for the exam or the wider
  economy (the context, purpose, or significance). Avoid one-line stubs.
- Any abbreviation must be expanded the first time it appears
  (e.g. FSR → Financial Stability Report), then the short form may be reused.
- Keep every specific number, date, rate, amount, place and proper name exactly as
  issued — these are what questions are built from. Do not round or drop them.
- Where a development continues or revises an earlier scheme/policy, say so briefly
  so the reader understands the through-line.
- Flag with ⭐ any item that covers a PERENNIAL HIGH-PRIORITY topic from this list:
{must_prepare_str}

When summarizing, pay special attention to content covering:
{hot_subtopics_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 2 — {total_questions} PRACTICE MCQs ({profile['exam_name']} Style)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TOPIC DISTRIBUTION — generate exactly this many questions per topic
(weightage derived from this exam's recent GA papers):
{topic_dist_str}

QUESTION STYLE DISTRIBUTION — generate this many of each style:
{style_dist_str}

HOW TO WRITE EACH STYLE (follow these exactly):
{style_rules_str}

CRITICAL FORMAT RULES — non-negotiable:
{format_rules_str}

OUTPUT FORMAT — strictly follow this for every question (use exactly {options_count} options, {option_letters}):
Q[N]. [Question text]
{option_line_example}
Answer: [letter]

VALID PART 2 EXAMPLE:
{profile['example_question']}

Before finalizing PART 2, verify that all {total_questions} questions have an Answer: line. Do not output a question without its answer.
{static_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAW CONTENT — {period_label.upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{content}"""

    return prompt
