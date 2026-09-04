"""Cross-medium static guards for agent-facing bridge context (#2732).

Three AST scans, each written as a pure function over source text so the
red-state fixtures below run without touching the filesystem (Risk 5 in
docs/plans/reply-chain-media-renders-as-literal-string.md):

1. **Placeholder shape** — flags the ``X or "[media]"`` fallback: a
   ``BoolOp(op=Or)`` whose right-hand operand is a bracketed string constant.
   This is the exact shape of the defect #2732 fixes and of PR #953's original
   line. Applied across every module in ``bridge/``.
2. **Write-only agent context** — every key written onto ``extra_context``
   (subscript assignment, dict-literal call argument, or dict literal bound to
   an extra-context/overrides name) in ``bridge/`` and ``agent/`` must have at
   least one reader outside ``tests/``. This is the shape spike-4 found on the
   email side: context stamped for the agent and never delivered. The
   allow-list below is derived empirically and ratchets shut — a new reader
   for an allow-listed key fails the stale check and forces the entry's
   removal, so the list can only shrink.
3. **Off-loop enforcement** — every ``TelegramMessage.query.filter`` call in
   ``bridge/context.py``'s async functions (``fetch_reply_chain`` and the
   ``_resolve_media_descriptor`` helper it awaits) must run under
   ``asyncio.to_thread``, with a ratcheted exemption for the pre-existing
   ``_cache_walk_root`` defect the plan leaves out of scope. Popoto is
   synchronous redis-py; an inline call blocks the bridge event loop and
   makes the 3-second ``asyncio.wait_for`` guard unenforceable (Risk 4).

Test names are selectable with ``-k placeholder``, ``-k write_only_context``,
and ``-k off_loop`` respectively, matching the plan's Verification rows.
"""

from __future__ import annotations

import ast
import re
import subprocess
import textwrap
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = REPO_ROOT / "bridge"


# ---------------------------------------------------------------------------
# Scan 1: bare bracketed-placeholder fallbacks (`X or "[media]"`)
# ---------------------------------------------------------------------------

# The named placeholders from the plan ([media], [image], [attachment],
# [document], [file]) are all instances of this general shape.
_PLACEHOLDER_RE = re.compile(r"^\[[a-z_ ]+\]$")


class PlaceholderFinding(NamedTuple):
    lineno: int
    placeholder: str


def find_placeholder_fallbacks(source: str) -> list[PlaceholderFinding]:
    """Flag every ``or``-fallback to a bracketed placeholder string.

    Matches any ``BoolOp(op=Or)`` whose right-hand operand is a string
    ``Constant`` of the ``^\\[[a-z_ ]+\\]$`` shape. Log lines like
    ``logger.info(f"[media] ...")`` are ``JoinedStr`` arguments to a call,
    not ``BoolOp`` operands, so they are structurally invisible to this scan
    — no allow-list needed.
    """
    findings: list[PlaceholderFinding] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for operand in node.values[1:]:
                if (
                    isinstance(operand, ast.Constant)
                    and isinstance(operand.value, str)
                    and _PLACEHOLDER_RE.match(operand.value)
                ):
                    findings.append(PlaceholderFinding(operand.lineno, operand.value))
    return findings


def test_placeholder_fixture_flags_bare_fallback():
    """Red state, proven in-suite: the exact defect shape yields one finding."""
    findings = find_placeholder_fallbacks('content = msg.text or "[media]"')
    assert len(findings) == 1
    assert findings[0].placeholder == "[media]"


def test_placeholder_fixture_ignores_logger_fstring():
    """A bracketed tag inside a log f-string is not a fallback and stays clean."""
    findings = find_placeholder_fallbacks('logger.info(f"[media] download failed {e}")')
    assert findings == []


def test_placeholder_fixture_covers_named_placeholders():
    """Every named placeholder from the plan matches the general shape."""
    for name in ("[media]", "[image]", "[attachment]", "[document]", "[file]"):
        findings = find_placeholder_fallbacks(f'x = y or "{name}"')
        assert len(findings) == 1, f"{name} must be flagged"
    # A bracketed string in *left* position is not a fallback.
    assert find_placeholder_fallbacks('x = "[media]" in y or z') == []


def test_no_placeholder_fallbacks_across_bridge():
    """No module in bridge/ falls back to a bare bracketed placeholder."""
    findings: dict[str, list[PlaceholderFinding]] = {}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        found = find_placeholder_fallbacks(path.read_text())
        if found:
            findings[path.name] = found
    assert not findings, (
        "Bare placeholder fallback(s) in bridge/ — a constant string standing in "
        "for content the agent cannot read. Carry the real reference (path, "
        f"filename, or explicit unreadable marker) instead: {findings}"
    )


# ---------------------------------------------------------------------------
# Scan 2: write-only extra_context keys (written for the agent, never read)
# ---------------------------------------------------------------------------

# Matches the names context payloads travel under: extra_context,
# extra_context_overrides, extra_overrides, _completed_extra_overrides.
_EXTRA_CONTEXT_NAME_RE = re.compile(r"extra_(context|overrides)")


class ContextWrite(NamedTuple):
    key: str
    lineno: int


def _bound_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dict_literal_keys(value: ast.expr) -> list[ContextWrite]:
    if not isinstance(value, ast.Dict):
        return []
    return [
        ContextWrite(k.value, k.lineno)
        for k in value.keys
        # `**unpack` entries carry a None key; only literal string keys count.
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]


def collect_extra_context_writes(source: str) -> list[ContextWrite]:
    """Collect every key written onto an extra-context payload.

    Three forms (a subscript-only collector under-detects):
    - ``extra_context["key"] = ...`` (any receiver named like extra context)
    - ``fn(extra_context={...})`` / ``fn(extra_context_overrides={...})``
    - ``extra_overrides = {**(extra_overrides or {}), "key": ...}`` — a dict
      literal bound to an extra-context/overrides name, the shape both
      ``bridge/telegram_bridge.py`` hydration branches use.
    """
    writes: list[ContextWrite] = []
    for node in ast.walk(ast.parse(source)):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets, value = [node.target], node.value
        for target in targets:
            if isinstance(target, ast.Subscript):
                receiver = _bound_name(target.value)
                if (
                    receiver
                    and _EXTRA_CONTEXT_NAME_RE.search(receiver)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    writes.append(ContextWrite(target.slice.value, target.lineno))
            elif isinstance(target, ast.Name) and _EXTRA_CONTEXT_NAME_RE.search(target.id):
                if value is not None:
                    writes.extend(_dict_literal_keys(value))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and _EXTRA_CONTEXT_NAME_RE.search(kw.arg):
                    writes.extend(_dict_literal_keys(kw.value))
    return writes


def collect_read_keys(source: str) -> set[str]:
    """Collect every string key read anywhere in the source.

    Receiver-agnostic but key-position-scoped: real readers use renamed
    locals (``extra.get("customer_id")``, ``_inj_ctx.get(...)``), so a read is
    - any Load-context ``Subscript`` whose key is a string constant, on ANY
      receiver;
    - any ``.get()`` call whose first argument is a string constant, on ANY
      receiver;
    - string-constant membership in a tuple/list being iterated (the
      ``models/agent_session.py`` ``for key in ("revival_context", ...)``
      shape).

    Writer statements contribute nothing: a Store-context subscript target and
    a dict-literal key are not read positions.
    """
    reads: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                reads.add(node.slice.value)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    reads.add(first.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if isinstance(node.iter, (ast.Tuple, ast.List)):
                for elt in node.iter.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        reads.add(elt.value)
    return reads


def _tracked_python_files() -> list[Path]:
    """Every tracked .py file present in the working tree."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    paths = [REPO_ROOT / rel for rel in result.stdout.split("\0") if rel]
    return [p for p in paths if p.exists()]


def _tree_write_only_keys() -> tuple[dict[str, list[str]], set[str]]:
    """(writes-by-key with locations, keys read anywhere outside tests/)."""
    files = _tracked_python_files()
    writes: dict[str, list[str]] = {}
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] not in ("bridge", "agent"):
            continue
        for write in collect_extra_context_writes(path.read_text()):
            writes.setdefault(write.key, []).append(f"{rel}:{write.lineno}")
    read_keys: set[str] = set()
    for path in files:
        if path.relative_to(REPO_ROOT).parts[0] == "tests":
            continue
        read_keys |= collect_read_keys(path.read_text())
    return writes, read_keys


# Derived empirically against the tree; every entry is asserted STILL unread
# below, so this list can only shrink — landing a reader forces the entry's
# removal. Email-seam entries are #3136's delivery gap: stamped onto the email
# session's extra_context in bridge/email_bridge.py and consumed by nothing.
# (attachments_truncated is NOT here: it is read via parsed.get(...) at
# bridge/email_bridge.py, so under the receiver-agnostic predicate it has
# readers.) The two session-snapshot keys are save_session_snapshot metadata
# serialized wholesale for humans; no code reads them in key position.
WRITE_ONLY_ALLOW_LIST = {
    "attachments_unrecoverable",  # known gap: #3136
    "attachments_recovered_count",  # known gap: #3136
    "attachments_referenced",  # known gap: #3136
    "email_attachments",  # known gap: #3136
    "email_from",  # known gap: #3136 (email transport metadata, same seam)
    "correlation_id",  # session-snapshot metadata, serialized wholesale
    "message_preview",  # session-snapshot metadata, serialized wholesale
}


def test_write_only_context_collector_sees_all_three_write_forms():
    source = textwrap.dedent(
        """
        extra_context["alpha"] = 1
        session.extra_context["beta"] = 2
        extra_overrides = {**(extra_overrides or {}), "gamma": True}
        extra_context: dict = {"delta": 4}
        enqueue(extra_context={"epsilon": 5}, extra_context_overrides={"zeta": 6})
        """
    )
    keys = {w.key for w in collect_extra_context_writes(source)}
    assert keys == {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"}


def test_write_only_context_collector_ignores_unrelated_dicts():
    source = 'payload["alpha"] = 1\nconfig = {"beta": 2}\nfn(options={"gamma": 3})'
    assert collect_extra_context_writes(source) == []


def test_write_only_context_reader_predicate_is_receiver_agnostic():
    source = textwrap.dedent(
        """
        value = extra.get("customer_id")
        banner = _inj_ctx["injection_risk_banner"]
        for key in ("revival_context", "classification_type"):
            consume(key)
        """
    )
    assert collect_read_keys(source) == {
        "customer_id",
        "injection_risk_banner",
        "revival_context",
        "classification_type",
    }


def test_write_only_context_writer_statement_is_not_a_read():
    """A write contributes no read — but a genuine read on its RHS does.

    The second fixture is the bridge/email_bridge.py shape the critique
    ruled on: ``extra_context["attachments_truncated"] =
    bool(parsed.get("attachments_truncated"))`` reads the key off a *parsed*
    payload, so under the receiver-agnostic predicate that key is read and is
    not a gap.
    """
    assert collect_read_keys('extra_context["omega"] = compute()') == set()
    both = 'extra_context["attachments_truncated"] = bool(parsed.get("attachments_truncated"))'
    assert collect_read_keys(both) == {"attachments_truncated"}
    assert {w.key for w in collect_extra_context_writes(both)} == {"attachments_truncated"}


def test_write_only_context_allow_list_matches_tree():
    """Every extra_context write has a reader, except the ratcheted allow-list.

    Fails in two directions:
    - a NEW write-only key appeared → deliver it to a consumer or (for a
      deliberate, reviewed gap) add it here;
    - an allow-listed key gained a reader (or lost its writer) → remove the
      entry. The allow-list only shrinks.
    """
    writes, read_keys = _tree_write_only_keys()

    # Non-vacuity pins: the collector must keep seeing the known real sites.
    # bridge/email_bridge.py subscript writes:
    assert "attachments_unrecoverable" in writes, (
        "collector no longer sees bridge/email_bridge.py's subscript writes — "
        "the scan went blind, fix the collector before trusting a green run"
    )
    # bridge/telegram_bridge.py dict-literal-bound-to-name writes:
    assert "reply_chain_hydrated" in writes, (
        "collector no longer sees bridge/telegram_bridge.py's override-dict "
        "writes — the scan went blind, fix the collector before trusting a "
        "green run"
    )

    unread = {key for key in writes if key not in read_keys}

    new_gaps = unread - WRITE_ONLY_ALLOW_LIST
    assert not new_gaps, (
        "extra_context key(s) written for the agent but read by nothing "
        "outside tests/ — context that never arrives is the email-seam "
        "failure mode (#3136). Wire a reader or add a reviewed allow-list "
        "entry: " + ", ".join(f"{k} (written at {writes[k]})" for k in sorted(new_gaps))
    )

    stale = WRITE_ONLY_ALLOW_LIST - unread
    assert not stale, (
        "allow-listed extra_context key(s) now have a reader (or the write "
        f"was removed) — delete the entries, the list only shrinks: {sorted(stale)}"
    )


# ---------------------------------------------------------------------------
# Scan 3: fetch_reply_chain Redis lookups must run off-loop
# ---------------------------------------------------------------------------


def _is_telegram_filter_call(node: ast.AST) -> bool:
    """``TelegramMessage.query.filter(...)`` call shape."""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "filter"
        and isinstance(f.value, ast.Attribute)
        and f.value.attr == "query"
        and isinstance(f.value.value, ast.Name)
        and f.value.value.id == "TelegramMessage"
    )


def _has_to_thread_ancestor(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Call):
            f = current.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr == "to_thread"
                and isinstance(f.value, ast.Name)
                and f.value.id == "asyncio"
            ):
                return True
        current = parents.get(current)
    return False


def find_inline_filter_calls(source: str, func_name: str = "fetch_reply_chain") -> list[int]:
    """Line numbers of ``TelegramMessage.query.filter`` calls inside the async
    function ``func_name`` that have no ``asyncio.to_thread`` ancestor call.

    Raises ``ValueError`` when no ``async def`` of that name exists, so the
    tree test cannot pass vacuously by the function being renamed away.
    """
    func: ast.AsyncFunctionDef | None = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            func = node
            break
    if func is None:
        raise ValueError(f"no async def {func_name} found")

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(func):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    return [
        node.lineno
        for node in ast.walk(func)
        if _is_telegram_filter_call(node) and not _has_to_thread_ancestor(node, parents)
    ]


def find_inline_filter_calls_by_async_function(source: str) -> dict[str, list[int]]:
    """Map each async function to its unwrapped ``TelegramMessage.query.filter``
    calls, module-wide. Only functions with at least one violation appear.

    This exists because the chain-walk lookup is factored into a helper
    (``_resolve_media_descriptor``) that ``fetch_reply_chain`` awaits — a scan
    scoped to ``fetch_reply_chain``'s own body would be vacuous against the
    real tree, which is the Risk 5 failure mode.
    """
    violations: dict[str, list[int]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(node):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        inline = [
            call.lineno
            for call in ast.walk(node)
            if _is_telegram_filter_call(call) and not _has_to_thread_ancestor(call, parents)
        ]
        if inline:
            violations[node.name] = inline
    return violations


_OFF_LOOP_INLINE_FIXTURE = textwrap.dedent(
    """
    async def fetch_reply_chain(client, chat_id, msg_id):
        records = list(TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg_id))
        return records
    """
)

_OFF_LOOP_WRAPPED_FIXTURE = textwrap.dedent(
    """
    async def fetch_reply_chain(client, chat_id, msg_id):
        records = await asyncio.to_thread(
            lambda: list(TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg_id))
        )
        return records
    """
)


def test_off_loop_fixture_inline_filter_is_flagged():
    """An inline blocking filter inside the async def must fail the predicate."""
    assert len(find_inline_filter_calls(_OFF_LOOP_INLINE_FIXTURE)) == 1


def test_off_loop_fixture_to_thread_wrapped_passes():
    """The sanctioned asyncio.to_thread(lambda: ...) shape passes."""
    assert find_inline_filter_calls(_OFF_LOOP_WRAPPED_FIXTURE) == []


def test_off_loop_fixture_missing_function_raises():
    """A renamed-away fetch_reply_chain is an error, never a vacuous pass."""
    with pytest.raises(ValueError):
        find_inline_filter_calls("async def something_else():\n    pass\n")


def test_off_loop_fixture_covers_helper_factoring():
    """The real tree factors the lookup into a helper fetch_reply_chain awaits;
    the module-wide scan must flag an inline call there too."""
    source = textwrap.dedent(
        """
        async def _resolve(msg, chat_id):
            return list(TelegramMessage.query.filter(chat_id=str(chat_id), message_id=msg.id))

        async def fetch_reply_chain(client, chat_id, msg_id):
            return await _resolve(None, chat_id)
        """
    )
    assert list(find_inline_filter_calls_by_async_function(source)) == ["_resolve"]


# _cache_walk_root's inline filter is a pre-existing defect of the same shape,
# explicitly out of scope for #2732 (plan No-Gos: moving it off-loop changes
# root-resolution cache behavior under load and deserves its own issue). The
# stale check below forces this exemption's removal the moment it is fixed.
_OFF_LOOP_EXEMPT_FUNCTIONS = {"_cache_walk_root"}


def test_fetch_reply_chain_lookups_are_off_loop():
    """Every TelegramMessage.query.filter in bridge/context.py's async
    functions is wrapped in asyncio.to_thread — the mechanical backstop for
    Risk 4 — except the ratcheted pre-existing exemption above. Scoped
    module-wide because the chain-walk lookup lives in the
    _resolve_media_descriptor helper, not lexically inside fetch_reply_chain.
    """
    source = (BRIDGE_DIR / "context.py").read_text()
    # Anchor: the walk entry point must still exist (raises if renamed away)
    # and must itself contain no inline lookup.
    assert find_inline_filter_calls(source, "fetch_reply_chain") == []

    by_function = find_inline_filter_calls_by_async_function(source)
    violations = {
        name: lines for name, lines in by_function.items() if name not in _OFF_LOOP_EXEMPT_FUNCTIONS
    }
    assert not violations, (
        "inline blocking TelegramMessage.query.filter call(s) in "
        f"bridge/context.py: {violations} — Popoto is synchronous redis-py; "
        "wrap the lookup in asyncio.to_thread so the callers' 3s "
        "asyncio.wait_for guard can actually preempt the walk (Risk 4)"
    )

    # Ratchet: every exemption must still match a real inline call, so a
    # stale entry fails loudly instead of silently widening the guard.
    stale = _OFF_LOOP_EXEMPT_FUNCTIONS - set(by_function)
    assert not stale, (
        f"off-loop exemption(s) no longer match an inline call: {sorted(stale)} "
        "— the pre-existing defect was fixed, delete the exemption"
    )
