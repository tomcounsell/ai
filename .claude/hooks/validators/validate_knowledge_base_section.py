#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Validate that a project's CLAUDE.md or README.md has a ## Knowledge Base (KB) section.

This is a SOFT validator — exit code is always 0. Warnings go to stderr.
Wire into CI or a pre-commit hook per-project if you want enforcement.

Convention: docs/conventions/knowledge-base-section.md

The section must name two things:
  1. A vault directory path under ~/work-vault/
  2. A memory project_key (matched by `--project <key>` or `project_key=<key>`)

Usage:
  uv run validate_knowledge_base_section.py CLAUDE.md
  uv run validate_knowledge_base_section.py README.md
  uv run validate_knowledge_base_section.py path/to/CLAUDE.md
"""

import argparse
import json
import re
import sys
from pathlib import Path

CONVENTION_URL = "docs/conventions/knowledge-base-section.md"

MISSING_SECTION_WARNING = """
WARNING: '{file}' is missing a ## Knowledge Base (KB) section.

This is a soft convention. See {convention} for the template.

Quick add (replace placeholders):

## Knowledge Base (KB)

**1. Vault (curated docs, iCloud-synced)**
- Location: `~/work-vault/<VAULT_DIR>/`
- Index: see that directory's `README.md` for the file index

**2. Memory system (Redis, agent-learned observations)**
- Project key: `<PROJECT_KEY>` (see `config/projects.json`)
- Search: `python -m tools.memory_search search "<query>" --project <PROJECT_KEY>`
"""

MISSING_VAULT_WARNING = """
WARNING: '{file}' has a ## Knowledge Base (KB) section but no vault path.

The section should name a directory under ~/work-vault/, e.g.:
  - Location: `~/work-vault/My Project/`

See {convention} for the full template.
"""

MISSING_PROJECT_KEY_WARNING = """
WARNING: '{file}' has a ## Knowledge Base (KB) section but no project_key reference.

The section should name the memory project_key, e.g.:
  - Project key: `my-project` (see `config/projects.json`)
  - Search: `python -m tools.memory_search search "..." --project my-project`

See {convention} for the full template.
"""


def extract_kb_section(content: str) -> str | None:
    """Extract the ## Knowledge Base (KB) section from file content.

    Matches either '## Knowledge Base (KB)' or '## Knowledge Base'.
    """
    match = re.search(
        r"^## Knowledge Base(?: \(KB\))?\s*$(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return None


def has_vault_path(section: str) -> bool:
    """Check the section references a ~/work-vault/ path."""
    return bool(re.search(r"~/work-vault/", section))


def has_project_key(section: str) -> bool:
    """Check the section references a project_key.

    Accepts any of:
      - `--project <key>`
      - `project_key=<key>` or `project_key: <key>`
      - 'Project key: `<key>`' (the template form)
    """
    patterns = [
        r"--project\s+\S",
        r"project_key\s*[:=]\s*\S",
        r"[Pp]roject\s+key\s*[:=]\s*`",
    ]
    return any(re.search(p, section) for p in patterns)


def validate(filepath: str) -> tuple[int, list[str]]:
    """Validate a file. Returns (warning_count, messages)."""
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return 1, [f"WARNING: failed to read '{filepath}': {e}"]

    section = extract_kb_section(content)
    if section is None:
        return 1, [MISSING_SECTION_WARNING.format(file=filepath, convention=CONVENTION_URL)]

    warnings = []
    if not has_vault_path(section):
        warnings.append(MISSING_VAULT_WARNING.format(file=filepath, convention=CONVENTION_URL))
    if not has_project_key(section):
        warnings.append(
            MISSING_PROJECT_KEY_WARNING.format(file=filepath, convention=CONVENTION_URL)
        )

    return len(warnings), warnings


def main():
    parser = argparse.ArgumentParser(
        description="Soft validator for ## Knowledge Base (KB) section"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default="CLAUDE.md",
        help="Path to CLAUDE.md or README.md (default: CLAUDE.md)",
    )
    args = parser.parse_args()

    # Consume stdin if provided (SDK passes context via stdin)
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    if not Path(args.file).exists():
        # Soft: not an error if the target file doesn't exist
        print(json.dumps({"result": "continue", "message": f"skipped: {args.file} not found"}))
        sys.exit(0)

    warning_count, messages = validate(args.file)

    if warning_count == 0:
        print(
            json.dumps(
                {
                    "result": "continue",
                    "message": f"Knowledge Base section present and well-formed in {args.file}",
                }
            )
        )
    else:
        for msg in messages:
            print(msg, file=sys.stderr)
        print(
            json.dumps(
                {
                    "result": "continue",
                    "message": f"{warning_count} KB warning(s) in {args.file} (non-blocking)",
                }
            )
        )

    # Always exit 0 — this is warn-only.
    sys.exit(0)


if __name__ == "__main__":
    main()
