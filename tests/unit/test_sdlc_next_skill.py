"""Unit tests for tools.sdlc_next_skill._build_context and decide().

Covers the G5 activation regression (#1639): _build_context must populate
``current_plan_hash`` when a plan file exists for the issue, otherwise G5's
loop bound on router row 2b is inert in the CLI path.

Layer 3 (#1761): _build_context now uses compute_plan_body_hash (strips
revision_applied:) instead of compute_plan_hash, so writing
``revision_applied: true`` does not bust the G5 cache.
"""

from __future__ import annotations

from pathlib import Path

from agent.sdlc_router import SKILL_DO_PLAN, SKILL_DO_PR_REVIEW, STATUS_COMPLETED
from tools import sdlc_next_skill


def test_build_context_sets_current_plan_hash_when_plan_exists(tmp_path, monkeypatch):
    """A real plan file for the issue → context["current_plan_hash"] is non-None."""
    plan = tmp_path / "sdlc-1639.md"
    plan.write_text("# Plan\n\nbody content\n", encoding="utf-8")

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1639)

    assert context.get("current_plan_hash") is not None
    assert context["current_plan_hash"].startswith("sha256:")


def test_build_context_omits_hash_when_no_plan(monkeypatch):
    """No plan file for the issue → current_plan_hash key is left unset (None-safe)."""
    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: None,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=999999)

    assert "current_plan_hash" not in context


def test_build_context_omits_hash_when_plan_unreadable(monkeypatch):
    """find_plan_path returns a missing path → compute_plan_hash None → key unset."""
    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: Path("/nonexistent/does-not-exist-plan.md"),
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1639)

    assert "current_plan_hash" not in context


def test_build_context_sets_issue_number_when_plan_exists(tmp_path, monkeypatch):
    """issue_number is set in context so G5 migration can resolve plan_path (#1761)."""
    plan = tmp_path / "sdlc-1761.md"
    plan.write_text("---\nstatus: active\n---\n# Plan\n", encoding="utf-8")

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan,
    )

    context = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    assert context.get("issue_number") == 1761


def test_build_context_uses_body_hash_not_full_bytes(tmp_path, monkeypatch):
    """_build_context must use compute_plan_body_hash so revision_applied: true
    does NOT change the current_plan_hash value (#1761 Layer 3)."""
    plan_before = tmp_path / "before.md"
    plan_after = tmp_path / "after.md"
    plan_before.write_text("---\nstatus: active\n---\n# Plan body\n", encoding="utf-8")
    plan_after.write_text(
        "---\nstatus: active\nrevision_applied: true\n---\n# Plan body\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan_before,
    )
    ctx_before = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    monkeypatch.setattr(
        "tools._sdlc_utils.find_plan_path",
        lambda issue_number: plan_after,
    )
    ctx_after = sdlc_next_skill._build_context(proposed_skill=None, issue_number=1761)

    # Both hashes must be equal — the only diff is revision_applied:, which is stripped.
    assert ctx_before["current_plan_hash"] == ctx_after["current_plan_hash"]


def test_decide_warm_cache_open_pr_defers_to_pr_review_not_plan(monkeypatch):
    """CLI smoke test (#1932 fix b3): sdlc-tool next-skill's decide() must emit a
    PR-stage skill, not /do-plan, for the warm-G5-cache + open-PR +
    non-plan-family-last-dispatch state.

    Mirrors TestG5OpenPrStepAside.test_g5_defers_to_pr_review_when_pr_open in
    tests/unit/test_sdlc_router.py, but drives it through the actual CLI
    entry point (``decide()``) instead of calling ``decide_next_dispatch``
    directly, so the fix is verified on the surface the agent actually
    invokes (``sdlc-tool next-skill``). A full subprocess invocation would
    resolve live gh/session state, which is impractical in a unit test — so
    ``_resolve_enriched`` and ``_build_context`` are monkeypatched to inject
    the fixture stage_states/meta/context in-process instead.
    """
    plan_hash = "sha256:cli-smoke-b3"
    states = {
        "ISSUE": STATUS_COMPLETED,
        "PLAN": STATUS_COMPLETED,
        "CRITIQUE": STATUS_COMPLETED,
        "BUILD": STATUS_COMPLETED,
        "TEST": STATUS_COMPLETED,
        "REVIEW": "pending",
        "DOCS": "pending",
        "MERGE": "pending",
        "_verdicts": {
            "CRITIQUE": {
                "verdict": "NEEDS REVISION",
                "artifact_hash": plan_hash,
            }
        },
    }
    meta = {
        "pr_number": 6789,
        "latest_critique_verdict": "NEEDS REVISION",
        "latest_review_verdict": None,
        "last_dispatched_skill": "/do-test",  # non-plan-family
        "same_stage_dispatch_count": 0,
        "revision_applied": False,
        "plan_revising": False,
    }

    monkeypatch.setattr(
        sdlc_next_skill,
        "_resolve_enriched",
        lambda issue_number, session_id: {"stages": states, "_meta": meta},
    )
    monkeypatch.setattr(
        sdlc_next_skill,
        "_build_context",
        lambda proposed_skill, issue_number: {"current_plan_hash": plan_hash},
    )

    result = sdlc_next_skill.decide(issue_number=6789)

    assert result["dispatched"] is True
    assert result["skill"] == SKILL_DO_PR_REVIEW
    assert result["skill"] != SKILL_DO_PLAN
