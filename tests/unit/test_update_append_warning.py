"""Unit tests for scripts/update/run.py's `_append_warning`/`_append_error`
newline-collapse helpers (#2845).

run.py's summary render block emits one `⚠️`/`-` bullet per list entry — a raw
multi-line entry (an exception `str()`, a wrapped multi-line diagnostic)
renders its sentinel on only the first physical line, dropping the rest.
These helpers are the recurrence guard for that whole class: every one of
the 85 warning/error-injection sites in `run.py` routes through one of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.update import readme_check
from scripts.update import run as run_module
from scripts.update.run import UpdateResult, _append_error, _append_warning

pytestmark = pytest.mark.unit


def _readme_block_source() -> ast.stmt:
    """The README-check `if rc.ok: ... else: for warn in rc.warnings: ...` block.

    Located by its `rc.ok` test rather than by line number, so the tests that
    execute it survive edits above it in `run_update`.
    """
    source = Path(run_module.__file__).read_text()
    tree = ast.parse(source)
    run_update = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_update"),
        None,
    )
    assert run_update is not None, "run_update not found as a top-level sync def in run.py"
    for node in ast.walk(run_update):
        if not isinstance(node, ast.If):
            continue
        if (ast.get_source_segment(source, node.test) or "") != "rc.ok":
            continue
        return node
    raise AssertionError("README-check block not found in run_update")


def _injection_call_count() -> int:
    """Count `result.<warnings|errors>.<append|extend>(...)` CALLS in run.py.

    Counted as AST call nodes rather than by regex: a regex over source also
    matches the pattern inside comments and docstrings, which is exactly the
    self-defeating-gate shape this PR had to repair on the Redis ACL row.
    """
    tree = ast.parse(Path(run_module.__file__).read_text())
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("append", "extend"):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Attribute) or owner.attr not in ("warnings", "errors"):
            continue
        if isinstance(owner.value, ast.Name) and owner.value.id == "result":
            total += 1
    return total


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
    """The dedup fix for the README step's N² bug, driven through run.py's
    OWN loop source rather than a copy of it.

    `result.warnings.extend(rc.warnings)` sat INSIDE the
    `for warn in rc.warnings:` loop, so N warnings produced N² entries. A
    test that re-types the fixed loop in its own body cannot observe that:
    it appends three items one at a time and asserts three came out, which
    holds under both the bug and the fix. So lift the real block out of
    `run_update` by AST and execute THAT, with `rc` stubbed — reverting the
    block to a raw `.extend()` turns this red at 9 entries.
    """
    block = _readme_block_source()
    rc_warnings = [
        "[popoto] README.md is missing a '## Running' section",
        "[other-repo] README.md is missing a '## Running' section",
        "[third-repo] README.md is missing a '## Running' section",
    ]

    class _Rc:
        ok = False
        checked = 3
        warnings = list(rc_warnings)

    result = UpdateResult()
    namespace = dict(vars(run_module))
    namespace.update(
        {
            "result": result,
            "rc": _Rc(),
            "v": False,
            "log": lambda *a, **k: None,
        }
    )
    exec(compile(ast.Module(body=[block], type_ignores=[]), "<readme>", "exec"), namespace)

    assert len(result.warnings) == 3, (
        f"expected 3 entries, got {len(result.warnings)} — the N² duplication is back"
    )
    assert result.warnings == rc_warnings


def test_every_warning_injection_site_routes_through_the_helpers():
    """Source-level completeness gate for the 85-site conversion.

    The behavioural tests above each exercise one producer. This one asserts
    the sweep itself: after the conversion, the only surviving
    `result.warnings.append(` / `.errors.append(` / `.extend(` calls in
    `run.py` are the single call inside `_append_warning`'s own body and the
    single call inside `_append_error`'s. A new injection site added raw --
    or the `readme_check` `extend` restored -- pushes the count above 2 and
    fails here rather than silently truncating a multi-line warning in
    production.
    """
    count = _injection_call_count()
    assert count == 2, (
        f"expected exactly 2 raw injection calls (one per helper body), found {count} — "
        "a site bypasses _append_warning/_append_error"
    )


def test_render_time_guard_no_newlines_in_warnings_or_errors():
    """At render time, no entry in either list may contain a newline.

    Seeded with a README-path fixture — the one producer proven to emit
    multi-line text unconditionally today. This asserts the helpers' collapse
    contract only; the guard that the README producer still *routes* through
    them is `test_readme_warnings_are_not_duplicated` above, which executes
    run.py's own block.
    """
    result = UpdateResult()
    _append_warning(
        result,
        f"[popoto] README.md is missing a '## Running' section\n{readme_check._EXAMPLE_BLOCK}",
    )
    _append_error(result, "Migration failed: ValidationError\nmultiple lines\nof detail")
    _append_warning(result, "a plain warning")

    for entry in result.warnings + result.errors:
        assert "\n" not in entry
