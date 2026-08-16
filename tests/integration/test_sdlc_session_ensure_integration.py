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

Issue #1267 / #2757 (TestStageArtifactVerificationGate below): a second,
unrelated group of integration tests drives ``tools.sdlc_next_skill.decide()``
end-to-end (real Redis, real ``PipelineLedger``, real ``docs/plans/`` lookup,
real issue-lock peek) across the three states the artifact gate can be in. The
only mocked boundary is the live ``gh``/``git`` calls the gate itself makes.

- **Falsified** -- a synthesized false BUILD-completion claim (the PR number
  is recorded, live GitHub says ``CLOSED``): the router must re-dispatch
  ``/do-build`` via guard ``g8`` rather than advance on the marker alone.
- **Verified** -- a terminal pipeline whose recorded PR is ``MERGED``: g8 stays
  silent and the router reaches the terminal ``/do-merge`` row.
- **Unverifiable** (#2757) -- BUILD claims completed but no PR number resolves
  in any state: there is no identifier to check, so g8 must not manufacture a
  verdict from a lookup it never performed. Whichever row then owns the state
  owns it; g8 is not that row.
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

# The head commit of the merged PR in the terminal-pipeline fixture below.
# Since #2062 WS3d the router only reaches the terminal row when the recorded
# REVIEW verdict attributes to the PR's CURRENT head, so this one 40-hex value
# has to appear in three places at once: the verdict record's ``head_sha``, the
# `git ls-remote refs/pull/N/head` answer, and the `gh pr view --json
# headRefOid` cross-check. Naming it once keeps them from drifting apart.
_G8_MERGED_PR_HEAD_SHA = "4f2b9c1d8e37a05614bd2ce9f80a71d3c6e5b492"


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
    """Issues #1267 / #2757: end-to-end proof that guard ``g8`` fires on a
    **falsified** BUILD-completion claim and stays silent on a **verified**
    or **unverifiable** one.

    Real Redis, real ``PipelineLedger`` storage, real issue-lock peek, real
    ``docs/plans/`` lookup (a throwaway high issue number matches nothing).
    The mocked boundary is ``subprocess.run``, i.e. the live ``gh``/``git``
    reads the gate and the router's context builder make; each test supplies
    the fake describing the world it is asserting about.
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

        Two ``git ls-remote`` shapes are answered, and they answer
        DIFFERENTLY on purpose -- do not collapse them:

        - ``--heads origin <branch>`` (``_check_branch_pushed``) -> empty,
          i.e. "branch gone". The PATCH branch probe must never reach this
          on a MERGED PR; answering "gone" is what proves the merged-state
          skip is load bearing rather than accidentally satisfied by a
          branch that still exists.
        - ``origin refs/pull/N/head`` (``tools.pr_head_resolver``) -> the
          PR's head SHA. That resolver is git-FIRST (#2404), so without
          this leg -- and without the ``git remote get-url origin`` answer
          its cross-repo guard needs -- ``context["pr_head_sha"]`` lands on
          the fail-closed empty sentinel and the verdict reads stale, which
          routes to row 8f (``/do-pr-review``) instead of row 10.
        """
        proc = MagicMock()
        if cmd[:3] == ["gh", "pr", "view"]:
            proc.returncode = 0
            json_arg = cmd[cmd.index("--json") + 1] if "--json" in cmd else ""
            if json_arg == "state":
                # tools.sdlc_next_skill._fetch_pr_state's live check.
                proc.stdout = json.dumps({"state": "MERGED"})
            elif json_arg == "headRefOid":
                # tools.pr_head_resolver._gh_pr_head's cross-check leg. It
                # must AGREE with the git answer below, or the resolver logs
                # a disagreement and the test would be asserting on a
                # scenario it did not mean to construct.
                proc.stdout = _G8_MERGED_PR_HEAD_SHA
            else:
                # tools.sdlc_stage_query._fetch_pr_merge_state's G6 check --
                # unrelated to this test (row 10 does not consult
                # pr_merge_state), answered harmlessly.
                proc.stdout = json.dumps({"mergeStateStatus": "UNKNOWN", "statusCheckRollup": []})
        elif cmd[:3] == ["git", "remote", "get-url"]:
            # tools.pr_head_resolver._origin_matches_repo: the authoritative
            # git read is skipped entirely unless origin points at the repo
            # the PR lives in.
            proc.returncode = 0
            proc.stdout = f"https://github.com/{_G8_TEST_REPO_SLUG}.git\n"
        elif cmd[:2] == ["git", "ls-remote"] and any("refs/pull/" in str(a) for a in cmd):
            proc.returncode = 0
            proc.stdout = f"{_G8_MERGED_PR_HEAD_SHA}\t{cmd[-1]}\n"
        elif cmd[:2] == ["git", "ls-remote"]:
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
        """A MERGED PR is an ACCEPTABLE BUILD artifact, and a terminal
        pipeline holding one routes to ``/do-merge`` (row 10).

        This is the repo's only end-to-end proof of that proposition, and
        the ``gh pr view --json state`` -> ``MERGED`` answer is what it
        turns on: ``_verify_stage_artifacts_live`` accepts ``OPEN`` **or**
        ``MERGED`` for BUILD, so guard g8 stays silent and the router is
        free to reach its terminal row. No ``MERGE`` marker is set,
        deliberately -- one would short-circuit the routing before either
        the ``gh`` or the ``git`` fake was consulted and leave the whole
        fixture dead.

        Two preconditions of row 10 are supplied explicitly because the
        router will not reach it without them (#2062 WS3a/WS3d, which is
        what left this test red on ``main`` -- it long predates the verdict
        requirement and had never been updated for it):

        - a **recorded APPROVED REVIEW verdict** -- without it
          ``_rule_ready_to_merge`` is unreachable and row 8e
          (``/do-pr-review``, "REVIEW completed without a recorded
          verdict") claims the tick first;
        - that verdict's ``head_sha`` **matching the live PR head** --
          an unattributable or mismatched verdict is stale, and the tick
          goes to row 8f instead.

        Neither is incidental scaffolding: they are the conditions under
        which "terminal" is a true description of the pipeline.
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
                # #2769 record shape: the head SHA is its own field, not a
                # trailer inside the verdict token.
                "_verdicts": {
                    "REVIEW": {
                        "verdict": "APPROVED",
                        "head_sha": _G8_MERGED_PR_HEAD_SHA,
                    }
                },
            }
        )
        ledger.pr_number = 918274
        ledger.save()

        result = sdlc_next_skill.decide(issue_number=issue_number)

        assert result.get("dispatched") is True, result
        assert result["skill"] == "/do-merge", result
        assert result["row_id"] == "10", result

    @staticmethod
    def _fake_no_pr_anywhere(slug: str, branch_exists: bool):
        """Build a ``subprocess.run`` fake for "this issue has no PR in any state".

        Answers every ``gh pr list`` (both the ``#N`` body search and the
        ``--head <branch>`` fallback, under every ``--state`` value) with an
        empty list, so ``_compute_meta`` cannot resolve a ``pr_number`` by any
        route. ``git branch -a`` answers according to ``branch_exists``, which
        is the sole producer of the router's row-5 input.
        """
        branches = "  remotes/origin/main\n"
        if branch_exists:
            branches += f"  remotes/origin/session/{slug}\n"

        def _fake(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            if cmd[:3] == ["gh", "pr", "list"]:
                proc.stdout = "[]"
            elif cmd[:2] == ["git", "branch"]:
                proc.stdout = branches
            elif cmd[:2] == ["git", "ls-remote"]:
                proc.stdout = ""
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        return _fake

    @pytest.mark.parametrize("branch_exists", [False, True])
    def test_no_pr_number_recorded_does_not_redispatch_build(
        self, monkeypatch, issue_number, cleanup_ledger, branch_exists
    ):
        """#2757: a BUILD claim whose PR number is *unresolvable* must never be
        adjudicated as a *falsified* claim by guard g8.

        "There is no identifier to look up" is not evidence the claim is false
        -- it is evidence the check could not be performed. Today g8 collapses
        the two and re-dispatches ``/do-build`` on the strength of a lookup it
        never made.

        No ``MERGE`` marker is set, deliberately: this test must pin the
        identifiability guard itself, not any terminal short-circuit.

        The correct outcome differs by ``branch_exists``, and both halves are
        asserted:

        - ``False`` -> no ``/do-build`` at **any** row (measured:
          ``Blocked(NO_RULE)``).
        - ``True``  -> ``/do-build`` at **row 5**, never at ``G8``.
          ``_rule_branch_exists_no_pr`` is the correct owner of "a branch
          exists with no PR, so build must create the PR" for a lane that
          genuinely never opened one; that row is deliberately out of fence
          and must not be "fixed". The row-id assertion is what proves the
          guard stopped g8 from manufacturing a verdict.
        """
        from agent.pipeline_ledger import PipelineLedger
        from tools import sdlc_next_skill

        monkeypatch.setenv("GH_REPO", _G8_TEST_REPO_SLUG)
        monkeypatch.setenv("VALOR_SESSION_ID", "")
        monkeypatch.setenv("AGENT_SESSION_ID", "")

        slug = f"sdlc-{issue_number}"
        monkeypatch.setattr("subprocess.run", self._fake_no_pr_anywhere(slug, branch_exists))

        ledger = PipelineLedger.get_or_create(_G8_TEST_REPO_SLUG, issue_number)
        ledger.stage_states_json = json.dumps(
            {
                "ISSUE": "completed",
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "completed",
            }
        )
        ledger.pr_number = None
        ledger.slug = slug
        ledger.save()

        result = sdlc_next_skill.decide(issue_number=issue_number)

        # decide() catches every exception and returns {"error": ...}, which
        # would satisfy a negative-only assertion -- pin the absence first.
        assert "error" not in result, result

        if branch_exists:
            assert result.get("dispatched") is True, result
            assert result["skill"] == "/do-build", result
            assert result["row_id"] == "5", result
        else:
            assert result.get("skill") != "/do-build", result
            assert result.get("blocked") is True, result
            assert result.get("guard_id") == "NO_RULE", result

    def test_empty_ledger_with_merged_pr_does_not_redispatch_build(
        self, monkeypatch, issue_number, cleanup_ledger, cleanup_test_sessions
    ):
        """#2757 end-to-end reproduction, through the durability-recovery path.

        This is the shape the three reported issues were actually in: the
        ``PipelineLedger`` reads *entirely* empty, so ``decide()`` reconstructs
        ``stage_states`` from durable signals -- which read ``("open", "all")``
        and therefore *do* see the merged PR, setting ``BUILD = completed`` --
        while ``_compute_meta`` resolves ``pr_number`` under ``state="open"``
        only and comes back with ``None``. A merged pipeline is then told to
        rebuild the work it just shipped.
        """
        from tools import sdlc_next_skill

        monkeypatch.setenv("GH_REPO", _G8_TEST_REPO_SLUG)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        slug = f"sdlc-{issue_number}"
        branch = f"session/{slug}"
        pr_number = 918275
        pr_body = f"Closes #{issue_number}"

        session_id = f"tg_valor_{TEST_PROJECT_KEY}_{issue_number}"
        session = AgentSession(
            session_id=session_id,
            project_key=TEST_PROJECT_KEY,
            session_type="eng",
            chat_id=f"test_chat_2757_{issue_number}",
            issue_url=f"https://github.com/{_G8_TEST_REPO_SLUG}/issues/{issue_number}",
            status="running",
            slug=slug,
        )
        session.save()
        monkeypatch.setenv("VALOR_SESSION_ID", session_id)

        def _fake(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            if cmd[:3] == ["gh", "pr", "list"]:
                state = cmd[cmd.index("--state") + 1] if "--state" in cmd else "open"
                # The PR is MERGED: invisible under `open`, visible otherwise.
                if state == "open":
                    proc.stdout = "[]"
                else:
                    proc.stdout = json.dumps(
                        [
                            {
                                "number": pr_number,
                                "body": pr_body,
                                "headRefName": branch,
                                "state": "MERGED",
                            }
                        ]
                    )
            elif cmd[:3] == ["gh", "pr", "view"]:
                json_arg = cmd[cmd.index("--json") + 1] if "--json" in cmd else ""
                if json_arg == "state":
                    proc.stdout = json.dumps({"state": "MERGED"})
                elif json_arg == "statusCheckRollup":
                    proc.stdout = json.dumps({"statusCheckRollup": []})
                else:
                    proc.stdout = json.dumps(
                        {"mergeStateStatus": "UNKNOWN", "statusCheckRollup": []}
                    )
            elif cmd[:2] == ["git", "branch"]:
                proc.stdout = f"  remotes/origin/{branch}\n"
            elif cmd[:2] == ["git", "ls-remote"]:
                proc.stdout = ""
            else:
                proc.returncode = 1
                proc.stdout = ""
            return proc

        monkeypatch.setattr("subprocess.run", _fake)

        result = sdlc_next_skill.decide(issue_number=issue_number)

        assert "error" not in result, result
        assert result.get("skill") != "/do-build", result


class TestSelfLockPeekIdentityEndToEnd:
    """Issue #2766: a supervisor that mints its own issue lock via a real
    ``session-ensure`` must never be told to stand down for that same lock on
    its first ``next-skill`` call, even when its session record is invisible
    to ``find_session_by_issue`` (terminal status, or non-``eng``
    ``session_type``).

    This is the demonstrated-red proof named in the plan's Success Criteria:
    a red produced only by ``patch("tools._sdlc_utils.find_session_by_issue",
    return_value=None)`` proves the mock, not the bug. These tests instead
    drive a REAL ``ensure_session`` (real Redis lock, real session record),
    then mutate the real record so the real, unmodified
    ``find_session_by_issue`` filters legitimately exclude it -- reproducing
    the two structurally ordinary conditions named in the plan's Problem
    section, not a stubbed lookup.
    """

    @pytest.fixture
    def cleanup_lock(self):
        from models.session_lifecycle import release_issue_lock

        issue_numbers: list[int] = []

        def _register(n: int) -> int:
            issue_numbers.append(n)
            return n

        yield _register
        for n in issue_numbers:
            try:
                release_issue_lock(n, None)
            except Exception:
                pass

    def _mint_self_lock(self, monkeypatch, cleanup_test_sessions, cleanup_lock, issue_number):
        """Real session-ensure: mints a run_id and acquires the real issue
        lock under it. Returns (session_id, run_id)."""
        from tools.sdlc_session_ensure import ensure_session

        cleanup_lock(issue_number)
        session_id = f"tg_valor_test_selflock_{issue_number}"

        bridge_session = AgentSession.create_eng(
            session_id=session_id,
            project_key=TEST_PROJECT_KEY,
            working_dir="/tmp",
            chat_id=f"test_chat_selflock_{issue_number}",
            telegram_message_id=1,
            message_text=f"SDLC issue {issue_number}",
            sender_name="IntegrationTest",
        )
        from models.session_lifecycle import transition_status

        transition_status(bridge_session, "running", "integration test setup")

        monkeypatch.setenv("VALOR_SESSION_ID", session_id)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        result = ensure_session(issue_number=issue_number)
        assert result["session_id"] == session_id
        run_id = result["run_id"]
        assert run_id
        return session_id, run_id

    def test_self_lock_survives_terminal_session_with_run_id(
        self, monkeypatch, cleanup_test_sessions, cleanup_lock
    ):
        """Session goes terminal (a stall advisory / reaper race, the plan's
        Race 2) while the lock the same run minted is still live. Without
        ``--run-id``, the caller has no way to avoid the self-block. With
        ``--run-id``, the peek succeeds under the caller's own stated
        identity, never consulting the session lookup at all."""
        from tools import sdlc_next_skill
        from tools._sdlc_utils import find_session_by_issue

        issue_number = 2_100_000 + random.randint(0, 999)
        session_id, run_id = self._mint_self_lock(
            monkeypatch, cleanup_test_sessions, cleanup_lock, issue_number
        )

        session = next(iter(AgentSession.query.filter(session_id=session_id)))
        session.status = "completed"
        session.save()

        # The #1915 terminal filter is doing exactly its job here -- the
        # session is genuinely invisible to the inference path.
        assert find_session_by_issue(issue_number) is None

        # Existing inference-path behavior (preserved, unchanged): with no
        # stated identity the caller self-blocks on its own lock.
        blocked = sdlc_next_skill.decide(issue_number=issue_number)
        assert blocked.get("blocked") is True, blocked
        assert blocked.get("reason") == "ISSUE_LOCKED", blocked
        assert blocked.get("owner_run_id") == run_id, blocked
        assert blocked.get("orphaned_lock") is False, blocked

        # The fix: a caller-stated --run-id peeks under its own identity and
        # never self-blocks.
        result = sdlc_next_skill.decide(issue_number=issue_number, run_id=run_id)
        assert result.get("blocked") is not True, result

    def test_self_lock_survives_non_eng_session_with_run_id(
        self, monkeypatch, cleanup_test_sessions, cleanup_lock
    ):
        """Session's type flips away from ``eng`` while the lock the same run
        minted is still live. Same self-block/fix shape as the terminal
        case, over the other ``find_session_by_issue`` exclusion axis.

        ``session_type`` is a Popoto KeyField, and in-place KeyField mutation
        (``save(migrate_key=True)``) leaves the pre-migration key behind
        instead of removing it (a separate, out-of-scope Popoto behavior) --
        so this simulates the flip by deleting the eng record and recreating
        an equivalent non-eng one under the same session_id/issue_url. The
        real lock (minted by the real ``ensure_session`` call above) is left
        untouched throughout."""
        from tools import sdlc_next_skill
        from tools._sdlc_utils import find_session_by_issue

        issue_number = 2_101_000 + random.randint(0, 999)
        session_id, run_id = self._mint_self_lock(
            monkeypatch, cleanup_test_sessions, cleanup_lock, issue_number
        )

        session = next(iter(AgentSession.query.filter(session_id=session_id)))
        issue_url = session.issue_url
        chat_id = session.chat_id
        session.delete()
        AgentSession(
            session_id=session_id,
            project_key=TEST_PROJECT_KEY,
            session_type="dev",
            chat_id=chat_id,
            issue_url=issue_url,
            status="running",
        ).save()

        assert find_session_by_issue(issue_number) is None

        blocked = sdlc_next_skill.decide(issue_number=issue_number)
        assert blocked.get("blocked") is True, blocked
        assert blocked.get("reason") == "ISSUE_LOCKED", blocked
        assert blocked.get("owner_run_id") == run_id, blocked
        assert blocked.get("orphaned_lock") is False, blocked

        result = sdlc_next_skill.decide(issue_number=issue_number, run_id=run_id)
        assert result.get("blocked") is not True, result
