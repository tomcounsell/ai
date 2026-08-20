"""
reflections/sdlc_progress.py — Stalled SDLC pipeline auto-resume (state-layer).

Companion to `agent.session_health` (process-layer). This reflection inspects
open lane PRs — every PR whose head branch is in the ``session/`` namespace,
whatever the rest of the name looks like — and, when a lane is genuinely
stalled, acts on it instead of paging a human.

Gates, in order, per open non-draft lane PR:

    1-2  head branch in the ``session/`` namespace with a non-empty slug, not
         draft
    3    last commit older than ``SDLC_STALL_THRESHOLD_HOURS``
    4    the **issue resolution ladder** (``_resolve_lane_issue``): recorded
         ledger by PR number, then the PR's own repo-matched closing
         reference, then the issue-derived slug shape. Ambiguity and
         non-resolution both decline and leave a ``gate-unknown`` finding —
         identity is never guessed.
    4'   issue still open (consumes the resolved number, so it cannot precede
         the ladder)
    5    lane liveness, read from the **issue lock**, never from session rows:
         ``session:issuelock:{N}`` is read directly and classified by
         ``models.session_lifecycle._lock_owner_is_live``. Key absent → free.
         Malformed payload, Redis error, or any exception → *unknown*, and
         unknown always declines to act. This is a **one-signal gate**: the
         lock is the sole liveness authority.
    6    this machine owns the project (``projects.<key>.machine``).

Then the action ladder, keyed on ``(slug, head-sha)``:

    escalation key already set                 -> stop acting entirely
    SDLC_STALL_RESUME_ENABLED=false            -> escalate once, stop
    attempts >= SDLC_STALL_RESUME_MAX_ATTEMPTS -> escalate once, stop
    rung 1  live non-ledger eng session        -> steer_session(...)
    rung 2  resumable eng session              -> resume_session(...)
    rung 3  no target                          -> create_session(slug=lane slug)
    rung 4  action failed (non-benign)         -> escalate once, stop

Rung selection runs BEFORE the action-cooldown claim, because every same-tick
brake needs its result: the per-tick target dedupe keys on the selected
session_id, and the degraded-create suppression keys on the selected ``kind``.
So the order below rung selection is: target dedupe, per-tick action cap,
create brake, degraded-create suppression, and only then the cooldown claim,
which is taken exclusively by a lane that is genuinely about to dispatch.

Rungs 1 and 2 both rank candidates ``(same_lane, recency)``, so a session
carrying the stalled lane's own slug always wins and recency decides only among
equals. Acting on another lane's session would land this issue's work as commits
on that lane's branch.

"Escalate once and **stop acting**" is literal: once the escalation key exists
for a ``(slug, head-sha)``, every later tick skips the lane before it can claim
an action window. That, plus the invariant that the attempts TTL is never
shorter than the escalation TTL, is what makes a create-loop impossible.

**Benign races are not attempts.** Another actor (typically
``reflections.crash_recovery``, or the lane re-taking its own issue lock) can
get there first. A resume failure whose row re-reads non-terminal, and a create
refusal against a lock that is now live, charge no attempt, fire no escalation,
and return early. The steer rung has no benign-race branch: every
``steer_session`` failure is a real dead end and charges an attempt.

The reflection never raises. Every external boundary (lock read, session query,
steer, resume, create, Redis, telegram) logs a warning and continues, and every
gate that returns *unknown* leaves a short marker in ``findings`` so a Redis or
``gh`` degradation is visible rather than looking like a healthy quiet tick.

Configuration (all optional, all provisional and tunable):
    SDLC_STALL_THRESHOLD_HOURS              default 4    minimum age of last commit
    SDLC_STALL_RESUME_ENABLED               default true break-glass; false =
                                                         notification-only, still
                                                         escalate-once
    SDLC_STALL_RESUME_MAX_ATTEMPTS          default 3    attempts per (slug, sha)
    SDLC_STALL_CREATE_MAX_PER_TICK          default 1    creations per project per tick
    SDLC_STALL_ACTIONS_MAX_PER_TICK         default 3    steer+resume+create per project
                                                         per tick; secondary to the
                                                         per-tick target dedupe
    SDLC_STALL_RESUME_COOLDOWN_HOURS        default 1    action cooldown per (slug, sha)
    SDLC_STALL_ATTEMPTS_TTL_DAYS            default 30   TTL on the attempts key; floored
                                                         at the escalation TTL
    SDLC_STALL_ESCALATION_TTL_DAYS          default 30   TTL on the escalation key

See ``docs/features/pm-session-liveness.md`` for the full state-layer rationale.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings
from reflections.pm_briefings.collector import _project_repo

# _LOCK_KEY, _lock_says_live and _get_redis moved to reflections/utilities.py
# (#2717) so the sibling reflection reflections/sdlc_upvote_lanes.py can share
# the same liveness rule and Redis connection instead of forking them.
# Re-exported here so this module's own call sites (and this module's own
# names, e.g. ``sdlc_progress._LOCK_KEY`` and ``sdlc_progress._get_redis``)
# keep resolving unchanged.
from reflections.utilities import (  # noqa: F401
    _LOCK_KEY,
    _get_redis,
    _lock_says_live,
    machine_owns_project,
    run_per_project_audit,
)
from tools.lane_identity import adopt_lane_slug

logger = logging.getLogger("reflections.sdlc_progress")

# Raw Redis namespace — NOT Popoto-managed. Pure bookkeeping per
# CLAUDE.md "no raw Redis on Popoto-managed keys" exception (precedent:
# docs_auditor.py REDIS_ISSUE_DEDUP_PREFIX).
#
# Three keys, three lifetimes. The action cooldown is short (retrying an action
# is cheap); the attempt budget is the convergence guard; the escalation key is
# the anti-ladder key that guarantees a human hears about a stalled head sha at
# most once.
_COOLDOWN_KEY = "sdlc:stall:resume:cooldown:{slug}:{sha}"
_ATTEMPTS_KEY = "sdlc:stall:resume:attempts:{slug}:{sha}"
_ESCALATED_KEY = "sdlc:stall:escalated:{slug}:{sha}"

# The issue lock, owned by models.session_lifecycle. Read directly (never via
# touch_issue_lock, which fails OPEN and so cannot express "unknown").
# _LOCK_KEY is imported from reflections.utilities above.

# `gh pr list` defaults to 30 results. The corpus is now the whole `session/`
# namespace rather than a handful of shape-matched branches, so the default is
# a silent truncation of the entire discovery surface. Explicit and generous.
_GH_PR_LIST_LIMIT = 200

# --- Thresholds -------------------------------------------------------------
# Every one of these is provisional and tunable via the paired env var. Take
# them with a grain of salt: they were chosen from one observed incident, not
# from a measured distribution.
_DEFAULT_THRESHOLD_HOURS = 4
_DEFAULT_RESUME_ENABLED = True
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_CREATE_MAX_PER_TICK = 1
# Secondary to the per-tick target dedupe, which is the real burst guard. This
# bounds steer + resume + create for ONE project tick, so the machine-wide
# ceiling is this number times the count of owned projects — the same scoping
# _DEFAULT_CREATE_MAX_PER_TICK already has. Provisional: chosen structurally
# (a detector with no track record on a newly-visible population should not act
# on all of it at once), not from a measured distribution. Take it with a grain
# of salt and tune via SDLC_STALL_ACTIONS_MAX_PER_TICK.
_DEFAULT_ACTIONS_MAX_PER_TICK = 3
_DEFAULT_RESUME_COOLDOWN_HOURS = 1
# INVARIANT: attempts TTL >= escalation TTL. If the attempts key lapsed first,
# the budget would re-arm while the escalation key still suppressed the page —
# the ladder would dispatch a fresh MAX_ATTEMPTS actions per attempts-TTL
# window, silently, for the whole escalation window. Both keys are sha-scoped,
# so a short attempts TTL buys nothing anyway: a new commit mints fresh keys.
# Enforced at read time by _attempts_ttl_seconds().
_DEFAULT_ATTEMPTS_TTL_DAYS = 30
_DEFAULT_ESCALATION_TTL_DAYS = 30


# _get_redis is imported from reflections.utilities above.


def _env_float(name: str, default: float) -> float:
    """Read a numeric env override, falling back to the module default."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _threshold_seconds() -> int:
    return int(_env_float("SDLC_STALL_THRESHOLD_HOURS", _DEFAULT_THRESHOLD_HOURS) * 3600)


def _action_cooldown_seconds() -> int:
    return int(
        _env_float("SDLC_STALL_RESUME_COOLDOWN_HOURS", _DEFAULT_RESUME_COOLDOWN_HOURS) * 3600
    )


def _attempts_ttl_seconds() -> int:
    """TTL on the attempts key, floored at the escalation TTL.

    The floor enforces the module invariant "attempts TTL >= escalation TTL":
    an attempts key that expires first re-arms the budget while the escalation
    key still suppresses the page, which turns "escalate once and stop" into
    "act forever, silently".
    """
    configured = int(_env_float("SDLC_STALL_ATTEMPTS_TTL_DAYS", _DEFAULT_ATTEMPTS_TTL_DAYS) * 86400)
    return max(configured, _escalation_ttl_seconds())


def _escalation_ttl_seconds() -> int:
    return int(_env_float("SDLC_STALL_ESCALATION_TTL_DAYS", _DEFAULT_ESCALATION_TTL_DAYS) * 86400)


def _max_attempts() -> int:
    return int(_env_float("SDLC_STALL_RESUME_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS))


def _create_max_per_tick() -> int:
    return int(_env_float("SDLC_STALL_CREATE_MAX_PER_TICK", _DEFAULT_CREATE_MAX_PER_TICK))


def _actions_max_per_tick() -> int:
    return int(_env_float("SDLC_STALL_ACTIONS_MAX_PER_TICK", _DEFAULT_ACTIONS_MAX_PER_TICK))


def _resume_enabled() -> bool:
    raw = os.environ.get("SDLC_STALL_RESUME_ENABLED")
    if raw is None:
        return _DEFAULT_RESUME_ENABLED
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _run_gh(
    args: list[str], *, cwd: str, timeout: int | None = None
) -> subprocess.CompletedProcess | None:
    """Run a gh CLI command. Returns CompletedProcess on success, None on failure."""
    if timeout is None:
        timeout = int(settings.timeouts.git_subprocess_s)
    try:
        return subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: gh CLI not on PATH; skipping")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: gh %s timed out", " ".join(args[:2]))
        return None
    except Exception as exc:
        logger.warning("sdlc_progress: gh %s failed: %s", " ".join(args[:2]), exc)
        return None


def _list_open_lane_prs(cwd: str) -> list[dict[str, Any]]:
    """Return open non-draft PRs whose head branch is a lane branch.

    The corpus boundary is the ``session/`` **namespace** — the repo's declared
    lane namespace, built in exactly one place
    (``tools.lane_identity.lane_branch_name``, used by
    ``agent.worktree_manager``). The *shape of the rest of the name* is not
    consulted: both an issue-derived lane name and a human-named one are real,
    and a detector that reads the shape can only ever see the first kind.

    Membership is expressed through ``_slug_from_branch`` returning a non-empty
    slug, so this module keeps a single owner of the prefix.

    ``closingIssuesReferences`` is requested here so the resolver's
    closing-reference rung costs no extra subprocess work. ``gh --json``
    selects top-level fields only, so each reference arrives as whatever object
    ``gh`` chooses to emit and the repository is derived from it downstream.

    Drafts stay excluded — a separate, deliberate exclusion this function does
    not revisit.
    """
    proc = _run_gh(
        [
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            str(_GH_PR_LIST_LIMIT),
            "--json",
            "number,headRefName,isDraft,baseRefName,closingIssuesReferences",
        ],
        cwd=cwd,
    )
    if proc is None or proc.returncode != 0:
        return []
    try:
        prs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        logger.warning("sdlc_progress: gh pr list returned non-JSON")
        return []
    return [
        pr
        for pr in prs
        if isinstance(pr, dict)
        and not pr.get("isDraft")
        and _slug_from_branch(pr.get("headRefName") or "")
    ]


def _issue_is_open(cwd: str, number: int) -> bool | None:
    """True if issue is open, False if closed, None if lookup failed/unknown."""
    proc = _run_gh(["issue", "view", str(number), "--json", "state"], cwd=cwd)
    if proc is None or proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    state = (data.get("state") or "").upper()
    if state == "OPEN":
        return True
    if state == "CLOSED":
        return False
    return None


def _slug_from_branch(branch: str) -> str | None:
    """Return the lane slug carried by a ``session/<slug>`` branch, else None.

    Shape-agnostic by design: whatever follows the prefix IS the slug, whether
    it is issue-derived or human-named. This is the module's single owner of
    the ``session/`` prefix and the only membership test for the corpus.

    Two remainders are rejected rather than returned, both because the slug is
    formatted straight into Redis keys:

    * **blank** — mirroring ``tools.lane_identity._nonempty``. ``"session/"``
      would otherwise yield ``""``, and two such branches would share one
      cooldown, attempts, and escalation key.
    * **containing a slash** — ``session/<slug>/fixup`` is not a lane, and
      admitting it would put a separator into every key formatted from it.
    """
    if not branch.startswith("session/"):
        return None
    slug = branch[len("session/") :].strip()
    if not slug or "/" in slug:
        return None
    return slug


def _issue_number_from_slug(slug: str) -> int | None:
    """Last-resort rung: *derive* an issue number from an issue-derived slug.

    This is the only rung of ``_resolve_lane_issue`` that derives rather than
    reads, which is exactly why it sits at the bottom. It answers only for a
    lane whose name happens to encode its issue, so on its own it is blind to
    every human-named lane — that blindness, when it was the module's whole
    discovery predicate, is the bug this ladder replaced. It survives because
    it costs nothing and still answers correctly for the lanes it can see, but
    both read-based rungs must decline before it is consulted.
    """
    m = re.match(r"sdlc-(\d+)$", slug)
    return int(m.group(1)) if m else None


def _ledger_pr_map(target_repo: str | None) -> dict[int, set[int]] | None:
    """One repo-scoped ``PipelineLedger`` enumeration per tick: ``{pr_number: {issue}}``.

    ``None`` means the enumeration **errored** (the resolution is degraded, and
    the caller suppresses the create rung); an empty dict means it cleanly
    found nothing, including the ``target_repo is None`` case where no query is
    issued at all — an unscoped enumeration could bind a lane to another repo's
    issue.

    Values are **sets**, never bare integers. Rung 1 requires exactly one
    distinct ``issue_number``, and a flat ``{pr_number: issue_number}`` map
    would silently overwrite on a second record — turning the ambiguity case
    into an undetectable unique *wrong* answer that then feeds
    ``adopt_lane_slug``, whose write is permanent.

    Enumerated once per tick rather than once per PR because ``pr_number`` and
    ``target_repo`` are unindexed plain ``Field``s: Popoto loads every key and
    filters in Python, so "repo-scoped" scopes nothing at the Redis layer and
    the cost is O(ledger) either way. Records with a ``None`` ``issue_number``
    are skipped, matching ``tools.merge_predicate._resolve_tracked_issue``.

    The import is lazy and broadly guarded: a module-level import lets a
    Popoto client-init failure break module load for the whole reflection.
    """
    if not target_repo:
        return {}
    try:
        from agent.pipeline_ledger import PipelineLedger

        records = list(PipelineLedger.query.filter(target_repo=target_repo))
    except Exception as exc:
        logger.warning(
            "sdlc_progress: rung ledger-by-pr enumeration failed for %s: %s", target_repo, exc
        )
        return None

    mapping: dict[int, set[int]] = {}
    for record in records:
        try:
            if getattr(record, "target_repo", None) != target_repo:
                continue
            pr_number = getattr(record, "pr_number", None)
            issue_number = getattr(record, "issue_number", None)
            if pr_number is None or issue_number is None:
                continue
            mapping.setdefault(int(pr_number), set()).add(int(issue_number))
        except Exception as exc:
            # A record we could not read makes the map silently incomplete,
            # which is indistinguishable from a clean decline — and a decline
            # does not suppress the create rung. Degrade the whole tick rather
            # than let a weaker rung write a permanent identity.
            logger.warning(
                "sdlc_progress: rung ledger-by-pr could not read a record for %s: %s",
                target_repo,
                exc,
            )
            return None
    return mapping


def _resolve_lane_issue(
    pr: dict[str, Any],
    slug: str,
    target_repo: str | None,
    ledger_map: dict[int, set[int]],
) -> tuple[int | None, str]:
    """Resolve the issue a lane PR belongs to. The module's only PR→issue path.

    Called the *issue resolution ladder* so it does not collide with
    ``tools/lane_identity.py``'s existing (and inverse) slug ladder. Ordered
    most-authoritative first:

    1. **Recorded ledger, by PR number** — a lookup into the per-tick
       ``{pr_number: set[int]}`` map, already repo-scoped by
       ``_ledger_pr_map``. Precedent: ``merge_predicate._resolve_tracked_issue``.
       Most authoritative because it *reads recorded identity*, not because it
       is infallible: ``pr_number`` is never cleared once written, so a stale
       but repo-matching record yields a unique *wrong* answer that no
       downstream gate catches. That is why every answer this ladder produces
       still passes through the unchanged gates.
    2. **The PR's own closing reference** — the single distinct entry in
       ``closingIssuesReferences`` **whose repository equals** ``target_repo``.
       This adopts an identity that exists in the world, and it is what covers
       the lanes the ledger cannot see. The repo match is not optional: GitHub
       permits a cross-repo ``Closes owner/repo#N``, a lone such reference
       would pass the uniqueness test, and the bare number would then be
       resolved against *this* project's repo.
    3. **Issue-derived slug shape** — ``_issue_number_from_slug``, the only
       rung that derives rather than reads, hence last.

    **Unique match required, everywhere.** A rung finding two or more distinct
    candidates returns ``(None, "ambiguous")`` and does **not** fall through:
    descending from "two authoritative answers" to "one guessed answer" is
    strictly worse than declining. A rung that cleanly finds nothing *declines*
    and falls through normally.

    **Declined is not errored.** An errored rung (a Redis outage on the ledger
    enumeration) is signalled by the caller receiving ``None`` from
    ``_ledger_pr_map``, not by this function: resolution still proceeds so
    steer and resume can act, but the caller suppresses the create rung and
    emits ``gate-unknown: ledger-degraded``. Create is the only action that
    writes permanent identity via ``adopt_lane_slug``, and that write is
    no-overwrite — steering the wrong session is recoverable, minting the wrong
    identity is not.

    Both read-based rungs are skipped entirely when ``target_repo`` is None.

    Returns ``(issue_number, rung_name)`` or ``(None, "ambiguous" |
    "unresolved")``.
    """
    # Rung 1: recorded ledger, by PR number.
    pr_number = pr.get("number")
    if isinstance(pr_number, int):
        recorded = ledger_map.get(pr_number) or set()
        if len(recorded) == 1:
            return (next(iter(recorded)), "ledger-by-pr")
        if len(recorded) > 1:
            logger.warning(
                "sdlc_progress: rung ledger-by-pr ambiguous for %s (PR #%s): %s",
                slug,
                pr_number,
                sorted(recorded),
            )
            return (None, "ambiguous")

    # Rung 2: the PR's own repo-matched closing reference.
    if target_repo:
        referenced: set[int] = set()
        for ref in pr.get("closingIssuesReferences") or []:
            if not isinstance(ref, dict):
                continue
            repository = ref.get("repository") or {}
            owner = (repository.get("owner") or {}).get("login")
            name = repository.get("name")
            if not owner or not name or f"{owner}/{name}" != target_repo:
                continue
            number = ref.get("number")
            if isinstance(number, int):
                referenced.add(number)
        if len(referenced) == 1:
            return (next(iter(referenced)), "closing-reference")
        if len(referenced) > 1:
            logger.warning(
                "sdlc_progress: rung closing-reference ambiguous for %s: %s",
                slug,
                sorted(referenced),
            )
            return (None, "ambiguous")

    # Rung 3: derive from an issue-derived slug.
    derived = _issue_number_from_slug(slug)
    if derived is not None:
        return (derived, "slug-shape")
    return (None, "unresolved")


def _last_commit(cwd: str, branch: str) -> tuple[str, int] | None:
    """Return (sha, unix_ts) of the last commit on ``origin/<branch>``.

    Returns None if the local ref isn't present — caller silently skips.
    Why ``origin/<branch>``? The worker may not have the local branch checked
    out (worktrees expose it only inside the worktree). The remote ref is the
    canonical view from the orchestrator's perspective.
    """
    ref = f"origin/{branch}"
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%H %ct", ref],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.git_subprocess_s,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: git not on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: git log timed out for %s", ref)
        return None
    except Exception as exc:
        logger.warning("sdlc_progress: git log raised for %s: %s", ref, exc)
        return None

    if proc.returncode != 0:
        # Branch not present locally. The caller turns this None into a
        # reported gate-unknown finding, so only the git detail is debug-level.
        logger.debug("sdlc_progress: %s not present locally (rc=%d)", ref, proc.returncode)
        return None
    parts = (proc.stdout or "").strip().split()
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


# --- Gate 5': liveness from the lock, not from session rows -----------------
#
# _lock_says_live is imported from reflections.utilities above (#2717) —
# not redefined here. See that module for the implementation and rationale.


def _lane_is_live(issue_number: int) -> bool | None:
    """True = a live lane owns this issue, False = free, None = unknown.

    A deliberately **one-signal** gate: the issue lock is the sole liveness
    authority. An earlier design OR-ed in a secondary ``AgentSession`` row
    check keyed on ``issue_number``; that branch was structurally dead, because
    no non-ledger creation path populates ``AgentSession.issue_number`` (the
    same gap documented at ``tools/merge_predicate.py``), and every row that
    does carry one is a ledger anchor the check skips. Inferring liveness from
    a row's existence is also the mirage the lock was adopted to kill. Do not
    reintroduce it without first plumbing ``issue_number`` through the
    creation path.
    """
    return _lock_says_live(issue_number)


# --- Redis bookkeeping ------------------------------------------------------


def _action_cooldown_set(slug: str, sha: str) -> bool:
    """Claim the action window for this ``(slug, sha)``. True = we may act.

    ``SET NX EX`` — atomic, so two overlapping ticks cannot both act. Redis
    unavailable → False (decline), preserving the "under-act while blind"
    posture.
    """
    key = _COOLDOWN_KEY.format(slug=slug, sha=sha)
    try:
        return bool(_get_redis().set(key, "1", nx=True, ex=_action_cooldown_seconds()))
    except Exception as exc:
        logger.warning("sdlc_progress: cooldown set failed for %s: %s", key, exc)
        return False


def _action_cooldown_release(slug: str, sha: str) -> None:
    """Hand the action window back after a claim that dispatched nothing.

    The claim in ``_action_cooldown_set`` is taken *after* rung selection and
    after every same-tick brake, so a lane deferred by the target dedupe, the
    action cap, the create brake, or the degraded-create suppression never
    reaches it — those paths need no release, and their "deferred to next tick"
    wording is literally true.

    What survives the claim is the narrow window between it and the dispatch
    itself: the create rung's pre-create lock re-read can still find a benign
    race, and a rung can still decline. Those paths sent nothing, so they owe
    the hour back rather than burning a full cooldown on a no-op tick.
    Releasing is safe precisely because the releasing tick did nothing: there
    is no action for a concurrent tick to duplicate.
    """
    key = _COOLDOWN_KEY.format(slug=slug, sha=sha)
    try:
        _get_redis().delete(key)
    except Exception as exc:
        logger.warning("sdlc_progress: cooldown release failed for %s: %s", key, exc)


def _attempts_count(slug: str, sha: str) -> int | None:
    """Read the attempt counter WITHOUT charging it. None = unknown.

    This is a bare ``GET`` (absent key = 0), consulted only by the pre-action
    budget gate. It must never be an ``INCR``: a lane sitting in the action
    cooldown would then charge an attempt every tick and escalate to a human
    without the ladder ever dispatching a real action.

    A read failure OR a non-integer payload both read as *unknown*, matching
    every other unknown in this module. Treating a corrupt payload as 0 would
    say "under budget, act" on the strength of data we cannot parse.
    """
    key = _ATTEMPTS_KEY.format(slug=slug, sha=sha)
    try:
        raw = _get_redis().get(key)
    except Exception as exc:
        logger.warning("sdlc_progress: attempts read failed for %s: %s", key, exc)
        return None
    if raw is None:
        return 0
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("sdlc_progress: attempts payload not an integer for %s: %r", key, raw)
        return None


def _bump_attempts(slug: str, sha: str) -> bool:
    """Charge one attempt (``INCR`` + TTL refresh). False = the write failed.

    Invoked from exactly one place: the post-classification charge step in
    ``_attempt_action``. A failure here cannot un-dispatch the action that was
    already sent, so the caller logs a marker and continues — it must never
    itself trigger an escalation.
    """
    key = _ATTEMPTS_KEY.format(slug=slug, sha=sha)
    try:
        r = _get_redis()
        r.incr(key)
        r.expire(key, _attempts_ttl_seconds())
        return True
    except Exception as exc:
        logger.warning("sdlc_progress: attempts bump failed for %s: %s", key, exc)
        return False


def _escalation_exists(slug: str, sha: str) -> bool | None:
    """Has a human already been paged about this ``(slug, sha)``? None = unknown.

    Read-only companion to ``_escalation_set``, consulted before the lane
    claims an action window. "Escalate once and stop acting" is only literal if
    a later tick can *see* the escalation: without this read the ladder would
    keep dispatching actions while ``_escalation_set``'s ``SET NX`` silently
    swallowed every further page.

    Unknown declines to act, consistent with every other unknown here.
    """
    key = _ESCALATED_KEY.format(slug=slug, sha=sha)
    try:
        return _get_redis().get(key) is not None
    except Exception as exc:
        logger.warning("sdlc_progress: escalation read failed for %s: %s", key, exc)
        return None


def _escalation_set(slug: str, sha: str) -> bool:
    """``SET NX`` the escalation key. True = this caller may page the human.

    False means either the human was already told about this head sha, or Redis
    is unavailable — in both cases nothing is sent.
    """
    key = _ESCALATED_KEY.format(slug=slug, sha=sha)
    try:
        return bool(_get_redis().set(key, "1", nx=True, ex=_escalation_ttl_seconds()))
    except Exception as exc:
        logger.warning("sdlc_progress: escalation set failed for %s: %s", key, exc)
        return False


# --- Messages ---------------------------------------------------------------


def _steer_message(*, slug: str, pr_number: Any, issue_number: int, age_hours: int) -> str:
    """The instruction sent to an eng session (and used as a created session's goal).

    Contains ``issue #{N}`` by design: it is what makes the message actionable
    and what ``_derive_sdlc_metadata`` keys on when the create rung mints a
    session. Asks for ONE ``/sdlc`` stage per CLAUDE.md principle 9.
    """
    return (
        f"SDLC lane {slug} is stalled: PR #{pr_number} on issue #{issue_number}, "
        f"last commit {age_hours}h ago, no live run holds the issue lock. "
        f"Resume the pipeline: invoke /sdlc for issue #{issue_number}. "
        "Route one stage, then return."
    )


def _escalation_message(
    *,
    project: str,
    slug: str,
    pr_number: Any,
    issue_number: int,
    age_hours: int,
    attempts: int,
    reason: str,
) -> str:
    """Attempt-and-failure voice: the system tried and could not."""
    return (
        f"[{project}] SDLC lane {slug} (PR #{pr_number}, issue #{issue_number}) stalled "
        f"{age_hours}h and auto-resume failed after {attempts} attempt(s): {reason}. "
        "Needs a human."
    )


def _escalate_once(
    *,
    project: str,
    slug: str,
    sha: str,
    pr_number: Any,
    issue_number: int,
    age_hours: int,
    attempts: int,
    reason: str,
) -> str | None:
    """Page a human at most once per ``(slug, head-sha)``.

    Returns the message that was sent, or None when nothing was sent — either
    because the human was already told about this head sha, or because Redis
    was unavailable for the ``SET NX`` guard (under-alert during a flap beats
    spam during one).
    """
    if not _escalation_set(slug, sha):
        logger.info("sdlc_progress: escalation already recorded for %s@%s", slug, sha[:8])
        return None
    message = _escalation_message(
        project=project,
        slug=slug,
        pr_number=pr_number,
        issue_number=issue_number,
        age_hours=age_hours,
        attempts=attempts,
        reason=reason,
    )
    _send_alert(message)
    return message


def _send_alert(message: str) -> None:
    """Best-effort Telegram alert. All failures swallowed and logged.

    Caller contract: fires ONLY from the escalation path, and only after
    ``_escalation_set`` returned True.
    """
    try:
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Eng: Valor", message],
            capture_output=True,
            text=True,
            timeout=settings.timeouts.subprocess_default_s,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("sdlc_progress: valor-telegram not on PATH; skipping alert")
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_progress: valor-telegram timed out")
    except Exception as exc:
        logger.warning("sdlc_progress: valor-telegram failed: %s", exc)


# --- The steer-target ladder ------------------------------------------------


def _pick_steer_target(project_key: str, lane_slug: str | None = None) -> tuple[str, Any]:
    """Return ``(kind, session)`` with kind in steer | resume | create | unknown.

    Rung 1 prefers a live (non-terminal, non-ledger) eng session for the
    project. Rung 2 falls back to a resumable eng session that carries a
    ``claude_session_uuid`` (required by ``resume_session``). Otherwise the
    caller creates one.

    Within BOTH buckets, a session whose ``slug`` matches ``lane_slug`` wins
    over a merely-more-recent one. This is not cosmetic: ``resume_session``
    transitions the row in place and the worker runs it in that row's own
    ``working_dir``, so resuming a session belonging to another lane makes work
    for issue A land as commits on lane B's branch. Only when no
    same-lane candidate exists does most-recently-updated decide.

    ``("unknown", None)`` means the session query itself failed — the caller
    declines to act rather than guessing.
    """
    try:
        from agent.session_health import _is_ledger
        from models.agent_session import AgentSession
        from models.session_lifecycle import NON_TERMINAL_STATUSES, RESUMABLE_STATUSES
        from utils.utc import to_unix_ts

        rows = list(AgentSession.query.filter(project_key=project_key, session_type="eng"))
    except Exception as exc:
        logger.warning("sdlc_progress: target query failed for %s: %s", project_key, exc)
        return ("unknown", None)

    def _rank(row) -> tuple[int, float]:
        """Same-lane first, then most recently updated."""
        same_lane = 1 if (lane_slug and getattr(row, "slug", None) == lane_slug) else 0
        return (same_lane, to_unix_ts(getattr(row, "updated_at", None)) or 0.0)

    live: list[Any] = []
    resumable: list[Any] = []
    for row in rows:
        try:
            if _is_ledger(row):
                continue
            status = getattr(row, "status", None)
            if status in NON_TERMINAL_STATUSES:
                live.append(row)
            elif status in RESUMABLE_STATUSES and getattr(row, "claude_session_uuid", None):
                resumable.append(row)
        except Exception as exc:  # pragma: no cover — defensive per-row guard
            logger.debug("sdlc_progress: target selection skipped a row: %r", exc)

    if live:
        return ("steer", max(live, key=_rank))
    if resumable:
        return ("resume", max(resumable, key=_rank))
    return ("create", None)


@dataclass
class ActionOutcome:
    """Result of one ladder action.

    ``kind`` is carried through so telemetry can tell the rungs apart —
    the create rung is the one to watch.
    """

    kind: str
    success: bool = False
    benign: bool = False
    dispatched: bool = False
    charged: bool = False
    charge_failed: bool = False
    declined: bool = False
    error: str | None = None


def _row_is_nonterminal(session_id: str) -> bool:
    """Re-read a row and report whether it is (still) non-terminal.

    Mirrors ``steer_session``'s re-read-before-reject pattern. Used only for
    benign-race classification on the resume rung; a failed re-read is not
    evidence of another actor, so it reports False.
    """
    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import TERMINAL_STATUSES

        fresh = AgentSession.query.filter(session_id=session_id).first()
        if fresh is None:
            return False
        return getattr(fresh, "status", None) not in TERMINAL_STATUSES
    except Exception as exc:
        logger.warning("sdlc_progress: benign-race re-read failed for %s: %s", session_id, exc)
        return False


def _attempt_action(
    kind: str,
    session: Any,
    *,
    slug: str,
    sha: str,
    issue_number: int,
    project_key: str,
    target_repo: str | None,
    message: str,
) -> ActionOutcome:
    """Dispatch one rung, classify the outcome, and charge the attempt.

    An attempt is charged on success AND on failure — a steer that lands but
    does not move the pipeline is precisely the steer-storm the budget bounds —
    but NEVER on a benign race, where another actor has already done the work.
    """
    outcome = ActionOutcome(kind=kind)

    if kind == "steer":
        session_id = getattr(session, "session_id", None) or ""
        try:
            from agent.session_executor import steer_session

            result = steer_session(session_id, message)
            outcome.dispatched = True
            outcome.success = bool(result.get("success"))
            outcome.error = result.get("error")
        except Exception as exc:
            logger.warning("sdlc_progress: steer_session raised for %s: %s", session_id, exc)
            outcome.dispatched = True
            outcome.error = f"steer raised: {exc}"
        # No benign-race branch, deliberately: a live non-ledger target simply
        # succeeds, so every steer failure means the target is gone, terminal,
        # never steerable, or the push errored — all real dead ends.

    elif kind == "resume":
        session_id = getattr(session, "session_id", None) or ""
        try:
            from tools.valor_session import resume_session

            result = resume_session(session, message, source="sdlc-stall")
            outcome.dispatched = True
            outcome.success = bool(getattr(result, "success", False))
            outcome.error = getattr(result, "error", None)
        except Exception as exc:
            logger.warning("sdlc_progress: resume_session raised for %s: %s", session_id, exc)
            outcome.dispatched = True
            outcome.error = f"resume raised: {exc}"
        if not outcome.success and _row_is_nonterminal(session_id):
            # Another actor (typically crash_recovery) resumed it first.
            outcome.benign = True
            return outcome

    elif kind == "create":
        # Gate 5' ran earlier in the tick; the lock can be re-acquired in
        # between, and a duplicate lane on top of a live one is the worst
        # outcome in this change. Re-read immediately before creating.
        lock_live = _lock_says_live(issue_number)
        if lock_live is None:
            outcome.declined = True
            outcome.error = "gate-unknown: create-lock-reread"
            return outcome
        if lock_live:
            outcome.benign = True
            outcome.error = "lane re-took its issue lock"
            return outcome
        # This rung only fires for an issue that already has a PR and a pushed
        # branch, so `slug` -- derived from that branch by the caller -- IS the
        # lane's identity, adopted from the world. Record it rather than asking
        # the resolver to re-derive it: the resolver's branch rung probes only
        # the issue-derived name, so for a human-named lane it would miss, mint
        # `sdlc-<issue>`, and create the session under a name that diverges from the
        # branch this tick is looking at. `slug` is therefore passed through
        # untouched, keeping the attempts, cooldown and escalation keys (all
        # charged against the caller's binding) on one name.
        # `target_repo` is threaded because this reflection iterates projects in
        # a process whose own cwd belongs to `ai` -- without it a non-`ai`
        # project's lane would be recorded under this repo's key.
        adopt_lane_slug(issue_number, slug, target_repo=target_repo)
        try:
            from tools.valor_session import create_session

            result = create_session(
                message=message,
                role="eng",
                slug=slug,
                project_key=project_key,
                session_type="eng",
            )
            outcome.dispatched = True
            outcome.success = bool(getattr(result, "success", False))
            outcome.error = getattr(result, "error", None)
        except Exception as exc:
            logger.warning("sdlc_progress: create_session raised for %s: %s", slug, exc)
            outcome.dispatched = True
            outcome.error = f"create raised: {exc}"
        if not outcome.success and _lock_says_live(issue_number) is True:
            # The lane restarted itself between the guard and the refusal.
            outcome.benign = True
            return outcome

    else:  # pragma: no cover — unreachable via _pick_steer_target
        outcome.declined = True
        outcome.error = f"unknown rung: {kind}"
        return outcome

    if _bump_attempts(slug, sha):
        outcome.charged = True
    else:
        outcome.charge_failed = True
    return outcome


# --- Per-project body -------------------------------------------------------


def _check_project_stalls(project: dict) -> dict:
    """Per-project body called by ``run_per_project_audit``.

    Returns ``{status, findings, summary, duration}``.
    """
    t0 = time.time()
    wd = project.get("working_directory", "")
    project_key = project.get("slug", "?")
    # The authoritative repo slug for THIS project. This process's own cwd
    # belongs to `ai` regardless of which project the tick is acting on -- only
    # subprocesses get `wd` -- so lane identity must be keyed explicitly or a
    # non-`ai` project's lane records under the wrong repo. `None` here is NOT
    # a safe default: `adopt_lane_slug` would fall back to lease-first
    # resolution and, with no live lease, to this process's cwd -- recording the
    # lane under `ai`. It is also what both read-based rungs of the issue
    # resolution ladder are scoped by, so `None` costs the tick its ledger
    # lookup and its closing-reference rung as well: an unscoped ledger query
    # could bind a lane to another repo's issue, and a closing reference cannot
    # be repo-matched with no repo to match against. An SDLC-capable project
    # carries a `github` block, so this is a gap rather than a tolerance.
    target_repo = _project_repo(project)
    findings: list[str] = []
    counts = {"steered": 0, "resumed": 0, "created": 0, "escalated": 0}
    # Per-call state, deliberately not in Redis: it brakes creations WITHIN one
    # project tick. It is therefore NOT atomic across overlapping ticks — two
    # ticks racing on different lanes of the same project can each mint one
    # session. Accepted at a 30-minute cadence; the per-(slug, sha) attempt
    # budget bounds the total regardless. See the plan's Risks table.
    creates_this_tick = 0
    # Same per-call, non-atomic status as `creates_this_tick`. `actions_this_tick`
    # is the secondary count bound; `targets_this_tick` is the PRIMARY burst
    # guard -- `_pick_steer_target` queries project-wide with same-lane only a
    # ranking preference, so several newly-visible lanes with no same-lane
    # session all select the SAME eng session, which a count cap alone would
    # happily steer three times in one tick with three different issues.
    actions_this_tick = 0
    targets_this_tick: set[str] = set()

    if not wd or not Path(wd).is_dir():
        return {
            "status": "skipped",
            "findings": [],
            "summary": "sdlc-progress-check: no working_directory",
            "duration": time.time() - t0,
        }

    # Gate 6': single-machine ownership. Two machines sharing a checkout must
    # never both act on the same lane. An exception inside the helper is
    # already swallowed there and reported as not-owner.
    try:
        owns = machine_owns_project(project_key)
    except Exception as exc:  # pragma: no cover — helper is fail-soft already
        logger.warning("sdlc_progress: ownership check raised for %s: %s", project_key, exc)
        owns = False
    if not owns:
        return {
            "status": "skipped",
            "findings": [],
            "summary": f"sdlc-progress-check: {project_key} not owned by this machine",
            "duration": time.time() - t0,
        }

    threshold = _threshold_seconds()
    max_attempts = _max_attempts()
    create_budget = _create_max_per_tick()
    action_budget = _actions_max_per_tick()
    resume_enabled = _resume_enabled()
    now = int(time.time())

    # One enumeration for the whole tick. None = it errored, which degrades
    # every rung-1 read and suppresses the create rung for this project.
    ledger_map = _ledger_pr_map(target_repo)
    ledger_degraded = ledger_map is None

    prs = _list_open_lane_prs(wd)
    for pr in prs:
        branch = pr.get("headRefName") or ""
        slug = _slug_from_branch(branch)
        if not slug:
            continue

        # Staleness first: it needs only the branch, so a fresh healthy lane
        # never pays for identity resolution or a `gh issue view`. It also
        # bounds finding noise -- unlike every other gate-unknown here, an
        # unresolvable identity is a STABLE condition that would otherwise emit
        # a finding every tick forever.
        commit = _last_commit(wd, branch)
        if commit is None:
            # No local `origin/` ref. Reported rather than skipped silently:
            # once the corpus is the whole namespace this skip is load-bearing,
            # and a silently-dropped lane is half of what this detector's
            # branch-shape era got wrong.
            findings.append(f"gate-unknown: branch-not-fetched {slug}")
            continue
        sha, ts = commit
        age = now - ts
        if age < threshold:
            continue
        age_hours = age // 3600

        issue_num, rung = _resolve_lane_issue(pr, slug, target_repo, ledger_map or {})
        # Reported before the unresolved bail: a lane that resolved to nothing
        # while the ledger was dark is the one case where the degradation
        # changed the outcome, so it is the one case that must say so.
        if ledger_degraded:
            findings.append(f"gate-unknown: ledger-degraded {slug}")
        if issue_num is None:
            if rung == "ambiguous":
                findings.append(f"gate-unknown: issue-ambiguous {slug}")
            else:
                findings.append(f"gate-unknown: issue-unresolved {slug}")
            continue

        issue_open = _issue_is_open(wd, issue_num)
        if issue_open is False:
            continue
        if issue_open is None:
            findings.append(f"gate-unknown: issue-state {slug}")
            continue

        # Gate 5': liveness from the lock.
        live = _lane_is_live(issue_num)
        if live is None:
            findings.append(f"gate-unknown: lock-read {slug}")
            continue
        if live:
            continue

        # "Escalate once and stop acting", enforced literally. Without this
        # read the ladder keeps dispatching actions after the page (the
        # SET NX in _escalation_set silently swallows the extra escalations),
        # so the human sees one message while the system acts forever.
        escalated = _escalation_exists(slug, sha)
        if escalated is None:
            findings.append(f"gate-unknown: escalation-read {slug}")
            continue
        if escalated:
            findings.append(f"already-escalated: {slug}")
            continue

        def _escalate(reason: str, attempts: int) -> None:
            msg = _escalate_once(
                project=project_key,
                slug=slug,
                sha=sha,
                pr_number=pr.get("number"),
                issue_number=issue_num,
                age_hours=age_hours,
                attempts=attempts,
                reason=reason,
            )
            if msg:
                counts["escalated"] += 1
                findings.append(msg)

        if not resume_enabled:
            _escalate("auto-resume disabled (SDLC_STALL_RESUME_ENABLED=false)", 0)
            continue

        attempts = _attempts_count(slug, sha)
        if attempts is None:
            # Cannot prove we are under budget -> decline, and do NOT escalate.
            findings.append(f"gate-unknown: attempts-read {slug}")
            continue
        if attempts >= max_attempts:
            _escalate("attempt budget exhausted", attempts)
            continue

        # Rung selection runs BEFORE the cooldown claim: every same-tick brake
        # below needs its result (the dedupe keys on the target session_id, the
        # degraded-create suppression keys on `kind`), and a lane deferred
        # after an hour-long SETNX claim would wait an hour, not a tick.
        kind, target = _pick_steer_target(project_key, lane_slug=slug)
        if kind == "unknown":
            findings.append(f"gate-unknown: target-query {slug}")
            continue

        target_id = getattr(target, "session_id", None) or ""
        if target_id and target_id in targets_this_tick:
            findings.append(f"action-cap: {slug} deferred to next tick (target already dispatched)")
            continue
        if actions_this_tick >= action_budget:
            findings.append(f"action-cap: {slug} deferred to next tick")
            continue
        if kind == "create" and creates_this_tick >= create_budget:
            findings.append(f"create-brake: {slug} deferred to next tick")
            continue
        if kind == "create" and ledger_degraded:
            # Create is the only action that writes permanent identity via
            # `adopt_lane_slug`, and that write is no-overwrite. Steering the
            # wrong session is recoverable; minting the wrong identity from a
            # resolution the ledger could not vouch for is not.
            findings.append(f"create-suppressed: {slug} (ledger degraded)")
            continue

        # Claims the action window; also the overlapping-tick guard.
        if not _action_cooldown_set(slug, sha):
            logger.info("sdlc_progress: action cooldown live for %s@%s", slug, sha[:8])
            continue

        message = _steer_message(
            slug=slug,
            pr_number=pr.get("number"),
            issue_number=issue_num,
            age_hours=age_hours,
        )
        outcome = _attempt_action(
            kind,
            target,
            slug=slug,
            sha=sha,
            issue_number=issue_num,
            project_key=project_key,
            target_repo=target_repo,
            message=message,
        )
        if outcome.dispatched:
            actions_this_tick += 1
            if target_id:
                targets_this_tick.add(target_id)
            if kind == "create":
                creates_this_tick += 1
        if outcome.charge_failed:
            findings.append(f"charge-failed: attempts-incr {slug}")

        if outcome.benign:
            findings.append(f"benign-race: {kind} {slug}")
            if not outcome.dispatched:
                # A benign race caught BEFORE dispatch (the create rung's
                # pre-create lock re-read) sent nothing, so the lane owes no
                # cooldown. A benign race after a dispatch (the resume rung)
                # keeps the window: a message really did land.
                _action_cooldown_release(slug, sha)
            continue
        if outcome.declined:
            # Declined means the rung never dispatched — nothing happened, so
            # the action window is returned for the next tick.
            findings.append(f"{outcome.error} {slug}")
            _action_cooldown_release(slug, sha)
            continue

        if outcome.success:
            counts[{"steer": "steered", "resume": "resumed", "create": "created"}[kind]] += 1
            findings.append(f"auto-resume {kind}: {slug} (PR #{pr.get('number')})")
            continue

        _escalate(f"{kind} failed: {outcome.error or 'unknown error'}", attempts + 1)

    return {
        "status": "ok",
        "findings": findings,
        "summary": (
            f"sdlc-progress-check: {len(prs)} lane PR(s) inspected, "
            f"{counts['steered']} steered, {counts['resumed']} resumed, "
            f"{counts['created']} created, {counts['escalated']} escalated"
        ),
        "duration": time.time() - t0,
    }


def run_sdlc_progress_check() -> dict:
    """Reflection entrypoint. Iterates every local project."""
    return run_per_project_audit(_check_project_stalls, name="sdlc-progress-check")
