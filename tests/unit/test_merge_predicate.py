"""Unit tests for tools.merge_predicate tracked-issue resolution (#2034).

Groups (b)/(c) of the merge predicate must key on the SDLC-tracked issue
derived from the PR's branch slug, not the first ``Closes #N`` in the PR body.
For a multi-issue-closure PR under an umbrella tracking issue, the first-match
body issue is a sub-issue with no SDLC substrate — keying on it false-fails the
gate. These tests exercise the tri-state resolver (``tracked`` / ``no signal`` /
``ambiguous``) and its wiring into ``evaluate_merge_predicate``.

All session sources are synthetic — the resolver's lazy imports
(``models.agent_session``, ``models.session_lifecycle``,
``config.project_key_resolver``) are replaced in ``sys.modules`` so no live
Redis is required.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import merge_predicate as mp

REPO_ROOT = Path("/tmp/fake-repo")

# Mirror of models.session_lifecycle.NON_TERMINAL_STATUSES for the fake module.
_NON_TERMINAL = frozenset(
    {
        "pending",
        "running",
        "active",
        "dormant",
        "waiting_for_children",
        "superseded",
        "paused_circuit",
        "paused",
        "paused_budget",
    }
)


def _session(slug, issue_number, project_key="valor", status="running"):
    return SimpleNamespace(
        slug=slug,
        issue_number=issue_number,
        project_key=project_key,
        status=status,
    )


@pytest.fixture
def install_session_source(monkeypatch):
    """Install fake ``models``/``config`` modules for the resolver's lazy imports.

    Returns a configurator; call it with the sessions/behaviour a test needs.
    Using ``sys.modules`` shims keeps the resolver's two-guard structure intact
    while never touching real Redis.
    """

    def _install(
        *,
        sessions=None,
        project="valor",
        query_exc=None,
        break_import=False,
    ):
        models_pkg = types.ModuleType("models")
        config_pkg = types.ModuleType("config")
        agent_session_mod = types.ModuleType("models.agent_session")
        lifecycle_mod = types.ModuleType("models.session_lifecycle")
        resolver_mod = types.ModuleType("config.project_key_resolver")

        class _Query:
            def filter(self, **kwargs):
                if query_exc is not None:
                    raise query_exc
                return self

            def all(self):
                return list(sessions or [])

        class _AgentSession:
            query = _Query()

        agent_session_mod.AgentSession = _AgentSession
        lifecycle_mod.NON_TERMINAL_STATUSES = _NON_TERMINAL

        def _resolve_project_key(cwd=None, env=None, **kwargs):
            # env={} must be passed by the resolver to force cwd-only scoping.
            assert env == {}, "resolver must pass env={} to force cwd-only scoping"
            return project

        resolver_mod.resolve_project_key = _resolve_project_key

        monkeypatch.setitem(sys.modules, "models", models_pkg)
        monkeypatch.setitem(sys.modules, "config", config_pkg)
        monkeypatch.setitem(sys.modules, "models.agent_session", agent_session_mod)
        monkeypatch.setitem(sys.modules, "models.session_lifecycle", lifecycle_mod)
        monkeypatch.setitem(sys.modules, "config.project_key_resolver", resolver_mod)

        if break_import:
            # A None entry makes ``from models.agent_session import ...`` raise.
            monkeypatch.setitem(sys.modules, "models.agent_session", None)

    return _install


# ---------------------------------------------------------------------------
# Resolver unit cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("head_ref", ["main", "master", "HEAD", "", "session/", None])
def test_resolver_no_slug_is_no_signal(head_ref):
    """Non-substrate head refs yield no signal before any import/query."""
    result = mp._resolve_tracked_issue(head_ref, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert result.issue_number is None


def test_resolver_happy_path_returns_tracked(install_session_source):
    install_session_source(sessions=[_session("dev-abc", 2029)])
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.TRACKED
    assert result.issue_number == 2029


def test_resolver_no_session_is_no_signal(install_session_source):
    install_session_source(sessions=[])
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "no session found for slug dev-abc" in result.note


def test_resolver_ambiguous_multiple_distinct_issues(install_session_source):
    install_session_source(sessions=[_session("dev-abc", 2029), _session("dev-abc", 1871)])
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.AMBIGUOUS
    assert result.distinct_count == 2
    assert result.slug == "dev-abc"


def test_resolver_terminal_and_transitional_sessions_filtered(install_session_source):
    """A live 2029 session plus a completed/superseded divergent session for the
    same slug resolves to 2029, not ambiguous."""
    install_session_source(
        sessions=[
            _session("dev-abc", 2029, status="running"),
            _session("dev-abc", 9999, status="completed"),
            _session("dev-abc", 8888, status="superseded"),
            _session("dev-abc", 7777, status="paused_budget"),
        ]
    )
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.TRACKED
    assert result.issue_number == 2029


def test_resolver_cross_project_session_discarded(install_session_source):
    """A matching-slug session in a different project is ignored → no signal."""
    install_session_source(
        sessions=[_session("dev-abc", 1871, project_key="other-project")],
        project="valor",
    )
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "no session found for slug dev-abc" in result.note


def test_resolver_project_unresolved_is_no_signal(install_session_source):
    install_session_source(sessions=[_session("dev-abc", 2029)], project=None)
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "project unresolved" in result.note


def test_resolver_import_guard_degrades_to_no_signal(install_session_source):
    install_session_source(sessions=[_session("dev-abc", 2029)], break_import=True)
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "unimportable" in result.note


def test_resolver_query_guard_degrades_to_no_signal(install_session_source):
    install_session_source(query_exc=RuntimeError("redis down"))
    result = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "query failed" in result.note


def test_no_session_and_project_unresolved_notes_are_distinct(install_session_source):
    install_session_source(sessions=[])
    no_session = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT).note
    install_session_source(sessions=[_session("dev-abc", 2029)], project=None)
    unresolved = mp._resolve_tracked_issue("session/dev-abc", REPO_ROOT).note
    assert no_session != unresolved


# ---------------------------------------------------------------------------
# evaluate_merge_predicate wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def wire_predicate(monkeypatch):
    """Stub the gh/substrate seams and record which issue groups (b)/(c) see.

    Returns the list that ``_check_docs_stage``/``_check_verdict_freshness``
    record their ``issue_number`` argument into.
    """

    def _wire(*, body, head_ref="session/dev-abc", substrate=True):
        recorded_issues: list[int] = []

        def _fake_pr_view(pr_number, repo_root):
            return {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
                "body": body,
                "headRefName": head_ref,
            }

        def _fake_docs(issue_number, head_ref_, repo_root, failed, notes):
            recorded_issues.append(("docs", issue_number))

        def _fake_verdict(pr_number, issue_number, repo_root, failed, notes):
            recorded_issues.append(("verdict", issue_number))

        monkeypatch.setattr(mp, "_substrate_present", lambda root: substrate)
        monkeypatch.setattr(mp, "_gh_pr_view", _fake_pr_view)
        monkeypatch.setattr(mp, "_check_docs_stage", _fake_docs)
        monkeypatch.setattr(mp, "_check_verdict_freshness", _fake_verdict)
        return recorded_issues

    return _wire


def test_multi_issue_closure_keys_on_tracked_umbrella(wire_predicate, install_session_source):
    """PR #2033 shape: body Closes #1871/#1267/#1760, slug → umbrella #2029.
    Groups (b)/(c) must query 2029, NOT the first-match 1871."""
    recorded = wire_predicate(body="Closes #1871\nCloses #1267\nCloses #1760")
    install_session_source(sessions=[_session("dev-abc", 2029)])

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 2029) in recorded
    assert ("verdict", 2029) in recorded
    assert all(issue == 2029 for _, issue in recorded)
    assert not any(issue == 1871 for _, issue in recorded)
    # A substitution note is surfaced for observability.
    assert any("SDLC-tracked issue #2029" in n for n in result.notes)


def test_single_issue_invariance_with_matching_session(wire_predicate, install_session_source):
    recorded = wire_predicate(body="Closes #42")
    install_session_source(sessions=[_session("dev-abc", 42)])

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded


def test_single_issue_invariance_session_absent_falls_back_to_body(
    wire_predicate, install_session_source
):
    recorded = wire_predicate(body="Closes #42")
    install_session_source(sessions=[])

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded
    assert any("using body issue" in n for n in result.notes)


def test_ambiguous_fails_closed_and_skips_groups_bc(wire_predicate, install_session_source):
    recorded = wire_predicate(body="Closes #1871")
    install_session_source(sessions=[_session("dev-abc", 2029), _session("dev-abc", 1871)])

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert not result.allowed
    ambiguous_failures = [f for f in result.failed_checks if "tracked-issue lookup ambiguous" in f]
    assert len(ambiguous_failures) == 1
    assert "dev-abc" in ambiguous_failures[0]
    assert "2 distinct" in ambiguous_failures[0]
    # Groups (b)/(c) were NOT keyed on a guessed issue.
    assert recorded == []


def test_cross_project_collision_falls_back_to_body(wire_predicate, install_session_source):
    recorded = wire_predicate(body="Closes #42")
    install_session_source(
        sessions=[_session("dev-abc", 999, project_key="other-project")],
        project="valor",
    )

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded
    assert not any(issue == 999 for _, issue in recorded)


def test_project_unresolved_falls_back_to_body_with_note(wire_predicate, install_session_source):
    recorded = wire_predicate(body="Closes #42")
    install_session_source(sessions=[_session("dev-abc", 2029)], project=None)

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert any("project unresolved" in n for n in result.notes)


def test_query_failure_falls_back_to_body(wire_predicate, install_session_source):
    recorded = wire_predicate(body="Closes #42")
    install_session_source(query_exc=RuntimeError("redis down"))

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded


def test_import_failure_falls_back_to_body(wire_predicate, install_session_source):
    recorded = wire_predicate(body="Closes #42")
    install_session_source(sessions=[_session("dev-abc", 2029)], break_import=True)

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded


def test_group_a_missing_body_link_unchanged(wire_predicate):
    """Group (a)'s body-link presence check is independent of tracked lookup."""
    wire_predicate(body="", substrate=False)
    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)
    assert not result.allowed
    assert any(
        "PR body lacks a Closes/Fixes/Resolves #N issue link" in f for f in result.failed_checks
    )


def test_tracked_issue_used_even_when_body_link_missing(wire_predicate, install_session_source):
    """When the body lacks a link but a session resolves, groups (b)/(c) still
    run against the tracked issue (group (a) blocks the merge regardless)."""
    recorded = wire_predicate(body="no issue link here")
    install_session_source(sessions=[_session("dev-abc", 2029)])

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 2029) in recorded
    assert not result.allowed  # group (a) still fails on missing body link
    assert any(
        "PR body lacks a Closes/Fixes/Resolves #N issue link" in f for f in result.failed_checks
    )
