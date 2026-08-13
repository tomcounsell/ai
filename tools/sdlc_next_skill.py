"""CLI entry point for the SDLC next-skill dispatch decision.

Wraps ``agent.sdlc_router.decide_next_dispatch()`` with session resolution
and the enriched meta payload from ``tools.sdlc_stage_query.query_enriched``.

This is the **production runtime path** that replaces the SKILL.md Step 4
hand-edited dispatch table. The LLM calls this tool and dispatches whatever
skill it returns — the LLM no longer authors routing decisions.

Usage::

    sdlc-tool next-skill --issue-number 1040
    sdlc-tool next-skill --issue-number 1040 --proposed-skill /do-build
    sdlc-tool next-skill --issue-number 1040 --format pretty

Environment:
    No rollout flags -- this module is the sole routing source of truth.
    The legacy SKILL.md hand-authored dispatch table has been removed.
    Setting ``SDLC_ROUTER_SOURCE`` has no effect.

Exit codes:
    0 — decision produced (either ``dispatched`` or ``blocked``)
    1 — session lookup or dispatch calculation failed fatally
    2 — wrapper-level usage / configuration error

Output (JSON, stdout)::

    {
        "skill": "/do-build",
        "reason": "...",
        "row_id": "4a",
        "dispatched": true
    }

    # When the router blocks:
    {
        "blocked": true,
        "reason": "...",
        "guard_id": "G4"
    }

The ``dispatched`` key is always present in a non-blocked response. It is
``true`` when the router produced a ``Dispatch`` object, ``false`` otherwise
(should not happen in practice — the blocked path uses the ``blocked`` key).

Graceful failure: any exception in session lookup or dispatch is caught and
emitted as JSON on stdout with ``{"error": "...", "dispatched": false}``
followed by exit code 1. This prevents the LLM from seeing a raw traceback.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Stage-advance artifact verification (#1267): the top-3 deterministic
# side-effects the router treats as authoritative composite state rather
# than trusting the executing agent's self-attested stage-completion marker
# (the ``<!-- OUTCOME {...} -->`` contract in ``agent/pipeline_state.py``).
# Fail-open scope is deliberately narrow -- see ``_verify_stage_artifacts``.
_INFRA_ERRORS = (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError)


def _target_repo_cwd() -> str | None:
    """Filesystem path of the SDLC target checkout, for git subprocess ``cwd``.

    ``SDLC_TARGET_REPO`` is a FILESYSTEM PATH, never a gh slug (see
    ``tools._sdlc_utils._resolve_target_repo`` rung 1). The local ``/do-sdlc``
    wrapper pins the process cwd to the ai repo via ``uv run --directory``, so
    every live ``git`` check in this module must run against this path or it
    inspects the wrong repo (#2078 G8 loop). ``None`` (env unset/empty)
    preserves bridge behavior, where the process cwd already is the target.
    A nonexistent path raises ``OSError`` from ``subprocess.run`` — covered
    by ``_INFRA_ERRORS`` fail-open in the verifier.
    """
    return os.environ.get("SDLC_TARGET_REPO") or None


def _resolve_enriched(issue_number: int | None, session_id: str | None) -> dict:
    """Return the enriched stage_states payload (stages + _meta)."""
    try:
        from tools.sdlc_stage_query import query_enriched

        return query_enriched(
            session_id=session_id,
            issue_number=issue_number,
        )
    except Exception as e:
        logger.debug(f"_resolve_enriched failed: {e}")
        return {"stages": {}, "_meta": {}}


def _fetch_pr_state(pr_number: int, repo: str | None = None) -> str | None:
    """Read the PR's raw state string via ``gh pr view``.

    Reuses the same ``gh pr view --json`` shape as
    ``tools.sdlc_stage_query._fetch_pr_merge_state``. This read is live with
    respect to gh's on-disk cache (gh 2.89.0 writes that cache only for
    ``gh api --cache``, which this repo never uses) but is NOT git-cross-checked
    -- unlike the head-SHA read in :func:`_fetch_pr_head_sha` (#2404). That is
    deliberate: a stale ``state`` read is fail-safe here (a momentarily-stale
    OPEN re-runs a stage rather than skipping a gate), whereas a stale head SHA
    is fail-OPEN against the verdict gate, so only the latter earns the
    authoritative git resolver. Returns ``None`` when
    the call fails, the response is unparseable, or ``state`` is absent /
    not a string -- callers must treat ``None`` as "could not determine",
    never as evidence of a false claim. May raise
    ``subprocess.TimeoutExpired``/``SubprocessError``/``OSError`` on infra
    failure -- the caller (``_verify_stage_artifacts``) applies the narrowed
    fail-open catch, this helper does not swallow anything itself.
    """
    cmd = ["gh", "pr", "view", str(pr_number), "--json", "state"]
    if repo:
        cmd = ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "state"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        return None
    data = json.loads(proc.stdout or "{}")
    state = data.get("state")
    return state if isinstance(state, str) else None


def _fetch_pr_head_sha(pr_number: int, repo: str | None = None) -> str | None:
    """Authoritatively resolve the PR's current head commit SHA (#2404).

    WS3d (issue #2062): feeds the router's head_sha verdict-staleness signal
    (``context["pr_head_sha"]``), mirroring the fail-closed shape of
    ``tools.merge_predicate._gh_latest_commit``. Resolution is git-first via
    ``tools.pr_head_resolver.resolve_pr_head_sha`` (``git ls-remote origin
    refs/pull/N/head``, which shares no response cache with ``gh`` and is
    authoritative for the ref), falling back to ``gh pr view --json
    headRefOid`` only when git yields nothing. This closes the fail-open path
    where a stale current-head read matches the verdict's trailer and makes a
    stale approval look fresh (#2404). Returns ``None`` on any non-exceptional
    failure — the CALLER (``_build_context``) converts both ``None`` and a
    raised error into the empty fail-closed sentinel; this helper never invents
    a SHA.
    """
    from tools.pr_head_resolver import resolve_pr_head_sha

    return resolve_pr_head_sha(pr_number, repo=repo, repo_root=_target_repo_cwd())


def _check_branch_pushed(branch_name: str) -> bool:
    """Live-check (``git ls-remote``) that ``branch_name`` exists on origin.

    Takes a FULL branch name, not a slug: the ``session/`` prefix is applied
    by ``tools.lane_identity.lane_branch_name`` and nowhere else, so a caller
    that has no lane slug gets ``None`` and no-ops instead of probing a name
    it guessed.

    Unlike the local ``git branch -a`` check elsewhere in this module (which
    can be satisfied by a stale remote-tracking ref), this queries the
    remote directly so a claimed "branch pushed" artifact is verified
    against the live world, not local ref cache staleness.
    """
    cmd = ["git", "ls-remote", "--heads", "origin", branch_name]
    proc = subprocess.run(cmd, cwd=_target_repo_cwd(), capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


def _check_plan_committed_on_main(rel_plan_path: str) -> bool:
    """Live-check (``git show``) that ``rel_plan_path`` is committed on ``main``.

    Takes a REPO-RELATIVE path, not a slug. The plan doc is resolved by
    ``find_plan_path`` (``tracking:`` frontmatter), which is independent of
    the lane slug -- a human-named plan legitimately tracks an issue-derived
    lane, so building ``docs/plans/{lane_slug}.md`` named a file that does not
    exist.

    Extends the existing ``plan_exists`` context flag (disk presence, may be
    an uncommitted/local-only file) to a real commit check on ``main``.
    """
    cmd = ["git", "show", f"main:{rel_plan_path}"]
    proc = subprocess.run(cmd, cwd=_target_repo_cwd(), capture_output=True, text=True, timeout=10)
    return proc.returncode == 0


def _verify_stage_artifacts_live(stage_states: dict, meta: dict, issue_number: int) -> dict:
    """Check the top-3 claimed stage artifacts against the live world.

    Only checks a stage whose marker actually claims completion -- a stage
    with no claimed artifact is left alone (no-op), never invented. Returns
    ``{}`` when every claimed artifact this function knows how to check
    verifies clean, or when nothing checkable is claimed. On the first
    mismatch, returns ``{"stage_artifacts_verified": False,
    "unverified_stage": <STAGE>}`` -- one mismatch per call is enough to
    drive the ``g8`` re-dispatch guard; the same check runs again next tick.

    #1267 merged-pipeline misfire: a PR that has already been merged is not
    an "unverified" BUILD artifact -- it is the strongest possible proof the
    artifact was real (a PR cannot merge without existing). Both the BUILD
    check and the PATCH branch-pushed check treat ``state == "MERGED"`` as
    verified: BUILD directly (state is OPEN or MERGED), and PATCH by
    skipping the ``git ls-remote`` branch check entirely, since a
    delete-branch-on-merge repo policy removes the remote ref as an expected
    side effect of merging, not evidence of a fabricated PATCH claim. Without
    this, a terminal merged pipeline would re-dispatch ``/do-build`` via
    guard ``g8`` forever instead of routing to the terminal ``/do-merge``
    (row 10) -- a duplicate-PR risk.
    """
    from tools.lane_identity import find_plan_path, lane_branch_name, resolve_lane_slug

    # The PLAN artifact is a plan DOC; the PATCH artifact is a lane BRANCH.
    # They are resolved from different sources and are not the same string --
    # sharing one plan-filename-derived slug between them is what probed a
    # branch that never existed and wedged #2663.
    plan_path = find_plan_path(issue_number)
    lane_slug = resolve_lane_slug(issue_number)
    lane_branch = lane_branch_name(lane_slug)

    if stage_states.get("PLAN") == "completed" and plan_path is not None:
        try:
            rel_plan_path = str(Path(plan_path).relative_to(_target_repo_cwd() or Path.cwd()))
        except ValueError:
            # Plan resolved outside the target checkout (a cross-repo
            # SDLC_TARGET_REPO override). `git show main:<abs>` is meaningless
            # there, so skip rather than manufacture a false negative.
            logger.debug(
                "stage-artifact-verify: issue #%s plan %s is outside the target repo; "
                "skipping the PLAN-committed-on-main check",
                issue_number,
                plan_path,
            )
        else:
            if not _check_plan_committed_on_main(rel_plan_path):
                logger.warning(
                    f"stage-artifact-verify: issue #{issue_number} PLAN claims completed "
                    f"but {rel_plan_path} is not committed on main"
                )
                return {"stage_artifacts_verified": False, "unverified_stage": "PLAN"}

    pr_number = meta.get("pr_number")
    repo = meta.get("_resolved_target_repo")
    build_claimed = stage_states.get("BUILD") == "completed"
    # No recorded lane slug -> no branch to probe -> the PATCH check no-ops.
    # Probing a guessed name is what force-dispatches `/do-patch` against a
    # clean worktree until the G4 oscillation cap hard-blocks the lane (#2718).
    patch_claimed = stage_states.get("PATCH") == "completed" and bool(lane_branch)
    if stage_states.get("PATCH") == "completed" and not lane_branch:
        logger.debug(
            "stage-artifact-verify: issue #%s PATCH claims completed but no lane slug is "
            "recorded; skipping the branch probe rather than guessing a branch name",
            issue_number,
        )

    # Resolve the live PR state at most once (used by both checks below) --
    # only when a claim that needs it is actually present, so an unclaimed
    # BUILD/PATCH stage still makes zero live calls (test_no_claimed_artifact_is_a_noop).
    pr_state: str | None = None
    if pr_number and (build_claimed or patch_claimed):
        pr_state = _fetch_pr_state(pr_number, repo=repo)

    if build_claimed:
        if not pr_number or pr_state not in ("OPEN", "MERGED"):
            logger.warning(
                f"stage-artifact-verify: issue #{issue_number} BUILD claims completed "
                f"but PR {pr_number!r} is not open or merged (state={pr_state!r})"
            )
            return {"stage_artifacts_verified": False, "unverified_stage": "BUILD"}

    if patch_claimed:
        if pr_state != "MERGED" and not _check_branch_pushed(lane_branch):
            logger.warning(
                f"stage-artifact-verify: issue #{issue_number} PATCH claims completed "
                f"but branch {lane_branch} is not pushed"
            )
            return {"stage_artifacts_verified": False, "unverified_stage": "PATCH"}

    return {}


def _verify_stage_artifacts(stage_states: dict, meta: dict, issue_number: int | None) -> dict:
    """Verify claimed stage-completion artifacts against the live world (#1267).

    Sets ``stage_artifacts_verified`` / ``unverified_stage`` in the returned
    dict on a mismatch; returns ``{}`` (no-op, flags left unset so
    ``guard_g8_artifact_verification`` never fires) when nothing claimed is
    checkable or when every claimed artifact verifies clean. This function
    makes NO dispatch decision -- it only sets context flags; the router's
    ``g8`` guard (positioned after G4, so the oscillation cap bounds a
    persistently-false claim) is what re-dispatches.

    Fail-open scope is narrow and load-bearing (#1267 Concern 4): only
    ``subprocess.TimeoutExpired``/``SubprocessError``/``OSError`` -- infra
    failures from the underlying ``gh``/``git`` calls -- are caught. On those,
    this logs a warning and returns ``{}`` (advances; the merge-gate from
    #2003 remains the hard backstop) rather than wedging the pipeline on
    network flakiness. Any OTHER exception (e.g. a ``TypeError``/``KeyError``
    from a malformed artifact spec or bad slug -- a logic bug, not infra) is
    deliberately NOT swallowed: it is logged at error level and re-raised so
    a broken gate is visible instead of silently failing open forever. This
    is a deliberate deviation from the blanket ``except Exception`` pattern
    used elsewhere in this module -- do not broaden this catch.
    """
    if not issue_number:
        return {}
    try:
        return _verify_stage_artifacts_live(stage_states, meta, issue_number)
    except _INFRA_ERRORS as e:
        logger.warning(
            f"stage-artifact-verify: infra error verifying issue #{issue_number} "
            f"artifacts ({type(e).__name__}: {e}) — failing open (advancing)"
        )
        return {}
    except Exception:
        logger.error(
            f"stage-artifact-verify: unexpected (non-infra) error verifying issue "
            f"#{issue_number} artifacts — not failing open",
            exc_info=True,
        )
        raise


def _build_context(
    proposed_skill: str | None,
    issue_number: int | None,
    stage_states: dict | None = None,
    meta: dict | None = None,
) -> dict:
    """Build the optional context dict for the dispatch function.

    The context dict carries caller-supplied hints that the guards may need
    but that are not present in stage_states or _meta:
    - ``proposed_skill``: the skill the LLM was about to invoke (used by G3
      to detect plan-family redirects when a PR is already open).
    - ``branch_exists``: whether the session branch already exists (Row 5).
    - ``current_plan_hash``: sha256 of the plan file (used by G5 to short-circuit
      re-critique on an unchanged plan; #1639). Without this, G5's loop bound on
      router row 2b is inert in the CLI path.
    - ``legacy_plan_hash``: the OLD full-bytes hash (``compute_plan_hash``),
      supplied so G5's transparent migration (#1761 Layer 3) can detect a
      stored legacy hash without the router importing from tools/ (the
      import-boundary contract — tools/ imports agent/sdlc_router, never the
      reverse).
    - ``stage_artifacts_verified`` / ``unverified_stage``: set by the #1267
      stage-advance verification gate (see ``_verify_stage_artifacts``) when
      a stage-completion marker's claimed artifact fails a live check.
      ``stage_states``/``meta`` are optional (default ``None``) so existing
      callers that only need the plan-hash/branch-exists context are
      unaffected; verification is skipped (no-op) when either is omitted.
    """
    context: dict = {}
    if proposed_skill:
        context["proposed_skill"] = proposed_skill

    # G5 activation (#1639): supply the current plan-file hash so
    # guard_g5_artifact_hash_cache can compare it against the cached CRITIQUE
    # verdict's artifact_hash and bound the row-2b re-critique loop. None-safe:
    # no plan path or unreadable file leaves the key unset (G5 then no-ops).
    if issue_number:
        try:
            from tools.lane_identity import find_plan_path
            from tools.sdlc_verdict import compute_plan_body_hash, compute_plan_hash

            plan_path = find_plan_path(issue_number)
            if plan_path is not None:
                plan_hash = compute_plan_body_hash(plan_path)
                if plan_hash is not None:
                    context["current_plan_hash"] = plan_hash
                    context["issue_number"] = issue_number
                    # G5 transparent migration (#1761 Layer 3): the router
                    # compares the stored artifact_hash against the legacy
                    # full-bytes hash. Caller-supplied because the router must
                    # not import from tools/ (import-boundary contract).
                    legacy_hash = compute_plan_hash(plan_path)
                    if legacy_hash is not None:
                        context["legacy_plan_hash"] = legacy_hash
        except Exception as e:
            logger.debug(
                "next-skill: plan-hash context unavailable for issue #%s (%s: %s)",
                issue_number,
                type(e).__name__,
                e,
            )

    # Check whether the lane's branch already exists (informs Row 5).
    # The branch is named by the lane's RECORDED slug, which the lane minted
    # once at lane start -- both the issue-derived shape and human-named
    # shapes occur on this remote, so the name is read, never derived. Without
    # a recorded slug we cannot affirm existence, so branch_exists stays False
    # (#2003) and no git call is made at all.
    if issue_number:
        context["branch_exists"] = False
        try:
            from tools.lane_identity import lane_branch_name, resolve_lane_slug

            lane_branch = lane_branch_name(resolve_lane_slug(issue_number))
            if lane_branch is not None:
                proc2 = subprocess.run(
                    ["git", "branch", "-a"],
                    cwd=_target_repo_cwd(),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                branch_names = proc2.stdout if proc2.returncode == 0 else ""
                context["branch_exists"] = lane_branch in branch_names
        except Exception:
            context["branch_exists"] = False

    # Stage-advance outcome verification gate (#1267): verify claimed
    # stage-completion artifacts against the live world. No-op when
    # stage_states/meta were not supplied (see the docstring above) or when
    # no stage claims a checkable artifact this tick.
    if issue_number and stage_states is not None and meta is not None:
        context.update(_verify_stage_artifacts(stage_states, meta, issue_number))

    # Head_sha verdict-staleness signal (WS3d, issue #2062): when a PR exists
    # AND a REVIEW verdict is recorded, fetch the live PR head so the router
    # can compare it against the verdict's REVIEW_CONTEXT head_sha trailer
    # (agreeing with tools/merge_predicate's Group (c) freshness check).
    # FAIL-CLOSED: a lookup failure (gh/network error or empty result) sets
    # the EMPTY sentinel plus pr_head_sha_lookup_failed — never silently
    # omits the key — so the router treats the verdict as stale and routes
    # to re-review rather than fast-pathing a possibly-stale approval to
    # /do-merge. The key is omitted only when the signal is genuinely not
    # applicable (no PR, or no recorded verdict — states other rules own).
    if stage_states is not None and meta is not None and meta.get("pr_number"):
        verdicts = stage_states.get("_verdicts") or {}
        review_recorded = bool(verdicts.get("REVIEW")) or bool(meta.get("latest_review_verdict"))
        if review_recorded:
            head_sha: str | None
            try:
                head_sha = _fetch_pr_head_sha(
                    meta["pr_number"], repo=meta.get("_resolved_target_repo")
                )
            except Exception as e:
                logger.warning(
                    f"pr-head lookup failed for PR #{meta.get('pr_number')} "
                    f"({type(e).__name__}: {e}) — failing closed toward stale"
                )
                head_sha = None
            if head_sha:
                context["pr_head_sha"] = head_sha
            else:
                context["pr_head_sha"] = ""
                context["pr_head_sha_lookup_failed"] = True

    return context


def _recover_stage_states_from_durable_signals(issue_number: int) -> dict:
    """Best-effort, read-only fallback: reconstruct stage_states from durable
    artifacts (committed plan, open/merged PR, review comments) when the
    ``PipelineLedger`` is empty for an issue that actually has durable state
    (issue #2395).

    Delegates to ``PipelineStateMachine.derive_from_durable_signals`` in
    ``agent/pipeline_state.py``, which already implements the plan/PR/review
    inspection. This wrapper only resolves the ``session`` argument that
    function requires (via ``find_session_by_issue``) and swallows every
    failure -- a lookup error, a subprocess error inside the derive call, or
    a missing session all fall back to "nothing recovered" (``{}``), which
    leaves today's behavior (empty stage_states flows through unchanged to
    ``decide_next_dispatch``, which routes fresh issues to ``/do-plan``)
    completely intact. Never raises.
    """
    try:
        from agent.pipeline_state import PipelineStateMachine
        from tools._sdlc_utils import find_session_by_issue

        issue_session = find_session_by_issue(issue_number)
        if issue_session is None:
            return {}
        return PipelineStateMachine.derive_from_durable_signals(issue_session) or {}
    except Exception as e:
        logger.debug(f"_recover_stage_states_from_durable_signals failed: {e}")
        return {}


def decide(
    issue_number: int | None = None,
    session_id: str | None = None,
    proposed_skill: str | None = None,
) -> dict:
    """Run the dispatch algorithm and return a JSON-serialisable result dict.

    This is the programmatic interface; CLI consumers go through ``main()``.

    Returns:
        On ``Dispatch``: ``{"skill": "/do-X", "reason": "...", "row_id": "...",
        "dispatched": True}``
        On ``Blocked``: ``{"blocked": True, "reason": "...", "guard_id": "..."}``
        On issue-lock contention: ``{"blocked": True, "reason": "ISSUE_LOCKED",
        "guard_id": "ISSUE_LOCK", "owner_session_id": "..."}``
        On error: ``{"error": "...", "dispatched": False}``
    """
    try:
        from agent.sdlc_router import (
            Blocked,
            Dispatch,
            decide_next_dispatch,
        )

        # Issue-lock pre-check (issue #1954): peek-only -- a next-skill call
        # must never itself claim or extend the lock, only mutation
        # subcommands (ensure_session, dispatch record, stage-marker) do
        # that. Runs BEFORE _resolve_enriched/decide_next_dispatch so a
        # contended issue short-circuits ahead of any guard evaluation.
        # decide_next_dispatch() itself is untouched -- no changes to the
        # G1-G7 guard table.
        if issue_number:
            from models.session_lifecycle import touch_issue_lock

            # Run-identity peek (issue #2003, minimal call-site update): this
            # read-only pre-check compares the lock against the CURRENT
            # legitimate run's identity, read back from the issue session's
            # active_run_id mirror (read-only -- peek never mutates or adopts).
            # When they match, the lock belongs to the run driving this
            # pipeline and next-skill proceeds; a mismatch (crash window /
            # foreign takeover mid-write) blocks with the owner surfaced.
            peek_run_id = None
            try:
                from tools._sdlc_utils import find_session_by_issue

                issue_session = find_session_by_issue(issue_number)
                if issue_session is not None:
                    peek_run_id = getattr(issue_session, "active_run_id", None)
            except Exception:
                peek_run_id = None

            lock_result = touch_issue_lock(
                issue_number, peek_run_id, session_id=session_id or "", peek=True
            )
            if not lock_result.acquired:
                return {
                    "blocked": True,
                    "reason": "ISSUE_LOCKED",
                    "guard_id": "ISSUE_LOCK",
                    "owner_run_id": lock_result.owner_run_id,
                    "owner_session_id": lock_result.owner_session_id,
                    "orphaned_lock": lock_result.orphaned_lock,
                }

        enriched = _resolve_enriched(issue_number, session_id)
        stage_states = enriched.get("stages") or {}
        meta = enriched.get("_meta") or {}

        # Ledger-durability recovery (issue #2395): a fully-empty ledger
        # ("pipeline never started") is ambiguous -- it could be a genuinely
        # fresh issue, or an issue whose durable state (committed plan, open
        # PR, review) exists but the PipelineLedger lost track of it (cold
        # Redis, eviction, cleared session). Gate on ``stage_states == {}``
        # EXACTLY, not a falsy check: a partially-populated ledger (any stage
        # marker present, even just ISSUE) is legitimately-recorded partial
        # state and must be left completely untouched -- reconstruction only
        # ever supplements a fully-empty ledger, never overrides it. This
        # reconstruction call is deliberately placed HERE, in the CLI wrapper,
        # rather than inside ``decide_next_dispatch`` -- that function is a
        # pure guard table (G1-G7) and must never import/call
        # ``derive_from_durable_signals`` itself, preserving the #1954
        # purity boundary (see the comment block above on the issue-lock
        # pre-check). Best-effort and read-only: any failure inside
        # ``_recover_stage_states_from_durable_signals`` leaves stage_states
        # empty, and the empty dict flows through to decide_next_dispatch
        # exactly as before (routing a fresh issue to /do-plan).
        if stage_states == {} and issue_number:
            recovered = _recover_stage_states_from_durable_signals(issue_number)
            if recovered:
                stage_states = recovered

        context = _build_context(proposed_skill, issue_number, stage_states, meta)

        result = decide_next_dispatch(stage_states, meta, context)

        if isinstance(result, Dispatch):
            return {
                "skill": result.skill,
                "reason": result.reason,
                "row_id": result.row_id,
                "dispatched": True,
            }
        elif isinstance(result, Blocked):
            return {
                "blocked": True,
                "reason": result.reason,
                "guard_id": result.guard_id,
            }
        else:
            # Unexpected return type — treat as blocking error
            return {
                "error": f"Unexpected result type: {type(result).__name__}",
                "dispatched": False,
            }

    except Exception as e:
        logger.debug(f"decide() failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "dispatched": False,
        }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Compute the next SDLC dispatch decision for an issue.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        metavar="N",
        help="GitHub issue number to look up the session.",
    )
    parser.add_argument(
        "--session-id",
        metavar="ID",
        help="Explicit AgentSession ID (overrides --issue-number lookup).",
    )
    parser.add_argument(
        "--proposed-skill",
        metavar="SKILL",
        help="The skill the LLM was about to invoke (passed to G3 guard for PR-lock detection).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "pretty"],
        default="json",
        help="Output format. 'json' is machine-parseable (default); 'pretty' is indented.",
    )
    args = parser.parse_args(argv)

    if not args.issue_number and not args.session_id:
        print(
            json.dumps({"error": "Must supply --issue-number or --session-id", "dispatched": False})
        )
        return 2

    # One tick, one repo resolution. The router's stage read and its lane-slug
    # read both need the target repo; without this scope each pays its own
    # `gh repo view` whenever GH_REPO is unset.
    from tools._sdlc_utils import cached_target_repo_resolution

    with cached_target_repo_resolution():
        result = decide(
            issue_number=args.issue_number,
            session_id=args.session_id,
            proposed_skill=args.proposed_skill,
        )

    if args.format == "pretty":
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))

    # Exit 1 on error, 0 on dispatch or block (both are valid outcomes)
    if result.get("error") and not result.get("dispatched"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
