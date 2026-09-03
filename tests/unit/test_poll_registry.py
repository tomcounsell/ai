"""Poll registry (#2701, Task 6).

Uses the real (test-db-claimed) Redis rather than a mock: the properties under
test are Redis semantics — SET NX idempotence, SET XX not resurrecting an
expired key, index-set membership — and a mock would assert the test author's
model of Redis rather than Redis.
"""

import uuid

import pytest

from bridge import poll_registry as reg


@pytest.fixture
def poll_id(redis_test_db):
    """A unique id per test, with every key it touches removed afterwards."""
    pid = f"test-{uuid.uuid4().hex[:12]}"
    yield pid
    r = reg._redis()
    for prefix in (
        reg.POLL_ROW_PREFIX,
        reg.POLL_ANSWERED_PREFIX,
        reg.POLL_DISPATCHED_PREFIX,
        reg.POLL_STEERED_PREFIX,
        reg.POLL_WARNED_PREFIX,
    ):
        r.delete(f"{prefix}{pid}")
    r.srem(reg.POLL_OPEN_INDEX, pid)


@pytest.fixture
def hint(redis_test_db):
    h = uuid.uuid4().hex
    yield h
    reg.delete_pending_poll(h)
    # A prose answer marks a provisional row steered by hint, so the steered
    # key is part of this fixture's footprint too.
    reg._redis().delete(f"{reg.POLL_STEERED_PREFIX}{h}")


def _register(pid, session_id="sess-1"):
    return reg.register_poll(
        pid,
        chat_id=-1003449100931,
        msg_id=1413,
        session_id=session_id,
        question="Which approach?",
        options=["A", "B", "Other: wait for followup message"],
    )


class TestDescriptorRow:
    def test_register_writes_and_indexes_in_one_call(self, poll_id):
        """A lost SADD is the ONLY way to lose a poll, so it cannot be separable."""
        assert _register(poll_id) is True
        row = reg.lookup_poll(poll_id)
        assert row["msg_id"] == 1413
        assert row["session_id"] == "sess-1"
        assert reg._redis().sismember(reg.POLL_OPEN_INDEX, poll_id)

    def test_row_is_immutable_under_re_registration(self, poll_id):
        """SET NX — a second write must not clobber the descriptor."""
        _register(poll_id)
        assert _register(poll_id, session_id="other") is False
        assert reg.lookup_poll(poll_id)["session_id"] == "sess-1"

    def test_lookup_of_unknown_poll_returns_none(self, redis_test_db):
        assert reg.lookup_poll("never-registered") is None


class TestMarkersAreIndependentKeys:
    def test_all_markers_survive_any_interleaving(self, poll_id):
        """Fields inside one JSON value would make each write a read-modify-write.

        The reconcile loop's warn write races the fast-path translator's marker
        writes, and the claim serializes *translation*, not the loop's own
        scan-side write. A lost update drops steered_at (row re-yielded forever)
        or drops dispatched (the double-enqueue the marker exists to prevent).
        """
        _register(poll_id)
        reg.mark_poll_warned(poll_id)
        reg.mark_poll_dispatched(poll_id)
        reg.mark_poll_steered(poll_id)

        assert reg.poll_dispatched(poll_id)
        assert reg.poll_steered(poll_id)
        assert reg._redis().exists(f"{reg.POLL_WARNED_PREFIX}{poll_id}")

    def test_markers_do_not_rewrite_the_descriptor_row(self, poll_id):
        _register(poll_id)
        before = reg.lookup_poll(poll_id)
        reg.mark_poll_dispatched(poll_id)
        reg.mark_poll_steered(poll_id)
        reg.mark_poll_warned(poll_id)
        assert reg.lookup_poll(poll_id) == before

    def test_warn_marker_dedupes(self, poll_id):
        assert reg.mark_poll_warned(poll_id) is True
        assert reg.mark_poll_warned(poll_id) is False


class TestClaim:
    def test_claim_is_one_shot(self, poll_id):
        assert reg.claim_poll_answer(poll_id) is True
        assert reg.claim_poll_answer(poll_id) is False

    def test_claim_value_is_a_timestamp_not_the_constant_one(self, poll_id):
        """Without a readable value, a stale claim is indistinguishable from a live
        one and the bridge-death recovery cannot execute at all."""
        reg.claim_poll_answer(poll_id)
        raw = reg._redis().get(f"{reg.POLL_ANSWERED_PREFIX}{poll_id}")
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        assert value != "1"
        from datetime import datetime

        datetime.fromisoformat(value)  # parses, or this raises

    def test_claim_age_reads_back(self, poll_id):
        reg.claim_poll_answer(poll_id)
        age = reg.poll_claim_age_s(poll_id)
        assert age is not None and 0 <= age < 60

    def test_claim_age_is_none_when_absent(self, poll_id):
        assert reg.poll_claim_age_s(poll_id) is None

    def test_takeover_requires_an_existing_claim(self, poll_id):
        """XX — it takes over a stale claim, never resurrects an expired one."""
        assert reg.takeover_poll_claim(poll_id) is False
        reg.claim_poll_answer(poll_id)
        assert reg.takeover_poll_claim(poll_id) is True

    def test_takeover_is_diagnosable_from_logs(self, poll_id, caplog):
        """The one-command residual must be visible, not inferred."""
        import logging

        reg.claim_poll_answer(poll_id)
        with caplog.at_level(logging.WARNING):
            reg.takeover_poll_claim(poll_id)
        assert "poll_claim_takeover" in caplog.text

    def test_release_allows_a_re_claim(self, poll_id):
        reg.claim_poll_answer(poll_id)
        reg.release_poll_claim(poll_id)
        assert reg.claim_poll_answer(poll_id) is True


class TestEnumeration:
    def test_unanswered_yields_a_registered_poll(self, poll_id):
        _register(poll_id)
        assert poll_id in [pid for pid, _row in reg.iter_unanswered_polls()]

    def test_steered_poll_is_no_longer_unanswered(self, poll_id):
        _register(poll_id)
        reg.mark_poll_steered(poll_id)
        assert poll_id not in [pid for pid, _row in reg.iter_unanswered_polls()]

    def test_claim_alone_does_not_mark_a_poll_answered(self, poll_id):
        """iter_unanswered keys on the STEERED marker, never on the claim.

        Treating "claim present" as answered makes the bridge-death recovery
        inert: the row would never be re-yielded for the takeover.
        """
        _register(poll_id)
        reg.claim_poll_answer(poll_id)
        assert poll_id in [pid for pid, _row in reg.iter_unanswered_polls()]

    def test_expired_row_is_swept_from_the_index(self, poll_id):
        """The index is a hint; the row is authoritative."""
        _register(poll_id)
        reg._redis().delete(f"{reg.POLL_ROW_PREFIX}{poll_id}")
        list(reg.iter_unanswered_polls())
        assert not reg._redis().sismember(reg.POLL_OPEN_INDEX, poll_id)

    def test_no_keyspace_scan_on_the_poll_path(self):
        """SCAN MATCH filters server-side but still walks EVERY key in a db
        shared with production Popoto keys — once per fast reconcile tick.

        Checked over the AST rather than the source text, for two reasons: the
        module docstring legitimately *names* the banned call to explain why it
        is absent, and `sscan_iter` over an index SET — the approved mechanism —
        contains `scan_iter` as a substring, so a text grep flags the correct
        implementation.
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(reg))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "scan_iter" not in called, "keyspace scan_iter on the poll path"
        assert "scan" not in called, "keyspace SCAN on the poll path"
        # The approved index-backed form IS used.
        assert "sscan_iter" in called


class TestProvisionalRow:
    def test_register_and_promote(self, hint, poll_id):
        assert reg.register_pending_poll(
            hint,
            chat_id=-1003449100931,
            session_id="sess-1",
            question="Q?",
            options=["A", "B"],
        )
        assert reg._redis().sismember(reg.POLL_PENDING_INDEX, hint)

        assert reg.promote_pending_poll(hint, poll_id, msg_id=1413)
        # Real row exists, provisional gone from both key and index.
        assert reg.lookup_poll(poll_id)["msg_id"] == 1413
        assert reg.lookup_pending_poll(hint) is None
        assert not reg._redis().sismember(reg.POLL_PENDING_INDEX, hint)

    def test_promote_without_a_provisional_row_is_a_no_op(self, redis_test_db):
        assert reg.promote_pending_poll("no-such-hint", "p", msg_id=1) is False

    def test_promote_refuses_to_overwrite_a_poll_registered_under_another_hint(self, hint, poll_id):
        """A false-positive adoption match must not swallow the question.

        `poll_id` is already registered (promoted) under `other_hint`. A second
        hint (`hint`) whose provisional row falsely resolves to the same
        `poll_id` — the shape of a false-positive truncated-prefix match — must
        fail its own promotion: it must not overwrite `other_hint`'s row, and
        its own pending row must stay intact so the question remains adoptable.
        """
        other_hint = uuid.uuid4().hex
        try:
            assert reg.register_pending_poll(
                other_hint, chat_id=-1, session_id="sess-A", question="QA", options=["A"]
            )
            assert reg.promote_pending_poll(other_hint, poll_id, msg_id=1) is True
            original_row = reg.lookup_poll(poll_id)

            assert reg.register_pending_poll(
                hint, chat_id=-2, session_id="sess-B", question="QB", options=["B"]
            )
            assert reg.promote_pending_poll(hint, poll_id, msg_id=999) is False

            pending_b = reg.lookup_pending_poll(hint)
            assert pending_b is not None
            assert pending_b["session_id"] == "sess-B"
            assert reg._redis().sismember(reg.POLL_PENDING_INDEX, hint)

            assert reg.lookup_poll(poll_id) == original_row
        finally:
            reg.delete_pending_poll(other_hint)

    def test_delete_removes_key_and_index_member(self, hint):
        reg.register_pending_poll(hint, chat_id=-1, session_id="s", question="Q", options=["A"])
        reg.delete_pending_poll(hint)
        assert reg.lookup_pending_poll(hint) is None
        assert not reg._redis().sismember(reg.POLL_PENDING_INDEX, hint)

    def test_provisional_row_survives_a_simulated_restart(self, hint):
        """Race 5/6: all routing state is in Redis, nothing in process memory."""
        reg.register_pending_poll(hint, chat_id=-1, session_id="s", question="Q", options=["A"])
        import importlib

        importlib.reload(reg)
        assert reg.lookup_pending_poll(hint) is not None


class TestSessionHasOpenPoll:
    def test_matches_on_session_id(self, poll_id):
        _register(poll_id, session_id="sess-open")
        assert reg.session_has_open_poll("sess-open") is True

    def test_ignores_an_already_steered_poll(self, poll_id):
        _register(poll_id, session_id="sess-open")
        reg.mark_poll_steered(poll_id)
        assert reg.session_has_open_poll("sess-open") is False

    def test_unknown_session_is_false(self, redis_test_db):
        assert reg.session_has_open_poll("never-asked-anything") is False

    def test_missing_session_id_is_false_without_a_read(self, redis_test_db):
        assert reg.session_has_open_poll(None) is False
        assert reg.session_has_open_poll("") is False

    def test_redis_failure_returns_false_rather_than_raising(self, monkeypatch):
        """A Redis hiccup must degrade to today's nudge behavior, never wedge a
        session that has no outstanding question."""

        def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(reg, "iter_unanswered_polls", _boom)
        assert reg.session_has_open_poll("sess-1") is False

    def test_a_provisional_row_already_counts_as_an_open_question(self, hint):
        """The relay is a polling loop; the pause check runs at turn end.

        Between `valor-ask-poll` enqueuing the payload and the relay writing the
        real row, the question exists only as a provisional row. Reading only
        POLL_OPEN_INDEX reports "no open question" during that window and the
        session gets nudged straight past it — the exact defect the pause exists
        to prevent.
        """
        reg.register_pending_poll(
            hint, chat_id=-1, session_id="sess-pending", question="Q?", options=["A", "B"]
        )
        assert reg.session_has_open_poll("sess-pending") is True

    def test_a_steered_provisional_row_no_longer_counts(self, hint):
        reg.register_pending_poll(
            hint, chat_id=-1, session_id="sess-pending", question="Q?", options=["A", "B"]
        )
        reg.mark_poll_steered(hint)
        assert reg.session_has_open_poll("sess-pending") is False


class TestMarkSessionPollsSteered:
    """A typed answer closes the question exactly as a tap does.

    `mark_poll_steered` is written only by the vote path. Without a route for
    the prose answer the row survives its full 24h TTL, `session_has_open_poll`
    keeps returning True, and every turn of that session takes the pause branch
    instead of nudge-continue — auto-continue silently off for a day, with the
    session advancing one turn per human message.
    """

    def test_closes_an_open_poll_for_the_session(self, poll_id):
        _register(poll_id, session_id="sess-typed")
        assert reg.session_has_open_poll("sess-typed") is True

        assert reg.mark_session_polls_steered("sess-typed") == 1

        assert reg.poll_steered(poll_id) is True
        assert reg.session_has_open_poll("sess-typed") is False

    def test_leaves_another_session_alone(self, poll_id):
        _register(poll_id, session_id="sess-other")
        assert reg.mark_session_polls_steered("sess-typed") == 0
        assert reg.session_has_open_poll("sess-other") is True

    def test_closes_a_provisional_row_without_deleting_it(self, hint):
        """The hint is the only evidence a send may have landed.

        Deleting the row would strand orphan adoption after a restart, so the
        close-out is a marker, not a delete.
        """
        reg.register_pending_poll(
            hint, chat_id=-1, session_id="sess-typed", question="Q?", options=["A", "B"]
        )
        assert reg.mark_session_polls_steered("sess-typed") == 1
        assert reg.lookup_pending_poll(hint) is not None
        assert reg.session_has_open_poll("sess-typed") is False

    def test_is_idempotent(self, poll_id):
        """The vote path marks the row before `resume_completed_session` runs."""
        _register(poll_id, session_id="sess-typed")
        reg.mark_poll_steered(poll_id)
        assert reg.mark_session_polls_steered("sess-typed") == 0
        assert reg.session_has_open_poll("sess-typed") is False

    def test_missing_session_id_is_a_no_op(self, redis_test_db):
        assert reg.mark_session_polls_steered(None) == 0
        assert reg.mark_session_polls_steered("") == 0

    def test_redis_failure_never_raises(self, monkeypatch):
        """It hangs off an answer route; a bookkeeping failure must not break it."""

        def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(reg, "iter_unanswered_polls", _boom)
        assert reg.mark_session_polls_steered("sess-1") == 0

    def test_promotion_carries_the_close_out_onto_the_real_poll_id(self, hint, poll_id):
        """A prose answer that lands while the payload is still in the outbox.

        Without the carry the promoted row is born unsteered and re-opens the
        pause on a question the human already answered — the same defect in a
        narrower window.
        """
        reg.register_pending_poll(
            hint, chat_id=-1, session_id="sess-typed", question="Q?", options=["A", "B"]
        )
        reg.mark_session_polls_steered("sess-typed")

        assert reg.promote_pending_poll(hint, poll_id, msg_id=1413) is True
        assert reg.poll_steered(poll_id) is True
        assert reg.session_has_open_poll("sess-typed") is False
