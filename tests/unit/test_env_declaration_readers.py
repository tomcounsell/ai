"""Recurrence guard: every `.env.example` declaration must have a reader (#2845).

A declaration passes through one of two legs:

1. Literal occurrence in a tracked, non-markdown file (excluding
   `.env.example` itself and this test file — see the self-exclusion note
   below).
2. A `@passthrough <binary>` sigil in the declaration's own comment block —
   for keys an external binary reads straight out of the environment, where
   no tracked Python can name them.

No hand-written allowlist. No second sigil parser here — `@passthrough` is
read exclusively through `_parse_env_example`'s fourth return element.

**Self-exclusion is load-bearing, not tidiness.** The leg-2 pin below writes
all three passthrough key names into this very file (to assert the set is
exactly those three). Without excluding this file from leg 1's pathspec,
those three names would satisfy leg 1 by their own mention here, and the
`@passthrough`-removal mutation (below) would come back green instead of red.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.update.verify import _parse_env_example

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
THIS_FILE_RELATIVE = "tests/unit/test_env_declaration_readers.py"

EXPECTED_PASSTHROUGH_KEYS = {
    "OP_SERVICE_ACCOUNT_TOKEN",
    "HEADSCALE_SERVER_URL",
    "HEADSCALE_PREAUTH_KEY",
}


def _has_tracked_non_markdown_reader(key: str) -> bool:
    """Leg 1: a literal occurrence in a tracked, non-markdown file."""
    result = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            "-F",
            key,
            "--",
            ":!*.md",
            ":!.env.example",
            f":!{THIS_FILE_RELATIVE}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # `git grep` exits 1 on zero matches — gate on stdout content, not $?.
    return bool(result.stdout.strip())


def _declared() -> list[tuple[str, str, bool, str | None]]:
    return _parse_env_example(ENV_EXAMPLE)


def test_every_declaration_has_a_reader():
    """Every `.env.example` key clears leg 1 (tracked non-markdown occurrence)
    or leg 2 (`@passthrough` sigil)."""
    unreadable = []
    for key, _description, _optional, passthrough in _declared():
        if passthrough:
            continue
        if not _has_tracked_non_markdown_reader(key):
            unreadable.append(key)
    assert unreadable == [], (
        f"Declarations with no reader through either leg: {unreadable}. "
        "Either delete the declaration (nothing reads it) or add a "
        "`# @passthrough <binary>` sigil if an external binary reads it "
        "straight out of the environment."
    )


def test_passthrough_set_is_exactly_the_three_named_keys():
    """Leg 2 stays a three-line statement of fact, never an escape hatch."""
    passthrough_keys = {key for key, _d, _o, passthrough in _declared() if passthrough}
    assert passthrough_keys == EXPECTED_PASSTHROUGH_KEYS


def test_optional_and_passthrough_sets_are_disjoint():
    declared = _declared()
    optional_keys = {key for key, _d, optional, _p in declared if optional}
    passthrough_keys = {key for key, _d, _o, passthrough in declared if passthrough}
    assert optional_keys.isdisjoint(passthrough_keys)


def test_service_label_prefix_is_not_marked_optional():
    """SERVICE_LABEL_PREFIX is declaration #0, directly below the header
    block. It is required install-time launchd config with no other Risk-1
    defence — the doubly-bounded control for a header-block bleed (a
    dropped blank line or `# ====` separator would mark it @optional)."""
    optional_keys = {key for key, _d, optional, _p in _declared() if optional}
    assert "SERVICE_LABEL_PREFIX" not in optional_keys


def test_deleted_declarations_have_no_reader_mutation():
    """Restoring one of the three deleted declarations must fail leg 1 —
    the red-state demo for the guard's own usefulness. Run live rather than
    asserted as a fixed list, so it stays truthful if the reader surface
    ever changes."""
    for dead_key in ("OLLAMA_URL", "OLLAMA_VISION_MODEL", "SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES"):
        assert not _has_tracked_non_markdown_reader(dead_key), (
            f"{dead_key} was deleted from .env.example because nothing reads it; "
            "a tracked non-markdown reader has appeared. Either the deletion was "
            "wrong or a new consumer needs to name it in .env.example again."
        )


@pytest.mark.parametrize("key", sorted(EXPECTED_PASSTHROUGH_KEYS))
def test_passthrough_keys_fail_leg_one_on_their_own(key: str):
    """Each @passthrough key genuinely has no tracked non-markdown reader —
    proving leg 2 is load-bearing, not decorative. (Confirms leg 1 alone
    would fail without the sigil; this is what makes the guard
    unsatisfiable without @passthrough, per Technical Approach.)"""
    assert not _has_tracked_non_markdown_reader(key)
