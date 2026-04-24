"""Shared helpers for the merge-gate baseline system.

Both `scripts/refresh_test_baseline.py` and `scripts/baseline_gate.py` parse
pytest junitxml and operate on `data/main_test_baseline.json`.  The parsing
rules and schema constants live here so the two scripts can't drift.

See `docs/features/merge-gate-baseline.md` for the feature overview and
`docs/plans/merge-gate-baseline-refresh.md` for the design plan (issue #1084).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# Exact prefix emitted by pytest-timeout in `<failure message="...">`.
# We match this prefix and NOT a loose substring like "Timeout" because a
# regular assertion failure whose message contains the word "Timeout"
# (for example `assert response != "Timeout"`) must classify as `real`/`flaky`,
# not as `hung`.  See Data Flow section 4 Implementation Note in the plan.
PYTEST_TIMEOUT_PREFIX = "Failed: Timeout >"

# Four classification buckets used throughout the baseline system.
CATEGORY_REAL = "real"
CATEGORY_FLAKY = "flaky"
CATEGORY_HUNG = "hung"
CATEGORY_IMPORT_ERROR = "import_error"

VALID_CATEGORIES = frozenset(
    {CATEGORY_REAL, CATEGORY_FLAKY, CATEGORY_HUNG, CATEGORY_IMPORT_ERROR}
)

# Schema version for the categorised baseline file.
SCHEMA_VERSION = 2


class JunitxmlParseError(Exception):
    """Raised when junitxml is unreadable (malformed, truncated, missing)."""


def parse_junitxml(xml_path: str | Path) -> dict[str, str]:
    """Parse a pytest junitxml file into a `{node_id: outcome}` map.

    Returns a mapping where each value is one of:
    - ``"pass"`` — testcase has no ``<failure>`` or ``<error>`` children
    - ``"fail"`` — testcase has a ``<failure>`` child whose ``message`` does NOT
      start with ``PYTEST_TIMEOUT_PREFIX``
    - ``"timeout"`` — testcase has a ``<failure>`` child whose ``message``
      starts with ``PYTEST_TIMEOUT_PREFIX`` (exact prefix match)
    - ``"collection_error"`` — testcase has an ``<error>`` child

    Wraps ``xml.etree.ElementTree.parse`` in ``try/except ParseError`` ONLY
    (never bare ``except``).  Truncated or malformed junitxml produces
    :class:`JunitxmlParseError` with the original ``ParseError`` chained.

    Raises:
        JunitxmlParseError: if the file is missing, unreadable, or truncated.
    """
    try:
        tree = ET.parse(str(xml_path))
    except FileNotFoundError as exc:
        raise JunitxmlParseError(f"junitxml not found: {xml_path}") from exc
    except ET.ParseError as exc:
        raise JunitxmlParseError(f"junitxml parse error at {xml_path}: {exc}") from exc

    root = tree.getroot()
    outcomes: dict[str, str] = {}

    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        if not name:
            # A testcase missing `name` is structurally unusable -- hint the caller.
            raise JunitxmlParseError(
                f"junitxml at {xml_path} has a <testcase> with no 'name' attribute "
                f"(classname={classname!r})"
            )
        node_id = _build_node_id(classname, name)

        failure = testcase.find("failure")
        error = testcase.find("error")

        if error is not None:
            outcomes[node_id] = "collection_error"
        elif failure is not None:
            message = failure.get("message") or ""
            if message.startswith(PYTEST_TIMEOUT_PREFIX):
                outcomes[node_id] = "timeout"
            else:
                outcomes[node_id] = "fail"
        else:
            outcomes[node_id] = "pass"

    return outcomes


def _build_node_id(classname: str, name: str) -> str:
    """Reconstruct pytest's node ID form (`path::Class::test` or `path::test`).

    pytest junitxml encodes ``classname`` as dotted module path (e.g.
    ``tests.unit.test_foo.TestBar``), which we reshape back to
    ``tests/unit/test_foo.py::TestBar::test_quux``.  This matches how pytest
    emits node IDs on stdout and how the merge-gate/baseline store keys them.
    """
    if not classname:
        return name

    parts = classname.split(".")
    # Find the last segment that starts with ``test_`` or is a bare ``test``
    # file (pytest allows ``conftest.py``-style modules too, but test files
    # always start with ``test_``).  Everything up to and including that
    # segment is the file path.  Anything after is the class name.
    file_end = -1
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            file_end = i
            break

    if file_end == -1:
        # Fall back to treating the whole thing as dotted module -> path.
        file_path = "/".join(parts) + ".py"
        return f"{file_path}::{name}"

    file_parts = parts[: file_end + 1]
    class_parts = parts[file_end + 1 :]
    file_path = "/".join(file_parts) + ".py"
    if class_parts:
        return f"{file_path}::{'::'.join(class_parts)}::{name}"
    return f"{file_path}::{name}"


def failing_node_ids(outcomes: dict[str, str]) -> set[str]:
    """Extract node IDs from a ``{node_id: outcome}`` map whose outcome is not pass."""
    return {node_id for node_id, outcome in outcomes.items() if outcome != "pass"}
