"""Post-session memory extraction and outcome detection.

Extracts novel observations from agent response text via Haiku,
saves them as Memory records with category-based importance levels.

Detects outcomes by comparing injected thoughts against response
content using LLM judgment (with bigram fallback), feeds results
into ObservationProtocol.

All operations are async, wrapped in try/except — failures must never
crash the agent or block session completion.

Event-loop safety invariant (hotfix #1055), now enforced by the wrapper
(#1925):
    Every Haiku call in this module routes through the shared ``_llm_call``
    helper, which delegates to ``agent.llm.run_typed``. ``run_typed`` owns
    the ``anthropic.AsyncAnthropic`` construction, the ``async with``
    httpx-cleanup invariant, the outer ``asyncio.wait_for(hard_timeout)``,
    and the shared ``agent.anthropic_client.semaphore_slot()`` acquisition
    -- this module no longer constructs an Anthropic client directly. See
    ``agent/llm/wrapper.py`` and ``docs/features/nonharness-llm-wrapper.md``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

from pydantic import BaseModel

from agent.llm import LLMCallError, run_typed
from config.settings import settings

logger = logging.getLogger(__name__)

# Timeout constants for Anthropic extraction calls (hotfix #1055).
#
# Double-timeout rationale:
#   - _EXTRACTION_SDK_TIMEOUT: passed to messages.create(timeout=...) so the
#     Anthropic SDK (httpx) raises APITimeoutError on idle/slow sockets.
#   - _EXTRACTION_HARD_TIMEOUT: outer asyncio.wait_for; fires from a separate
#     asyncio timer so it still fires even when the SDK timer does not (e.g.,
#     half-open TCP sockets where no socket event ever arrives).
#   - 5s buffer lets the SDK raise its own typed error first for cleaner logs.
#
# Sourced from settings.timeouts.anthropic_sdk_s / anthropic_hard_s (issue
# #1968) -- these two fields are the single source of truth for BOTH these
# constants and agent/llm/wrapper.py's `DEFAULT_SDK_TIMEOUT` /
# `DEFAULT_HARD_TIMEOUT`, which previously duplicated the same 30.0/35.0
# pair verbatim. Preserve the two-timer structure -- never collapse to one
# value.
_EXTRACTION_SDK_TIMEOUT = settings.timeouts.anthropic_sdk_s
_EXTRACTION_HARD_TIMEOUT = settings.timeouts.anthropic_hard_s

# -----------------------------------------------------------------------------
# JSON-shrapnel + refusal-prose hardening (issue #1212).
#
# These constants are tuned against junk Memory records produced before the
# parser was hardened. Each refusal pattern is annotated with the originating
# Memory IDs so future readers can trace why the substring is here. When Haiku
# rephrases its refusals over time, the response is to *append* a new pattern
# to this tuple — not to re-architect. The failure mode is silent rejection of
# refusal text only; a too-broad pattern would silently drop legitimate
# observations, so additions must stay narrow (full phrases, not bare
# keywords). The `memory-dedup` and `memory-quality-audit` nightly reflections
# (the latter imports `_looks_like_refusal` directly — see issue #1231) provide
# the recurring safety net.
# -----------------------------------------------------------------------------
_REFUSAL_PATTERNS: tuple[str, ...] = (
    "there is no agent session",  # 5be7da58 / 76dbd772 / 796e1429
    "no agent session response",  # 76dbd772
    "please provide the session",  # 1540c270
    "**rationale:**",  # c219982fbf9746f9a3ff9b09b042faa6 (refusal-style preamble)
    "contains no novel observations",  # c219982fbf9746f9a3ff9b09b042faa6
    "no agent session was provided",  # 1540c270 / 796e1429
    "session was initialized with empty input",  # 1540c270 (placeholder echo)
    "no agent session response to analyze",  # 5be7da58 (canonical refusal opener)
    # --- Extended refusal phrasings (issue #1822). Haiku rephrased its refusal
    # in distinct ways that escaped the #1212 vocabulary and were saved as
    # high-confidence noise. Each is appended as a narrow FULL phrase (never a
    # bare keyword) and annotated with the originating Memory ID from the
    # 2026-06-29 production investigation. The shared anchor is meta-commentary
    # *about the session* ("the session response contains only…") rather than a
    # real observation. Narrowness is guarded by TestRefusalPatternsNarrowness.
    "the session response contains only metadata",  # 0208f60d
    "the session response contains only system metadata",  # b0b24ef7
    "the session response contains procedural documentation",  # 517ccf5
    "does not contain any substantive observations",  # 9fd6006a
    "the session response does not contain any",  # 1a572475
    "no substantive observations to extract",  # 868869
    "the session response appears to contain only",  # 8f2c9d5c
)

# -----------------------------------------------------------------------------
# Session-scoping boilerplate filter (issue #1822, Fix 3).
#
# SDLC sub-sessions inject a scope-boundary preamble into their system prompt
# ("this session is scoped to sdlc-local-N; do not include work from other
# sessions"). Haiku occasionally reads that infrastructure text as session
# context and extracts it as a high-confidence "observation" that recurs on
# every SDLC cycle. These markers are STRUCTURAL, not observational, so any
# parsed observation containing one is dropped before it can be persisted.
#
# Only substrings ACTUALLY OBSERVED in real noise records are listed here.
# Earlier-proposed markers ("scope boundary", "cross-session") were UNCONFIRMED
# and deliberately omitted — adding an unevidenced marker risks silently
# dropping legitimate observations. Narrowness is guarded by
# TestScopingMarkersNarrowness.
# -----------------------------------------------------------------------------
_SCOPING_MARKERS: tuple[str, ...] = (
    "sdlc-local-",  # 1911b062 (e.g. "scoped to isolated session contexts (sdlc-local-96)")
    "scoped to isolated session",  # 1911b062 (scope-boundary preamble echo)
)

# Single-line JSON-syntax fragment, e.g. '"tags": ["a", "b"]' or
# '"category": "decision"'. Saved by the line-based fallback when the strict
# json.loads() raised on a fenced/preamble-wrapped payload (root cause of the
# JSON-shrapnel symptom in issue #1212).
_JSON_SHRAPNEL_RE = re.compile(r'^"[a-z_]+"\s*:\s*.*,?\s*$')

# Whitespace-dominance threshold for the pre-LLM substantive-content guard.
# An input is considered whitespace-dominated when its non-whitespace ratio is
# below this value — at which point we skip the Haiku call entirely. The 0.3
# value is empirical (tested against terse-but-real inputs like "Done. PR #X
# merged." which sit at ~80% non-whitespace). Tune from this single edit if
# production data shows false positives. Locked in by
# test_whitespace_dominant_input_skips_llm_call which exercises both sides of
# the boundary (25% rejected, 35% accepted).
_MIN_NON_WHITESPACE_RATIO = 0.3

# -----------------------------------------------------------------------------
# LLM-based refusal-detector complement (issue #1829).
#
# Default-OFF: the closed-vocabulary ``_REFUSAL_PATTERNS`` check above is a
# fundamentally finite vocabulary — whenever Haiku phrases a refusal in a way
# that isn't a listed substring, the novel phrasing escapes and gets persisted
# as noise (the recurring #1497/#1786/#1931/#2016 anomaly-cluster). This
# complement adds one optional yes/no Haiku call (via the existing
# ``_llm_call`` helper) that classifies the post-LLM extraction output as
# REFUSAL or CONTENT, generalizing beyond the enumerated phrasings. It is
# gated behind ``MEMORY_REFUSAL_LLM_ENABLED``, which defaults to False — zero
# cost, zero behavior change until an operator opts in.
#
# Fail-open contract: any error raised by the complement call (``TimeoutError``
# or otherwise) is caught, recorded via ``_record_extraction_error``, and
# extraction PROCEEDS to parse — a classifier failure must never discard a
# legitimate extraction. Likewise, any unexpected/malformed model output is
# treated as CONTENT (not REFUSAL) at the parse boundary.
#
# Call-count guarantee: at most one extra Haiku call per non-empty extraction,
# and only when the flag is ON AND the closed-vocab ``_looks_like_refusal``
# check already returned False (obvious refusals never reach this call).
# -----------------------------------------------------------------------------
_REFUSAL_LLM_ENV_VAR = "MEMORY_REFUSAL_LLM_ENABLED"


def _refusal_llm_enabled() -> bool:
    """Return True if the LLM refusal-detector complement is enabled.

    Read from ``os.environ`` at call time (not module-capture) so tests can
    toggle it with ``monkeypatch.setenv``. Defaults to False. Mirrors
    ``agent/tool_budget.py``'s ``_env_true`` true-value parsing (a local copy,
    not an import, to avoid new coupling): any value except ``""``, ``"0"``,
    ``"false"``, ``"no"`` (case-insensitive) is truthy.
    """
    return os.environ.get(_REFUSAL_LLM_ENV_VAR, "false").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def extract_json_payload(raw_text: str) -> str | None:
    r"""Strip markdown code fences and slice to outermost JSON brackets.

    Handles two common Haiku output shapes that broke strict ``json.loads``
    before the issue #1212 fix:

      1. Code-fenced output: ```json\n[...]\n``` (with or without the
         ``json`` language tag, with or without surrounding whitespace).
      2. Preamble + JSON: ``"Here are the observations:\n[...]"`` — prose
         before the array that the model adds despite an explicit "return
         only JSON" instruction.

    Returns the cleaned JSON-shaped substring if found, else ``None``. Pure
    function — no IO, no exceptions raised. Returning ``None`` means "fall
    through to the existing line-based fallback"; returning a string means
    "this is the JSON payload, try ``json.loads`` on it".

    The slice is to the outermost ``[...]`` or ``{...}`` of matching type.
    Nested content inside the brackets is preserved verbatim — the function
    does NOT validate the JSON, only extracts it. ``json.loads`` downstream
    is the validator.
    """
    if not raw_text:
        return None

    text = raw_text.strip()
    if not text:
        return None

    # Strip markdown code fences if present (\`\`\`json...\`\`\` or \`\`\`...\`\`\`)
    if text.startswith("```"):
        # Drop the opening fence (and optional language tag like "json")
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        else:
            # Single-line fence — strip the leading backticks and any tag word
            text = text.lstrip("`").lstrip()
            # Remove a leading lang tag (e.g. "json ") if present
            if " " in text[:10]:
                text = text.split(" ", 1)[1]

        # Drop the closing fence if present
        closing = text.rfind("```")
        if closing != -1:
            text = text[:closing]

        text = text.strip()
        if not text:
            return None

    # Slice to the outermost [...] or {...}. Prefer arrays since the
    # extraction prompt asks for an array; fall back to bare objects.
    first_bracket = text.find("[")
    first_brace = text.find("{")

    # Decide which bracket type comes first (outermost wins).
    if first_bracket == -1 and first_brace == -1:
        return None
    if first_bracket == -1:
        start, end_char = first_brace, "}"
    elif first_brace == -1:
        start, end_char = first_bracket, "]"
    elif first_bracket < first_brace:
        start, end_char = first_bracket, "]"
    else:
        start, end_char = first_brace, "}"

    end = text.rfind(end_char)
    if end == -1 or end <= start:
        return None

    sliced = text[start : end + 1].strip()
    return sliced or None


def _looks_like_refusal(text: str) -> bool:
    """Return True if ``text`` matches a known refusal/JSON-shrapnel pattern.

    Two checks, OR-combined:

      1. Substring match against ``_REFUSAL_PATTERNS`` (case-insensitive).
         These are narrow, full-phrase patterns — a substring like ``"session"``
         alone would never appear here, only complete phrases like
         ``"there is no agent session"``. This prevents legitimate
         observations that mention "session" or "no novel" from being
         mistakenly rejected.
      2. Single-line JSON-syntax match against ``_JSON_SHRAPNEL_RE``. Catches
         the exploded JSON lines (``"tags": [...]``) that the line-based
         fallback used to persist as separate Memory records.

    Empty/whitespace-only input returns ``False`` — the 50-char guard at the
    callsite handles those, and treating empty as refusal would be confusing
    (it's not refusal, it's just nothing).
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    for pattern in _REFUSAL_PATTERNS:
        if pattern in lowered:
            return True
    if _JSON_SHRAPNEL_RE.match(stripped):
        return True
    return False


def _is_scoping_boilerplate(text: str) -> bool:
    """Return True if ``text`` echoes SDLC session-scoping boilerplate (Fix 3).

    Case-insensitive substring match against ``_SCOPING_MARKERS``. These markers
    are structural session-infrastructure text (scope-boundary preambles, session
    slugs), never genuine observations — any parsed observation containing one is
    dropped before persistence.

    Narrow by construction: only full, evidenced markers are listed, so a
    legitimate observation that merely mentions "session" or "scope" is NOT
    dropped (guarded by ``TestScopingMarkersNarrowness``). Empty/whitespace input
    returns ``False``.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _SCOPING_MARKERS)


class ExtractionResult(BaseModel):
    """Generic typed wrapper around a Haiku call's raw text response.

    ``_llm_call`` is shared infrastructure across four call sites in this
    module (primary observation extraction, the refusal-detector
    complement, post-merge learning, outcome judgment) with four genuinely
    different response shapes -- a bare word, a JSON array of observations,
    a JSON object, a JSON array of judgments. Rather than force one of
    those shapes onto every caller, ``ExtractionResult`` captures "whatever
    text the prompt asked for" as a single schema-validated field, and this
    module's existing raw-text handling (refusal-pattern matching, the
    ``extract_json_payload`` + ``json.loads`` tolerant parser, the
    line-based fallback) operates on ``.text`` exactly as it did on the old
    ``msg.content[0].text.strip()`` before this migration (#1925). Preserves
    ``_llm_call``'s external ``-> str`` contract byte-for-byte so every
    caller -- including the merged refusal-detector complement (#1829) --
    is unaffected by the call mechanism swap.
    """

    text: str


async def _llm_call(
    model: str,
    max_tokens: int,
    messages: list,
) -> str:
    """Centralized Haiku call, now routed through ``agent.llm.run_typed`` (#1925).

    Every Haiku call site in this module routes through here. ``run_typed``
    owns the hotfix #1055 invariants that used to live in this function
    directly:

      * A fresh ``anthropic.AsyncAnthropic`` inside ``async with`` for
        deterministic httpx cleanup (no half-open sockets after shutdown).
      * The outer ``asyncio.wait_for(hard_timeout)`` timer that fires even
        when the SDK timer doesn't (e.g. half-open TCP).
      * The SDK-level ``timeout`` kwarg so the SDK raises a typed
        ``APITimeoutError`` first for cleaner logs.
      * The shared ``agent.anthropic_client.semaphore_slot()`` (#1111),
        held for the whole call.

    ``max_tokens`` is accepted for signature compatibility with every
    existing call site but is not forwarded -- ``run_typed`` has no output
    token cap knob. This is a documented, accepted gap: none of this
    module's prompts rely on truncation for correctness.

    Constants are read at call time (not captured) so test monkeypatching of
    ``_EXTRACTION_SDK_TIMEOUT`` / ``_EXTRACTION_HARD_TIMEOUT`` still works.

    Translates ``LLMCallError`` back to ``TimeoutError`` when the wrapper's
    hard timeout fired (``isinstance(e.__cause__, TimeoutError)``), so every
    existing ``except TimeoutError:`` call site in this module keeps working
    unchanged. Any other failure (provider error, exhausted schema retry)
    propagates as ``LLMCallError`` -- callers' broad ``except Exception``
    blocks already handle that; the recorded ``error_class`` string may
    differ from the old raw SDK exception name (e.g. ``LLMCallError``
    instead of ``APITimeoutError``), which is an accepted analytics-only
    drift, not an observable behavior change (see the plan's Rabbit Holes:
    per-site counters need not survive byte-for-byte).

    Returns the assistant text (``.text``, stripped).
    """
    prompt = messages[0]["content"]
    try:
        result = await run_typed(
            prompt,
            ExtractionResult,
            model=model,
            sdk_timeout=_EXTRACTION_SDK_TIMEOUT,
            hard_timeout=_EXTRACTION_HARD_TIMEOUT,
        )
    except LLMCallError as e:
        if isinstance(e.__cause__, TimeoutError):
            raise TimeoutError(str(e)) from e
        raise
    return result.text.strip()


def _record_extraction_error(
    error_class: str,
    session_id: str,
    project_key: str | None = None,
) -> None:
    """Emit ``memory.extraction.error`` analytics counter (hotfix #1055).

    Non-fatal — silent if analytics unavailable. Skips ``CancelledError`` which
    is expected on worker shutdown and carries no signal. Every ``except``
    branch in this module's three async-Anthropic call sites must call this.
    """
    if error_class == "CancelledError":
        return
    try:
        from analytics.collector import record_metric

        record_metric(
            "memory.extraction.error",
            1.0,
            {
                "error_class": error_class.lower(),
                "session_id": session_id,
                "project_key": project_key or "",
            },
        )
    except Exception as e:
        # D3 (issue #1817): was silently swallowed. Non-fatal by design
        # (analytics must never crash extraction) but now observable.
        logger.debug(
            "[memory_extraction] record_metric(memory.extraction.error) failed for session %s: %s",
            session_id,
            e,
        )


async def _looks_like_refusal_llm(text: str) -> bool:
    """Ask Haiku whether ``text`` is a refusal (yes/no complement).

    This WRAPS the closed-vocabulary ``_looks_like_refusal`` check — it never
    replaces it, and is only invoked when ``_refusal_llm_enabled()`` is True
    AND the closed-vocab check already returned False (obvious refusals are
    caught earlier and never reach this call). Issues exactly one
    ``_llm_call`` against a bounded slice of ``text`` and expects exactly one
    of two tokens back: ``REFUSAL`` or ``CONTENT``.

    Returns True only when the model's reply starts with ``REFUSAL``
    (case-insensitive). Any other output — including unexpected or malformed
    text — is treated as ``CONTENT`` (fail-open at the parse boundary; a
    classifier that returns garbage must never suppress a legitimate
    extraction).
    """
    from config.models import MODEL_FAST

    prompt = (
        "Is the following text a refusal or meta-commentary about the ABSENCE "
        "of content (e.g. 'there is nothing to extract', 'no observations "
        "found'), as opposed to a genuine observation about a real event, "
        "decision, or pattern?\n\n"
        "Reply with EXACTLY one word: REFUSAL or CONTENT.\n\n"
        f"---\n\n{text[:4000]}"
    )
    raw = await _llm_call(
        model=MODEL_FAST,
        max_tokens=5,
        messages=[{"role": "user", "content": prompt}],
    )
    return raw.strip().upper().startswith("REFUSAL")


# Extraction prompt for Haiku — structured JSON output
EXTRACTION_PROMPT = (
    "Extract novel observations from this agent session response.\n"
    "Return a JSON array of objects, each with:\n"
    '  "category": one of "correction", "decision", "pattern", "surprise"\n'
    '  "observation": the observation text (one sentence, specific)\n'
    '  "file_paths": list of file paths referenced (empty list if none)\n'
    '  "tags": list of domain tags (1-3 short keywords)\n'
    "\n"
    "Only include genuinely novel, specific observations.\n"
    "If none, return: []\n"
    "\n"
    "Example:\n"
    '[{"category": "decision", "observation": "chose blue-green deployment over rolling updates",'
    ' "file_paths": ["deploy/config.yaml"], "tags": ["deployment", "infrastructure"]}]'
)

# Importance levels for categorized extraction
CATEGORY_IMPORTANCE = {
    "correction": 4.0,
    "decision": 4.0,
    "pattern": 1.0,
    "surprise": 1.0,
}
DEFAULT_CATEGORY_IMPORTANCE = 1.0  # fallback for uncategorized

# Post-merge extraction prompt -- requests structured JSON with metadata
POST_MERGE_EXTRACTION_PROMPT = (
    "You are reviewing a merged pull request. Extract the single most"
    " important project-level takeaway — knowledge that would help a"
    " developer working on this codebase in the future.\n"
    "\n"
    "Focus on architectural decisions, design patterns chosen, or"
    " conventions established. Skip implementation details.\n"
    "\n"
    "Return a JSON object with these fields:\n"
    '  "observation": the takeaway (one sentence, specific)\n'
    '  "category": one of "decision", "correction", "pattern", "surprise"\n'
    '  "tags": list of domain tags (1-3 short keywords)\n'
    '  "file_paths": list of key file paths from the diff (up to 5)\n'
    "\n"
    "If there is no meaningful project-level takeaway, return NONE.\n"
    "\n"
    "PR Title: {title}\n"
    "PR Description: {body}\n"
    "Diff Summary: {diff_summary}"
)


async def extract_observations_async(
    session_id: str,
    response_text: str,
    project_key: str | None = None,
    turn_count: int | None = None,
    is_conversational: bool = True,
) -> list[dict]:
    """Extract novel observations from agent response via Haiku.

    Calls Haiku to identify decisions, surprises, corrections, and patterns.
    Saves each as a Memory record with category-based importance (4.0 for
    corrections/decisions, 1.0 for patterns/surprises).

    Fix 2 (#1822) — trivial-session gate: a CLI-origin single-turn session (e.g.
    the user runs ``/update`` and the session ends) produces only low-signal
    noise, so extraction is skipped when ``turn_count <= 1`` AND the session is
    NOT conversational. A substantive single-turn *conversational* (Telegram)
    message is high-value and still extracts. ``turn_count=None`` (unknown) and
    ``is_conversational=True`` (the defaults) make the gate a no-op, preserving
    backward-compatible behavior for direct callers and tests.

    Per-session cumulative cap (issue #2040): a content-agnostic structural
    backstop against the memory-quality audit's agent-id-cluster signal.
    Enforced at two coordinated points sharing one ``current_count`` reading
    of non-superseded ``extraction-{session_id}`` records: (a) a pre-LLM
    short-circuit that skips the Haiku call entirely once at/above the cap,
    and (b) a per-batch clamp on the save loop so a single call can never
    push the cumulative total past the cap (closing the check-then-batch
    overshoot a pre-LLM-only check would allow). ``settings.features.
    memory_extraction_session_cap`` of 0 disables the cap. The count query
    fails open (proceeds unclamped) on any error.

    Returns list of dicts with keys: content, memory_id.
    """
    # Fix 2 (#1822) trivial-session gate — a pure early return placed BEFORE the
    # try block so it cannot be swallowed and makes NO Haiku call. Only skips
    # non-conversational (CLI-origin) single-turn sessions; conversational
    # single-turn messages still extract.
    if turn_count is not None and turn_count <= 1 and not is_conversational:
        logger.debug(
            "[memory_extraction] Trivial-session gate — skipping extraction for "
            "session_id=%s (turn_count=%s, is_conversational=False)",
            session_id,
            turn_count,
        )
        return []
    # Guard order (issue #1212): the 50-char check is empirically working and
    # MUST stay first — Tom verified it catches true empties in the issue
    # comment IC_kwDOEYGa088AAAABAwQnJw. The new refusal-pattern + whitespace
    # guards are ADDITIVE cost optimizations that skip the Haiku call when the
    # input is obviously bad. They are NOT replacements for the post-LLM
    # refusal check, which is the load-bearing defense — refusal can emerge
    # from inputs that pass all three pre-LLM guards (length OK, no refusal
    # substring, enough non-whitespace). Dual-filter is by design.
    if not response_text or len(response_text.strip()) < 50:
        return []
    if _looks_like_refusal(response_text):
        logger.debug(
            "[memory_extraction] Pre-LLM refusal-pattern match — skipping extraction "
            "for session_id=%s",
            session_id,
        )
        return []
    # Whitespace-dominance ratio guard. len(response_text) > 0 is guaranteed by
    # the 50-char check above, so the division is always safe.
    non_ws_chars = len(re.sub(r"\s+", "", response_text))
    if non_ws_chars / len(response_text) < _MIN_NON_WHITESPACE_RATIO:
        logger.debug(
            "[memory_extraction] Pre-LLM whitespace-dominance guard — skipping extraction "
            "for session_id=%s (ratio=%.2f)",
            session_id,
            non_ws_chars / len(response_text),
        )
        return []

    # Per-session cumulative cap (issue #2040). Read at call time (not
    # module-capture) so tests can monkeypatch settings. session_cap <= 0
    # disables the cap entirely (unbounded, matching prior behavior).
    # current_count is read ONCE here and reused unchanged at the per-batch
    # clamp below (search "save_limit") so the invariant
    # current_count + saved <= session_cap holds exactly, not merely at this
    # pre-LLM check -- a pre-LLM-only check would allow a call that passes at
    # current_count = cap-1 to still save up to per_call_cap (10) more,
    # overshooting to cap-1+10, which is exactly what re-arms the audit's
    # agent-id-cluster signal (see docs/plans/memory_extraction_session_cap.md).
    # Only NON-superseded records count, so a session already cleaned up by
    # the audit is not permanently locked out (self-healing). The count query
    # fails open on any error: proceed with extraction, unclamped, because
    # this module must never crash the agent or block session completion.
    session_cap = settings.features.memory_extraction_session_cap
    current_count = 0
    if session_cap > 0:
        try:
            from models.memory import Memory

            current_count = sum(
                1
                for m in Memory.query.filter(agent_id=f"extraction-{session_id}")
                if not getattr(m, "superseded_by", None)
            )
        except Exception as e:
            logger.debug(
                "[memory_extraction] Session-cap count query failed (non-fatal, "
                "fail-open, unclamped) for session_id=%s: %s",
                session_id,
                e,
            )
            current_count = 0
            session_cap = 0  # disable both the short-circuit and the clamp below

        if session_cap > 0 and current_count >= session_cap:
            logger.info(
                "[memory_extraction] Session cap hit — session_id=%s has %d "
                "non-superseded extraction records (cap=%d); skipping Haiku call",
                session_id,
                current_count,
                session_cap,
            )
            try:
                from analytics.collector import record_metric

                record_metric(
                    "memory.extraction.session_cap_hit",
                    1.0,
                    {
                        "session_id": session_id,
                        "project_key": project_key,
                        "stage": "pre_llm",
                    },
                )
            except Exception as e:
                logger.debug(
                    "[memory_extraction] record_metric(memory.extraction.session_cap_hit) "
                    "failed for session %s: %s",
                    session_id,
                    e,
                )
            return []

    try:
        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("[memory_extraction] No Anthropic API key, skipping extraction")
            return []

        # Truncate response to avoid token limits
        truncated = response_text[:8000]

        # hotfix #1055: _llm_call centralizes AsyncAnthropic + async with + double-timeout.
        # Sync anthropic.Anthropic is forbidden here — it blocks the worker event loop.
        try:
            raw_text = await _llm_call(
                model=MODEL_FAST,
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": f"{EXTRACTION_PROMPT}\n\n---\n\n{truncated}",
                    }
                ],
            )
        except TimeoutError:
            logger.warning(
                "[memory_extraction] Anthropic call exceeded %.1fs hard timeout (non-fatal); "
                "extraction skipped for session_id=%s",
                _EXTRACTION_HARD_TIMEOUT,
                session_id,
            )
            _record_extraction_error("TimeoutError", session_id, project_key)
            return []

        if raw_text.upper() == "NONE" or not raw_text:
            logger.debug("[memory_extraction] No novel observations found")
            return []

        # Post-LLM refusal-pattern filter (issue #1212 PRIMARY defense).
        # Even when input passed all three pre-LLM guards, Haiku can still
        # return refusal prose ("There is no agent session response to
        # analyze…"). Drop those before parsing.
        if _looks_like_refusal(raw_text):
            logger.debug(
                "[memory_extraction] Post-LLM refusal text — skipping save for session_id=%s",
                session_id,
            )
            return []

        # LLM refusal-detector complement (issue #1829) — optional, default-OFF.
        # Wraps (never replaces) the closed-vocab check above. Only runs when
        # the flag is on AND the closed-vocab check did not already
        # short-circuit. Any error fails open: extraction proceeds to parse.
        if _refusal_llm_enabled():
            try:
                if await _looks_like_refusal_llm(raw_text):
                    logger.debug(
                        "[memory_extraction] LLM refusal-complement match — skipping save "
                        "for session_id=%s",
                        session_id,
                    )
                    return []
            except TimeoutError:
                logger.warning(
                    "[memory_extraction] Refusal-complement call exceeded hard timeout "
                    "(non-fatal, fail-open); extraction proceeds for session_id=%s",
                    session_id,
                )
                _record_extraction_error("TimeoutError", session_id, project_key)
            except Exception as e:
                logger.debug(
                    "[memory_extraction] Refusal-complement failed (non-fatal, fail-open): %s",
                    e,
                )
                _record_extraction_error(type(e).__name__, session_id, project_key)

        # Parse observations with category-aware importance
        parsed = _parse_categorized_observations(raw_text)

        # Resolve project_key BEFORE the not-parsed short-circuit so the
        # fallback_dropped counter below always has a key to increment
        # (issue #2201). Keeps its own None early-return unchanged.
        if not project_key:
            from config.project_key_resolver import resolve_project_key

            project_key = resolve_project_key()
            if project_key is None:
                logger.warning(
                    "[memory_extraction] extract_observations write skipped: "
                    "resolve_project_key returned None (VALOR_PROJECT_KEY=%r)",
                    os.environ.get("VALOR_PROJECT_KEY"),
                )
                return []

        if not parsed:
            from models.memory_gate import _increment_gate_counter

            _increment_gate_counter(project_key, "fallback_dropped")
            return []

        # Save each observation as Memory
        from models.memory import SOURCE_AGENT, Memory

        # Per-batch clamp (issue #2040) — the load-bearing half of the
        # per-session cap. per_call_cap is the pre-existing per-response
        # limit, now a named local. When session_cap > 0, clamp the save
        # slice to whatever headroom remains under the cap given the
        # current_count reading taken above (NOT re-queried), so
        # current_count + saved <= session_cap holds exactly after this call.
        per_call_cap = 10  # existing behavior, now a named local
        if session_cap > 0:
            save_limit = max(0, min(per_call_cap, session_cap - current_count))
        else:
            save_limit = per_call_cap

        if save_limit < min(per_call_cap, len(parsed)):
            logger.info(
                "[memory_extraction] Session cap clamp — session_id=%s save slice "
                "reduced to %d (current_count=%d, cap=%d, parsed=%d)",
                session_id,
                save_limit,
                current_count,
                session_cap,
                len(parsed),
            )
            try:
                from analytics.collector import record_metric

                record_metric(
                    "memory.extraction.session_cap_hit",
                    1.0,
                    {
                        "session_id": session_id,
                        "project_key": project_key,
                        "stage": "batch_clamp",
                    },
                )
            except Exception as e:
                logger.debug(
                    "[memory_extraction] record_metric(memory.extraction.session_cap_hit) "
                    "failed for session %s: %s",
                    session_id,
                    e,
                )

        saved = []
        for obs_content, importance, metadata in parsed[:save_limit]:  # per-session cap clamp
            m = Memory.safe_save(
                agent_id=f"extraction-{session_id}",
                project_key=project_key,
                content=obs_content[:500],
                importance=importance,
                source=SOURCE_AGENT,
                metadata=metadata,
            )
            if m:
                # Fire-and-forget async title generation
                # (writer path #2: post-session extraction).
                try:
                    from agent.private_tag import strip_private
                    from tools.memory_search.title_generator import (
                        generate_title_async,
                    )

                    generate_title_async(m.memory_id, strip_private(obs_content[:500]))
                except Exception as e:
                    # D3 (issue #1817): title generation is best-effort — a
                    # missing title never blocks the memory save — but was
                    # previously invisible on failure.
                    logger.debug(
                        "[memory_extraction] generate_title_async failed for memory %s: %s",
                        getattr(m, "memory_id", "?"),
                        e,
                    )

                saved.append(
                    {
                        "content": obs_content[:500],
                        "memory_id": getattr(m, "memory_id", ""),
                    }
                )

        logger.info(
            f"[memory_extraction] Extracted {len(saved)} observations from session {session_id}"
        )

        # Analytics: record extraction count
        try:
            from analytics.collector import record_metric

            record_metric(
                "memory.extraction",
                float(len(saved)),
                {"session_id": session_id, "project_key": project_key},
            )
        except Exception as e:
            # D3 (issue #1817): was silently swallowed.
            logger.debug(
                "[memory_extraction] record_metric(memory.extraction) failed for session %s: %s",
                session_id,
                e,
            )

        return saved

    except Exception as e:
        logger.warning(f"[memory_extraction] Extraction failed (non-fatal): {e}")
        _record_extraction_error(type(e).__name__, session_id, project_key)
        return []


def _parse_categorized_observations(raw_text: str) -> list[tuple[str, float, dict]]:
    """Parse Haiku output into (content, importance, metadata) tuples.

    Tries tolerant JSON parsing first (strips markdown code fences and slices
    to outermost brackets via ``extract_json_payload``, then ``json.loads``).
    On a successful JSON parse with ≥1 valid observation, short-circuits and
    returns (issue #1212).

    Any other outcome -- no JSON-shaped substring found, ``json.loads``
    raising, or the payload parsing but yielding zero valid observations --
    returns an empty list. Issue #2201 removed the line-splitting fallback
    that used to run in these cases: it exploded a single unparseable
    payload into one Memory record per line ("shrapnel"). Unparseable
    output is now dropped and counted (`fallback_dropped`, incremented by
    the caller `extract_observations_async` once `project_key` is
    resolved) rather than saved in any form. No retry is attempted -- JSON
    is the sanctioned contract since #1212/#2016.

    This function takes no `project_key` parameter and must never
    reference one -- see the caller for where the drop is counted.

    Returns list of (content_string, importance_float, metadata_dict) tuples.
    """
    # Refusal short-circuit: if the LLM returned refusal prose, drop it
    # immediately. Belt-and-suspenders alongside the post-LLM check in
    # extract_observations_async — this defends call sites that invoke the
    # parser directly (tests, future call sites).
    if _looks_like_refusal(raw_text):
        return []

    # Tolerant JSON path: strip code fences / preamble, then strict json.loads.
    # If extraction yields a JSON-shaped substring but parsing fails, fall
    # through to the line-based parser (worst case: same behavior as before
    # the fix). If extraction yields no JSON-shaped substring, also fall
    # through.
    payload = extract_json_payload(raw_text)
    if payload is not None:
        try:
            data = json.loads(payload)
            # Handle bare dict (single observation) — wrap in list
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                results: list[tuple[str, float, dict]] = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    # Fix A (#2016): type-guard BOTH fields before calling any
                    # string method on either. category must be checked before
                    # .lower() and observation before len()/_is_scoping_boilerplate/
                    # _looks_like_refusal — a malformed item (e.g. category: null)
                    # would otherwise raise AttributeError that the surrounding
                    # except (json.JSONDecodeError, TypeError) does NOT catch,
                    # aborting the whole batch. This closes the whole-text-vs-
                    # per-record asymmetry: the line-based fallback below already
                    # filters each line through _looks_like_refusal, but this JSON
                    # branch previously let shrapnel-shaped observation values
                    # through untouched, causing the same anomaly cluster to be
                    # re-filed repeatedly by the audit (#1497/#1786/#1931).
                    category_raw = item.get("category", "")
                    if not isinstance(category_raw, str):
                        continue
                    category = category_raw.lower()
                    observation = item.get("observation", "")
                    if not isinstance(observation, str):
                        continue
                    if not observation or len(observation) < 10:
                        continue
                    # Fix 3 (#1822): drop session-scoping boilerplate echoed as
                    # an observation (e.g. "...scoped to ... (sdlc-local-96)...").
                    if _is_scoping_boilerplate(observation):
                        continue
                    # Fix A (#2016): apply the same per-record refusal/shrapnel
                    # filter the line-based fallback already applies, so
                    # JSON-shrapnel-shaped values (e.g. '"category": "decision"')
                    # never get saved and later superseded by the audit.
                    if _looks_like_refusal(observation):
                        # NIT (#2016 re-critique): logger.info, not logger.debug —
                        # debug is typically off in production, and that blind
                        # spot is exactly what caused four historical
                        # misdiagnoses of this recurring signal.
                        logger.info(
                            "Fix A (#2016) dropped JSON-branch observation: category=%s preview=%r",
                            category,
                            observation[:60],
                        )
                        continue
                    importance = CATEGORY_IMPORTANCE.get(category, DEFAULT_CATEGORY_IMPORTANCE)
                    metadata = {
                        "category": category,
                        "file_paths": item.get("file_paths", []),
                        "tags": item.get("tags", []),
                    }
                    results.append((observation, importance, metadata))
                if results:
                    # Short-circuit: tolerant JSON path succeeded, do NOT run
                    # the line-based fallback. This is the issue #1212 fix —
                    # previously, fenced JSON would raise on the strict
                    # json.loads, fall through to the fallback, and explode
                    # one observation into 4-5 shrapnel rows.
                    return results
        except (json.JSONDecodeError, TypeError):
            pass  # Fall through to the unconditional drop below

    # Unparseable payloads are dropped+counted (issue #2201) — do not
    # re-add a line-splitting fallback here. This single unconditional
    # return converges all three non-JSON-success cases: (1) no JSON-shaped
    # substring found above, (2) json.loads raised (caught above), and
    # (3) the payload parsed but `results` was empty (fell through the `if
    # results:` short-circuit). Any of these previously exploded raw_text
    # into one Memory record per line.
    return []


async def extract_post_merge_learning(
    pr_title: str,
    pr_body: str,
    diff_summary: str,
    project_key: str | None = None,
) -> dict | None:
    """Extract and save a project-level takeaway from a merged PR.

    Calls Haiku to distill the single most important learning from a merged
    pull request, then saves it as a Memory with importance=7.0.

    Args:
        pr_title: The pull request title.
        pr_body: The pull request body/description.
        diff_summary: A summary of the code changes (e.g., filenames changed).
        project_key: Project partition key. Resolved from env if not provided.

    Returns:
        Dict with memory_id and content if saved, or None if nothing to save.
    """
    if not pr_title:
        return None

    try:
        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning(
                "[memory_extraction] No Anthropic API key, skipping post-merge extraction"
            )
            return None

        prompt = POST_MERGE_EXTRACTION_PROMPT.format(
            title=pr_title,
            body=(pr_body or "")[:4000],
            diff_summary=(diff_summary or "")[:4000],
        )

        # hotfix #1055: _llm_call centralizes AsyncAnthropic + async with + double-timeout.
        # Called from .claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()
        # via asyncio.run(...). The async-with + asyncio.wait_for pattern is still
        # safe under asyncio.run because both helpers are reentrant-free and
        # produce no nested loops. Guarded by
        # test_extract_post_merge_learning_runs_inside_asyncio_run.
        try:
            raw_text = await _llm_call(
                model=MODEL_FAST,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
        except TimeoutError:
            logger.warning(
                "[memory_extraction] Post-merge Anthropic call exceeded %.1fs hard timeout "
                "(non-fatal); post-merge learning skipped",
                _EXTRACTION_HARD_TIMEOUT,
            )
            _record_extraction_error("TimeoutError", "post-merge", project_key)
            return None

        # Check if response indicates no takeaway (NONE at start, empty, or too short)
        first_line = raw_text.split("\n")[0].strip()
        if first_line.upper() == "NONE" or not raw_text or len(raw_text) < 20:
            logger.debug("[memory_extraction] No post-merge learning extracted")
            return None

        # Save the learning as a memory
        from models.memory import SOURCE_AGENT, Memory

        if not project_key:
            from config.project_key_resolver import resolve_project_key

            project_key = resolve_project_key()
            if project_key is None:
                logger.warning(
                    "[memory_extraction] post_merge write skipped: "
                    "resolve_project_key returned None (VALOR_PROJECT_KEY=%r)",
                    os.environ.get("VALOR_PROJECT_KEY"),
                )
                return None

        # Try to parse structured JSON response for metadata
        content_text = raw_text
        metadata: dict = {"category": "decision"}  # default for post-merge
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                observation = parsed.get("observation", "")
                if observation and len(observation) >= 20:
                    content_text = observation
                    metadata = {
                        "category": parsed.get("category", "decision"),
                        "tags": parsed.get("tags", []),
                        "file_paths": parsed.get("file_paths", []),
                    }
        except (json.JSONDecodeError, TypeError):
            # Non-JSON response -- use raw text with default metadata
            pass

        m = Memory.safe_save(
            agent_id="post-merge",
            project_key=project_key,
            content=content_text[:500],
            importance=7.0,
            source=SOURCE_AGENT,
            metadata=metadata,
        )

        if m:
            # Fire-and-forget async title generation
            # (writer path #3: post-merge learning extraction).
            try:
                from agent.private_tag import strip_private
                from tools.memory_search.title_generator import (
                    generate_title_async,
                )

                generate_title_async(m.memory_id, strip_private(content_text[:500]))
            except Exception as e:
                # D3 (issue #1817): title generation is best-effort — was
                # previously invisible on failure.
                logger.debug(
                    "[memory_extraction] generate_title_async failed for memory %s: %s",
                    getattr(m, "memory_id", "?"),
                    e,
                )

            logger.info(f"[memory_extraction] Post-merge learning saved: {content_text[:100]}")
            return {
                "content": content_text[:500],
                "memory_id": getattr(m, "memory_id", ""),
            }

        return None

    except Exception as e:
        logger.warning(f"[memory_extraction] Post-merge extraction failed (non-fatal): {e}")
        _record_extraction_error(type(e).__name__, "post-merge", project_key)
        return None


# Outcome judgment prompt for Haiku — classifies influence of injected thoughts
# Uses double-braces {{}} to escape literal braces from str.format()
OUTCOME_JUDGMENT_PROMPT = (
    "You are evaluating whether injected memory thoughts influenced an agent's response.\n"
    "For each thought below, classify its relationship to the response as:\n"
    '  "acted" — the response was meaningfully influenced by this memory\n'
    '  "used" — agent consumed the memory (read + reasoned) but it did not drive the response\n'
    '  "echoed" — keywords overlap but no causal link (coincidental)\n'
    '  "dismissed" — no relationship between memory and response\n'
    "\n"
    "Return a JSON array with one object per thought, each with:\n"
    '  "index": the 0-based index of the thought\n'
    '  "outcome": "acted", "used", "echoed", or "dismissed"\n'
    '  "reasoning": one sentence explaining your judgment\n'
    "\n"
    "Example:\n"
    '[{{"index": 0, "outcome": "acted",'
    ' "reasoning": "Response adopted the deployment strategy."}}]\n'
    "\n"
    "Thoughts:\n{thoughts}\n\n"
    "---\n\n"
    "Agent response:\n{response}"
)

# Truncation bounds for outcome judgment
_OUTCOME_RESPONSE_MAX_CHARS = 4000
_OUTCOME_THOUGHT_MAX_CHARS = 500
_OUTCOME_MAX_THOUGHTS = 5


async def _judge_outcomes_llm(
    injected_thoughts: list[tuple[str, str]],
    response_text: str,
) -> dict[str, dict] | None:
    """Use Haiku to judge whether injected thoughts influenced the response.

    Returns dict of {memory_key: {"outcome": str, "reasoning": str}} or None
    on failure. Callers should fall back to bigram overlap when this returns None.

    Maps "echoed" to "dismissed" for ObservationProtocol compatibility --
    echoed keywords without causal influence are noise, not signal.

    Event-loop safety (hotfix #1055): uses AsyncAnthropic with double-timeout
    inside ``async with`` for deterministic httpx cleanup.
    """
    try:
        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            return None

        # Apply truncation bounds
        capped_thoughts = injected_thoughts[:_OUTCOME_MAX_THOUGHTS]
        thoughts_text = "\n".join(
            f"[{i}] {content[:_OUTCOME_THOUGHT_MAX_CHARS]}"
            for i, (_key, content) in enumerate(capped_thoughts)
        )
        truncated_response = response_text[:_OUTCOME_RESPONSE_MAX_CHARS]

        prompt = OUTCOME_JUDGMENT_PROMPT.format(
            thoughts=thoughts_text,
            response=truncated_response,
        )

        # hotfix #1055: _llm_call centralizes AsyncAnthropic + async with + double-timeout.
        try:
            raw_text = await _llm_call(
                model=MODEL_FAST,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
        except TimeoutError:
            logger.warning(
                "[memory_extraction] Outcome judgment Anthropic call exceeded %.1fs "
                "hard timeout (non-fatal); falling back to bigram overlap",
                _EXTRACTION_HARD_TIMEOUT,
            )
            _record_extraction_error("TimeoutError", "outcome-judgment", None)
            return None

        judgments = json.loads(raw_text)

        if not isinstance(judgments, list):
            return None

        result: dict[str, dict] = {}
        for item in judgments:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(capped_thoughts):
                continue
            outcome = item.get("outcome", "dismissed")
            reasoning = item.get("reasoning", "")

            # "echoed" → "dismissed": keyword overlap without causal influence is noise, not signal
            if outcome == "echoed":
                outcome = "dismissed"
            # "used" is now first-class: consumed but did not drive the response (popoto v1.5.0)
            elif outcome not in ("acted", "used", "dismissed"):
                outcome = "dismissed"

            memory_key = capped_thoughts[idx][0]
            result[memory_key] = {"outcome": outcome, "reasoning": str(reasoning)[:200]}

        # Fill in any thoughts that weren't covered by the LLM response
        for i, (key, _content) in enumerate(capped_thoughts):
            if key not in result:
                result[key] = {"outcome": "dismissed", "reasoning": "not classified by judge"}

        return result

    except Exception as e:
        logger.debug(f"[memory_extraction] LLM outcome judgment failed, will use fallback: {e}")
        _record_extraction_error(type(e).__name__, "outcome-judgment", None)
        return None


def _extract_bigrams(text: str) -> set[tuple[str, ...]]:
    """Extract unigrams and bigrams from text for overlap detection.

    Filters out words shorter than 4 chars to reduce noise.
    """
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    unigrams = {(w,) for w in words}
    bigrams = {(words[i], words[i + 1]) for i in range(len(words) - 1)}
    return unigrams | bigrams


def _persist_outcome_metadata(
    memories: list,
    outcome_map: dict[str, str],
    reasoning_map: dict[str, str] | None = None,
) -> None:
    """Persist dismissal/acted/used outcome data in Memory metadata.

    Updates dismissal_count, last_outcome, and outcome_history in each
    memory's metadata dict. When dismissal_count reaches the threshold,
    decays importance. Resets dismissal_count on "acted" outcomes.

    Outcome semantics:
      "acted"    — memory drove the response; resets dismissal_count (positive signal)
      "used"     — consumed and reasoned about but did not drive the response;
                   leaves dismissal_count unchanged (popoto v1.5.0, neutral signal)
      "dismissed" — no relationship to the response; increments dismissal_count

    Args:
        memories: List of Memory instances to update.
        outcome_map: Dict of {memory_id: "acted"|"used"|"dismissed"}.
        reasoning_map: Optional dict of {memory_id: reasoning_string}
            from LLM judge. If absent, reasoning defaults to empty string.

    Runs after ObservationProtocol to avoid conflicting saves.
    All exceptions are caught per-record -- one failure does not block others.
    """
    from config.memory_defaults import (
        DISMISSAL_DECAY_THRESHOLD,
        DISMISSAL_IMPORTANCE_DECAY,
        MAX_OUTCOME_HISTORY,
        MIN_IMPORTANCE_FLOOR,
    )

    if reasoning_map is None:
        reasoning_map = {}

    for m in memories:
        mid = getattr(m, "memory_id", "")
        if mid not in outcome_map:
            continue
        outcome = outcome_map[mid]
        try:
            meta = getattr(m, "metadata", None) or {}
            if not isinstance(meta, dict):
                meta = {}

            # Append to outcome_history (capped at MAX_OUTCOME_HISTORY)
            history = meta.get("outcome_history", [])
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "outcome": outcome,
                    "reasoning": reasoning_map.get(mid, ""),
                    "ts": int(time.time()),
                }
            )
            # Keep only the most recent entries
            if len(history) > MAX_OUTCOME_HISTORY:
                history = history[-MAX_OUTCOME_HISTORY:]
            meta["outcome_history"] = history

            if outcome == "dismissed":
                # Ignored: increments dismissal_count, may trigger importance decay
                meta["dismissal_count"] = meta.get("dismissal_count", 0) + 1
                meta["last_outcome"] = "dismissed"
                # Check threshold for importance decay
                if meta["dismissal_count"] >= DISMISSAL_DECAY_THRESHOLD:
                    current_importance = getattr(m, "importance", 1.0)
                    new_importance = max(
                        current_importance * DISMISSAL_IMPORTANCE_DECAY,
                        MIN_IMPORTANCE_FLOOR,
                    )
                    m.importance = new_importance
                    meta["dismissal_count"] = 0  # reset after decay
                    logger.debug(
                        f"[memory_extraction] Decayed importance for {mid}: "
                        f"{current_importance} -> {new_importance}"
                    )
            elif outcome == "used":
                # Consumed but did not drive the response (popoto v1.5.0 neutral signal):
                # leave dismissal_count unchanged, record last_outcome for history
                meta["last_outcome"] = "used"
            elif outcome == "acted":
                # Drove the response: positive signal, reset dismissal_count
                meta["dismissal_count"] = 0  # reset on positive signal
                meta["last_outcome"] = "acted"

            m.metadata = meta
            m.save()
        except Exception as e:
            # D3 (issue #1817): was silently swallowed ("fail-silent per
            # record" is intentional — one bad record must not abort the
            # rest of the batch — but the failure is now observable).
            logger.debug(
                "[memory_extraction] outcome update failed for memory %s: %s",
                mid,
                e,
            )
            continue  # fail-silent per record


def compute_act_rate(outcome_history: list[dict]) -> float | None:
    """Compute the act rate from an outcome history list.

    Returns the ratio of "acted" outcomes to total outcomes, or None if
    the history is empty.
    """
    if not outcome_history:
        return None
    acted = sum(1 for entry in outcome_history if entry.get("outcome") == "acted")
    return acted / len(outcome_history)


async def detect_outcomes_async(
    injected_thoughts: list[tuple[str, str]],
    response_text: str,
) -> dict[str, str]:
    """Compare injected thoughts against response content.

    Uses LLM judgment (Haiku) as the primary signal. Falls back to bigram
    (1-2 word phrase) overlap when the LLM call fails or is unavailable.

    Feeds results into ObservationProtocol.on_context_used().

    Returns dict of {memory_key: "acted"|"used"|"dismissed"}.
    """
    if not injected_thoughts or not response_text:
        return {}

    try:
        outcome_map: dict[str, str] = {}
        reasoning_map: dict[str, str] = {}
        memory_keys: list[str] = []

        # Try LLM judgment first (hotfix #1055: now async-native via AsyncAnthropic)
        llm_result = await _judge_outcomes_llm(injected_thoughts, response_text)

        if llm_result is not None:
            # LLM judgment succeeded -- use it
            for memory_key, thought_content in injected_thoughts:
                judgment = llm_result.get(memory_key, {})
                outcome_map[memory_key] = judgment.get("outcome", "dismissed")
                reasoning_map[memory_key] = judgment.get("reasoning", "")
                memory_keys.append(memory_key)
            logger.debug("[memory_extraction] Used LLM judgment for outcome detection")
        else:
            # Fallback to bigram overlap
            response_bigrams = _extract_bigrams(response_text)
            for memory_key, thought_content in injected_thoughts:
                thought_bigrams = _extract_bigrams(thought_content)
                overlap = thought_bigrams & response_bigrams

                if overlap:
                    outcome_map[memory_key] = "acted"
                else:
                    outcome_map[memory_key] = "dismissed"

                memory_keys.append(memory_key)
            logger.debug("[memory_extraction] Used bigram fallback for outcome detection")

        # Feed into ObservationProtocol
        try:
            from popoto import ObservationProtocol

            from models.memory import Memory

            # Load memory instances by key
            memories = []
            for key in memory_keys:
                if key:
                    try:
                        results = Memory.query.filter(memory_id=key)
                        if results:
                            memories.append(results[0])
                    except Exception:  # noqa: S112 -- memory ops silent by design
                        continue

            if memories:
                # Build outcome map keyed by redis_key
                redis_outcome_map = {}
                for m in memories:
                    mid = getattr(m, "memory_id", "")
                    if mid in outcome_map:
                        redis_key = getattr(m.db_key, "redis_key", "")
                        if redis_key:
                            redis_outcome_map[redis_key] = outcome_map[mid]

                if redis_outcome_map:
                    ObservationProtocol.on_context_used(memories, redis_outcome_map)
                    acted = sum(1 for v in redis_outcome_map.values() if v == "acted")
                    used_count = sum(1 for v in redis_outcome_map.values() if v == "used")
                    dismissed = len(redis_outcome_map) - acted - used_count
                    logger.info(
                        f"[memory_extraction] Outcome detection: "
                        f"{acted} acted, {used_count} used, {dismissed} dismissed"
                    )

                # Persist dismissal/acted data in metadata (with reasoning)
                # Done after ObservationProtocol to avoid conflicting saves
                _persist_outcome_metadata(memories, outcome_map, reasoning_map)

        except Exception as e:
            logger.warning(f"[memory_extraction] ObservationProtocol failed (non-fatal): {e}")

        return outcome_map

    except Exception as e:
        logger.warning(f"[memory_extraction] Outcome detection failed (non-fatal): {e}")
        _record_extraction_error(type(e).__name__, "detect-outcomes", None)
        return {}


async def run_post_session_extraction(
    session_id: str,
    response_text: str,
    project_key: str | None = None,
    turn_count: int | None = None,
    is_conversational: bool = True,
) -> None:
    """Run full post-session extraction pipeline.

    1. Extract novel observations from response via Haiku
    2. Detect outcomes for injected thoughts
    3. Clean up session state

    ``turn_count`` / ``is_conversational`` carry the Fix 2 (#1822) trivial-session
    gate signals, captured at schedule time and threaded by value (see
    ``agent/session_executor.py``). Defaults make the gate a no-op.

    Called from BackgroundTask._run_work() after session completes.
    """
    try:
        # Extract observations
        await extract_observations_async(
            session_id,
            response_text,
            project_key,
            turn_count=turn_count,
            is_conversational=is_conversational,
        )

        # Detect outcomes for injected thoughts
        from agent.memory_hook import get_injected_thoughts

        injected = get_injected_thoughts(session_id)
        if injected:
            await detect_outcomes_async(injected, response_text)

    except Exception as e:
        logger.warning(f"[memory_extraction] Post-session extraction failed (non-fatal): {e}")
    finally:
        # Always clean up session state, even if extraction/detection fails
        try:
            from agent.memory_hook import clear_session

            clear_session(session_id)
        except Exception as e:
            logger.warning(f"[memory_extraction] Session cleanup failed: {e}")
