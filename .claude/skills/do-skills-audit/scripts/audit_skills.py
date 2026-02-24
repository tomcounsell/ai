#!/usr/bin/env python3
"""Skills audit: validate all SKILL.md files against canonical template standards.

Runs 12 deterministic validation rules and optionally syncs against
Anthropic's latest published best practices.

Exit codes:
  0 — all pass (may have warnings)
  1 — at least one FAIL
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = (
    Path(__file__).resolve().parents[4]
)  # .claude/skills/do-skills-audit/scripts -> repo
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
MAX_LINES = 500
MAX_DESC_LEN = 1024
MAX_NAME_LEN = 64

KNOWN_FIELDS = frozenset(
    {
        "name",
        "description",
        "argument-hint",
        "disable-model-invocation",
        "user-invocable",
        "allowed-tools",
        "model",
        "context",
        "agent",
        "hooks",
    }
)

# Classification lists — which skills should have specific frontmatter flags.
INFRA_SKILLS = frozenset(
    {"update", "setup", "reclassify", "new-skill", "new-valor-skill", "prime"}
)
BACKGROUND_SKILLS = frozenset(
    {
        "agent-browser",
        "telegram",
        "reading-sms-messages",
        "checking-system-logs",
        "google-workspace",
    }
)
FORK_SKILLS = frozenset(
    {"do-build", "do-pr-review", "do-docs-audit", "pthread", "do-design-review", "sdlc"}
)

TRIGGER_PHRASES = re.compile(
    r"(?i)\b(use when|triggered by|also use when|invoke when|"
    r"use for|handles|use this when)\b"
)

ARGUMENTS_RE = re.compile(r"\$ARGUMENTS|\$\d+|\$\{ARGUMENTS")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    skill: str
    rule: int
    severity: str  # PASS, WARN, FAIL
    message: str


@dataclass
class AuditReport:
    skills_audited: int = 0
    results: list[Finding] = field(default_factory=list)
    summary: dict[str, int] = field(
        default_factory=lambda: {"pass": 0, "warn": 0, "fail": 0}
    )

    def add(self, finding: Finding) -> None:
        self.results.append(finding)
        self.summary[finding.severity.lower()] += 1


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and return (frontmatter_dict, body)."""
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    try:
        fm = yaml.safe_load(match.group(1))
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}

    return fm, match.group(2).strip()


# ---------------------------------------------------------------------------
# Individual rule checks
# ---------------------------------------------------------------------------


def rule_01_line_count(skill_name: str, lines: list[str]) -> Finding:
    """SKILL.md must be <= 500 lines."""
    count = len(lines)
    if count > MAX_LINES:
        return Finding(skill_name, 1, "FAIL", f"Line count {count} exceeds {MAX_LINES}")
    return Finding(skill_name, 1, "PASS", f"Line count ({count} lines)")


def rule_02_frontmatter_exists(skill_name: str, fm: dict) -> Finding:
    """Must have YAML frontmatter."""
    if not fm:
        return Finding(skill_name, 2, "FAIL", "No YAML frontmatter found")
    return Finding(skill_name, 2, "PASS", "Frontmatter present")


def rule_03_name_field(skill_name: str, fm: dict, dir_name: str) -> Finding:
    """Name must be present, lowercase, hyphens/numbers only, max 64 chars, match dir."""
    name = fm.get("name")
    if not name:
        return Finding(skill_name, 3, "FAIL", "Missing 'name' field")
    name = str(name)
    if len(name) > MAX_NAME_LEN:
        return Finding(
            skill_name, 3, "FAIL", f"Name '{name}' exceeds {MAX_NAME_LEN} chars"
        )
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", name):
        return Finding(
            skill_name,
            3,
            "FAIL",
            f"Name '{name}' must be lowercase letters, numbers, and hyphens only",
        )
    if name != dir_name:
        return Finding(
            skill_name,
            3,
            "FAIL",
            f"Name '{name}' does not match directory name '{dir_name}'",
        )
    return Finding(skill_name, 3, "PASS", f"Name '{name}' valid")


def rule_04_description_trigger(skill_name: str, fm: dict) -> Finding:
    """Description must be present and trigger-oriented."""
    desc = fm.get("description")
    if not desc:
        return Finding(skill_name, 4, "FAIL", "Missing 'description' field")
    desc = str(desc)
    if not TRIGGER_PHRASES.search(desc):
        return Finding(
            skill_name,
            4,
            "WARN",
            "Description missing trigger phrase (e.g. 'Use when...', 'Triggered by...')",
        )
    return Finding(skill_name, 4, "PASS", "Description has trigger phrase")


def rule_05_description_length(skill_name: str, fm: dict) -> Finding:
    """Description must be <= 1024 characters."""
    desc = fm.get("description", "")
    length = len(str(desc))
    if length > MAX_DESC_LEN:
        return Finding(
            skill_name, 5, "WARN", f"Description length {length} exceeds {MAX_DESC_LEN}"
        )
    return Finding(skill_name, 5, "PASS", f"Description length ({length} chars)")


def rule_06_infra_classification(skill_name: str, fm: dict) -> Finding:
    """Infrastructure skills must have disable-model-invocation: true."""
    is_infra = skill_name in INFRA_SKILLS
    has_flag = fm.get("disable-model-invocation") is True
    if is_infra and not has_flag:
        return Finding(
            skill_name,
            6,
            "WARN",
            "Infrastructure skill missing 'disable-model-invocation: true'",
        )
    if has_flag and not is_infra:
        # Not an error — just informational, skill may intentionally disable
        pass
    return Finding(skill_name, 6, "PASS", "Infrastructure classification correct")


def rule_07_background_classification(skill_name: str, fm: dict) -> Finding:
    """Background reference skills must have user-invocable: false."""
    is_bg = skill_name in BACKGROUND_SKILLS
    has_flag = fm.get("user-invocable") is False
    if is_bg and not has_flag:
        return Finding(
            skill_name,
            7,
            "WARN",
            "Background reference skill missing 'user-invocable: false'",
        )
    return Finding(skill_name, 7, "PASS", "Background classification correct")


def rule_08_fork_classification(skill_name: str, fm: dict) -> Finding:
    """Fork skills should have context: fork."""
    is_fork = skill_name in FORK_SKILLS
    has_flag = fm.get("context") == "fork"
    if is_fork and not has_flag:
        return Finding(
            skill_name,
            8,
            "WARN",
            "Fork skill missing 'context: fork'",
        )
    return Finding(skill_name, 8, "PASS", "Fork classification correct")


def rule_09_sub_file_links(skill_name: str, body: str, skill_dir: Path) -> Finding:
    """Markdown links to files must point to existing files in the skill directory."""
    # Match [text](file.md) style links, excluding URLs
    link_re = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
    broken = []
    for _text, href in link_re.findall(body):
        # Skip URLs, anchors, and code blocks
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        # Skip template variables like {review_url}
        if re.search(r"\{[a-zA-Z_]+\}", href):
            continue
        # Strip anchor from path
        path_part = href.split("#")[0]
        if not path_part:
            continue
        target = skill_dir / path_part
        if not target.exists():
            broken.append(href)
    if broken:
        return Finding(
            skill_name,
            9,
            "FAIL",
            f"Broken sub-file links: {', '.join(broken)}",
        )
    return Finding(skill_name, 9, "PASS", "All sub-file links valid")


def rule_10_duplicate_descriptions(
    descriptions: dict[str, str],
) -> list[Finding]:
    """No two skills should have identical descriptions."""
    findings = []
    seen: dict[str, str] = {}  # desc -> first skill
    for skill_name, desc in sorted(descriptions.items()):
        normalized = desc.strip().lower()
        if normalized in seen:
            findings.append(
                Finding(
                    skill_name,
                    10,
                    "WARN",
                    f"Duplicate description with '{seen[normalized]}'",
                )
            )
        else:
            seen[normalized] = skill_name
    return findings


def rule_11_known_fields(skill_name: str, fm: dict) -> Finding:
    """Frontmatter should only contain recognized fields."""
    unknown = set(fm.keys()) - KNOWN_FIELDS
    if unknown:
        return Finding(
            skill_name,
            11,
            "WARN",
            f"Unknown frontmatter fields: {', '.join(sorted(unknown))}",
        )
    return Finding(skill_name, 11, "PASS", "All frontmatter fields recognized")


def rule_12_argument_hint(skill_name: str, fm: dict, body: str) -> Finding:
    """If body uses $ARGUMENTS/$0/$1, argument-hint should be set."""
    uses_args = bool(ARGUMENTS_RE.search(body))
    has_hint = "argument-hint" in fm
    if uses_args and not has_hint:
        return Finding(
            skill_name,
            12,
            "WARN",
            "Uses $ARGUMENTS but missing 'argument-hint' field",
        )
    return Finding(skill_name, 12, "PASS", "Argument hint check passed")


# ---------------------------------------------------------------------------
# Fix helpers
# ---------------------------------------------------------------------------


def apply_fixes(skill_path: Path, fm: dict, text: str, dir_name: str) -> list[str]:
    """Apply trivial auto-fixes. Returns list of descriptions of what was fixed."""
    fixes = []
    modified = False

    # Fix 1: Add missing name field
    if "name" not in fm:
        fm["name"] = dir_name
        modified = True
        fixes.append(f"Added name: {dir_name}")

    # Fix 2: Trim trailing whitespace in frontmatter string values
    for key, value in fm.items():
        if isinstance(value, str) and value != value.strip():
            fm[key] = value.strip()
            modified = True
            fixes.append(f"Trimmed whitespace in '{key}'")

    if modified:
        # Rebuild the file
        fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        # Preserve body
        match = re.match(r"^---\n.*?\n---\n?(.*)", text, re.DOTALL)
        body = match.group(1) if match else text
        new_text = f"---\n{fm_text}\n---\n{body}"
        skill_path.write_text(new_text, encoding="utf-8")

    return fixes


# ---------------------------------------------------------------------------
# Main audit logic
# ---------------------------------------------------------------------------


def discover_skills(skills_dir: Path, single: str | None = None) -> list[Path]:
    """Find all SKILL.md files."""
    if single:
        target = skills_dir / single / "SKILL.md"
        if target.exists():
            return [target]
        return []
    return sorted(skills_dir.glob("*/SKILL.md"))


def audit_skill(
    skill_path: Path, report: AuditReport, do_fix: bool = False
) -> dict[str, str]:
    """Audit a single skill. Returns {skill_name: description} for dedup check."""
    skill_dir = skill_path.parent
    dir_name = skill_dir.name
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    fm, body = parse_frontmatter(text)

    # Apply fixes first if requested
    if do_fix:
        fixes = apply_fixes(skill_path, fm, text, dir_name)
        for fix in fixes:
            report.add(Finding(dir_name, 0, "PASS", f"Fixed: {fix}"))
        # Re-read after fixes
        if fixes:
            text = skill_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            fm, body = parse_frontmatter(text)

    # Run all rules
    report.add(rule_01_line_count(dir_name, lines))
    report.add(rule_02_frontmatter_exists(dir_name, fm))
    report.add(rule_03_name_field(dir_name, fm, dir_name))
    report.add(rule_04_description_trigger(dir_name, fm))
    report.add(rule_05_description_length(dir_name, fm))
    report.add(rule_06_infra_classification(dir_name, fm))
    report.add(rule_07_background_classification(dir_name, fm))
    report.add(rule_08_fork_classification(dir_name, fm))
    report.add(rule_09_sub_file_links(dir_name, body, skill_dir))
    report.add(rule_11_known_fields(dir_name, fm))
    report.add(rule_12_argument_hint(dir_name, fm, body))

    desc = str(fm.get("description", ""))
    return {dir_name: desc} if desc else {}


def run_sync(args: argparse.Namespace, report_json: dict | None = None) -> str | None:
    """Run the best practices sync script if available."""
    sync_script = Path(__file__).parent / "sync_best_practices.py"
    if not sync_script.exists():
        return "⚠️  sync_best_practices.py not found — skipping best practices sync"

    cmd = [sys.executable, str(sync_script)]
    if args.apply:
        cmd.append("--apply")
    if args.update_skills:
        cmd.append("--update-skills")
    if args.force_refresh:
        cmd.append("--force-refresh")
    if args.json:
        cmd.append("--json")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "⚠️  Best practices sync timed out after 60s"
    except Exception as e:
        return f"⚠️  Best practices sync failed: {e}"


def format_human(report: AuditReport, sync_output: str | None = None) -> str:
    """Format report as human-readable table."""
    lines = [
        "Skills Audit Report",
        "═══════════════════",
        "",
    ]

    # Group by skill
    by_skill: dict[str, list[Finding]] = {}
    for f in report.results:
        by_skill.setdefault(f.skill, []).append(f)

    for skill in sorted(by_skill):
        findings = by_skill[skill]
        # Only show non-PASS findings in compact mode, or all if few
        non_pass = [f for f in findings if f.severity != "PASS"]
        if non_pass:
            for f in non_pass:
                icon = "⚠️ " if f.severity == "WARN" else "❌"
                lines.append(
                    f"  {icon} {f.severity:<4}  {f.skill:<30}  Rule {f.rule:>2}: {f.message}"
                )
        else:
            lines.append(f"  ✅ PASS  {skill:<30}  All {len(findings)} rules passed")

    lines.append("")
    lines.append(
        f"Summary: {report.skills_audited} skills audited | "
        f"{report.summary['pass']} PASS | "
        f"{report.summary['warn']} WARN | "
        f"{report.summary['fail']} FAIL"
    )

    if sync_output:
        lines.append("")
        lines.append(sync_output)

    return "\n".join(lines)


def format_json(report: AuditReport, sync_output: str | None = None) -> str:
    """Format report as JSON."""
    data = {
        "skills_audited": report.skills_audited,
        "results": [asdict(f) for f in report.results],
        "summary": report.summary,
    }
    if sync_output:
        data["best_practices_sync"] = sync_output
    return json.dumps(data, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Claude Code skills")
    parser.add_argument("--fix", action="store_true", help="Auto-fix trivial issues")
    parser.add_argument("--json", action="store_true", help="JSON output only")
    parser.add_argument("--skill", type=str, help="Audit a single skill by name")
    parser.add_argument(
        "--no-sync", action="store_true", help="Skip best practices sync"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Apply best practices updates"
    )
    parser.add_argument(
        "--update-skills",
        action="store_true",
        help="Update existing skills to match best practices",
    )
    parser.add_argument("--force-refresh", action="store_true", help="Bypass doc cache")
    args = parser.parse_args()

    report = AuditReport()

    # Discover skills
    skill_paths = discover_skills(SKILLS_DIR, args.skill)
    if not skill_paths:
        print(f"No skills found{f' matching {args.skill!r}' if args.skill else ''}")
        return 1

    report.skills_audited = len(skill_paths)

    # Audit each skill, collecting descriptions for dedup
    all_descriptions: dict[str, str] = {}
    for sp in skill_paths:
        descs = audit_skill(sp, report, do_fix=args.fix)
        all_descriptions.update(descs)

    # Rule 10: duplicate descriptions (cross-skill check)
    if not args.skill:  # Only check when auditing all skills
        dedup_findings = rule_10_duplicate_descriptions(all_descriptions)
        for f in dedup_findings:
            report.add(f)
        if not dedup_findings:
            report.summary["pass"] += 1  # No duplicates found

    # Best practices sync
    sync_output = None
    if not args.no_sync:
        sync_output = run_sync(args)

    # Output
    if args.json:
        print(format_json(report, sync_output))
    else:
        print(format_human(report, sync_output))

    return 1 if report.summary["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
