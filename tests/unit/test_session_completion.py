"""Drafter-isolation regression guard for session_completion.py.

Issue #1148, Risk 4: the completion-runner draft+refine passes call
get_response_via_harness with model="opus" but MUST NOT pass the PM
persona via system_prompt — that would taint drafter turns with PM
orchestration rules and corrupt the user-facing summary.

This file holds two complementary guards:

1. A source-level check that no call to get_response_via_harness inside
   session_completion.py supplies a system_prompt= kwarg.
2. An AST-level check that walks every Call node and asserts the same.

Both are zero-side-effect static checks; together they catch a future
refactor that accidentally threads persona text through the drafter.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_COMPLETION_PATH = Path(__file__).resolve().parent.parent.parent / "agent" / "session_completion.py"


def test_drafter_calls_omit_system_prompt():
    """No call site in session_completion.py may pass system_prompt= to harness.

    This is the canonical Risk 4 regression guard cited by docs/plans/sdlc-1148.md
    and the plan's Verification table.
    """
    source = _COMPLETION_PATH.read_text()
    # Reject any literal system_prompt= kwarg anywhere in the file. This is
    # narrower and stricter than a positional check because the harness
    # function signature uses keyword-only args after *, so kwargs are the
    # only way to set system_prompt.
    matches = re.findall(r"\bsystem_prompt\s*=", source)
    assert matches == [], (
        f"session_completion.py must not pass system_prompt= to the harness; "
        f"found {len(matches)} occurrence(s). This is a Risk 4 regression "
        f"(see docs/plans/sdlc-1148.md): drafter turns must run with the "
        f"default Claude Code system prompt, not the PM persona."
    )


def test_drafter_calls_omit_system_prompt_via_ast():
    """AST guard: every get_response_via_harness call has no system_prompt kwarg."""
    source = _COMPLETION_PATH.read_text()
    tree = ast.parse(source)
    found_calls = 0
    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match either bare `get_response_via_harness(...)` or
        # `module.get_response_via_harness(...)`.
        callee = node.func
        name: str | None = None
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        if name != "get_response_via_harness":
            continue
        found_calls += 1
        for kw in node.keywords:
            if kw.arg == "system_prompt":
                offenders.append(node.lineno)

    assert found_calls >= 2, (
        f"Expected >=2 get_response_via_harness call sites in session_completion.py "
        f"(Pass 1 draft + Pass 2 refine). Found {found_calls}; the file may have "
        f"been refactored — re-validate the drafter isolation."
    )
    assert offenders == [], (
        f"get_response_via_harness call(s) at line(s) {offenders} pass a "
        f"system_prompt kwarg. Drafter turns must omit system_prompt (Risk 4)."
    )


@pytest.mark.parametrize("call_lineno_anchor", [564, 626])
def test_drafter_call_sites_at_expected_lines(call_lineno_anchor):
    """Sanity: the documented drafter call lines still resolve to a harness call.

    The plan cites session_completion.py:564 (Pass 1) and :626 (Pass 2). The
    original anchors at 525/587 shifted by ~39 lines in #1195 when the
    continuation-PM spawn site grew an extended docstring, a parent
    ``session_id`` guard, and switched from raw ``_AgentSession.create`` to
    the typed ``create_pm`` factory.

    A future refactor that moves these calls is fine as long as the AST
    guard above stays green, but this test pins the documented anchors so
    drift is visible.
    """
    source_lines = _COMPLETION_PATH.read_text().splitlines()
    # Window scan: 5 lines before and after the anchor — the harness call
    # spans multiple lines (kwargs on separate lines).
    start = max(0, call_lineno_anchor - 5)
    end = min(len(source_lines), call_lineno_anchor + 5)
    window = "\n".join(source_lines[start:end])
    assert "get_response_via_harness" in window, (
        f"Expected get_response_via_harness near line {call_lineno_anchor} in "
        f"session_completion.py (window {start}-{end}). If the file has been "
        f"refactored, update the anchor in this test and in docs/plans/sdlc-1148.md."
    )
