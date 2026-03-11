#!/usr/bin/env python3
"""Sync against Anthropic's latest published skill best practices.

Fetches official documentation, diffs against our local reference copies,
updates local files when content has changed, and generates a delta report.

Local reference files live at:
  .claude/skills/do-skills-audit/references/
    anthropic-skills-docs.txt     — stripped text from docs page
    anthropic-skill-creator.md    — raw skill-creator SKILL.md
    metadata.json                 — fetch timestamps and source URLs

The audit always reads from local reference files (no live fetch needed).
Run with no flags (or --sync) to check for upstream updates.

Exit codes:
  0 — success
  1 — could not fetch and no local files exist
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parents[1]
REFERENCES_DIR = SKILL_DIR / "references"
DOCS_FILE = REFERENCES_DIR / "anthropic-skills-docs.txt"
CREATOR_FILE = REFERENCES_DIR / "anthropic-skill-creator.md"
METADATA_FILE = REFERENCES_DIR / "metadata.json"

REPO_ROOT = Path(__file__).resolve().parents[4]
OUR_TEMPLATE = REPO_ROOT / ".claude" / "skills" / "new-skill" / "SKILL_TEMPLATE.md"
OUR_SKILL_DOCS = REPO_ROOT / ".claude" / "skills" / "new-skill" / "SKILL.md"

SKILLS_DOCS_URL = "https://code.claude.com/docs/en/skills"
SKILL_CREATOR_URL = (
    "https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md"
)

SYNC_TTL_DAYS = 7  # Only re-fetch if local files are older than this

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

ANTHROPIC_RULES = {
    "line_limit": 500,
    "name_max_chars": 64,
    "name_pattern": r"^[a-z0-9][a-z0-9-]*$",
    "description_max_chars": 1024,
    "description_guidance": (
        "Description is the PRIMARY TRIGGERING MECHANISM. Include both what AND when."
    ),
    "progressive_disclosure": (
        "3 levels: metadata (always) -> SKILL.md body (on trigger) -> sub-files (on demand)"
    ),
    "context_budget": "2% of context window for all skill descriptions combined",
    "directory_structure": ["scripts/", "references/", "assets/"],
    "no_auxiliary_docs": "No README.md, CHANGELOG.md, etc. in skill directories",
}

ANTHROPIC_SUBSTITUTIONS = [
    "$ARGUMENTS",
    "$ARGUMENTS[N]",
    "$N (shorthand for $ARGUMENTS[N])",
    "${CLAUDE_SESSION_ID}",
]


# ---------------------------------------------------------------------------
# SSL helpers
# ---------------------------------------------------------------------------


def _get_ssl_context():
    """Get an SSL context with proper CA certificates."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch_url(url: str) -> str | None:
    """Fetch URL content. Returns None on failure."""
    try:
        ctx = _get_ssl_context()
        req = Request(url, headers={"User-Agent": "skills-audit/1.0"})
        with urlopen(req, timeout=15, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError) as e:
        print(f"⚠️  Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def _strip_html(html: str) -> str:
    """Simple HTML to text conversion."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Local reference file management
# ---------------------------------------------------------------------------


def local_files_exist() -> bool:
    """Return True if all reference files are present."""
    return DOCS_FILE.exists() and CREATOR_FILE.exists()


def local_files_age_days() -> int:
    """Return age of local reference files in days (based on metadata)."""
    if not METADATA_FILE.exists():
        return 999
    try:
        meta = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(meta["fetched_at"])
        return (datetime.now(tz=UTC) - fetched).days
    except (json.JSONDecodeError, KeyError, ValueError):
        return 999


def load_local_docs() -> dict[str, str]:
    """Load the local reference files. Returns empty dict if missing."""
    sources: dict[str, str] = {}
    if DOCS_FILE.exists():
        sources["skills_docs"] = DOCS_FILE.read_text(encoding="utf-8")
    if CREATOR_FILE.exists():
        sources["skill_creator"] = CREATOR_FILE.read_text(encoding="utf-8")
    return sources


def save_local_docs(docs_text: str | None, creator_text: str | None) -> list[str]:
    """Write fetched content to local reference files. Returns list of changed files."""
    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    changed = []

    if docs_text is not None:
        stripped = _strip_html(docs_text)
        existing = DOCS_FILE.read_text(encoding="utf-8") if DOCS_FILE.exists() else ""
        if stripped != existing:
            DOCS_FILE.write_text(stripped, encoding="utf-8")
            changed.append(DOCS_FILE.name)

    if creator_text is not None:
        existing = CREATOR_FILE.read_text(encoding="utf-8") if CREATOR_FILE.exists() else ""
        if creator_text != existing:
            CREATOR_FILE.write_text(creator_text, encoding="utf-8")
            changed.append(CREATOR_FILE.name)

    # Always update metadata timestamp
    meta = {
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "sources": {
            "skills_docs": SKILLS_DOCS_URL,
            "skill_creator": SKILL_CREATOR_URL,
        },
        "sync_ttl_days": SYNC_TTL_DAYS,
    }
    METADATA_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return changed


def sync_from_upstream(force: bool = False) -> dict:
    """Fetch upstream docs and update local reference files if stale or forced.

    Returns a status dict with keys:
      skipped      — True if sync was skipped (fresh enough)
      age_days     — age of local files
      changed      — list of files that were updated
      fetch_errors — list of URLs that failed
    """
    age = local_files_age_days()
    if not force and local_files_exist() and age < SYNC_TTL_DAYS:
        return {"skipped": True, "age_days": age, "changed": [], "fetch_errors": []}

    fetch_errors = []
    docs_text = _fetch_url(SKILLS_DOCS_URL)
    if docs_text is None:
        fetch_errors.append(SKILLS_DOCS_URL)

    creator_text = _fetch_url(SKILL_CREATOR_URL)
    if creator_text is None:
        fetch_errors.append(SKILL_CREATOR_URL)

    changed = save_local_docs(docs_text, creator_text)

    return {
        "skipped": False,
        "age_days": 0,
        "changed": changed,
        "fetch_errors": fetch_errors,
    }


# ---------------------------------------------------------------------------
# Best practices extraction (deterministic, no LLM)
# ---------------------------------------------------------------------------


def extract_fields_from_docs(text: str) -> set[str]:
    """Extract frontmatter field names mentioned in documentation."""
    fields = set()
    for m in re.finditer(r"`([a-z][a-z-]+)`", text):
        candidate = m.group(1)
        if candidate in ANTHROPIC_KNOWN_FIELDS:
            fields.add(candidate)
    return fields


def extract_line_limit(text: str) -> int | None:
    """Extract recommended line limit from docs."""
    m = re.search(
        r"(?:under\s+|less\s+than\s+|<=?\s*|max(?:imum)?\s+)(\d+)\s*lines",
        text,
        re.I,
    )
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------


def compare_fields(doc_fields: set[str], our_fields: set[str]) -> dict:
    return {
        "in_anthropic_not_ours": sorted(doc_fields - our_fields),
        "in_ours_not_anthropic": sorted(our_fields - doc_fields),
        "aligned": sorted(doc_fields & our_fields),
    }


def _parse_fm(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1))
        return (fm if isinstance(fm, dict) else {}), match.group(2)
    except yaml.YAMLError:
        return {}, text


def read_our_template() -> dict:
    our_fields: set[str] = set()

    if OUR_TEMPLATE.exists():
        text = OUR_TEMPLATE.read_text(encoding="utf-8")
        fm, _ = _parse_fm(text)
        our_fields.update(fm.keys())

    if OUR_SKILL_DOCS.exists():
        text = OUR_SKILL_DOCS.read_text(encoding="utf-8")
        for m in re.finditer(r"\|\s*`([a-z][a-z-]+)`\s*\|", text):
            our_fields.add(m.group(1))

    return {"fields": our_fields}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(sources: dict[str, str], our_state: dict) -> dict:
    all_text = " ".join(v for v in sources.values() if isinstance(v, str))

    report: dict = {
        "alignments": [],
        "drifts": [],
        "new_in_anthropic": [],
        "recommendations": [],
    }

    doc_fields = extract_fields_from_docs(all_text)
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

    doc_limit = extract_line_limit(all_text)
    if doc_limit and doc_limit != 500:
        report["drifts"].append(f"Anthropic recommends {doc_limit} line limit, we enforce 500")
    elif doc_limit == 500:
        report["alignments"].append("Line limit 500 — aligned")
    else:
        report["alignments"].append(
            "Line limit 500 — our standard (Anthropic says 'under 500 lines')"
        )

    for sub in ANTHROPIC_SUBSTITUTIONS:
        if sub.replace("$", "").replace("{", "").replace("}", "").lower() in all_text.lower():
            report["alignments"].append(f"Substitution variable {sub} documented")

    return report


def format_report_human(report: dict, sync_status: dict | None = None) -> str:
    lines = [
        "",
        "Best Practices Sync Report",
        "═══════════════════════════",
        "",
    ]

    if sync_status:
        if sync_status.get("skipped"):
            lines.append(
                f"Local docs: FRESH ({sync_status['age_days']} days old) — skipped upstream check"
            )
        else:
            changed = sync_status.get("changed", [])
            errors = sync_status.get("fetch_errors", [])
            if changed:
                lines.append(f"Updated local reference files: {', '.join(changed)}")
            else:
                lines.append("Local reference files: up to date (no changes upstream)")
            if errors:
                for url in errors:
                    lines.append(f"⚠️  Fetch failed: {url}")
        lines.append("")

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
        lines.append("✅ Fully aligned with Anthropic's latest published best practices.")
        lines.append("")

    return "\n".join(lines)


def format_report_json(report: dict, sync_status: dict | None = None) -> str:
    data = dict(report)
    if sync_status:
        data["sync_status"] = sync_status
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Apply logic
# ---------------------------------------------------------------------------


def apply_updates(report: dict) -> list[str]:
    changes = []
    if not OUR_SKILL_DOCS.exists():
        return ["⚠️  new-skill/SKILL.md not found — cannot apply"]

    text = OUR_SKILL_DOCS.read_text(encoding="utf-8")

    for rec in report.get("recommendations", []):
        m = re.search(r"Add '([^']+)' to SKILL_TEMPLATE.md", rec)
        if m:
            field_name = m.group(1)
            if f"`{field_name}`" not in text:
                desc = ANTHROPIC_KNOWN_FIELDS.get(field_name, "See Anthropic docs")
                if "| Field " in text:
                    table_pattern = r"(\| `[a-z-]+`\s*\|[^\n]+\n)(?=\n|$|\|)"
                    rows = list(re.finditer(table_pattern, text))
                    if rows:
                        last_row = rows[-1]
                        new_row = f"| `{field_name}` | No | {desc} |\n"
                        text = text[: last_row.end()] + new_row + text[last_row.end() :]
                        changes.append(f"Added '{field_name}' to field constraints table")

    if changes:
        OUR_SKILL_DOCS.write_text(text, encoding="utf-8")

    return changes if changes else ["No updates needed — template is current"]


def scan_skills_for_updates(report: dict) -> list[dict]:
    suggestions = []
    skills_dir = REPO_ROOT / ".claude" / "skills"

    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        text = skill_md.read_text(encoding="utf-8")
        fm, body = _parse_fm(text)

        skill_suggestions = []
        if re.search(r"\$ARGUMENTS|\$\d+", body) and "argument-hint" not in fm:
            skill_suggestions.append("Add 'argument-hint' field (uses $ARGUMENTS)")

        desc = str(fm.get("description", ""))
        if desc and not re.search(r"(?i)(use when|triggered by|also use when)", desc):
            skill_suggestions.append("Description should include trigger phrasing ('Use when...')")

        if skill_suggestions:
            suggestions.append({"skill": skill_name, "suggestions": skill_suggestions})

    return suggestions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Anthropic skill best practices to local reference files"
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip upstream check, read from local files only",
    )
    parser.add_argument("--force-refresh", action="store_true", help="Force upstream fetch")
    parser.add_argument("--apply", action="store_true", help="Apply updates to our template")
    parser.add_argument("--update-skills", action="store_true", help="Suggest skill updates")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Step 1: Sync upstream → local files (unless --no-sync)
    sync_status = None
    if not args.no_sync:
        sync_status = sync_from_upstream(force=args.force_refresh)

    # Step 2: Load from local reference files
    sources = load_local_docs()
    if not sources:
        msg = "⚠️  No local reference files found. Run without --no-sync to fetch from upstream."
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 1

    # Step 3: Compare against our template
    our_state = read_our_template()
    report = generate_report(sources, our_state)

    # Step 4: Apply if requested
    if args.apply:
        changes = apply_updates(report)
        report["applied_changes"] = changes

    if args.update_skills:
        report["skill_suggestions"] = scan_skills_for_updates(report)

    # Step 5: Output
    if args.json:
        print(format_report_json(report, sync_status))
    else:
        print(format_report_human(report, sync_status))

    return 0


if __name__ == "__main__":
    sys.exit(main())
