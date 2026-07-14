"""Parity test: SKILL.md Step 4 must delegate to the routing tool; DISPATCH_RULES
must have documented predicates; guard table must match Python GUARDS list.

After Phase 2 (issue #1216), the SKILL.md Step 4 dispatch table was replaced by
a single ``sdlc-tool next-skill`` call.  The hand-edited table no longer exists,
so the old row-by-row comparison is gone.

This rewrite asserts:

1. SKILL.md Step 4 no longer contains a hand-authored dispatch table (no rows
   like ``| 1 | ... |``).
2. SKILL.md Step 4 references ``sdlc-tool next-skill`` as the routing entry point.
3. Every ``DispatchRule`` in ``DISPATCH_RULES`` has a non-empty ``__doc__`` on its
   ``state_predicate`` (quality gate — predicates must remain documented even
   though they are no longer cross-checked against markdown).
4. Guard table parity: every guard ID in SKILL.md Step 3.5 has a matching
   callable in the exported ``GUARDS`` list.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.sdlc_router import DISPATCH_RULES, GUARDS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_MD = REPO_ROOT / ".claude" / "skills" / "sdlc" / "SKILL.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_step4_section(md: str) -> str:
    """Return the text of the Step 4 section (up to the next ## heading)."""
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("## Step 4: Dispatch ONE Sub-Skill"):
            start = i
            break
    if start is None:
        return ""

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## ") and i > start:
            end = i
            break

    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Tests: SKILL.md Step 4 uses the tool (no hand-authored table)
# ---------------------------------------------------------------------------


def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"


def test_step4_references_next_skill_tool():
    """SKILL.md Step 4 must reference ``sdlc-tool next-skill`` as the routing entry point."""
    md = SKILL_MD.read_text(encoding="utf-8")
    section = _extract_step4_section(md)
    assert section, "Could not locate '## Step 4: Dispatch ONE Sub-Skill' in SKILL.md"
    assert "sdlc-tool next-skill" in section, (
        "SKILL.md Step 4 must reference 'sdlc-tool next-skill' as the routing tool.\n"
        "Found section:\n" + section[:500]
    )


def test_step4_has_no_hand_authored_dispatch_table():
    """SKILL.md Step 4 must NOT contain a hand-authored dispatch table (rows like ``| 1 | ...``).

    The table was replaced by a ``sdlc-tool next-skill`` call in issue #1216.
    If this test fails, someone re-introduced the manual table — revert and use
    the tool instead.
    """
    md = SKILL_MD.read_text(encoding="utf-8")
    section = _extract_step4_section(md)
    assert section, "Could not locate '## Step 4: Dispatch ONE Sub-Skill' in SKILL.md"

    # Row-number pattern: lines like "| 1 |", "| 4a |", "| 10b |"
    row_number_re = re.compile(r"^\|\s*\d+[a-z]?\s*\|", re.MULTILINE)
    matches = row_number_re.findall(section)
    assert not matches, (
        f"SKILL.md Step 4 contains {len(matches)} hand-authored dispatch table row(s). "
        "These should be removed — routing is now delegated to 'sdlc-tool next-skill'.\n"
        "Offending lines: " + str(matches[:5])
    )


def test_step4_references_blocked_output_contract():
    """SKILL.md Step 4 must document the ``blocked`` JSON key output contract."""
    md = SKILL_MD.read_text(encoding="utf-8")
    section = _extract_step4_section(md)
    assert section, "Could not locate '## Step 4: Dispatch ONE Sub-Skill' in SKILL.md"
    assert '"blocked"' in section or "blocked" in section, (
        "SKILL.md Step 4 must document what to do when the tool returns a blocked decision.\n"
        "Add instructions for handling {'blocked': true, 'reason': '...', 'guard_id': '...'}."
    )


# ---------------------------------------------------------------------------
# Tests: DISPATCH_RULES predicate quality
# ---------------------------------------------------------------------------


def test_every_dispatch_rule_has_documented_predicate():
    """Every DispatchRule's state_predicate must have a non-empty __doc__.

    Predicates are no longer compared against SKILL.md row-by-row, but they
    must remain self-documenting for maintainability and test traceability.
    """
    undocumented = []
    for rule in DISPATCH_RULES:
        doc = rule.state_predicate.__doc__ or ""
        if not doc.strip():
            undocumented.append(rule.row_id)
    assert not undocumented, (
        f"DispatchRules with undocumented state_predicate: {undocumented}\n"
        "Add a docstring to each predicate function."
    )


def test_dispatch_rules_cover_expected_row_ids():
    """DISPATCH_RULES must cover the canonical row set (1–10).

    Row 10b (stage_states-unavailable merge fallback) was deleted in #2003 —
    it actively weakened the "never merge unfinished work" invariant. Merge
    enforcement lives in the merge-guard hook via ``tools.merge_predicate``;
    rows 9/10 and G6 remain scheduling-only.
    """
    expected = {
        "1",
        "2",
        "2b",
        "2c",
        "3",
        "4a",
        "4b",
        "4c",
        "5",
        "6",
        "7",
        "8",
        "8b",
        "8c",
        "8d",
        "8e",
        "8f",
        "9",
        "10",
    }
    actual = {r.row_id for r in DISPATCH_RULES}
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"DISPATCH_RULES missing expected rows: {missing}"
    assert not extra, f"DISPATCH_RULES has unexpected rows: {extra}"


# ---------------------------------------------------------------------------
# Guard table parity (Step 3.5 in SKILL.md)
# ---------------------------------------------------------------------------


_GUARD_ROW_RE = re.compile(r"^G\d+")


def _split_cells(row_text: str) -> list[str]:
    """Split a single-line markdown table row on unescaped pipes."""
    placeholder = "\x00PIPE\x00"
    safe = row_text.replace(r"\|", placeholder)
    parts = safe.split("|")
    cells = [p.strip().replace(placeholder, "|") for p in parts]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def parse_guard_rows(md: str) -> list[dict]:
    """Parse the Step 3.5 guard table from SKILL.md."""
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "Step 3.5" in line and "Guard" in line:
            start = i
            break
    if start is None:
        raise AssertionError("Could not locate 'Step 3.5' guards section in SKILL.md")

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
        guard_id_cell = cells[0]
        match = _GUARD_ROW_RE.match(guard_id_cell.strip())
        if not match:
            continue
        guard_id = match.group(0)
        rows.append(
            {
                "guard_id": guard_id,
                "condition": cells[1].replace("<br>", " "),
                "forced_dispatch": cells[2],
            }
        )
    return rows


def test_guard_row_ids_in_python():
    """Every guard_id found by parse_guard_rows has a matching callable in GUARDS."""
    md = SKILL_MD.read_text(encoding="utf-8")
    guard_rows = parse_guard_rows(md)
    assert guard_rows, "No guard rows found — parse_guard_rows returned empty list"

    guard_names = {g.__name__.lower() for g in GUARDS}

    missing = []
    for row in guard_rows:
        guard_id = row["guard_id"].lower()
        if not any(name.startswith(f"guard_{guard_id}_") for name in guard_names):
            missing.append(row["guard_id"])

    assert not missing, (
        f"Guard IDs in SKILL.md without matching callables in GUARDS: {missing}\n"
        f"Available GUARDS: {sorted(guard_names)}"
    )


def test_g6_guard_row_present_in_skill_md():
    """SKILL.md Step 3.5 guard table must contain a G6 row."""
    md = SKILL_MD.read_text(encoding="utf-8")
    guard_rows = parse_guard_rows(md)
    guard_ids = [r["guard_id"] for r in guard_rows]
    assert "G6" in guard_ids, (
        f"G6 guard row not found in SKILL.md guard table. Found guard IDs: {guard_ids}"
    )


def test_escaped_pipe_in_cell_is_preserved():
    """Escaped pipe ``\\|`` must not split a cell."""
    cells = _split_cells(r"| row | cell with \| escaped pipe | skill |")
    assert cells == ["row", "cell with | escaped pipe", "skill"]
