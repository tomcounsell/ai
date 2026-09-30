"""Table test for the canonical `_truthy()` Popoto string-boolean coercion
helper (#2439), plus consolidation assertions.

Popoto's untyped `Field(default=False)` round-trips through Redis as the
*string* `'False'`/`'True'` (not a real bool). A naive `bool(value)` read
treats both non-empty strings as truthy, which is wrong. `_truthy()`
(canonical home: `agent.session_pickup`) is the single coercion helper every
read site should use.

This module also asserts the consolidation requirement from the #2439 fix:
`models.crash_signature` must import the canonical helper rather than carry
a drifted inline duplicate.
"""

from __future__ import annotations

import inspect

import pytest

from agent.session_pickup import _truthy


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("  ", False),
        ("False", False),
        ("false", False),
        ("0", False),
        ("no", False),
        ("True", True),
        ("true", True),
        ("1", True),
        ("yes", True),
        (True, True),
        (False, False),
        (0, False),
        (1, True),
    ],
)
def test_truthy_table(value, expected):
    assert _truthy(value) is expected


def test_crash_signature_imports_canonical_truthy_not_a_local_duplicate():
    """Regression guard (#2439): `models.crash_signature._truthy` must be the
    exact same function object as the canonical `agent.session_pickup._truthy`
    — i.e. an import, not a drifted inline copy.
    """
    import models.crash_signature as crash_signature_module

    assert crash_signature_module._truthy is _truthy


def test_crash_signature_has_no_inline_truthy_definition():
    """Belt-and-suspenders: the source of `models/crash_signature.py` must not
    define its own `def _truthy` — it must import the canonical one.
    """
    import models.crash_signature as crash_signature_module

    source = inspect.getsource(crash_signature_module)
    assert "def _truthy" not in source
