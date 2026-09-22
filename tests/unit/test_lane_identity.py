"""Regression tests for SDLC lane identity (issues #2735, #2718).

Both issues are the same defect from two ends: no component records the lane's
identity, so every consumer guesses it and each one guesses differently.

- **#2735** -- ``find_plan_path`` treats *any* textual ``#N`` mention in *any*
  plan file as evidence of ownership, including a mention inside a "Not
  building" No-Gos line. A foreign plan therefore answers for an issue that has
  no plan at all, contaminating ``plan_exists`` / ``revision_applied``.
- **#2718** -- G8 derives a lane slug from the *plan filename* and probes
  ``session/{that-stem}``. When the lane's real branch is ``session/sdlc-{N}``
  the probe finds nothing, G8 declares the PATCH artifact unverified, and the
  router force-dispatches ``/do-patch`` against a clean worktree until the G4
  oscillation cap hard-blocks the lane.

Per #2658's demonstrated-red posture these tests were written before the fix
and captured failing.

Real Popoto/Redis integration -- no mocks on the ledger, per CLAUDE.md's
testing philosophy. Every test cleans up the records it creates.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.pipeline_ledger import PipelineLedger

_TEST_REPO = "test-owner/test-repo"

# Issue numbers chosen well outside the real repo's range so a stray live
# record can never collide with (or be mistaken for) a fixture record.
_ISSUE_LANE = 927350  # the lane whose branch diverges from its plan filename
_ISSUE_OWNED = 927351  # an issue that genuinely owns a plan doc
_ISSUE_MENTIONED = 927352  # an issue merely *mentioned* by that plan
_ISSUE_RESOLVER = 927353  # scratch issue for resolver-contract tests
_ISSUE_ADOPT = 927354  # scratch issue for adopt_lane_slug tests
_ISSUE_META = 927355  # scratch issue for the stage-query `_meta` slug read


def _cleanup(*issue_numbers: int, target_repo: str = _TEST_REPO) -> None:
    for issue_number in issue_numbers:
        for record in PipelineLedger.query.filter(ledger_key=f"{target_repo}:{issue_number}"):
            record.delete()


@pytest.fixture
def clean_ledgers():
    """Remove every fixture ledger before and after the test."""
    issues = (
        _ISSUE_LANE,
        _ISSUE_OWNED,
        _ISSUE_MENTIONED,
        _ISSUE_RESOLVER,
        _ISSUE_ADOPT,
        _ISSUE_META,
    )
    _cleanup(*issues)
    yield
    _cleanup(*issues)


def _write_plans_dir(tmp_path, files: dict[str, str]):
    """Materialize ``docs/plans/`` under ``tmp_path`` and return the repo root."""
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (plans_dir / name).write_text(text, encoding="utf-8")
    return tmp_path


def _tracking_frontmatter(issue_number: int, **extra: str) -> str:
    lines = [
        "---",
        "status: Ready",
        f"tracking: https://github.com/{_TEST_REPO}/issues/{issue_number}",
    ]
    lines.extend(f"{key}: {value}" for key, value in extra.items())
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# #2735 -- plan ownership is a `tracking:` line, never a bare mention
# ---------------------------------------------------------------------------


class TestFindPlanPathOwnership:
    """``find_plan_path`` resolves on exactly one rung: ``tracking:``."""

    def test_bare_mention_does_not_own_the_plan(self, tmp_path, monkeypatch):
        """A plan tracking issue A that merely mentions #B is not B's plan.

        This is the live #2663 / #2716 case: `session-liveness-tick-counter.md`
        tracks #2716 and mentions #2663 twice, and today both issues resolve to
        it.
        """
        from tools.lane_identity import find_plan_path

        repo_root = _write_plans_dir(
            tmp_path,
            {
                "owned-by-other.md": _tracking_frontmatter(_ISSUE_OWNED)
                + f"\n# Plan\n\nRelated work landed in #{_ISSUE_MENTIONED}.\n",
            },
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))

        assert find_plan_path(_ISSUE_MENTIONED) is None

    def test_tracking_frontmatter_still_resolves(self, tmp_path, monkeypatch):
        """The owning issue still resolves to its own plan."""
        from tools.lane_identity import find_plan_path

        repo_root = _write_plans_dir(
            tmp_path,
            {
                "owned-by-other.md": _tracking_frontmatter(_ISSUE_OWNED)
                + f"\n# Plan\n\nRelated work landed in #{_ISSUE_MENTIONED}.\n",
            },
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))

        resolved = find_plan_path(_ISSUE_OWNED)
        assert resolved is not None
        assert resolved.name == "owned-by-other.md"

    def test_no_go_section_mention_never_owns(self, tmp_path, monkeypatch):
        """A "Not building" line is the *opposite* of ownership.

        A No-Gos entry is exactly where a plan names an issue it explicitly
        does not own, so a resolver that reads it as ownership is confidently
        wrong in the one place the document says so.
        """
        from tools.lane_identity import find_plan_path

        repo_root = _write_plans_dir(
            tmp_path,
            {
                "owned-by-other.md": _tracking_frontmatter(_ISSUE_OWNED)
                + "\n# Plan\n\n## No-Gos (Out of Scope)\n\n"
                + f"- Not building the #{_ISSUE_MENTIONED} rework here.\n",
            },
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))

        assert find_plan_path(_ISSUE_MENTIONED) is None

    def test_no_plan_at_all_resolves_to_none(self, tmp_path, monkeypatch):
        """An issue with no plan doc anywhere resolves to None, not a guess."""
        from tools.lane_identity import find_plan_path

        repo_root = _write_plans_dir(tmp_path, {})
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))

        assert find_plan_path(_ISSUE_MENTIONED) is None

    def test_plans_dir_ladder_falls_back_to_git_toplevel(self, tmp_path, monkeypatch):
        """With ``SDLC_TARGET_REPO`` unset the ladder uses the git toplevel.

        This test is also the guard for the module-attribute import rule: it
        monkeypatches the literal ``tools._sdlc_utils._git_toplevel``, which
        only takes effect if ``lane_identity`` calls it through the module
        rather than binding the name at import time. Fourteen existing tests
        depend on that same property.
        """
        from tools.lane_identity import find_plan_path

        repo_root = _write_plans_dir(
            tmp_path,
            {"owned-by-other.md": _tracking_frontmatter(_ISSUE_OWNED) + "\n# Plan\n"},
        )
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        monkeypatch.setattr("tools._sdlc_utils._git_toplevel", lambda *a, **k: repo_root)

        resolved = find_plan_path(_ISSUE_OWNED)
        assert resolved is not None
        assert resolved.name == "owned-by-other.md"


# ---------------------------------------------------------------------------
# #2735 AC 4 -- `_meta` is not contaminated across issues
# ---------------------------------------------------------------------------


class TestMetaContamination:
    """``stage-query`` never reports another issue's plan flags."""

    def test_meta_flags_absent_for_issue_with_no_plan(self, tmp_path, monkeypatch):
        """`plan_exists` / `revision_applied` come from the issue's OWN plan.

        Today `sdlc-tool stage-query --issue-number 2663` reports
        `revision_applied: true` read verbatim out of #2716's frontmatter for
        an issue that has never had a plan. Those flags feed router rows
        4b/4c and guard G7.
        """
        from tools.sdlc_stage_query import _compute_meta

        repo_root = _write_plans_dir(
            tmp_path,
            {
                "owned-by-other.md": _tracking_frontmatter(
                    _ISSUE_OWNED,
                    revision_applied="true",
                    revision_applied_at="2026-08-01T00:00:00Z",
                )
                + f"\n# Plan\n\n## No-Gos\n\n- Not building #{_ISSUE_MENTIONED}.\n",
            },
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))
        monkeypatch.setenv("GH_REPO", _TEST_REPO)

        with (
            patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None, None)),
            patch("tools.sdlc_stage_query._lookup_pr", return_value=None),
        ):
            meta = _compute_meta({}, None, _ISSUE_MENTIONED)

        assert meta["plan_exists"] is False
        assert meta["revision_applied"] is False
        assert meta["revision_applied_at"] is None


class _SessionStub:
    """Minimal ``AgentSession`` shape ``_compute_meta`` reads off a session."""

    def __init__(self, slug=None, pr_number=None):
        self.slug = slug
        self.pr_number = pr_number


class TestMetaLaneSlugRead:
    """``_meta['slug']`` is a two-rung read, and it never mints.

    ``slug_source`` is what lets an operator tell "the branch check was skipped
    because no identity is recorded" from "the branch check ran and verified
    clean" -- the two states #2718 conflated.
    """

    @pytest.fixture
    def meta_env(self, tmp_path, monkeypatch):
        """An empty plans dir and a pinned repo, so only the slug read varies."""
        repo_root = _write_plans_dir(tmp_path, {})
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))
        monkeypatch.setenv("GH_REPO", _TEST_REPO)
        return repo_root

    def _meta(self, session):
        from tools.sdlc_stage_query import _compute_meta

        with (
            patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None, None)),
            patch("tools.sdlc_stage_query._lookup_pr", return_value=None),
        ):
            return _compute_meta({}, session, _ISSUE_META)

    def test_recorded_ledger_slug_wins(self, meta_env, clean_ledgers):
        """Rung 1. A recorded identity outranks whatever a session carries."""
        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_META)
        ledger.slug = "the-recorded-lane"
        ledger.save(update_fields=["slug"])

        meta = self._meta(_SessionStub(slug="a-stale-session-slug"))

        assert meta["slug"] == "the-recorded-lane"
        assert meta["slug_source"] == "recorded"

    def test_session_slug_is_the_cold_path_fallback(self, meta_env, clean_ledgers):
        """Rung 2: a pre-cutover lane never heals, so its session is the source.

        Dropping this leg would retire branch-head PR recovery for exactly the
        lanes that have no recorded identity and never will.
        """
        meta = self._meta(_SessionStub(slug="a-pre-cutover-lane"))

        assert meta["slug"] == "a-pre-cutover-lane"
        assert meta["slug_source"] == "session"

    def test_neither_rung_answers_is_unresolved(self, meta_env, clean_ledgers):
        meta = self._meta(None)

        assert meta["slug"] is None
        assert meta["slug_source"] == "unresolved"

    def test_meta_never_mints_and_creates_no_ledger(self, meta_env, clean_ledgers):
        """``_compute_meta`` runs for any issue an operator names, not just lanes.

        Healing here would mint an identity for a non-lane issue, contradicting
        "minted exactly once at lane start".
        """
        meta = self._meta(None)

        assert meta["slug"] is None
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_META) is None
        assert list(PipelineLedger.query.filter(ledger_key=f"{_TEST_REPO}:{_ISSUE_META}")) == []


# ---------------------------------------------------------------------------
# #2718 -- G8 probes the lane branch, not the plan filename
# ---------------------------------------------------------------------------


class TestG8LaneBranchDivergence:
    """G8's PATCH check reads the recorded lane slug, never a plan stem."""

    def test_g8_does_not_fire_when_lane_branch_diverges_from_plan_slug(
        self, tmp_path, monkeypatch, clean_ledgers
    ):
        """A lane named ``sdlc-{N}`` with a human-named plan doc verifies clean.

        This is #2718's exact shape: plan doc ``some-other-name.md`` tracking
        issue N, real branch ``session/sdlc-N`` pushed, PATCH claimed
        completed. Today G8 derives ``some-other-name``, probes a branch that
        never existed, and force-dispatches ``/do-patch`` forever.
        """
        from tools import sdlc_next_skill
        from tools.lane_identity import mint_lane_slug

        repo_root = _write_plans_dir(
            tmp_path,
            {"some-other-name.md": _tracking_frontmatter(_ISSUE_LANE) + "\n# Plan\n"},
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))
        monkeypatch.setenv("GH_REPO", _TEST_REPO)

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_LANE)
        ledger.slug = mint_lane_slug(_ISSUE_LANE)
        ledger.save(update_fields=["slug"])

        probed: list[str] = []

        def fake_branch_pushed(name: str) -> bool:
            # The world contains exactly one pushed branch for this lane.
            probed.append(name)
            return name.removeprefix("session/") == f"sdlc-{_ISSUE_LANE}"

        monkeypatch.setattr(sdlc_next_skill, "_check_branch_pushed", fake_branch_pushed)
        monkeypatch.setattr(sdlc_next_skill, "_check_plan_committed_on_main", lambda _: True)

        result = sdlc_next_skill._verify_stage_artifacts_live(
            {"PLAN": "completed", "PATCH": "completed"},
            {"pr_number": None},
            _ISSUE_LANE,
        )

        assert result == {}, (
            f"G8 fired against a lane whose branch is pushed; probed {probed!r} "
            f"instead of the recorded lane branch session/sdlc-{_ISSUE_LANE}"
        )

    def test_g8_patch_check_noops_when_slug_unresolvable(self, tmp_path, monkeypatch):
        """With no recorded slug the PATCH check skips rather than guessing."""
        from tools import sdlc_next_skill

        repo_root = _write_plans_dir(
            tmp_path,
            {"some-other-name.md": _tracking_frontmatter(_ISSUE_LANE) + "\n# Plan\n"},
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))
        monkeypatch.setenv("GH_REPO", _TEST_REPO)

        probed: list[str] = []

        def fake_branch_pushed(name: str) -> bool:
            probed.append(name)
            return False

        monkeypatch.setattr(sdlc_next_skill, "_check_branch_pushed", fake_branch_pushed)
        monkeypatch.setattr(sdlc_next_skill, "_check_plan_committed_on_main", lambda _: True)

        result = sdlc_next_skill._verify_stage_artifacts_live(
            {"PLAN": "completed", "PATCH": "completed"},
            {"pr_number": None},
            _ISSUE_LANE,
        )

        assert result == {}
        assert probed == [], f"probed a guessed branch name: {probed!r}"


# ---------------------------------------------------------------------------
# Resolver contracts
# ---------------------------------------------------------------------------


class TestMintAndBranchName:
    def test_mint_lane_slug_shape(self):
        from tools.lane_identity import mint_lane_slug

        assert mint_lane_slug(2735) == "sdlc-2735"

    def test_lane_branch_name_applies_the_session_prefix(self):
        from tools.lane_identity import lane_branch_name

        assert lane_branch_name("sdlc-2735") == "session/sdlc-2735"
        assert lane_branch_name("session-liveness-tick-counter") == (
            "session/session-liveness-tick-counter"
        )

    def test_lane_branch_name_is_none_safe(self):
        from tools.lane_identity import lane_branch_name

        assert lane_branch_name(None) is None
        assert lane_branch_name("") is None


class TestResolveLaneSlugReadPath:
    """``allow_heal=False`` is inert: no ledger, no write, no mint."""

    def test_read_path_creates_no_ledger(self, clean_ledgers):
        from tools.lane_identity import resolve_lane_slug

        assert resolve_lane_slug(_ISSUE_RESOLVER, target_repo=_TEST_REPO) is None
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_RESOLVER) is None

    def test_read_path_returns_recorded_slug(self, clean_ledgers):
        from tools.lane_identity import resolve_lane_slug

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_RESOLVER)
        ledger.slug = "a-human-named-lane"
        ledger.save(update_fields=["slug"])

        assert resolve_lane_slug(_ISSUE_RESOLVER, target_repo=_TEST_REPO) == "a-human-named-lane"

    def test_whitespace_slug_is_treated_as_empty(self, clean_ledgers):
        from tools.lane_identity import resolve_lane_slug

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_RESOLVER)
        ledger.slug = "   "
        ledger.save(update_fields=["slug"])

        assert resolve_lane_slug(_ISSUE_RESOLVER, target_repo=_TEST_REPO) is None

    def test_unresolvable_target_repo_returns_none_and_writes_nothing(self, clean_ledgers):
        """No ``None:{issue}`` phantom key is ever assembled -- on either arm."""
        from tools.lane_identity import resolve_lane_slug

        with patch("tools._sdlc_utils.resolve_target_repo_for_read", return_value=None):
            assert resolve_lane_slug(_ISSUE_RESOLVER) is None
            assert resolve_lane_slug(_ISSUE_RESOLVER, allow_heal=True) is None

        assert list(PipelineLedger.query.filter(ledger_key=f"None:{_ISSUE_RESOLVER}")) == []


class TestResolveLaneSlugHealPath:
    """``allow_heal=True`` mints once, conditional-on-empty, never overwrites."""

    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch):
        """No test here touches origin unless it installs its own listing."""
        import tools.lane_identity as lane_identity

        monkeypatch.setattr(lane_identity, "_ls_remote_heads", dict)

    def test_heal_creates_the_ledger_and_mints(self, clean_ledgers):
        from tools.lane_identity import resolve_lane_slug

        resolved = resolve_lane_slug(_ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO)

        assert resolved == f"sdlc-{_ISSUE_RESOLVER}"
        ledger = PipelineLedger.get(_TEST_REPO, _ISSUE_RESOLVER)
        assert ledger is not None
        assert ledger.slug == f"sdlc-{_ISSUE_RESOLVER}"

    def test_heal_never_overwrites_a_recorded_slug(self, clean_ledgers):
        from tools.lane_identity import resolve_lane_slug

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_RESOLVER)
        ledger.slug = "a-human-named-lane"
        ledger.save(update_fields=["slug"])

        resolved = resolve_lane_slug(_ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO)

        assert resolved == "a-human-named-lane"
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_RESOLVER).slug == "a-human-named-lane"

    def test_heal_is_idempotent(self, clean_ledgers):
        from tools.lane_identity import resolve_lane_slug

        first = resolve_lane_slug(_ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO)
        second = resolve_lane_slug(_ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO)

        assert first == second
        assert (
            len(list(PipelineLedger.query.filter(ledger_key=f"{_TEST_REPO}:{_ISSUE_RESOLVER}")))
            == 1
        )

    def test_heal_does_not_touch_stage_states(self, clean_ledgers):
        """``save(update_fields=["slug"])`` -- the stage blob is never rewritten."""
        from tools.lane_identity import resolve_lane_slug

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_RESOLVER)
        ledger.stage_states_json = '{"PLAN": "completed"}'
        ledger.save(update_fields=["stage_states_json"])

        resolve_lane_slug(_ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO)

        reloaded = PipelineLedger.get(_TEST_REPO, _ISSUE_RESOLVER)
        assert reloaded.stage_states_json == '{"PLAN": "completed"}'
        assert reloaded.slug == f"sdlc-{_ISSUE_RESOLVER}"

    def test_ladder_adopts_a_differently_shaped_branch_via_the_pr_head(
        self, clean_ledgers, monkeypatch
    ):
        """Rung 2 is shape-agnostic and precedes the fixed-shape probe.

        ``.worktrees/sdlc-1920`` and ``.worktrees/sdlc-1997`` sit on
        ``session/dev-<hash>`` branches. A probe that only knows the
        issue-derived shape would miss them and invent a competing identity
        for a lane that already has a branch -- the exact failure the ladder
        exists to prevent.
        """
        import tools.lane_identity as lane_identity

        head_sha = "a" * 40
        monkeypatch.setattr(
            lane_identity,
            "_ls_remote_heads",
            lambda: {
                "refs/heads/session/dev-7bd4cf82": head_sha,
                "refs/heads/session/unrelated-lane": "b" * 40,
            },
        )
        monkeypatch.setattr(
            "tools.pr_head_resolver.resolve_pr_head_sha",
            lambda *a, **k: head_sha,
        )

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_RESOLVER)
        ledger.pr_number = 4242
        ledger.save(update_fields=["pr_number"])

        resolved = lane_identity.resolve_lane_slug(
            _ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO
        )
        assert resolved == "dev-7bd4cf82"
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_RESOLVER).slug == "dev-7bd4cf82"

    def test_ambiguous_pr_head_match_falls_through_to_the_mint(self, clean_ledgers, monkeypatch):
        """Two branches at the same tip is a per-invocation identity, so rung 2
        declines rather than picking one by listing order."""
        import tools.lane_identity as lane_identity

        head_sha = "a" * 40
        monkeypatch.setattr(
            lane_identity,
            "_ls_remote_heads",
            lambda: {
                "refs/heads/session/dev-7bd4cf82": head_sha,
                "refs/heads/session/dev-81976da0": head_sha,
            },
        )
        monkeypatch.setattr(
            "tools.pr_head_resolver.resolve_pr_head_sha",
            lambda *a, **k: head_sha,
        )

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_RESOLVER)
        ledger.pr_number = 4242
        ledger.save(update_fields=["pr_number"])

        resolved = lane_identity.resolve_lane_slug(
            _ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO
        )
        assert resolved == f"sdlc-{_ISSUE_RESOLVER}"

    def test_ladder_adopts_a_pushed_lane_branch(self, clean_ledgers, monkeypatch):
        """Rung 3: an already-pushed lane branch on origin is adopted.

        This is what lets a mid-pipeline lane like #2663 capture the identity
        it already has instead of the resolver inventing a rival one.
        """
        import tools.lane_identity as lane_identity

        probed = []

        def fake_heads():
            probed.append(True)
            return {f"refs/heads/session/sdlc-{_ISSUE_RESOLVER}": "c" * 40}

        monkeypatch.setattr(lane_identity, "_ls_remote_heads", fake_heads)

        resolved = lane_identity.resolve_lane_slug(
            _ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO
        )
        assert resolved == f"sdlc-{_ISSUE_RESOLVER}"
        assert probed, "rung 3 never consulted origin"

    def test_ladder_never_adopts_a_plan_filename_stem(self, clean_ledgers, monkeypatch, tmp_path):
        """There is no plan-stem rung: a plan filename is not a lane identity.

        A plan doc that *tracks* the issue is still only a document that names
        it. Deriving the lane slug from its filename is the defect this module
        closes, and because the write is no-overwrite a wrong adoption could
        never be corrected. The ladder falls through to the mint.
        """
        import tools.lane_identity as lane_identity

        repo_root = _write_plans_dir(
            tmp_path,
            {
                "session-liveness-tick-counter.md": _tracking_frontmatter(_ISSUE_RESOLVER)
                + "\n# Plan\n"
            },
        )
        monkeypatch.setenv("SDLC_TARGET_REPO", str(repo_root))
        monkeypatch.setattr(lane_identity, "_ls_remote_heads", dict)

        resolved = lane_identity.resolve_lane_slug(
            _ISSUE_RESOLVER, allow_heal=True, target_repo=_TEST_REPO
        )
        assert resolved == f"sdlc-{_ISSUE_RESOLVER}"
        assert not hasattr(lane_identity, "_adopt_tracking_plan_stem")


class TestAdoptLaneSlug:
    """``adopt_lane_slug`` records what the caller already knows -- no ladder.

    The ladder is for callers that must *discover* an identity. A caller
    holding a pushed branch name has already adopted one from the world, and
    re-deriving over it is exactly the #2718 divergence this module closes.
    """

    def test_adopt_records_a_caller_supplied_slug(self, clean_ledgers):
        from tools.lane_identity import adopt_lane_slug

        assert adopt_lane_slug(_ISSUE_ADOPT, "sdlc-lane-a", target_repo=_TEST_REPO) == (
            "sdlc-lane-a"
        )
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_ADOPT).slug == "sdlc-lane-a"

    def test_adopt_records_a_human_named_branch_verbatim(self, clean_ledgers):
        """#2718's exact shape: the recorded value is the branch, not ``sdlc-N``.

        This is the load-bearing case. Re-deriving here would mint
        ``sdlc-{N}`` for a lane whose branch is ``session-liveness-tick-counter``
        and every downstream branch probe would then miss.
        """
        from tools.lane_identity import adopt_lane_slug, mint_lane_slug

        adopt_lane_slug(_ISSUE_ADOPT, "session-liveness-tick-counter", target_repo=_TEST_REPO)

        recorded = PipelineLedger.get(_TEST_REPO, _ISSUE_ADOPT).slug
        assert recorded == "session-liveness-tick-counter"
        assert recorded != mint_lane_slug(_ISSUE_ADOPT)

    def test_adopt_never_overwrites_a_recorded_slug(self, clean_ledgers):
        """Conditional-on-empty: the loser returns the winner's value."""
        from tools.lane_identity import adopt_lane_slug

        ledger = PipelineLedger.get_or_create(_TEST_REPO, _ISSUE_ADOPT)
        ledger.slug = "the-winner"
        ledger.save(update_fields=["slug"])

        assert adopt_lane_slug(_ISSUE_ADOPT, "a-rival-name", target_repo=_TEST_REPO) == "the-winner"
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_ADOPT).slug == "the-winner"

    @pytest.mark.parametrize("slug", [None, "", "   "])
    def test_adopt_declines_a_blank_slug(self, clean_ledgers, slug):
        """A lane whose identity is a run of spaces is not an identity."""
        from tools.lane_identity import adopt_lane_slug

        assert adopt_lane_slug(_ISSUE_ADOPT, slug, target_repo=_TEST_REPO) is None
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_ADOPT) is None

    @pytest.mark.parametrize("issue_number", [0, None, -5])
    def test_adopt_declines_a_falsy_or_negative_issue_number(self, clean_ledgers, issue_number):
        from tools.lane_identity import adopt_lane_slug

        assert adopt_lane_slug(issue_number, "some-lane", target_repo=_TEST_REPO) is None
        assert list(PipelineLedger.query.filter(ledger_key=f"{_TEST_REPO}:{issue_number}")) == []

    def test_adopt_writes_nothing_when_the_target_repo_is_unresolvable(self, clean_ledgers):
        """No ``None:{issue}`` phantom record is ever assembled."""
        from tools.lane_identity import adopt_lane_slug

        with patch("tools._sdlc_utils.resolve_target_repo_for_read", return_value=None):
            assert adopt_lane_slug(_ISSUE_ADOPT, "some-lane") is None

        assert list(PipelineLedger.query.filter(ledger_key=f"None:{_ISSUE_ADOPT}")) == []
        assert PipelineLedger.get(_TEST_REPO, _ISSUE_ADOPT) is None

    def test_adopt_walks_no_ladder_and_consults_no_git(self, clean_ledgers, monkeypatch):
        """Unlike the healing arm, adoption makes no git subprocess at all.

        Both probes are patched through the module attribute (the same idiom
        the ladder tests use -- ``_git_toplevel`` is reached through
        ``tools._sdlc_utils`` rather than bound at import, which is what makes
        the patch bite).
        """
        import tools.lane_identity as lane_identity

        probed: list[str] = []

        monkeypatch.setattr(
            lane_identity, "_ls_remote_heads", lambda: probed.append("ls-remote") or {}
        )
        monkeypatch.setattr(
            "tools._sdlc_utils._git_toplevel", lambda *a, **k: probed.append("toplevel")
        )

        resolved = lane_identity.adopt_lane_slug(
            _ISSUE_ADOPT, "session-liveness-tick-counter", target_repo=_TEST_REPO
        )

        assert resolved == "session-liveness-tick-counter"
        assert probed == [], f"adoption walked the ladder: {probed!r}"


# ---------------------------------------------------------------------------
# The single-minter invariant (issue #1915's unfinished half)
# ---------------------------------------------------------------------------


class TestSingleMinter:
    def test_derive_slug_from_message_is_gone(self):
        """``tools/valor_session.py`` no longer mints a competing identity."""
        import tools.valor_session as valor_session

        assert not hasattr(valor_session, "_derive_slug_from_message")

    def test_issue_number_from_message_replaces_it(self):
        from tools.valor_session import _issue_number_from_message

        assert _issue_number_from_message("handle issue #1109") == 1109
        assert _issue_number_from_message("Start the pipeline for issue 735") == 735
        assert _issue_number_from_message("do something generic") is None
        assert _issue_number_from_message("") is None


# ---------------------------------------------------------------------------
# Branch identity (#3411): the record is the truth, the slug is only a seed
# ---------------------------------------------------------------------------


class _BranchSessionStub:
    """Minimal ``AgentSession`` shape the branch accessor reads.

    Popoto stores an unset string field as ``""``, never ``None``, so the
    default here is ``""`` rather than ``None`` on purpose -- a stub that
    defaulted to ``None`` would never exercise Risk 4.
    """

    def __init__(self, branch_name="", slug=None, session_id="", working_dir=""):
        self.branch_name = branch_name
        self.slug = slug
        self.session_id = session_id
        self.working_dir = working_dir
        self.commit_sha = ""


def _git_stdout(cwd, *args):
    import subprocess

    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout


def _init_repo(path):
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=path, capture_output=True, check=True)
    (path / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True)
    return path


class TestReadWorktreeBranch:
    """The one lane-scoped spelling of ``rev-parse --abbrev-ref HEAD``."""

    def test_reads_the_live_branch(self, tmp_path):
        from tools.lane_identity import read_worktree_branch

        repo = _init_repo(tmp_path / "repo")
        assert read_worktree_branch(repo) == "main"

    def test_detached_head_is_not_a_branch_name(self, tmp_path):
        import subprocess

        from tools.lane_identity import read_worktree_branch

        repo = _init_repo(tmp_path / "repo")
        subprocess.run(["git", "checkout", "--detach"], cwd=repo, capture_output=True, check=True)
        # Spike-2: git answers with the literal string "HEAD" here.
        assert read_worktree_branch(repo) is None

    def test_missing_path_returns_none(self, tmp_path):
        from tools.lane_identity import read_worktree_branch

        assert read_worktree_branch(tmp_path / "nope") is None

    def test_non_repo_path_returns_none(self, tmp_path):
        from tools.lane_identity import read_worktree_branch

        plain = tmp_path / "plain"
        plain.mkdir()
        assert read_worktree_branch(plain) is None

    def test_blank_path_returns_none(self):
        from tools.lane_identity import read_worktree_branch

        assert read_worktree_branch(None) is None
        assert read_worktree_branch("") is None

    def test_subprocess_timeout_returns_none(self, tmp_path):
        import subprocess

        from tools import lane_identity

        repo = _init_repo(tmp_path / "repo")
        with patch.object(
            lane_identity.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            assert lane_identity.read_worktree_branch(repo) is None


class TestWorktreeIsDetached:
    """The tri-state detachment probe. ``None`` is not ``True`` (#3411).

    ``checkpoint_branch_state`` clears ``branch_name`` on ``True`` alone. Every
    inconclusive answer must therefore be ``None``, because the obvious
    "simplification" -- folding any non-zero exit into ``True``, since ``-q``
    suppresses stderr for the ordinary detached case -- would make a vanished
    or non-repo path read as confirmed detachment and erase a good record.
    These tests exist to make that refactor fail.
    """

    def test_on_a_branch_is_false(self, tmp_path):
        from tools.lane_identity import worktree_is_detached

        repo = _init_repo(tmp_path / "repo")
        assert worktree_is_detached(repo) is False

    def test_detached_head_is_true(self, tmp_path):
        import subprocess

        from tools.lane_identity import worktree_is_detached

        repo = _init_repo(tmp_path / "repo")
        subprocess.run(["git", "checkout", "--detach"], cwd=repo, capture_output=True, check=True)
        assert worktree_is_detached(repo) is True

    def test_non_repo_path_is_none_not_detached(self, tmp_path):
        from tools.lane_identity import worktree_is_detached

        plain = tmp_path / "plain"
        plain.mkdir()
        # `symbolic-ref` exits 128 here, not 1. Reading that as detachment is
        # the erasure bug; `is None` is the assertion, `is not True` the point.
        result = worktree_is_detached(plain)
        assert result is None
        assert result is not True

    def test_missing_path_is_none(self, tmp_path):
        from tools.lane_identity import worktree_is_detached

        assert worktree_is_detached(tmp_path / "nope") is None

    def test_blank_path_is_none(self):
        from tools.lane_identity import worktree_is_detached

        assert worktree_is_detached(None) is None
        assert worktree_is_detached("") is None

    def test_subprocess_timeout_is_none(self, tmp_path):
        import subprocess

        from tools import lane_identity

        repo = _init_repo(tmp_path / "repo")
        with patch.object(
            lane_identity.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            assert lane_identity.worktree_is_detached(repo) is None


class TestResolveLaneBranch:
    """Precedence: the record, then the seed. Never ``"HEAD"``, never ``""``."""

    def test_recorded_branch_wins_over_the_seed(self):
        from tools.lane_identity import resolve_lane_branch

        session = _BranchSessionStub(branch_name="eval/code-simplifier", slug="eval-lane")
        assert resolve_lane_branch(session) == "eval/code-simplifier"

    def test_empty_record_falls_back_to_the_seed(self):
        from tools.lane_identity import resolve_lane_branch

        # Risk 4: an `is None` test here would read "" as a *set* record and
        # hand the launch guard an empty expectation, failing every lane.
        assert resolve_lane_branch(_BranchSessionStub(branch_name="", slug="sdlc-3411")) == (
            "session/sdlc-3411"
        )

    def test_whitespace_record_falls_back_to_the_seed(self):
        from tools.lane_identity import resolve_lane_branch

        assert resolve_lane_branch(_BranchSessionStub(branch_name="   ", slug="sdlc-3411")) == (
            "session/sdlc-3411"
        )

    def test_none_record_falls_back_to_the_seed(self):
        from tools.lane_identity import resolve_lane_branch

        assert resolve_lane_branch(_BranchSessionStub(branch_name=None, slug="sdlc-3411")) == (
            "session/sdlc-3411"
        )

    def test_recorded_head_literal_falls_back_to_the_seed(self):
        from tools.lane_identity import resolve_lane_branch

        assert resolve_lane_branch(_BranchSessionStub(branch_name="HEAD", slug="sdlc-3411")) == (
            "session/sdlc-3411"
        )

    def test_recorded_branch_is_stripped(self):
        from tools.lane_identity import resolve_lane_branch

        assert resolve_lane_branch(_BranchSessionStub(branch_name="  session/x  ")) == "session/x"

    def test_slugless_session_falls_back_to_the_session_id_seed(self):
        # The session-id seed is git-sanitised by ``_session_branch_name``, which
        # is asserted here rather than re-spelled: a literal expectation would
        # make this test a second, drifting copy of that sanitiser.
        from agent.session_revival import _session_branch_name
        from tools.lane_identity import resolve_lane_branch

        session = _BranchSessionStub(slug=None, session_id="tg_valor_-100_1473")
        assert resolve_lane_branch(session) == _session_branch_name("tg_valor_-100_1473")
        assert resolve_lane_branch(session).startswith("session/")

    def test_nothing_recorded_and_nothing_to_seed_is_none(self):
        from tools.lane_identity import resolve_lane_branch

        assert resolve_lane_branch(_BranchSessionStub()) is None
        assert resolve_lane_branch(None) is None

    def test_never_hands_the_guard_an_empty_expectation(self):
        """The interface contract between the accessor and the #1377 guard.

        ``verify_worktree_branch`` raises ``ValueError`` on an empty
        ``expected_branch``, so a blank answer would turn every lane's launch
        into a crash. The accessor answers a real name or ``None``.
        """
        from tools.lane_identity import resolve_lane_branch

        for stub in (
            _BranchSessionStub(),
            _BranchSessionStub(branch_name=""),
            _BranchSessionStub(branch_name="  "),
            _BranchSessionStub(branch_name="HEAD"),
            _BranchSessionStub(branch_name="HEAD", slug="  "),
            _BranchSessionStub(slug=""),
        ):
            answer = resolve_lane_branch(stub)
            assert answer is None or answer.strip() == answer != ""


class TestRefreshLaneBranch:
    """Reads the live ``HEAD``; the WRITE belongs to ``checkpoint_branch_state``."""

    def test_delegates_the_write_and_returns_the_record(self, tmp_path):
        from tools import lane_identity

        repo = _init_repo(tmp_path / "repo")
        session = _BranchSessionStub(slug="sdlc-3411", working_dir=str(repo))

        def fake_checkpoint(s):
            s.branch_name = "main"

        with patch.object(
            lane_identity, "_checkpoint_branch_state", side_effect=fake_checkpoint
        ) as writer:
            assert lane_identity.refresh_lane_branch(session, repo) == "main"
        writer.assert_called_once_with(session)

    def test_detached_worktree_refreshes_to_no_branch(self, tmp_path):
        import subprocess

        from tools import lane_identity

        repo = _init_repo(tmp_path / "repo")
        subprocess.run(["git", "checkout", "--detach"], cwd=repo, capture_output=True, check=True)
        session = _BranchSessionStub(working_dir=str(repo))

        with patch.object(lane_identity, "_checkpoint_branch_state") as writer:
            assert lane_identity.refresh_lane_branch(session, repo) is None
        writer.assert_called_once_with(session)

    def test_does_not_write_the_record_itself(self, tmp_path):
        """``checkpoint_branch_state`` is the sole writer -- assert by omission."""
        from tools import lane_identity

        repo = _init_repo(tmp_path / "repo")
        session = _BranchSessionStub(branch_name="stale/value", working_dir=str(repo))

        with patch.object(lane_identity, "_checkpoint_branch_state"):
            lane_identity.refresh_lane_branch(session, repo)
        assert session.branch_name == "stale/value"

    def test_no_session_or_no_path_is_a_noop(self, tmp_path):
        from tools import lane_identity

        repo = _init_repo(tmp_path / "repo")
        with patch.object(lane_identity, "_checkpoint_branch_state") as writer:
            assert lane_identity.refresh_lane_branch(None, repo) is None
            assert lane_identity.refresh_lane_branch(_BranchSessionStub(), None) is None
        writer.assert_not_called()


class TestSweep:
    """Reports divergence; mutates nothing (the [EXTERNAL] No-Go)."""

    def _lane(self, tmp_path, slug, *, branch=None, detach=False, dirty=False):
        """Build a repo with one linked lane worktree and return the repo root."""
        import subprocess

        repo = _init_repo(tmp_path / "repo")
        worktree = repo / ".worktrees" / slug
        subprocess.run(
            ["git", "worktree", "add", str(worktree), "-b", f"session/{slug}"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        if branch:
            subprocess.run(
                ["git", "checkout", "-b", branch], cwd=worktree, capture_output=True, check=True
            )
        if detach:
            subprocess.run(
                ["git", "checkout", "--detach"], cwd=worktree, capture_output=True, check=True
            )
        if dirty:
            (worktree / "f.txt").write_text("uncommitted\n")
        return repo, worktree

    def test_aligned_lane_is_clean(self, tmp_path):
        from tools.lane_identity import sweep

        repo, _ = self._lane(tmp_path, "sdlc-1")
        result = sweep(repo_root=repo, sessions_for_slug=lambda slug: [])
        assert result.exit_code == 0
        lane = next(x for x in result.lanes if x.slug == "sdlc-1")
        assert lane.live_branch == "session/sdlc-1"
        assert lane.diverged is False

    def test_diverged_clean_lane_is_reported_but_not_a_failure(self, tmp_path):
        from tools.lane_identity import sweep

        repo, _ = self._lane(tmp_path, "sdlc-2", branch="eval/code-simplifier")
        result = sweep(repo_root=repo, sessions_for_slug=lambda slug: [])
        lane = next(x for x in result.lanes if x.slug == "sdlc-2")
        assert lane.diverged is True
        assert lane.live_branch == "eval/code-simplifier"
        # The #1377 guard auto-checks-out a clean mismatch, so the next turn
        # launches -- divergence alone is not a refusal.
        assert lane.would_refuse is False
        assert result.exit_code == 0

    def test_diverged_dirty_lane_would_refuse(self, tmp_path):
        from tools.lane_identity import sweep

        repo, _ = self._lane(tmp_path, "sdlc-3", branch="eval/x", dirty=True)
        result = sweep(repo_root=repo, sessions_for_slug=lambda slug: [])
        lane = next(x for x in result.lanes if x.slug == "sdlc-3")
        assert lane.would_refuse is True
        assert result.exit_code != 0

    def test_missing_expected_branch_would_refuse(self, tmp_path):
        """The incident's shape: cleanup deleted the branch the guard demands."""
        import subprocess

        from tools.lane_identity import sweep

        repo, _ = self._lane(tmp_path, "sdlc-4", branch="eval/y")
        subprocess.run(
            ["git", "branch", "-D", "session/sdlc-4"], cwd=repo, capture_output=True, check=True
        )
        result = sweep(repo_root=repo, sessions_for_slug=lambda slug: [])
        lane = next(x for x in result.lanes if x.slug == "sdlc-4")
        assert lane.expected_branch_exists is False
        assert lane.would_refuse is True
        assert result.exit_code != 0

    def test_detached_lane_reports_no_live_branch(self, tmp_path):
        from tools.lane_identity import sweep

        repo, _ = self._lane(tmp_path, "sdlc-5", detach=True)
        result = sweep(repo_root=repo, sessions_for_slug=lambda slug: [])
        lane = next(x for x in result.lanes if x.slug == "sdlc-5")
        assert lane.live_branch is None
        assert lane.diverged is True

    def test_recorded_branch_is_the_expectation_not_the_seed(self, tmp_path):
        from tools.lane_identity import sweep

        repo, _ = self._lane(tmp_path, "sdlc-6", branch="eval/recorded")
        session = _BranchSessionStub(branch_name="eval/recorded", slug="sdlc-6")
        result = sweep(repo_root=repo, sessions_for_slug=lambda slug: [session])
        lane = next(x for x in result.lanes if x.slug == "sdlc-6")
        assert lane.expected_branch == "eval/recorded"
        assert lane.diverged is False
        assert result.exit_code == 0

    def test_sweep_mutates_nothing(self, tmp_path):
        from tools.lane_identity import sweep

        repo, worktree = self._lane(tmp_path, "sdlc-7", branch="eval/z", dirty=True)

        def snapshot():
            return (
                _git_stdout(worktree, "rev-parse", "--abbrev-ref", "HEAD"),
                _git_stdout(worktree, "status", "--porcelain"),
                _git_stdout(repo, "branch", "--list"),
            )

        before = snapshot()
        sweep(repo_root=repo, sessions_for_slug=lambda slug: [])
        assert snapshot() == before

    def test_report_names_every_lane_and_its_state(self, tmp_path):
        from tools.lane_identity import render_sweep_report, sweep

        repo, _ = self._lane(tmp_path, "sdlc-8", branch="eval/w", dirty=True)
        report = render_sweep_report(sweep(repo_root=repo, sessions_for_slug=lambda slug: []))
        assert "sdlc-8" in report
        assert "eval/w" in report
        assert "session/sdlc-8" in report
