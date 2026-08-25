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
    """Default-mock the promise gate so tests in this package do not call the LLM.

    Hoisting this from the former monolith to a package conftest **widened its
    scope**, contrary to what PR #3005's description claimed. The three files
    absorbed into this package (``_await``, ``_chat_log``, ``_voice_flag``) never
    had it before. The widening was missed because those files contain no literal
    ``promise_gate`` reference to grep for -- the path is indirect:
    ``_chat_log`` (5 tests) and ``_voice_flag`` (3 tests) call ``cmd_send``, and
    ``tools/valor_telegram.py`` calls ``cli_check_or_exit`` unconditionally.
    Those files mock only ``resolve_chat`` and ``_get_redis_connection``, so
    before the move all 8 ran the real gate. ``_await`` is unaffected: it targets
    ``tools.valor_telegram_await`` and never reaches ``cmd_send``.

    Kept deliberately rather than scoped back. Those 8 tests assert relay-payload
    shape, chat-log recording, and voice-flag/cleanup behavior -- never gate
    behavior -- so running the real gate was incidental cost, not coverage.
    ``cli_check_or_exit`` itself is covered directly by
    ``tests/unit/test_promise_gate.py``; the CLI's contract with it here is the
    ``--help`` anti-leak assertion in
    ``test_valor_telegram_rtr.py::TestValorTelegramPromiseGate``, which needs no
    real gate call. If a test in this package ever does need the live gate, it
    must opt out explicitly -- nothing in the package overrides this fixture today.
    """
    monkeypatch.setattr(
        "bridge.promise_gate.cli_check_or_exit",
        lambda text, transport, session_id: None,
    )
