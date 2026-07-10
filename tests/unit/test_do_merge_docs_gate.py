"""Tests for the shared terminal merge predicate (``tools/merge_predicate.py``).

The Step 2b DOCS-stage gate (issue #1944) and the REVIEW-verdict freshness
check (issue #2003, critique BLOCKER 2) used to live as shell snippets inside
``docs/sdlc/do-merge.md``. Both were extracted into
``tools.merge_predicate.evaluate_merge_predicate`` — the single deterministic
predicate consumed by BOTH the /do-merge skill and the merge-guard hook — so
these tests exercise the helper directly.

The subprocess seams (``_gh_pr_view``, ``_run_stage_query``, ``_run_verdict_get``,
``_gh_latest_commit``, ``_sdlc_tool_resolvable``) are monkeypatched; the repo
root is a tmp_path fixture carrying a ``docs/sdlc/do-merge.md`` marker (the
substrate probe) and real ``docs/features/{slug}.md`` fixture files for the
degraded fallback.

Covers:
- DOCS gate: completed / in_progress / pending / empty-stages / no-usable-slug
  (behavior parity with the pre-extraction bash snippet)
- Verdict freshness: stale timestamp, head_sha trailer mismatch, matching
  trailer (``-k stale_verdict`` selects these — Verification row)
- Ordered substrate detection: absent → groups b/c skipped, group (a) still
  enforced; present + evaluation error → fail closed
- Parity guard (plan Risk 3): the do-merge addendum must reference the helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.merge_predicate as mp

REPO_ROOT = Path(__file__).resolve().parents[2]
DO_MERGE_MD = REPO_ROOT / "docs" / "sdlc" / "do-merge.md"

ISSUE = 1944
PR = 999
HEAD_SHA = "a" * 40


def _good_pr(**overrides) -> dict:
    pr = {
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}],
        "reviewDecision": "APPROVED",
        "body": f"Does the thing.\n\nCloses #{ISSUE}",
        "headRefName": "session/my-slug",
    }
    pr.update(overrides)
    return pr


def _fresh_verdict(**overrides) -> dict:
    record = {"verdict": "APPROVED", "recorded_at": "2026-07-10T12:00:00+00:00"}
    record.update(overrides)
    return record


@pytest.fixture
def substrate_repo(tmp_path, monkeypatch):
    """A tmp repo root with the substrate present and green default seams."""
    (tmp_path / "docs" / "sdlc").mkdir(parents=True)
    (tmp_path / "docs" / "sdlc" / "do-merge.md").write_text("# addendum\n")
    (tmp_path / "docs" / "features").mkdir(parents=True)

    monkeypatch.setattr(mp, "_sdlc_tool_resolvable", lambda root: True)
    monkeypatch.setattr(mp, "_gh_pr_view", lambda pr, root: _good_pr())
    monkeypatch.setattr(
        mp,
        "_run_stage_query",
        lambda issue, root: {"stages": {"DOCS": "completed"}, "_meta": {}},
    )
    monkeypatch.setattr(mp, "_run_verdict_get", lambda issue, root: _fresh_verdict())
    monkeypatch.setattr(
        mp,
        "_gh_latest_commit",
        lambda pr, root: {"sha": HEAD_SHA, "date": "2026-07-10T00:00:00Z"},
    )
    return tmp_path


def _make_feature_doc(repo_root: Path, slug: str) -> None:
    (repo_root / "docs" / "features" / f"{slug}.md").write_text("# feature\n")


# ---------------------------------------------------------------------------
# DOCS gate: completed → authoritative PASS
# ---------------------------------------------------------------------------


def test_completed_passes(substrate_repo):
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is True
    assert result.substrate_present is True
    assert result.failed_checks == []


def test_skip_recorded_as_completed_passes(substrate_repo):
    """A DOCS-skip (#1799) records ``completed``, admitted identically."""
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is True


# ---------------------------------------------------------------------------
# DOCS gate: in_progress → the only HARD fail
# ---------------------------------------------------------------------------


def test_in_progress_hard_fails(substrate_repo, monkeypatch):
    monkeypatch.setattr(
        mp,
        "_run_stage_query",
        lambda issue, root: {"stages": {"DOCS": "in_progress"}, "_meta": {}},
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert "DOCS stage in_progress" in result.failed_checks


# ---------------------------------------------------------------------------
# DOCS gate: pending → degraded fallback to docs/features/{slug}.md existence
# ---------------------------------------------------------------------------


def test_pending_with_feature_doc_passes_degraded(substrate_repo, monkeypatch):
    monkeypatch.setattr(
        mp,
        "_run_stage_query",
        lambda issue, root: {"stages": {"DOCS": "pending"}, "_meta": {}},
    )
    _make_feature_doc(substrate_repo, "my-slug")
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is True
    assert any("degraded" in note for note in result.notes)


def test_pending_without_feature_doc_fails(substrate_repo, monkeypatch):
    monkeypatch.setattr(
        mp,
        "_run_stage_query",
        lambda issue, root: {"stages": {"DOCS": "pending"}, "_meta": {}},
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("my-slug.md absent" in check for check in result.failed_checks)


# ---------------------------------------------------------------------------
# DOCS gate: empty stages (session reaped) → degraded fallback
# ---------------------------------------------------------------------------


def test_empty_stages_with_feature_doc_passes_degraded(substrate_repo, monkeypatch):
    monkeypatch.setattr(mp, "_run_stage_query", lambda issue, root: {"stages": {}, "_meta": {}})
    _make_feature_doc(substrate_repo, "my-slug")
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is True
    assert any("degraded" in note for note in result.notes)


def test_empty_stages_without_feature_doc_fails(substrate_repo, monkeypatch):
    monkeypatch.setattr(mp, "_run_stage_query", lambda issue, root: {"stages": {}, "_meta": {}})
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False


# ---------------------------------------------------------------------------
# DOCS gate: no usable slug (head ref main / detached) → FAIL, no main.md lookup
# ---------------------------------------------------------------------------


def test_no_usable_slug_fails_without_main_lookup(substrate_repo, monkeypatch):
    monkeypatch.setattr(mp, "_gh_pr_view", lambda pr, root: _good_pr(headRefName="main"))
    monkeypatch.setattr(
        mp,
        "_run_stage_query",
        lambda issue, root: {"stages": {"DOCS": "pending"}, "_meta": {}},
    )
    _make_feature_doc(substrate_repo, "main")  # must NOT be consulted
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("no usable slug" in check for check in result.failed_checks)
    assert not any("main.md" in check for check in result.failed_checks)


# ---------------------------------------------------------------------------
# DOCS gate: stage-query failure with substrate present → FAIL CLOSED
# ---------------------------------------------------------------------------


def test_stage_query_error_fails_closed(substrate_repo, monkeypatch):
    def boom(issue, root):
        raise RuntimeError("stage-query exited 2")

    monkeypatch.setattr(mp, "_run_stage_query", boom)
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("DOCS stage state unavailable" in check for check in result.failed_checks)


# ---------------------------------------------------------------------------
# Verdict presence / freshness (#2003 BLOCKER 2) — `-k stale_verdict` rows
# ---------------------------------------------------------------------------


def test_missing_review_verdict_fails(substrate_repo, monkeypatch):
    monkeypatch.setattr(mp, "_run_verdict_get", lambda issue, root: {})
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert "no recorded REVIEW verdict" in result.failed_checks


def test_non_approved_verdict_fails(substrate_repo, monkeypatch):
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: _fresh_verdict(verdict="CHANGES REQUESTED"),
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("not APPROVED" in check for check in result.failed_checks)


def test_stale_verdict_timestamp_predates_head_commit_fails(substrate_repo, monkeypatch):
    """APPROVED verdict recorded BEFORE the PR's latest commit → stale, fail."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: _fresh_verdict(recorded_at="2026-07-09T00:00:00+00:00"),
    )
    monkeypatch.setattr(
        mp,
        "_gh_latest_commit",
        lambda pr, root: {"sha": HEAD_SHA, "date": "2026-07-10T00:00:00Z"},
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert "REVIEW verdict predates PR head commit" in result.failed_checks


def test_stale_verdict_head_sha_trailer_mismatch_fails(substrate_repo, monkeypatch):
    """An APPROVED verdict carrying a head_sha trailer for a DIFFERENT commit
    is stale relative to the PR head — a bare 'APPROVED in text' pass would
    reopen the stale-approval bypass."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: _fresh_verdict(verdict=f"APPROVED\nREVIEW_CONTEXT head_sha={'b' * 40}"),
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("head_sha trailer mismatch" in check for check in result.failed_checks)


def test_stale_verdict_matching_trailer_is_fresh_and_passes(substrate_repo, monkeypatch):
    """The trailer comparison is PREFERRED over the timestamp: a matching
    head_sha trailer is fresh even when the recorded timestamp is old."""
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: _fresh_verdict(
            verdict=f"APPROVED\nREVIEW_CONTEXT head_sha={HEAD_SHA}",
            recorded_at="2020-01-01T00:00:00+00:00",
        ),
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is True
    assert any("head_sha trailer matches" in note for note in result.notes)


def test_stale_verdict_normalized_trailer_still_matches(substrate_repo, monkeypatch):
    """``sdlc-tool verdict record`` normalizes stored text through
    ``normalize_verdict`` (uppercase, underscores -> spaces), so a recorded
    trailer arrives as ``REVIEW CONTEXT HEAD SHA=<HEX>``. The trailer match
    must survive that normalization — otherwise the exact-SHA freshness leg
    is unreachable for every verdict this repo's own tool stores."""
    normalized = f"APPROVED ... REVIEW CONTEXT HEAD SHA={HEAD_SHA.upper()}"
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: _fresh_verdict(
            verdict=normalized,
            recorded_at="2020-01-01T00:00:00+00:00",
        ),
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is True
    assert any("head_sha trailer matches" in note for note in result.notes)


def test_stale_verdict_normalized_trailer_mismatch_fails(substrate_repo, monkeypatch):
    """A normalized-form trailer for a DIFFERENT commit still fails —
    normalization tolerance must not weaken the mismatch leg."""
    normalized = f"APPROVED ... REVIEW CONTEXT HEAD SHA={'B' * 40}"
    monkeypatch.setattr(
        mp,
        "_run_verdict_get",
        lambda issue, root: _fresh_verdict(verdict=normalized),
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("head_sha trailer mismatch" in check for check in result.failed_checks)


def test_stale_verdict_missing_latest_commit_fails_closed(substrate_repo, monkeypatch):
    """Substrate present + latest-commit data unavailable → fail closed with a
    named leg, never a silent freshness pass."""

    def boom(pr, root):
        raise RuntimeError("gh api pulls commits exited 1")

    monkeypatch.setattr(mp, "_gh_latest_commit", boom)
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("PR latest commit unavailable" in check for check in result.failed_checks)


# ---------------------------------------------------------------------------
# Group (a) PR state legs
# ---------------------------------------------------------------------------


def test_closed_pr_fails_group_a(substrate_repo, monkeypatch):
    monkeypatch.setattr(mp, "_gh_pr_view", lambda pr, root: _good_pr(state="MERGED"))
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("must be OPEN" in check for check in result.failed_checks)


def test_ci_failure_and_pending_both_fail(substrate_repo, monkeypatch):
    monkeypatch.setattr(
        mp,
        "_gh_pr_view",
        lambda pr, root: _good_pr(
            statusCheckRollup=[
                {"name": "tests", "conclusion": "FAILURE"},
                {"name": "lint", "conclusion": "", "state": "PENDING"},
            ]
        ),
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("concluded FAILURE" in check for check in result.failed_checks)
    assert any("still pending" in check for check in result.failed_checks)


def test_missing_issue_link_fails(substrate_repo, monkeypatch):
    monkeypatch.setattr(
        mp, "_gh_pr_view", lambda pr, root: _good_pr(body="No closing keyword here")
    )
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("issue link" in check for check in result.failed_checks)


def test_gh_error_fails_closed(substrate_repo, monkeypatch):
    def boom(pr, root):
        raise RuntimeError("gh pr view exited 1")

    monkeypatch.setattr(mp, "_gh_pr_view", boom)
    result = mp.evaluate_merge_predicate(PR, repo_root=substrate_repo)
    assert result.allowed is False
    assert any("PR state unavailable" in check for check in result.failed_checks)


# ---------------------------------------------------------------------------
# Ordered substrate detection (cycle-2 CONCERN 3)
# ---------------------------------------------------------------------------


def test_substrate_absent_skips_groups_b_c_but_enforces_group_a(tmp_path, monkeypatch):
    """Foreign repo (no addendum): groups (b)/(c) skip with a logged notice,
    group (a) still enforces — and no substrate subprocess is ever invoked."""

    def substrate_boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("substrate call invoked in a substrate-absent repo")

    monkeypatch.setattr(mp, "_run_stage_query", substrate_boom)
    monkeypatch.setattr(mp, "_run_verdict_get", substrate_boom)
    monkeypatch.setattr(mp, "_gh_latest_commit", substrate_boom)
    monkeypatch.setattr(mp, "_gh_pr_view", lambda pr, root: _good_pr(state="CLOSED"))

    result = mp.evaluate_merge_predicate(PR, repo_root=tmp_path)
    assert result.substrate_present is False
    assert result.allowed is False  # group (a) still enforced
    assert any("must be OPEN" in check for check in result.failed_checks)
    assert any("substrate absent" in note for note in result.notes)


def test_substrate_absent_green_pr_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "_gh_pr_view", lambda pr, root: _good_pr())
    result = mp.evaluate_merge_predicate(PR, repo_root=tmp_path)
    assert result.substrate_present is False
    assert result.allowed is True
    assert any("substrate absent" in note for note in result.notes)


def test_substrate_probe_requires_tool_resolvability(tmp_path, monkeypatch):
    """Addendum present but sdlc-tool unresolvable → substrate absent."""
    (tmp_path / "docs" / "sdlc").mkdir(parents=True)
    (tmp_path / "docs" / "sdlc" / "do-merge.md").write_text("# addendum\n")
    monkeypatch.setattr(mp, "_sdlc_tool_resolvable", lambda root: False)
    monkeypatch.setattr(mp, "_gh_pr_view", lambda pr, root: _good_pr())
    result = mp.evaluate_merge_predicate(PR, repo_root=tmp_path)
    assert result.substrate_present is False
    assert result.allowed is True


# ---------------------------------------------------------------------------
# Parity guard (plan Risk 3): skill addendum must consume the shared helper
# ---------------------------------------------------------------------------


def test_do_merge_addendum_references_merge_predicate():
    """The repo addendum must invoke ``tools.merge_predicate`` as its single
    deterministic gate — re-inlined bash copies of the extracted checks would
    drift from the hook (the #1944 class with roles reversed)."""
    text = DO_MERGE_MD.read_text(encoding="utf-8")
    assert "tools.merge_predicate" in text, (
        "docs/sdlc/do-merge.md must reference `python -m tools.merge_predicate` "
        "as the deterministic merge gate shared with the merge-guard hook"
    )


def test_merge_guard_hook_references_merge_predicate():
    hook = (REPO_ROOT / ".claude" / "hooks" / "validators" / "validate_merge_guard.py").read_text(
        encoding="utf-8"
    )
    assert "merge_predicate" in hook
