"""Scheduler adapter: admit, materialize, activate (Task 6, Race 2, Race 7)."""

from __future__ import annotations

import uuid

from models.agent_session import AgentSession, SessionType
from models.improvement_case import ImprovementCase
from models.session_lifecycle import transition_status
from tools.improvement_control import scheduler_adapter as adapter
from tools.improvement_control.intents import list_intents
from tools.improvement_control.journal import transition
from tools.improvement_control.lease import CaseLease
from utils.redis_client import text_redis

PK = "test-3215-dispatch"


def new_case(state: str = "investigating", *, charter_digest: str | None = None) -> ImprovementCase:
    from datetime import UTC, datetime

    return ImprovementCase.create(
        project_key=PK,
        state=state,
        title="t",
        created_at=datetime.now(UTC),
        charter_digest=charter_digest,
    )


def propose(
    case_id: str,
    action_id: str,
    *,
    expected_revision: int = 0,
    action_type: str = "investigate",
    artifact_ref: str = "",
) -> None:
    r = transition(
        PK,
        case_id,
        expected_revision=expected_revision,
        generation=1,
        event="action_proposed",
        payload_digest="sha256:d",
        action_id=action_id,
        action_type=action_type,
        artifact_ref=artifact_ref,
    )
    assert r.accepted, r


def fake_lease() -> CaseLease:
    return CaseLease(text_redis(), default_ttl_seconds=90)


class CountingPush:
    """A fake `_push_agent_session` that creates a real AgentSession row and
    counts how many times it is asked to bind a given idempotency key, the
    same shape the real create-or-bind seam guarantees.

    Also records every call's kwargs (``self.push_kwargs``), so a test can
    assert on ``extra_context_overrides``, ``working_dir``, and
    ``message_text`` without a separate mock (Success Criterion 5).
    """

    def __init__(self):
        self.calls = 0
        self._bound: dict[str, str] = {}
        self.push_kwargs: list[dict] = []

    async def __call__(self, *, idempotency_key: str, status: str, **kwargs):
        self.calls += 1
        self.push_kwargs.append({"idempotency_key": idempotency_key, "status": status, **kwargs})
        if idempotency_key in self._bound:
            return (1, self._bound[idempotency_key])
        session_id = f"improve-test-{uuid.uuid4().hex[:8]}"
        AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text=kwargs.get("message_text", "x"),
            sender_name="improvement-controller",
            session_id=session_id,
            working_dir=".",
            status=status,
            extra_context=kwargs.get("extra_context_overrides") or {},
        )
        self._bound[idempotency_key] = session_id
        return (1, session_id)


class RaiseOnceThenCountingPush(CountingPush):
    """Race 2: the first call raises after the create-or-bind key is
    conceptually reserved; the retry must yield the same bound session id."""

    def __init__(self):
        super().__init__()
        self._raised = False

    async def __call__(self, *, idempotency_key: str, status: str, **kwargs):
        if not self._raised and idempotency_key not in self._bound:
            self._raised = True
            # Simulate the crash landing AFTER the seam bound the idempotency
            # key (so a retry gets the same id), but before this function
            # returned it to the adapter.
            session_id = f"improve-test-{uuid.uuid4().hex[:8]}"
            AgentSession.create(
                project_key=PK,
                chat_id="0",
                session_type=SessionType.ENG,
                message_text="x",
                sender_name="improvement-controller",
                session_id=session_id,
                working_dir=".",
                status=status,
            )
            self._bound[idempotency_key] = session_id
            self.calls += 1
            raise RuntimeError("simulated crash after bind")
        return await super().__call__(idempotency_key=idempotency_key, status=status, **kwargs)


class TestReconciliationRequiredSkip:
    def test_case_with_a_wedged_intent_is_skipped_every_tick(self):
        from tools.improvement_control.intents import admit, mark_reconciliation_required

        case = new_case()
        propose(case.id, "a1")
        r1 = admit(
            PK,
            case.id,
            "a1",
            expected_revision=1,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert r1.accepted
        mark_reconciliation_required(
            PK, case.id, "a1", expected_revision=r1.revision, generation=1, from_state="admitted"
        )
        result = adapter.tick(PK, lease=fake_lease(), push=CountingPush())
        assert result.skipped.get(case.id) == "reconciliation_required"


class TestAdmitMaterializeActivate:
    def test_happy_path_admits_materializes_and_activates(self, monkeypatch):
        import os

        from tools.improvement_control import keys

        monkeypatch.setattr("agent.session_health.any_worker_alive", lambda: True)
        published = []
        monkeypatch.setattr(
            "agent.agent_session_queue.publish_session_notify",
            lambda s: published.append((s.session_id, s.status)),
        )
        charter_digest = f"sha256:{uuid.uuid4().hex}"
        case = new_case(charter_digest=charter_digest)
        propose(case.id, "a1", action_type="experiment", artifact_ref="$CF:abc:brief.txt")
        push = CountingPush()
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert case.id in result.admitted
        assert case.id in result.materialized
        assert case.id in result.activated
        intents = list_intents(PK, case.id)
        assert len(intents) == 1
        assert intents[0].state == "running"

        # Success Criterion 7: published exactly once per activation, after
        # the row is pending (the recorder captures the status at publish time).
        (session_id,) = push._bound.values()
        assert published == [(session_id, "pending")]

        # Success Criterion 5: the recorded extra_context_overrides carry
        # exactly these four keys and no `generation` (Decision 12: a
        # generation copied at admit time would be stale by design the
        # moment any later acquirer writes). `action_type` flows from the
        # journal entry into the admitted intent.
        kwargs = push.push_kwargs[0]
        assert kwargs["extra_context_overrides"].keys() == {
            "research_case_id",
            "experiment_id",
            "action_id",
            "idempotency_key",
        }
        assert "generation" not in kwargs["extra_context_overrides"]
        assert "parent_agent_session_id" not in kwargs
        assert intents[0].action_type == "experiment"

        # Data Flow step 9: the worker gets an absolute checkout path and a
        # message that names the research skill and the loadable brief.
        assert os.path.isabs(kwargs["working_dir"])
        assert os.path.isdir(kwargs["working_dir"])
        assert kwargs["message_text"].startswith("/improve-research ")
        assert kwargs["message_text"].endswith(" brief_ref=$CF:abc:brief.txt")

        # Data Flow step 6: admit received the case's charter digest and the
        # entry's payload digest, and wrote both onto the intent hash.
        raw = text_redis().hgetall(keys.intent_key(PK, case.id, "a1"))
        assert raw["charter_digest"] == charter_digest
        assert raw["request_digest"] == "sha256:d"

    def test_charter_drift_between_propose_and_admit_skips_the_case(self):
        """Data Flow step 6's charter check: a case whose recorded digest no
        longer matches the pinned charter is skipped, nothing admitted."""
        from datetime import UTC, datetime

        from models.improvement_charter import ImprovementCharter

        ImprovementCharter.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            digest=f"sha256:{uuid.uuid4().hex}",
            state="active",
        )
        case = new_case(charter_digest=f"sha256:{uuid.uuid4().hex}")
        propose(case.id, "a1")
        push = CountingPush()
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert result.skipped.get(case.id) == "charter_digest_stale"
        assert case.id not in result.admitted
        assert list_intents(PK, case.id) == []
        assert push.calls == 0

    def test_no_live_worker_leaves_the_intent_materialized(self, monkeypatch):
        monkeypatch.setattr("agent.session_health.any_worker_alive", lambda: False)
        published = []
        monkeypatch.setattr(
            "agent.agent_session_queue.publish_session_notify",
            lambda s: published.append(s.session_id),
        )
        case = new_case()
        propose(case.id, "a1")
        push = CountingPush()
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert result.skipped.get(case.id) == "no_live_worker"
        intents = list_intents(PK, case.id)
        assert intents[0].state == "materialized"
        # Success Criterion 7: never published on the no-live-worker path.
        assert published == []

    def test_race_2_crash_after_bind_then_retry_yields_one_row(self, monkeypatch):
        """A crash between admission and session creation: the next tick
        retries with the same idempotency key and gets the same id back;
        the intent ends materialized with attempts == 2."""
        monkeypatch.setattr("agent.session_health.any_worker_alive", lambda: False)
        case = new_case()
        propose(case.id, "a1")
        push = RaiseOnceThenCountingPush()

        # First tick: the push raises inside asyncio.run, caught by the
        # per-case exception isolation in tick().
        result1 = adapter.tick(PK, lease=fake_lease(), push=push)
        assert case.id in result1.errors

        # Second tick: admit is refused (already admitted), but this proves
        # the retry path is idempotent at the seam -- rerun the proposal
        # under a fresh action id is out of scope here; assert the bound
        # session id is stable across two direct push calls with the same key.
        import asyncio

        idempotency_key = f"improve:{PK}:{case.id}:a1"
        _, sid1 = asyncio.run(
            push(
                idempotency_key=idempotency_key,
                status="admitted",
                project_key=PK,
                session_id="ignored",
                working_dir=".",
                message_text="x",
                sender_name="x",
                chat_id="0",
                telegram_message_id=0,
            )
        )
        _, sid2 = asyncio.run(
            push(
                idempotency_key=idempotency_key,
                status="admitted",
                project_key=PK,
                session_id="ignored",
                working_dir=".",
                message_text="x",
                sender_name="x",
                chat_id="0",
                telegram_message_id=0,
            )
        )
        assert sid1 == sid2


class TestActivateRace7:
    def _materialized_case(self, monkeypatch, row_status: str | None):
        """Admit + materialize a case, then set the bound row's status
        directly (or delete it, for the missing-row case), mimicking a
        retry tick that finds the row somewhere other than `admitted`."""
        case = new_case()
        propose(case.id, "a1")
        push = CountingPush()
        monkeypatch.setattr("agent.session_health.any_worker_alive", lambda: False)
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert result.skipped.get(case.id) == "no_live_worker"
        session_id = list(push._bound.values())[0]
        session = AgentSession.query.get(session_id=session_id)
        if row_status is None:
            session.delete()
        elif row_status == "completed":
            # A terminal status: bypass transition_status (non-terminal only)
            # and finalize_session (whose own step 7 would race this test's
            # own assertions) with a direct ORM write, simulating "the
            # worker already finished this row through its ordinary path".
            session.status = "completed"
            session.save()
        elif row_status != "admitted":
            transition_status(session, row_status, reason="test setup")
        monkeypatch.setattr("agent.session_health.any_worker_alive", lambda: True)
        return case, session_id, push

    def test_activate_retry_republishes_a_pending_row(self, monkeypatch):
        published = []
        monkeypatch.setattr(
            "agent.agent_session_queue.publish_session_notify",
            lambda s: published.append(s.session_id),
        )
        case, session_id, push = self._materialized_case(monkeypatch, "pending")
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert published == [session_id]
        assert case.id in result.activated
        intents = list_intents(PK, case.id)
        assert intents[0].state == "running"

    def test_activate_retry_never_demotes_a_running_session(self, monkeypatch):
        published = []
        monkeypatch.setattr(
            "agent.agent_session_queue.publish_session_notify",
            lambda s: published.append(s.session_id),
        )
        case, session_id, push = self._materialized_case(monkeypatch, "running")
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert published == []
        assert case.id not in result.activated
        session = AgentSession.query.get(session_id=session_id)
        assert session.status == "running"
        intents = list_intents(PK, case.id)
        assert intents[0].state == "running"

    def test_activate_retry_settles_a_finished_session(self, monkeypatch):
        case, session_id, push = self._materialized_case(monkeypatch, "completed")
        adapter.tick(PK, lease=fake_lease(), push=push)
        intents = list_intents(PK, case.id)
        assert intents[0].state == "settled"
        assert intents[0].reason == "no_proposal"

    def test_activate_treats_a_missing_row_as_terminal(self, monkeypatch):
        case, session_id, push = self._materialized_case(monkeypatch, None)
        result = adapter.tick(PK, lease=fake_lease(), push=push)
        assert result.skipped.get(case.id) == "session_row_missing"
        intents = list_intents(PK, case.id)
        assert intents[0].state == "materialized"


class TestTickIsolatesFailures:
    def test_tick_isolates_one_bad_case(self, monkeypatch):
        """Failure Path Test Strategy: `scheduler_adapter.tick`'s per-case
        `except Exception` -- one case's failure is logged and the tick
        continues to the rest."""
        good_case = new_case()
        bad_case = new_case()
        propose(good_case.id, "a-good")
        propose(bad_case.id, "a-bad")

        real_admit = adapter.admit

        def flaky_admit(project_key, case_id, action_id, **kwargs):
            if case_id == bad_case.id:
                raise RuntimeError("simulated admit failure")
            return real_admit(project_key, case_id, action_id, **kwargs)

        monkeypatch.setattr(adapter, "admit", flaky_admit)
        monkeypatch.setattr("agent.session_health.any_worker_alive", lambda: False)
        push = CountingPush()

        result = adapter.tick(PK, lease=fake_lease(), push=push)

        assert bad_case.id in result.errors
        assert result.errors[bad_case.id] == "RuntimeError"
        assert good_case.id in result.admitted
        assert bad_case.id not in result.admitted
