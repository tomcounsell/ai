"""Integration tests for the <private>...</private> ingestion exclusion.

These tests exercise the same transformations the Telegram bridge performs:
- Compute safe_text = strip_private(text) once.
- Pass safe_text to the persistence sites (Memory.safe_save, TelegramMessage,
  AgentSession.message_text via clean_message).
- Pre-strip reply_chain_context in BOTH the completed-resume path
  (bridge/telegram_bridge.py:1416) and the fresh-message prehydration path
  (bridge/telegram_bridge.py:1922).
- Keep the original `text`/`clean_text` for the live-agent SDK prompt input.

The tests are kept at integration level (not full bridge spawn) because the
bridge's decision is purely a string-flow composition; spinning up Telethon
adds noise without added signal. The actual call sites in
``bridge/telegram_bridge.py`` are also covered by the verification grep
checks in the plan's Verification table.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from agent.private_tag import strip_private
from bridge.response import clean_message
from bridge.telegram_bridge import _build_completed_resume_text

SECRET = "sk-int-test-secret-redacted"
OLDSECRET = "OLDSECRETFROMLEGACYCHAIN"
PRIVATE_WRAPPED = f"<private>{SECRET}</private>"

USER_TEXT = f"Refactor the auth handler. The current key is {PRIVATE_WRAPPED}. Should we rotate?"


def _bridge_compute_safe_pair(text: str) -> tuple[str, str, str, str]:
    """Mirror the bridge handler's text computation:
    text → safe_text, clean_text (live), safe_clean_text (persisted).
    """
    safe_text = strip_private(text)
    clean_text = clean_message(text) or text
    safe_clean_text = clean_message(safe_text) or safe_text
    return text, safe_text, clean_text, safe_clean_text


class TestInboundPersistence:
    """Path A from the plan's Data Flow: Telegram message ingestion.

    Persistent writes (Memory.safe_save, TelegramMessage.store_message,
    bridge log, AgentSession.message_text) all consume safe_text /
    safe_clean_text — wrapped content must not appear.
    """

    def test_safe_text_excludes_wrapped_region(self):
        text, safe_text, _clean_text, _safe_clean_text = _bridge_compute_safe_pair(USER_TEXT)
        assert PRIVATE_WRAPPED in text  # raw text retains tags
        assert SECRET not in safe_text
        assert "<private>" not in safe_text
        assert "</private>" not in safe_text
        # Real (non-private) content is preserved.
        assert "Refactor the auth handler" in safe_text
        assert "rotate" in safe_text

    def test_safe_clean_text_excludes_wrapped_region(self):
        _text, _safe_text, _clean_text, safe_clean_text = _bridge_compute_safe_pair(USER_TEXT)
        assert SECRET not in safe_clean_text
        assert "<private>" not in safe_clean_text

    def test_log_truncation_uses_safe_text(self):
        """The bridge logs `safe_text[:50]`; ensure that prefix never reveals
        the secret even when it's near the start of the message.
        """
        text = f"{PRIVATE_WRAPPED} prefix wraps the start of the message"
        _text, safe_text, _ct, _sct = _bridge_compute_safe_pair(text)
        log_prefix = safe_text[:50]
        assert SECRET not in log_prefix

    def test_memory_save_call_arg_excludes_wrapped(self):
        """The bridge calls Memory.safe_save(content=safe_text[:500], ...).
        Confirm safe_text[:500] never carries the wrapped content even when
        the secret would have appeared inside the first 500 chars.
        """
        text = f"prelude {PRIVATE_WRAPPED} {'x' * 600}"
        _text, safe_text, _ct, _sct = _bridge_compute_safe_pair(text)
        # The bridge does Memory.safe_save(content=safe_text[:500], ...)
        memory_payload = safe_text[:500]
        assert SECRET not in memory_payload

    def test_telegram_history_store_uses_safe_text(self):
        """TelegramMessage.content is written with `safe_text` (no truncation).
        Confirm full safe_text is also clean.
        """
        _text, safe_text, _ct, _sct = _bridge_compute_safe_pair(USER_TEXT)
        # Simulate TelegramMessage.content = safe_text
        telegram_message_content = safe_text
        assert SECRET not in telegram_message_content


class TestLiveAgentVisibility:
    """OQ1 resolution: live agent prompt construction uses the ORIGINAL `text`
    so the agent can reason about wrapped content this turn.

    Verifies we did not over-strip — without this, the user's tag would
    silently break the agent's ability to answer in-turn questions about
    wrapped content (a UX regression).
    """

    def test_clean_text_retains_wrapped_for_live_path(self):
        text, _safe_text, clean_text, _safe_clean_text = _bridge_compute_safe_pair(USER_TEXT)
        assert PRIVATE_WRAPPED in text
        # clean_message is built from raw text (live SDK prompt input).
        # It MUST retain the wrapped region so the agent sees the tags this turn.
        assert SECRET in clean_text
        assert "<private>" in clean_text


class TestReplyChainLeakClosure:
    """B1 closure: a legacy reply-chain that contains <private> markers
    (e.g. persisted before this PR shipped) must be stripped before splice
    into AgentSession.message_text.

    Plan references:
    - completed-resume path (bridge/telegram_bridge.py:1416 + 1462-1466 →
      dispatch_telegram_session at line ~1465)
    - fresh-message prehydration (bridge/telegram_bridge.py:1922 + 1947-1949)
    """

    def test_completed_resume_reply_chain_stripped_before_augmented_text(self):
        """Mirror the completed-resume code path:
            reply_chain_context = format_reply_chain(chain)
            reply_chain_context = strip_private(reply_chain_context)  # sdlc-1179
            augmented_text = _build_completed_resume_text(
                completed, safe_clean_text, reply_chain_context=reply_chain_context
            )
        Both inputs must be pre-stripped.
        """

        # Simulate format_reply_chain output containing a leftover <private>
        # marker from a legacy persisted message.
        legacy_chain = (
            "REPLY THREAD CONTEXT (oldest to newest):\n"
            f"  • Tom (3m ago): the api key is <private>{OLDSECRET}</private>\n"
            "  • Valor (2m ago): noted, will rotate\n"
        )
        # Bridge applies strip_private to the chain immediately after format.
        safe_chain = strip_private(legacy_chain)
        assert OLDSECRET not in safe_chain
        assert "<private>" not in safe_chain

        # safe_clean_text feeds into augmented_text (also pre-stripped).
        _t, _st, _ct, safe_clean_text = _bridge_compute_safe_pair(USER_TEXT)

        class _FakeCompleted:
            context_summary = "discussion of auth rotation"

        augmented = _build_completed_resume_text(
            _FakeCompleted(),
            safe_clean_text,
            reply_chain_context=safe_chain,
        )
        # Neither secret leaks into AgentSession.message_text.
        assert OLDSECRET not in augmented
        assert SECRET not in augmented
        assert "<private>" not in augmented
        # The non-private context is preserved.
        assert "auth rotation" in augmented
        assert "rotate" in augmented

    def test_fresh_message_prehydration_reply_chain_stripped(self):
        """Mirror the fresh-message prehydration code path:
            reply_chain_context = format_reply_chain(chain)
            reply_chain_context = strip_private(reply_chain_context)  # sdlc-1179
            enqueued_message_text = (
                f"{reply_chain_context}\n\nCURRENT MESSAGE:\n{enqueued_message_text}"
            )
        The splice components must be pre-stripped.
        """
        legacy_chain = (
            "REPLY THREAD CONTEXT (oldest to newest):\n"
            f"  • Tom (5m ago): we use <private>{OLDSECRET}</private> for staging\n"
        )
        safe_chain = strip_private(legacy_chain)
        assert OLDSECRET not in safe_chain

        _t, _st, _ct, safe_clean_text = _bridge_compute_safe_pair(USER_TEXT)
        # safe_clean_text seeds enqueued_message_text in the fresh path.
        enqueued_message_text = safe_clean_text

        # Fresh-path prehydration splice (line ~1948-1949).
        spliced = f"{safe_chain}\n\nCURRENT MESSAGE:\n{enqueued_message_text}"

        # Neither secret leaks into the persisted enqueued_message_text.
        assert OLDSECRET not in spliced
        assert SECRET not in spliced
        assert "<private>" not in spliced


class TestIngestEndToEnd:
    """End-to-end through hook_utils.memory_bridge.ingest with patched Memory.

    Confirms the strip happens at the ingest layer, not just at call sites.
    """

    def test_ingest_strips_private_before_safe_save(self):
        # Load the live ingest function (no monkey-patching of strip_private).
        spec = importlib.util.spec_from_file_location(
            "memory_bridge_module",
            str(
                Path(__file__).resolve().parent.parent.parent
                / ".claude/hooks/hook_utils/memory_bridge.py"
            ),
        )
        assert spec is not None and spec.loader is not None
        mb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mb)

        from unittest.mock import MagicMock, patch

        captured = {}

        def fake_safe_save(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom
        mock_memory_cls.safe_save.side_effect = fake_safe_save

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("models.memory.SOURCE_HUMAN", "human"),
            patch.object(mb, "_get_project_key", return_value="test"),
        ):
            ok = mb.ingest(USER_TEXT)

        assert ok is True
        saved = captured["content"]
        assert SECRET not in saved
        assert "<private>" not in saved
        assert "Refactor the auth handler" in saved


class TestStripPrivateContractInvariants:
    """Sanity checks that downstream code can rely on."""

    def test_idempotent_under_repeated_application(self):
        for text in (
            USER_TEXT,
            f"{PRIVATE_WRAPPED}",
            "no tags",
            "  multi space  no tag  ",
            "<private>line1\nline2</private> after",
        ):
            once = strip_private(text)
            twice = strip_private(once)
            assert once == twice, f"Not idempotent for: {text!r}"

    def test_secret_pattern_never_in_safe_text_when_wrapped(self):
        """For a secret with a recognizable pattern, the safe variant must
        never match the pattern even after multiple ingestion-style
        transformations."""
        text = f"please use {PRIVATE_WRAPPED} for staging"
        _t, safe_text, _ct, _sct = _bridge_compute_safe_pair(text)
        # Accept any pattern matching `sk-int-test-secret-...` -> none should
        # remain.
        assert not re.search(r"sk-int-test-secret", safe_text)
