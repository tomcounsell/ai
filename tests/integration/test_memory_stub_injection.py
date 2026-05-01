"""Token-cost benchmark: stub format vs full-body thought blocks.

Asserts ≥5× token reduction (from the issue's stated goal). Uses
``tiktoken`` for the same tokenizer Anthropic-compatible counts agree
with on plain English text.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


def _count_tokens(strings: list[str]) -> int:
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    total = 0
    for s in strings:
        total += len(enc.encode(s))
    return total


def _make_record(memory_id: str, content: str, title: str, category: str) -> MagicMock:
    rec = MagicMock()
    rec.memory_id = memory_id
    rec.content = content
    rec.title = title
    rec.metadata = {"category": category}
    # confirm_access is best-effort and called by the formatter.
    rec.confirm_access = MagicMock()
    return rec


# Three ~300-token bodies (~1500 chars).
_BODY_TEMPLATE = (
    "When migrating the auth middleware to the new compliance schema, the "
    "session token storage layer must satisfy the legal requirements raised "
    "by the security review in PR #832. The migration runs in three phases: "
    "first, dual-write the new schema alongside the old one; second, switch "
    "reads to the new schema once at least 99% of records have been "
    "backfilled; third, drop the old columns after a soak window of seven "
    "days. Backfill is idempotent and resumable, retries on transient "
    "errors, and skips records whose new-schema fingerprint already matches. "
    "Manual rollback is non-destructive: re-run with --reverse and the "
    "schema returns to its pre-migration state. The new schema "
    "encrypts session tokens at rest using a per-tenant key derived from "
    "the tenant's signing certificate. Telemetry events emitted during the "
    "migration are stamped with phase=dual-write|read-switch|cleanup so the "
    "dashboard can graph progress per phase. ADD_NOISE_TO_PAD_TOKENS_HERE "
)


@pytest.mark.integration
def test_stub_format_at_least_5x_token_reduction():
    from hook_utils.memory_bridge import _format_stub_blocks, _format_thought_blocks

    bodies = [_BODY_TEMPLATE] * 3
    records_full = []
    records_stub = []
    for i, body in enumerate(bodies):
        records_full.append(_make_record(f"m{i}", body, "Auth migration plan", "decision"))
        records_stub.append(_make_record(f"m{i}", body, "Auth migration plan", "decision"))

    full_blocks, _ = _format_thought_blocks(records_full, max_results=10)
    stub_blocks, _ = _format_stub_blocks(records_stub, max_results=10)

    assert len(full_blocks) == 3
    assert len(stub_blocks) == 3

    full_tokens = _count_tokens(full_blocks)
    stub_tokens = _count_tokens(stub_blocks)

    # The body is large enough that the ratio should comfortably exceed 5×.
    assert stub_tokens > 0, "stubs must encode at least some tokens"
    ratio = full_tokens / stub_tokens
    assert ratio >= 5.0, (
        f"expected ≥5× token reduction, got {ratio:.2f}× ({full_tokens} → {stub_tokens})"
    )


@pytest.mark.integration
def test_stub_renders_id_and_category_even_without_title():
    """Graceful degradation: empty title still produces a useful stub."""
    from hook_utils.memory_bridge import _format_stub_blocks

    rec = _make_record("m-no-title", "some content", title="", category="correction")
    stubs, entries = _format_stub_blocks([rec])
    assert stubs == ['<thought id="m-no-title">[correction]</thought>']
    # Sidecar still carries full content for outcome detection.
    assert entries == [{"memory_id": "m-no-title", "content": "some content"}]
