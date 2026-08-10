"""Read-only progress aggregation for a single ``AgentSession`` (issue #2663).

Answers one question — *is session X still working?* — from signals that
already exist, and answers it **truthfully rather than confidently**.

Why this module exists
----------------------
On 2026-08-07 a healthy session was misdiagnosed as deadlocked by hand
forensics across ``ps``, ``lsof``, the CLI transcript, and background-task
output files. It was working the whole time and went on to open a 14-file
PR. The bogus hang report (#2662) was closed invalid. Every hand-read
signal in that investigation was individually misleading:

===========================  ===============================================
Signal                       Failure mode
===========================  ===============================================
instantaneous ``%CPU``       0.0% when sampled between subprocess bursts
child-process count          0 in the gaps between ``Bash`` calls
MCP server idleness          true but irrelevant when the subagent uses Bash
parent transcript silence    the EXPECTED shape of a long synchronous
                             ``Agent`` call — subagent steps are not written
                             as sidechain entries until the call returns
===========================  ===============================================

``%CPU`` and child count are therefore **deliberately absent** from this
module. They are not merely unused; collecting them would invite the exact
inference that produced the misdiagnosis.

The signal that was accurate the whole time —
:func:`agent.session_runner.liveness.tool_activity_ts` — is the load-bearing
input here. It is written by the runner's ``matcher: ""`` ``PreToolUse``
hook, ticks on tool calls made from inside an in-process subagent, and works
in foreign repos that do not carry this repo's ``.claude/hooks``. Until this
module existed its only consumer was the watchdog's ``_session_progress_ts``.

Truthfulness contract
---------------------
1. **Absence of evidence yields UNKNOWN, never a false "wedged".** A missing
   marker directory, an absent transcript, an unreadable task dir, or a dead
   pid each read as "no signal" — they never manufacture a negative verdict.
2. **Only positive evidence can produce PROGRESSING.** Every verdict input is
   a timestamp proving something happened, never the absence of one.
3. **Nothing here mutates.** No ``save()``, no steering, no kill. Read-only
   by construction, so any agent may call it against any session.
4. **Never raises.** Every collector is individually fail-silent; a broken
   collector degrades that one signal to ``None`` and the rest still report.

Verdict vocabulary
------------------
``PROGRESSING``
    At least one liveness signal is fresher than the window.
``NO RECENT ACTIVITY``
    Liveness signals exist but all of them are older than the window. This
    is deliberately NOT called "wedged" or "stuck": a long ``Bash`` call
    fires its ``PreToolUse`` hook once at the START, so a genuinely working
    session running a 25-minute test suite has a stale marker throughout.
``UNKNOWN``
    No liveness evidence at all, or the session is in a terminal state so
    "still working" is not a question with a progress answer.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Verdict constants. Callers compare against these rather than string
# literals so a rename cannot silently split the contract.
VERDICT_PROGRESSING = "PROGRESSING"
VERDICT_NO_RECENT_ACTIVITY = "NO RECENT ACTIVITY"
VERDICT_UNKNOWN = "UNKNOWN"

# Statuses from which "is it still working?" has no progress answer. Sourced
# lazily from models.session_lifecycle so the vocabulary stays single-defined.
_TERMINAL_FALLBACK = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})

# Fallback freshness window, in seconds, when the watchdog's own constant
# cannot be imported. See :func:`default_window_s`.
_WINDOW_FALLBACK_S = 1800

# How far ahead of our clock a marker may be dated and still be believed.
# Writers and readers are the same host today, so this only absorbs ordinary
# jitter; anything beyond it is skew or corruption. See :meth:`Signal.age_s`.
FUTURE_TS_TOLERANCE_S = 60.0


def terminal_statuses() -> frozenset[str]:
    """Terminal session statuses, from the lifecycle module when importable."""
    try:
        from models.session_lifecycle import TERMINAL_STATUSES  # noqa: PLC0415

        return frozenset(TERMINAL_STATUSES)
    except Exception:
        return _TERMINAL_FALLBACK


def default_window_s() -> int:
    """Freshness window in seconds — the watchdog's own progress deadline.

    Deliberately borrowed from ``SESSION_PROGRESS_DEADLINE_S`` rather than
    picked independently. A shorter window would let this CLI report "no
    recent activity" for a session the running system still considers to be
    progressing, and that disagreement is precisely how #2662 was
    manufactured: a human picked a threshold tighter than the one the
    machinery uses and read a normal quiet gap as a hang.

    Operators who want a tighter read pass ``--window`` explicitly and own
    the interpretation.
    """
    try:
        from agent.agent_session_queue import SESSION_PROGRESS_DEADLINE_S  # noqa: PLC0415

        return int(SESSION_PROGRESS_DEADLINE_S)
    except Exception:
        try:
            return int(os.environ.get("SESSION_PROGRESS_DEADLINE_S", _WINDOW_FALLBACK_S))
        except (TypeError, ValueError):
            return _WINDOW_FALLBACK_S


@dataclass(frozen=True)
class Signal:
    """One observed timestamp, or the recorded absence of one.

    ``ts is None`` means "we looked and found nothing", which is the only
    honest reading of a missing marker file. It is never coerced to 0 or to
    "now"; either coercion would turn silence into a claim.
    """

    name: str
    ts: float | None
    detail: str | None = None
    #: Whether this signal may drive the verdict. Context-only signals are
    #: reported for the reader but never make a verdict more or less certain.
    counts_as_evidence: bool = True

    def age_s(self, now: float) -> float | None:
        """Seconds since this signal fired, or ``None`` when absent or impossible.

        A timestamp meaningfully in the future is not evidence of anything. It
        means clock skew or a corrupt marker, and treating it as age ``0.0``
        would pin the verdict to ``PROGRESSING`` forever — the most confident
        possible answer drawn from the least trustworthy possible input, which
        is exactly the posture this module exists to avoid. Reported as absent
        instead, so it reads as ``UNKNOWN``.

        Sub-``FUTURE_TS_TOLERANCE_S`` overshoot is ordinary jitter between a
        writer's clock and ours, and still clamps to ``0.0``.
        """
        if self.ts is None:
            return None
        delta = now - self.ts
        if delta < -FUTURE_TS_TOLERANCE_S:
            return None
        return max(0.0, delta)

    def is_implausible(self, now: float) -> bool:
        """True when a timestamp exists but is too far in the future to trust."""
        return self.ts is not None and (now - self.ts) < -FUTURE_TS_TOLERANCE_S


def _as_unix_ts(val) -> float | None:
    """Coerce datetime / int / float / ISO-string to a Unix timestamp.

    Delegates to :func:`agent.session_runner.liveness._as_unix_ts` so the
    naive-datetime-is-UTC convention (Popoto strips tzinfo on save) has one
    definition. Falls back to a local implementation only if that import
    fails, so this module stays usable standalone.
    """
    try:
        from agent.session_runner.liveness import _as_unix_ts as _impl  # noqa: PLC0415

        return _impl(val)
    except Exception as exc:
        logger.debug("session_progress: liveness._as_unix_ts unavailable (%s)", exc)
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).timestamp()
    return None


# ---------------------------------------------------------------------------
# Collectors. Each returns a Signal (or list of artifacts) and never raises.
# ---------------------------------------------------------------------------


def tool_activity_signal(session_id: str | None, *, hook_edge_root: str | None = None) -> Signal:
    """The load-bearing signal: freshest runner hook-edge tool-activity marker.

    Reads ``<hook-edge-dir>/<session_id>/*.toolactivity``, rewritten on every
    tool call including tool calls made from inside an in-process subagent.
    This is what read 0.0s age throughout the supposed #2662 deadlock.

    ``hook_edge_root`` overrides the marker base directory for tests. When
    omitted the production resolver is used; if even that fails the signal
    degrades to absent rather than raising.
    """
    if not session_id:
        return Signal("tool_activity", None, detail="no session_id")

    if hook_edge_root is None:
        try:
            from agent.session_runner.liveness import tool_activity_ts  # noqa: PLC0415

            ts = tool_activity_ts(session_id)
        except Exception as exc:
            # Loud, not silent. This is the load-bearing signal; degrading it
            # to absent is correct behaviour but must never be invisible, or a
            # break in tool_activity_ts costs the tool its headline reading
            # while every caller still reads a confident UNKNOWN.
            logger.warning(
                "session_progress: tool_activity_ts unavailable (%s: %s) — "
                "the load-bearing liveness signal is degraded to absent",
                type(exc).__name__,
                exc,
            )
            return Signal(
                "tool_activity",
                None,
                detail=f"unavailable: {type(exc).__name__}",
            )
        return Signal("tool_activity", ts)

    # Test/override path: read the markers directly under the given root.
    try:
        suffix = ".toolactivity"
        try:
            from agent.session_runner.hook_edge import TOOL_ACTIVITY_SUFFIX  # noqa: PLC0415

            suffix = TOOL_ACTIVITY_SUFFIX
        except Exception as exc:
            logger.debug("session_progress: marker suffix import failed (%s)", exc)
        session_dir = pathlib.Path(hook_edge_root) / str(session_id)
        stamps: list[float] = []
        for marker in session_dir.glob(f"*{suffix}"):
            try:
                stamps.append(float(marker.read_text().strip()))
            except (OSError, ValueError):
                continue
        return Signal("tool_activity", max(stamps) if stamps else None)
    except Exception:
        return Signal("tool_activity", None)


def _task_output_roots(explicit: list[str] | None = None) -> list[pathlib.Path]:
    """Candidate roots holding ``claude-<uid>/`` background-task trees.

    The Claude CLI writes these under ``/tmp`` on macOS even though
    ``$TMPDIR`` points into ``/var/folders``, so both are probed. Order is
    irrelevant — every candidate is globbed and the newest mtime wins.
    """
    if explicit is not None:
        return [pathlib.Path(p) for p in explicit]
    roots: list[pathlib.Path] = []
    for candidate in (os.environ.get("TMPDIR"), "/tmp"):
        if not candidate:
            continue
        path = pathlib.Path(candidate)
        if path not in roots:
            roots.append(path)
    return roots


def task_output_signal(
    claude_session_uuid: str | None,
    *,
    roots: list[str] | None = None,
) -> Signal:
    """Newest background-task output file for the claude session, by mtime.

    Path shape: ``<root>/claude-<uid>/<escaped-cwd>/<uuid>/tasks/<id>.output``.
    Globbed rather than reconstructed so the cwd-escaping rules and the uid
    segment stay someone else's problem.

    A background task that has produced no output yet, a session with no
    background tasks, and an unreadable task directory are all indistinguishable
    from here and all read as absent — which is correct, because none of them
    is evidence of a hang.
    """
    if not claude_session_uuid:
        return Signal("task_output", None, detail="no claude_session_uuid")
    newest_ts: float | None = None
    newest_path: str | None = None
    try:
        for root in _task_output_roots(roots):
            try:
                matches = root.glob(f"claude-*/*/{claude_session_uuid}/tasks/*.output")
            except Exception as exc:
                logger.debug("session_progress: task-output glob failed under %s (%s)", root, exc)
                continue
            for path in matches:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if newest_ts is None or mtime > newest_ts:
                    newest_ts, newest_path = mtime, str(path)
    except Exception:
        return Signal("task_output", None)
    return Signal("task_output", newest_ts, detail=newest_path)


def transcript_path(
    claude_session_uuid: str | None,
    runner_cwd: str | None,
    *,
    projects_root: str | None = None,
) -> str | None:
    """Resolve the CLI transcript JSONL for a claude session, or ``None``.

    Prefers the canonical ``cwd`` + uuid derivation
    (:func:`agent.session_runner.adapter._transcript_path_from_spec`) so the
    slugging formula stays single-defined. Falls back to globbing
    ``<projects_root>/*/<uuid>.jsonl`` when ``runner_cwd`` is unset or the
    derived path does not exist — which is what makes this work for a session
    running in a **foreign repo** whose cwd this process never recorded.
    """
    if not claude_session_uuid:
        return None
    root = projects_root or os.path.join(os.path.expanduser("~"), ".claude", "projects")
    # The canonical derivation hardcodes ``~/.claude/projects``, so it is only
    # consulted when no override is in play; the glob below covers the rest.
    if runner_cwd and not projects_root:
        try:
            from agent.session_runner.adapter import (  # noqa: PLC0415
                _transcript_path_from_spec,
            )

            derived = _transcript_path_from_spec(runner_cwd, claude_session_uuid)
            if os.path.exists(derived):
                return derived
        except Exception as exc:
            logger.debug("session_progress: transcript derivation failed (%s)", exc)
    try:
        matches = sorted(pathlib.Path(root).glob(f"*/{claude_session_uuid}.jsonl"))
        return str(matches[0]) if matches else None
    except Exception:
        return None


def transcript_signal(path: str | None) -> Signal:
    """Transcript mtime, as **context**, with a hard caveat on its staleness.

    A FRESH mtime is real proof of work, so it counts as evidence. A STALE
    mtime proves nothing: while the parent is blocked in a long synchronous
    ``Agent`` call the subagent's steps are not written as sidechain entries
    until the call returns, so the parent transcript is expected to sit
    silent for the whole call. Reading that silence as a hang is exactly the
    #2662 mistake. The asymmetry is safe because the verdict takes the
    freshest signal: a stale transcript can never by itself force a negative
    verdict when any other signal is fresh.
    """
    if not path:
        return Signal("transcript", None, detail="no transcript found")
    try:
        return Signal("transcript", os.path.getmtime(path), detail=path)
    except OSError:
        return Signal("transcript", None, detail=path)


def pr_links(path: str | None, *, limit: int = 5) -> list[dict]:
    """``pr-link`` artifact entries from a transcript, newest last.

    The CLI writes one JSONL line per PR it opens::

        {"type": "pr-link", "prNumber": 102, "prUrl": "...",
         "prRepository": "owner/repo", "timestamp": "2026-..."}

    Artifacts are reported but do NOT drive the verdict: a pr-link records
    that work happened, not that work is happening. Letting it vote would
    make a finished session read PROGRESSING for the window's duration.

    Malformed lines are skipped; an unreadable file yields ``[]``.
    """
    if not path:
        return []
    found: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"pr-link"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "pr-link":
                    continue
                found.append(
                    {
                        "number": entry.get("prNumber"),
                        "url": entry.get("prUrl"),
                        "repository": entry.get("prRepository"),
                        "timestamp": entry.get("timestamp"),
                        "ts": _as_unix_ts(entry.get("timestamp")),
                    }
                )
    except OSError:
        return []
    except Exception:
        return []
    # De-duplicate by PR number, keeping the newest sighting of each.
    by_number: dict = {}
    for entry in found:
        key = entry.get("number") or entry.get("url")
        prior = by_number.get(key)
        if prior is None or (entry.get("ts") or 0) >= (prior.get("ts") or 0):
            by_number[key] = entry
    ordered = sorted(by_number.values(), key=lambda e: e.get("ts") or 0)
    return ordered[-limit:]


def pid_alive(pid: int | None) -> bool | None:
    """``True``/``False`` if the pid's liveness is knowable, else ``None``.

    Reported for the reader only. It never votes on the verdict, and neither
    does anything derived from the process table: ``%CPU`` and child-process
    count are the two readings that produced the #2662 misdiagnosis and are
    not collected anywhere in this module.
    """
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive but owned by another user — existence is what we asked.
        return True
    except Exception:
        return None


def orm_liveness_signals(session) -> list[Signal]:
    """Per-session liveness timestamps carried on the ``AgentSession`` row.

    ``last_tool_use_at`` is repo-scoped (stamped by this repo's
    ``.claude/hooks/pre_tool_use.py``) and is therefore structurally absent
    for a session running against a foreign repo. Its absence is not a
    finding — the hook-edge marker covers that case.
    """
    names = ("last_tool_use_at", "last_turn_at", "last_stdout_at")
    return [Signal(name, _as_unix_ts(getattr(session, name, None))) for name in names]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def compute_verdict(
    signals: list[Signal],
    status: str | None,
    *,
    window_s: float,
    now: float,
) -> tuple[str, str]:
    """Return ``(verdict, reason)`` from evidence freshness alone.

    Order of decision:

    1. A terminal ``status`` short-circuits to ``UNKNOWN``. A completed
       session's markers describe its final turn; calling that "progressing"
       would be a confident wrong answer, and calling it "no recent
       activity" would read as an alarm about a session that simply
       finished. Neither is true, so we decline to guess.
    2. No evidence at all → ``UNKNOWN``.
    3. Freshest evidence within ``window_s`` → ``PROGRESSING``.
    4. Otherwise → ``NO RECENT ACTIVITY`` (explicitly not "wedged").
    """
    if status and status in terminal_statuses():
        return VERDICT_UNKNOWN, f"session status is {status!r} — not running"

    # `age_s` returns None for a future-dated timestamp, so an implausible
    # marker drops out here rather than voting as maximally fresh.
    ages = [
        (sig.name, age)
        for sig in signals
        if sig.counts_as_evidence and sig.ts is not None and (age := sig.age_s(now)) is not None
    ]
    if not ages:
        skewed = [sig.name for sig in signals if sig.is_implausible(now)]
        if skewed:
            return (
                VERDICT_UNKNOWN,
                f"the only timestamps found are dated in the future "
                f"({', '.join(sorted(skewed))}) — clock skew or a corrupt "
                f"marker, so they are not evidence of anything",
            )
        return (
            VERDICT_UNKNOWN,
            "no liveness evidence found (no hook-edge marker, no task output, "
            "no transcript, no ORM liveness timestamps) — absence of evidence "
            "is not evidence of a hang",
        )

    name, age = min(ages, key=lambda pair: pair[1])
    if age <= window_s:
        return VERDICT_PROGRESSING, f"{name} {format_age(age)} ago"
    return (
        VERDICT_NO_RECENT_ACTIVITY,
        f"freshest signal is {name} {format_age(age)} ago, older than the "
        f"{format_age(window_s)} window — a long single tool call also looks "
        f"like this, so this is not proof of a hang",
    )


def format_age(seconds: float | None) -> str:
    """Compact human age: ``4s``, ``5m``, ``2h 6m``, or ``unknown``."""
    if seconds is None:
        return "unknown"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class ProgressReport:
    """Aggregated read-only progress view of one session."""

    session_id: str | None
    agent_session_id: str | None
    status: str | None
    session_type: str | None
    now: float
    window_s: float
    verdict: str
    verdict_reason: str
    signals: list[Signal] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    fields: dict = field(default_factory=dict)

    @property
    def verdict_line(self) -> str:
        """The single-line answer, e.g.
        ``PROGRESSING — tool_activity 4s ago; PR #102 opened 5m ago``.
        """
        parts = [self.verdict_reason]
        for art in self.artifacts[-2:]:
            number = art.get("number")
            age = art.get("ts")
            when = format_age(self.now - age) if age else "unknown"
            label = f"PR #{number}" if number else "PR"
            parts.append(f"{label} opened {when} ago")
        line = f"{self.verdict} — {'; '.join(parts)}"
        note = self.contradiction_note
        return f"{line} ({note})" if note else line

    @property
    def contradiction_note(self) -> str | None:
        """A fact in ``fields`` that argues against the verdict, or None.

        ``exec_pid_alive is False`` is positive evidence the process is gone,
        not absence of evidence — the distinction this whole module is built
        on. It still must not *vote*: a session between turns legitimately has
        a dead ``exec_pid``, and letting that force a negative would
        manufacture the false "wedged" reading #2662 was made of. So the fact
        is surfaced on the line callers actually read, and left out of the
        arithmetic.
        """
        if self.verdict != VERDICT_PROGRESSING:
            return None
        if self.fields.get("exec_pid_alive") is not False:
            return None
        pid = self.fields.get("exec_pid")
        return f"note: exec_pid {pid} is not running" if pid else "note: exec_pid is not running"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent_session_id": self.agent_session_id,
            "status": self.status,
            "session_type": self.session_type,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "verdict_line": self.verdict_line,
            "window_s": self.window_s,
            "signals": [
                {
                    "name": sig.name,
                    "ts": sig.ts,
                    "age_s": sig.age_s(self.now),
                    "detail": sig.detail,
                    "counts_as_evidence": sig.counts_as_evidence,
                }
                for sig in self.signals
            ],
            "pr_links": self.artifacts,
            "fields": self.fields,
        }

    def render(self) -> str:
        lines = [self.verdict_line, ""]
        lines.append(f"Session: {self.session_id}")
        for key in (
            "agent_session_id",
            "status",
            "session_type",
            "created_at",
            "started_at",
            "updated_at",
            "slug",
            "branch_name",
            "runner_cwd",
            "exec_pid",
            "exec_pid_alive",
        ):
            if key in self.fields and self.fields[key] not in (None, ""):
                lines.append(f"  {key + ':':<18} {self.fields[key]}")
        lines.append("")
        lines.append(f"Liveness (window {format_age(self.window_s)}):")
        for sig in self.signals:
            age = format_age(sig.age_s(self.now)) if sig.ts is not None else "—"
            tag = "" if sig.counts_as_evidence else "  [context only]"
            detail = f"  ({sig.detail})" if sig.detail else ""
            lines.append(f"  {sig.name + ':':<18} {age}{detail}{tag}")
        lines.append("")
        if self.artifacts:
            lines.append("Artifacts:")
            for art in self.artifacts:
                when = format_age(self.now - art["ts"]) if art.get("ts") else "unknown"
                lines.append(f"  PR #{art.get('number')} {art.get('url')} ({when} ago)")
        else:
            lines.append("Artifacts: none found")
        return "\n".join(lines)


def build_report(
    session,
    *,
    now: float | None = None,
    window_s: float | None = None,
    hook_edge_root: str | None = None,
    projects_root: str | None = None,
    task_output_roots: list[str] | None = None,
) -> ProgressReport:
    """Aggregate every signal for ``session`` into a :class:`ProgressReport`.

    ``session`` is duck-typed: any object exposing the ``AgentSession``
    attributes works, which keeps this callable from tests without touching
    Redis. The CLI passes a real ORM instance resolved by
    ``tools.valor_session._find_session`` — the ORM is the only route to a
    session record; no raw Redis access happens anywhere on this path.

    The keyword roots exist for hermetic tests; production passes none.
    """
    now = time.time() if now is None else now
    window_s = default_window_s() if window_s is None else window_s

    session_id = getattr(session, "session_id", None)
    claude_uuid = getattr(session, "claude_session_uuid", None)
    runner_cwd = getattr(session, "runner_cwd", None)
    exec_pid = getattr(session, "exec_pid", None)

    tpath = transcript_path(claude_uuid, runner_cwd, projects_root=projects_root)

    signals: list[Signal] = [
        tool_activity_signal(session_id, hook_edge_root=hook_edge_root),
        task_output_signal(claude_uuid, roots=task_output_roots),
        transcript_signal(tpath),
        *orm_liveness_signals(session),
    ]
    artifacts = pr_links(tpath)
    status = getattr(session, "status", None)
    verdict, reason = compute_verdict(signals, status, window_s=window_s, now=now)

    fields = {
        "agent_session_id": getattr(session, "agent_session_id", None),
        "status": status,
        "session_type": getattr(session, "session_type", None),
        "created_at": _fmt(getattr(session, "created_at", None)),
        "started_at": _fmt(getattr(session, "started_at", None)),
        "updated_at": _fmt(getattr(session, "updated_at", None)),
        "slug": getattr(session, "slug", None),
        "branch_name": getattr(session, "branch_name", None),
        "runner_cwd": runner_cwd,
        "exec_pid": exec_pid,
        "exec_pid_alive": pid_alive(exec_pid),
        "transcript": tpath,
    }

    return ProgressReport(
        session_id=session_id,
        agent_session_id=fields["agent_session_id"],
        status=status,
        session_type=fields["session_type"],
        now=now,
        window_s=window_s,
        verdict=verdict,
        verdict_reason=reason,
        signals=signals,
        artifacts=artifacts,
        fields=fields,
    )


def _fmt(value) -> str | None:
    """Stringify a timestamp field for display; never raises."""
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None
