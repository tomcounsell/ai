"""Shared utilities for SDLC session and plan lookups.

Extracted from tools/sdlc_stage_query.py, sdlc_verdict.py, and sdlc_dispatch.py
to avoid duplicating session-lookup and plan-path logic across SDLC tool modules.

Imports models.agent_session plus agent.sdlc_router (the sanctioned tools→agent
direction — the router itself never imports tools/, so no cycle; see
tests/unit/test_architectural_constraints.py).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

# Canonical home is agent/sdlc_router.py — the router needs normalize_verdict
# and must not import from tools/. Re-exported here for the existing
# tools/tests import path.
from agent.sdlc_router import normalize_verdict  # noqa: F401
from models.agent_session import AgentSession

logger = logging.getLogger(__name__)


def _resolve_target_repo() -> str | None:
    """Resolve the owner/name GitHub slug for the target repo.

    Resolution ladder:
    1. GH_REPO env var — already an owner/name slug, return directly (zero subprocess).
    2. SDLC_TARGET_REPO env var — a FILESYSTEM PATH (not a slug!), used as cwd for
       ``gh repo view --json nameWithOwner -q .nameWithOwner``; slug is the command's stdout.
    3. _git_toplevel() — also used as cwd for same gh repo view command.
    4. None — every step falls through on failure; degrades to current behavior.

    IMPORTANT: SDLC_TARGET_REPO is NEVER passed to gh --repo. It is a path, not a slug.
    """
    # Rung 0: GH_REPO is already an owner/name slug — return it directly.
    if repo := os.environ.get("GH_REPO"):
        return repo
    # Rung 1: SDLC_TARGET_REPO is a FILESYSTEM PATH — use it as cwd, not as --repo.
    # Rung 2: else the git working-tree root, also used as cwd.
    cwd = os.environ.get("SDLC_TARGET_REPO") or _git_toplevel()
    if not cwd:
        return None
    try:
        proc = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            logger.warning(
                f"_resolve_target_repo: gh repo view failed (rc={proc.returncode}) in cwd={cwd}"
            )
            return None
        slug = (proc.stdout or "").strip()
        return slug or None
    except Exception as e:
        logger.warning(f"_resolve_target_repo: gh repo view raised in cwd={cwd}: {e}")
        return None


def _git_toplevel(cwd: Path | None = None) -> Path | None:
    """Return the git working-tree root for ``cwd`` (default: process cwd).

    Returns None when ``git`` is missing, the directory is not a git repo, or
    the call times out. Callers fall through to the next resolution step.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd or Path.cwd()),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"_git_toplevel failed: {e}")
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


# Statuses excluded by default from find_session_by_issue()'s three passes.
# A terminal session must never be returned as "the" owner of an issue unless
# a caller explicitly opts in via include_terminal=True — see #1954/incident
# #1915, where a terminal session was revived and picked back up as the owner
# while a second, independent live session already believed it owned the issue.
_TERMINAL_ISSUE_LOOKUP_STATUSES = frozenset({"failed", "completed", "killed"})


def find_session_by_issue(issue_number: int, include_terminal: bool = False):
    """Find an eng session tracking the given issue number.

    Two-pass match over eng sessions:

    1. Primary pass: ``issue_url`` endswith ``/issues/{issue_number}``.
    2. Fallback pass: ``message_text`` matches the case-insensitive regex
       ``\\bissue\\s*#?\\s*{issue_number}\\b``. This catches Telegram-
       originated eng sessions that have no ``issue_url`` (the bridge builds
       sessions from message text, not URLs) so operators running SDLC over
       the bridge are still findable by issue number.

    Resolution order (tightened for #1671/#1672 — concern C2):

    1. **issue_url ownership pass**: scan eng sessions for one whose
       ``issue_url`` endswith ``/issues/{issue_number}``. A live bridge eng
       session that owns the issue via its URL wins over a stale deterministic
       ``sdlc-local-{N}`` record. This pass runs FIRST so a leftover
       ``sdlc-local-{N}`` from an earlier local run can never shadow the
       authoritative bridge session that owns the issue.
    2. **Deterministic-id pass**: a session auto-ensured by
       ``find_session(ensure=True)`` (or by ``sdlc_session_ensure``) is keyed
       ``sdlc-local-{N}`` and may carry no ``issue_url`` / ``message_text``.
       Match it by its deterministic id so the READ path (verdict get,
       stage-query, next-skill) finds the same record a prior WRITE created.
       This is the fallback for the sessionless-local case it was built for
       (#1558) — only reached when no eng session owns the issue via
       ``issue_url``.
    3. **message_text fallback pass**: match by message_text for bridge-
       originated sessions that have no ``issue_url``.

    The ``issue_url`` pass takes priority: if any session matches there, it
    is returned without running the deterministic-id or ``message_text``
    scans. When multiple sessions could match via ``message_text`` alone (e.g.,
    a conversation mentioning two issue numbers), the first iterated session
    wins — this is an acceptable limitation because bridge sessions today carry
    a single originating message and multi-issue mentions are rare.

    Terminal-session filtering (#1954, incident #1915): all three passes
    exclude sessions whose ``status`` is ``failed``, ``completed``, or
    ``killed`` by default. A terminal session is not "the" owner of an
    issue -- reviving one and letting a second, independent live session
    believe it also owns the issue is exactly how incident #1915 happened.
    Pass ``include_terminal=True`` to opt into seeing terminal sessions too
    (e.g. audit/debug/reporting tooling that legitimately wants historical
    sessions); the default (``False``) is correct for routing/dispatch code
    that must never resolve a dead session as the live owner.

    Args:
        issue_number: GitHub issue number to search for.
        include_terminal: When ``False`` (the default), sessions with
            ``status`` in ``{"failed", "completed", "killed"}`` are excluded
            from all three passes. Pass ``True`` to include them.

    Returns:
        AgentSession or None.
    """
    if not issue_number or issue_number < 1:
        return None

    try:
        # issue_url ownership pass (C2): a live bridge PM session that owns the
        # issue via its URL must win over a stale deterministic sdlc-local-{N}
        # record. Compute this FIRST so the deterministic-id fallback never
        # shadows the authoritative bridge session.
        #
        # NOTE: Linear scan of eng sessions — acceptable for current scale (typically
        # <100 eng sessions). If session count grows significantly, consider adding
        # an indexed lookup by issue_url or caching issue->session mappings.
        eng_sessions = list(AgentSession.query.filter(session_type="eng"))
        if not include_terminal:
            eng_sessions = [
                s
                for s in eng_sessions
                if getattr(s, "status", None) not in _TERMINAL_ISSUE_LOOKUP_STATUSES
            ]
        target_suffix = f"/issues/{issue_number}"
        for s in eng_sessions:
            issue_url = getattr(s, "issue_url", None) or ""
            if issue_url.endswith(target_suffix):
                return s

        # Deterministic-id pass: only reached when no eng session owns the issue
        # via issue_url. Matches the sdlc-local-{N} record a prior sessionless
        # WRITE created so the subsequent READ finds it (#1558).
        local_id = f"sdlc-local-{issue_number}"
        try:
            local = list(AgentSession.query.filter(session_id=local_id))
            # Verify the returned record's id actually matches — a query backend
            # (or test mock) that ignores the filter must not yield a false hit.
            local = [s for s in local if getattr(s, "session_id", None) == local_id]
            if not include_terminal:
                local = [
                    s
                    for s in local
                    if getattr(s, "status", None) not in _TERMINAL_ISSUE_LOOKUP_STATUSES
                ]
            for s in local:
                if getattr(s, "session_type", None) == "eng":
                    return s
            if local:
                return local[0]
        except Exception as e:
            logger.debug(f"find_session_by_issue deterministic-id pass failed: {e}")

        # Fallback: match by message_text for bridge-originated sessions that
        # have no issue_url. Word boundaries prevent matches like
        # "tissue 1147" — only "issue 1147", "issue #1147", "SDLC issue 1147".
        pattern = re.compile(rf"\bissue\s*#?\s*{issue_number}\b", re.IGNORECASE)
        for s in eng_sessions:
            message_text = getattr(s, "message_text", None) or ""
            if message_text and pattern.search(message_text):
                return s

        return None
    except Exception as e:
        logger.debug(f"find_session_by_issue failed: {e}")
        return None


def find_session(
    session_id: str | None = None,
    issue_number: int | None = None,
    ensure: bool = False,
    caller_run_id: str | None = None,
):
    """Resolve a PM AgentSession by session_id or issue_number.

    Resolution order (precedence — corrected for #1671/#1672):

    1. **Explicit ``session_id`` argument** — highest precedence. A caller that
       passes a concrete id means it; this is unchanged and overrides everything
       below, including issue-based resolution.
    2. **Issue-based lookup** via :func:`find_session_by_issue`, attempted when
       ``issue_number is not None and issue_number >= 1``. This now runs *before*
       env-var resolution so that an explicit ``--issue-number N`` write lands on
       the same session the router reads for that issue (the deterministic
       ``sdlc-local-{N}`` or the bridge PM session that owns the issue via
       ``issue_url``). A forked subagent that inherited a parent's
       ``VALOR_SESSION_ID`` no longer diverts the write to the parent's session.
    3. **Env-var session** (``VALOR_SESSION_ID`` / ``AGENT_SESSION_ID``) — a
       *fallback*, consulted only when there is no explicit ``session_id`` and no
       issue-based match. This preserves the bridge case: a write with no
       ``--issue-number`` resolves the env-var session exactly as before.
    4. **Auto-ensure** (writes only) — unchanged, gated on
       ``issue_number >= 1`` or env-var presence.

    *Why issue-number beats env-var (steps 2 vs 3):* the #1671/#1672 skew — reads
    resolved by issue number while writes resolved by an inherited env-var session
    — silently fragmented SDLC state. Both paths now consult
    ``find_session_by_issue`` first for an explicit issue number, so reads and
    writes converge on one session.

    Args:
        session_id: Optional explicit session ID.
        issue_number: Optional GitHub issue number for issue-based lookup.
        ensure: Opt-in auto-create flag. When ``False`` (the default) this is a
            pure, side-effect-free lookup — no session is ever created. **Four
            SDLC *write* subcommands** pass ``ensure=True``:
            ``sdlc_meta_set.write_meta``, ``sdlc_stage_marker.write_marker``,
            ``sdlc_verdict._cli_record``, and ``sdlc_dispatch._cli_record``
            (the dispatch ``record`` path joined the other three so a cold-start
            ``dispatch record --issue-number N`` has an issue-scoped home — see
            #1671). The ``dispatch`` ``get``/``reset`` paths stay non-ensuring.
            So a write always has a home regardless of how the pipeline is
            driven. When ``True`` and no existing PM session is found, this calls
            :func:`tools.sdlc_session_ensure.ensure_session` to create (or dedup
            onto a live bridge session) a PM session, then re-resolves and
            returns it. Creation is gated: it only happens when
            ``issue_number >= 1`` OR a session-id env var is present — a bare
            sessionless write with no issue context still returns ``None`` (no
            fabricated session). The ensure path reuses ``ensure_session``'s
            idempotency, PM-type gating, terminal-status gating, and bridge
            dedup (#1147) verbatim; an ensure failure yields ``None`` rather
            than raising. The side effect is opt-in and grep-able via
            ``grep -rn 'ensure=True' tools/``.
        caller_run_id: The caller's ``--run-id``, threaded by the four write
            subcommands. **Cold-state run-identity gate (#2003 cycle-3):**
            when the pure lookup misses AND the caller carries a run_id, the
            auto-ensure branch is SKIPPED and ``None`` is returned. A run_id
            is minted only by ``ensure_session`` (which creates and binds the
            session record), so a run_id-carrying write that finds no session
            is stale by definition — ensuring here would mint a fresh session
            + issue lock as a side effect of a write that is about to be
            refused anyway, wedging the next legitimate ``session-ensure``
            behind ISSUE_LOCKED for up to the 300s TTL. Recovery is the
            documented one: re-run ``session-ensure`` (with ``--reuse-run-id``
            when the lock is still yours). Identity-less programmatic callers
            (``caller_run_id=None``) keep the #1558/#1671 auto-ensure
            behavior unchanged.

    Returns:
        The PM AgentSession or None.
    """
    # Step 1: explicit session_id argument wins over everything below.
    if session_id:
        try:
            sessions = list(AgentSession.query.filter(session_id=session_id))
            if sessions:
                for s in sessions:
                    if getattr(s, "session_type", None) == "eng":
                        return s
                return sessions[0]
        except Exception as e:
            logger.debug(f"find_session by explicit id failed: {e}")

    # Step 2: issue-based resolution BEFORE env-var resolution. An explicit
    # --issue-number N must override an inherited env-var session so the write
    # converges with the issue-number read path (#1671/#1672).
    if issue_number is not None and issue_number >= 1:
        try:
            found = find_session_by_issue(issue_number)
            if found is not None:
                return found
        except Exception as e:
            logger.debug(f"find_session_by_issue failed: {e}")

    # Step 3: env-var session is now a FALLBACK — only when no explicit id and
    # no issue match. Preserves the bridge no-issue-number case byte-for-byte.
    env_id = os.environ.get("VALOR_SESSION_ID") or os.environ.get("AGENT_SESSION_ID")
    if env_id:
        try:
            sessions = list(AgentSession.query.filter(session_id=env_id))
            if sessions:
                for s in sessions:
                    if getattr(s, "session_type", None) == "eng":
                        return s
                return sessions[0]
        except Exception as e:
            logger.debug(f"find_session by env id failed: {e}")

    # Opt-in auto-ensure (writes only). Create a session so the write has a home.
    # Gated: only when there is an issue context (issue_number >= 1) or a
    # session-id env var is present. Reads (ensure=False) never reach this branch.
    #
    # Cold-state run-identity gate (#2003 cycle-3): a run_id-carrying caller
    # that reaches this point has a claim no session record can corroborate —
    # never ensure-mint on its behalf (see the caller_run_id arg docstring).
    if ensure and caller_run_id:
        logger.debug(
            "find_session: cold-state write with run_id=%s and no resolvable "
            "session — refusing auto-ensure (re-run session-ensure to recover)",
            caller_run_id,
        )
        return None
    if ensure and (
        (issue_number is not None and issue_number >= 1)
        or os.environ.get("VALOR_SESSION_ID")
        or os.environ.get("AGENT_SESSION_ID")
    ):
        try:
            # Lazy import to avoid an import-time cycle (sdlc_session_ensure
            # imports from this module).
            from tools.sdlc_session_ensure import ensure_session

            result = ensure_session(issue_number) if issue_number is not None else {}
            ensured_id = result.get("session_id") if isinstance(result, dict) else None
            if ensured_id:
                # Re-resolve through the same id path so the returned object is a
                # live AgentSession, not the ensure-result dict.
                return find_session(session_id=ensured_id)
        except Exception as e:
            logger.debug(f"find_session auto-ensure failed: {e}")

    return None


def _parse_issue_number_from_url(issue_url: str | None) -> int | None:
    """Extract the GitHub issue number from an ``issue_url``.

    Mirrors ``tools.sdlc_dispatch._parse_issue_number_from_url`` (kept private
    there too -- this is a small enough regex that a shared import wasn't
    worth the coupling). Returns ``None`` if ``issue_url`` is falsy or does
    not contain an ``issues/N`` segment. Never raises.
    """
    if not issue_url:
        return None
    match = re.search(r"issues/(\d+)", issue_url)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def renew_issue_lock_for_session(session, run_id: str | None = None) -> None:
    """Renew the per-issue SDLC ownership lock as a side effect of a write.

    Shared helper (issues #1954/#2003) for SDLC CLI subcommands that fire
    during an in-progress BUILD/TEST/REVIEW stage and therefore have an
    established recurrence path to lean on for renewal -- currently wired
    into ``sdlc_stage_marker.write_marker()`` only. ``sdlc_dispatch``'s
    ``record`` subcommand does NOT call this helper: its underlying
    ``record_dispatch_for_session()`` already calls ``touch_issue_lock()``
    directly as part of its own contention-check-and-refuse logic, so wiring
    this helper there too would touch the same Redis key twice per call for
    no benefit.

    Run identity (issue #2003, cycle-2 BLOCKER): renewal compares by run_id,
    never by session_id or process identity. The identity is the caller's
    explicit ``run_id`` when given (the CLI's ``--run-id``), falling back to
    ``session.active_run_id`` -- the read-back of the identity this
    pipeline's own ``ensure_session()`` established, NOT foreign adoption.
    Without either, renewal is skipped: an identity-less caller must never
    extend (or mint) a lock.

    Derives the issue number from ``session.issue_number`` (the write-once
    mirror field set by ``ensure_session()``) when present, falling back to
    parsing ``session.issue_url`` for sessions that predate that field or
    were matched via the bridge issue_url/message_text passes.

    Best-effort and side-effect-only: never raises, returns nothing. A
    lock-touch failure (Redis hiccup, missing issue number) never blocks or
    alters the caller's write outcome.
    """
    if session is None:
        return

    issue_number = getattr(session, "issue_number", None) or _parse_issue_number_from_url(
        getattr(session, "issue_url", None)
    )
    if not issue_number:
        return

    effective_run_id = run_id or getattr(session, "active_run_id", None)
    if not effective_run_id:
        logger.debug(
            "renew_issue_lock_for_session: no run_id (explicit or active_run_id) -- "
            "skipping renewal for issue #%s",
            issue_number,
        )
        return

    try:
        from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

        sid = getattr(session, "session_id", None) or ""
        touch_issue_lock(issue_number, effective_run_id, session_id=sid, ttl=ISSUE_LOCK_TTL_SECONDS)
    except Exception as e:
        logger.debug(f"renew_issue_lock_for_session: touch_issue_lock failed (non-fatal): {e}")


def check_run_ownership(
    session, run_id: str | None, issue_number: int | None = None
) -> dict | None:
    """Refuse a state-mutating write when a FOREIGN run holds the issue lock.

    Shared ownership gate (issue #2003) for the mutating sdlc-tool
    subcommands that do not renew the lock themselves (``verdict record``,
    ``meta-set``, ``stage-marker``'s pre-write check). Peek-only: never
    acquires, renews, or mints -- renewal stays scoped to the established
    recurrence paths (#1954 scope-narrowing).

    Args:
        session: The resolved AgentSession (used to derive the issue number
            when ``issue_number`` is not given).
        run_id: The caller's explicit run identity (from ``--run-id``).
        issue_number: Optional explicit issue number override.

    Returns:
        ``None`` when the write may proceed (lock free, owned by this
        run_id, or no issue context). An ``ISSUE_LOCKED`` dict
        (``{"reason": "ISSUE_LOCKED", "owner_run_id": ...,
        "owner_session_id": ...}``) when a foreign run holds the lock.
        Never raises; errors fail open (``None``).
    """
    derived_issue = issue_number
    if not derived_issue and session is not None:
        derived_issue = getattr(session, "issue_number", None) or _parse_issue_number_from_url(
            getattr(session, "issue_url", None)
        )
    if not derived_issue:
        return None

    try:
        from models.session_lifecycle import touch_issue_lock

        sid = getattr(session, "session_id", None) or "" if session is not None else ""
        result = touch_issue_lock(derived_issue, run_id, session_id=sid, peek=True)
    except Exception as e:
        logger.warning(
            "check_run_ownership: peek failed for issue #%s (failing open; error class %s): %s",
            derived_issue,
            type(e).__name__,
            e,
        )
        return None

    if result.acquired:
        return None
    return {
        "reason": "ISSUE_LOCKED",
        "owner_run_id": result.owner_run_id,
        "owner_session_id": result.owner_session_id,
        "orphaned_lock": result.orphaned_lock,
    }


def session_owns_issue(session, issue_number) -> bool:
    """Return True iff the session owns the issue by one of the three predicates
    that find_session_by_issue resolves on. Never raises.

    The three predicates are checked in order (OR'd):
    1. session.issue_url endswith ``/issues/{issue_number}``
    2. session.session_id == ``sdlc-local-{issue_number}``
    3. session.message_text matches ``\\bissue\\s*#?\\s*{issue_number}\\b``
       (case-insensitive, same regex as find_session_by_issue)

    Returns False immediately if issue_number is falsy or session is None.
    Wrap the entire body in try/except so a bad attribute or unexpected session
    shape never propagates out — callers gate on the bool.
    """
    if not issue_number:
        return False
    if session is None:
        return False
    try:
        # Predicate 1: issue_url ownership
        issue_url = getattr(session, "issue_url", "") or ""
        if issue_url.endswith(f"/issues/{issue_number}"):
            return True

        # Predicate 2: deterministic sdlc-local-{N} id
        session_id = getattr(session, "session_id", "") or ""
        if session_id == f"sdlc-local-{issue_number}":
            return True

        # Predicate 3: message_text fallback — identical regex to find_session_by_issue
        message_text = getattr(session, "message_text", "") or ""
        if message_text and re.search(
            rf"\bissue\s*#?\s*{issue_number}\b", message_text, re.IGNORECASE
        ):
            return True

        return False
    except Exception:
        return False


def find_plan_path(issue_number: int) -> Path | None:
    """Locate the plan file tracking this issue.

    Walks ``docs/plans/`` and returns the first ``.md`` file referencing the
    issue, matching either the bare ``#{issue_number}`` or the tracking-URL
    forms (``issues/{issue_number}``). A trailing digit boundary prevents
    ``#1455`` from matching issue ``145``. Returns None if not found.

    Plans-directory resolution order (D1 — portability):

    1. ``SDLC_TARGET_REPO`` env var (explicit override wins — preserves
       backward-compatible cross-repo override semantics).
    2. Else the cwd's git working-tree root (``git rev-parse --show-toplevel``)
       so the pipeline finds plans in whatever repo it is invoked from.
    3. Else the ``__file__``-relative ``~/src/ai/docs/plans`` fallback.

    Each step falls through on failure (not a git repo, ``git`` missing) so a
    missing env var degrades to "correct" rather than "silently wrong".

    **Bare-#N fallback safety (CONCERN 3):** When resolution reached step 3
    (``__file__`` fallback — SDLC_TARGET_REPO unset and not inside any git
    repo), a bare-``#N`` textual match is suppressed and None is returned.
    A bare mention of an issue number in the ai-repo plans is likely a
    cross-reference or No-Gos entry referencing a foreign (target-repo) issue,
    not the plan that actually owns it.  The ``tracking:`` match is always
    authoritative and is returned immediately regardless of resolution path.
    """
    if not issue_number:
        return None

    # Track whether we fell all the way to the __file__ fallback.  A bare-#N
    # match from this path is likely a foreign cross-reference and must be
    # suppressed so the caller knows to trigger plan creation in the target repo.
    _is_ai_repo_fallback = False

    repo_root_env = os.environ.get("SDLC_TARGET_REPO")
    if repo_root_env:
        plans_dir = Path(repo_root_env) / "docs" / "plans"
    else:
        toplevel = _git_toplevel()
        if toplevel is not None:
            plans_dir = toplevel / "docs" / "plans"
        else:
            # Resolution fell back to the ai-repo __file__ path.  Flag this so
            # the bare-#N fallback can be suppressed below.
            _is_ai_repo_fallback = True
            plans_dir = Path(__file__).resolve().parent.parent / "docs" / "plans"

    if not plans_dir.is_dir():
        return None

    # Match `#145`, `issues/145`, and the full tracking URL, but NOT `#1455`
    # (the trailing non-digit lookahead enforces the boundary).
    ref_re = re.compile(rf"(?:#|issues/){issue_number}(?![0-9])")
    # A `tracking:` frontmatter line pointing at this issue is the AUTHORITATIVE
    # owner — a plan that merely *mentions* `#{issue}` (e.g. an out-of-scope
    # cross-reference in another plan's No-Gos) must never win over the plan that
    # actually tracks the issue. Prefer a tracking-field match; fall back to any
    # textual reference only when no plan claims ownership.
    tracking_re = re.compile(rf"^tracking:.*(?:#|issues/){issue_number}(?![0-9])", re.MULTILINE)
    fallback: Path | None = None
    try:
        for entry in plans_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".md":
                continue
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if tracking_re.search(text):
                # tracking: match is authoritative regardless of resolution path.
                return entry
            if fallback is None and ref_re.search(text):
                fallback = entry
    except Exception as e:
        logger.debug(f"find_plan_path walk failed: {e}")

    # When plan resolution fell back to the ai-repo __file__ path
    # (SDLC_TARGET_REPO unset, not in a git repo), a bare-#N textual match is
    # likely a foreign plan that merely mentions the issue — return None to
    # force re-planning in the target repo.
    return None if (_is_ai_repo_fallback and fallback is not None) else fallback
