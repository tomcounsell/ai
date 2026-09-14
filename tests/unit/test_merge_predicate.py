"""Unit tests for tools.merge_predicate tracked-issue resolution (#2034).

Groups (b)/(c) of the merge predicate must key on the SDLC-tracked issue
resolved from the durable ``PipelineLedger`` by PR number, not the first
``Closes #N`` in the PR body. For a multi-issue-closure PR under an umbrella
tracking issue, the first-match body issue is a sub-issue with no SDLC
substrate -- keying on it false-fails the gate (repro shape: an umbrella
tracking issue whose PR body closes several sub-issues with no ledgers of
their own).

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

Identifiers below are dedicated synthetic values (990000+ range), never real
GitHub issue/PR numbers in this repo. An earlier revision of this file used
the REAL repo string and REAL production identifiers (this repo's own
umbrella issue/PR/sub-issues); when ``redis_test_db`` isolation was
imperfect under ``pytest -n auto``, ``get_or_create`` on those real
identifiers could collide with -- and ``ledger_factory``'s teardown could
*delete* -- the actual production ``PipelineLedger`` record. Synthetic
identifiers make that class of collision structurally impossible: nothing in
production ever creates a ledger keyed on ``test-owner/...``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent.pipeline_ledger import PipelineLedger
from tools import merge_predicate as mp

REPO_ROOT = Path("/tmp/fake-repo")
TARGET_REPO = "test-owner/merge-predicate-test-repo"
OTHER_REPO = "test-owner/merge-predicate-other-repo"

# Dedicated synthetic issue/PR numbers -- see module docstring. The
# multi-issue-closure shape is preserved: UMBRELLA_ISSUE's ledger carries
# TRACKED_PR, while the PR body's first ``Closes #N`` points at a sub-issue
# (SUB_ISSUE_A) that has no ledger of its own for that PR.
UMBRELLA_ISSUE = 990029
TRACKED_PR = 990033
SUB_ISSUE_A = 991871
SUB_ISSUE_B = 991267
SUB_ISSUE_C = 991760
SINGLE_ISSUE = 990042
SIMPLE_PR = 990001
OTHER_REPO_ISSUE = 990999
NO_LEDGER_PR = 990999999

_SYNTHETIC_LEDGER_KEYS: list[tuple[str, int]] = [
    (TARGET_REPO, UMBRELLA_ISSUE),
    (TARGET_REPO, SUB_ISSUE_A),
    (TARGET_REPO, SUB_ISSUE_B),
    (TARGET_REPO, SUB_ISSUE_C),
    (TARGET_REPO, SINGLE_ISSUE),
    (OTHER_REPO, UMBRELLA_ISSUE),
    (OTHER_REPO, OTHER_REPO_ISSUE),
]


def _cleanup_ledger(target_repo: str, issue_number: int) -> None:
    """Delete any PipelineLedger record for a synthetic test identifier.

    Mirrors ``tests/unit/test_pipeline_ledger.py``'s ``_cleanup`` helper:
    explicit deletion by ``ledger_key``, not reliant on Redis flushdb
    isolation holding under parallel workers.
    """
    for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
        record.delete()


@pytest.fixture(autouse=True)
def _clean_synthetic_ledgers():
    """Belt-and-suspenders cleanup before AND after every test.

    Guards against a leaked record from a prior aborted run poisoning this
    run, independent of whether ``redis_test_db`` isolation held -- same
    defensive posture as ``test_pipeline_ledger.py``'s
    ``setup_method``/``teardown_method`` pattern.
    """
    for target_repo, issue_number in _SYNTHETIC_LEDGER_KEYS:
        _cleanup_ledger(target_repo, issue_number)
    yield
    for target_repo, issue_number in _SYNTHETIC_LEDGER_KEYS:
        _cleanup_ledger(target_repo, issue_number)


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
    """Umbrella-issue shape: UMBRELLA_ISSUE's ledger carries pr_number=TRACKED_PR."""
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)
    result = mp._resolve_tracked_issue(TRACKED_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.TRACKED
    assert result.issue_number == UMBRELLA_ISSUE


def test_resolver_no_ledger_is_no_signal():
    """No PipelineLedger carries this pr_number -> NO_SIGNAL, not a crash."""
    result = mp._resolve_tracked_issue(NO_LEDGER_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert f"no PipelineLedger found for pr_number {NO_LEDGER_PR}" in result.note


def test_resolver_ambiguous_multiple_distinct_issues(ledger_factory):
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)
    ledger_factory(TARGET_REPO, SUB_ISSUE_A, pr_number=TRACKED_PR)
    result = mp._resolve_tracked_issue(TRACKED_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.AMBIGUOUS
    assert result.distinct_count == 2


def test_resolver_cross_repo_ledger_discarded(ledger_factory):
    """A ledger for this pr_number under a different target_repo is ignored."""
    ledger_factory(OTHER_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)
    result = mp._resolve_tracked_issue(TRACKED_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert f"no PipelineLedger found for pr_number {TRACKED_PR}" in result.note


def test_resolver_repo_unresolvable_is_no_signal(ledger_factory, monkeypatch):
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)

    def _raise(root):
        raise RuntimeError("gh repo view failed")

    monkeypatch.setattr(mp, "_gh_repo_name_with_owner", _raise)
    result = mp._resolve_tracked_issue(TRACKED_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "target repo unresolvable" in result.note


def test_resolver_import_guard_degrades_to_no_signal(ledger_factory, monkeypatch):
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)
    monkeypatch.setitem(sys.modules, "agent.pipeline_ledger", None)
    result = mp._resolve_tracked_issue(TRACKED_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "unimportable" in result.note


def test_resolver_query_guard_degrades_to_no_signal(ledger_factory, monkeypatch):
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)

    def _raise(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(PipelineLedger.query, "filter", _raise)
    result = mp._resolve_tracked_issue(TRACKED_PR, REPO_ROOT)
    assert result.outcome is mp._TrackedOutcome.NO_SIGNAL
    assert "query failed" in result.note


# ---------------------------------------------------------------------------
# evaluate_merge_predicate wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def wire_predicate(monkeypatch):
    """Stub the gh/substrate seams and record which issue groups (b)/(c) see.

    Returns the list that ``_check_stage_markers``/``_check_verdict_freshness``
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
        monkeypatch.setattr(mp, "_check_stage_markers", _fake_docs)
        monkeypatch.setattr(mp, "_check_verdict_freshness", _fake_verdict)
        return recorded_issues

    return _wire


def test_multi_issue_closure_keys_on_tracked_umbrella(wire_predicate, ledger_factory):
    """Umbrella shape: body Closes #SUB_ISSUE_A/#SUB_ISSUE_B/#SUB_ISSUE_C,
    PipelineLedger for UMBRELLA_ISSUE carries pr_number=TRACKED_PR. Groups
    (b)/(c) must query UMBRELLA_ISSUE, NOT the first-match SUB_ISSUE_A -- the
    exact false merge-gate failure #2034 reports."""
    recorded = wire_predicate(
        body=f"Closes #{SUB_ISSUE_A}\nCloses #{SUB_ISSUE_B}\nCloses #{SUB_ISSUE_C}"
    )
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=TRACKED_PR)

    result = mp.evaluate_merge_predicate(TRACKED_PR, repo_root=REPO_ROOT)

    assert ("docs", UMBRELLA_ISSUE) in recorded
    assert ("verdict", UMBRELLA_ISSUE) in recorded
    assert all(issue == UMBRELLA_ISSUE for _, issue in recorded)
    assert not any(issue == SUB_ISSUE_A for _, issue in recorded)
    # A substitution note is surfaced for observability.
    assert any(f"SDLC-tracked issue #{UMBRELLA_ISSUE}" in n for n in result.notes)


def test_single_issue_invariance_with_matching_ledger(wire_predicate, ledger_factory):
    recorded = wire_predicate(body=f"Closes #{SINGLE_ISSUE}")
    ledger_factory(TARGET_REPO, SINGLE_ISSUE, pr_number=SIMPLE_PR)

    mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", SINGLE_ISSUE) in recorded
    assert ("verdict", SINGLE_ISSUE) in recorded


def test_noop_resolver_falls_back_to_first_closes_issue(wire_predicate):
    """NO-OP-FAILS case: no PipelineLedger exists for this PR number, so the
    resolver returns NO_SIGNAL and the predicate must key groups (b)/(c) on
    the first Closes #N in the body. This is the load-bearing assertion that
    distinguishes a working resolver from an inert one: an inert resolver
    that never resolves TRACKED would make every call take this same path,
    so ``test_multi_issue_closure_keys_on_tracked_umbrella`` (which requires
    UMBRELLA_ISSUE, not SUB_ISSUE_A) is what actually catches an inert
    resolver -- this test documents that the fallback path itself still
    behaves correctly."""
    recorded = wire_predicate(body=f"Closes #{SINGLE_ISSUE}")

    result = mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", SINGLE_ISSUE) in recorded
    assert ("verdict", SINGLE_ISSUE) in recorded
    assert any("using body issue" in n for n in result.notes)


def test_ambiguous_fails_closed_and_skips_groups_bc(wire_predicate, ledger_factory):
    recorded = wire_predicate(body=f"Closes #{SUB_ISSUE_A}")
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=SIMPLE_PR)
    ledger_factory(TARGET_REPO, SUB_ISSUE_A, pr_number=SIMPLE_PR)

    result = mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert not result.allowed
    ambiguous_failures = [f for f in result.failed_checks if "tracked-issue lookup ambiguous" in f]
    assert len(ambiguous_failures) == 1
    assert f"PR #{SIMPLE_PR}" in ambiguous_failures[0]
    assert "2 distinct" in ambiguous_failures[0]
    # Groups (b)/(c) were NOT keyed on a guessed issue.
    assert recorded == []


def test_cross_repo_collision_falls_back_to_body(wire_predicate, ledger_factory):
    recorded = wire_predicate(body=f"Closes #{SINGLE_ISSUE}")
    ledger_factory(OTHER_REPO, OTHER_REPO_ISSUE, pr_number=SIMPLE_PR)

    mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", SINGLE_ISSUE) in recorded
    assert ("verdict", SINGLE_ISSUE) in recorded
    assert not any(issue == OTHER_REPO_ISSUE for _, issue in recorded)


def test_repo_unresolvable_falls_back_to_body_with_note(wire_predicate, monkeypatch):
    recorded = wire_predicate(body=f"Closes #{SINGLE_ISSUE}")

    def _raise(root):
        raise RuntimeError("gh repo view failed")

    monkeypatch.setattr(mp, "_gh_repo_name_with_owner", _raise)

    result = mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", SINGLE_ISSUE) in recorded
    assert any("target repo unresolvable" in n for n in result.notes)


def test_query_failure_falls_back_to_body(wire_predicate, monkeypatch):
    recorded = wire_predicate(body=f"Closes #{SINGLE_ISSUE}")

    def _raise(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(PipelineLedger.query, "filter", _raise)

    mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", SINGLE_ISSUE) in recorded
    assert ("verdict", SINGLE_ISSUE) in recorded


def test_import_failure_falls_back_to_body(wire_predicate, monkeypatch, ledger_factory):
    recorded = wire_predicate(body=f"Closes #{SINGLE_ISSUE}")
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=SIMPLE_PR)
    monkeypatch.setitem(sys.modules, "agent.pipeline_ledger", None)

    mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", SINGLE_ISSUE) in recorded
    assert ("verdict", SINGLE_ISSUE) in recorded


def test_group_a_missing_body_link_unchanged(wire_predicate):
    """Group (a)'s body-link presence check is independent of tracked lookup."""
    wire_predicate(body="", substrate=False)
    result = mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)
    assert not result.allowed
    assert any(
        "PR body lacks a Closes/Fixes/Resolves #N issue link" in f for f in result.failed_checks
    )


def test_tracked_issue_used_even_when_body_link_missing(wire_predicate, ledger_factory):
    """When the body lacks a link but a ledger resolves, groups (b)/(c) still
    run against the tracked issue (group (a) blocks the merge regardless)."""
    recorded = wire_predicate(body="no issue link here")
    ledger_factory(TARGET_REPO, UMBRELLA_ISSUE, pr_number=SIMPLE_PR)

    result = mp.evaluate_merge_predicate(SIMPLE_PR, repo_root=REPO_ROOT)

    assert ("docs", UMBRELLA_ISSUE) in recorded
    assert not result.allowed  # group (a) still fails on missing body link
    assert any(
        "PR body lacks a Closes/Fixes/Resolves #N issue link" in f for f in result.failed_checks
    )


# ---------------------------------------------------------------------------
# Group (d): single-owner MERGE lease gate (issue #2026, WS1)
# ---------------------------------------------------------------------------


class TestLeaseOwnershipGate:
    """_check_lease_ownership — the Race 2 refusal (fork merging past a
    blocked gate). Concurrent/owner-path coverage lives in
    tests/integration/test_sdlc_multi_lineage.py against real Redis."""

    def test_no_run_id_skips_with_note_hook_layer_exempt(self):
        """The merge-guard hook carries no run identity: with run_id=None the
        gate is SKIPPED (note, not failure) so the hook layer keeps working."""
        failed: list[str] = []
        notes: list[str] = []
        mp._check_lease_ownership(2026, None, failed, notes)
        assert failed == []
        assert any("skipped: no run_id supplied" in n for n in notes)

    def test_no_lease_held_refuses(self, monkeypatch):
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(
                acquired=False, owner_session_id=None, owner_run_id=None
            ),
        )
        failed: list[str] = []
        notes: list[str] = []
        mp._check_lease_ownership(2026, "some-run", failed, notes)
        assert any("no issue lease held" in f for f in failed)

    def test_foreign_owner_refuses_with_owner_named(self, monkeypatch):
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(
                acquired=False, owner_session_id="s", owner_run_id="supervisor-run"
            ),
        )
        failed: list[str] = []
        notes: list[str] = []
        mp._check_lease_ownership(2026, "fork-run", failed, notes)
        assert any("does not hold the issue lease" in f for f in failed)
        assert any("supervisor-run" in f for f in failed)

    def test_owner_passes(self, monkeypatch):
        from models.session_lifecycle import IssueLockResult

        monkeypatch.setattr(
            "models.session_lifecycle.touch_issue_lock",
            lambda *a, **k: IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="owner-run"
            ),
        )
        failed: list[str] = []
        notes: list[str] = []
        mp._check_lease_ownership(2026, "owner-run", failed, notes)
        assert failed == []
        assert any("holds the issue lease" in n for n in notes)


# === #2404: verdict-freshness gate cannot pass on a stale head-SHA read ======
#
# The gate compares the recorded REVIEW verdict's `head_sha` trailer against the
# PR's *current* head. A stale read (gh serving the pre-push SHA) equals the
# trailer, so the gate would pass a verdict predating newly pushed code -- a
# fail-closed design silently going fail-open. `_gh_latest_commit` now resolves
# the SHA authoritatively via `git ls-remote` (tools.pr_head_resolver), so even
# when gh serves the stale value the gate blocks.

_OLD = "a" * 40  # stale/pre-push SHA gh would serve; also the trailer value
_NEW = "b" * 40  # true current head, resolved from git ls-remote
_DATE = "2026-07-27T00:00:00Z"


def _fake_commits_proc(*_a, **_k):
    class _P:
        returncode = 0
        stderr = ""
        stdout = f'{{"sha": "{_OLD}", "commit": {{"committer": {{"date": "{_DATE}"}}}}}}'

    return _P()


def test_gh_latest_commit_overrides_stale_gh_sha_with_git(monkeypatch):
    """gh's `.../commits` serves the STALE _OLD sha; git ls-remote serves the
    true _NEW head. `_gh_latest_commit` returns _NEW (authoritative) and keeps
    gh's committer date."""
    from tools import pr_head_resolver as phr

    monkeypatch.setattr(mp, "_gh_repo_name_with_owner", lambda root: "o/n")
    monkeypatch.setattr(mp.subprocess, "run", _fake_commits_proc)
    monkeypatch.setattr(phr, "_git_ls_remote_pr_head", lambda pr, repo, root: _NEW)
    monkeypatch.setattr(phr, "_gh_pr_head", lambda pr, repo: _OLD)

    commit = mp._gh_latest_commit(990033, REPO_ROOT)
    assert commit["sha"] == _NEW
    assert commit["date"] == _DATE


def test_verdict_freshness_blocks_when_stale_trailer_predates_git_head(monkeypatch):
    """Regression (acceptance bar): trailer carries the OLD sha; the
    authoritative current head is NEW -> the gate BLOCKS. A stale read cannot
    make the approval look fresh."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {
            "verdict": f"APPROVED REVIEW_CONTEXT head_sha={_OLD}",
            "recorded_at": _DATE,
        },
    )
    monkeypatch.setattr(mp, "_gh_latest_commit", lambda pr, root: {"sha": _NEW, "date": _DATE})

    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(990033, 990029, REPO_ROOT, failed, notes)
    assert any("predates PR head commit" in f for f in failed)
    assert notes == []


def test_verdict_freshness_passes_when_trailer_matches_authoritative_head(monkeypatch):
    """Control: trailer == authoritative current head -> verdict is fresh."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {
            "verdict": f"APPROVED REVIEW_CONTEXT head_sha={_NEW}",
            "recorded_at": _DATE,
        },
    )
    monkeypatch.setattr(mp, "_gh_latest_commit", lambda pr, root: {"sha": _NEW, "date": _DATE})

    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(990033, 990029, REPO_ROOT, failed, notes)
    assert failed == []
    assert any("head_sha trailer matches PR head commit" in n for n in notes)


# ---------------------------------------------------------------------------
# Issue #2769: the head SHA now rides in the record's own `head_sha` field.
# `_check_verdict_freshness` must read the FIELD, not just the in-token trailer.
#
# These assert the EXACT `notes` string, not merely pass/fail. A pass/fail-only
# assertion cannot detect the regression: a record whose head SHA is invisible
# to the reader looks trailer-less, and the trailer-less branch silently
# downgrades to the WEAKER `recorded_at`-vs-commit-date comparison, which also
# reports "fresh". That is a fail-OPEN on the merge gate #2404/#2415 exist to
# keep honest, and only the note text distinguishes the two branches.
# ---------------------------------------------------------------------------

_FRESH_NOTE = "REVIEW verdict fresh: head_sha trailer matches PR head commit"
_WEAK_NOTE = "REVIEW verdict fresh: recorded after the PR's latest commit"


def test_verdict_freshness_reads_the_head_sha_field_new_shape(monkeypatch):
    """New-shape record: bare `APPROVED` token + a separate `head_sha` field."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {
            "verdict": "APPROVED",
            "head_sha": _NEW,
            "recorded_at": _DATE,
        },
    )
    monkeypatch.setattr(mp, "_gh_latest_commit", lambda pr, root: {"sha": _NEW, "date": _DATE})

    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(990033, 990029, REPO_ROOT, failed, notes)
    assert failed == []
    assert _FRESH_NOTE in notes
    # The strong branch ran -- NOT the weaker timestamp fallback.
    assert _WEAK_NOTE not in notes


def test_verdict_freshness_blocks_on_stale_head_sha_field(monkeypatch):
    """New-shape record whose field names the OLD head -> BLOCK, and never a
    silent downgrade to the timestamp comparison."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {
            "verdict": "APPROVED",
            "head_sha": _OLD,
            "recorded_at": _DATE,
        },
    )
    monkeypatch.setattr(mp, "_gh_latest_commit", lambda pr, root: {"sha": _NEW, "date": _DATE})

    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(990033, 990029, REPO_ROOT, failed, notes)
    assert "REVIEW verdict predates PR head commit (head_sha trailer mismatch)" in failed
    assert notes == []


def test_verdict_freshness_field_wins_over_disagreeing_legacy_trailer(monkeypatch):
    """Defined precedence (#2769): the field is authoritative. Here the legacy
    in-token trailer is STALE but the field is current -> fresh."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {
            "verdict": f"APPROVED REVIEW CONTEXT HEAD SHA={_OLD.upper()}",
            "head_sha": _NEW,
            "recorded_at": _DATE,
        },
    )
    monkeypatch.setattr(mp, "_gh_latest_commit", lambda pr, root: {"sha": _NEW, "date": _DATE})

    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(990033, 990029, REPO_ROOT, failed, notes)
    assert failed == []
    assert _FRESH_NOTE in notes


def test_verdict_freshness_legacy_record_still_takes_the_strong_branch(monkeypatch):
    """Permanent legacy fallback: a pre-split record carrying only the mangled
    in-token trailer must still hit the head_sha branch, never the weak one."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: {
            "verdict": f"APPROVED REVIEW CONTEXT HEAD SHA={_NEW.upper()}",
            "recorded_at": _DATE,
        },
    )
    monkeypatch.setattr(mp, "_gh_latest_commit", lambda pr, root: {"sha": _NEW, "date": _DATE})

    failed: list[str] = []
    notes: list[str] = []
    mp._check_verdict_freshness(990033, 990029, REPO_ROOT, failed, notes)
    assert failed == []
    assert _FRESH_NOTE in notes
    assert _WEAK_NOTE not in notes


def test_merge_predicate_head_sha_reader_import_stays_in_function():
    """The merge-guard hook loads this module under a bare interpreter, so its
    module-level imports must stay stdlib-only. `head_sha_of_record` pulls in
    models.agent_session transitively and MUST remain a lazy in-function
    import."""
    import inspect

    source = inspect.getsource(mp)
    assert "head_sha_of_record" in source
    for line in source.splitlines():
        if "from tools._sdlc_utils import" in line:
            # Any occurrence must be indented (i.e. inside a function body).
            assert line != line.lstrip(), f"module-level tools import: {line!r}"
    assert not hasattr(mp, "head_sha_of_record")


# ---------------------------------------------------------------------------
# _derive_slug — the docs/features fallback's slug derivation (#2891)
# ---------------------------------------------------------------------------
#
# A head ref that is not `session/`-prefixed keeps its prefix, so its slug
# carries a `/` -- e.g. `fix/router-blocked-on-conflict` -- and the DOCS
# fallback leg probes `docs/features/fix/router-blocked-on-conflict.md`, a
# nested path that can never exist. The slug must degrade to "" for any
# slash-bearing remainder so the leg emits the honest "no usable slug" refusal
# instead of naming a specific absent file.


def test_derive_slug_slash_bearing_non_session_head_ref_yields_no_slug():
    """PR #2797's head ref: no slug, so the DOCS fallback refuses honestly."""
    assert mp._derive_slug("fix/router-blocked-on-conflict") == ""


def test_derive_slug_session_prefix_stripped():
    """The session/ convention still yields the flat remainder as the slug."""
    assert mp._derive_slug("session/foo-bar") == "foo-bar"


def test_derive_slug_session_nested_remainder_yields_no_slug():
    """A nested session branch (remainder still slash-bearing) is unusable too."""
    assert mp._derive_slug("session/foo/bar") == ""


def test_derive_slug_no_slug_refs():
    """main/master/HEAD/empty/None can never be a usable slug."""
    for head_ref in ("main", "master", "HEAD", "", None):
        assert mp._derive_slug(head_ref) == ""


def test_docs_stage_slash_bearing_head_ref_emits_honest_no_slug_refusal(monkeypatch):
    """Regression pin for #2891: a non-session/ head ref must refuse with the
    generic "no usable slug" message, never probe a nested docs/features path."""
    failed: list[str] = []
    notes: list[str] = []
    mp._check_docs_marker(
        {"DOCS": "ready"}, "fix/router-blocked-on-conflict", REPO_ROOT, failed, notes
    )
    assert len(failed) == 1
    assert "no usable slug for the docs/features fallback" in failed[0]
    assert "docs/features/fix/router-blocked-on-conflict.md absent" not in failed[0]
    assert notes == []
