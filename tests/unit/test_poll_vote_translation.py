"""Vote → steering translation (#2701, Tasks 9b/13b).

The properties under test here are the ones that are invisible from reading the
code in order: which branch a vote takes, what happens when a claim is lost, and
what must NOT happen twice.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.answer_routing import AnswerTarget, AnswerTargetKind
from bridge.poll_vote import (
    ESCAPE_HATCH_OPTION,
    build_steer_text,
    select_option,
    translate_poll_vote,
)

OPTIONS = ["Approach A", "Approach B", ESCAPE_HATCH_OPTION]
ROW = {
    "chat_id": "-1003449100931",
    "msg_id": 1413,
    "session_id": "sess-1",
    "question": "Which approach?",
    "options": OPTIONS,
    "created_at": "2026-09-02T08:00:00+00:00",
}


def _voters(*pairs):
    """Build a PollResults-alike from (option_index, voters) pairs."""
    from bridge.response import encode_option

    results = MagicMock()
    results.results = [MagicMock(option=encode_option(i, "a" * 32), voters=v) for i, v in pairs]
    return results


def _client_returning(results):
    client = MagicMock()
    update = MagicMock()
    update.results = results
    response = MagicMock()
    response.updates = [update]
    client.side_effect = None
    client.__call__ = AsyncMock(return_value=response)

    async def _call(_request):
        return response

    client_obj = MagicMock(side_effect=_call)
    client_obj.get_messages = AsyncMock(return_value=MagicMock(media=None))
    return client_obj


class TestSelectOption:
    """Deterministic under multiple voters — the DM 'exactly one' rule is dead."""

    def test_single_voter_wins(self):
        assert select_option(_voters((1, 1)), OPTIONS) == 1

    def test_no_voters_returns_none(self):
        assert select_option(_voters(), OPTIONS) is None

    def test_highest_voters_wins(self):
        assert select_option(_voters((0, 1), (1, 3)), OPTIONS) == 1

    def test_tie_broken_by_lowest_index_deterministically(self):
        """Repeated runs must agree — a nondeterministic tie-break is a bug."""
        for _ in range(20):
            assert select_option(_voters((2, 2), (0, 2)), OPTIONS) == 0

    def test_multiple_voters_emits_a_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            select_option(_voters((0, 1), (1, 1)), OPTIONS)
        assert "poll_multiple_voters" in caplog.text

    def test_undecodable_options_return_none(self):
        results = MagicMock()
        results.results = [MagicMock(option=b"", voters=1)]
        assert select_option(results, OPTIONS) is None


class TestSteerText:
    def test_steer_carries_the_question_not_just_the_option(self):
        """The resumed turn must not have to re-derive what it asked."""
        text = build_steer_text("Which approach?", "Approach A")
        assert "Which approach?" in text
        assert "Approach A" in text

    def test_escape_hatch_instructs_a_narrowed_followup(self):
        text = build_steer_text("Which approach?", ESCAPE_HATCH_OPTION)
        assert "followup" in text.lower()
        assert "reply" in text.lower()


@pytest.fixture
def registry():
    """Patch every registry helper the translator touches."""
    with (
        patch("bridge.poll_vote.lookup_poll", return_value=dict(ROW)) as lookup,
        patch("bridge.poll_vote.claim_poll_answer", return_value=True) as claim,
        patch("bridge.poll_vote.poll_claim_age_s", return_value=None) as age,
        patch("bridge.poll_vote.poll_dispatched", return_value=False) as dispatched,
        patch("bridge.poll_vote.takeover_poll_claim", return_value=True) as takeover,
        patch("bridge.poll_vote.release_poll_claim") as release,
        patch("bridge.poll_vote.mark_poll_dispatched") as mark_dispatched,
        patch("bridge.poll_vote.mark_poll_steered") as mark_steered,
    ):
        yield {
            "lookup": lookup,
            "claim": claim,
            "age": age,
            "dispatched": dispatched,
            "takeover": takeover,
            "release": release,
            "mark_dispatched": mark_dispatched,
            "mark_steered": mark_steered,
        }


def _target(kind, project_key="proj"):
    session = MagicMock()
    session.session_id = "sess-1"
    session.project_key = project_key
    session.initial_telegram_message = {"sender_name": "Tom"}
    return AnswerTarget(kind=kind, session=session, matched_status="running")


class TestBranchPerKind:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kind",
        [AnswerTargetKind.LIVE, AnswerTargetKind.PENDING, AnswerTargetKind.LIVE_GUARD],
    )
    async def test_live_kinds_steer_with_a_mandatory_room_id(self, registry, kind):
        """room_id is asserted on the KEYWORD, not just on the steer text.

        Omitting it is a silent legacy-key downgrade rather than an error: the
        write lands on `steering:{session_id}` while every peer caller writes the
        Room leg, and nothing raises.
        """
        with (
            patch("bridge.poll_vote.resolve_answer_target", return_value=_target(kind)),
            patch("agent.steering.push_steering_message") as push,
            patch("models.room.room_id_for_session", return_value="proj:room"),
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        push.assert_called_once()
        assert push.call_args.kwargs["room_id"] == "proj:room"
        assert "Approach A" in push.call_args[0][1]

    @pytest.mark.asyncio
    async def test_session_without_project_key_takes_the_legacy_leg(self, registry):
        """room_id=None must steer, not raise."""
        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                return_value=_target(AnswerTargetKind.LIVE, project_key=None),
            ),
            patch("agent.steering.push_steering_message") as push,
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        push.assert_called_once()
        assert push.call_args.kwargs["room_id"] is None

    @pytest.mark.asyncio
    async def test_completed_resumes_rather_than_steering(self, registry):
        """THE MAINLINE. /ask-me finalizes its session before a human taps.

        Dropping this branch loses EVERY vote, not a rare one.
        """
        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                return_value=_target(AnswerTargetKind.COMPLETED),
            ),
            patch("bridge.poll_vote.resume_completed_session", new_callable=AsyncMock) as resume,
            patch("agent.steering.push_steering_message") as push,
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        push.assert_not_called()
        resume.assert_called_once()
        # The poll's OWN msg_id is the dedup key: claim_message is inbound-only,
        # so an outbound poll's id is unused, unique and stable here.
        assert resume.call_args.kwargs["telegram_message_id"] == 1413
        assert resume.call_args.kwargs["reply_chain_context"] is None

    @pytest.mark.asyncio
    async def test_none_creates_no_session(self, registry):
        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                return_value=AnswerTarget(kind=AnswerTargetKind.NONE, session=None),
            ),
            patch("bridge.poll_vote.resume_completed_session", new_callable=AsyncMock) as resume,
            patch("agent.steering.push_steering_message") as push,
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        push.assert_not_called()
        resume.assert_not_called()


class TestQuietReturns:
    @pytest.mark.asyncio
    async def test_unknown_poll_id_returns_quietly(self):
        with (
            patch("bridge.poll_vote.lookup_poll", return_value=None),
            patch("bridge.poll_vote.claim_poll_answer") as claim,
        ):
            await translate_poll_vote(MagicMock(), "never-sent")
        claim.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_voters_does_not_consume_the_claim(self, registry):
        """A spurious update must not burn the one-shot claim.

        If it did, the real answer that arrives later would be swallowed
        permanently — the exact failure this feature exists to prevent.
        """
        await translate_poll_vote(_client_returning(_voters()), "poll-1")
        registry["claim"].assert_not_called()


class TestClaimDurability:
    @pytest.mark.asyncio
    async def test_young_lost_claim_returns_without_steering(self, registry):
        """A genuine concurrent translator. Returning is correct."""
        from bridge.poll_registry import POLL_RECONCILE_SLOW_INTERVAL_S

        registry["claim"].return_value = False
        registry["age"].return_value = POLL_RECONCILE_SLOW_INTERVAL_S / 2

        with (
            patch("bridge.poll_vote.resolve_answer_target") as resolve,
            patch("agent.steering.push_steering_message") as push,
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        resolve.assert_not_called()
        push.assert_not_called()
        registry["takeover"].assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_claim_with_no_dispatch_is_taken_over_and_steers_once(self, registry):
        """Bridge-death recovery ACTUALLY EXECUTES.

        This is the state a death after the claim leaves behind: claim present,
        no `dispatched`, claim older than one slow interval, and no `except`
        handler ever ran to release it. An unconditional return here would make
        the whole Risk 9 recovery inert for the failure it names first.
        """
        from bridge.poll_registry import POLL_RECONCILE_SLOW_INTERVAL_S

        registry["claim"].return_value = False
        registry["age"].return_value = POLL_RECONCILE_SLOW_INTERVAL_S * 3
        registry["dispatched"].return_value = False
        registry["takeover"].return_value = True

        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                return_value=_target(AnswerTargetKind.LIVE),
            ),
            patch("agent.steering.push_steering_message") as push,
            patch("models.room.room_id_for_session", return_value="proj:room"),
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        registry["takeover"].assert_called_once()
        assert push.call_count == 1

    @pytest.mark.asyncio
    async def test_stale_claim_with_dispatch_present_steers_zero_times(self, registry):
        """The mirror case, and the load-bearing half of the guard.

        The side effect already happened; re-attempt only the completion marker.
        """
        from bridge.poll_registry import POLL_RECONCILE_SLOW_INTERVAL_S

        registry["claim"].return_value = False
        registry["age"].return_value = POLL_RECONCILE_SLOW_INTERVAL_S * 3
        registry["dispatched"].return_value = True

        with (
            patch("bridge.poll_vote.resolve_answer_target") as resolve,
            patch("agent.steering.push_steering_message") as push,
            patch("bridge.poll_vote.resume_completed_session", new_callable=AsyncMock) as resume,
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        push.assert_not_called()
        resume.assert_not_called()
        resolve.assert_not_called()
        registry["takeover"].assert_not_called()
        registry["mark_steered"].assert_called_once()

    @pytest.mark.asyncio
    async def test_failure_before_the_steer_releases_the_claim(self, registry):
        """So the next reconciliation tick retries rather than swallowing it."""
        registry["dispatched"].return_value = False

        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                side_effect=RuntimeError("redis down"),
            ),
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        registry["release"].assert_called_once()
        registry["mark_steered"].assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_after_the_steer_does_not_release_or_redispatch(self, registry):
        """The mirror-image failure: one vote, two enqueues.

        A blanket release after a SUCCESSFUL side effect re-runs the dispatch,
        which on the COMPLETED mainline double-enqueues the session. The
        `dispatched` marker is what bounds the release.
        """
        registry["dispatched"].return_value = True
        registry["mark_steered"].side_effect = RuntimeError("marker write failed")

        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                return_value=_target(AnswerTargetKind.LIVE),
            ),
            patch("agent.steering.push_steering_message") as push,
            patch("models.room.room_id_for_session", return_value="proj:room"),
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            with pytest.raises(RuntimeError):
                await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        assert push.call_count == 1
        registry["release"].assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_marker_written_before_the_completion_marker(self, registry):
        """`mark_poll_dispatched` must precede anything post-steer that can throw."""
        order = []
        registry["mark_dispatched"].side_effect = lambda *_a: order.append("dispatched")
        registry["mark_steered"].side_effect = lambda *_a: order.append("steered")

        with (
            patch(
                "bridge.poll_vote.resolve_answer_target",
                return_value=_target(AnswerTargetKind.LIVE),
            ),
            patch("agent.steering.push_steering_message"),
            patch("models.room.room_id_for_session", return_value="proj:room"),
            patch("bridge.response.close_poll", new_callable=AsyncMock),
        ):
            await translate_poll_vote(_client_returning(_voters((0, 1))), "poll-1")

        assert order == ["dispatched", "steered"]


class TestSenderName:
    def test_falls_back_through_the_session_then_the_literal(self):
        """No GetPollVotesRequest attempt — it cannot resolve an anonymous poll.

        Verified by the Task 2 gate: polls are sent with `public_voters=False`
        and per-voter detail is only retrievable for a public poll.
        """
        from bridge.poll_vote import _resolve_sender_name

        assert _resolve_sender_name(_target(AnswerTargetKind.LIVE)) == "Tom"

        bare = MagicMock()
        bare.initial_telegram_message = None
        assert (
            _resolve_sender_name(AnswerTarget(kind=AnswerTargetKind.LIVE, session=bare))
            == "Telegram poll"
        )

    def test_no_get_poll_votes_call_is_built(self):
        """Dead weight on the inbound fast path is not built at all.

        Checked over the AST, not the raw text: the module docstrings
        deliberately *name* GetPollVotesRequest to explain why it is absent, and
        a substring grep cannot tell an explanation from an import.
        """
        import ast
        import inspect

        import bridge.poll_vote as mod

        tree = ast.parse(inspect.getsource(mod))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        assert "GetPollVotesRequest" not in imported, (
            "poll_vote imports GetPollVotesRequest — it cannot resolve a voter "
            "for a poll sent with public_voters=False, so it is dead weight on "
            "the inbound fast path"
        )
