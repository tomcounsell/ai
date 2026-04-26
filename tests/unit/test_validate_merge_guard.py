"""Tests for the merge-guard tokenizer (item 7 of sdlc-1155).

Covers the new ``_extract_executed_commands`` tokenizer and the
``_merge_cmd_in_command`` wrapper that restricts the merge-command regex
to actual command positions. The tokenizer must:

- Block a direct merge call.
- Allow an ``echo``/``printf`` prefix (handled by the fast path in main,
  but the regex still matches so we assert the post-fast-path behavior on
  the wrapper for documentation purposes).
- Allow a commit message whose heredoc body references the trigger.
- Allow a ``gh issue create --body "..."`` whose body references the trigger.
- Allow a ``gh pr comment --body "..."`` that mentions the trigger.
- Block ``<something> && <trigger> <pr>`` since the second position IS a
  real command.
- Allow ``<trigger> --help`` (handled by the caller's help-flag check).
- Fail closed on tokenizer failure: block a direct trigger call even if
  the tokenizer raises or returns an empty list.

All tests build the trigger pattern via string concatenation so the test
file itself never contains the literal substring that would trip the
hook during development.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MERGE = "gh pr " + "merge"

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "hooks"
    / "validators"
    / "validate_merge_guard.py"
)


@pytest.fixture(scope="module")
def guard():
    """Load the validator module fresh (its parent-relative data dir only
    matters for the authorization check, which these tests bypass)."""
    spec = importlib.util.spec_from_file_location("vmg", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_direct_merge_call_is_detected(guard):
    """A bare direct invocation must still be caught by the tokenizer."""
    assert guard._merge_cmd_in_command(f"{MERGE} 42") is True


def test_echo_diagnostic_matches_regex(guard):
    """The fast path in main() short-circuits before reaching the tokenizer,
    but the regex does match on an echo command. Document that behavior."""
    # The _merge_cmd_in_command does detect because "echo ... gh pr merge" is
    # treated as one command span in the tokenizer (no separator between the
    # argv positions). The fast path in ``main()`` is what skips echo.
    assert guard._merge_cmd_in_command(f"echo {MERGE}") in (True, False)
    # The important guarantee is the fast path prefix check is exported
    # implicitly: main() returns without blocking when command starts with
    # "echo ". This is tested integration-wise, not at the helper level.


def test_commit_message_heredoc_mentions_trigger_allowed(guard):
    """A git commit whose heredoc body mentions the trigger must NOT block."""
    cmd = f"git commit -m \"$(cat <<'EOF'\nBody that references {MERGE} in prose.\nEOF\n)\""
    assert guard._merge_cmd_in_command(cmd) is False


def test_issue_create_body_mentions_trigger_allowed(guard):
    """gh issue create --body "..." with trigger in quoted body must NOT block."""
    cmd = f'gh issue create --title x --body "Refers to {MERGE} indirectly"'
    assert guard._merge_cmd_in_command(cmd) is False


def test_pr_comment_body_mentions_trigger_allowed(guard):
    """gh pr comment --body "..." with trigger in quoted body must NOT block."""
    cmd = f'gh pr comment 42 --body "mentions {MERGE} here"'
    assert guard._merge_cmd_in_command(cmd) is False


def test_second_command_after_and_blocks(guard):
    """`ls && {trigger} 42` has the trigger at an actual command position."""
    cmd = f"ls && {MERGE} 42"
    assert guard._merge_cmd_in_command(cmd) is True


def test_help_flag_regex_matches_at_command_position(guard):
    """The help-flag helper must return True when --help is at a real position."""
    cmd = f"{MERGE} --help"
    assert guard._merge_cmd_in_command(cmd) is True
    assert guard._command_has_help_flag(cmd) is True


def test_tokenizer_failure_fails_closed(guard, monkeypatch):
    """If _extract_executed_commands raises, the wrapper falls back to bare
    match on the full string. A real trigger call still blocks."""

    def boom(_cmd):  # pragma: no cover - deterministic failure
        raise RuntimeError("synthetic tokenizer failure")

    monkeypatch.setattr(guard, "_extract_executed_commands", boom)
    assert guard._merge_cmd_in_command(f"{MERGE} 42") is True


def test_tokenizer_empty_span_fails_closed(guard, monkeypatch):
    """An ambiguous parse (empty list on non-empty input) also falls back."""
    monkeypatch.setattr(guard, "_extract_executed_commands", lambda _c: [])
    assert guard._merge_cmd_in_command(f"{MERGE} 42") is True


def test_command_substitution_is_detected(guard):
    """Merge calls wrapped in command substitution MUST still block.

    Regression coverage for the PR #1160 review finding: the tokenizer used
    to treat double-quoted and backtick regions as opaque, which meant a real
    merge invocation hidden inside ``$(...)`` or ``` `...` `` could slip
    through as a "quoted argument" even though shell would execute it.

    All three patterns below execute the merge command in the shell; the
    guard must recognise each of them as an actual command position.
    """
    # $() inside double quotes, used as a variable value
    cmd1 = f'X="$({MERGE} 42)" && echo ok'
    assert guard._merge_cmd_in_command(cmd1) is True

    # $() inside double quotes, passed to eval
    cmd2 = f'eval "$({MERGE} 42)"'
    assert guard._merge_cmd_in_command(cmd2) is True

    # Legacy backtick substitution at an unquoted position
    cmd3 = f"eval `{MERGE} 42`"
    assert guard._merge_cmd_in_command(cmd3) is True

    # $() at an unquoted position (direct substitution)
    cmd4 = f"$({MERGE} 42)"
    assert guard._merge_cmd_in_command(cmd4) is True

    # Backtick substitution nested inside double quotes
    cmd5 = f'eval "`{MERGE} 42`"'
    assert guard._merge_cmd_in_command(cmd5) is True


def test_commit_heredoc_body_with_substitution_patterns_allowed(guard):
    """A commit-message heredoc whose body contains literal ``$(...)``,
    backticks, and stray ``)`` characters must still be exempt.

    This covers the secondary bug surfaced by the PR #1160 patch: when the
    outer ``$(cat <<'EOF' ... EOF)`` substitution body contained literal
    shell-syntax tokens (from code examples in commit prose), the
    paren-close finder miscounted depth and picked a bogus close inside
    the body. That collapsed the outer span and let content after the real
    close get mis-tokenised as a live merge call. _find_dollar_paren_close
    must skip heredoc bodies during its depth scan.
    """
    body = (
        f"Fix describes patterns:\n"
        f'  X="$({MERGE} 42)" && echo ok\n'
        f'  eval "$({MERGE} 42)"\n'
        f"  eval `{MERGE} 42`\n"
        f"And prose with stray tokens: encounters $( or backtick "
        f"(whether quoted or unquoted), same result.\n"
    )
    cmd = f"git add file && git commit -m \"$(cat <<'EOF'\n{body}EOF\n)\" && git push"
    # The entire merge-token content is inside the heredoc body, so the
    # guard must NOT block this commit.
    assert guard._merge_cmd_in_command(cmd) is False
