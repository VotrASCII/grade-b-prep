"""
Heuristic validation of generated GA summaries.

Called after split_response/clean_summary_markdown, before saving to disk.
Hard errors (severity="error") raise ValueError so run_week_with_retry
re-attempts generation. Warnings are printed but don't block.
"""
import re
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Issue types
# ---------------------------------------------------------------------------

Severity = Literal["error", "warn"]


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    line: int | None = None


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# LLM meta-commentary that must never appear in a published summary
_META_RE = re.compile(
    r"Not relevant\.?\s*Need to rewrite"
    r"|Let'?s produce"
    r"|Let me now (?:write|generate|produce)"
    r"|omitted for brevity"
    r"|would continue to cover"
    r"|Remaining .{0,30} items would continue"
    r"|continues with bullet points"
    r"|total bullets per section"
    r"|I'?ll now generate"
    r"|rewrite summary accordingly"
    r"|(?:concise )?bullets for each section",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def validate_summary(summary: str) -> list[Issue]:
    issues: list[Issue] = []
    lines = summary.splitlines()

    # 1. Empty / too-short output
    if len(summary.strip()) < 300:
        issues.append(Issue("error", "EMPTY", f"Summary only {len(summary.strip())} chars — generation likely failed"))

    # 2. PART 2 marker leaked into summary
    for i, line in enumerate(lines, 1):
        if re.match(r"^#{0,6}\s*PART\s*2", line.strip(), re.IGNORECASE):
            issues.append(Issue("error", "PART2_LEAKED", f"PART 2 marker found in summary body", i))

    # 3. LLM meta-commentary / restart artefacts
    for i, line in enumerate(lines, 1):
        if _META_RE.search(line):
            issues.append(Issue("error", "META_TEXT", f"LLM meta-text: {line.strip()[:80]}", i))

    # 4. Duplicate section headings
    seen_headings: dict[str, int] = {}
    for i, line in enumerate(lines, 1):
        if line.startswith("## "):
            title = line[3:].strip()
            if title in seen_headings:
                issues.append(Issue(
                    "error", "DUP_SECTION",
                    f"Duplicate section '{title}' (first at line {seen_headings[title]})", i,
                ))
            else:
                seen_headings[title] = i

    # 5. Bare heading marker (## with no title)
    for i, line in enumerate(lines, 1):
        if re.match(r"^#{1,6}\s*$", line.strip()):
            issues.append(Issue("warn", "BARE_HEADING", "Heading marker with no title", i))

    # 6. Unclosed bold spans (odd ** count on a line)
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s and len(re.findall(r"\*\*", s)) % 2 != 0:
            issues.append(Issue("warn", "UNCLOSED_BOLD", f"Odd ** count: {s[:70]}", i))

    # 7. Bolded bullet symbol (**•**)
    for i, line in enumerate(lines, 1):
        if re.search(r"\*\*[•·]\*\*", line):
            issues.append(Issue("warn", "BOLD_BULLET", "Bolded bullet symbol **•** found", i))

    # 8. Truncated bold content
    for i, line in enumerate(lines, 1):
        if re.search(r"\.\.\.\*\*|\*\*\.\.\.", line):
            issues.append(Issue("warn", "TRUNCATED", f"Truncated bold span: {line.strip()[:70]}", i))

    # 9. Too few sections
    section_count = len(seen_headings)
    if 0 < section_count < 4:
        issues.append(Issue("warn", "FEW_SECTIONS", f"Only {section_count} section(s) found (expected ≥ 4)"))

    return issues


# ---------------------------------------------------------------------------
# Public helper — called by daily_runner
# ---------------------------------------------------------------------------

def check_summary(summary: str, label: str = "") -> None:
    """
    Validate summary and print findings.

    Raises ValueError listing all hard errors so run_week_with_retry will
    re-attempt LLM generation. Warnings are printed but don't block.
    """
    issues = validate_summary(summary)
    tag = f"[{label}] " if label else ""
    errors = [x for x in issues if x.severity == "error"]
    warnings = [x for x in issues if x.severity == "warn"]

    for w in warnings:
        loc = f":{w.line} " if w.line else ""
        print(f"  {tag}[VALIDATOR WARN] {loc}{w.code}: {w.message}")

    if errors:
        lines = [f"  {tag}[VALIDATOR] {len(errors)} hard error(s) — will retry generation:"]
        for e in errors:
            loc = f":{e.line} " if e.line else ""
            lines.append(f"    ✗ {loc}{e.code}: {e.message}")
        msg = "\n".join(lines)
        print(msg)
        raise ValueError(f"Summary failed validation ({len(errors)} error(s)): " +
                         "; ".join(f"{e.code}:{e.message[:50]}" for e in errors))

    if not issues:
        print(f"  {tag}[VALIDATOR] ✓ summary clean")
