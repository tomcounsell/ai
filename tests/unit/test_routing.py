"""Unit tests for bridge.routing mention detection (config-only) and terminus detection.

These tests cover the three-state behavior of get_valor_usernames after the
removal of the hardcoded VALOR_USERNAMES constant:

1. project=None -> empty set (test ergonomics)
2. project with empty mention_triggers -> empty set
3. project with mention_triggers -> normalized set

They also cover classify_conversation_terminus fast-paths and failure modes.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from bridge import routing
from bridge.routing import (
    NeedsResponseDecision,
    TerminusDecision,
    classify_conversation_terminus,
    classify_needs_response,
    get_valor_usernames,
    is_message_for_others,
    is_message_for_valor,
    persona_to_session_type,
)
from config.enums import PersonaType, SessionType


@pytest.mark.parametrize(
    "persona, expected",
    [
        (PersonaType.TEAMMATE, SessionType.TEAMMATE),
        (PersonaType.ENGINEER, SessionType.ENG),
        (None, SessionType.ENG),
        (PersonaType.CUSTOMER_SERVICE, SessionType.ENG),
    ],
)
def test_persona_to_session_type(persona, expected):
    """TEAMMATE persona -> TEAMMATE session; ENGINEER/None/other -> ENG session.

    This mapping is the single source of truth shared by the live handler and
    the catchup/reconciler scanners; a regression here is exactly the bug that
    let teammate chats default to an eng PM<->Dev loop.
    """
    assert persona_to_session_type(persona) == expected


async def test_classify_needs_response_llm_failure_defaults_true(monkeypatch):
    """A run_typed failure → conservative True default (#1925).

    PydanticAI's schema validation replaces the old free-text length-bound
    parse guard; the remaining failure mode is the wrapper raising (provider
    error or exhausted schema-validation retries), which must still route to
    the conservative "respond" default.
    """
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(side_effect=RuntimeError("llm unavailable"))
    )
    assert await classify_needs_response("ship the deploy pipeline fix when ready") is True


async def test_classify_needs_response_normal_work_label(monkeypatch):
    """A needs_response=True decision routes to True."""
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(return_value=NeedsResponseDecision(needs_response=True))
    )
    assert await classify_needs_response("can you fix the bug in routing?") is True


async def test_classify_needs_response_normal_ignore_label(monkeypatch):
    """A needs_response=False decision routes to False."""
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(return_value=NeedsResponseDecision(needs_response=False))
    )
    assert await classify_needs_response("thanks, that is great news everyone") is False


def test_get_valor_usernames_none_returns_empty_set():
    assert get_valor_usernames(None) == set()


def test_get_valor_usernames_empty_triggers_returns_empty_set(monkeypatch):
    # Force DEFAULT_MENTIONS empty so the dict.get default also yields []
    monkeypatch.setattr(routing, "DEFAULT_MENTIONS", [])
    project = {"telegram": {"mention_triggers": []}}
    assert get_valor_usernames(project) == set()


def test_get_valor_usernames_returns_normalized_triggers():
    project = {"telegram": {"mention_triggers": ["@Foo", "BAR", "valorengels"]}}
    assert get_valor_usernames(project) == {"foo", "bar", "valorengels"}


def test_get_valor_usernames_falls_back_to_default_mentions(monkeypatch):
    monkeypatch.setattr(routing, "DEFAULT_MENTIONS", ["@valor", "valor"])
    # No mention_triggers key on the project -> should use DEFAULT_MENTIONS
    project: dict = {"telegram": {}}
    assert get_valor_usernames(project) == {"valor"}


def test_is_message_for_valor_true_with_loaded_project():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_valor("hey @valor please help", project) is True


def test_is_message_for_valor_false_when_directed_elsewhere():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_valor("hey @somebody help", project) is False


def test_is_message_for_others_true_when_only_other_mentions():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_others("@bob look at this", project) is True


def test_is_message_for_others_false_when_valor_mentioned():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_others("@valor and @bob", project) is False


def test_no_legacy_valor_usernames_constant():
    """The hardcoded VALOR_USERNAMES constant must be gone."""
    assert not hasattr(routing, "VALOR_USERNAMES")


# =============================================================================
# classify_conversation_terminus tests
# =============================================================================


@pytest.mark.asyncio
async def test_classify_terminus_bot_no_question_returns_silent():
    """Bot sender with a declarative message (no ?) → SILENT (primary loop break)."""
    result = await classify_conversation_terminus(
        text="That makes sense, thanks.",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_human_question_returns_respond():
    """Human sender with a standalone ? → RESPOND fast-path."""
    result = await classify_conversation_terminus(
        text="Can you explain this further?",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_url_with_query_param_not_respond():
    """URL query-string ? must NOT trigger the RESPOND fast-path for bot senders."""
    result = await classify_conversation_terminus(
        text="Check https://example.com?q=1 for details",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_acknowledgment_token_returns_silent():
    """Human-sent acknowledgment token → SILENT (fires after sender check)."""
    result = await classify_conversation_terminus(
        text="got it",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_acknowledgment_fires_after_bot_check():
    """'yes' from a bot → SILENT via bot fast-path (fires first, same outcome)."""
    result = await classify_conversation_terminus(
        text="yes",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_ollama_failure_defaults_to_respond(monkeypatch):
    """When the LLM wrapper fails, classifier returns RESPOND (conservative)."""
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(side_effect=RuntimeError("llm unavailable"))
    )

    result = await classify_conversation_terminus(
        text="Interesting thought about the deployment pipeline here.",
        thread_messages=["previous context"],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_empty_text_returns_respond():
    """Empty text → RESPOND (treat as continuation, never silently drop)."""
    result = await classify_conversation_terminus(
        text="",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_bot_react_collapses_to_silent(monkeypatch):
    """When LLM returns REACT but sender_is_bot=True, result must be SILENT."""
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(return_value=TerminusDecision(verdict="REACT"))
    )

    result = await classify_conversation_terminus(
        text="Sure, that all looks good.",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"  # REACT must collapse to SILENT for bots


# =============================================================================
# Question-aware Fast-Path 2 tests (issue #1090)
# =============================================================================


@pytest.mark.asyncio
async def test_classify_terminus_human_short_reply_to_valor_question_returns_respond(
    monkeypatch,
):
    """Human "Yes" replying to a Valor question must NOT be silenced (issue #1090).

    Fast-Path 2 should skip its ≤1-word check because the replied-to Valor
    message contained a standalone ``?``. The message falls through to the LLM
    call; with the LLM wrapper mocked to fail, the classifier's conservative
    default of RESPOND is returned.
    """
    # Force the LLM call to fail so we hit the conservative default.
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(side_effect=RuntimeError("llm unavailable"))
    )

    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["Should I select the Yudame workspace?"],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_human_short_reply_no_question_still_silent():
    """Regression: when the replied-to message is NOT a question, Fast-Path 2
    still fires and a 1-word human reply is SILENT (existing behavior)."""
    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["Here is the report you asked for."],
        sender_is_bot=False,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_bot_short_reply_to_valor_question_still_silent():
    """Bot-loop suppression: a bot "Yes" reply to a Valor question is still
    silenced via Fast-Path 1, because the reply text contains no ``?``. This
    test pins the Fast-Path 1 → Fast-Path 2 ordering — if reordered, it fails.
    """
    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["Should I do X?"],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_url_query_in_thread_not_treated_as_question():
    """URL query-string ``?`` in thread_messages must NOT count as a question.
    Fast-Path 2 should still fire and SILENT the short reply."""
    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["See https://example.com?q=1"],
        sender_is_bot=False,
    )
    assert result == "SILENT"


# =============================================================================
# Fast-Path 1.5: link/pointer tests (issue #1836)
# =============================================================================


@pytest.mark.asyncio
async def test_classify_terminus_human_bare_url_returns_respond():
    """Human sender + bare URL (no other text) → RESPOND via Fast-Path 1.5.

    Without this fast-path, a bare URL is word_count == 1 and would hit
    Fast-Path 2's ≤1-word SILENT branch before reaching the LLM.
    """
    result = await classify_conversation_terminus(
        text="https://github.com/BuilderIO/agent-native/tree/main/plans",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_human_look_here_url_returns_respond():
    """Motivating example from issue #1836: 'look here: <url>' → RESPOND.

    No '?', no imperative verb, not an ack token — previously fell through
    to the LLM classifier, which plausibly returned REACT and silently
    dropped the message.
    """
    result = await classify_conversation_terminus(
        text="look here: https://github.com/BuilderIO/agent-native/tree/main/plans",
        thread_messages=["Which approach do you want to use for this?"],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_human_multi_url_returns_respond():
    """Human sender + multiple bare URLs, no other content → RESPOND."""
    result = await classify_conversation_terminus(
        text="https://example.com/a https://example.com/b",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_bot_bare_url_still_silent():
    """Bot sender + bare URL must still be SILENT via Fast-Path 1 (unchanged).

    Fast-Path 1 fires before Fast-Path 1.5 is ever reached — the link/pointer
    fast-path is `not sender_is_bot`-gated and must not resurrect bot loops.
    """
    result = await classify_conversation_terminus(
        text="https://github.com/BuilderIO/agent-native/tree/main/plans",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_url_with_substantive_prose_falls_through_to_llm(
    monkeypatch,
):
    """URL followed by substantive prose (>3 words after URL removal) must NOT be
    force-RESPONDed by Fast-Path 1.5 — the ≤3-word remainder guard holds.

    The message has no ``?``, no imperative verb, is not an ack token, and its
    URL-stripped remainder is many words, so no fast-path fires and it reaches
    the LLM. We pin the LLM wrapper to SILENT via a mocked ``run_typed``. If
    Fast-Path 1.5 had incorrectly fired, the result would be RESPOND;
    asserting SILENT proves the guard held.
    """
    monkeypatch.setattr(
        routing, "run_typed", AsyncMock(return_value=TerminusDecision(verdict="SILENT"))
    )

    result = await classify_conversation_terminus(
        text=(
            "https://example.com/report and here are my detailed thoughts on why "
            "this approach fails badly in production"
        ),
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "SILENT"


# =============================================================================
# Fast-Path 0: imperative verb tests (issue #1318)
# =============================================================================


@pytest.mark.asyncio
async def test_classify_terminus_imperative_single_line_returns_respond():
    """Fast-Path 0: explicit imperative on its own line → RESPOND, no LLM call."""
    result = await classify_conversation_terminus(
        text="Continue to finish all stage of SDLC",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_imperative_multi_line_returns_respond():
    """Fast-Path 0: imperative on line 2 of a multi-line message → RESPOND.

    This is the May 7 motivating incident from issue #1318. The reply contained
    a status line followed by a blank line followed by the directive. Without
    multi-line awareness, the imperative would have been missed.
    """
    text = "I left a comment on PR 1316\n\nContinue to finish all stage of SDLC"
    result = await classify_conversation_terminus(
        text=text,
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_imperative_go_ahead_returns_respond():
    """Fast-Path 0: multi-word imperative 'go ahead' → RESPOND."""
    result = await classify_conversation_terminus(
        text="Go ahead and merge it",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_imperative_proceed_returns_respond():
    """Fast-Path 0: 'Proceed with the plan' → RESPOND."""
    result = await classify_conversation_terminus(
        text="Proceed with the plan",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_imperative_single_word_returns_respond():
    """Fast-Path 0: single-word imperative 'continue' → RESPOND.

    Without Fast-Path 0, this would hit Fast-Path 2 (≤1 word) and return SILENT.
    """
    result = await classify_conversation_terminus(
        text="continue",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_ok_great_does_not_respond_via_fast_path_0():
    """Regression: Fast-Path 0 must NOT over-fire on conversation closers.

    'ok great' contains no imperative verb, so Fast-Path 0 must not match. The
    actual classification of 'ok great' (REACT vs SILENT) is decided by the
    LLM and is not pinned here — what we guard against is Fast-Path 0
    incorrectly returning RESPOND.
    """
    assert routing._IMPERATIVE_LINE_RE.search("ok great") is None


@pytest.mark.asyncio
async def test_classify_terminus_thanks_still_silent():
    """Regression: 'thanks' must still return SILENT."""
    result = await classify_conversation_terminus(
        text="thanks",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_bot_imperative_still_silent():
    """Fast-Path 0 is human-only: a bot saying 'Continue with deployment'
    must still hit Fast-Path 1 and return SILENT (loop break preserved)."""
    result = await classify_conversation_terminus(
        text="Continue with deployment",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


def test_imperative_line_re_does_not_match_mid_sentence():
    """Mid-sentence imperatives must NOT trigger Fast-Path 0.

    'I would just continue this automatically' contains 'continue' but not as
    the leading word of any line, so the regex must not match. The message
    falls through to the LLM for full classification.
    """
    assert routing._IMPERATIVE_LINE_RE.search("I would just continue this automatically") is None


# ============================================================================
# Config path resolution — launchd Desktop-hang guard (June 2026 outage)
# ============================================================================


class TestResolveConfigPathLaunchdGuard:
    """Under launchd, a ~/Desktop PROJECTS_CONFIG_PATH must never be opened.

    macOS TCC and iCloud file eviction make open()/stat() on ~/Desktop block
    indefinitely from a launchd agent, silently wedging the bridge/worker at
    import. The VALOR_LAUNCHD guard is authoritative even when the Desktop
    path was set explicitly via PROJECTS_CONFIG_PATH.
    """

    def test_desktop_env_path_overridden_under_launchd(self, monkeypatch, tmp_path):
        local = routing.Path(routing.__file__).parent.parent / "config" / "projects.json"
        if not local.exists():
            pytest.skip("local config/projects.json not present in this checkout")

        desktop = routing.Path.home() / "Desktop" / "Valor" / "projects.json"
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(desktop))

        resolved = routing._resolve_config_path()
        assert resolved == local, "launchd must avoid the Desktop path, using the local copy"

    def test_desktop_env_path_honored_without_launchd(self, monkeypatch):
        """Outside launchd, an explicit Desktop path is honored as-is."""
        desktop = routing.Path.home() / "Desktop" / "Valor" / "projects.json"
        monkeypatch.delenv("VALOR_LAUNCHD", raising=False)
        monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(desktop))

        assert routing._resolve_config_path() == desktop

    def test_non_desktop_env_path_honored_under_launchd(self, monkeypatch, tmp_path):
        """A non-Desktop explicit path is honored even under launchd."""
        custom = tmp_path / "projects.json"
        custom.write_text("{}")
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        monkeypatch.setenv("PROJECTS_CONFIG_PATH", str(custom))

        assert routing._resolve_config_path() == custom

    def test_is_under_desktop(self):
        home = routing.Path.home()
        assert routing._is_under_desktop(home / "Desktop" / "Valor" / "projects.json")
        assert routing._is_under_desktop(home / "Desktop" / "x.json")
        assert not routing._is_under_desktop(home / "src" / "ai" / "config" / "projects.json")
        assert not routing._is_under_desktop(routing.Path("/etc/projects.json"))


class TestGuardedConfigRead:
    """C4 — guarded config read: a partial/corrupt projects.json must never raise.

    A launchd KeepAlive respawn can race a mid-iCloud-write projects.json,
    producing a truncated/corrupt JSON file. The guarded loader catches that,
    logs, and falls back to the last-known-good sidecar instead of
    propagating the exception — which would otherwise crash-loop the bridge
    at import time.
    """

    def test_successful_read_caches_last_known_good(self, monkeypatch, tmp_path):
        config_path = tmp_path / "projects.json"
        config_path.write_text(json.dumps({"projects": {"a": 1}, "defaults": {}}))
        lkg_path = tmp_path / "lkg.json"
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)

        result = routing._guarded_json_load(config_path)

        assert result == {"projects": {"a": 1}, "defaults": {}}
        assert lkg_path.exists()
        assert json.loads(lkg_path.read_text()) == result
        assert not lkg_path.with_suffix(".tmp").exists()

    def test_corrupt_read_falls_back_to_last_known_good(self, monkeypatch, tmp_path, caplog):
        config_path = tmp_path / "projects.json"
        config_path.write_text('{"projects": {"a": 1}, "def')  # truncated mid-write
        lkg_path = tmp_path / "lkg.json"
        lkg_path.write_text(json.dumps({"projects": {"good": True}, "defaults": {}}))
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)

        with caplog.at_level(logging.ERROR):
            result = routing._guarded_json_load(config_path)

        assert result == {"projects": {"good": True}, "defaults": {}}
        assert any("Failed to parse" in r.message for r in caplog.records)

    def test_corrupt_read_without_last_known_good_returns_empty_defaults(
        self, monkeypatch, tmp_path, caplog
    ):
        config_path = tmp_path / "projects.json"
        config_path.write_text("not json at all")
        lkg_path = tmp_path / "does_not_exist.json"
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)

        with caplog.at_level(logging.ERROR):
            result = routing._guarded_json_load(config_path)

        assert result == {"projects": {}, "defaults": {}}
        assert any("No last-known-good config available" in r.message for r in caplog.records)

    def test_guarded_json_load_never_raises_on_malformed_input(self, monkeypatch, tmp_path):
        """Import-time invariant: a JSONDecodeError from a partial config must
        never propagate — the loader always returns a dict."""
        config_path = tmp_path / "projects.json"
        config_path.write_bytes(b"\x00\x01garbage-not-json")
        lkg_path = tmp_path / "lkg.json"
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)

        result = routing._guarded_json_load(config_path)  # must not raise

        assert isinstance(result, dict)

    def test_load_config_falls_back_to_last_known_good_on_corrupt_file(self, monkeypatch, tmp_path):
        config_path = tmp_path / "projects.json"
        config_path.write_text('{"defaults": {"working_direct')  # partial write
        lkg_path = tmp_path / "lkg.json"
        lkg_config = {"projects": {}, "defaults": {"working_directory": str(tmp_path)}}
        lkg_path.write_text(json.dumps(lkg_config))
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)
        monkeypatch.setattr(routing, "_resolve_config_path", lambda: config_path)
        monkeypatch.setattr(routing, "ACTIVE_PROJECTS", [])

        result = routing.load_config()  # must not raise

        assert result == lkg_config

    def test_read_last_known_good_returns_none_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", tmp_path / "missing.json")
        assert routing._read_last_known_good_config() is None

    def test_write_last_known_good_is_atomic_and_creates_parent_dir(self, monkeypatch, tmp_path):
        lkg_path = tmp_path / "nested" / "lkg.json"
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)

        routing._write_last_known_good_config({"projects": {}, "defaults": {}})

        assert lkg_path.exists()
        assert not lkg_path.with_suffix(".tmp").exists()  # tmp file renamed away, not left behind

    def test_write_last_known_good_swallows_oserror(self, monkeypatch, tmp_path):
        """A write failure (e.g. read-only filesystem) must not raise — this
        sidecar is a best-effort cache, not a critical write."""
        lkg_path = tmp_path / "lkg.json"
        monkeypatch.setattr(routing, "_LAST_KNOWN_GOOD_PATH", lkg_path)

        def _boom(*args, **kwargs):
            raise OSError("simulated read-only filesystem")

        monkeypatch.setattr(routing.Path, "mkdir", _boom)

        routing._write_last_known_good_config({"projects": {}})  # must not raise
