"""Shared fixtures for the valor_telegram test package (split of test_valor_telegram.py, #2879).

Package files live one level deeper than the former monolith
(``tests/unit/test_valor_telegram.py``), so the ``sys.path`` insert needs one
more ``.parent`` to still resolve to the repo root.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


@pytest.fixture(autouse=True)
def _bypass_promise_gate(monkeypatch):
    """Default-mock the promise gate so existing tests do not call the LLM."""
    monkeypatch.setattr(
        "bridge.promise_gate.cli_check_or_exit",
        lambda text, transport, session_id: None,
    )
