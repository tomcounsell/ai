"""E2E: a stalled lane's steer really lands on ``steering:{session_id}`` (#2696, #2755).

The lane here is deliberately **human-named**, so the issue resolution ladder
must resolve it from recorded identity for any of this to work at all.

The unit suite fences ``steer_session`` and ``AgentSession.query`` at the
boundary. This test does not: it saves a real ``AgentSession`` through the ORM,
runs the real reflection body against real Redis, and reads the steering inbox
back with ``peek_steering_messages`` — the same API the worker's turn-boundary
drain uses. That closes the one gap a fenced unit test structurally cannot: that
the reflection's chosen target, the executor's ledger/terminal guards, and the
steering queue key all agree on the same session.

Only the three inputs the reflection has no business owning are stubbed: ``gh``
(open PR list), ``gh issue view`` (issue state), and ``git log`` (head sha and
age), plus the machine-ownership gate, since a test machine does not own the
fixture project. Everything downstream of those runs for real.

Redis isolation comes from the autouse ``redis_test_db`` fixture; sessions carry
a ``test-`` ``project_key`` prefix and are deleted through the ORM in teardown,
never with raw Redis (CLAUDE.md testing hygiene).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from agent.pipeline_ledger import PipelineLedger
from agent.steering import peek_steering_messages
from models.agent_session import AgentSession
from reflections import sdlc_progress

_PROJECT_KEY = "test-sdlc-stall-2696"
_ISSUE = 918273
# A HUMAN-NAMED lane (#2755). Its slug encodes nothing about its issue, so the
# only way this test can pass is if discovery resolves identity from what the
# world records -- here, the PR's own closing reference -- rather than from the
# shape of the branch string. This is the end-to-end proof of the fix.
_SLUG = "dev-41a59eee"
_BRANCH = f"session/{_SLUG}"
_SHA = "e2e0000000000000000000000000000000000001"
_ORG = "test-sdlc"
_REPO = "stall-2755"
_TARGET_REPO = f"{_ORG}/{_REPO}"


@pytest.fixture
def project(tmp_path):
    """A project dict shaped exactly as ``run_per_project_audit`` yields one.

    It carries a ``github`` block because both read-based rungs of the issue
    resolution ladder are repo-scoped: without a repo to match against, a
    closing reference cannot be trusted and no ledger query is issued.
    """
    return {
        "slug": _PROJECT_KEY,
        "working_directory": str(tmp_path),
        "github": {"org": _ORG, "repo": _REPO},
    }


@pytest.fixture
def eng_session():
    """A real, saved, live, non-ledger eng session — the steer target.

    Deleted through the ORM afterward, scoped to this test's ``test-`` key.
    """
    created: list[AgentSession] = []

    def _make(session_id: str, *, status: str = "running", is_ledger: bool = False):
        session = AgentSession()
        session.session_id = session_id
        session.project_key = _PROJECT_KEY
        session.session_type = "eng"
        session.status = status
        session.is_ledger = is_ledger
        session.message_text = "e2e fixture"
        session.updated_at = datetime.now(tz=UTC)
        session.save()
        created.append(session)
        return session

    yield _make

    for session in created:
        session.delete()
    for leftover in AgentSession.query.filter(project_key=_PROJECT_KEY):
        leftover.delete()
    # The create rung's `adopt_lane_slug` writes a real ledger record for this
    # fixture repo. Removed through the ORM, scoped to this test's repo key.
    for ledger in PipelineLedger.query.filter(target_repo=_TARGET_REPO):
        ledger.delete()


@pytest.fixture
def stalled_lane(monkeypatch):
    """Gates 1-4 and 6' pass; gate 5' and the whole ladder run for real."""
    stale_ts = int(time.time()) - 9 * 3600
    monkeypatch.setattr(
        sdlc_progress,
        "_list_open_lane_prs",
        lambda cwd: [
            {
                "number": 4242,
                "headRefName": _BRANCH,
                "isDraft": False,
                # The exact object shape `gh --json` emits for a closing
                # reference: the repository arrives as a nested object, so the
                # repo-match rule reads it without parsing a URL.
                "closingIssuesReferences": [
                    {
                        "id": "I_e2e",
                        "number": _ISSUE,
                        "repository": {
                            "id": "R_e2e",
                            "name": _REPO,
                            "owner": {"id": "O_e2e", "login": _ORG},
                        },
                        "url": f"https://github.com/{_TARGET_REPO}/issues/{_ISSUE}",
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: (_SHA, stale_ts))
    monkeypatch.setattr(sdlc_progress, "machine_owns_project", lambda key: True)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", alerts.append)
    return alerts


def test_steer_lands_on_the_real_steering_queue(project, eng_session, stalled_lane):
    """The happy path: a real steering message, readable by the worker's API."""
    session = eng_session("test-sdlc-stall-live")

    result = sdlc_progress._check_project_stalls(project)

    assert result["status"] == "ok", result
    assert stalled_lane == [], "the happy path must page nobody"
    assert "1 steered" in result["summary"]

    pending = peek_steering_messages(session.session_id)
    assert len(pending) == 1, f"expected one steering message, got {pending}"
    message = pending[0]
    assert message["sender"] == "pm"
    assert message["is_abort"] is False
    assert f"issue #{_ISSUE}" in message["text"]
    assert "/sdlc" in message["text"]
    assert _SLUG in message["text"]


def test_attempt_is_charged_in_real_redis(project, eng_session, stalled_lane):
    """The (slug, sha)-scoped attempt key is a real key with a real TTL."""
    eng_session("test-sdlc-stall-live")

    sdlc_progress._check_project_stalls(project)

    assert sdlc_progress._attempts_count(_SLUG, _SHA) == 1

    redis = sdlc_progress._get_redis()
    attempts_key = sdlc_progress._ATTEMPTS_KEY.format(slug=_SLUG, sha=_SHA)
    cooldown_key = sdlc_progress._COOLDOWN_KEY.format(slug=_SLUG, sha=_SHA)
    assert redis.ttl(attempts_key) > 0
    assert redis.ttl(cooldown_key) > 0

    redis.delete(attempts_key)
    redis.delete(cooldown_key)


def test_action_cooldown_suppresses_a_second_tick(project, eng_session, stalled_lane):
    """Two back-to-back ticks steer once — the SET NX cooldown is the guard."""
    session = eng_session("test-sdlc-stall-live")

    sdlc_progress._check_project_stalls(project)
    sdlc_progress._check_project_stalls(project)

    assert len(peek_steering_messages(session.session_id)) == 1
    assert stalled_lane == []

    redis = sdlc_progress._get_redis()
    redis.delete(sdlc_progress._ATTEMPTS_KEY.format(slug=_SLUG, sha=_SHA))
    redis.delete(sdlc_progress._COOLDOWN_KEY.format(slug=_SLUG, sha=_SHA))


def test_ledger_anchor_is_never_steered(project, eng_session, stalled_lane, monkeypatch):
    """A sdlc-local-{N} anchor has no worker draining its queue (spike-1, #2495).

    The create rung is fenced here because provisioning a worktree is out of this
    test's scope; what matters is that the ledger row was not selected and its
    steering queue stayed empty.
    """
    anchor = eng_session(f"sdlc-local-{_ISSUE}", is_ledger=True)

    creates: list[dict] = []
    monkeypatch.setattr(
        "tools.valor_session.create_session",
        lambda **kwargs: creates.append(kwargs) or _Refused(),
    )

    sdlc_progress._check_project_stalls(project)

    assert peek_steering_messages(anchor.session_id) == []
    assert len(creates) == 1, "with only a ledger anchor present the ladder falls to create"
    assert creates[0]["slug"] == _SLUG

    redis = sdlc_progress._get_redis()
    redis.delete(sdlc_progress._ATTEMPTS_KEY.format(slug=_SLUG, sha=_SHA))
    redis.delete(sdlc_progress._COOLDOWN_KEY.format(slug=_SLUG, sha=_SHA))
    redis.delete(sdlc_progress._ESCALATED_KEY.format(slug=_SLUG, sha=_SHA))


class _Refused:
    """A ``CreateResult``-shaped refusal, so the fenced create rung stays inert."""

    success = False
    error = "create fenced in E2E"
    session_id = None
