"""End-to-end resume self-heal for SDLC run identity (issue #2144).

Proves **acceptance criterion 1**: a pipeline turn that was killed mid-BUILD
and resumed from transcript — losing its ``run_id`` from context, with the
issue lease and supervised-run signal lapsed (TTL) — can still land its next
``stage-marker`` write via the tool's self-heal path, with **no** manual
``sdlc-tool session-ensure`` and **no** ``--run-id`` supplied. This is the
exact #2133 shape: a bridge-originated PM pipeline where the only carrier that
survives the resume is the durable ``AgentSession.active_run_id`` mirror.

Real Redis, real ``PipelineLedger``, real issue lock — no mocks on the
self-heal path. The single stubbed boundary is target-repo resolution
(``GH_REPO`` -> a synthetic slug, rung-0 short-circuit, no live ``gh``),
mirroring ``tests/integration/test_sdlc_session_ensure_integration.py``. The
process cwd is redirected to a tmp dir so ``os.getcwd()``-based signal reads
cannot pick up the live worktree ``.sdlc-run`` of the running pipeline.
"""

from __future__ import annotations

import random
from pathlib import Path
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession

pytestmark = [pytest.mark.integration]

# Recognizable, test-scoped identifiers so teardown can scope cleanup narrowly.
TEST_PROJECT_KEY = "test-sdlc-2144-resume"
TEST_REPO_SLUG = "test-owner/test-repo-2144-resume"

# The create fall-through resolves its project/repo pairing from the cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_marker_main(argv):
    """Invoke the real ``sdlc_stage_marker`` CLI and return its exit code."""
    import tools.sdlc_stage_marker as sm

    with patch("sys.argv", argv):
        try:
            sm.main()
        except SystemExit as e:
            return e.code
    return None


@pytest.fixture
def issue_number():
    """A fresh, never-real issue number per run — no pre-existing lock/ledger."""
    return 2_144_000 + random.randint(0, 999)


@pytest.fixture
def cleanup(issue_number):
    """ORM-delete the test sessions and ledger before and after (Popoto only)."""

    def _cleanup():
        try:
            # The bridge-style session by project_key, plus the sdlc-local-{N}
            # record the create fall-through mints under the repo's own project.
            local_id = f"sdlc-local-{issue_number}"
            for s in AgentSession.query.all():
                if (
                    getattr(s, "project_key", None) == TEST_PROJECT_KEY
                    or getattr(s, "session_id", None) == local_id
                ):
                    s.delete()
        except Exception:
            pass
        try:
            from agent.pipeline_ledger import PipelineLedger

            key = f"{TEST_REPO_SLUG}:{issue_number}"
            for rec in PipelineLedger.query.filter(ledger_key=key):
                rec.delete()
        except Exception:
            pass

    _cleanup()
    yield
    _cleanup()


def _mint_identity(issue_number, monkeypatch):
    """Create a bridge-style Eng session and mint run identity via ensure_session.

    Returns ``(session_id, ensure_result)``. Mirrors how the Telegram bridge
    builds an Eng session (message_text, no issue_url) so the resumed turn is
    findable by issue number via the ``message_text`` fallback in
    ``find_session_by_issue``.
    """
    from tools.sdlc_session_ensure import ensure_session

    session_id = "tg_valor_2144_resume"
    AgentSession.create_eng(
        session_id=session_id,
        project_key=TEST_PROJECT_KEY,
        working_dir="/tmp",
        chat_id="test_chat_2144_resume",
        telegram_message_id=1,
        message_text=f"SDLC issue {issue_number}",
        sender_name="IntegrationTest2144",
    )
    monkeypatch.setenv("VALOR_SESSION_ID", session_id)
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    return session_id, ensure_session(issue_number=issue_number)


class TestResumeSelfHealEndToEnd:
    def test_no_run_id_marker_self_heals_after_lease_lapse(
        self, monkeypatch, tmp_path, issue_number, cleanup
    ):
        # Hermetic repo resolution (rung-0), and keep cwd off the live worktree.
        monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
        monkeypatch.chdir(tmp_path)

        # 1. Mint the run identity the way a live BUILD turn would.
        _session_id, result = _mint_identity(issue_number, monkeypatch)
        run_id = result["run_id"]
        assert run_id, result

        # The run_id is mirrored onto the durable session record — the ONLY
        # carrier that survives a resume for a bridge-originated pipeline.
        from tools._sdlc_utils import find_session_by_issue

        sess = find_session_by_issue(issue_number)
        assert sess is not None
        assert getattr(sess, "active_run_id", None) == run_id

        # 2. Simulate the killed-mid-BUILD resume: the lease and supervised-run
        #    signal lapse (TTL), and the run_id is gone from the resumed turn's
        #    conversation context.
        from agent.supervised_run import clear_supervised_run_signal
        from models.session_lifecycle import release_issue_lock

        assert release_issue_lock(issue_number, run_id) is True
        clear_supervised_run_signal(issue_number, run_id)

        # 3. The resumed turn writes its BUILD-completed marker with NO --run-id.
        code = _run_marker_main(
            [
                "sdlc-tool",
                "--stage",
                "BUILD",
                "--status",
                "completed",
                "--issue-number",
                str(issue_number),
            ]
        )
        assert code == 0, "resumed marker write must self-heal and exit 0"

        # 4. The marker LANDED via self-heal: the durable ledger reflects BUILD
        #    completed, with no manual session-ensure and no --run-id supplied.
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states.get("BUILD") == "completed", sm.states

        # Teardown: release the lock the heal re-acquired under the same run_id.
        release_issue_lock(issue_number, run_id)

    def test_stale_run_id_marker_self_heals_via_post_write_retry(
        self, monkeypatch, tmp_path, issue_number, cleanup
    ):
        """Regression for the guard gap (#2144): a resumed turn that STILL
        carries the now-stale ``--run-id`` after the lease lapsed must land its
        marker via the post-write heal + at-most-once retry — even though the
        re-established id equals the stale one (the SAME run's lapsed lease is
        re-acquired). This is the most common real manifestation of the bug
        (the ``session-ensure --reuse-run-id`` live-ops pattern). Before the
        guard was relaxed, ``maybe_heal_after_write`` suppressed the retry when
        the healed id equalled the prior id, so the marker silently refused.
        """
        monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
        monkeypatch.chdir(tmp_path)

        _session_id, result = _mint_identity(issue_number, monkeypatch)
        run_id = result["run_id"]
        assert run_id, result

        # Lease + signal lapse (TTL), but the resumed turn keeps the stale id.
        from agent.supervised_run import clear_supervised_run_signal
        from models.session_lifecycle import release_issue_lock

        assert release_issue_lock(issue_number, run_id) is True
        clear_supervised_run_signal(issue_number, run_id)

        # The first write refuses LEASE_ABSENT under the stale id; the post-write
        # heal re-acquires the SAME run's free lease and the single retry lands —
        # all inside this one CLI call, no manual session-ensure.
        code = _run_marker_main(
            [
                "sdlc-tool",
                "--stage",
                "TEST",
                "--status",
                "completed",
                "--issue-number",
                str(issue_number),
                "--run-id",
                run_id,
            ]
        )
        assert code == 0, "stale-run-id marker write must self-heal and exit 0"

        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(TEST_REPO_SLUG, issue_number)
        assert sm.states.get("TEST") == "completed", sm.states

        release_issue_lock(issue_number, run_id)


class TestDurableRunIdentityRebind:
    """Issue #2675: a re-ensure after a lapsed lease, against a session record
    that knows nothing, must REBIND to the existing run identity rather than
    mint a fresh one.

    This is the create-fall-through shape from ``ensure_session``'s resolution
    order: the session-side continuity record (``active_run_id`` /
    ``owned_run_ids``) is empty by construction, so every pre-#2675 proof is
    structurally unreachable and the run silently re-mints -- after which every
    write still carrying the old id is refused, and the next re-ensure lands on
    another empty history and re-mints again.

    Real Redis, real ledger, real issue lock. The only stubbed boundary is
    target-repo resolution (``GH_REPO``), as in the class above.
    """

    def _anchor_via_real_marker_write(self, issue_number, run_id):
        """Land a real stage marker so the run's identity is anchored the way
        production anchors it -- through ``resolve_ledger_lease`` confirming
        this run as the live lease owner."""
        code = _run_marker_main(
            [
                "sdlc-tool",
                "--stage",
                "PLAN",
                "--status",
                "completed",
                "--issue-number",
                str(issue_number),
                "--run-id",
                run_id,
            ]
        )
        assert code == 0, "setup marker write must land"

    def _reensure_on_a_forgetful_session(self, issue_number, monkeypatch, claim):
        """Drop every session-side carrier, then re-ensure carrying ``claim``.

        Deleting the session record forces ``ensure_session`` down its **create
        fall-through** -- the branch that produces a record with no
        ``active_run_id`` and an empty ``owned_run_ids``, which is precisely the
        state that makes the three session-side proofs unreachable. The cwd
        moves back to the repo root only because the create branch resolves the
        project/repo pairing from it.
        """
        from models.agent_session import AgentSession
        from tools.sdlc_session_ensure import ensure_session

        for s in AgentSession.query.all():
            if getattr(s, "project_key", None) == TEST_PROJECT_KEY:
                s.delete()
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.chdir(_REPO_ROOT)
        return ensure_session(issue_number=issue_number, reuse_run_id=claim)

    def test_lapsed_lease_rebinds_to_the_anchored_identity(
        self, monkeypatch, tmp_path, issue_number, cleanup
    ):
        from models.session_lifecycle import release_issue_lock

        monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
        monkeypatch.chdir(tmp_path)

        _session_id, result = _mint_identity(issue_number, monkeypatch)
        run_id = result["run_id"]
        assert run_id, result
        self._anchor_via_real_marker_write(issue_number, run_id)

        # The identity is on the DURABLE, issue-keyed anchor -- not just the
        # session record that is about to disappear.
        from agent.pipeline_ledger import read_run_identities

        assert run_id in read_run_identities(TEST_REPO_SLUG, issue_number)

        # The lease lapses and the session record is gone.
        assert release_issue_lock(issue_number, run_id) is True

        rebound = self._reensure_on_a_forgetful_session(issue_number, monkeypatch, run_id)

        assert rebound.get("run_id") == run_id, (
            f"re-ensure must rebind to the anchored identity, not mint a fresh one: {rebound}"
        )
        release_issue_lock(issue_number, run_id)

    def test_unanchored_claim_still_falls_through_to_a_fresh_mint(
        self, monkeypatch, tmp_path, issue_number, cleanup
    ):
        """NO-ADOPT CONTROL. A claim the anchor has never seen is not
        corroborated by anything, so the contest mints fresh -- the anchor
        widens what counts as self, it does not wave claims through."""
        from models.session_lifecycle import release_issue_lock

        monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
        monkeypatch.chdir(tmp_path)

        _session_id, result = _mint_identity(issue_number, monkeypatch)
        run_id = result["run_id"]
        self._anchor_via_real_marker_write(issue_number, run_id)
        assert release_issue_lock(issue_number, run_id) is True

        bogus = "deadbeefdeadbeefdeadbeefdeadbeef"
        minted = self._reensure_on_a_forgetful_session(issue_number, monkeypatch, bogus)

        assert minted.get("run_id") not in (bogus, None), minted
        assert minted["run_id"] != run_id, (
            f"the anchor must never hand a caller an identity it did not claim: {minted}"
        )
        release_issue_lock(issue_number, minted["run_id"])

    def test_foreign_live_holder_still_yields_issue_locked(
        self, monkeypatch, tmp_path, issue_number, cleanup
    ):
        """A live foreign lease still refuses, even though BOTH the claim and
        the live owner sit in the issue-keyed anchor. The anchor is never
        authority to take over a lock somebody else holds."""
        from models.session_lifecycle import release_issue_lock, touch_issue_lock

        monkeypatch.setenv("GH_REPO", TEST_REPO_SLUG)
        monkeypatch.chdir(tmp_path)

        _session_id, result = _mint_identity(issue_number, monkeypatch)
        run_id = result["run_id"]
        self._anchor_via_real_marker_write(issue_number, run_id)
        assert release_issue_lock(issue_number, run_id) is True

        # A genuinely different run takes the issue and anchors itself too.
        foreign = "f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0"
        assert touch_issue_lock(
            issue_number, foreign, session_id="sdlc-local-foreign", target_repo=TEST_REPO_SLUG
        ).acquired
        self._anchor_via_real_marker_write(issue_number, foreign)

        from agent.pipeline_ledger import read_run_identities

        anchored = read_run_identities(TEST_REPO_SLUG, issue_number)
        assert run_id in anchored and foreign in anchored, anchored

        blocked = self._reensure_on_a_forgetful_session(issue_number, monkeypatch, run_id)

        assert blocked.get("blocked") is True, blocked
        assert blocked.get("reason") == "ISSUE_LOCKED", blocked
        assert blocked.get("owner_run_id") == foreign, blocked
        release_issue_lock(issue_number, foreign)
