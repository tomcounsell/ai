"""Unit tests for ``agent.session_runner.liveness.derive_sdk_ever_output``.

Owner directive (2026-07-07): the ``sdk_ever_output`` derivation is
relocated from ``agent/session_health.py`` (worker-owned, inline) to
``agent/session_runner/liveness.py`` (runner-owned, single exported
function). ``session_health.py`` becomes a pure consumer. See
``docs/plans/headless-runner-zombie-liveness.md``.

These tests cover all 2^3 combinations of the three OR-inputs
(``last_tool_use_at``, ``last_turn_at``, ``last_stdout_at``) — the fix's
whole point is that ``last_stdout_at`` alone (the headless per-stream
liveness signal) must be sufficient, which was previously NOT the case
(the pre-fix derivation only OR'd the first two fields).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from agent.session_runner.liveness import derive_sdk_ever_output


@dataclass
class _FakeEntry:
    last_tool_use_at: datetime | None = None
    last_turn_at: datetime | None = None
    last_stdout_at: datetime | None = None


_NOW = datetime.now(tz=UTC)


@pytest.mark.parametrize(
    "last_tool_use_at,last_turn_at,last_stdout_at,expected",
    [
        (None, None, None, False),
        (_NOW, None, None, True),
        (None, _NOW, None, True),
        (None, None, _NOW, True),
        (_NOW, _NOW, None, True),
        (_NOW, None, _NOW, True),
        (None, _NOW, _NOW, True),
        (_NOW, _NOW, _NOW, True),
    ],
)
def test_derive_sdk_ever_output_all_combinations(
    last_tool_use_at, last_turn_at, last_stdout_at, expected
):
    entry = _FakeEntry(
        last_tool_use_at=last_tool_use_at,
        last_turn_at=last_turn_at,
        last_stdout_at=last_stdout_at,
    )
    assert derive_sdk_ever_output(entry) is expected


def test_derive_sdk_ever_output_missing_attrs_default_false():
    """An entry missing the fields entirely (getattr default) is False."""

    class _Bare:
        pass

    assert derive_sdk_ever_output(_Bare()) is False


def test_derive_sdk_ever_output_stdout_only_is_true():
    """The headless-runner regression case: a toolless streaming turn that
    has only ever stamped ``last_stdout_at`` must derive True — this is the
    bug this plan fixes (previously the derivation ignored this field
    entirely)."""
    entry = _FakeEntry(last_stdout_at=_NOW)
    assert derive_sdk_ever_output(entry) is True
