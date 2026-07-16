"""Tests for reflections/housekeeping/test_baseline_refresh_check.py (#1933, #2004).

The reflection shares the ONE staleness definition with the merge gate: it
reads the baseline's :class:`ArtifactEnvelope` and calls
``scripts._baseline_common.staleness()`` (age > 14 days, dirty commit,
commit distance) instead of reimplementing an age-only check. It runs no
tests of its own -- only reads a small JSON file -- so these tests only ever
point ``DEFAULT_BASELINE_PATH`` at a temp file, never touch the real
machine-local baseline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import reflections.housekeeping.test_baseline_refresh_check as check


def _write_baseline(
    path: Path,
    generated_at: str | None,
    schema_version: int = 2,
    commit: str | None = None,
) -> None:
    payload: dict = {"schema_version": schema_version, "tests": {}}
    if generated_at is not None:
        payload["generated_at"] = generated_at
    if commit is not None:
        payload["commit"] = commit
    path.write_text(json.dumps(payload))


def test_stale_baseline_returns_warning_status(tmp_path: Path) -> None:
    baseline_path = tmp_path / "main_test_baseline.json"
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    _write_baseline(baseline_path, generated_at=old)

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "warning"
    assert result["findings"]
    assert "30 days old" in result["findings"][0] or "days old" in result["findings"][0]
    # The remediation must name the timeout-safe launcher (issue #2066): a bare
    # foreground `refresh_test_baseline.py` is killed at the 10-min bash cap, so
    # the actionable command surfaced to the operator is the detached wrapper.
    assert "refresh_baseline_detached.sh" in result["findings"][0]


def test_fresh_baseline_returns_ok_status(tmp_path: Path) -> None:
    baseline_path = tmp_path / "main_test_baseline.json"
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _write_baseline(baseline_path, generated_at=fresh)

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "ok"
    assert result["findings"] == []


def test_missing_baseline_file_is_benign(tmp_path: Path) -> None:
    baseline_path = tmp_path / "does-not-exist.json"

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "ok"
    assert result["findings"] == []


def test_malformed_json_is_benign(tmp_path: Path) -> None:
    baseline_path = tmp_path / "main_test_baseline.json"
    baseline_path.write_text("{not valid json")

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "ok"
    assert result["findings"] == []


def test_missing_generated_at_is_benign(tmp_path: Path) -> None:
    baseline_path = tmp_path / "main_test_baseline.json"
    _write_baseline(baseline_path, generated_at=None)

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "ok"
    assert result["findings"] == []


def test_unparseable_generated_at_is_benign(tmp_path: Path) -> None:
    baseline_path = tmp_path / "main_test_baseline.json"
    _write_baseline(baseline_path, generated_at="not-a-date")

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "ok"
    assert result["findings"] == []


def test_dirty_commit_returns_warning_status(tmp_path: Path) -> None:
    """Shared staleness definition: a -dirty capture warns even when time-fresh."""
    baseline_path = tmp_path / "main_test_baseline.json"
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _write_baseline(baseline_path, generated_at=fresh, commit="abc1234-dirty")

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "warning"
    assert "dirty" in result["findings"][0]


def test_commit_distance_returns_warning_status(tmp_path: Path) -> None:
    """Shared staleness definition: far behind HEAD warns even when time-fresh."""
    baseline_path = tmp_path / "main_test_baseline.json"
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _write_baseline(baseline_path, generated_at=fresh, commit="abc1234")

    with (
        patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path),
        patch.object(check, "commits_behind_head", return_value=425),
    ):
        result = asyncio.run(check.run())

    assert result["status"] == "warning"
    assert "commits behind" in result["findings"][0]


def test_uses_shared_staleness_definition(tmp_path: Path) -> None:
    """The warning comes from scripts._baseline_common.staleness, not a local check."""
    baseline_path = tmp_path / "main_test_baseline.json"
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    _write_baseline(baseline_path, generated_at=fresh, commit="abc1234")

    with (
        patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path),
        patch.object(check, "staleness", return_value=["injected reason"]) as mock_staleness,
    ):
        result = asyncio.run(check.run())

    mock_staleness.assert_called_once()
    assert result["status"] == "warning"
    assert "injected reason" in result["findings"][0]


def test_run_never_raises_on_a_directory_where_a_file_is_expected(tmp_path: Path) -> None:
    """Defense in depth: even a load-time exception must be caught, not raised."""
    baseline_path = tmp_path / "is-a-directory"
    baseline_path.mkdir()

    with patch.object(check, "DEFAULT_BASELINE_PATH", baseline_path):
        result = asyncio.run(check.run())

    assert result["status"] == "ok"
