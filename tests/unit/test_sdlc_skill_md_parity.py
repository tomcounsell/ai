"""Parity test: SKILL.md dispatch table must match agent.sdlc_router.DISPATCH_RULES.

This test prevents drift between the human-readable runbook in
``.claude/skills/sdlc/SKILL.md`` and the Python reference implementation in
``agent/sdlc_router.py``. Each row in the markdown table is cross-checked
with a ``DispatchRule`` of the matching ``row_id``.

Checked per row:
  - ``skill`` — must match exactly.
  - ``state_predicate.__doc__`` — must match the markdown State cell after
    whitespace / case normalization.

NOT checked per row:
  - The "Reason" column. It is descriptive only and can evolve freely for
    human readability without breaking CI.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.sdlc_router import DISPATCH_RULES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_MD = REPO_ROOT / ".claude" / "skills" / "sdlc" / "SKILL.md"


# ---------------------------------------------------------------------------
# Markdown table parser
# ---------------------------------------------------------------------------


_ROW_NUMBER_RE = re.compile(r"^\d+[a-z]?$")


def _split_cells(row_text: str) -> list[str]:
    """Split a single-line markdown table row on unescaped pipes.

    Tolerates ``\\|`` as an escaped pipe (kept literal in the cell). Strips
    leading/trailing whitespace from each cell.
    """
    # Replace escaped pipes with a placeholder, then split on real pipes.
    placeholder = "\x00PIPE\x00"
    safe = row_text.replace(r"\|", placeholder)
    parts = safe.split("|")
    # First and last entries are empty (row starts/ends with |)
    cells = [p.strip().replace(placeholder, "|") for p in parts]
    # Strip leading/trailing empty cells
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    """Markdown table separator row: ``| --- | --- | ...``"""
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def parse_dispatch_rows(md: str) -> list[dict]:
    """Parse the Step 4 dispatch table into a list of row dicts.

    Each dict has keys ``row_id``, ``state``, ``skill``. Only rows whose first
    cell is a row number (``1``, ``4a``, ``10b``, ...) are returned — the
    header row and separator row are dropped.

    The parser:
      - Finds the start of Step 4's dispatch table by header anchor.
      - Reads consecutive lines beginning with ``|``.
      - Splits on unescaped pipes, tolerates ``<br>`` as an intra-cell
        line break (kept as a space).
    """
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## Step 4: Dispatch ONE Sub-Skill":
            start = i
            break
    if start is None:
        raise AssertionError("Could not locate '## Step 4: Dispatch ONE Sub-Skill' in SKILL.md")

    rows: list[dict] = []
    in_table = False
    for line in lines[start:]:
        if not line.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        cells = _split_cells(line)
        if len(cells) < 3:
            continue
        if _is_separator_row(cells):
            continue
        row_id_cell = cells[0]
        if not _ROW_NUMBER_RE.fullmatch(row_id_cell):
            # skip header row "| # | State | Invoke | Reason |"
            continue
        state = cells[1].replace("<br>", " ")
        skill_cell = cells[2]
        # Skills in markdown are wrapped in backticks, e.g. `/do-plan {slug}`
        skill_match = re.search(r"`(/do-[a-z-]+)", skill_cell)
        skill = skill_match.group(1) if skill_match else skill_cell.strip("`")
        rows.append({"row_id": row_id_cell, "state": state, "skill": skill})

    return rows


def _normalize(text: str) -> str:
    r"""Lowercase, drop backticks, collapse whitespace, strip punctuation.

    Backticks are markdown formatting — the Python docstring uses plain text.
    Dropping them lets a markdown cell like ``\`revision_applied\` not set``
    match the docstring ``revision_applied not set``.
    """
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text.lower().strip())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"


def test_parser_finds_expected_row_numbers():
    md = SKILL_MD.read_text(encoding="utf-8")
    rows = parse_dispatch_rows(md)
    row_ids = [r["row_id"] for r in rows]
    # Canonical row_ids from SKILL.md
    expected = ["1", "2", "3", "4a", "4b", "4c", "5", "6", "7", "8", "8b", "9", "10", "10b"]
    assert row_ids == expected, f"Unexpected row_ids: {row_ids}"


def test_every_markdown_row_has_matching_dispatch_rule():
    md = SKILL_MD.read_text(encoding="utf-8")
    rows = parse_dispatch_rows(md)
    rules_by_row = {r.row_id: r for r in DISPATCH_RULES}
    missing = [row["row_id"] for row in rows if row["row_id"] not in rules_by_row]
    assert not missing, f"SKILL.md rows missing from DISPATCH_RULES: {missing}"


def test_every_dispatch_rule_has_matching_markdown_row():
    md = SKILL_MD.read_text(encoding="utf-8")
    rows = parse_dispatch_rows(md)
    row_ids_in_md = {r["row_id"] for r in rows}
    missing = [rule.row_id for rule in DISPATCH_RULES if rule.row_id not in row_ids_in_md]
    assert not missing, f"DISPATCH_RULES rows missing from SKILL.md: {missing}"


def test_skill_strings_match():
    md = SKILL_MD.read_text(encoding="utf-8")
    rows = parse_dispatch_rows(md)
    rules_by_row = {r.row_id: r for r in DISPATCH_RULES}
    mismatches = []
    for row in rows:
        rule = rules_by_row.get(row["row_id"])
        if rule is None:
            continue
        if rule.skill != row["skill"]:
            mismatches.append(f"row {row['row_id']}: md={row['skill']!r} py={rule.skill!r}")
    assert not mismatches, "Skill mismatches:\n" + "\n".join(mismatches)


def test_state_cell_matches_predicate_docstring():
    """For each row, the state predicate's __doc__ must match the markdown
    State cell after normalization."""
    md = SKILL_MD.read_text(encoding="utf-8")
    rows = parse_dispatch_rows(md)
    rules_by_row = {r.row_id: r for r in DISPATCH_RULES}
    mismatches = []
    for row in rows:
        rule = rules_by_row.get(row["row_id"])
        if rule is None:
            continue
        doc = rule.state_predicate.__doc__ or ""
        if _normalize(doc) != _normalize(row["state"]):
            mismatches.append(
                f"row {row['row_id']}:\n  md state: {row['state']!r}\n  py __doc__: {doc!r}"
            )
    assert not mismatches, "State mismatches:\n\n" + "\n\n".join(mismatches)


# ---------------------------------------------------------------------------
# Negative tests: verify the parity catches drift
# ---------------------------------------------------------------------------


def test_parity_detects_skill_mutation():
    """Inject a bad row into an in-memory copy of SKILL.md and verify the
    parity logic produces a readable row-level diff rather than silently
    passing."""
    md = SKILL_MD.read_text(encoding="utf-8")
    mutated = md.replace(
        "| 1 | No plan exists | `/do-plan {slug}`",
        "| 1 | No plan exists | `/do-bogus {slug}`",
    )
    rows = parse_dispatch_rows(mutated)
    row_1 = next(r for r in rows if r["row_id"] == "1")
    rules_by_row = {r.row_id: r for r in DISPATCH_RULES}
    assert rules_by_row["1"].skill != row_1["skill"], (
        "Parity test would silently pass on real skill drift"
    )


def test_parity_detects_state_cell_mutation():
    """Mutation to the state cell should produce a detectable mismatch."""
    md = SKILL_MD.read_text(encoding="utf-8")
    mutated = md.replace(
        "| 1 | No plan exists |",
        "| 1 | Completely different wording |",
    )
    rows = parse_dispatch_rows(mutated)
    row_1 = next(r for r in rows if r["row_id"] == "1")
    rules_by_row = {r.row_id: r for r in DISPATCH_RULES}
    rule_1 = rules_by_row["1"]
    assert _normalize(row_1["state"]) != _normalize(rule_1.state_predicate.__doc__ or "")


def test_escaped_pipe_in_cell_is_preserved():
    """Escaped pipe ``\\|`` must not split a cell."""
    cells = _split_cells(r"| row | cell with \| escaped pipe | skill |")
    assert cells == ["row", "cell with | escaped pipe", "skill"]
