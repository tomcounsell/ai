#!/usr/bin/env python3
"""Sync against Anthropic's latest published skill best practices.

Fetches official documentation, extracts best practices, compares against
our current template/validator, and generates a delta report.

Can be run standalone or called from audit_skills.py as part of default audit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"
CACHE_FILE = DATA_DIR / "best_practices_cache.json"
CACHE_TTL_DAYS = 7

SKILLS_DOCS_URL = "https://code.claude.com/docs/en/skills"
SKILL_CREATOR_URL = (
    "https://raw.githubusercontent.com/anthropics/skills/main/"
    "skills/skill-creator/SKILL.md"
)

OUR_TEMPLATE = REPO_ROOT / ".claude" / "skills" / "new-skill" / "SKILL_TEMPLATE.md"
OUR_SKILL_DOCS = REPO_ROOT / ".claude" / "skills" / "new-skill" / "SKILL.md"

# Known frontmatter fields per Anthropic docs (Feb 2026)
ANTHROPIC_KNOWN_FIELDS = {
    "name": "Display name for the skill. Lowercase, numbers, hyphens (max 64 chars).",
    "description": "What the skill does and when to use it. PRIMARY TRIGGERING MECHANISM.",
    "argument-hint": "Hint shown during autocomplete for expected arguments.",
    "disable-model-invocation": "Prevent Claude from auto-loading. For manual-only skills.",
    "user-invocable": "Set false to hide from /menu. For background knowledge.",
    "allowed-tools": "Tools Claude can use without permission when skill is active.",
    "model": "Model to use when this skill is active.",
    "context": "Set to 'fork' to run in a forked subagent context.",
    "agent": "Which subagent type to use when context: fork is set.",
    "hooks": "Hooks scoped to this skill's lifecycle.",
}

# Key structural rules from Anthropic
ANTHROPIC_RULES = {
    "line_limit": 500,
    "name_max_chars": 64,
    "name_pattern": r"^[a-z0-9][a-z0-9-]*$",
    "description_max_chars": 1024,
    "description_guidance": (
        "Description is the PRIMARY TRIGGERING MECHANISM. "
        "Include both what AND when."
    ),
    "progressive_disclosure": (
        "3 levels: metadata (always) -> SKILL.md body (on trigger) "
        "-> sub-files (on demand)"
    ),
    "context_budget": "2% of context window for all skill descriptions combined",
    "directory_structure": ["scripts/", "references/", "assets/"],
    "no_auxiliary_docs": "No README.md, CHANGELOG.md, etc. in skill directories",
}

# String substitutions documented by Anthropic
ANTHROPIC_SUBSTITUTIONS = [
    "$ARGUMENTS",
    "$ARGUMENTS[N]",
    "$N (shorthand for $ARGUMENTS[N])",
    "${CLAUDE_SESSION_ID}",
]


# ---------------------------------------------------------------------------
# Fetching and caching
# ---------------------------------------------------------------------------


def _fetch_url(url: str) -> str | None:
    """Fetch URL content. Returns None on failure."""
    try:
        req = Request(url, headers={"User-Agent": "skills-audit/1.0"})
        with urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError) as e:
        print(f"⚠️  Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def _strip_html(html: str) -> str:
    """Simple HTML to text conversion."""
    # Remove script/style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    # Replace common block elements with newlines
    text = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.I)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_cache() -> dict | None:
    """Load cached docs if fresh enough."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(data["fetched_at"])
        age_days = (datetime.now(datetime.UTC) - fetched).days
        if age_days < CACHE_TTL_DAYS:
            data["_cache_age_days"] = age_days
            data["_cache_status"] = "FRESH"
            return data
        else:
            data["_cache_age_days"] = age_days
            data["_cache_status"] = "STALE"
            return data
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def save_cache(sources: dict[str, str]) -> None:
    """Save fetched docs to cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "fetched_at": datetime.now(datetime.UTC).isoformat(),
        "ttl_days": CACHE_TTL_DAYS,
        "sources": sources,
    }
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fetch_docs(force_refresh: bool = False) -> dict:
    """Fetch Anthropic docs, using cache when appropriate."""
    if not force_refresh:
        cached = load_cache()
        if cached and cached.get("_cache_status") == "FRESH":
            return cached

    sources = {}

    # Fetch main skills doc
    html = _fetch_url(SKILLS_DOCS_URL)
    if html:
        sources["skills_docs"] = _strip_html(html)
    elif not force_refresh:
        # Fall back to stale cache
        cached = load_cache()
        if cached:
            sources["skills_docs"] = cached.get("sources", {}).get("skills_docs", "")

    # Fetch skill-creator (raw markdown, no HTML stripping needed)
    raw = _fetch_url(SKILL_CREATOR_URL)
    if raw:
        sources["skill_creator"] = raw
    elif not force_refresh:
        cached = load_cache()
        if cached:
            sources["skill_creator"] = cached.get("sources", {}).get(
                "skill_creator", ""
            )

    if sources:
        save_cache(sources)

    cache_info = load_cache()
    return cache_info or {"sources": sources, "_cache_status": "JUST_FETCHED"}


# ---------------------------------------------------------------------------
# Best practices extraction (deterministic, no LLM)
# ---------------------------------------------------------------------------


def extract_fields_from_docs(text: str) -> set[str]:
    """Extract frontmatter field names mentioned in documentation."""
    # Look for field names in tables, code blocks, and inline references
    fields = set()
    # Match backtick-quoted field names
    for m in re.finditer(r"`([a-z][a-z-]+)`", text):
        candidate = m.group(1)
        if candidate in ANTHROPIC_KNOWN_FIELDS:
            fields.add(candidate)
    return fields


def extract_line_limit(text: str) -> int | None:
    """Extract recommended line limit from docs."""
    m = re.search(
        r"(?:under|less than|<=?\s*|max(?:imum)?\s+)(\d+)\s*lines", text, re.I
    )
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def compare_fields(doc_fields: set[str], our_fields: set[str]) -> dict:
    """Compare field sets."""
    return {
        "in_anthropic_not_ours": sorted(doc_fields - our_fields),
        "in_ours_not_anthropic": sorted(our_fields - doc_fields),
        "aligned": sorted(doc_fields & our_fields),
    }


def read_our_template() -> dict:
    """Read our current template and extract what we support."""
    our_fields: set[str] = set()
    our_rules: dict[str, str] = {}

    if OUR_TEMPLATE.exists():
        text = OUR_TEMPLATE.read_text(encoding="utf-8")
        fm, _ = _parse_fm(text)
        our_fields.update(fm.keys())

    if OUR_SKILL_DOCS.exists():
        text = OUR_SKILL_DOCS.read_text(encoding="utf-8")
        # Extract fields mentioned in the field constraints table
        for m in re.finditer(r"\|\s*`([a-z][a-z-]+)`\s*\|", text):
            our_fields.add(m.group(1))

    return {"fields": our_fields, "rules": our_rules}


def _parse_fm(text: str) -> tuple[dict, str]:
    """Parse frontmatter from text."""
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1))
        return (fm if isinstance(fm, dict) else {}), match.group(2)
    except yaml.YAMLError:
        return {}, text


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(docs: dict, our_state: dict) -> dict:
    """Generate a delta report comparing Anthropic docs vs. our standards."""
    report: dict = {
        "cache_status": docs.get("_cache_status", "UNKNOWN"),
        "cache_age_days": docs.get("_cache_age_days", -1),
        "fetched_at": docs.get("fetched_at", "unknown"),
        "alignments": [],
        "drifts": [],
        "new_in_anthropic": [],
        "recommendations": [],
    }

    sources = docs.get("sources", {})
    all_text = " ".join(sources.values())

    # Compare fields
    doc_fields = extract_fields_from_docs(all_text)
    # Add all known Anthropic fields
    doc_fields.update(ANTHROPIC_KNOWN_FIELDS.keys())
    field_comparison = compare_fields(doc_fields, our_state["fields"])

    for f in field_comparison["aligned"]:
        report["alignments"].append(f"Field '{f}' documented in both")

    for f in field_comparison["in_anthropic_not_ours"]:
        report["new_in_anthropic"].append(
            f"Field '{f}': {ANTHROPIC_KNOWN_FIELDS.get(f, 'described in Anthropic docs')}"
        )
        report["recommendations"].append(
            f"Add '{f}' to SKILL_TEMPLATE.md and field constraints table"
        )

    for f in field_comparison["in_ours_not_anthropic"]:
        report["drifts"].append(
            f"Field '{f}' in our template but not in Anthropic docs (may be custom)"
        )

    # Check line limit alignment
    doc_limit = extract_line_limit(all_text)
    if doc_limit and doc_limit != 500:
        report["drifts"].append(
            f"Anthropic recommends {doc_limit} line limit, we enforce 500"
        )
    elif doc_limit == 500:
        report["alignments"].append("Line limit 500 — aligned")
    else:
        report["alignments"].append(
            "Line limit 500 — our standard (Anthropic says 'under 500 lines')"
        )

    # Check for new substitution variables
    for sub in ANTHROPIC_SUBSTITUTIONS:
        if (
            sub.replace("$", "").replace("{", "").replace("}", "").lower()
            in all_text.lower()
        ):
            report["alignments"].append(f"Substitution variable {sub} documented")

    return report


def format_report_human(report: dict) -> str:
    """Format delta report as human-readable text."""
    lines = [
        "",
        "Best Practices Sync Report",
        "═══════════════════════════",
        "",
        (
            f"Cache status: {report['cache_status']} "
            f"({report['cache_age_days']} days old)"
            if report["cache_age_days"] >= 0
            else f"Cache status: {report['cache_status']}"
        ),
        "",
    ]

    if report["new_in_anthropic"]:
        lines.append("## New/Changed in Anthropic Docs")
        for item in report["new_in_anthropic"]:
            lines.append(f"  ✨ {item}")
        lines.append("")

    if report["drifts"]:
        lines.append("## Drifts from Anthropic")
        for item in report["drifts"]:
            lines.append(f"  ⚠️  {item}")
        lines.append("")

    if report["alignments"]:
        lines.append("## Aligned with Anthropic")
        for item in report["alignments"]:
            lines.append(f"  ✅ {item}")
        lines.append("")

    if report["recommendations"]:
        lines.append("## Recommendations")
        for item in report["recommendations"]:
            lines.append(f"  - [ ] {item}")
        lines.append("")

    if not report["new_in_anthropic"] and not report["drifts"]:
        lines.append(
            "✅ Fully aligned with Anthropic's latest published best practices."
        )
        lines.append("")

    return "\n".join(lines)


def format_report_json(report: dict) -> str:
    """Format delta report as JSON."""
    return json.dumps(report, indent=2)


# ---------------------------------------------------------------------------
# Apply logic
# ---------------------------------------------------------------------------


def apply_updates(report: dict) -> list[str]:
    """Apply recommended updates to template and skill docs."""
    changes = []

    if not OUR_SKILL_DOCS.exists():
        return ["⚠️  new-skill/SKILL.md not found — cannot apply"]

    text = OUR_SKILL_DOCS.read_text(encoding="utf-8")

    # Check for fields mentioned in recommendations but missing from our docs
    for rec in report.get("recommendations", []):
        m = re.search(r"Add '([^']+)' to SKILL_TEMPLATE.md", rec)
        if m:
            field_name = m.group(1)
            if f"`{field_name}`" not in text:
                desc = ANTHROPIC_KNOWN_FIELDS.get(field_name, "See Anthropic docs")
                # Add to field constraints table if it exists
                if "| Field " in text and f"`{field_name}`" not in text:
                    # Find the last table row and add after it
                    table_pattern = r"(\| `[a-z-]+`\s*\|[^\n]+\n)(?=\n|$|\|)"
                    rows = list(re.finditer(table_pattern, text))
                    if rows:
                        last_row = rows[-1]
                        new_row = f"| `{field_name}` | No | {desc} |\n"
                        text = text[: last_row.end()] + new_row + text[last_row.end() :]
                        changes.append(
                            f"Added '{field_name}' to field constraints table"
                        )

    if changes:
        OUR_SKILL_DOCS.write_text(text, encoding="utf-8")

    return changes if changes else ["No updates needed — template is current"]


def scan_skills_for_updates(report: dict) -> list[dict]:
    """Scan existing skills and suggest updates based on new best practices."""
    suggestions = []
    skills_dir = REPO_ROOT / ".claude" / "skills"

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        text = skill_md.read_text(encoding="utf-8")
        fm, body = _parse_fm(text)

        skill_suggestions = []

        # Check if argument-hint is missing but $ARGUMENTS is used
        if re.search(r"\$ARGUMENTS|\$\d+", body) and "argument-hint" not in fm:
            skill_suggestions.append("Add 'argument-hint' field (uses $ARGUMENTS)")

        # Check if description lacks trigger phrasing
        desc = str(fm.get("description", ""))
        if desc and not re.search(r"(?i)(use when|triggered by|also use when)", desc):
            skill_suggestions.append(
                "Description should include trigger phrasing ('Use when...')"
            )

        if skill_suggestions:
            suggestions.append({"skill": skill_name, "suggestions": skill_suggestions})

    return suggestions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync against Anthropic best practices"
    )
    parser.add_argument("--apply", action="store_true", help="Apply updates")
    parser.add_argument(
        "--update-skills", action="store_true", help="Update existing skills"
    )
    parser.add_argument("--force-refresh", action="store_true", help="Bypass cache")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Fetch docs
    docs = fetch_docs(force_refresh=args.force_refresh)
    if not docs.get("sources"):
        msg = "⚠️  Could not fetch Anthropic docs and no cache available"
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 0  # Non-fatal

    # Read our current state
    our_state = read_our_template()

    # Generate report
    report = generate_report(docs, our_state)

    # Apply if requested
    if args.apply:
        changes = apply_updates(report)
        report["applied_changes"] = changes

    # Scan skills for updates if requested
    if args.update_skills:
        suggestions = scan_skills_for_updates(report)
        report["skill_suggestions"] = suggestions

    # Output
    if args.json:
        print(format_report_json(report))
    else:
        print(format_report_human(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
