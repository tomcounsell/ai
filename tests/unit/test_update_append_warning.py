"""Unit tests for scripts/update/run.py's `_append_warning`/`_append_error`
newline-collapse helpers (#2845).

`run.py:2482-2494` renders one `⚠️`/`-` bullet per list entry — a raw
multi-line entry (an exception `str()`, a wrapped multi-line diagnostic)
renders its sentinel on only the first physical line, dropping the rest.
These helpers are the recurrence guard for that whole class: every one of
the 85 warning/error-injection sites in `run.py` routes through one of them.
"""

from __future__ import annotations

import pytest

from scripts.update import readme_check
from scripts.update.run import UpdateResult, _append_error, _append_warning

pytestmark = pytest.mark.unit


def test_append_warning_collapses_embedded_newlines():
    result = UpdateResult()
    multiline = "line one\nline two\nline three"
    _append_warning(result, multiline)
    assert len(result.warnings) == 1
    assert "\n" not in result.warnings[0]
    assert "line one" in result.warnings[0]
    assert "line three" in result.warnings[0]


def test_append_error_collapses_embedded_newlines():
    result = UpdateResult()
    multiline = (
        "pydantic_core._pydantic_core.ValidationError: 1 validation error\n"
        "for Settings features.crash_autoresume_max_attempts\n"
        "Input should be a valid integer"
    )
    _append_error(result, multiline)
    assert len(result.errors) == 1
    assert "\n" not in result.errors[0]


def test_append_warning_single_line_is_unaffected():
    result = UpdateResult()
    _append_warning(result, "a plain single-line warning")
    assert result.warnings == ["a plain single-line warning"]


def test_readme_shaped_multiline_warning_collapses_complete():
    """readme_check.py:147-150's real nine-line _EXAMPLE_BLOCK shape."""
    result = UpdateResult()
    warning_text = (
        f"[popoto] README.md is missing a '{readme_check.REQUIRED_HEADING}' section\n"
        f"  Add the following to README.md:\n"
        f"{readme_check._EXAMPLE_BLOCK}"
    )
    _append_warning(result, warning_text)
    assert len(result.warnings) == 1
    assert "\n" not in result.warnings[0]
    assert "## Running" in result.warnings[0]
    assert "uvicorn app.main:app --reload" in result.warnings[0]


def test_readme_warnings_are_not_duplicated():
    """The dedup fix for run.py:1804-1807's N² bug: `result.warnings.extend`
    sat INSIDE the `for warn in rc.warnings:` loop, so N warnings produced
    N² entries. `_append_warning(result, warn)` per iteration makes it N —
    mirrors run.py's fixed loop shape exactly (`for warn in rc.warnings:
    _append_warning(result, warn)`)."""
    rc_warnings = [
        "[popoto] README.md is missing a '## Running' section",
        "[other-repo] README.md is missing a '## Running' section",
        "[third-repo] README.md is missing a '## Running' section",
    ]
    result = UpdateResult()
    for warn in rc_warnings:
        _append_warning(result, warn)
    assert len(result.warnings) == 3
    assert result.warnings == rc_warnings


def test_render_time_guard_no_newlines_in_warnings_or_errors():
    """The recurrence guard: at render time, no entry in either list may
    contain a newline. Seeded with a README-path fixture — the one producer
    proven to emit multi-line text unconditionally today — so this guard
    would fail if that producer's conversion (run.py:1804-1807) were ever
    reverted to a raw `.extend()`."""
    result = UpdateResult()
    _append_warning(
        result,
        f"[popoto] README.md is missing a '## Running' section\n{readme_check._EXAMPLE_BLOCK}",
    )
    _append_error(result, "Migration failed: ValidationError\nmultiple lines\nof detail")
    _append_warning(result, "a plain warning")

    for entry in result.warnings + result.errors:
        assert "\n" not in entry
