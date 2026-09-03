"""Lane identity for the SDLC pipeline: one recorded slug, minted once (#2735, #2718).

**Two slugs, kept distinct — read this before touching anything here.**

- The **lane slug** names the branch, the worktree, and the task list. It lives on
  ``PipelineLedger.slug``, is minted exactly once at lane start by
  ``tools/sdlc_session_ensure.py::ensure_session``, and is *read* by every other
  consumer. Nothing else derives it.
- The **plan-doc slug** is the plan filename stem. It is independent: a
  human-named plan (``session-liveness-tick-counter``) legitimately tracks a lane
  whose identity is issue-derived (sdlc-N).

``tracking:`` frontmatter is the bridge between the two, and it is the only
bridge. Do not re-unify these concepts — forcing them to be the same string is
what wedged #2663: G8 derived a slug from the plan filename and probed a branch
that never existed, so the PATCH artifact read as unverified forever.

**The adoption ladder**, walked by :func:`resolve_lane_slug` only when healing is
explicitly enabled (a lane-start path) and the field is still empty:

1. **The recorded value.** Never re-derive over a recorded slug. This rung is
   why the function is safe to call from anywhere.
2. **The lane's PR head SHA, matched against ``git ls-remote --heads origin``.**
   This rung sits *above* the direct branch probe because it is
   shape-agnostic: it recovers lanes whose branch is ``session/dev-<hash>`` or
   any other name a supervisor assigned, which a fixed-shape probe misses
   entirely. Adoption requires a *unique* match; zero or two-plus matches fall
   through (merged-and-deleted is the common zero case and is not an error).
3. **A direct probe for the issue-derived branch on origin.** Adopts an
   identity that already exists in the world before inventing one.
4. **Mint.** :func:`mint_lane_slug` is the sole home of the issue-derived slug
   literal in this repo.

Rungs 2 and 3 **adopt an identity that already exists in the world** -- a pushed
branch, a PR's head ref. A plan document is not an identity; it is a document
that mentions an issue, so a ``docs/plans/`` filename-stem rung is deliberately
absent. Reading a plan filename to name a lane is derivation wearing adoption's
clothes, and it is the precise defect this module closes.

**A wrong recorded slug is repairable**, by :func:`repair_lane_slug` and by
nothing else. The adoption ladder above is conditional-on-empty by design, so
it prevents the bad state but cannot exit it; the repair is the separate,
evidence-gated path out. It fires only where a *fail-closed decision* is about
to be taken on the recorded name, and only on a **unique** ``git ls-remote
--heads origin`` match against the lane's PR head SHA -- zero and two-or-more
matches leave the record alone. Every correction files its justification on the
ledger, so a wrong repair is auditable rather than silent. Ordinary reads are
unaffected: rung 1 still returns the recorded value and never re-derives.

A machine-local ``git worktree list`` rung is deliberately absent for a different
reason: it would make two hosts reach different answers for the same lane, and a
per-host identity is not an identity.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agent.pipeline_ledger import PipelineLedger
from tools import _sdlc_utils

logger = logging.getLogger(__name__)

# The one place the ``session/`` branch prefix is constructed. Every probe that
# acts on a lane branch routes through :func:`lane_branch_name`.
_BRANCH_PREFIX = "session/"
_REF_HEADS_PREFIX = "refs/heads/"

# Slug-write race budget. Popoto has no compare-and-set, so the healing write
# takes a short-lived SETNX on a dedicated non-Popoto key, re-reads, and writes
# only if the field is still empty. A loser re-reads and returns the winner's
# value; losing is not an error. Provisional/tunable, mirrors the create-race
# budget in agent/pipeline_ledger.py.
_SLUG_RACE_RETRY_ATTEMPTS = 5
_SLUG_RACE_RETRY_BACKOFF_S = 0.20
_SLUG_LOCK_TTL_S = 5

# Full-listing ls-remote is one round trip against origin; the ladder walks it
# at most once per healing call, and never on a read path.
_LS_REMOTE_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Pure constructors
# ---------------------------------------------------------------------------


def mint_lane_slug(issue_number: int) -> str:
    """Return the issue-derived lane slug for ``issue_number``.

    Pure and write-free. This is the **only** place in ``tools/``, ``agent/``
    and ``reflections/`` where this literal is constructed -- three independent
    minters used to exist and they drifted (#1915). One function, one home, one
    literal.
    """
    return f"sdlc-{issue_number}"


def lane_branch_name(slug: str | None) -> str | None:
    """Return the git branch name for ``slug``, or ``None`` when unresolvable.

    The ``session/`` prefix is applied here and nowhere else, so a consumer that
    has no slug gets ``None`` and no-ops rather than probing a guessed name.
    """
    if not slug or not slug.strip():
        return None
    return f"{_BRANCH_PREFIX}{slug.strip()}"


def _nonempty(value: object) -> str | None:
    """Return ``value`` as a stripped string, or ``None`` when blank.

    ``""`` and whitespace-only are both "no slug recorded" -- a lane whose
    identity is a run of spaces is not an identity.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# ---------------------------------------------------------------------------
# Plan-doc resolution: ownership is a `tracking:` line, never a mention
# ---------------------------------------------------------------------------


def find_plan_path(issue_number: int) -> Path | None:
    """Locate the plan file that *tracks* this issue, or ``None``.

    Resolution is **one rung**: a ``tracking:`` frontmatter line naming the
    issue. A plan that merely mentions ``#N`` in prose does not own N -- and a
    "Not building #N" No-Gos line is the *opposite* of ownership, which is
    exactly where the deleted bare-mention fallback used to answer confidently
    and wrongly (#2735). A scan of ``docs/plans/`` found 309 issue numbers with
    no owning plan that nonetheless resolved to one.

    Plans-directory resolution order (D1 -- portability), retained verbatim from
    the pre-move implementation:

    1. ``SDLC_TARGET_REPO`` env var (explicit override wins -- preserves
       backward-compatible cross-repo override semantics).
    2. Else the cwd's git working-tree root (``git rev-parse --show-toplevel``)
       so the pipeline finds plans in whatever repo it is invoked from.
    3. Else the ``__file__``-relative ``docs/plans`` fallback.

    Each step falls through on failure (not a git repo, ``git`` missing) so a
    missing env var degrades to "correct" rather than "silently wrong".

    ``_git_toplevel`` is reached through the ``_sdlc_utils`` module rather than
    bound at import time: fourteen existing tests monkeypatch that literal path,
    and a bound name would make every one of them inert.
    """
    if not issue_number:
        return None

    repo_root_env = os.environ.get("SDLC_TARGET_REPO")
    if repo_root_env:
        plans_dir = Path(repo_root_env) / "docs" / "plans"
    else:
        toplevel = _sdlc_utils._git_toplevel()
        if toplevel is not None:
            plans_dir = Path(toplevel) / "docs" / "plans"
        else:
            plans_dir = Path(__file__).resolve().parent.parent / "docs" / "plans"

    if not plans_dir.is_dir():
        return None

    # Match `tracking: ...#145`, `tracking: ...issues/145`, and the full
    # tracking URL, but NOT `#1455` (the trailing non-digit lookahead enforces
    # the boundary).
    tracking_re = re.compile(rf"^tracking:.*(?:#|issues/){issue_number}(?![0-9])", re.MULTILINE)
    try:
        for entry in sorted(plans_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".md":
                continue
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"find_plan_path: unreadable plan doc {entry.name}: {e}")
                continue
            if tracking_re.search(text):
                return entry
    except Exception as e:
        logger.debug(f"find_plan_path walk failed: {e}")

    return None


# Where completed plan documents are moved once their lane ships. Kept separate
# from the live plans dir on purpose: an archived plan is history, not an active
# lane artifact, so it must NOT make `plan_exists` true or route row 1.
_ARCHIVED_PLANS_RELPATH = ("docs", "archive", "plans-completed")


def find_archived_plan_path(issue_number: int) -> Path | None:
    """Locate an ARCHIVED plan that tracks this issue, or ``None``.

    Exists to close a hole in the skip precondition (#2851 recon). ``skip_stage``
    may only record PLAN/CRITIQUE as ``skipped`` when there is verifiably nothing
    to critique, and its first precondition is "no plan document" via
    :func:`find_plan_path` — which searches ``docs/plans/`` only. Archiving a
    plan therefore made its lane's CRITIQUE **retroactively skippable**, an
    undesigned escape hatch straight through the verdict invariant (#2415) that
    the precondition exists to protect. Measured on #2734 and #2741: both read
    ``plan_exists: false`` after their plans moved to the archive.

    This is deliberately a SEPARATE function rather than a widening of
    :func:`find_plan_path`. Callers that ask "does this lane have a live plan?"
    — the ``plan_exists`` meta field, row 1's no-plan predicate, G5's plan-hash
    anchor — must keep their current answer for an archived plan. Only the skip
    precondition cares that a plan *ever* existed.
    """
    if not issue_number:
        return None

    repo_root_env = os.environ.get("SDLC_TARGET_REPO")
    if repo_root_env:
        archive_dir = Path(repo_root_env).joinpath(*_ARCHIVED_PLANS_RELPATH)
    else:
        toplevel = _sdlc_utils._git_toplevel()
        if toplevel is not None:
            archive_dir = Path(toplevel).joinpath(*_ARCHIVED_PLANS_RELPATH)
        else:
            archive_dir = Path(__file__).resolve().parent.parent.joinpath(*_ARCHIVED_PLANS_RELPATH)

    if not archive_dir.is_dir():
        return None

    tracking_re = re.compile(rf"^tracking:.*(?:#|issues/){issue_number}(?![0-9])", re.MULTILINE)
    try:
        for entry in sorted(archive_dir.iterdir()):
            if not entry.is_file() or entry.suffix != ".md":
                continue
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"find_archived_plan_path: unreadable plan doc {entry.name}: {e}")
                continue
            if tracking_re.search(text):
                return entry
    except Exception as e:
        logger.debug(f"find_archived_plan_path walk failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Adoption ladder helpers
# ---------------------------------------------------------------------------


def _target_repo_cwd() -> str | None:
    """Filesystem path of the SDLC target checkout, for git subprocess ``cwd``.

    ``SDLC_TARGET_REPO`` is a FILESYSTEM PATH, never a gh slug. ``None`` (env
    unset/empty) preserves bridge behavior, where the process cwd already is
    the target checkout.
    """
    return os.environ.get("SDLC_TARGET_REPO") or None


def _ls_remote_heads() -> dict[str, str]:
    """Return ``{refname: sha}`` for every head on ``origin``, ``{}`` on failure.

    One full listing serves both PR-SHA matching and the direct branch probe, so
    a healing call costs at most one round trip regardless of how far down the
    ladder it walks. A failure is a clean "nothing to adopt", never an error:
    the ladder falls through to the mint.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=_target_repo_cwd(),
            capture_output=True,
            text=True,
            timeout=_LS_REMOTE_TIMEOUT_S,  # timeout-guard: allow
        )
    except Exception as e:
        logger.debug(f"lane_identity: git ls-remote failed: {e}")
        return {}
    if proc.returncode != 0:
        logger.debug(f"lane_identity: git ls-remote returned {proc.returncode}")
        return {}

    heads: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].startswith(_REF_HEADS_PREFIX):
            heads[parts[1].strip()] = parts[0].strip()
    return heads


def _slug_from_ref(ref: str) -> str | None:
    """``refs/heads/session/foo`` -> ``foo``; anything else -> ``None``."""
    prefix = f"{_REF_HEADS_PREFIX}{_BRANCH_PREFIX}"
    if not ref.startswith(prefix):
        return None
    return _nonempty(ref[len(prefix) :])


@dataclass(frozen=True)
class PrBranchTruth:
    """Which lane branch a PR's head SHA points at, per ``git ls-remote``.

    ``sha`` is the resolved head (``None`` when it did not resolve) and
    ``matches`` holds the lane slugs whose ``session/`` head sits at that SHA,
    sorted. ``unique_slug`` is the ONLY answer any caller may act on: a
    re-created branch, a fork, or a stale dev branch left at the same tip all
    produce duplicates, and a listing-order-dependent answer would be a
    per-invocation identity. Zero matches is the merged-and-deleted case.
    """

    sha: str | None = None
    matches: tuple[str, ...] = ()

    @property
    def unique_slug(self) -> str | None:
        return self.matches[0] if len(self.matches) == 1 else None


def _match_pr_head_to_lane_branches(pr_number: object, target_repo: str) -> PrBranchTruth:
    """Resolve the PR's head SHA and find the lane branches sitting at it.

    The head SHA comes from ``tools.pr_head_resolver.resolve_pr_head_sha``
    (git-first via ``git ls-remote origin refs/pull/N/head``) and **never** a
    bare ``gh`` read: a stale ``gh`` head SHA is what flipped the
    verdict-staleness gate fail-open in #2895.

    One matcher serves both the adoption rung and the repair path, so the
    ambiguity discipline cannot drift between them.
    """
    if not isinstance(pr_number, int) or pr_number < 1:
        return PrBranchTruth()

    from tools.pr_head_resolver import resolve_pr_head_sha

    try:
        sha = resolve_pr_head_sha(
            pr_number,
            repo=target_repo,
            repo_root=_target_repo_cwd(),
            cross_check=False,
        )
    except Exception as e:
        logger.debug(f"lane_identity: PR head resolution failed for PR {pr_number}: {e}")
        return PrBranchTruth()
    if not sha:
        return PrBranchTruth()

    matches = sorted(
        slug
        for ref, ref_sha in _ls_remote_heads().items()
        if ref_sha == sha and (slug := _slug_from_ref(ref))
    )
    return PrBranchTruth(sha=sha, matches=tuple(matches))


def _adopt_from_pr(pr_number: object, target_repo: str) -> str | None:
    """Rung 2: recover the lane branch name via the PR's head SHA.

    Shape-agnostic, which is why it precedes the fixed-shape probe: it is the
    rung that recovers a lane whose branch a supervisor named something else
    entirely. The match must be **unique** (see :class:`PrBranchTruth`).
    """
    truth = _match_pr_head_to_lane_branches(pr_number, target_repo)
    unique = truth.unique_slug
    if unique:
        return unique
    if truth.matches:
        logger.warning(
            "lane_identity: PR %s head %s matches %d lane branches (%s) -- "
            "ambiguous, falling through to the next rung",
            pr_number,
            truth.sha,
            len(truth.matches),
            ", ".join(truth.matches),
        )
    # Zero matches is the merged-and-deleted case: a clean fall-through.
    return None


def _adopt_pushed_lane_branch(issue_number: int) -> str | None:
    """Rung 3: adopt the issue-derived branch when it already exists on origin."""
    candidate = mint_lane_slug(issue_number)
    branch = lane_branch_name(candidate)
    if branch and f"{_REF_HEADS_PREFIX}{branch}" in _ls_remote_heads():
        return candidate
    return None


# ---------------------------------------------------------------------------
# Conditional-on-empty healing write
# ---------------------------------------------------------------------------


def _slug_lock_key(ledger_key: str) -> str:
    """Redis key for the slug-write serialization lock.

    A DEDICATED, NON-Popoto-managed key holding no model data, mirroring
    ``agent/pipeline_ledger.py``'s create lock. Distinct from that lock so a
    slug write never contends with a ledger create.
    """
    return f"sdlc:lane_slug_lock:{ledger_key}"


def _acquire_slug_lock(ledger_key: str) -> bool:
    """SETNX-acquire the slug-write lock. Fails OPEN on any broker error."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        return bool(
            POPOTO_REDIS_DB.set(_slug_lock_key(ledger_key), "1", nx=True, ex=_SLUG_LOCK_TTL_S)
        )
    except Exception as exc:  # pragma: no cover -- defensive, broker-dependent
        logger.warning(
            "lane_identity: slug-lock acquire failed for %r (failing open): %s", ledger_key, exc
        )
        return True


def _release_slug_lock(ledger_key: str) -> None:
    """Release the slug-write lock. Best-effort; the TTL is the fuse."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(_slug_lock_key(ledger_key))
    except Exception as exc:  # pragma: no cover -- defensive, broker-dependent
        logger.warning(
            "lane_identity: slug-lock release failed for %r (non-fatal): %s", ledger_key, exc
        )


def _record_slug_if_empty(ledger_key: str, candidate: str) -> str:
    """Write ``candidate`` to the ledger's ``slug`` only if it is still empty.

    Re-reads immediately before writing (a direct-key ``load()``, index-
    independent, so the #1720 class-set window does not apply), writes with
    ``update_fields=["slug"]`` so ``stage_states_json`` is never touched, and
    takes **no lease** -- this is an identity write, not a stage transition, and
    gating it on the lease would reintroduce the deadlock class #2026 closed.

    A lost race is not an error: the loser returns the winner's value.
    """
    lock_acquired = _acquire_slug_lock(ledger_key)
    try:
        if not lock_acquired:
            for attempt in range(_SLUG_RACE_RETRY_ATTEMPTS):
                fresh = PipelineLedger.load(ledger_key=ledger_key)
                recorded = _nonempty(getattr(fresh, "slug", None)) if fresh is not None else None
                if recorded:
                    return recorded
                if attempt < _SLUG_RACE_RETRY_ATTEMPTS - 1:
                    time.sleep(_SLUG_RACE_RETRY_BACKOFF_S)

        fresh = PipelineLedger.load(ledger_key=ledger_key)
        if fresh is None:
            # The record vanished between get_or_create and here. Return the
            # candidate so the caller still gets a usable identity; the next
            # lane-start call re-creates and records it.
            logger.debug("lane_identity: ledger %r disappeared before the slug write", ledger_key)
            return candidate
        recorded = _nonempty(getattr(fresh, "slug", None))
        if recorded:
            return recorded
        fresh.slug = candidate
        fresh.save(update_fields=["slug"])
        return candidate
    finally:
        if lock_acquired:
            _release_slug_lock(ledger_key)


# ---------------------------------------------------------------------------
# Evidence-gated repair of a wrong recorded slug
# ---------------------------------------------------------------------------

# Where a repair's justification is filed on the ledger. A correction to a
# lane's identity must be auditable after the fact -- a wrong repair moves a
# lane's branch, worktree, and task list, so "it changed and nobody can say
# why" is not an acceptable end state (#3065 Risk 3).
_SLUG_REPAIR_KEY = "_slug_repairs"


def _record_repair_evidence(
    ledger_key: str,
    previous: str,
    corrected: str,
    pr_number: object,
    head_sha: str | None,
) -> None:
    """Append this repair's justification to the ledger. Best-effort.

    Written through ``update_stage_states``' optimistic retry rather than the
    slug lock, because it touches ``stage_states_json`` — a blob with other
    concurrent writers — while the slug write touches only ``slug``. A failure
    here is logged and never raised: the correction itself already landed, and
    losing the audit trail must not turn a good repair into an exception.
    """
    try:
        from tools.stage_states_helpers import update_stage_states

        ledger = PipelineLedger.load(ledger_key=ledger_key)
        if ledger is None:
            return

        def _append(states: dict) -> dict:
            entries = states.setdefault(_SLUG_REPAIR_KEY, [])
            if isinstance(entries, list):
                entries.append(
                    {
                        "from": previous,
                        "to": corrected,
                        "pr_number": pr_number,
                        "head_sha": head_sha,
                        "at": int(time.time()),
                    }
                )
            return states

        update_stage_states(ledger, _append, field="stage_states_json")
    except Exception as e:
        logger.warning(
            "lane_identity: could not record slug-repair evidence for %r (%s -> %s): %s",
            ledger_key,
            previous,
            corrected,
            e,
        )


def _write_slug_repair(
    ledger_key: str,
    expected: str,
    corrected: str,
    pr_number: object,
    head_sha: str | None,
) -> str | None:
    """Overwrite a contradicted slug with ``corrected``. Returns what is recorded.

    Deliberately NOT :func:`_record_slug_if_empty`: that function's refusal to
    overwrite is the whole defect this closes. What replaces it is not "write
    unconditionally" but "write only against the value we adjudicated" — the
    recorded slug is re-read under the slug lock immediately before the write
    and compared to ``expected``:

    - already ``corrected`` — a concurrent repairer got there first. Both
      callers computed the same value from the same ground truth, so this
      converges to a **no-op** rather than a second write (Race 2).
    - neither ``expected`` nor ``corrected`` — the record moved under us and
      our evidence is about a value nobody has any more. Leave it alone.

    Returns the corrected slug on a write or a converged no-op, ``None`` when
    the record was left untouched.
    """
    lock_acquired = _acquire_slug_lock(ledger_key)
    try:
        if not lock_acquired:
            # Another writer holds the lock. Wait for it and adopt its result
            # if it is the same correction; never fight it.
            for attempt in range(_SLUG_RACE_RETRY_ATTEMPTS):
                fresh = PipelineLedger.load(ledger_key=ledger_key)
                current = _nonempty(getattr(fresh, "slug", None)) if fresh is not None else None
                if current == corrected:
                    return corrected
                if attempt < _SLUG_RACE_RETRY_ATTEMPTS - 1:
                    time.sleep(_SLUG_RACE_RETRY_BACKOFF_S)
            return None

        fresh = PipelineLedger.load(ledger_key=ledger_key)
        if fresh is None:
            logger.debug("lane_identity: ledger %r disappeared before the slug repair", ledger_key)
            return None
        current = _nonempty(getattr(fresh, "slug", None))
        if current == corrected:
            return corrected
        if current != expected:
            logger.warning(
                "lane_identity: recorded slug for %r changed from %r to %r under the repair "
                "-- leaving it alone rather than writing evidence about a stale value",
                ledger_key,
                expected,
                current,
            )
            return None
        fresh.slug = corrected
        fresh.save(update_fields=["slug"])
    finally:
        if lock_acquired:
            _release_slug_lock(ledger_key)

    logger.warning(
        "lane_identity: repaired the recorded lane slug for %r: %r -> %r "
        "(PR %s head %s uniquely matches session/%s)",
        ledger_key,
        expected,
        corrected,
        pr_number,
        head_sha,
        corrected,
    )
    _record_repair_evidence(ledger_key, expected, corrected, pr_number, head_sha)
    return corrected


def repair_lane_slug(
    issue_number: int,
    *,
    target_repo: str | None = None,
) -> str | None:
    """Correct a recorded lane slug that branch truth contradicts.

    Ordinary reads keep going through :func:`resolve_lane_slug` rung 1, which
    returns the recorded value and never re-derives over it. This function is
    for the narrow case that made a wrong slug permanent: a **fail-closed
    decision** about to be taken on the recorded name. Before failing a lane
    closed on "the branch is not pushed", the decision must first establish
    that it is asking about the right branch.

    The gate is deliberately narrow. A repair fires only when the lane's PR
    head SHA resolves (through ``tools.pr_head_resolver.resolve_pr_head_sha``,
    never a bare ``gh`` read) to **exactly one** branch in the ``git ls-remote
    --heads origin`` listing and that branch's slug differs from the recorded
    one. Zero matches and two-or-more matches both leave the record untouched,
    matching :func:`_adopt_from_pr`'s existing ambiguity discipline — a wrong
    repair is worse than a wrong original, because it moves a lane that was
    merely mislabelled.

    Creates nothing: a lane with no ledger, no recorded slug, or no PR is not
    a lane this function has anything to say about.

    Returns the slug branch truth confirms for this lane (the corrected value
    after a repair, or the recorded value when it was already right), or
    ``None`` when branch truth could not adjudicate.
    """
    if not issue_number or issue_number < 1:
        return None

    if target_repo is None:
        target_repo = _sdlc_utils.resolve_target_repo_for_read(issue_number)
    if not target_repo:
        logger.debug(
            "lane_identity: target repo unresolvable for issue #%s -- no repair", issue_number
        )
        return None

    ledger = PipelineLedger.get(target_repo, issue_number)
    if ledger is None:
        return None
    recorded = _nonempty(getattr(ledger, "slug", None))
    if not recorded:
        # An empty slug is the healing arm's job (conditional-on-empty), not
        # the repair's. There is no contradiction to adjudicate.
        return None

    pr_number = getattr(ledger, "pr_number", None)
    truth = _match_pr_head_to_lane_branches(pr_number, target_repo)
    corrected = truth.unique_slug
    if corrected is None:
        if truth.matches:
            logger.debug(
                "lane_identity: issue #%s PR %s head matches %d lane branches -- "
                "ambiguous, leaving the recorded slug %r alone",
                issue_number,
                pr_number,
                len(truth.matches),
                recorded,
            )
        return None
    if corrected == recorded:
        return recorded

    return _write_slug_repair(ledger.ledger_key, recorded, corrected, pr_number, truth.sha)


def adopt_lane_slug(
    issue_number: int,
    slug: str | None,
    *,
    target_repo: str | None = None,
) -> str | None:
    """Record an identity the caller **already knows**, conditional-on-empty.

    The ladder in :func:`resolve_lane_slug` exists for callers that must
    *discover* a lane's identity. Some callers do not have to: a caller holding
    a pushed branch name has already adopted the identity from the world, and it
    is strictly better evidence than anything the ladder could re-derive. The
    ladder's branch rung probes only the issue-derived name, so for a lane whose
    branch is human-named it misses and mints ``sdlc-<issue>`` -- a name that
    diverges from the branch the caller is looking at. That divergence is the
    defect this module closes, so re-deriving here would reintroduce it at the
    site meant to fix it.

    Hence the rule this module enforces in three clauses: a site that SEARCHES
    may guess, a site that WRITES identity may not, and a site that already
    KNOWS records what it knows.

    Writes through the same conditional-on-empty path as the healing arm, so it
    can never overwrite a recorded identity and needs no lease. Returns the
    recorded slug (the caller's value, or the winner's on a lost race), or
    ``None`` when there is nothing to record or no repo to key by.
    """
    slug = _nonempty(slug)
    if not issue_number or issue_number < 1 or not slug:
        return None

    if target_repo is None:
        target_repo = _sdlc_utils.resolve_target_repo_for_read(issue_number)
    if not target_repo:
        logger.debug(
            "lane_identity: target repo unresolvable for issue #%s -- not adopting %r",
            issue_number,
            slug,
        )
        return None

    ledger = PipelineLedger.get_or_create(target_repo, issue_number)
    if ledger is None:
        return slug
    return _record_slug_if_empty(ledger.ledger_key, slug)


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def resolve_lane_slug(
    issue_number: int,
    *,
    allow_heal: bool = False,
    target_repo: str | None = None,
) -> str | None:
    """Return the lane's recorded slug, or ``None``.

    ``allow_heal`` defaults to ``False`` and that default is load-bearing. With
    healing off the function stops after the recorded-value rung: no git call,
    no write, and **no ledger creation**. That matters because ``stage-query``
    runs for any issue number the router, the dashboard, or an operator asks
    about -- not just lanes. A healing default would mint an identity for issues
    that are not lanes, contradicting "minted exactly once at lane start".

    Only lane-start paths pass ``allow_heal=True``. They walk the adoption
    ladder (see the module docstring) and record the result
    conditional-on-empty, so the write can never overwrite and never needs a
    lease.

    ``target_repo`` is resolved by ``resolve_target_repo_for_read`` on **both**
    arms when the caller does not supply it. One resolver on both arms makes
    read/write key divergence impossible by construction rather than something a
    test has to chase. A caller that holds an authoritative repo slug -- the
    reflections, which iterate projects in a process whose cwd belongs to a
    different repo -- MUST pass it.

    Returns ``None`` when the target repo cannot be resolved. A ledger key is
    never assembled from a ``None`` repo: on the healing arm that would
    *create* a phantom record.
    """
    if not issue_number or issue_number < 1:
        return None

    if target_repo is None:
        target_repo = _sdlc_utils.resolve_target_repo_for_read(issue_number)
    if not target_repo:
        logger.debug(
            "lane_identity: target repo unresolvable for issue #%s -- returning None", issue_number
        )
        return None

    if allow_heal:
        ledger = PipelineLedger.get_or_create(target_repo, issue_number)
    else:
        ledger = PipelineLedger.get(target_repo, issue_number)

    # Rung 1: the recorded value. Never re-derive over it.
    recorded = _nonempty(getattr(ledger, "slug", None)) if ledger is not None else None
    if recorded:
        return recorded

    if not allow_heal or ledger is None:
        return None

    candidate = (
        _adopt_from_pr(getattr(ledger, "pr_number", None), target_repo)
        or _adopt_pushed_lane_branch(issue_number)
        or mint_lane_slug(issue_number)
    )
    return _record_slug_if_empty(ledger.ledger_key, candidate)
