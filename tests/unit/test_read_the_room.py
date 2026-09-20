"""Unit tests for ``bridge/read_the_room.py``.

Covers:

* All verdict branches (send / trim-long / trim-short / trim-missing-text /
  suppress-with-anchor / suppress-fallthrough / failure).
* Short-circuit return paths (empty draft, empty snapshot, no chat_id,
  ``len < SHORT_OUTPUT_THRESHOLD`` bypass, SDLC bypass) -- RTR runs
  unconditionally, with no env-var gate.
* Snapshot construction (K cap, time-window filter, mixed sender attribution).
* Fail-open exception handling: ``LLMCallError`` from the leg (``timeout``,
  ``slot_timeout``, ``transport``, ``validation``) and the last-resort catch.
* The ``run_typed`` call shape (#3410): ``READ_THE_ROOM`` task, the session's
  ``project_key``, the system prompt, and the hotfix #1055 kwargs
  (``sdk_timeout``, ``slot_timeout``, ``max_retries=0``, ``hard_timeout=None``).
* The reaction-payload alignment between :meth:`TelegramRelayOutputHandler.react`
  and the RTR suppress branch (Implementation Note AD1).
* The fall-through audit signal when ``reply_to_msg_id is None`` (Implementation
  Note SI1, F4).
* Suppress-reaction queue-key alignment when ``session.session_id != chat_id``
  (Implementation Note F7).

The leg is faked at the module's ``run_typed`` import seam with the shared
:class:`tests.helpers.llm_fakes.FakeRunTyped`; no client is constructed.
"""

from __future__ import annotations

import asyncio
import time
import types
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from agent.llm.tasks import Backend, TaskKind
from bridge import read_the_room as rtr_module
from bridge.read_the_room import (
    DEFAULT_K,
    DEFAULT_MAX_AGE_SECONDS,
    READ_THE_ROOM,
    READ_THE_ROOM_SYSTEM_PROMPT,
    RTR_SDK_TIMEOUT,
    RTR_STALE_TRIGGER_SECONDS,
    RTR_SUPPRESS_EMOJI,
    TRIM_TOO_SHORT_THRESHOLD,
    RoomVerdict,
    _format_snapshot_for_prompt,
    _humanize_age,
    is_group_chat,
    read_the_room,
)
from tests.helpers.llm_fakes import FakeRunTyped, failing

# === Fixtures ===================================================================

# Telegram assigns negative ids to groups/supergroups/channels. RTR only ever
# room-reads group chats (#2199), so the shared test chat_id is a supergroup id.
GROUP_CHAT_ID = "-1001234567890"


class FakeSession:
    """Minimal stand-in for ``AgentSession`` used in unit tests."""

    def __init__(
        self,
        *,
        session_id: str = "sess-test",
        is_sdlc: bool = False,
        telegram_message_id: int | None = None,
        project_key: str | None = None,
    ):
        self.session_id = session_id
        self.is_sdlc = is_sdlc
        self.telegram_message_id = telegram_message_id
        self.project_key = project_key
        self.session_events: list[dict] | None = None
        self._save_calls = 0

    def save(self):
        self._save_calls += 1


def _long_draft(extra: str = "") -> str:
    """Draft text guaranteed to exceed ``SHORT_OUTPUT_THRESHOLD`` (200 chars)."""
    return ("Logged 4 entries to the project knowledge base. " * 6) + extra


def _verdict(action: str, *, revised_text=None, reason: str = "") -> RoomVerdict:
    return RoomVerdict(action=action, revised_text=revised_text, reason=reason)


def _patch_run_typed(monkeypatch, verdict: RoomVerdict | None = None, *, fake=None) -> FakeRunTyped:
    """Patch ``bridge.read_the_room.run_typed`` with a :class:`FakeRunTyped`.

    ``verdict`` is what the fake answers; pass ``fake`` instead for an error
    or a per-call responder. Returns the fake so tests assert on its calls.
    """
    fake = fake if fake is not None else FakeRunTyped(result=verdict)
    monkeypatch.setattr(rtr_module, "run_typed", fake)
    return fake


def _patch_snapshot(monkeypatch, snapshot):
    async def fake_fetch(chat_id, *, k, max_age_seconds):
        return list(snapshot)

    monkeypatch.setattr(rtr_module, "_fetch_snapshot", fake_fetch)


def _patch_trigger_age(monkeypatch, age_seconds):
    """Patch the async trigger-age lookup so tests never touch Redis."""

    async def fake_age(chat_id, message_id):
        return age_seconds

    monkeypatch.setattr(rtr_module, "_fetch_trigger_age", fake_age)


# === Group / DM gate (#2199) ====================================================


@pytest.mark.parametrize(
    "chat_id, expected",
    [
        ("-1001234567890", True),
        (-1001234567890, True),
        ("-42", True),
        ("12345", False),
        (12345, False),
        ("0", False),
        (None, False),
        ("not-an-int", False),
    ],
)
def testis_group_chat(chat_id, expected):
    assert is_group_chat(chat_id) is expected


def test_dm_excluded_returns_send(monkeypatch):
    """A positive (DM) chat_id short-circuits to send without a Haiku call."""
    fake = _patch_run_typed(monkeypatch, _verdict("suppress"))

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "dm_excluded"
    assert fake.call_count == 0


# === Deterministic staleness (#2199) ============================================


def test_stale_trigger_deterministic_suppress(monkeypatch):
    """A trigger older than the threshold suppresses without calling Haiku."""
    fake = _patch_run_typed(monkeypatch, _verdict("send"))
    _patch_trigger_age(monkeypatch, RTR_STALE_TRIGGER_SECONDS + 60)

    session = FakeSession(telegram_message_id=777)
    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session))
    assert verdict.action == "suppress"
    assert verdict.reason == "stale_trigger"
    assert fake.call_count == 0
    assert session.session_events and session.session_events[0]["type"] == "rtr.suppressed"
    assert session.session_events[0]["reason"] == "stale_trigger"


def test_fresh_trigger_threads_age_into_prompt(monkeypatch):
    """A fresh trigger does not deterministically suppress; its age is passed
    to the Haiku prompt as a temporal signal."""
    fake = _patch_run_typed(monkeypatch, _verdict("send", reason="clean"))
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    _patch_trigger_age(monkeypatch, 42)

    session = FakeSession(telegram_message_id=777)
    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session))
    assert verdict.action == "send"
    assert fake.call_count == 1
    payload = fake.last.prompt
    assert "## Trigger age" in payload
    assert "ago" in payload


def test_absent_trigger_age_omits_age_block(monkeypatch):
    """With no trigger id (age None) the prompt carries no trigger-age block."""
    fake = _patch_run_typed(monkeypatch, _verdict("send", reason="clean"))
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert fake.call_count == 1
    payload = fake.last.prompt
    assert "## Trigger age" not in payload


@pytest.mark.parametrize(
    "age_seconds, expected",
    [
        (5, "5 seconds ago"),
        (89, "89 seconds ago"),
        (120, "2 minutes ago"),
        (3600, "60 minutes ago"),
        (5340, "89 minutes ago"),
        (7200, "2 hours ago"),
        (172800, "2 days ago"),
    ],
)
def test_humanize_age(age_seconds, expected):
    assert _humanize_age(age_seconds) == expected


# === Short-circuit tests ========================================================


def test_empty_draft_returns_send(monkeypatch):
    """Runs with no RTR-related env var set anywhere -- RTR is unconditional."""
    verdict = asyncio.run(read_the_room("", GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_draft"


def test_empty_draft_none_returns_send(monkeypatch):
    verdict = asyncio.run(read_the_room(None, GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_draft"


def test_whitespace_only_draft_returns_send(monkeypatch):
    verdict = asyncio.run(read_the_room("   \n  \t", GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_draft"


def test_no_chat_id_returns_send(monkeypatch):
    verdict = asyncio.run(read_the_room(_long_draft(), None, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "no_chat_id"


def test_short_output_short_circuits(monkeypatch):
    """Below ``SHORT_OUTPUT_THRESHOLD`` we should never call Haiku."""
    fake = _patch_run_typed(monkeypatch, _verdict("send"))

    short_draft = "Tiny ack."
    verdict = asyncio.run(read_the_room(short_draft, GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "short_output"
    assert fake.call_count == 0


def test_sdlc_session_short_circuits_with_event(monkeypatch):
    """SDLC sessions skip RTR and emit a ``rtr.bypassed`` event.

    Uses ``is_sdlc=True`` -- the real predicate production reads
    (``getattr(session, "is_sdlc", False)``). This test must fail against
    the unrepaired ``read_the_room.py`` (which read a different, phantom
    session field instead); see the red-state proof pasted into the PR.
    """
    fake = _patch_run_typed(monkeypatch, _verdict("send"))

    session = FakeSession(is_sdlc=True)
    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session))
    assert verdict.action == "send"
    assert verdict.reason == "sdlc_session"
    assert fake.call_count == 0

    assert session.session_events and session.session_events[0]["type"] == "rtr.bypassed"
    assert session.session_events[0]["reason"] == "sdlc_session"


def test_short_sdlc_reply_takes_short_output_path_not_bypass(monkeypatch):
    """The ``len < SHORT_OUTPUT_THRESHOLD`` check runs BEFORE the SDLC bypass,
    so a short SDLC ``delivery_text`` returns ``short_output`` and never
    reaches (or emits) the ``rtr.bypassed`` branch. This is not a regression
    -- both branches return ``send`` -- but it means ``rtr.bypassed`` counts
    undercount real SDLC bypasses whenever the composed message is short."""
    fake = _patch_run_typed(monkeypatch, _verdict("send"))

    session = FakeSession(is_sdlc=True)
    verdict = asyncio.run(read_the_room("Tiny ack.", GROUP_CHAT_ID, session))
    assert verdict.action == "send"
    assert verdict.reason == "short_output"
    assert fake.call_count == 0
    assert not session.session_events


def test_no_session_does_not_trigger_bypass_or_raise(monkeypatch):
    """``session=None`` must not fire the SDLC bypass and must not raise --
    ``_append_event`` no-ops on a ``None`` session."""
    fake = _patch_run_typed(monkeypatch, _verdict("send"))
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session=None))
    assert verdict.action == "send"
    assert fake.call_count == 1


def test_is_sdlc_attribute_exists_on_real_agent_session():
    """Canary against the exact defect this plan repairs: a future rename of
    ``AgentSession.is_sdlc`` must fail loud here rather than silently
    degrading the RTR/drafter bypass back to "no bypass" via ``getattr``'s
    default. Checked against the real imported model, not a fake."""
    from models.agent_session import AgentSession

    assert hasattr(AgentSession, "is_sdlc")


def test_empty_snapshot_returns_send(monkeypatch):
    fake = _patch_run_typed(monkeypatch, _verdict("send"))
    _patch_snapshot(monkeypatch, [])

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_snapshot"
    assert fake.call_count == 0


# === Verdict-pass-through tests =================================================


def test_send_verdict(monkeypatch):
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    fake = _patch_run_typed(monkeypatch, _verdict("send", reason="clean"))

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "clean"
    assert fake.call_count == 1


def test_runs_unconditionally_with_no_rtr_env_var_set(monkeypatch):
    """Core acceptance-criterion claim: RTR reaches the Haiku pass with no
    RTR-related env var set anywhere in the process -- there is no such var
    left to set; every other test in this module makes the same claim by
    construction."""
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    fake = _patch_run_typed(monkeypatch, _verdict("send", reason="clean"))

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert fake.call_count == 1


def test_trim_long_verdict_preserves_revised_text(monkeypatch):
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "moved on"}])
    revised = "Quick pointer: look at the dashboard for details."
    _patch_run_typed(
        monkeypatch, _verdict("trim", revised_text=revised, reason="partial_redundant")
    )

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "trim"
    assert verdict.revised_text == revised


def test_trim_short_verdict_preserves_text(monkeypatch):
    """RTR returns the trim verdict verbatim; the *handler* is responsible
    for coercing too-short trims to suppress (see the test in
    tests/unit/output_handler/test_output_handler_filters.py).
    """
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "moved on"}])
    _patch_run_typed(monkeypatch, _verdict("trim", revised_text="ok", reason="redundant"))

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "trim"
    assert verdict.revised_text == "ok"
    assert len(verdict.revised_text) < TRIM_TOO_SHORT_THRESHOLD


def test_trim_with_no_revised_text_falls_back_to_send(monkeypatch):
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "moved on"}])
    _patch_run_typed(monkeypatch, _verdict("trim", revised_text=None, reason="redundant"))

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "trim_missing_revised_text"


def test_suppress_verdict(monkeypatch):
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "answered already"}])
    _patch_run_typed(monkeypatch, _verdict("suppress", reason="duplicate_answer"))

    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession()))
    assert verdict.action == "suppress"
    assert verdict.reason == "duplicate_answer"


# === Failure / fail-open tests ==================================================


@pytest.mark.parametrize("reason", ["timeout", "slot_timeout", "transport", "validation"])
def test_llm_call_error_returns_send_and_logs_event(monkeypatch, reason, caplog):
    """Every ``LLMCallError`` reason from the leg (the SDK-level 3 s timer, the
    semaphore wait, a transport refusal, a schema-validation exhaustion) is
    the same fail-open outcome: ``send`` / ``rtr_error``, an ``rtr.failed``
    event naming the reason, and a warning naming the site."""
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    _patch_run_typed(monkeypatch, fake=failing(reason))

    session = FakeSession()
    with caplog.at_level("WARNING", logger="bridge.read_the_room"):
        verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_error"

    assert session.session_events and session.session_events[0]["type"] == "rtr.failed"
    assert session.session_events[0]["reason"] == "rtr_error"
    assert session.session_events[0]["error"] == f"LLMCallError:{reason}"
    assert any("read_the_room.verdict" in r.getMessage() for r in caplog.records)


def test_unexpected_exception_caught_last_resort(monkeypatch):
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    _patch_run_typed(monkeypatch, fake=FakeRunTyped(error=RuntimeError("boom")))

    session = FakeSession()
    verdict = asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_error"
    assert session.session_events[0]["error"] == "RuntimeError"


# === run_typed call shape (#3410) ===============================================


def test_declaration_is_a_thinking_task_on_anthropic():
    assert READ_THE_ROOM.site == "read_the_room.verdict"
    assert READ_THE_ROOM.kind is TaskKind.THINKING
    assert READ_THE_ROOM.backend is Backend.ANTHROPIC


def test_run_typed_call_shape_carries_the_hotfix_1055_kwargs(monkeypatch):
    """The only timer around the live request is the leg's SDK-level client
    timeout: ``hard_timeout=None`` keeps the wrapper's coroutine-level cap off
    this path, ``max_retries=0`` stops the SDK multiplying the 3 s bound, and
    the slot wait is bounded separately by ``slot_timeout``."""
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    fake = _patch_run_typed(monkeypatch, _verdict("send", reason="clean"))

    verdict = asyncio.run(
        read_the_room(_long_draft(), GROUP_CHAT_ID, FakeSession(project_key="valor"))
    )
    assert verdict.action == "send"

    call = fake.last
    assert call.output_type is RoomVerdict
    assert call.task is READ_THE_ROOM
    assert call.project_key == "valor"
    assert call.kwargs["system"] == READ_THE_ROOM_SYSTEM_PROMPT
    assert call.kwargs["sdk_timeout"] == RTR_SDK_TIMEOUT == 3.0
    assert call.kwargs["slot_timeout"] == RTR_SDK_TIMEOUT
    assert call.kwargs["max_retries"] == 0
    assert call.kwargs["hard_timeout"] is None
    assert "## Recent chat snapshot" in call.prompt
    assert "- Tom: hi" in call.prompt
    assert "## Draft about to be sent" in call.prompt


def test_session_without_project_key_passes_none(monkeypatch):
    """A session that carries no key (or ``session=None``) leaves the router
    on its fail-closed rule; the call still reaches the leg."""
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    fake = _patch_run_typed(monkeypatch, _verdict("send", reason="clean"))

    asyncio.run(read_the_room(_long_draft(), GROUP_CHAT_ID, session=None))
    assert fake.last.project_key is None


def test_room_verdict_rejects_an_unknown_action():
    with pytest.raises(ValidationError):
        RoomVerdict(action="yeet", reason="??")


def test_room_verdict_empty_revised_text_is_none():
    """The model may answer ``revised_text=""``; the verdict carries ``None`` so
    the ``trim``-without-text coercion and the handler's truthiness checks
    see one shape."""
    assert RoomVerdict(action="send", revised_text="", reason="clean").revised_text is None


# === Snapshot construction ======================================================


def test_snapshot_k_cap(monkeypatch):
    """``_fetch_snapshot_sync`` should slice to K newest in the time window."""
    now = time.time()
    fake_messages = []
    for i in range(20):
        m = types.SimpleNamespace()
        m.timestamp = now - i  # all within 5-min window
        m.sender = f"user{i}"
        m.content = f"msg-{i}"
        m.direction = "in"
        m.message_id = i
        fake_messages.append(m)

    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=fake_messages)
    fake_model = MagicMock()
    fake_model.query = fake_query
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync(
            GROUP_CHAT_ID, k=DEFAULT_K, max_age_seconds=DEFAULT_MAX_AGE_SECONDS
        )

    assert len(out) == DEFAULT_K
    # Newest-last (chronological) ordering -- last entry has the smallest age.
    senders_last = out[-1]["sender"]
    senders_first = out[0]["sender"]
    assert senders_last == "user0"
    assert senders_first == f"user{DEFAULT_K - 1}"


def test_snapshot_time_window_drops_old(monkeypatch):
    now = time.time()
    fake_messages = [
        types.SimpleNamespace(
            timestamp=now - 1, sender="recent", content="r", direction="in", message_id=1
        ),
        types.SimpleNamespace(
            timestamp=now - 30, sender="newish", content="n", direction="in", message_id=2
        ),
        types.SimpleNamespace(
            timestamp=now - 600, sender="ancient", content="a", direction="in", message_id=3
        ),
    ]
    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=fake_messages)
    fake_model = MagicMock(query=fake_query)
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync(GROUP_CHAT_ID, k=DEFAULT_K, max_age_seconds=300)

    senders = [m["sender"] for m in out]
    assert "ancient" not in senders
    assert "recent" in senders
    assert "newish" in senders


def test_snapshot_mixed_attribution_passes_through(monkeypatch):
    """Both ``sender="Valor"`` (out) and ``sender="system"`` (in) entries
    must reach the prompt unfiltered (Risk 3 / B2)."""
    now = time.time()
    fake_messages = [
        types.SimpleNamespace(
            timestamp=now - 5,
            sender="Valor",
            content="prior agent turn",
            direction="out",
            message_id=10,
        ),
        types.SimpleNamespace(
            timestamp=now - 3,
            sender="system",
            content="pm-direct turn",
            direction="in",
            message_id=11,
        ),
        types.SimpleNamespace(
            timestamp=now - 1,
            sender="Tom",
            content="human reply",
            direction="in",
            message_id=12,
        ),
    ]
    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=fake_messages)
    fake_model = MagicMock(query=fake_query)
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync(GROUP_CHAT_ID, k=DEFAULT_K, max_age_seconds=300)

    senders = [m["sender"] for m in out]
    assert senders == ["Valor", "system", "Tom"]


def test_snapshot_query_failure_returns_empty(monkeypatch):
    fake_query = MagicMock()
    fake_query.filter = MagicMock(side_effect=RuntimeError("redis down"))
    fake_model = MagicMock(query=fake_query)
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync(GROUP_CHAT_ID, k=DEFAULT_K, max_age_seconds=300)

    assert out == []


# === Helpers ====================================================================


def test_format_snapshot_renders_lines():
    snap = [
        {"sender": "Tom", "content": "hi there"},
        {"sender": "Valor", "content": "earlier reply"},
    ]
    out = _format_snapshot_for_prompt(snap)
    assert "- Tom: hi there" in out
    assert "- Valor: earlier reply" in out


def test_format_snapshot_empty():
    assert _format_snapshot_for_prompt([]).startswith("(no recent messages)")


def test_format_snapshot_truncates_long_content():
    snap = [{"sender": "Tom", "content": "x" * 1000}]
    out = _format_snapshot_for_prompt(snap)
    assert len(out) < 600


def test_system_prompt_describes_attribution():
    """The prompt must spell out the {Valor, system} attribution rule
    so the model treats agent-authored prior turns correctly."""
    assert "Valor" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "system" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "self_duplicate" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "send" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "trim" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "suppress" in READ_THE_ROOM_SYSTEM_PROMPT


def test_emoji_constant():
    """The default reactor emoji is 👀 (first-person reactor voice).
    Operators tune in this single line per memory feedback_reactor_voice_emoji."""
    assert RTR_SUPPRESS_EMOJI == "👀"
