import json
from pathlib import Path


def build_prompt(content: str, period_name: str, year: int | str | None = None) -> str:
    period_label = f"{period_name} {year}".strip() if year else period_name
    taxonomy_path = Path("data/patterns/taxonomy.json")
    taxonomy = json.loads(taxonomy_path.read_text())

    # ── hot subtopics from every topic bucket ──────────────────────────────
    hot_subtopics = []
    for topic_data in taxonomy["topic_weights"].values():
        hot_subtopics.extend(topic_data["hot_subtopics"])
    hot_subtopics_str = "\n".join(f"  • {s}" for s in hot_subtopics)

    # ── style writing rules + trap patterns ───────────────────────────────
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

    # ── option E patterns ──────────────────────────────────────────────────
    option_e_patterns = ", ".join(
        taxonomy["critical_format_rules"]["option_e_patterns"]
    )

    prompt = f"""\
You are an expert RBI Grade B Phase 1 General Awareness exam coach.
Below is raw content from PIB press releases and RBI circulars for {period_label}.
Do two things strictly as instructed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 1 — GA SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Write a structured markdown summary of the most exam-relevant events from this period.

Use exactly these sections:
## RBI & Monetary Policy
## Banking & Financial Sector
## Government Schemes & Budget
## Economy & Trade
## International Affairs & Organizations
## Awards, Rankings & Appointments
## Sports / Environment / Science & Tech

Rules for the summary:
- 50 bullet points per section max
- Any abbreviations must be expanded at least once (e.g. FSR → Financial Stability Report)
- Each bullet must include a specific number, date, or name where relevant
- Flag with ⭐ any item that covers a PERENNIAL HIGH-PRIORITY topic from this list:
{must_prepare_str}

When summarizing, pay special attention to content covering:
{hot_subtopics_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PART 2 — 80 PRACTICE MCQs (RBI Grade B Phase 1 Style)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TOPIC DISTRIBUTION — generate exactly this many questions per topic:
  • RBI & Monetary Policy         → 21 questions
  • Banking & Financial Sector    → 16 questions
  • Government Schemes & Budget   → 11 questions
  • International Affairs         → 11 questions
  • Economy & Trade Data          → 11 questions
  • Awards / Rankings             → 5 question
  • Sports / Environment / Sci    → 5 question

QUESTION STYLE DISTRIBUTION — generate this many of each style:
  • Fill in the blank (exact numbers/names)      → 21 questions
  • Direct factual (who/what/where)              → 16 questions
  • Statement correct/incorrect (2-3 stmts)      → 16 questions
  • NOT / odd one out                            → 11 questions
  • Multiple statements select combination       → 11 questions
  • Match the following                          → 5 question

HOW TO WRITE EACH STYLE (follow these exactly):
{style_rules_str}

CRITICAL FORMAT RULES — non-negotiable:
1. ALWAYS use exactly 5 options: A, B, C, D, E — NEVER use 4
2. Option E must frequently be one of: {option_e_patterns}
3. Fill-in-blank with numbers: options must be close (e.g. 5.7%, 5.9%, 6.0%, 6.1%, 6.2%) — never obvious gaps
4. Start current affairs questions with 'Recently,'
5. Reference specific reports in stems: RBI Annual Report, FSR, Union Budget, SPF, etc.
6. Wrong options must be plausible entities from the same category — never random
7. 'All of the above' and 'None of the above' as correct answers should appear at least 2-3 times across the 80 questions
8. Do NOT invent facts — base every question strictly on the raw content below
9. Do NOT use markdown bold/italic formatting in PART 2. No ** markers in questions or options.
10. Every question MUST include exactly one answer line immediately after the options.

OUTPUT FORMAT — strictly follow this for every question:
Q[N]. [Question text]
A. [option]  B. [option]  C. [option]  D. [option]  E. [option]
Answer: [letter]

VALID PART 2 EXAMPLE:
Q1. Recently, which institution released the Monetary Policy Statement?
A. Reserve Bank of India  B. Ministry of Finance  C. SEBI  D. NABARD  E. None of the above
Answer: A

Before finalizing PART 2, verify that all 80 questions have an Answer: line. Do not output a question without its answer.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RAW CONTENT — {period_label.upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{content}"""

    return prompt
