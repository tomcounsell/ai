"""Regression tests for issue #2012: the SDLC pipeline's issue-keyed
``PipelineLedger`` survives a driver -> takeover handoff, and the reader
degrades safely when the ledger is genuinely empty.

This module is the plan's explicitly-named "takeover regression test"
artifact -- ``grep -rln "takeover" tests/`` must find this file (see the
Verification table in ``docs/plans/sdlc-issue-keyed-stage-ledger.md``).

Two scenarios, matching the plan's Task 4 ("build-takeover-tests"):

1. ``TestTakeoverRegression`` (AC #1, AC #4) -- the exact #1997 / PR #2008
   incident this whole plan fixes. A "driver" run acquires the per-issue
   run_id lease, writes stage progress through
   ``PipelineStateMachine.for_issue()``, then goes terminal: its lease is
   released (modeling expiry/crash -- the mechanism doesn't matter, only
   that the lease becomes free). A "takeover" run with a DIFFERENT run_id
   and a foreign session_id (mirroring the incident's ``dev-7bd4cf82``
   shape) wins the now-free lease and continues the SAME issue: it
   completes TEST/REVIEW, records a REVIEW verdict via
   ``tools.sdlc_verdict.record_verdict``, and records a ``pr_number`` via
   ``tools.sdlc_meta_set.write_meta`` -- the same two writer entry points a
   real ``/do-pr-review`` + ``/do-build`` invocation would use. The
   regression assertion: ``tools.sdlc_stage_query.query_enriched()`` --
   ``/do-merge``'s read path -- sees ALL of that data after the handoff,
   because the ledger keyed on ``(target_repo, issue_number)`` never moved
   between the driver and the takeover.

2. ``TestEmptyLedgerMergeGateBehavior`` (AC #2) -- ``query_enriched()`` on an
   issue that was NEVER written returns the defined empty-but-valid shape
   (never a crash, never a ``KeyError``/``AttributeError``), and that exact
   real shape -- not a hand-typed stub -- degrades ``merge_predicate``'s
   DOCS/verdict checks to an actionable refusal (or a cold-start reconstruct
   pass), never a silent stall.

Real Popoto/Redis integration for the ledger and issue lock -- no mocks, per
this repo's testing philosophy (CLAUDE.md "Testing Philosophy"). Only the
process-boundary seams that would otherwise shell out to ``gh`` (PR merge
state / CI status lookups in ``_compute_meta``, and the ``sdlc-tool``
subprocess calls inside ``merge_predicate``) are patched, matching the
pattern already used throughout ``tests/unit/test_sdlc_stage_query.py`` and
``tests/unit/test_do_merge_docs_gate.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import tools.merge_predicate as mp
from agent.pipeline_ledger import PipelineLedger
from agent.pipeline_state import PipelineStateMachine
from models.session_lifecycle import release_issue_lock, touch_issue_lock
from tools.sdlc_meta_set import write_meta
from tools.sdlc_stage_query import _default_meta, query_enriched
from tools.sdlc_verdict import get_verdict, record_verdict

_REPO = "test-owner/takeover-repo"
_ISSUE = 900101


def _cleanup_ledger(target_repo: str, issue_number: int) -> None:
    for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
        record.delete()


class TestTakeoverRegression:
    """The exact #1997 / PR #2008 scenario: terminal driver + foreign-slug
    takeover. See module docstring for the full narrative."""

    def setup_method(self):
        _cleanup_ledger(_REPO, _ISSUE)
        # Best-effort: a prior failed run may have left the lock held under
        # one of these run_ids. Nothing to release if unheld.
        self._driver_run_id = None
        self._takeover_run_id = None

    def teardown_method(self):
        _cleanup_ledger(_REPO, _ISSUE)
        if self._takeover_run_id:
            release_issue_lock(_ISSUE, self._takeover_run_id)
        if self._driver_run_id:
            release_issue_lock(_ISSUE, self._driver_run_id)

    def test_driver_progress_survives_takeover_and_is_readable_by_merge_gate(self):
        # --- Driver run: acquires the lease, writes real stage progress ---
        driver_run_id = uuid.uuid4().hex
        self._driver_run_id = driver_run_id
        driver_session_id = "sdlc-local-1997"  # mirrors the incident's driver slug

        driver_lock = touch_issue_lock(
            _ISSUE, driver_run_id, session_id=driver_session_id, target_repo=_REPO
        )
        assert driver_lock.acquired is True
        assert driver_lock.target_repo == _REPO

        driver_sm = PipelineStateMachine.for_issue(_REPO, _ISSUE)
        driver_sm.start_stage("ISSUE")
        driver_sm.complete_stage("ISSUE")
        driver_sm.start_stage("PLAN")
        driver_sm.complete_stage("PLAN")
        driver_sm.start_stage("CRITIQUE")
        driver_sm.complete_stage("CRITIQUE")
        driver_sm.start_stage("BUILD")
        driver_sm.complete_stage("BUILD")

        # Bonus assertion: the ledger key is stable -- get_or_create for the
        # same (target_repo, issue_number) always resolves the SAME record,
        # never a fresh one per caller.
        ledger_after_driver = PipelineLedger.get_or_create(_REPO, _ISSUE)
        assert ledger_after_driver.ledger_key == f"{_REPO}:{_ISSUE}"

        # --- Driver goes terminal: its lease is released ---
        # (Mechanism is irrelevant to the ledger -- crash, TTL expiry, or an
        # explicit release all produce the same observable state: the lease
        # is free. What matters for this regression is that the SECOND
        # writer below has a DIFFERENT run_id/session_id and still reads/
        # writes the SAME ledger record.)
        assert release_issue_lock(_ISSUE, driver_run_id) is True

        # --- Takeover run: a FOREIGN run_id/session_id wins the free lease ---
        takeover_run_id = uuid.uuid4().hex
        self._takeover_run_id = takeover_run_id
        takeover_session_id = "dev-7bd4cf82"  # mirrors the incident's foreign-slug shape
        assert takeover_run_id != driver_run_id
        assert takeover_session_id != driver_session_id

        takeover_lock = touch_issue_lock(
            _ISSUE, takeover_run_id, session_id=takeover_session_id, target_repo=_REPO
        )
        assert takeover_lock.acquired is True
        assert takeover_lock.target_repo == _REPO

        # The takeover continues the SAME issue via for_issue() -- same key,
        # a brand new PipelineStateMachine instance (mirrors a fresh CLI
        # invocation from a different process).
        takeover_sm = PipelineStateMachine.for_issue(_REPO, _ISSUE)
        assert takeover_sm.states["BUILD"] == "completed", (
            "takeover's fresh for_issue() instance did not see the driver's BUILD "
            "progress -- the ledger moved (or was re-keyed) across the handoff"
        )
        takeover_sm.start_stage("TEST")
        takeover_sm.complete_stage("TEST")
        takeover_sm.start_stage("REVIEW")

        # Record the REVIEW verdict via the SAME writer entry point a real
        # /do-pr-review invocation uses (tools.sdlc_verdict._cli_record calls
        # this after lease validation) -- a freshly-fetched ledger, exactly
        # as a new CLI process would.
        ledger_for_verdict = PipelineLedger.get_or_create(_REPO, _ISSUE)
        verdict_record = record_verdict(
            ledger_for_verdict,
            "REVIEW",
            "APPROVED",
            blockers=0,
            tech_debt=0,
            issue_number=_ISSUE,
        )
        assert verdict_record.get("verdict") == "APPROVED"
        takeover_sm.complete_stage("REVIEW")

        # Record pr_number via the SAME writer entry point /do-build uses --
        # tools.sdlc_meta_set.write_meta, gated on the takeover's own lease.
        meta_result = write_meta(
            key="pr_number",
            value="2008",
            issue_number=_ISSUE,
            run_id=takeover_run_id,
        )
        assert meta_result == {"key": "pr_number", "value": 2008}

        # --- Regression assertion: /do-merge's read path sees everything ---
        # _resolve_target_repo_for_read peeks the still-live takeover lease
        # for target_repo, so no env/gh resolution is needed here. Stub only
        # the gh-network boundary (_fetch_pr_merge_state) so this test never
        # shells out.
        with patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)):
            enriched = query_enriched(issue_number=_ISSUE)

        assert enriched["stages"]["BUILD"] == "completed"
        assert enriched["stages"]["TEST"] == "completed"
        assert enriched["stages"]["REVIEW"] == "completed"
        assert enriched["_meta"]["latest_review_verdict"] == "APPROVED"
        assert enriched["_meta"]["pr_number"] == 2008

        # And the verdict is independently readable via the same get_verdict
        # path sdlc_verdict._cli_get uses.
        reloaded_ledger = PipelineLedger.get_or_create(_REPO, _ISSUE)
        assert get_verdict(reloaded_ledger, "REVIEW").get("verdict") == "APPROVED"

        # The ledger key never changed across the whole handoff.
        assert reloaded_ledger.ledger_key == ledger_after_driver.ledger_key


class TestEmptyLedgerMergeGateBehavior:
    """AC #2: defined, tested behavior when the ledger is empty."""

    _REPO = "test-owner/empty-ledger-repo"
    _ISSUE = 900199

    def setup_method(self):
        _cleanup_ledger(self._REPO, self._ISSUE)

    def teardown_method(self):
        _cleanup_ledger(self._REPO, self._ISSUE)

    def test_query_enriched_on_never_written_issue_returns_defined_empty_shape(self):
        """A genuinely never-written issue number -- never a crash, never a
        KeyError/AttributeError, always the documented empty-but-valid
        shape with a real, resolvable target_repo (so this exercises the
        ledger-empty branch specifically, not the target_repo-unresolved
        branch already covered by TestResolveIssueRecord in
        test_sdlc_stage_query.py)."""
        with (
            patch(
                "tools.sdlc_stage_query._resolve_target_repo_for_read",
                return_value=self._REPO,
            ),
            patch("tools.sdlc_stage_query._find_session_by_issue", return_value=None),
        ):
            result = query_enriched(issue_number=self._ISSUE)

        assert result == {"stages": {}, "_meta": _default_meta()}
        assert result["_meta"]["pr_number"] is None
        assert result["_meta"]["latest_review_verdict"] is None

        # A ledger record now exists (get_or_create is not an error path) but
        # carries no stage state -- confirms this test exercised the
        # "ledger resolved but empty" branch, not "target_repo unresolved".
        ledger = PipelineLedger.get_or_create(self._REPO, self._ISSUE)
        assert ledger.stage_states_json == "{}"


class TestEmptyQueryContractWithMergePredicate:
    """Closes the loop between "stage-query really produces this shape on a
    fresh issue" (above) and "merge_predicate handles that shape gracefully"
    (already covered with hand-typed stubs in test_do_merge_docs_gate.py) --
    feeds the REAL query_enriched()/get_verdict() output for a never-written
    issue through the REAL merge_predicate degradation logic.
    """

    _REPO = "test-owner/predicate-contract-repo"
    _ISSUE = 900299
    _PR = 999

    def setup_method(self):
        _cleanup_ledger(self._REPO, self._ISSUE)

    def teardown_method(self):
        _cleanup_ledger(self._REPO, self._ISSUE)

    def _real_empty_stage_query_payload(self) -> dict:
        with (
            patch(
                "tools.sdlc_stage_query._resolve_target_repo_for_read",
                return_value=self._REPO,
            ),
            patch("tools.sdlc_stage_query._find_session_by_issue", return_value=None),
        ):
            return query_enriched(issue_number=self._ISSUE)

    def _real_empty_verdict_payload(self) -> dict:
        ledger = PipelineLedger.get_or_create(self._REPO, self._ISSUE)
        return get_verdict(ledger, "REVIEW")

    def test_real_empty_payload_without_feature_doc_refuses_with_actionable_reason(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "docs" / "sdlc").mkdir(parents=True)
        (tmp_path / "docs" / "sdlc" / "do-merge.md").write_text("# addendum\n")
        (tmp_path / "docs" / "features").mkdir(parents=True)

        monkeypatch.setattr(mp, "_sdlc_tool_resolvable", lambda root: True)
        monkeypatch.setattr(
            mp,
            "_gh_pr_view",
            lambda pr, root: {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}],
                "reviewDecision": "APPROVED",
                "body": f"Does the thing.\n\nCloses #{self._ISSUE}",
                "headRefName": "session/never-written-slug",
            },
        )

        real_payload = self._real_empty_stage_query_payload()
        assert real_payload["stages"] == {}
        monkeypatch.setattr(mp, "_run_stage_query", lambda issue, root: real_payload)
        monkeypatch.setattr(
            mp, "_run_verdict_get", lambda issue, root: self._real_empty_verdict_payload()
        )

        result = mp.evaluate_merge_predicate(self._PR, repo_root=tmp_path)

        # Never a silent stall: refuses with actionable reasons, not a crash.
        assert result.allowed is False
        assert result.failed_checks
        assert any("no recorded REVIEW verdict" in check for check in result.failed_checks)

    def test_real_empty_payload_with_feature_doc_degrades_docs_gate_to_pass(
        self, tmp_path, monkeypatch
    ):
        """The DOCS half of the same real empty payload reconstructs via the
        cold-start docs/features/{slug}.md fallback when that file exists --
        the other half of AC #2's "reconstruct-or-refuse"."""
        (tmp_path / "docs" / "sdlc").mkdir(parents=True)
        (tmp_path / "docs" / "sdlc" / "do-merge.md").write_text("# addendum\n")
        features_dir = tmp_path / "docs" / "features"
        features_dir.mkdir(parents=True)
        (features_dir / "never-written-slug.md").write_text("# feature\n")

        monkeypatch.setattr(mp, "_sdlc_tool_resolvable", lambda root: True)
        monkeypatch.setattr(
            mp,
            "_gh_pr_view",
            lambda pr, root: {
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}],
                "reviewDecision": "APPROVED",
                "body": f"Does the thing.\n\nCloses #{self._ISSUE}",
                "headRefName": "session/never-written-slug",
            },
        )

        real_payload = self._real_empty_stage_query_payload()
        monkeypatch.setattr(mp, "_run_stage_query", lambda issue, root: real_payload)
        # Verdict still empty -- overall predicate still refuses, but the
        # DOCS-specific note demonstrates the reconstruct half in isolation.
        monkeypatch.setattr(
            mp, "_run_verdict_get", lambda issue, root: self._real_empty_verdict_payload()
        )

        result = mp.evaluate_merge_predicate(self._PR, repo_root=tmp_path)

        assert any("degraded" in note for note in result.notes)
        # Still refused overall (no REVIEW verdict), but never a crash and
        # never a silent stall -- the DOCS half alone reconstructed cleanly.
        assert result.allowed is False
        assert any("no recorded REVIEW verdict" in check for check in result.failed_checks)
