"""Contract test: live handler must dispatch dedup through bridge/dispatch.py.

The Telegram live handler (``bridge/telegram_bridge.py::handler``) has several
early-return branches that must each record dedup so the reconciler's next
3-minute scan skips the message. Historically this was a distributed
per-call-site rule: every new branch had to remember to call
``record_message_processed``. A missed call produced a duplicate agent
session (see issue #948).

This test enforces the contract at the AST level:

1. The top-level ``handler`` function (decorated with ``@client.on(...)``)
   contains zero direct calls to ``enqueue_agent_session`` or
   ``record_message_processed``.
2. ``bridge/dispatch.py::dispatch_telegram_session`` calls
   ``enqueue_agent_session`` BEFORE ``record_message_processed`` so a
   failed enqueue never poisons the dedup record.
3. The AST walker itself detects a violation when given a synthetic
   source containing a bare ``enqueue_agent_session`` call inside a
   ``@client.on``-decorated handler (no manual inject/revert dance).

Scope notes:
- The walker enters the ``handler`` body but does NOT descend into nested
  functions, lambdas, or comprehensions (C1). Non-handler code in
  ``telegram_bridge.py`` (catchup/reconcile wrappers, etc.) is not
  constrained.
- ``handler`` is resolved deterministically by walking the module body and
  matching the ``AsyncFunctionDef`` whose name is ``handler`` AND whose
  decorator list contains a ``<x>.on(...)`` call (Telethon event
  registration) (C2). A rename will fail loudly.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SRC = REPO_ROOT / "bridge" / "telegram_bridge.py"
DISPATCH_SRC = REPO_ROOT / "bridge" / "dispatch.py"

BANNED_IN_HANDLER = frozenset({"enqueue_agent_session", "record_message_processed"})


def _find_telethon_handler(tree: ast.Module) -> ast.AsyncFunctionDef | None:
    """Find the Telethon ``handler`` async function deterministically.

    Walks the full tree (handler is defined inside ``run_bridge``, not at
    module top level), but pins to ``AsyncFunctionDef`` whose name is
    ``handler`` AND whose decorator list contains a ``<name>.on(...)``
    call. Returns None if not found.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name != "handler":
            continue
        for dec in node.decorator_list:
            # Match @<name>.on(...) or @<name>.<attr>.on(...)
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if dec.func.attr == "on":
                    return node
    return None


def _direct_calls(fn: ast.AsyncFunctionDef | ast.FunctionDef):
    """Yield every ``ast.Call`` node in ``fn``'s body WITHOUT descending into
    nested ``FunctionDef``/``AsyncFunctionDef``/``Lambda`` nodes.

    This is the scope-aware walker required by C1 in the plan. Without
    it, a nested helper's ``enqueue_agent_session`` call would be
    attributed to ``handler`` itself, producing a false positive that
    incentivizes contributors to work around the rule.
    """

    def _walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                yield child
            yield from _walk(child)

    yield from _walk(fn)


def _banned_calls_in(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> list[tuple[int, str]]:
    """Return [(lineno, name), ...] for banned direct calls in ``fn``."""
    hits: list[tuple[int, str]] = []
    for call in _direct_calls(fn):
        func = call.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name and name in BANNED_IN_HANDLER:
            hits.append((call.lineno, name))
    return hits


class TestBridgeDispatchContract:
    def test_handler_contains_no_direct_banned_calls(self):
        """The live handler must route dedup through bridge/dispatch.py."""
        tree = ast.parse(BRIDGE_SRC.read_text())
        handler = _find_telethon_handler(tree)
        assert handler is not None, (
            "Could not find a Telethon handler in bridge/telegram_bridge.py "
            "(AsyncFunctionDef named 'handler' with @<client>.on(...) decorator). "
            "If the handler was renamed, update this contract test."
        )
        hits = _banned_calls_in(handler)
        assert not hits, (
            f"handler contains direct calls to "
            f"{sorted(BANNED_IN_HANDLER)} — these MUST go through "
            f"bridge.dispatch.dispatch_telegram_session or "
            f"bridge.dispatch.record_telegram_message_handled. Offending "
            f"call sites (lineno, name): {hits}"
        )

    def test_dispatch_calls_enqueue_before_record(self):
        """dispatch_telegram_session must enqueue THEN record dedup.

        Reversing the order would let a failed enqueue leave a dedup
        record behind, causing the reconciler to skip a message that was
        never enqueued (Risk 3 from the plan).
        """
        tree = ast.parse(DISPATCH_SRC.read_text())
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch_telegram_session":
                fn = node
                break
        assert fn is not None, "dispatch_telegram_session not found in bridge/dispatch.py"

        enqueue_line: int | None = None
        record_line: int | None = None
        for call in _direct_calls(fn):
            f = call.func
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute):
                name = f.attr
            else:
                name = None
            if name == "enqueue_agent_session" and enqueue_line is None:
                enqueue_line = call.lineno
            elif name == "record_message_processed" and record_line is None:
                record_line = call.lineno
        assert enqueue_line is not None, "dispatch_telegram_session must call enqueue_agent_session"
        assert record_line is not None, (
            "dispatch_telegram_session must call record_message_processed"
        )
        assert enqueue_line < record_line, (
            f"dispatch_telegram_session must call enqueue_agent_session "
            f"BEFORE record_message_processed (got enqueue at line "
            f"{enqueue_line}, record at line {record_line})"
        )

    def test_contract_detects_violation_in_synthetic_source(self):
        """The AST walker must flag a bare enqueue in a synthetic handler.

        This replaces the fragile manual "inject a bare call, confirm, revert"
        step from Task 4. If the walker regressed and stopped detecting
        violations, this test would pass trivially — which is why we also
        assert a NO-violation baseline on a clean synthetic source.
        """
        violating_source = """
from agent.agent_session_queue import enqueue_agent_session

class client:
    @staticmethod
    def on(_):
        def deco(fn):
            return fn
        return deco

@client.on(None)
async def handler(event):
    await enqueue_agent_session(project_key='x', session_id='y')
"""
        tree = ast.parse(violating_source)
        handler = _find_telethon_handler(tree)
        assert handler is not None, "synthetic handler not located by walker"
        hits = _banned_calls_in(handler)
        assert hits, "walker failed to flag bare enqueue_agent_session in synthetic handler"
        assert any(name == "enqueue_agent_session" for _, name in hits)

        clean_source = """
class client:
    @staticmethod
    def on(_):
        def deco(fn):
            return fn
        return deco

@client.on(None)
async def handler(event):
    # routes through the wrapper; no banned direct call
    from bridge.dispatch import dispatch_telegram_session
    await dispatch_telegram_session(
        project_key='x',
        session_id='y',
        telegram_message_id=1,
        chat_id='c',
        working_dir='',
        message_text='',
        sender_name='',
    )
"""
        tree = ast.parse(clean_source)
        handler = _find_telethon_handler(tree)
        assert handler is not None
        assert _banned_calls_in(handler) == []

    def test_walker_does_not_descend_into_nested_functions(self):
        """C1: a banned call inside a nested helper must NOT trip the contract.

        This prevents the walker from forcing contributors to avoid
        legitimate nested helpers just to satisfy the contract.
        """
        source = """
class client:
    @staticmethod
    def on(_):
        def deco(fn):
            return fn
        return deco

@client.on(None)
async def handler(event):
    def nested_helper():
        # This banned call is in a nested function; the contract MUST NOT
        # flag it because nested functions have their own scope.
        enqueue_agent_session()
    pass
"""
        tree = ast.parse(source)
        handler = _find_telethon_handler(tree)
        assert handler is not None
        hits = _banned_calls_in(handler)
        assert hits == [], f"walker must not descend into nested FunctionDef; spurious hits: {hits}"


class TestSteeringPathsRecordDedup:
    """Every push_steering_message call in handler must be followed by
    record_telegram_message_handled (or dispatch_telegram_session) before
    the branch's return statement.

    This prevents the reconciler from re-dispatching steered messages as
    duplicates (issue #963, Bug 1).
    """

    @staticmethod
    def _find_steering_dedup_violations(
        fn: ast.AsyncFunctionDef,
    ) -> list[tuple[int, str]]:
        """Walk ``fn``'s body and find push_steering_message calls that are
        NOT followed by record_telegram_message_handled or
        dispatch_telegram_session before a return in the same branch.

        Returns [(lineno, description), ...] for each violation.
        """
        violations: list[tuple[int, str]] = []

        def _get_call_name(node: ast.Call) -> str | None:
            f = node.func
            if isinstance(f, ast.Name):
                return f.id
            if isinstance(f, ast.Attribute):
                return f.attr
            return None

        dedup_calls = frozenset({"record_telegram_message_handled", "dispatch_telegram_session"})

        def _check_stmts(stmts: list[ast.stmt], pending_push_line: int | None = None):
            """Walk a list of statements, tracking pending push_steering_message calls.

            pending_push_line: line number of an unresolved push from an ancestor scope.
            """
            local_pending = pending_push_line

            for stmt in stmts:
                # Check all calls in this statement (but not nested functions)
                for node in ast.walk(stmt):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    if isinstance(node, ast.Call):
                        name = _get_call_name(node)
                        if name == "push_steering_message":
                            local_pending = node.lineno
                        elif name in dedup_calls:
                            local_pending = None

                # If this statement is an If, recurse into branches
                if isinstance(stmt, ast.If):
                    _check_stmts(stmt.body, local_pending)
                    _check_stmts(stmt.orelse, local_pending)
                    # After an if/else, the pending state is complex; reset
                    # only if both branches clear it. For simplicity, keep
                    # the current pending state (conservative: may produce
                    # false positives but never false negatives).
                elif isinstance(stmt, (ast.For, ast.While)):
                    _check_stmts(stmt.body, local_pending)
                    _check_stmts(stmt.orelse, local_pending)
                elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                    _check_stmts(stmt.body, local_pending)
                elif isinstance(stmt, (ast.Try, ast.ExceptHandler)):
                    if isinstance(stmt, ast.Try):
                        _check_stmts(stmt.body, local_pending)
                        for handler_node in stmt.handlers:
                            _check_stmts(handler_node.body, local_pending)
                        _check_stmts(stmt.orelse, local_pending)
                        _check_stmts(stmt.finalbody, local_pending)

                # Check for return with pending push
                if isinstance(stmt, ast.Return) and local_pending is not None:
                    violations.append(
                        (
                            local_pending,
                            f"push_steering_message at line {local_pending} "
                            f"not followed by dedup before return at line {stmt.lineno}",
                        )
                    )

        # Filter out nested functions, then check the full body as one scope
        top_stmts = [
            s for s in fn.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        _check_stmts(top_stmts)

        return violations

    def test_all_steering_paths_have_dedup_in_handler(self):
        """Every push_steering_message in handler must be followed by dedup."""
        tree = ast.parse(BRIDGE_SRC.read_text())
        handler = _find_telethon_handler(tree)
        assert handler is not None
        violations = self._find_steering_dedup_violations(handler)
        assert not violations, (
            "push_steering_message calls without subsequent "
            "record_telegram_message_handled before return:\n"
            + "\n".join(f"  - {desc}" for _, desc in violations)
        )

    def test_detects_violation_in_synthetic_source(self):
        """Walker must flag a push_steering_message without dedup before return."""
        violating_source = """
class client:
    @staticmethod
    def on(_):
        def deco(fn):
            return fn
        return deco

@client.on(None)
async def handler(event):
    push_steering_message("sid", "text", "sender")
    return
"""
        tree = ast.parse(violating_source)
        handler = _find_telethon_handler(tree)
        assert handler is not None
        violations = self._find_steering_dedup_violations(handler)
        assert violations, "walker failed to detect missing dedup after push_steering_message"

    def test_detects_nested_branch_violation(self):
        """Walker must detect violation inside nested if branches."""
        violating_source = """
class client:
    @staticmethod
    def on(_):
        def deco(fn):
            return fn
        return deco

@client.on(None)
async def handler(event):
    if cond1:
        push_steering_message("sid", "text", "sender")
        if cond2:
            return
"""
        tree = ast.parse(violating_source)
        handler = _find_telethon_handler(tree)
        assert handler is not None
        violations = self._find_steering_dedup_violations(handler)
        assert violations, (
            "walker failed to detect nested-branch violation: "
            "push_steering_message in outer if, return in nested if, no dedup"
        )

    def test_clean_source_has_no_violations(self):
        """A properly guarded push_steering_message must not trigger a violation."""
        clean_source = """
class client:
    @staticmethod
    def on(_):
        def deco(fn):
            return fn
        return deco

@client.on(None)
async def handler(event):
    push_steering_message("sid", "text", "sender")
    await record_telegram_message_handled(chat_id, msg_id)
    return
"""
        tree = ast.parse(clean_source)
        handler = _find_telethon_handler(tree)
        assert handler is not None
        violations = self._find_steering_dedup_violations(handler)
        assert not violations, f"false positive on clean source: {violations}"


class TestDedupWarningLogging:
    """C4: dedup failures must log at WARNING level (not debug)."""

    def test_record_logs_warning_on_redis_failure(self, caplog):
        import logging
        from unittest.mock import patch

        from bridge.dedup import record_message_processed

        async def _run():
            with patch(
                "models.dedup.DedupRecord.get_or_create",
                side_effect=RuntimeError("redis down"),
            ):
                with caplog.at_level(logging.WARNING, logger="bridge.dedup"):
                    await record_message_processed("chat_err", 42)

        import asyncio

        asyncio.run(_run())

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "dedup record failed" in r.getMessage()
        ]
        assert warnings, (
            "record_message_processed must log at WARNING (not debug) when "
            "the underlying save raises. Got records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )
        msg = warnings[0].getMessage()
        assert "chat_err" in msg and "42" in msg and "redis down" in msg
