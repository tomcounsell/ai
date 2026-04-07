#!/usr/bin/env python3
"""AI semantic evaluator for build acceptance criteria.

Reads the plan's ## Acceptance Criteria section and compares against
git diff main..HEAD using an AI judge. Returns structured verdicts.

Usage:
    python scripts/evaluate_build.py <plan-path>
    python scripts/evaluate_build.py --dry-run <plan-path>
    python scripts/evaluate_build.py --help

Exit codes:
    0 - All criteria PASS or PARTIAL (no FAIL)
    1 - Unexpected error (non-blocking — caller logs and proceeds)
    2 - One or more FAIL verdicts
    3 - No ## Acceptance Criteria section in plan or empty diff (skip with warning)
"""

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import anthropic

from config.models import HAIKU

# Configure logging to file and stderr
LOG_FILE = Path("logs/evaluate_build.log")


def _setup_logging() -> logging.Logger:
    """Set up logging to file and stderr."""
    logger = logging.getLogger("evaluate_build")
    logger.setLevel(logging.DEBUG)

    # File handler
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_FILE)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
    except Exception:
        pass  # Log file setup failure is non-blocking

    # Stderr handler for visible messages
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(sh)

    return logger


logger = _setup_logging()


def extract_section(plan_text: str, heading: str) -> str:
    """Extract content of a markdown section by heading name.

    Returns the text between the heading and the next heading of level 1-3,
    or end of document. Does not distinguish heading levels -- any heading
    at level 1, 2, or 3 terminates the section.
    """
    pattern = r"^(#{1,3}) " + re.escape(heading) + r"\s*\n(.*?)(?=^#{1,3} |\Z)"
    match = re.search(pattern, plan_text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(2)
    return ""


def get_git_diff() -> str:
    """Run git diff main..HEAD and return output string."""
    try:
        result = subprocess.run(
            ["git", "diff", "main..HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception as e:
        logger.warning(f"Failed to run git diff: {e}")
        return ""


def evaluate_criteria(criteria_text: str, diff_text: str) -> list[dict]:
    """Call Anthropic API (Claude Haiku) to evaluate acceptance criteria against diff.

    Returns a list of verdict dicts: {"criterion": str, "verdict": str, "evidence": str}
    """
    prompt = f"""You are a build acceptance evaluator. Given a list of acceptance criteria and a git diff, assess each criterion.

For each criterion output:
- verdict: "PASS" if clearly met, "PARTIAL" if partially met or uncertain, "FAIL" if not met
- evidence: one sentence explaining what you found (or didn't find) in the diff

Acceptance Criteria:
{criteria_text}

Git Diff (main..HEAD):
{diff_text}

Respond with valid JSON only:
{{"verdicts": [{{"criterion": "<text>", "verdict": "PASS|PARTIAL|FAIL", "evidence": "<sentence>"}}]}}"""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=HAIKU,
        max_tokens=2048,
        timeout=60,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text
    data = json.loads(response_text)
    return data["verdicts"]


def _make_dry_run_verdicts(criteria_text: str) -> list[dict]:
    """Parse criteria and return mock PASS verdicts for each (--dry-run mode)."""
    verdicts = []
    lines = criteria_text.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Strip checkbox markers
        line = re.sub(r"^-\s*\[[ xX]\]\s*", "", line)
        line = re.sub(r"^\*\s*", "", line)
        if line:
            verdicts.append(
                {
                    "criterion": line,
                    "verdict": "PASS",
                    "evidence": "[dry-run] Mock PASS verdict — no API call made.",
                }
            )
    return verdicts


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]

    if not args or args[0] in ("--help", "-h"):
        print("Usage: python scripts/evaluate_build.py [--dry-run] <plan-path>")
        print()
        print("AI semantic evaluator for build acceptance criteria.")
        print("Reads the plan's ## Acceptance Criteria section and compares")
        print("against git diff main..HEAD using Claude Haiku.")
        print()
        print("Options:")
        print("  --dry-run   Parse criteria and diff but skip API call;")
        print("              output mock PASS verdicts for testing.")
        print("  --help      Show this help and exit.")
        print()
        print("Exit codes:")
        print("  0 - All criteria PASS or PARTIAL (no FAIL)")
        print("  1 - Unexpected error (non-blocking — caller logs and proceeds)")
        print("  2 - One or more FAIL verdicts")
        print("  3 - No ## Acceptance Criteria section or empty diff (skip)")
        return 0

    plan_path = Path(args[0])
    if not plan_path.exists():
        print(f"Error: Plan file not found: {plan_path}", file=sys.stderr)
        logger.warning(f"Plan file not found: {plan_path}")
        return 1

    try:
        plan_text = plan_path.read_text()
    except Exception as e:
        print(f"Error reading plan file: {e}", file=sys.stderr)
        logger.warning(f"Error reading plan file: {e}")
        return 1

    # Extract Acceptance Criteria section
    criteria_text = extract_section(plan_text, "Acceptance Criteria")
    if not criteria_text or not criteria_text.strip():
        msg = "AI evaluator: no Acceptance Criteria section, skipping"
        print(msg, file=sys.stderr)
        logger.warning(msg)
        return 3

    # Get git diff
    diff_text = get_git_diff()
    if not diff_text or not diff_text.strip():
        msg = "AI evaluator: empty diff, skipping"
        print(msg, file=sys.stderr)
        logger.warning(msg)
        return 3

    # Run evaluation (or dry-run)
    try:
        if dry_run:
            verdicts = _make_dry_run_verdicts(criteria_text)
        else:
            verdicts = evaluate_criteria(criteria_text, diff_text)
    except json.JSONDecodeError as e:
        msg = f"AI evaluator failed (non-blocking): JSON parse error — {e}"
        print(msg, file=sys.stderr)
        logger.warning(msg)
        return 1
    except Exception as e:
        msg = f"AI evaluator failed (non-blocking): {e}"
        print(msg, file=sys.stderr)
        logger.warning(msg)
        return 1

    # Output structured verdicts to stdout
    output = {"verdicts": verdicts}
    print(json.dumps(output, indent=2))

    # Log and collect results
    fail_count = 0
    partial_count = 0
    pass_count = 0

    for v in verdicts:
        criterion = v.get("criterion", "")
        verdict = v.get("verdict", "").upper()
        evidence = v.get("evidence", "")

        if verdict == "FAIL":
            fail_count += 1
            logger.warning(f"AC criterion FAIL — {criterion}: {evidence}")
        elif verdict == "PARTIAL":
            partial_count += 1
            logger.info(f"AC criterion PARTIAL — {criterion}: {evidence}")
            print(f"WARNING: AC criterion PARTIAL — {criterion}: {evidence}", file=sys.stderr)
        else:
            pass_count += 1
            logger.info(f"AC criterion PASS — {criterion}: {evidence}")

    logger.info(f"Evaluation complete: {pass_count} PASS, {partial_count} PARTIAL, {fail_count} FAIL")

    if fail_count > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
