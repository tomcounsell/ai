"""Shared fixtures for the tools.sdlc_session_ensure test package (#2879)."""

from unittest.mock import patch

import pytest

_LANE_IDENTITY_CLASS = "TestLaneSlugMintedAtLaneStart"


@pytest.fixture(autouse=True)
def _stub_lane_identity(request):
    """Neutralize lane-slug resolution (#2735) outside the class that tests it.

    ``ensure_session`` mints the lane's identity on entry, which peeks the issue
    lock for the pinned target repo and writes a ``PipelineLedger``. That is
    correct behavior and ``TestLaneSlugMintedAtLaneStart`` asserts it -- but for
    every other test in this package it is noise: it adds a ``touch_issue_lock``
    call that the lock-wiring assertions count, and it leaves a real ledger
    record per test.
    """
    if request.cls is not None and request.cls.__name__ == _LANE_IDENTITY_CLASS:
        yield
        return
    with patch("tools.sdlc_session_ensure.resolve_lane_slug", return_value=None):
        yield
