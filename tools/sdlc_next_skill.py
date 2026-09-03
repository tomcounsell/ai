"""CLI entry point for the SDLC next-skill dispatch decision.

Wraps ``agent.sdlc_router.decide_next_dispatch()`` with session resolution
and the enriched meta payload from ``tools.sdlc_stage_query.query_enriched``.

This is the **production runtime path** that replaces the SKILL.md Step 4
hand-edited dispatch table. The LLM calls this tool and dispatches whatever
skill it returns — the LLM no longer authors routing decisions.

Usage::

    sdlc-tool next-skill --issue-number 1040 --run-id <caller-run-id>
    sdlc-tool next-skill --issue-number 1040 --run-id <caller-run-id> --proposed-skill /do-build
    sdlc-tool next-skill --issue-number 1040 --run-id <caller-run-id> --format pretty

    ``--run-id`` states the caller's own run identity for the issue-lock peek
    (issue #2766). Omitting it falls back to inferring identity from the
    session mirror, which is exactly how the next-skill self-lock reproduces
    -- always pass the caller's run-id explicitly.

Environment:
    No rollout flags -- this module is the sole routing source of truth.
    The legacy SKILL.md hand-authored dispatch table has been removed.
    Setting ``SDLC_ROUTER_SOURCE`` has no effect.

Exit codes:
    0 — decision produced (``dispatch``, ``terminal``, or ``blocked``)
    1 — session lookup or dispatch calculation failed fatally
    2 — wrapper-level usage / configuration error

Output (JSON, stdout)::

    {
        "skill": "/do-build",
        "reason": "...",
        "row_id": "4a",
        "decision": "dispatch",
        "recorded": false,
        "recorded_reason": "NOT_PERSISTED_CALL_DISPATCH_RECORD"
    }

    # When the lane is finished (#2894, #2817) — a SUCCESS, exit 0:
    {
        "decision": "terminal",
        "reason": "Pipeline complete — nothing to dispatch (...)",
        "evidence": "merge_marker",
        "row_id": "T"
    }

    # When the router blocks:
    {
        "blocked": true,
        "decision": "blocked",
        "reason": "...",
        "guard_id": "G4"
    }

Every response carries ``decision``: ``"dispatch"``, ``"terminal"``,
``"blocked"``, or ``"error"``. ``terminal`` is NOT a flavour of ``blocked``:
a shipped pipeline is a correct, successful end state, and reporting it as an
escalation is the confusion #2817 exists to end.

**This tool persists nothing.** It is a pure decision call plus a read-only
issue-lock peek — that holds with or without ``--run-id``, which only states
the identity the lock is peeked under (#2766) and is never written anywhere.
Advancing the ledger is a separate ``sdlc-tool dispatch record`` call the
caller must make before invoking the returned skill. ``recorded: false`` on
every dispatch decision says so in machine-readable form (issue #2897): a
caller must never read the decision as evidence the ledger moved. It did not.

Graceful failure: any exception in session lookup or dispatch is caught and
emitted as JSON on stdout with ``{"error": "...", "decision": "error"}``
followed by exit code 1. This prevents the LLM from seeing a raw traceback.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Issue #2897: the machine-readable reason a dispatch decision is not a
# ledger write. Constant because it is unconditional -- next-skill has no
# code path that persists a dispatch, so there is no other reason to give.
NOT_RECORDED_REASON = "NOT_PERSISTED_CALL_DISPATCH_RECORD"

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


def _ls_remote_heads() -> dict[str, str] | None:
    """Return ``{refname: sha}`` for every head on ``origin``, ``None`` on failure.

    ``None`` is deliberately distinct from ``{}``: an unreachable remote is not
    an empty remote. Collapsing the two is what let a network blip read as
    "this lane has no branch" (#3065 Cluster A). Callers turn ``None`` into
    *indeterminate*.

    Unlike a local ``git branch -a`` read (which a stale remote-tracking ref
    can satisfy), this queries the remote directly, so branch truth is checked
    against the live world rather than local ref-cache staleness.
    """
    cmd = ["git", "ls-remote", "--heads", "origin"]
    proc = subprocess.run(cmd, cwd=_target_repo_cwd(), capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        logger.debug("branch-truth: git ls-remote --heads origin returned %s", proc.returncode)
        return None
    heads: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            heads[parts[1].strip()] = parts[0].strip()
    return heads


# Branch-truth verdicts (#3065 Cluster A). Three values, because the two-valued
# answer ``_check_branch_pushed`` used to give could not tell "this lane has no
# pushed branch" from "the name I asked about is the wrong name" or "I could not
# read the remote" -- and all three got the same fail-closed consequence.
BRANCH_TRUTH_FOUND = "found"
BRANCH_TRUTH_ABSENT = "absent"
BRANCH_TRUTH_INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class BranchTruth:
    """The answer to "which pushed branch holds this lane's work?".

    ``status`` is one of :data:`BRANCH_TRUTH_FOUND`, :data:`BRANCH_TRUTH_ABSENT`,
    :data:`BRANCH_TRUTH_INDETERMINATE`. ``branch`` names the branch that holds
    the work on *found* (which may differ from the branch the caller asked
    about — that difference IS a wrong recorded slug, and is what
    ``tools.lane_identity.repair_lane_slug`` acts on). ``reason`` is always
    populated so an indeterminate answer is reportable rather than silent.
    """

    status: str
    branch: str | None = None
    reason: str = ""
    matches: tuple[str, ...] = ()

    @property
    def is_found(self) -> bool:
        return self.status == BRANCH_TRUTH_FOUND

    @property
    def is_absent(self) -> bool:
        return self.status == BRANCH_TRUTH_ABSENT

    @property
    def is_indeterminate(self) -> bool:
        return self.status == BRANCH_TRUTH_INDETERMINATE


_UNSET = object()


def resolve_branch_truth(
    lane_branch: str | None,
    pr_number: object = None,
    repo: str | None = None,
    heads: object = _UNSET,
) -> BranchTruth:
    """Resolve which pushed branch holds this lane's work. Three-valued.

    Ground truth is the PR's head commit SHA — resolved through
    ``tools.pr_head_resolver.resolve_pr_head_sha`` (git-first via ``git
    ls-remote refs/pull/N/head``), **never a bare ``gh`` read**, per CLAUDE.md:
    a stale ``gh`` head SHA is what flipped the verdict-staleness gate
    fail-open in #2895 — matched against the ``git ls-remote --heads origin``
    listing. The branch name is an output of that match, not an input to it,
    which is why a wrong recorded slug can no longer produce a wrong answer.

    Verdicts:

    - **found** — exactly one head carries the PR's head SHA (or, on a lane
      with no PR, ``lane_branch`` is in the listing). ``branch`` names it.
    - **absent** — the lane has **no PR** and its recorded branch is not in a
      successfully-read listing. This is the only verdict a fail-closed
      decision may act on.
    - **indeterminate** — the remote could not be read, the PR head could not
      be resolved, the head matches two or more heads, or the head matches
      none of them. The last case is Race 1: a listing taken mid-push does not
      yet contain the head, and a stale *negative* is the dangerous direction,
      so it defers rather than claiming absence. ``lane_branch`` being
      ``None`` is also indeterminate — there is no name to ask about, and
      guessing one is #2718.

    This function makes no decision and writes nothing; it only reports what
    the world says.

    Makes ZERO live calls when there is nothing to check at all -- no PR and
    no recorded branch name. That case is answered (*indeterminate*) before
    any subprocess runs, so a lane with no claimable artifact never pays for
    a live probe (mirrors the #2757 "unverifiable costs no call" contract).

    ``heads`` lets a caller that already fetched the listing this tick (e.g.
    ``_build_context``, which shares one resolution across ``branch_exists``
    and the G8 artifact check) inject it instead of paying for a second
    ``git ls-remote --heads origin`` round trip. Omitted (the default), this
    function fetches its own listing.
    """
    lane_branch = (lane_branch or "").strip() or None
    has_pr = isinstance(pr_number, int) and pr_number >= 1

    if not has_pr and lane_branch is None:
        return BranchTruth(
            status=BRANCH_TRUTH_INDETERMINATE,
            reason="no lane branch recorded and no PR to resolve a head from",
        )

    if heads is _UNSET:
        try:
            heads = _ls_remote_heads()
        except Exception as e:
            return BranchTruth(
                status=BRANCH_TRUTH_INDETERMINATE,
                reason=f"git ls-remote --heads origin failed ({type(e).__name__}: {e})",
            )
    if heads is None:
        return BranchTruth(
            status=BRANCH_TRUTH_INDETERMINATE,
            reason="git ls-remote --heads origin was unreadable (remote unreachable)",
        )

    if has_pr:
        try:
            head_sha = _fetch_pr_head_sha(int(pr_number), repo=repo)
        except Exception as e:
            return BranchTruth(
                status=BRANCH_TRUTH_INDETERMINATE,
                reason=f"PR #{pr_number} head SHA resolution failed ({type(e).__name__}: {e})",
            )
        if not head_sha:
            return BranchTruth(
                status=BRANCH_TRUTH_INDETERMINATE,
                reason=f"PR #{pr_number} head SHA did not resolve",
            )
        matches = tuple(
            sorted(ref.removeprefix("refs/heads/") for ref, sha in heads.items() if sha == head_sha)
        )
        if len(matches) == 1:
            return BranchTruth(
                status=BRANCH_TRUTH_FOUND,
                branch=matches[0],
                reason=f"PR #{pr_number} head {head_sha} uniquely matches {matches[0]}",
                matches=matches,
            )
        if matches:
            return BranchTruth(
                status=BRANCH_TRUTH_INDETERMINATE,
                reason=(
                    f"PR #{pr_number} head {head_sha} matches {len(matches)} heads "
                    f"({', '.join(matches)}) -- ambiguous"
                ),
                matches=matches,
            )
        # Race 1: a listing that does not contain the head is a possibly-stale
        # negative (mid-push, or merged-and-deleted). Never *absent* while a PR
        # exists.
        return BranchTruth(
            status=BRANCH_TRUTH_INDETERMINATE,
            reason=(
                f"PR #{pr_number} head {head_sha} matches no head in the listing "
                f"(mid-push or merged-and-deleted)"
            ),
        )

    # has_pr is False here, and the (not has_pr and lane_branch is None) case
    # already returned above without a live call -- lane_branch is guaranteed
    # non-None below.
    if f"refs/heads/{lane_branch}" in heads:
        return BranchTruth(
            status=BRANCH_TRUTH_FOUND,
            branch=lane_branch,
            reason=f"{lane_branch} is present on origin",
            matches=(lane_branch,),
        )
    return BranchTruth(
        status=BRANCH_TRUTH_ABSENT,
        reason=f"{lane_branch} is not on origin and this lane has no PR",
    )


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


def _verify_stage_artifacts_live(
    stage_states: dict,
    meta: dict,
    issue_number: int,
    heads: object = _UNSET,
) -> dict:
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

    #2757 reached that same misfire through the other door: not a PR whose
    state read wrong, but a PR number that was not there to read. The old
    BUILD branch reported a mismatch whenever the recorded identifier was
    absent, so the gate answered a question it had never asked -- the live
    read is itself gated on a recorded PR number, so ``gh`` was never
    consulted at all. Three states, not two: an artifact is **verified**
    when its identifier resolves and the world confirms it, **falsified**
    when the identifier resolves and the world contradicts it, and
    **unverifiable** when there is no identifier to resolve. Only the
    middle one may set ``stage_artifacts_verified: False``; the third
    no-ops with a debug log, matching ``_fetch_pr_state``'s stated contract
    that ``None`` means "could not determine", never "the claim is false".

    Both identifiability guards here are defense in depth. The real fix for
    #2757 is upstream: ``tools/sdlc_stage_query.py::_compute_meta`` now
    retries its PR lookup against merged PRs, so a shipped lane's
    ``pr_number`` survives its own merge and this function verifies it on
    the merits rather than skipping it.
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
            # `git show main:<path>` paths are ALWAYS repo-root-relative,
            # whatever cwd the subprocess runs in, so the process cwd is never
            # the right base. `find_plan_path` resolves against the repo root
            # too; rebasing on cwd makes the two ladders disagree from any
            # subdirectory and reports a committed plan as unverified -- the
            # #2718 symptom through a different door.
            import tools._sdlc_utils as _sdlc_utils

            repo_root = _target_repo_cwd() or _sdlc_utils._git_toplevel() or str(Path.cwd())
            rel_plan_path = str(Path(plan_path).relative_to(repo_root))
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
    # No recorded PR number -> nothing to look up -> the BUILD check no-ops
    # (#2757). "Unverifiable" is not "falsified": manufacturing a mismatch from
    # a lookup that never ran is what re-dispatched `/do-build` on merged work.
    pr_identifiable = bool(pr_number)
    build_marked = stage_states.get("BUILD") == "completed"
    patch_marked = stage_states.get("PATCH") == "completed"
    build_claimed = build_marked and pr_identifiable
    if build_marked and not pr_identifiable:
        logger.debug(
            "stage-artifact-verify: issue #%s BUILD claims completed but no PR number is "
            "recorded; skipping the live PR check rather than reporting a claim it cannot "
            "verify",
            issue_number,
        )
    # No recorded lane slug -> no branch to probe -> the PATCH check no-ops.
    # Probing a guessed name is what force-dispatches `/do-patch` against a
    # clean worktree until the G4 oscillation cap hard-blocks the lane (#2718).
    #
    # A lane whose MERGE is recorded completed is skipped outright (#2757): a
    # missing remote branch is then the expected side effect of a
    # delete-branch-on-merge policy, not evidence of a fabricated PATCH claim.
    # This replaces the old "no recorded PR number -> no-op" proxy. That proxy
    # existed only because the two-valued probe could not tell
    # deletion-on-merge from a genuinely unpushed branch;
    # :func:`resolve_branch_truth` now can -- a lane WITH a PR whose head
    # matches nothing in the listing is *indeterminate*, never *absent* -- so
    # the only lane that can still fail closed here is one with no PR and no
    # merge, which is exactly the lane `/do-patch` exists for.
    merge_recorded = stage_states.get("MERGE") == "completed"
    patch_claimed = patch_marked and bool(lane_branch) and not merge_recorded
    if patch_marked and not lane_branch:
        logger.debug(
            "stage-artifact-verify: issue #%s PATCH claims completed but no lane slug is "
            "recorded; skipping the branch probe rather than guessing a branch name",
            issue_number,
        )
    elif patch_marked and merge_recorded:
        logger.debug(
            "stage-artifact-verify: issue #%s PATCH claims completed on a lane whose MERGE "
            "is recorded completed; skipping the branch probe because a deleted branch is "
            "the expected side effect of merging",
            issue_number,
        )

    # Resolve the live PR state at most once (used by both checks below) --
    # only when a claim that needs it is actually present, so an unclaimed
    # BUILD/PATCH stage still makes zero live calls (test_no_claimed_artifact_is_a_noop).
    # `patch_claimed` no longer implies a recorded PR number, so the truthiness
    # of `pr_number` is checked explicitly rather than inferred.
    pr_state: str | None = None
    if (build_claimed or patch_claimed) and pr_identifiable:
        pr_state = _fetch_pr_state(pr_number, repo=repo)

    if build_claimed:
        if pr_state not in ("OPEN", "MERGED"):
            logger.warning(
                f"stage-artifact-verify: issue #{issue_number} BUILD claims completed "
                f"but PR {pr_number!r} is not open or merged (state={pr_state!r})"
            )
            return {"stage_artifacts_verified": False, "unverified_stage": "BUILD"}

    if patch_claimed and pr_state != "MERGED":
        truth = resolve_branch_truth(lane_branch, pr_number=pr_number, repo=repo, heads=heads)
        if truth.is_absent:
            logger.warning(
                f"stage-artifact-verify: issue #{issue_number} PATCH claims completed "
                f"but no pushed branch holds its work ({truth.reason})"
            )
            return {
                "stage_artifacts_verified": False,
                "unverified_stage": "PATCH",
                "branch_truth": truth.status,
                "branch_truth_reason": truth.reason,
            }
        if truth.is_indeterminate:
            # Report it AS indeterminate rather than as a silent clean pass:
            # G8 must step aside here, but a supervisor reading the context has
            # to be able to tell "verified" from "unreadable" (#3065).
            logger.info(
                f"stage-artifact-verify: issue #{issue_number} PATCH branch truth is "
                f"indeterminate ({truth.reason}) — deferring rather than dispatching /do-patch"
            )
            return {
                "branch_truth": truth.status,
                "branch_truth_reason": truth.reason,
            }
        return {
            "branch_truth": truth.status,
            "branch_truth_branch": truth.branch,
        }

    return {}


def _verify_stage_artifacts(
    stage_states: dict,
    meta: dict,
    issue_number: int | None,
    heads: object = _UNSET,
) -> dict:
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
        return _verify_stage_artifacts_live(stage_states, meta, issue_number, heads)
    except _INFRA_ERRORS as e:
        logger.warning(
            f"stage-artifact-verify: infra error verifying issue #{issue_number} "
            f"artifacts ({type(e).__name__}: {e}) — failing open (advancing)"
        )
        # The direction is right (advance), but silence is not: an infra
        # failure used to be indistinguishable in the output from a genuine
        # clean verification. Report it AS indeterminate (#3065) — the flags
        # G8 reads are still unset, so nothing dispatches on it.
        return {
            "artifact_verification_indeterminate": True,
            "artifact_verification_reason": f"{type(e).__name__}: {e}",
        }
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

    # Branch truth, resolved ONCE per tick and shared by both consumers
    # (#3065). ``branch_exists`` (Row 5) and the G8 PATCH artifact check used
    # to ask two different questions of two different sources -- a local
    # ``git branch -a`` read that a stale remote-tracking ref satisfies, and a
    # live single-ref probe of a name derived from the recorded slug. One
    # resolver, one live listing, one answer.
    #
    # ``branch_exists`` is True only on *found*: an unreadable remote must not
    # assert existence. That matches the pre-#3065 behavior for the failure
    # case while removing the stale-local-ref false positive.
    heads: object = _UNSET
    if issue_number:
        context["branch_exists"] = False
        try:
            from tools.lane_identity import lane_branch_name, resolve_lane_slug

            lane_branch = lane_branch_name(resolve_lane_slug(issue_number))
            if lane_branch is not None:
                heads = _ls_remote_heads()
                context["branch_exists"] = (
                    heads is not None and f"refs/heads/{lane_branch}" in heads
                )
        except Exception as e:
            logger.debug("next-skill: branch-truth resolution failed (%s: %s)", type(e).__name__, e)
            context["branch_exists"] = False
            heads = _UNSET

    # Stage-advance outcome verification gate (#1267): verify claimed
    # stage-completion artifacts against the live world. No-op when
    # stage_states/meta were not supplied (see the docstring above) or when
    # no stage claims a checkable artifact this tick. The listing above is
    # handed down so the whole tick costs one `git ls-remote --heads origin`;
    # the PR-head resolve inside `resolve_branch_truth` stays lazy and runs
    # only when a PATCH claim actually needs adjudicating.
    if issue_number and stage_states is not None and meta is not None:
        context.update(_verify_stage_artifacts(stage_states, meta, issue_number, heads))

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


def build_decision_context(
    issue_number: int | None,
    stage_states: dict | None = None,
    meta: dict | None = None,
    proposed_skill: str | None = None,
) -> dict:
    """Public router-context builder, shared by BOTH ``decide_next_dispatch`` callers.

    The CLI path (:func:`decide`) and the in-process path
    (``agent/session_runner/runner.py::_load_ledger``) used to disagree by
    construction: the runner passed no context at all, so every context-fed
    guard — G3's proposed-skill arm, G5's plan-hash cache, G8's artifact
    verification — saw permanently empty inputs there and could reach a
    different answer than the CLI on the same lane. This function is the one
    place that assembles those facts, so "the two paths agree" is a property of
    the code rather than something a test has to keep chasing.

    Never raises: every fact-gathering step inside is individually guarded, and
    a total failure yields a partial (or empty) context, which is the same
    fail-open shape both callers already had.
    """
    try:
        return _build_context(proposed_skill, issue_number, stage_states, meta)
    except Exception as e:
        logger.debug(
            "build_decision_context failed for issue #%s (%s: %s)",
            issue_number,
            type(e).__name__,
            e,
        )
        return {}


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
    run_id: str | None = None,
) -> dict:
    """Run the dispatch algorithm and return a JSON-serialisable result dict.

    This is the programmatic interface; CLI consumers go through ``main()``.

    Args:
        issue_number: GitHub issue number to look up the session/lock for.
        session_id: Explicit AgentSession id (overrides issue-number lookup
            for the enriched-context resolution; unrelated to the lock peek).
        proposed_skill: The skill the LLM was about to invoke.
        run_id: Optional read-only identity assertion (issue #2766). When
            supplied, it IS the peek identity for the issue-lock pre-check --
            ``find_session_by_issue`` is never consulted, so a caller can
            never be told to stand down for a lock it holds itself just
            because its session record is momentarily invisible to that
            lookup (terminal status, non-``eng`` session_type, or a lookup
            exception). This is never minted, adopted, or written anywhere;
            it changes only *which identity ``touch_issue_lock`` compares
            against*, never *whether* the peek runs or whether it can
            acquire/renew (it never does -- ``peek=True`` on every path).
            When omitted, the pre-#2766 inference path
            (``find_session_by_issue(...).active_run_id``) runs unchanged.

    Returns:
        On ``Dispatch``: ``{"skill": "/do-X", "reason": "...", "row_id": "...",
        "decision": "dispatch", "recorded": False, "recorded_reason": ...}``.
        ``recorded`` is always ``False`` -- this function never writes to the
        ledger, so it never claims to (#2897); the caller records the
        dispatch with ``sdlc-tool dispatch record``.
        On ``Terminal``: ``{"decision": "terminal", "reason": "...",
        "evidence": "merge_marker" | "merged_pr", "row_id": "T"}`` -- the lane
        is finished. No ``blocked`` key and no ``recorded`` claim: there is
        nothing to escalate and nothing to record.
        On ``Blocked``: ``{"blocked": True, "decision": "blocked",
        "reason": "...", "guard_id": "..."}``, plus a ``"decision_inputs"``
        key (the ``stage_states``/``meta`` the router decided from, and on a
        reconciliation double-veto the selected row and vetoing guard) when
        the router populated one (#3065 Cluster D) -- notably on the
        ``NO_RULE`` fallthrough and on a reconciliation double-veto.
        On issue-lock contention: ``{"blocked": True, "reason": "ISSUE_LOCKED",
        "guard_id": "ISSUE_LOCK", "owner_session_id": "...", "peek_identity":
        "caller" | "session_mirror" | "unresolved"}`` (and, only on the
        caller-supplied blocked path, ``"session_mirror_run_id"``:
        diagnostics only, never used to override the block).
        On error: ``{"error": "...", "decision": "error"}``
    """
    try:
        from agent.sdlc_router import (
            Blocked,
            Dispatch,
            Terminal,
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

            # Run-identity peek (issue #2003, minimal call-site update; issue
            # #2766, caller-stated identity): this read-only pre-check
            # compares the lock against the CURRENT legitimate run's
            # identity. When a caller states its own identity via
            # ``run_id``, that assertion IS the peek identity, full stop --
            # find_session_by_issue is never consulted, so a caller can
            # never be told to stand down for its own lock just because the
            # session lookup happens to miss (terminal status, non-eng
            # session_type, or a lookup exception -- see #2766). Omitting
            # ``run_id`` preserves the original inference path unchanged:
            # read back the issue session's active_run_id mirror (still
            # read-only -- peek never mutates or adopts). When the compared
            # identity matches the lock's owner, the lock belongs to the run
            # driving this pipeline and next-skill proceeds; a mismatch
            # (crash window / foreign takeover mid-write, or a genuine rival)
            # blocks with the owner surfaced.
            #
            # The terminal filter and the eng-only filter inside
            # find_session_by_issue (#1915) are never widened and never
            # opted out of at this call site -- the peek argument passed to
            # touch_issue_lock is never inverted either. Widening either
            # would trade this bug for a #1915 regression; see the plan's
            # Rabbit Holes section.
            stated_run_id = (run_id or "").strip() or None
            peek_identity: str
            if stated_run_id:
                peek_run_id = stated_run_id
                peek_identity = "caller"
            else:
                peek_run_id = None
                try:
                    from tools._sdlc_utils import find_session_by_issue

                    issue_session = find_session_by_issue(issue_number)
                    if issue_session is not None:
                        peek_run_id = getattr(issue_session, "active_run_id", None)
                    peek_identity = "session_mirror" if peek_run_id else "unresolved"
                except Exception as e:
                    peek_run_id = None
                    peek_identity = "unresolved"
                    logger.debug(f"next-skill peek: session lookup failed ({e})")

            lock_result = touch_issue_lock(
                issue_number, peek_run_id, session_id=session_id or "", peek=True
            )
            if not lock_result.acquired:
                blocked_payload = {
                    "blocked": True,
                    "decision": "blocked",
                    "reason": "ISSUE_LOCKED",
                    "guard_id": "ISSUE_LOCK",
                    "owner_run_id": lock_result.owner_run_id,
                    "owner_session_id": lock_result.owner_session_id,
                    "orphaned_lock": lock_result.orphaned_lock,
                    "peek_identity": peek_identity,
                }
                if stated_run_id:
                    # Stale-self diagnostic (Race 4): a caller-supplied
                    # run_id that does not match the live lock might be this
                    # same supervisor's OWN successor id after an
                    # orphaned-lock re-ensure it forgot to rebind to, not a
                    # rival. Surface what the session mirror currently says
                    # so the supervisor's owned_run_ids self-identity check
                    # has the data to tell the two apart. Diagnostic only --
                    # never overrides the block, and a failure here must
                    # never turn the block into an error.
                    try:
                        from tools._sdlc_utils import find_session_by_issue

                        mirror_session = find_session_by_issue(issue_number)
                        if mirror_session is not None:
                            mirror_run_id = getattr(mirror_session, "active_run_id", None)
                            if mirror_run_id:
                                blocked_payload["session_mirror_run_id"] = mirror_run_id
                    except Exception as e:
                        logger.debug(f"next-skill peek: session-mirror diagnostic failed ({e})")
                return blocked_payload

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
            # ``recorded: False`` is unconditional and load-bearing (#2897):
            # this function has no write path, so a decision is never a
            # ledger advance. The caller records it via
            # ``sdlc-tool dispatch record`` before invoking the skill.
            dispatch_payload: dict = {
                "skill": result.skill,
                "reason": result.reason,
                "row_id": result.row_id,
                "decision": "dispatch",
                "recorded": False,
                "recorded_reason": NOT_RECORDED_REASON,
            }
            # #3065 Cluster D: report a PREVIOUS dispatch that carries no
            # confirming record. Distinct from ``recorded`` above, which is
            # about THIS decision (always False -- ``decide`` never writes).
            # Absent when the last dispatch is accounted for, so its mere
            # presence is the signal.
            if result.unrecorded_dispatch is not None:
                dispatch_payload["unrecorded_dispatch"] = result.unrecorded_dispatch
            return dispatch_payload
        elif isinstance(result, Terminal):
            # A finished lane is a SUCCESS, not an escalation (#2894, #2817).
            # Deliberately NOT folded into the blocked shape and carrying no
            # ``blocked`` key: a shipped pipeline reporting
            # ``{"blocked": true, "guard_id": "NO_RULE"}`` is exactly the
            # confusion this shape exists to end. No ``recorded`` claim either
            # -- there is no dispatch to record.
            return {
                "decision": "terminal",
                "reason": result.reason,
                "evidence": result.evidence,
                "row_id": result.row_id,
            }
        elif isinstance(result, Blocked):
            payload = {
                "blocked": True,
                "decision": "blocked",
                "reason": result.reason,
                "guard_id": result.guard_id,
            }
            # decision_inputs (#3065 Cluster D): the stage_states/meta (and,
            # on a reconciliation double-veto, the selected row + vetoing
            # guard) the router actually decided from. Optional — most
            # Blocked instances (the numbered guards) already state their
            # reason in prose and carry no decision_inputs — so this key is
            # only added when the router populated it.
            if result.decision_inputs is not None:
                payload["decision_inputs"] = result.decision_inputs
            return payload
        else:
            # Unexpected return type — treat as blocking error
            return {
                "error": f"Unexpected result type: {type(result).__name__}",
                "decision": "error",
            }

    except Exception as e:
        logger.debug(f"decide() failed: {e}", exc_info=True)
        return {
            "error": str(e),
            "decision": "error",
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
        "--run-id",
        metavar="ID",
        default=None,
        help=(
            "Optional read-only identity assertion (issue #2766): the caller's own "
            "run_id, used ONLY to peek the issue lock under the caller's own stated "
            "identity so a run is never told to stand down for its own lock. "
            "next-skill never mints, adopts, or renews a run_id with this value -- "
            "it changes only which identity is compared, never whether the peek "
            "runs. Omit to preserve the prior session-lookup inference path. "
            "Passing it does NOT make this call persist a dispatch; nothing here "
            "ever does -- use 'sdlc-tool dispatch record' for that (#2897)."
        ),
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
            json.dumps({"error": "Must supply --issue-number or --session-id", "decision": "error"})
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
            run_id=args.run_id,
        )

    if args.format == "pretty":
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result))

    # Exit 1 on error only. dispatch, terminal, and blocked are all valid
    # outcomes -- a terminal (finished) pipeline is a success, not a failure.
    if result.get("decision") == "error":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
