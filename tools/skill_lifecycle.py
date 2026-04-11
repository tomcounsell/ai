"""Skill lifecycle management: friction detection, expiry, refresh, and reporting.

Usage:
    python -m tools.skill_lifecycle detect-friction
    python -m tools.skill_lifecycle expire [--dry-run]
    python -m tools.skill_lifecycle refresh
    python -m tools.skill_lifecycle report
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure project root on sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_KEYWORDS = {"tool", "params", "flags", "cli", "command", "argument"}

# Skills directory
_SKILLS_DIR = Path(__file__).parent.parent / ".claude" / "skills"

# Analytics DB
_DB_PATH = Path(__file__).parent.parent / "data" / "analytics.db"

# Safety window: 48 hours in seconds
_SAFETY_WINDOW_SECS = 48 * 3600

# Default refresh extension: 30 days
_REFRESH_DAYS = 30


# ---------------------------------------------------------------------------
# Friction Detection
# ---------------------------------------------------------------------------


def _query_correction_memories() -> list:
    """Query Memory records with category=correction. Best-effort."""
    try:
        from models.memory import Memory

        # Popoto filter API -- query by metadata category
        # Filter in Python since nested metadata filtering may not be supported
        all_memories = []
        try:
            results = Memory.query.filter(metadata__category="correction")
            all_memories = list(results)
        except Exception:
            # Fallback: scan all and filter manually
            try:
                for m in Memory.query.all():
                    meta = getattr(m, "metadata", None) or {}
                    if meta.get("category") == "correction":
                        all_memories.append(m)
            except Exception as e:
                logger.warning("Failed to query memories: %s", e)
        return all_memories
    except Exception as e:
        logger.warning("Could not import Memory model: %s", e)
        return []


def detect_friction() -> list[dict]:
    """Find friction patterns in Memory correction records with tool-related tags.

    Uses exact-match heuristics only (no LLM classification).
    Returns structured list of friction patterns found.
    """
    memories = _query_correction_memories()
    results = []

    for m in memories:
        meta = getattr(m, "metadata", None) or {}
        tags = set(meta.get("tags", []))
        if tags & TOOL_KEYWORDS:
            results.append(
                {
                    "memory_id": m.memory_id,
                    "content": m.content,
                    "tags": sorted(tags),
                    "created": str(getattr(m, "created_at", "")),
                }
            )

    return results


# ---------------------------------------------------------------------------
# Frontmatter Parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_skill_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from a SKILL.md file.

    Returns dict of key-value pairs. Simple line-based parsing
    (no full YAML dependency).
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    result = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # Type coercion for known fields
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False

        result[key] = value

    return result


def update_expires_at_in_frontmatter(content: str, new_date: str) -> str:
    """Update or insert expires_at in SKILL.md frontmatter.

    Args:
        content: Full SKILL.md file content.
        new_date: New expires_at value (YYYY-MM-DD).

    Returns:
        Updated content string.
    """
    # Try to replace existing expires_at line
    pattern = re.compile(r"^(expires_at:\s*).*$", re.MULTILINE)
    if pattern.search(content):
        return pattern.sub(f"expires_at: {new_date}", content)

    # Insert before closing ---
    # Find the second --- (closing frontmatter)
    first = content.index("---")
    second = content.index("---", first + 3)
    return content[:second] + f"expires_at: {new_date}\n" + content[second:]


# ---------------------------------------------------------------------------
# Analytics Helpers
# ---------------------------------------------------------------------------


def _get_analytics_connection() -> sqlite3.Connection | None:
    """Get a read-only connection to analytics SQLite. Returns None if unavailable."""
    try:
        if not _DB_PATH.exists():
            return None
        conn = sqlite3.connect(str(_DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning("Could not connect to analytics DB: %s", e)
        return None


def _get_last_invocation_ts(conn: sqlite3.Connection, skill_name: str) -> float | None:
    """Get the timestamp of the most recent invocation for a skill."""
    try:
        query = (
            "SELECT MAX(timestamp) FROM metrics WHERE name='skill.invocation' AND dimensions LIKE ?"
        )
        cursor = conn.execute(query, (f'%"skill": "{skill_name}"%',))
        row = cursor.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None
    except Exception as e:
        logger.warning("Failed to query last invocation for %s: %s", skill_name, e)
        return None


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def should_expire_skill(skill_name: str, last_invoked_ts: float | None) -> bool:
    """Determine if a generated skill should be expired.

    Returns True if the skill should be removed (no invocation within 48h safety window).
    Returns False if the skill was recently invoked and should be kept.
    """
    if last_invoked_ts is None:
        return True
    return (time.time() - last_invoked_ts) > _SAFETY_WINDOW_SECS


def _find_generated_skills() -> list[tuple[str, Path, dict]]:
    """Find all skills with generated: true in their frontmatter.

    Returns list of (skill_name, skill_md_path, frontmatter_dict).
    """
    results = []
    try:
        for skill_dir in _SKILLS_DIR.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text()
                fm = parse_skill_frontmatter(content)
                if fm.get("generated") is True:
                    results.append((fm.get("name", skill_dir.name), skill_md, fm))
            except Exception as e:
                logger.warning("Failed to read %s: %s", skill_md, e)
    except Exception as e:
        logger.warning("Failed to scan skills directory: %s", e)
    return results


def cmd_expire(args: argparse.Namespace) -> None:
    """Find and expire generated skills past their expiry date."""
    dry_run = getattr(args, "dry_run", False)
    generated = _find_generated_skills()

    if not generated:
        print("No generated skills found.")
        return

    now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    conn = _get_analytics_connection()

    expired_count = 0
    for skill_name, skill_md, fm in generated:
        expires_at = fm.get("expires_at", "")
        if not expires_at or str(expires_at) > now_str:
            continue  # Not expired yet

        # Check 48h safety window
        last_ts = None
        if conn:
            last_ts = _get_last_invocation_ts(conn, skill_name)

        if not should_expire_skill(skill_name, last_ts):
            print(f"SKIP {skill_name}: invoked within 48h safety window")
            continue

        expired_count += 1
        if dry_run:
            print(f"WOULD EXPIRE: {skill_name} (expired: {expires_at})")
        else:
            print(f"EXPIRE: {skill_name} (expired: {expires_at})")
            # Create removal PR via gh
            try:
                import subprocess

                subprocess.run(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--title",
                        f"Remove expired skill: {skill_name}",
                        "--body",
                        f"Auto-generated removal. Skill expired on {expires_at}, "
                        f"not invoked within 48h safety window.",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            except Exception as e:
                logger.warning("Failed to create removal PR for %s: %s", skill_name, e)

    if conn:
        conn.close()

    if expired_count == 0:
        print("No skills need expiry.")


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def cmd_refresh(args: argparse.Namespace) -> None:
    """Refresh expires_at for recently invoked generated skills."""
    conn = _get_analytics_connection()
    if not conn:
        print("No analytics data available.")
        return

    generated = _find_generated_skills()
    if not generated:
        print("No generated skills found.")
        conn.close()
        return

    # Query all skill invocations in the last 30 days
    cutoff = time.time() - (30 * 86400)
    new_expiry = (datetime.now(tz=UTC) + timedelta(days=_REFRESH_DAYS)).strftime("%Y-%m-%d")

    refreshed = 0
    for skill_name, skill_md, fm in generated:
        last_ts = _get_last_invocation_ts(conn, skill_name)
        if last_ts is not None and last_ts > cutoff:
            try:
                content = skill_md.read_text()
                updated = update_expires_at_in_frontmatter(content, new_expiry)
                skill_md.write_text(updated)
                refreshed += 1
                print(f"REFRESHED: {skill_name} -> expires_at: {new_expiry}")
            except Exception as e:
                logger.warning("Failed to refresh %s: %s", skill_name, e)

    conn.close()
    if refreshed == 0:
        print("No skills needed refresh.")
    else:
        print(f"Refreshed {refreshed} skill(s).")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def get_skill_report() -> list[dict]:
    """Query analytics for per-skill invocation metrics.

    Returns list of dicts with keys: skill, count, last_used.
    """
    conn = _get_analytics_connection()
    if conn is None:
        return []

    try:
        cursor = conn.execute(
            "SELECT dimensions, COUNT(*) as cnt, MAX(timestamp) as last_ts "
            "FROM metrics WHERE name='skill.invocation' "
            "GROUP BY dimensions"
        )
        results = []
        for row in cursor.fetchall():
            dims_str = row[0] if isinstance(row, (tuple, list)) else row["dimensions"]
            count = row[1] if isinstance(row, (tuple, list)) else row["cnt"]
            last_ts = row[2] if isinstance(row, (tuple, list)) else row["last_ts"]

            skill_name = "unknown"
            try:
                dims = json.loads(dims_str) if dims_str else {}
                skill_name = dims.get("skill", "unknown")
            except (json.JSONDecodeError, TypeError):
                pass

            results.append(
                {
                    "skill": skill_name,
                    "count": count,
                    "last_used": datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d %H:%M")
                    if last_ts
                    else "never",
                }
            )

        return results
    except Exception as e:
        logger.warning("Failed to generate skill report: %s", e)
        return []


def cmd_report(args: argparse.Namespace) -> None:
    """Print per-skill analytics report."""
    report = get_skill_report()
    if not report:
        print("No skill invocation data found.")
        return

    # Format as table
    print(f"{'Skill':<30} {'Count':>6} {'Last Used':<20}")
    print("-" * 58)
    for entry in sorted(report, key=lambda x: x["count"], reverse=True):
        print(f"{entry['skill']:<30} {entry['count']:>6} {entry['last_used']:<20}")


# ---------------------------------------------------------------------------
# Detect-Friction Command
# ---------------------------------------------------------------------------


def cmd_detect_friction(args: argparse.Namespace) -> None:
    """Print detected friction patterns."""
    results = detect_friction()
    if not results:
        print("No friction patterns detected.")
        return

    print(f"Found {len(results)} friction pattern(s):\n")
    for r in results:
        print(f"  ID: {r['memory_id']}")
        print(f"  Content: {r['content']}")
        print(f"  Tags: {', '.join(r['tags'])}")
        print(f"  Created: {r['created']}")
        print()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for skill lifecycle management."""
    parser = argparse.ArgumentParser(
        prog="skill_lifecycle",
        description="Skill lifecycle: friction detection, expiry, refresh, reporting.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # detect-friction
    subparsers.add_parser(
        "detect-friction", help="Detect friction patterns from Memory corrections"
    )

    # expire
    expire_parser = subparsers.add_parser(
        "expire", help="Expire generated skills past their expiry date"
    )
    expire_parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be expired without acting"
    )

    # refresh
    subparsers.add_parser(
        "refresh", help="Refresh expires_at for recently invoked generated skills"
    )

    # report
    subparsers.add_parser("report", help="Print per-skill analytics report")

    args = parser.parse_args()

    if args.command == "detect-friction":
        cmd_detect_friction(args)
    elif args.command == "expire":
        cmd_expire(args)
    elif args.command == "refresh":
        cmd_refresh(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
