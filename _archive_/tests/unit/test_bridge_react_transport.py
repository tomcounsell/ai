"""react-transport-derivation (#2629), Task 4: the bridge's Telethon ``_react``
closure gets the same system-transport guard ``TelegramRelayOutputHandler.react``
uses, placed BEFORE the ``int(chat_id)`` conversion (``int("0")`` succeeds --
it is ``set_reaction`` with peer 0 that raises ``PeerIdInvalidError``).

The critique round on the plan found this closure currently unreachable from
the executor (the worker owns session execution; the bridge process never
calls ``_execute_agent_session``) and downgraded the ask from a
stub-Telethon-client integration suite to a signature/guard smoke test. This
module still exercises the REAL production source text -- not a
reimplementation -- by extracting the nested ``async def _react(...)`` body
verbatim from ``bridge/telegram_bridge.py`` via ``inspect.getsource`` and
``exec``-ing it in an isolated namespace with a stubbed ``set_reaction`` and
``_client``. A future edit that reorders the guard after ``int(chat_id)``, or
that regresses the transport-resolution try/except, fails these tests without
needing to import (and side-effect-run) the full bridge module.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import textwrap
from unittest.mock import AsyncMock, patch

import bridge.telegram_bridge as telegram_bridge_module


def _extract_react_closure():
    """Pull the nested ``_react`` function's source out of ``_make_react_cb``
    and exec it in an isolated namespace, returning the callable.

    Raises AssertionError (not a silent skip) if the source has drifted away
    from the expected shape -- a test that can't find its target must fail
    loudly, not pass vacuously.
    """
    src = inspect.getsource(telegram_bridge_module)
    match = re.search(
        r"( {12}async def _react\(.*?await set_reaction\([^\n]*\n)",
        src,
        re.S,
    )
    assert match, (
        "Could not locate the _react closure body in bridge/telegram_bridge.py "
        "-- source has drifted away from the shape this smoke test expects."
    )
    func_src = textwrap.dedent(match.group(1))

    # ``_client`` is a free variable captured from the enclosing
    # ``_make_react_cb(_client=client)`` closure, not a parameter of
    # ``_react`` itself -- bind a sentinel for it in the exec namespace.
    fake_client = object()
    namespace: dict = {"set_reaction": AsyncMock(), "_client": fake_client}
    exec(compile(func_src, "<_react_closure_under_test>", "exec"), namespace)  # noqa: S102
    assert "_react" in namespace, "extracted source did not define _react"
    return namespace["_react"], namespace["set_reaction"], fake_client


class _FakeSystemSession:
    """Shape of a reflection-scheduler session: placeholder chat, no transport."""

    session_id = "0_1234567890"
    chat_id = "0"
    project_key = "valor"
    extra_context: dict = {}


class TestBridgeReactClosureSignature:
    """Static/signature checks against the real source text."""

    def test_signature_accepts_session_default_none(self):
        """Back-compat: a stale 3-positional-arg caller (chat_id, msg_id,
        emoji) must still work -- session defaults to None."""
        sig = inspect.signature(
            _extract_react_closure()[0]  # the callable itself
        )
        params = list(sig.parameters.values())
        names = [p.name for p in params]
        assert names == ["chat_id", "msg_id", "emoji", "session"]
        assert params[names.index("emoji")].default is None
        assert params[names.index("session")].default is None

    def test_source_guard_precedes_int_conversion(self):
        """The ``== "system"`` return must appear on an earlier line than
        ``int(chat_id)`` -- a textual pin matching the Verification table's
        manual-check row, kept in code so it can't silently bit-rot.

        Scoped to the ``_react`` closure body itself (not the whole module)
        -- other unrelated ``int(chat_id)``/``"system"`` occurrences appear
        elsewhere in bridge/telegram_bridge.py."""
        src = inspect.getsource(telegram_bridge_module)
        match = re.search(
            r"( {12}async def _react\(.*?await set_reaction\([^\n]*\n)",
            src,
            re.S,
        )
        assert match, "Could not locate the _react closure body for the ordering check."
        func_src = match.group(1)
        guard_pos = func_src.index('if transport == "system":')
        int_conv_pos = func_src.index("int(chat_id)")
        assert guard_pos < int_conv_pos, (
            "the system-transport guard must be placed before int(chat_id) -- "
            "int('0') succeeds, so a reordered guard would let a chatless "
            "session reach set_reaction(client, 0, ...) and raise "
            "PeerIdInvalidError"
        )


class TestBridgeReactClosureBehavior:
    """Behavioral smoke test: exec the real extracted source and drive it."""

    def test_set_reaction_not_awaited_for_system_transport(self):
        react_fn, set_reaction_mock, fake_client = _extract_react_closure()

        with patch(
            "agent.output_handler.TelegramRelayOutputHandler._resolve_transport",
            return_value="system",
        ):
            # A non-int-parseable chat_id proves the guard runs BEFORE
            # int(chat_id) -- if the ordering ever regressed, int() would
            # raise ValueError here instead of a clean early return.
            asyncio.run(react_fn("not-an-int", 42, "\U0001f44d", _FakeSystemSession()))

        set_reaction_mock.assert_not_awaited()

    def test_set_reaction_awaited_for_real_peer(self):
        react_fn, set_reaction_mock, fake_client = _extract_react_closure()

        with patch(
            "agent.output_handler.TelegramRelayOutputHandler._resolve_transport",
            return_value="telegram",
        ):
            asyncio.run(react_fn("12345", 42, "\U0001f44d", None))

        set_reaction_mock.assert_awaited_once()
        call_args = set_reaction_mock.call_args[0]
        assert call_args[0] is fake_client
        assert call_args[1] == 12345  # int(chat_id)
        assert call_args[2] == 42
        assert call_args[3] == "\U0001f44d"

    def test_resolve_transport_raising_falls_back_to_telegram(self):
        """Risk 3, bridge leg: resolution must never raise here either."""
        react_fn, set_reaction_mock, fake_client = _extract_react_closure()

        with patch(
            "agent.output_handler.TelegramRelayOutputHandler._resolve_transport",
            side_effect=AttributeError("descriptor-polluted extra_context"),
        ):
            # Must not raise.
            asyncio.run(react_fn("12345", 42, "\U0001f44d", None))

        set_reaction_mock.assert_awaited_once()

    def test_session_none_back_compat_still_reaches_set_reaction(self):
        """A stale 3-arg-shaped caller (session omitted) still resolves via
        _resolve_transport(None, ...) -> "telegram" and reaches set_reaction."""
        react_fn, set_reaction_mock, fake_client = _extract_react_closure()

        asyncio.run(react_fn("12345", 42, "\U0001f44d"))

        set_reaction_mock.assert_awaited_once()
