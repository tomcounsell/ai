"""Integration test for tools.sdlc_session_ensure bridge short-circuit.

Drives the headline dashboard claim for #1147: a bridge-initiated Eng session
with no issue_url (only message_text) must NOT produce a duplicate
``sdlc-local-{N}`` record when /sdlc Step 1.5 runs ``ensure_session``.

Uses real Popoto Redis writes (no mocks) to validate the end-to-end flow:
1. Create an Eng AgentSession mimicking bridge creation (session_type=eng,
   message_text="SDLC issue 9999", issue_url=None).
2. Set VALOR_SESSION_ID=<bridge_session_id>.
3. Invoke ensure_session(9999).
4. Assert: result reuses the bridge session id, created=False.
5. Assert: no ``sdlc-local-9999`` exists in Redis.

Cleanup happens in teardown via ``instance.delete()`` per CLAUDE.md's manual
testing hygiene rule — every test session is created with a recognizable
``project_key`` prefix and deleted through the Popoto ORM.

Issue #1267 (TestStageArtifactVerificationGate below): a second, unrelated
integration test drives ``tools.sdlc_next_skill.decide()`` end-to-end
(real Redis, real ``PipelineLedger``, real ``docs/plans/`` lookup, real
issue-lock peek) against a synthesized false BUILD-completion claim, and
asserts the router re-dispatches ``/do-build`` via guard ``g8`` rather than
advancing. The only mocked boundary is the live ``gh pr view`` call the
verification gate itself makes.
"""

from __future__ import annotations

import json
import random
from unittest.mock import MagicMock

import pytest

from models.agent_session import AgentSession

# Recognizable project_key prefix so teardown can scope cleanup narrowly and any
# leaked records are easy to spot on the dashboard.
TEST_PROJECT_KEY = "test-sdlc-ensure-int"

# Issue #1267: a synthetic, never-real GitHub owner/repo slug for the
# artifact-verification-gate integration test below. GH_REPO is set to this
# in-test so _resolve_target_repo() short-circuits at rung 0 (no live `gh
# repo view` call) -- the ONLY live boundary this test exercises is the `gh
# pr view` call the verification gate itself makes, and that is monkeypatched.
_G8_TEST_REPO_SLUG = "test-owner/test-repo-1267-g8"


@pytest.fixture
def cleanup_test_sessions():
    """Delete every AgentSession created under TEST_PROJECT_KEY before and after."""

    def _cleanup():
        try:
            stale = [
                s
                for s in AgentSession.query.all()
                if getattr(s, "project_key", None) == TEST_PROJECT_KEY
            ]
        except Exception:
            return
        for s in stale:
            try:
                s.delete()
            except Exception:
                pass

    _cleanup()
    yield
    _cleanup()


def test_bridge_short_circuit_produces_no_duplicate(monkeypatch, cleanup_test_sessions):
    """End-to-end: bridge Eng session + VALOR_SESSION_ID => no sdlc-local-N duplicate."""
    from tools.sdlc_session_ensure import ensure_session

    bridge_session_id = "tg_valor_test_9999"

    # Create a bridge-style Eng session the way the Telegram bridge would.
    bridge_session = AgentSession.create_eng(
        session_id=bridge_session_id,
        project_key=TEST_PROJECT_KEY,
        working_dir="/tmp",
        chat_id="test_chat_9999",
        telegram_message_id=1,
        message_text="SDLC issue 9999",
        sender_name="IntegrationTest",
    )

    # Transition to running so it looks like a live worker turn.
    try:
        from models.session_lifecycle import transition_status

        transition_status(bridge_session, "running", "integration test setup")
    except Exception:
        # Not critical for this test — the short-circuit still activates as long
        # as status is non-terminal, and "pending" is non-terminal.
        pass

    # Simulate what agent/sdk_client.py does for bridge-initiated sessions.
    monkeypatch.setenv("VALOR_SESSION_ID", bridge_session_id)
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

    result = ensure_session(issue_number=9999)

    # The short-circuit must return the bridge session id and NOT create a new
    # sdlc-local-9999 record. It also mints the run identity (#2003).
    assert result["session_id"] == bridge_session_id
    assert result["created"] is False
    assert result["run_id"]

    # Confirm via direct Popoto query: the duplicate zombie must not exist.
    zombie = list(AgentSession.query.filter(session_id="sdlc-local-9999"))
    assert zombie == [], (
        "ensure_session must NOT create sdlc-local-9999 when "
        "VALOR_SESSION_ID points at a live Eng session"
    )

    # And there should be exactly one Eng session in our test project_key.
    eng_sessions = [
        s
        for s in AgentSession.query.all()
        if getattr(s, "project_key", None) == TEST_PROJECT_KEY
        and getattr(s, "session_type", None) == "eng"
    ]
    assert len(eng_sessions) == 1
    assert eng_sessions[0].session_id == bridge_session_id


def test_ownerless_bridge_session_adopted_no_duplicate(monkeypatch, cleanup_test_sessions):
    """WS-F (#2026) live trigger: a bridge PM eng session built from the BARE
    "SDLC N" form (no literal word "issue", so the message_text regex misses)
    with issue_url=None + AGENT_SESSION_ID set is ADOPTED — no sdlc-local-N is
    minted, and the PM session ends up holding the issue lock + supervised-run
    signal under the returned run_id.

    This reproduces the observed "SDLC 1312" case end-to-end through real Redis,
    closing the gap the synthetic unit tests miss (critique concern #5).
    """
    from agent.supervised_run import (
        clear_supervised_run_signal,
        read_supervised_run_signal,
    )
    from models.session_lifecycle import release_issue_lock, touch_issue_lock
    from tools.sdlc_session_ensure import ensure_session

    issue_number = 700200
    bridge_session_id = "tg_valor_test_wsf_700200"

    bridge_session = AgentSession.create_eng(
        session_id=bridge_session_id,
        project_key=TEST_PROJECT_KEY,
        working_dir="/tmp",
        chat_id="test_chat_wsf",
        telegram_message_id=1,
        # BARE form — NO literal "issue", so find_session_by_issue's message_text
        # regex would miss; only adoption prevents the duplicate mint.
        message_text=f"SDLC {issue_number}",
        sender_name="IntegrationTest",
    )
    # issue_url stays None — the ownerless bridge case.
    assert getattr(bridge_session, "issue_url", None) in (None, "")

    try:
        from models.session_lifecycle import transition_status

        transition_status(bridge_session, "running", "integration test setup")
    except Exception:
        pass

    monkeypatch.setenv("AGENT_SESSION_ID", bridge_session_id)
    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

    result = ensure_session(
        issue_number=issue_number,
        issue_url=f"https://github.com/tomcounsell/ai/issues/{issue_number}",
    )
    run_id = result.get("run_id")

    try:
        # Adopted, not minted.
        assert result["session_id"] == bridge_session_id
        assert result["created"] is False
        assert run_id

        # No competing sdlc-local-N record.
        zombie = list(AgentSession.query.filter(session_id=f"sdlc-local-{issue_number}"))
        assert zombie == [], "adoption must not mint sdlc-local-N for an ownerless bridge session"

        # Exactly one eng session in the test project.
        eng_sessions = [
            s
            for s in AgentSession.query.all()
            if getattr(s, "project_key", None) == TEST_PROJECT_KEY
            and getattr(s, "session_type", None) == "eng"
        ]
        assert len(eng_sessions) == 1
        assert eng_sessions[0].session_id == bridge_session_id

        # issue_url stamped on the adopted PM session (best-effort findability).
        persisted = list(AgentSession.query.filter(session_id=bridge_session_id))[0]
        assert persisted.issue_url == f"https://github.com/tomcounsell/ai/issues/{issue_number}"

        # PM session holds the issue lock under the returned run_id.
        peek = touch_issue_lock(issue_number, None, peek=True)
        assert peek.owner_run_id == run_id

        # Supervised-run signal was published against the run_id.
        signal = read_supervised_run_signal(issue_number, working_dir="/tmp")
        assert signal and signal.get("run_id") == run_id
    finally:
        # Free the issue lock + signal so the test leaves no live-lease residue.
        release_issue_lock(issue_number, run_id)
        clear_supervised_run_signal(issue_number, run_id, working_dir="/tmp")


def test_b2_injected_env_shape_adopts_ownerless_session_no_duplicate(
    monkeypatch, cleanup_test_sessions
):
    """Issue #2190, Seam B2: exercises the EXACT env shape
    ``agent/session_executor.py``'s ``_harness_env`` now produces --
    ``VALOR_SESSION_ID=<session.session_id>`` AND
    ``AGENT_SESSION_ID=<session.agent_session_id>`` (a genuine, distinct hex,
    not a session_id-shaped stand-in) -- against a live, ownerless bridge PM
    session built from bare "SDLC N" text (no issue_url stamped).

    Prior WS-F integration coverage (``test_ownerless_bridge_session_adopted_no_duplicate``
    above) injects only ``AGENT_SESSION_ID`` set to a session_id-shaped
    string, which is not the real production shape. This test closes that
    gap: it asserts adoption succeeds via the real hex ``agent_session_id``
    fixture and BOTH env vars set, end-to-end (real Redis, real
    find_session/find_session_by_issue, real issue lock + supervised-run
    signal), with zero ``sdlc-local-<N>`` mint.
    """
    from agent.supervised_run import (
        clear_supervised_run_signal,
        read_supervised_run_signal,
    )
    from models.session_lifecycle import release_issue_lock, touch_issue_lock
    from tools.sdlc_session_ensure import ensure_session

    issue_number = 700201
    bridge_session_id = "tg_valor_test_b2_700201"

    bridge_session = AgentSession.create_eng(
        session_id=bridge_session_id,
        project_key=TEST_PROJECT_KEY,
        working_dir="/tmp",
        chat_id="test_chat_b2",
        telegram_message_id=1,
        message_text=f"SDLC {issue_number}",
        sender_name="IntegrationTestB2",
    )
    assert getattr(bridge_session, "issue_url", None) in (None, "")
    # The B2 injection contract: agent_session_id is the Popoto AutoKey hex,
    # distinct from session_id -- the exact namespace mismatch #2190 fixes.
    assert bridge_session.agent_session_id != bridge_session_id

    try:
        from models.session_lifecycle import transition_status

        transition_status(bridge_session, "running", "integration test setup")
    except Exception:
        pass

    # Mirror agent/session_executor.py's _harness_env construction exactly.
    monkeypatch.setenv("VALOR_SESSION_ID", bridge_session.session_id)
    monkeypatch.setenv("AGENT_SESSION_ID", bridge_session.agent_session_id)

    result = ensure_session(
        issue_number=issue_number,
        issue_url=f"https://github.com/tomcounsell/ai/issues/{issue_number}",
    )
    run_id = result.get("run_id")

    try:
        assert result["session_id"] == bridge_session_id
        assert result["created"] is False
        assert run_id

        zombie = list(AgentSession.query.filter(session_id=f"sdlc-local-{issue_number}"))
        assert zombie == [], "B2-shaped adoption must not mint sdlc-local-N"

        eng_sessions = [
            s
            for s in AgentSession.query.all()
            if getattr(s, "project_key", None) == TEST_PROJECT_KEY
            and getattr(s, "session_type", None) == "eng"
        ]
        assert len(eng_sessions) == 1
        assert eng_sessions[0].session_id == bridge_session_id

        persisted = list(AgentSession.query.filter(session_id=bridge_session_id))[0]
        assert persisted.issue_url == f"https://github.com/tomcounsell/ai/issues/{issue_number}"

        peek = touch_issue_lock(issue_number, None, peek=True)
        assert peek.owner_run_id == run_id

        signal = read_supervised_run_signal(issue_number, working_dir="/tmp")
        assert signal and signal.get("run_id") == run_id
    finally:
        release_issue_lock(issue_number, run_id)
        clear_supervised_run_signal(issue_number, run_id, working_dir="/tmp")


def test_new_anchor_session_created_with_is_ledger_true(monkeypatch, cleanup_test_sessions):
    """Non-executable ledger flag (#2042), real Popoto Redis, end-to-end.

    When ensure_session() falls all the way through to the create-new-session
    branch (no env session, no existing issue-scoped session), the freshly
    persisted ``sdlc-local-{N}`` row must carry ``is_ledger=True`` -- proving
    the flag survives the real create_local()/save() round-trip through
    Redis, not just that it was passed as a kwarg in a mocked call.
    """
    from tools.sdlc_session_ensure import ensure_session

    monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    # Bypass real project->repo resolution (immutable pairing via
    # projects.json) so this test does not depend on the local machine's
    # config -- point both at the recognizable test project_key/dir.
    monkeypatch.setattr("tools.valor_session.resolve_project_key", lambda cwd: TEST_PROJECT_KEY)
    monkeypatch.setattr(
        "tools.valor_session._resolve_project_working_directory",
        lambda project_key: ("/tmp", {}),
    )

    issue_number = 700001
    result = ensure_session(issue_number=issue_number)

    assert result["created"] is True
    expected_session_id = f"sdlc-local-{issue_number}"
    assert result["session_id"] == expected_session_id

    # Real Redis readback -- confirms the flag round-tripped through
    # create_local()'s single save(), not a follow-up write.
    from agent.session_pickup import _truthy

    persisted = list(AgentSession.query.filter(session_id=expected_session_id))
    assert len(persisted) == 1
    assert persisted[0].project_key == TEST_PROJECT_KEY
    assert _truthy(persisted[0].is_ledger), (
        "newly created sdlc-local anchor must have is_ledger=True on the very first persisted row"
    )


class TestStageArtifactVerificationGate:
    """Issue #1267: end-to-end proof that a false BUILD-completion claim
    re-dispatches ``/do-build`` (guard ``g8``) instead of the router
    advancing on the self-attested marker alone.

    Real Redis, real ``PipelineLedger`` storage, real issue-lock peek, real
    ``docs/plans/`` lookup (a throwaway high issue number matches nothing).
    The single mocked boundary is the live ``gh pr view`` call --
    synthesizing "the marker claims PR #N is open, but live GitHub says it
    is CLOSED".
    """

    @staticmethod
    def _fake_gh_pr_view(cmd, **kwargs):
        proc = MagicMock()
        if cmd[:3] == ["gh", "pr", "view"]:
            proc.returncode = 0
            json_arg = cmd[cmd.index("--json") + 1] if "--json" in cmd else ""
            if json_arg == "state":
                # tools.sdlc_next_skill._fetch_pr_state's live check.
                proc.stdout = json.dumps({"state": "CLOSED"})
            else:
                # tools.sdlc_stage_query._fetch_pr_merge_state's G6 check --
                # unrelated to this test, answered harmlessly.
                proc.stdout = json.dumps({"mergeStateStatus": "BLOCKED", "statusCheckRollup": []})
        else:
            proc.returncode = 1
            proc.stdout = ""
        return proc

    @staticmethod
    def _fake_gh_pr_view_merged(cmd, **kwargs):
        """#1267 g8 merged-pipeline misfire: live GitHub says the PR is
        MERGED (branch already deleted under a delete-branch-on-merge
        policy) -- the polar opposite fixture of ``_fake_gh_pr_view`` above.
        """
        proc = MagicMock()
        if cmd[:3] == ["gh", "pr", "view"]:
            proc.returncode = 0
            json_arg = cmd[cmd.index("--json") + 1] if "--json" in cmd else ""
            if json_arg == "state":
                # tools.sdlc_next_skill._fetch_pr_state's live check.
                proc.stdout = json.dumps({"state": "MERGED"})
            else:
                # tools.sdlc_stage_query._fetch_pr_merge_state's G6 check --
                # unrelated to this test (row 10 does not consult
                # pr_merge_state), answered harmlessly.
                proc.stdout = json.dumps({"mergeStateStatus": "UNKNOWN", "statusCheckRollup": []})
        elif cmd[:2] == ["git", "ls-remote"]:
            # The PATCH branch-pushed check must never even run here -- a
            # MERGED PR short-circuits it. If it does run, answer "branch
            # gone" (empty stdout) to prove the merged-state skip is load
            # bearing, not accidentally passing because the branch is still
            # present.
            proc.returncode = 0
            proc.stdout = ""
        else:
            proc.returncode = 1
            proc.stdout = ""
        return proc

    @pytest.fixture
    def issue_number(self):
        """A fresh, never-real high issue number per test run -- matches no
        real plan doc and holds no pre-existing issue lock or ledger."""
        return 2_000_000 + random.randint(0, 999)

    @pytest.fixture
    def cleanup_ledger(self, issue_number):
        def _cleanup():
            try:
                from agent.pipeline_ledger import PipelineLedger

                key = f"{_G8_TEST_REPO_SLUG}:{issue_number}"
                for rec in PipelineLedger.query.filter(ledger_key=key):
                    rec.delete()
            except Exception:
                pass

        _cleanup()
        yield
        _cleanup()

    def test_g8_redispatches_build_on_synthesized_false_pr_claim(
        self, monkeypatch, issue_number, cleanup_ledger
    ):
        from agent.pipeline_ledger import PipelineLedger
        from tools import sdlc_next_skill

        # Rung-0 short-circuit: no live `gh repo view` call for repo resolution.
        monkeypatch.setenv("GH_REPO", _G8_TEST_REPO_SLUG)
        monkeypatch.setenv("VALOR_SESSION_ID", "")
        monkeypatch.setenv("AGENT_SESSION_ID", "")
        monkeypatch.setattr("subprocess.run", self._fake_gh_pr_view)

        # Synthesize the false claim directly on the durable ledger: BUILD
        # marked completed, PR #918273 self-attested as the artifact -- but
        # (per the monkeypatched gh call above) live GitHub says it's CLOSED.
        ledger = PipelineLedger.get_or_create(_G8_TEST_REPO_SLUG, issue_number)
        ledger.stage_states_json = json.dumps({"BUILD": "completed"})
        ledger.pr_number = 918273
        ledger.save()

        result = sdlc_next_skill.decide(issue_number=issue_number)

        assert result.get("dispatched") is True, result
        assert result["skill"] == "/do-build", result
        assert result["row_id"] == "G8", result

    def test_terminal_merged_pipeline_routes_to_merge_not_build(
        self, monkeypatch, issue_number, cleanup_ledger
    ):
        """#1267 regression: a terminal MERGED pipeline must route to the
        terminal ``/do-merge`` dispatch (row 10), never re-dispatch
        ``/do-build`` via guard g8.

        Before the fix, ``_verify_stage_artifacts_live`` treated "BUILD
        artifact verified" as strictly ``state == "OPEN"``, so an already
        MERGED PR (state MERGED, branch deleted) was flagged as an
        unverified BUILD claim -- g8 fired and re-dispatched ``/do-build``
        on an issue that had already shipped (duplicate-PR risk). This
        drives the real ``tools.sdlc_next_skill.decide()`` path end-to-end
        (real Redis ledger, real guard/dispatch-rule evaluation) with every
        stage through DOCS marked completed and a live ``gh pr view``
        response of MERGED.
        """
        from agent.pipeline_ledger import PipelineLedger
        from tools import sdlc_next_skill

        monkeypatch.setenv("GH_REPO", _G8_TEST_REPO_SLUG)
        monkeypatch.setenv("VALOR_SESSION_ID", "")
        monkeypatch.setenv("AGENT_SESSION_ID", "")
        monkeypatch.setattr("subprocess.run", self._fake_gh_pr_view_merged)

        ledger = PipelineLedger.get_or_create(_G8_TEST_REPO_SLUG, issue_number)
        ledger.stage_states_json = json.dumps(
            {
                "ISSUE": "completed",
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "completed",
                "TEST": "completed",
                "REVIEW": "completed",
                "DOCS": "completed",
            }
        )
        ledger.pr_number = 918274
        ledger.save()

        result = sdlc_next_skill.decide(issue_number=issue_number)

        assert result.get("dispatched") is True, result
        assert result["skill"] == "/do-merge", result
        assert result["row_id"] == "10", result
