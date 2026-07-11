"""Unit tests for tools.merge_predicate tracked-issue resolution (#2034).

Groups (b)/(c) of the merge predicate must key on the SDLC-tracked issue
resolved from the durable ``PipelineLedger`` by PR number, not the first
``Closes #N`` in the PR body. For a multi-issue-closure PR under an umbrella
tracking issue, the first-match body issue is a sub-issue with no SDLC
substrate -- keying on it false-fails the gate (repro: PR #2033, body closes
#1871/#1267/#1760, umbrella tracking issue #2029).

An earlier mechanism (PR #2035, superseded by this fix) resolved the tracked
issue via ``AgentSession.query.filter(slug=..., issue_number=...)``. That
mechanism is empirically inert in production: ``slug`` and ``issue_number``
are populated by disjoint AgentSession creation paths, so 0 of the live
sessions co-populate both fields, and the resolver always degraded to
NO_SIGNAL. These tests build REAL, production-shaped ``PipelineLedger``
records (via ``get_or_create``, under the autouse ``redis_test_db`` fixture --
see ``tests/unit/test_pipeline_ledger.py``) and never construct any
AgentSession-shaped fixture, so the suite provably fails if the resolver ever
reverts to the inert slug-keyed mechanism.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent.pipeline_ledger import PipelineLedger
from tools import merge_predicate as mp

REPO_ROOT = Path("/tmp/fake-repo")
TARGET_REPO = "tomcounsell/ai"
OTHER_REPO = "tomcounsell/other-repo"


@pytest.fixture
def ledger_factory():
    """Create real PipelineLedger records and clean them up on teardown."""
    created: list[PipelineLedger] = []

    def _factory(target_repo: str, issue_number: int, pr_number: int | None = None):
        ledger = PipelineLedger.get_or_create(target_repo, issue_number)
        if pr_number is not None:
            ledger.pr_number = pr_number
            ledger.save()
        created.append(ledger)
        return ledger

    yield _factory

    for ledger in created:
        ledger.delete()


@pytest.fixture(autouse=True)
def stub_repo_name(monkeypatch):
    """Default target-repo resolution for direct resolver calls.

    Individual tests override this via ``monkeypatch.setattr`` when they need
    a different repo or a failure.
    """
    monkeypatch.setattr(mp, "_gh_repo_name_with_owner", lambda root: TARGET_REPO)


# ---------------------------------------------------------------------------
# Resolver unit cases
# ---------------------------------------------------------------------------


def test_resolver_happy_path_returns_tracked(ledger_factory):
    """PR #2033 shape: umbrella issue #2029's ledger carries pr_number=2033."""
    ledger_factory(TARGET_REPO, 2029, pr_number=2033)
    result = mp._resolve_tracked_issue(2033, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.TRACKED
    assert result.issue_number == 2029


def test_resolver_no_ledger_is_no_signal():
    """No PipelineLedger carries this pr_number -> NO_SIGNAL, not a crash."""
    result = mp._resolve_tracked_issue(999999, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "no PipelineLedger found for pr_number 999999" in result.note


def test_resolver_ambiguous_multiple_distinct_issues(ledger_factory):
    ledger_factory(TARGET_REPO, 2029, pr_number=2033)
    ledger_factory(TARGET_REPO, 1871, pr_number=2033)
    result = mp._resolve_tracked_issue(2033, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.AMBIGUOUS
    assert result.distinct_count == 2


def test_resolver_cross_repo_ledger_discarded(ledger_factory):
    """A ledger for this pr_number under a different target_repo is ignored."""
    ledger_factory(OTHER_REPO, 2029, pr_number=2033)
    result = mp._resolve_tracked_issue(2033, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "no PipelineLedger found for pr_number 2033" in result.note


def test_resolver_repo_unresolvable_is_no_signal(ledger_factory, monkeypatch):
    ledger_factory(TARGET_REPO, 2029, pr_number=2033)

    def _raise(root):
        raise RuntimeError("gh repo view failed")

    monkeypatch.setattr(mp, "_gh_repo_name_with_owner", _raise)
    result = mp._resolve_tracked_issue(2033, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "target repo unresolvable" in result.note


def test_resolver_import_guard_degrades_to_no_signal(ledger_factory, monkeypatch):
    ledger_factory(TARGET_REPO, 2029, pr_number=2033)
    monkeypatch.setitem(sys.modules, "agent.pipeline_ledger", None)
    result = mp._resolve_tracked_issue(2033, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "unimportable" in result.note


def test_resolver_query_guard_degrades_to_no_signal(ledger_factory, monkeypatch):
    ledger_factory(TARGET_REPO, 2029, pr_number=2033)

    def _raise(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(PipelineLedger.query, "filter", _raise)
    result = mp._resolve_tracked_issue(2033, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "query failed" in result.note


# ---------------------------------------------------------------------------
# evaluate_merge_predicate wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def wire_predicate(monkeypatch):
    """Stub the gh/substrate seams and record which issue groups (b)/(c) see.

    Returns the list that ``_check_docs_stage``/``_check_verdict_freshness``
    record their ``issue_number`` argument into.
    """

    def _wire(*, body, head_ref="session/dev-abc", substrate=True, target_repo=TARGET_REPO):
        recorded_issues: list[tuple[str, int]] = []

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
        monkeypatch.setattr(mp, "_gh_repo_name_with_owner", lambda root: target_repo)
        monkeypatch.setattr(mp, "_check_docs_stage", _fake_docs)
        monkeypatch.setattr(mp, "_check_verdict_freshness", _fake_verdict)
        return recorded_issues

    return _wire


def test_multi_issue_closure_keys_on_tracked_umbrella(wire_predicate, ledger_factory):
    """PR #2033 shape: body Closes #1871/#1267/#1760, PipelineLedger for #2029
    carries pr_number=2033. Groups (b)/(c) must query 2029, NOT the
    first-match 1871 -- the exact false merge-gate failure #2034 reports."""
    recorded = wire_predicate(body="Closes #1871\nCloses #1267\nCloses #1760")
    ledger_factory(TARGET_REPO, 2029, pr_number=2033)

    result = mp.evaluate_merge_predicate(2033, repo_root=REPO_ROOT)

    assert ("docs", 2029) in recorded
    assert ("verdict", 2029) in recorded
    assert all(issue == 2029 for _, issue in recorded)
    assert not any(issue == 1871 for _, issue in recorded)
    # A substitution note is surfaced for observability.
    assert any("SDLC-tracked issue #2029" in n for n in result.notes)


def test_single_issue_invariance_with_matching_ledger(wire_predicate, ledger_factory):
    recorded = wire_predicate(body="Closes #42")
    ledger_factory(TARGET_REPO, 42, pr_number=1)

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded


def test_noop_resolver_falls_back_to_first_closes_issue(wire_predicate):
    """NO-OP-FAILS case: no PipelineLedger exists for this PR number, so the
    resolver returns NO_SIGNAL and the predicate must key groups (b)/(c) on
    the first Closes #N in the body. This is the load-bearing assertion that
    distinguishes a working resolver from an inert one: an inert resolver
    that never resolves TRACKED would make every call take this same path,
    so ``test_multi_issue_closure_keys_on_tracked_umbrella`` (which requires
    2029, not 1871) is what actually catches an inert resolver -- this test
    documents that the fallback path itself still behaves correctly."""
    recorded = wire_predicate(body="Closes #42")

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded
    assert any("using body issue" in n for n in result.notes)


def test_ambiguous_fails_closed_and_skips_groups_bc(wire_predicate, ledger_factory):
    recorded = wire_predicate(body="Closes #1871")
    ledger_factory(TARGET_REPO, 2029, pr_number=1)
    ledger_factory(TARGET_REPO, 1871, pr_number=1)

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert not result.allowed
    ambiguous_failures = [f for f in result.failed_checks if "tracked-issue lookup ambiguous" in f]
    assert len(ambiguous_failures) == 1
    assert "PR #1" in ambiguous_failures[0]
    assert "2 distinct" in ambiguous_failures[0]
    # Groups (b)/(c) were NOT keyed on a guessed issue.
    assert recorded == []


def test_cross_repo_collision_falls_back_to_body(wire_predicate, ledger_factory):
    recorded = wire_predicate(body="Closes #42")
    ledger_factory(OTHER_REPO, 999, pr_number=1)

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded
    assert not any(issue == 999 for _, issue in recorded)


def test_repo_unresolvable_falls_back_to_body_with_note(wire_predicate, monkeypatch):
    recorded = wire_predicate(body="Closes #42")

    def _raise(root):
        raise RuntimeError("gh repo view failed")

    monkeypatch.setattr(mp, "_gh_repo_name_with_owner", _raise)

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert any("target repo unresolvable" in n for n in result.notes)


def test_query_failure_falls_back_to_body(wire_predicate, monkeypatch):
    recorded = wire_predicate(body="Closes #42")

    def _raise(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(PipelineLedger.query, "filter", _raise)

    mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 42) in recorded
    assert ("verdict", 42) in recorded


def test_import_failure_falls_back_to_body(wire_predicate, monkeypatch, ledger_factory):
    recorded = wire_predicate(body="Closes #42")
    ledger_factory(TARGET_REPO, 2029, pr_number=1)
    monkeypatch.setitem(sys.modules, "agent.pipeline_ledger", None)

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


def test_tracked_issue_used_even_when_body_link_missing(wire_predicate, ledger_factory):
    """When the body lacks a link but a ledger resolves, groups (b)/(c) still
    run against the tracked issue (group (a) blocks the merge regardless)."""
    recorded = wire_predicate(body="no issue link here")
    ledger_factory(TARGET_REPO, 2029, pr_number=1)

    result = mp.evaluate_merge_predicate(1, repo_root=REPO_ROOT)

    assert ("docs", 2029) in recorded
    assert not result.allowed  # group (a) still fails on missing body link
    assert any(
        "PR body lacks a Closes/Fixes/Resolves #N issue link" in f for f in result.failed_checks
    )
