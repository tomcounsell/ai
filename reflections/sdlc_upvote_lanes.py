"""reflections/sdlc_upvote_lanes.py — Autonomous SDLC pickup on `upvote` issues.

Start-half sibling to `reflections/sdlc_progress.py`'s recovery-half: this
reflection starts lanes that do not exist yet, rather than unsticking lanes
that do. Per project, per tick: pick the single oldest open `upvote`-labeled
issue that has no live/started/recorded lane, announce the pickup in that
project's `Eng: X` Telegram group, capture the announcement's message id via
the relay ack (`bridge/outbox_ack.py`), and create an Eng session anchored to
it (`create_session(telegram_message_id=...)`) so every subsequent message
threads under the announcement.

Invariants (also asserted by anti-criteria in the tracking plan):

- **No claim key.** No Redis key, file, or record marks an issue as
  "claimed" ahead of time. Every start/skip decision derives from artifacts
  that exist for their own reasons (a non-terminal `AgentSession`, a
  `PipelineLedger` entry, the issue lock, an existing PR). The one Redis key
  this module writes (`upvote:pickup:failed:{repo}:{N}`) is a clock-expiring
  failure *backoff*, written only on observed failure, never a claim.
- **No label mutation.** `upvote` is a human-owned signal in both
  directions; this module never adds or removes it.
- **No stage selection.** `/sdlc` is a single-stage router that assesses
  state and dispatches one sub-skill; this reflection never chooses between
  PLAN and BUILD. It only decides start-or-skip.

Worst-case per-tick time budget: the deadline-and-early-return machinery in
`run_sdlc_upvote_lanes()` bounds the whole run to roughly
`UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S` (one `gh` call may straddle the
final deadline check), which is held strictly below `UPVOTE_ENTRY_TIMEOUT_S`
so the scheduler's own thread-timeout never fires against a still-running
tick (the reflection worker's `_reflection_pool` cannot cancel a wedged sync
callable — nothing here can be interrupted mid-flight, only refused before
it starts). A single pickup is uninterruptible and can cost up to
`UPVOTE_PICKUP_WORST_CASE_S` (dominated by `create_session`'s cold-worktree
`uv sync`, `settings.timeouts.uv_sync_s`), so a pickup is admitted only when
the remaining run budget covers that whole worst case
(`UPVOTE_PICKUP_WORST_CASE_S < UPVOTE_RUN_BUDGET_S`, asserted by test). Gate
1.5's backoff key uses `SETEX`/`GET` on a plain, non-Popoto Redis string
namespace (`upvote:pickup:failed:{repo}:{N}`) — not the raw-Redis-on-Popoto-
keys rule this repo otherwise enforces, and not the "no claim key" invariant
above (it is written only on failure, asserts no ownership, and requires no
release step).
"""

from __future__ import annotations

import functools
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from config.settings import settings
from reflections.pm_briefings.collector import _gh_issue_list, _project_repo
from reflections.utilities import (
    _get_redis,
    _lock_says_live,
    machine_owns_project,
    resolve_eng_group,
    run_per_project_audit,
)
from tools.lane_identity import lane_branch_name, mint_lane_slug, resolve_lane_slug

logger = logging.getLogger("reflections.sdlc_upvote_lanes")

# ---------------------------------------------------------------------------
# Env-overridable, provisional constants. Every default below is a starting
# guess, not a measured optimum (grain of salt) -- tune via the matching
# TIMEOUTS__/UPVOTE_* env var, never by editing the literal in place.
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# Per-project ceiling on concurrently live auto-started lanes. Provisional:
# sized so a single project cannot monopolize the worker, not a measured
# optimum. Env: UPVOTE_LANE_MAX_LIVE.
UPVOTE_LANE_MAX_LIVE = _env_int("UPVOTE_LANE_MAX_LIVE", 3)

# Machine-wide companion ceiling, evaluated across all projects in one
# sweep. Provisional: sized so the machine-wide count cannot exceed the
# project count by much. Env: UPVOTE_LANE_MAX_LIVE_MACHINE.
UPVOTE_LANE_MAX_LIVE_MACHINE = _env_int("UPVOTE_LANE_MAX_LIVE_MACHINE", 5)

# The single candidate-truncation knob -- passed directly as _gh_issue_list's
# `limit`. There is no second post-sort slice: the sort is server-side
# oldest-first, so a second slice would cut from the same end and only the
# smaller of the two would ever bind. Provisional. Env:
# UPVOTE_CANDIDATE_SCAN_MAX.
UPVOTE_CANDIDATE_SCAN_MAX = _env_int("UPVOTE_CANDIDATE_SCAN_MAX", 10)

# Bounded wait for the relay ack after an announcement. Provisional. Env:
# UPVOTE_ANCHOR_WAIT_S.
UPVOTE_ANCHOR_WAIT_S = _env_float("UPVOTE_ANCHOR_WAIT_S", 20.0)
_ANCHOR_POLL_INTERVAL_S = 0.25

# How long a self-observed create-failure suppresses re-announcing the same
# issue. Provisional. Env: UPVOTE_FAILURE_BACKOFF_S.
UPVOTE_FAILURE_BACKOFF_S = _env_int("UPVOTE_FAILURE_BACKOFF_S", 3600)

# Per-subprocess-call timeout (gh CLI calls and the valor-telegram send
# calls, the latter of which also covers a promise-gate LLM round-trip).
# Provisional. Env: UPVOTE_GH_TIMEOUT_S.
UPVOTE_GH_TIMEOUT_S = _env_int("UPVOTE_GH_TIMEOUT_S", 30)

# Wall-clock deadline for the whole run, captured once at the top of
# run_sdlc_upvote_lanes(). Provisional. Env: UPVOTE_RUN_BUDGET_S.
UPVOTE_RUN_BUDGET_S = _env_int("UPVOTE_RUN_BUDGET_S", 1200)

# The registered reflection-entry timeout (see
# scripts/update/reflection_register.py::register_sdlc_upvote_pickup, which
# imports this rather than repeating the literal). Must stay above
# UPVOTE_RUN_BUDGET_S + UPVOTE_GH_TIMEOUT_S so the scheduler's own timeout
# never fires against a tick that is still legitimately running.
UPVOTE_ENTRY_TIMEOUT_S = 1500

# Derived, never fresh literals -- a TIMEOUTS__UV_SYNC_S override must not
# silently invalidate this arithmetic. create_session's cold-worktree `uv
# sync` is the dominant term: every pickup is a cold worktree (the lane slug
# is unique per issue, so get_or_create_worktree never reuses one).
UPVOTE_CREATE_WORST_CASE_S = settings.timeouts.uv_sync_s + settings.timeouts.git_subprocess_s
UPVOTE_PICKUP_WORST_CASE_S = (
    2 * UPVOTE_GH_TIMEOUT_S + UPVOTE_ANCHOR_WAIT_S + UPVOTE_CREATE_WORST_CASE_S
)

_FAILED_KEY_TEMPLATE = "upvote:pickup:failed:{repo}:{issue_number}"


# ---------------------------------------------------------------------------
# Run-scoped state
# ---------------------------------------------------------------------------


@dataclass
class _RunState:
    """Carries the wall-clock deadline and the machine-wide live-lane
    accumulator across the whole sweep. `run_per_project_audit` owns the
    per-project loop, so a per-project callable can neither `break` nor
    abort by raising (each project is wrapped in its own try/except) --
    budget enforcement is therefore an early return, and this object is how
    that early return stays consistent across projects."""

    deadline: float
    machine_live_total: int = 0


def _budget_remaining(state: _RunState) -> float:
    return state.deadline - time.monotonic()


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _scrubbed_env() -> dict[str, str]:
    """Explicit env for the send subprocess -- never inherited wholesale.

    An inherited VALOR_SESSION_ID arms Read-the-Room (whose `suppress`
    verdict enqueues a reaction instead of the message and still exits 0);
    an inherited TELEGRAM_REPLY_TO threads the announcement under an
    unrelated message in a different chat; AGENT_SESSION_ID misattributes
    ownership. All three are stripped.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("VALOR_SESSION_ID", "TELEGRAM_REPLY_TO", "AGENT_SESSION_ID")
    }


def _run_send(argv: list[str]) -> int:
    """Run a `python -m tools.valor_telegram send` invocation. Returns exit code.

    Interpreter-pinned via sys.executable -- never the bare `valor-telegram`
    console script, whose PATH shim is stale on this machine (issue #2566)
    and crashes on import outside the launchd-pinned PATH. TimeoutExpired is
    caught explicitly (it is not a CalledProcessError subclass) and treated
    like a non-zero exit.
    """
    full_argv = [sys.executable, "-m", "tools.valor_telegram", *argv]
    try:
        proc = subprocess.run(
            full_argv,
            capture_output=True,
            text=True,
            timeout=UPVOTE_GH_TIMEOUT_S,
            env=_scrubbed_env(),
        )
        if proc.returncode != 0:
            logger.warning(
                "sdlc_upvote_lanes: send failed rc=%s stderr=%s",
                proc.returncode,
                (proc.stderr or "")[:300],
            )
        return proc.returncode
    except subprocess.TimeoutExpired:
        logger.warning("sdlc_upvote_lanes: send timed out after %ss", UPVOTE_GH_TIMEOUT_S)
        return 124
    except Exception as exc:  # noqa: BLE001
        logger.warning("sdlc_upvote_lanes: send raised: %s", exc)
        return 1


def _announce(chat_id: int, producer_id: str, text: str) -> int:
    return _run_send(
        [
            "send",
            "--chat",
            str(chat_id),
            "--session-id",
            producer_id,
            "--ack-sent-id",
            "--no-read-the-room",
            text,
        ]
    )


def _retract(chat_id: int, producer_id: str, anchor: int | None, text: str) -> int:
    """Send the threaded retraction. `anchor` may be falsy (0 or None) when the
    ack never arrived -- splice --reply-to in only when truthy, and use a
    distinct '-retract' producer id so this send's own outbox/ack keys never
    cross-talk with the announcement's (Race 3)."""
    argv = [
        "send",
        "--chat",
        str(chat_id),
        "--session-id",
        f"{producer_id}-retract",
        "--no-read-the-room",
        text,
    ]
    if anchor:
        argv[-1:-1] = ["--reply-to", str(anchor)]
    return _run_send(argv)


# ---------------------------------------------------------------------------
# Skip gates (§C)
# ---------------------------------------------------------------------------


def _non_terminal_session_for(slug: str, project_key: str):
    """Return the first non-terminal AgentSession matching slug+project_key,
    or None. Also returns whether a non-terminal row exists for a *different*
    project_key (a cross-project slug collision -- reported, not skipped)."""
    from models.agent_session import AgentSession
    from models.session_lifecycle import NON_TERMINAL_STATUSES

    own_match = None
    cross_project_match = False
    for row in AgentSession.query.filter(slug=slug):
        status = getattr(row, "status", None)
        if status not in NON_TERMINAL_STATUSES:
            continue
        if getattr(row, "project_key", None) == project_key:
            own_match = row
        else:
            cross_project_match = True
    return own_match, cross_project_match


def _recent_terminal_failed_session(slug: str, project_key: str, backoff_s: int):
    from datetime import UTC, datetime, timedelta

    from bridge.utc import to_unix_ts
    from models.agent_session import AgentSession

    cutoff_ts = (datetime.now(tz=UTC) - timedelta(seconds=backoff_s)).timestamp()
    for row in AgentSession.query.filter(slug=slug):
        if getattr(row, "project_key", None) != project_key:
            continue
        if getattr(row, "status", None) != "failed":
            continue
        created_ts = to_unix_ts(getattr(row, "created_at", None))
        if created_ts is not None and created_ts >= cutoff_ts:
            return row
    return None


def _failed_backoff_active(repo: str, issue_number: int) -> bool:
    key = _FAILED_KEY_TEMPLATE.format(repo=repo, issue_number=issue_number)
    try:
        return _get_redis().get(key) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("sdlc_upvote_lanes: backoff read failed for %s: %s", key, exc)
        return False


def _set_failed_backoff(repo: str, issue_number: int, error: str) -> None:
    key = _FAILED_KEY_TEMPLATE.format(repo=repo, issue_number=issue_number)
    try:
        _get_redis().setex(key, UPVOTE_FAILURE_BACKOFF_S, str(error)[:500])
    except Exception as exc:  # noqa: BLE001
        logger.warning("sdlc_upvote_lanes: backoff write failed for %s: %s", key, exc)


def _ledger_has_recorded_stage(repo: str, issue_number: int) -> bool:
    try:
        from agent.pipeline_ledger import PipelineLedger

        ledger = PipelineLedger.get(repo, issue_number)
        if ledger is None:
            return False
        raw = getattr(ledger, "stage_states_json", None)
        return raw not in (None, "", "{}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sdlc_upvote_lanes: ledger read failed for %s#%s: %s", repo, issue_number, exc
        )
        return False


def _has_pr_on_branch(repo: str, issue_number: int, cwd: str) -> dict | None:
    """Returns the first PR (open or merged) on the lane's branch, or None.

    The branch is the recorded lane slug when one exists, and the issue-derived
    name otherwise. This site SEARCHES, so it may guess: probing costs one `gh`
    call and records nothing, so a wrong guess is free. It must NOT skip on an
    unresolved slug -- it is called after gate 2 has already excluded every
    candidate with a recorded stage, so every candidate reaching here has no
    recorded slug, and skipping would disable gate 4 entirely.

    --state all deliberately: an OPEN pr means sdlc_progress owns the lane;
    a MERGED pr on an issue that's still open means the implementation PR
    lacked `Closes #N` and this gate must stop the lane from restarting.
    """
    head = lane_branch_name(
        resolve_lane_slug(issue_number, target_repo=repo) or mint_lane_slug(issue_number)
    )
    if head is None:
        return None
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                head,
                "--state",
                "all",
                "--json",
                "number,state,url",
            ],
            capture_output=True,
            text=True,
            timeout=UPVOTE_GH_TIMEOUT_S,
            cwd=cwd,
        )
        if proc.returncode != 0:
            return None
        import json as _json

        prs = _json.loads(proc.stdout or "[]")
        return prs[0] if prs else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sdlc_upvote_lanes: pr-branch check failed for %s#%s: %s", repo, issue_number, exc
        )
        return None


def _count_live_lanes(repo: str, cwd: str) -> int:
    """Approximate live-lane count: open session/sdlc-* PRs in this repo.

    Deliberately an undercount (a human-started lane with no PR yet, on a
    non-upvote issue, is invisible) -- this is a ramp brake, not admission
    control, and an exact count would mean a full non-terminal AgentSession
    scan per project per tick.
    """
    import json as _json

    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--json",
                "number,headRefName",
                "--limit",
                "100",
            ],
            capture_output=True,
            text=True,
            timeout=UPVOTE_GH_TIMEOUT_S,
            cwd=cwd,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("sdlc_upvote_lanes: live-lane count failed for %s: %s", repo, exc)
        return 0
    if proc.returncode != 0:
        return 0
    try:
        prs = _json.loads(proc.stdout or "[]")
    except _json.JSONDecodeError:
        return 0
    return sum(
        1
        for pr in prs
        if isinstance(pr, dict) and str(pr.get("headRefName", "")).startswith("session/sdlc-")
    )


# ---------------------------------------------------------------------------
# Per-project audit body
# ---------------------------------------------------------------------------


def _pick_up_upvoted(project: dict, *, state: _RunState) -> dict:
    project_key = project.get("slug", "?")
    findings: list[str] = []

    if _budget_remaining(state) <= 0:
        return {"status": "ok", "findings": ["budget exhausted; project not scanned"]}

    if not machine_owns_project(project_key):
        return {"status": "skipped", "findings": []}

    eng_group = resolve_eng_group(project)
    if eng_group is None:
        # NOTE: "ok" (not "skipped") -- utilities.py:207 discards findings on
        # "skipped" status, and this is a real config gap the operator needs
        # to see in the aggregated report, not a legitimate no-op like the
        # machine-ownership check above.
        return {"status": "ok", "findings": ["no Eng: group configured"]}
    eng_group_name, eng_chat_id = eng_group

    repo = _project_repo(project)
    if repo is None:
        # NOTE: same rationale as the Eng: group check above -- "ok" so the
        # finding survives aggregation instead of being silently dropped.
        return {"status": "ok", "findings": ["no github.org/repo configured"]}

    cwd = project.get("working_directory", ".")

    # Concurrency ceiling (§B): per-project and machine-wide, both counted
    # from the same one `gh pr list` call -- zero extra subprocess calls for
    # the machine-wide half.
    live_count = _count_live_lanes(repo, cwd)
    if live_count >= UPVOTE_LANE_MAX_LIVE:
        findings.append(f"lane ceiling reached ({live_count}/{UPVOTE_LANE_MAX_LIVE})")
        state.machine_live_total += live_count
        return {"status": "ok", "findings": findings}
    if state.machine_live_total + live_count >= UPVOTE_LANE_MAX_LIVE_MACHINE:
        findings.append(
            f"machine-wide lane ceiling reached "
            f"({state.machine_live_total + live_count}/{UPVOTE_LANE_MAX_LIVE_MACHINE})"
        )
        state.machine_live_total += live_count
        return {"status": "ok", "findings": findings}
    state.machine_live_total += live_count

    if _budget_remaining(state) <= 0:
        return {
            "status": "ok",
            "findings": findings + ["budget exhausted; project not scanned"],
        }

    candidates = _gh_issue_list(
        repo,
        ["upvote"],
        cwd=cwd,
        limit=UPVOTE_CANDIDATE_SCAN_MAX,
        extra_args=["--search", "sort:created-asc"],
        timeout=UPVOTE_GH_TIMEOUT_S,
    )
    # Deterministic tie-break stabilizer over the already-correct
    # server-side-sorted page -- not the ordering mechanism itself (§A).
    candidates = sorted(candidates, key=lambda c: (c.get("createdAt") or "", c.get("number") or 0))

    for candidate in candidates:
        if _budget_remaining(state) <= 0:
            findings.append("budget exhausted; project not scanned")
            break

        issue_number = candidate.get("number")
        if not isinstance(issue_number, int):
            continue
        # Resolve ONCE, heal OFF. Every gate below looks sessions up by this
        # name, and none of them may record one: this is still a *scan*, and
        # the reflection declines most candidates seconds later. Healing here
        # would permanently fix an identity (the write is no-overwrite) for an
        # issue no lane ever starts. The heal moves past every gate, to the
        # point the reflection has actually committed to a pickup.
        slug = resolve_lane_slug(issue_number, target_repo=repo) or mint_lane_slug(issue_number)
        title = candidate.get("title") or ""
        url = candidate.get("url") or ""

        own_session, cross_project_collision = _non_terminal_session_for(slug, project_key)
        if cross_project_collision:
            findings.append(
                f"cross-project slug collision on {slug} "
                f"(another project's lane holds it) -- proceeding anyway"
            )
        if own_session is not None:
            continue  # gate 1

        if _failed_backoff_active(repo, issue_number):
            continue  # gate 1.5

        if _recent_terminal_failed_session(slug, project_key, UPVOTE_FAILURE_BACKOFF_S):
            continue  # gate 1.6

        if _ledger_has_recorded_stage(repo, issue_number):
            continue  # gate 2

        live = _lock_says_live(issue_number)
        if live is True or live is None:
            continue  # gate 3 -- fail closed on unknown

        pr = _has_pr_on_branch(repo, issue_number, cwd)
        if pr is not None:
            if pr.get("state") == "MERGED":
                findings.append(
                    f"issue #{issue_number} has a merged PR "
                    f"(#{pr.get('number')}) but is still open -- likely missing 'Closes #N'"
                )
            continue  # gate 4

        # Admission check (§B) -- before announcing, because create_session
        # is uninterruptible once it starts.
        if _budget_remaining(state) < UPVOTE_PICKUP_WORST_CASE_S:
            findings.append(
                f"insufficient run budget for a pickup; "
                f"deferring issue #{issue_number} to the next tick"
            )
            break

        producer_id = f"upvote-{project_key}-{issue_number}-{int(time.time())}"
        announce_text = f"Picking up issue #{issue_number} for SDLC: {title}\n{url}"
        rc = _announce(eng_chat_id, producer_id, announce_text)
        if rc != 0:
            findings.append(
                f"announcement send failed (rc={rc}) for issue #{issue_number}; started nothing"
            )
            continue

        from bridge.outbox_ack import await_sent_message_id

        anchor = await_sent_message_id(producer_id, UPVOTE_ANCHOR_WAIT_S)
        unconfirmed_delivery = anchor is None
        if unconfirmed_delivery:
            anchor = 0
            findings.append(
                f"issue #{issue_number}: delivery unconfirmed "
                f"(no ack within {UPVOTE_ANCHOR_WAIT_S}s) -- starting unanchored"
            )

        # Race 1: re-read gates 1 and 3 immediately before create.
        own_session, _ = _non_terminal_session_for(slug, project_key)
        live_recheck = _lock_says_live(issue_number)
        if own_session is not None or live_recheck is True or live_recheck is None:
            _retract(
                eng_chat_id,
                producer_id,
                anchor,
                f"Issue #{issue_number} was picked up by another lane just now.",
            )
            findings.append(
                f"issue #{issue_number}: another lane started during the anchor wait (benign)"
            )
            continue

        # Past every gate and past the Race-1 re-check: this is the first point
        # at which the reflection has committed to starting a lane, so it is
        # the first point at which recording an identity is correct. The result
        # is deliberately NOT reassigned to `slug` -- rung 1 returns what the
        # probe above already read, and on a miss the heal mints the same
        # issue-derived name the probe fell back to, so they agree by
        # construction. Reassigning would risk gates 1/1.6 and the Race-1
        # re-check having tested one name while the session is created under
        # another, silently losing duplicate-lane protection.
        resolve_lane_slug(issue_number, allow_heal=True, target_repo=repo)

        session_message = (
            f"Run the next SDLC stage for issue #{issue_number}: {title}. This issue is "
            "labeled `upvote` (pre-approved for autonomous pickup). Invoke `/sdlc` and let "
            "the router choose the stage."
        )

        from tools.valor_session import create_session

        try:
            result = create_session(
                message=session_message,
                role="eng",
                slug=slug,
                project_key=project_key,
                session_type="eng",
                chat_id=str(eng_chat_id),
                telegram_message_id=anchor,
            )
            create_success = bool(getattr(result, "success", False))
            create_error = getattr(result, "error", None)
        except Exception as exc:  # noqa: BLE001
            create_success = False
            create_error = f"create raised: {exc}"

        if not create_success:
            _set_failed_backoff(repo, issue_number, create_error or "unknown error")
            _retract(
                eng_chat_id,
                producer_id,
                anchor,
                f"Could not start the SDLC lane for issue #{issue_number}: "
                f"{create_error}. Retrying on the next tick.",
            )
            findings.append(f"create_session failed for issue #{issue_number}: {create_error}")
            continue

        findings.append(f"started SDLC lane for issue #{issue_number}: {title}")
        state.machine_live_total += 1
        break  # one pickup per project per tick

    return {"status": "ok", "findings": findings}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_sdlc_upvote_lanes() -> dict:
    """Scheduled entry point (reflections/utilities.run_per_project_audit owns
    the loop). See the module docstring for the invariants and the worst-case
    per-tick budget."""
    raw = os.environ.get("SDLC_UPVOTE_PICKUP_ENABLED")
    if raw is not None and raw.strip().lower() in {"0", "false", "no", "off"}:
        return {"status": "disabled", "findings": [], "summary": "sdlc-upvote-pickup: disabled"}

    state = _RunState(deadline=time.monotonic() + UPVOTE_RUN_BUDGET_S)
    result = run_per_project_audit(
        functools.partial(_pick_up_upvoted, state=state), name="sdlc-upvote-pickup"
    )
    return result
