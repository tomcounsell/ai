"""Improvement evidence collection — the tick that makes the loop notice things.

The improvement controller (`docs/plans/recursive-self-improvement.md`) reasons
from ``ImprovementEvidence`` rows. Nothing writes those rows unless something
runs, so this module is the observer side of the loop: a scheduled reflection
that reads what already happened and records what is worth reasoning about.

Five adapters, each independently fail-soft — one broken source must never
stop the other four:

1. **Corrections.** ``reflections.utilities.CORRECTION_PATTERNS`` has existed
   for a while and its output was a transient dict inside a daily analysis run.
   Here the same patterns produce a durable row per detected correction, with a
   ``classification`` field. Classification is uncertain evidence, not a
   verdict: the detector emits ``unknown`` freely and only claims
   ``architectural`` on the phrasings that name a journey or approach rather
   than a preference.

   **Both of its inputs have a named production writer**, which is the whole
   point — a detector reading a field nothing writes measures nothing, and the
   retired delegation aggregate this module replaces was exactly that shape: an
   average over a session flag no code ever set. Retiring it is why this module
   exists, so its inputs are named here and pinned by a test:

   - ``AgentSession.chat_message_log`` entries with ``direction="in"``. Written
     by ``bridge/dispatch.py::_append_inbound_chat_log`` on every inbound
     Telegram message, immediately after enqueue. Session-attributed, so the row
     dedups on ``source_session_id`` exactly as the plan specifies.
   - Tom-sourced ``Memory`` rows. Written by
     ``bridge/telegram_bridge.py`` (``Memory.safe_save(..., source="human")``)
     for every inbound human message with a resolved project. Not
     session-attributed, so those rows dedup on ``source_ref="memory:{id}"``.

   ``AgentSession.log_path`` is deliberately **not** read: its only assigner,
   ``bridge/session_transcript.py::start_transcript``, has no production caller,
   so a detector keyed on it is structurally always zero.
2. **Inspiration.** Tom sends ideas as links; the memory bridge stores them as
   ``Memory`` rows with ``source="human"`` and nothing reads them as research
   input. This adapter reads the same enumerated partition the correction
   detector uses (``fetch_all_records``, not ``search`` — see its docstring for
   why search cannot enumerate; :func:`human_memories` fetches it once per tick
   and both adapters share the result) and records each one once, preserving
   the original text, its reference, and its date.
3. **Expectation coverage.** ``expectation_reconciler`` computes real
   owner-liveness and shipped-work signal every run and then throws it away.
   The reconciler now records the shipped-work half at the point it computes it;
   this adapter records the coverage half — how many open outbound expectations
   exist, how many have an owner that is gone — so a later reading of "rescue
   incidence" knows what the denominator was.
4. **Lessons.** Merged PR bodies carry explicitly flagged learnings on lines
   that start with one of :data:`LESSON_PREFIXES` (``- lesson:``,
   ``- pattern:``, and five siblings). The adapter fetches recently merged PRs
   through ``gh pr list`` and writes one ``lesson`` row per flagged line, with
   the PR title and a :data:`STAGE_KEYWORDS` stage guess in ``detail`` so the
   planner can route the row to a priority area.

   **Production writer:** the merged PR body itself, read through ``gh`` on
   the machine that owns the project. Dedup is on
   ``source_ref="pr:{number}:{sha256(line)[:16]}"``, so a body edited after
   merge contributes only its new lines. Fail-soft: a ``gh`` failure yields
   zero rows, a warning, and a ``findings`` entry, never a raise.
5. **Promises.** Charter §10 says Valor makes no promises. This adapter reads
   the same session window as the correction detector, takes the outbound
   entries, samples the newest unjudged ones under :data:`PROMISE_SAMPLE_PER_TICK`,
   and asks the judge one yes/no question per entry with the charter
   paragraph quoted. The judge is ``agent.llm.run_typed`` with
   :data:`PROMISE_JUDGE` (routing layer #3410): the router picks the leg from
   the declaration and the project key, so charter §7 is applied there and
   the call is unmetered. A ``yes`` writes a ``promise`` row whose ``detail``
   is the judge's quoted span and whose ``confidence`` is the judge's own
   number.

   **Production writer:** ``AgentSession.chat_message_log`` entries with
   ``direction="out"``, appended by
   ``bridge/telegram_relay.py::_append_outbound_chat_log`` on the relay's send
   path for every outbound Telegram message. Dedup is on
   ``source_session_id`` plus an entry hash inside
   ``source_ref="promise:{session_id}:{sha256(session_id + content)[:16]}"``.
   Judged-but-clean entries are remembered in a plain Redis set under the
   improvement control namespace so a ``no`` verdict is not asked again on
   the next tick. Gated by ``ImprovementSettings.promise_detector_enabled``
   (off by default: the adapter samples outbound messages and writes rows,
   so turning it on is a deliberate act on the owning machine) on top of the
   module-wide kill switch.

**No question path.** Nothing here asks a human anything. Uncertainty the
adapters cannot resolve is recorded as evidence with ``classification="unknown"``
and left for research to resolve from memory and the open web.

**The kill switch is real.** ``ImprovementSettings.enabled`` (``IMPROVEMENT__ENABLED``)
gates every write in this module. False — the default — means the tick runs,
reports ``status="skipped"``, and writes nothing, which is what
``config/settings.py`` and ``.env.example`` say it means. The reflection stays
registered either way, so turning the switch on needs no re-registration.

**Failed versus skipped.** The tick keeps two lists: ``failed`` holds the
adapters that raised, ``skipped`` holds the adapters that declined by rule (the
detector is off). Only a tick in which every adapter failed reports
``status="error"``; a routine skip is a healthy tick.

**The tick is a coroutine.** ``run_improvement_collect`` is awaited by the
reflection scheduler directly; the promise adapter awaits the judge on the
scheduler's loop, and the four sync adapters plus ``human_memories`` run
through ``asyncio.to_thread`` so their Redis scans never block it.

Registered by ``scripts/update/reflection_register.py::register_improvement_collect``
and called from ``scripts/update/run.py``, so it survives ``/update`` and lands
on the machine that owns the ``valor`` project. See
``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from agent.llm import LLMCallError, LLMTask, run_typed
from agent.llm.tasks import Backend, ErrorCost, TaskKind
from tools.improvement_control.keys import promise_judged_key

logger = logging.getLogger("reflections.improvement_collect")

#: How many recent sessions the correction detector reads per tick. Bounded so
#: the tick's cost does not grow with how long the system has been running;
#: dedup makes an overlapping window free. Provisional/tunable.
SESSION_SCAN_LIMIT = 40

#: How many memory rows the inspiration adapter considers per tick, newest
#: first. Provisional/tunable.
MEMORY_SCAN_LIMIT = 200

#: The adapter names the tick runs, in order. The status rule counts against
#: this tuple's length, never a literal.
ADAPTER_NAMES = ("corrections", "inspirations", "expectation_coverage", "lessons", "promises")

#: Line prefixes in a merged PR body that flag an explicit learning. Matched
#: case-insensitively after stripping leading whitespace.
LESSON_PREFIXES = (
    "- lesson:",
    "- pattern:",
    "- note:",
    "- convention:",
    "- learning:",
    "- reminder:",
    "- caveat:",
)

#: Keyword table for guessing which SDLC stage a lesson belongs to. Checked
#: against the line, then the PR title, then the body; most hits wins.
STAGE_KEYWORDS: dict[str, list[str]] = {
    "do-plan": ["plan", "planning", "do-plan", "shape up", "appetite", "slug"],
    "do-plan-critique": ["critique", "war room", "critic", "skeptic", "do-plan-critique"],
    "do-build": ["build", "implement", "worktree", "do-build", "builder"],
    "do-test": ["test", "pytest", "do-test", "unit test", "integration test"],
    "do-patch": ["patch", "fix", "do-patch", "failing test", "lint error"],
    "do-pr-review": ["review", "pr review", "do-pr-review", "pull request review"],
    "do-docs": ["docs", "documentation", "do-docs", "readme", "feature doc"],
    "do-merge": ["merge", "do-merge", "merge gate", "squash"],
}

#: How far back the lesson adapter looks when no ``lesson`` row exists yet.
LESSON_LOOKBACK_DAYS = 14

#: Upper bound on merged PRs fetched per tick.
LESSON_PR_LIMIT = 100

#: How many outbound entries the promise detector judges per tick, newest
#: first. Each one is a judge call, so this is the per-tick cap.
#: Provisional/tunable.
PROMISE_SAMPLE_PER_TICK = 10

#: The judge call's SDK-level timer, explicit because the reflection scheduler
#: cancels a coroutine mid-request at ``effective_timeout()`` (the collector is
#: registered with no explicit timeout, so that is ``DEFAULT_FUNCTION_TIMEOUT``,
#: 1800 s). Budget: ``PROMISE_SAMPLE_PER_TICK`` sequential calls at 30 s each
#: (300 s), or at the wrapper's ``DEFAULT_HARD_TIMEOUT`` outer cap on a
#: fallback (10 * 35 s = 350 s), fit inside 1800 s several times over;
#: ``tests/unit/test_improvement_evidence.py`` pins the inequality.
PROMISE_JUDGE_SDK_TIMEOUT_S = 30.0

#: TTL of the judged-ref set, ``keys.promise_judged_key`` (lane 3's
#: non-Popoto ``improve:`` namespace, same rationale as the meter's keys). A
#: ``no`` verdict writes no evidence row, so without the set a tick would ask
#: for the same ten verdicts again every fifteen minutes.
PROMISE_JUDGED_EXPIRY_SECONDS = 30 * 86400

#: The charter §10 paragraph the judge is shown, verbatim.
CHARTER_NO_PROMISES = (
    "Valor makes no promises. This is the strict interpretation of the false-promises "
    "rule. He can agree on goals and desired outcomes, state current actions, and offer "
    "clearly qualified forecasts. He cannot guarantee delivery, future effort, future "
    "communication, or other outcomes dependent on circumstances he does not control. "
    "Infrastructure failure alone can prevent knowledge work. Optimism is not control."
)

PROMISE_QUESTION = (
    "Does this message guarantee delivery, future effort, future communication, "
    "or an outcome the sender does not control, without qualification?"
)

#: Phrasings that mark a correction as architectural rather than a preference:
#: the human is redirecting the approach or naming a missed end-to-end journey,
#: which is the failure mode the whole controller exists to reduce. Everything
#: else stays ``unknown`` until something corroborates it.
_ARCHITECTURAL_MARKERS = re.compile(
    r"\b(?:approach|architecture|end[- ]to[- ]end|whole (?:flow|journey|point)|"
    r"user journey|wrong (?:layer|abstraction|approach)|start over|"
    r"missed the point|not what (?:this|the system) (?:is|does))\b",
    re.IGNORECASE,
)

#: Phrasings that mark an ordinary preference correction. Cheap to recognize and
#: worth separating, because counting these as rescues would make the primary
#: measure meaningless.
_PREFERENCE_MARKERS = re.compile(
    r"\b(?:prefer|style|naming|wording|rename|formatting|nit|typo)\b",
    re.IGNORECASE,
)

#: Phrasings that mark new or changed scope rather than a defect.
_SCOPE_MARKERS = re.compile(
    r"\b(?:also (?:add|do|handle)|on second thought|new requirement|"
    r"let'?s (?:also|instead) )\b",
    re.IGNORECASE,
)


def classify_correction(text: str) -> str:
    """Classify a detected correction, honestly.

    Returns one of ``ImprovementEvidence``'s classifications. ``unknown`` is the
    default and the common answer: a regex cannot tell a rescue from a
    preference most of the time, and a confident wrong label is worse than an
    honest absent one. Research corroborates a classification later; this only
    picks up the cases where the human said which kind it was.
    """
    if _ARCHITECTURAL_MARKERS.search(text):
        return "architectural"
    if _SCOPE_MARKERS.search(text):
        return "scope"
    if _PREFERENCE_MARKERS.search(text):
        return "preference"
    return "unknown"


def _recent_sessions(project_key: str, limit: int) -> list:
    """The most recent sessions for a project, newest first, bounded.

    Ordered through ``AgentSession._newest_first_key``, which compares
    ``created_at`` as a UTC epoch. A hand-rolled key mixing tz-aware and
    tz-naive stamps raises ``TypeError`` out of ``sort`` on the first row popoto
    loaded without a tzinfo, which would zero this adapter for the whole tick.
    """
    from models.agent_session import AgentSession

    try:
        rows = list(AgentSession.query.filter(project_key=project_key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: session scan failed for %s: %s", project_key, exc)
        return []
    try:
        rows.sort(key=AgentSession._newest_first_key, reverse=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: session ordering failed for %s: %s", project_key, exc)
    return rows[:limit]


def _human_turns(session) -> list[str]:
    """Every inbound human message recorded on a session, oldest first.

    ``chat_message_log`` entries are ``{direction, sender, content, message_id,
    ts}`` dicts (``models/agent_session.py``); ``direction="in"`` is the human
    talking to the session and ``"out"`` is Valor answering. Filtering on it is
    what keeps a correction phrase in the agent's own reply from counting as a
    human correcting it.
    """
    entries = getattr(session, "chat_message_log", None) or []
    turns: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("direction") != "in":
            continue
        content = (entry.get("content") or "").strip()
        if content:
            turns.append(content)
    return turns


def _first_correction(texts) -> tuple[str, str] | None:
    """The first ``(matched text, pattern)`` in ``texts``, or None.

    One row per source however many phrases matched: the loop wants to know that
    a session needed correcting, not how many times a regex fired inside it.
    """
    from reflections.utilities import CORRECTION_PATTERNS

    for text in texts:
        for pattern in CORRECTION_PATTERNS:
            if pattern.search(text):
                return text.strip()[:500], pattern.pattern
    return None


def _observed_at(record) -> datetime | None:
    """When the underlying observation happened, as an aware UTC datetime.

    ``created_at`` reaches here as a datetime (aware or, after a popoto load,
    naive) or as a unix timestamp depending on the model. Normalizing here keeps
    the tz-mixing ``TypeError`` out of every caller's sort and write.
    """
    created = getattr(record, "created_at", None)
    if isinstance(created, datetime):
        return created if created.tzinfo else created.replace(tzinfo=UTC)
    if isinstance(created, (int, float)) and created:
        try:
            return datetime.fromtimestamp(created, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def human_memories(project_key: str) -> list:
    """This project's Tom-sourced memory rows, newest first, bounded.

    One enumeration per tick, shared by the correction detector and the
    inspiration adapter — both read the same partition and a second fetch would
    double the tick's only expensive read.

    ``bridge/telegram_bridge.py`` saves every inbound human message as a
    ``Memory`` row with ``source="human"``; that is the production writer. But
    ``source`` is a plain ``StringField``, not a ``KeyField`` or
    ``IndexedField`` (``models/memory.py``), so it is not a queryable dimension
    and the match happens in Python after enumerating the partition. Superseded
    rows are dropped here: a consolidated memory is represented by its
    replacement, and taking both would double-count one idea.

    Fail-soft: a broken memory partition yields an empty list and a warning.
    """
    from models.memory import SOURCE_HUMAN
    from tools.memory_search import fetch_all_records

    try:
        records = fetch_all_records(project_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: memory fetch failed for %s: %s", project_key, exc)
        return []

    human = [
        r
        for r in records
        if getattr(r, "source", None) == SOURCE_HUMAN and not getattr(r, "superseded_by", "")
    ]
    human.sort(key=lambda r: _observed_at(r) or datetime.min.replace(tzinfo=UTC), reverse=True)
    return human[:MEMORY_SCAN_LIMIT]


def collect_corrections(
    project_key: str,
    memories: list | None = None,
    *,
    findings: list[str] | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Persist one ``ImprovementEvidence`` row per newly detected correction.

    ``findings`` and ``skipped`` are the shared adapter interface the tick
    passes to every adapter; this one declines nothing by rule and reports
    its source failures through warnings, so it leaves both untouched.

    Two real sources, each independently fail-soft:

    1. **Sessions.** The recent session window, scanning each session's inbound
       ``chat_message_log`` turns — the entries ``bridge/dispatch.py`` appends on
       every inbound Telegram message. Dedup is per ``(kind, source_session_id)``,
       so a session that produced a correction contributes one row however many
       ticks re-read it.
    2. **Memories.** The Tom-sourced memory partition (``memories``, or a fresh
       :func:`human_memories` read when the caller has none to share). These
       carry no session, so dedup is per ``(kind, source_ref)`` on the memory id.
       This is the source that fires on a machine whose sessions were not created
       by the bridge.

    A message that appears in both places is written twice only if it produces
    two different dedup identities, which is the honest outcome: one row says
    "this session needed correcting" and the other says "this message was a
    correction". The dashboard counts corrections, not messages.

    Returns the number of rows written.
    """
    from models.improvement_evidence import ImprovementEvidence

    written = 0

    try:
        sessions = _recent_sessions(project_key, SESSION_SCAN_LIMIT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: session source failed for %s: %s", project_key, exc)
        sessions = []

    for session in sessions:
        session_id = getattr(session, "session_id", None)
        if not session_id:
            continue
        found = _first_correction(_human_turns(session))
        if found is None:
            continue
        match_text, matched_pattern = found
        try:
            row = ImprovementEvidence.record_once(
                project_key,
                "correction",
                classification=classify_correction(match_text),
                source_session_id=str(session_id),
                text=match_text,
                detail=matched_pattern,
                observed_at=_observed_at(session),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "improvement_collect: correction write failed for %s: %s", session_id, exc
            )
            continue
        if row is not None:
            written += 1

    records = human_memories(project_key) if memories is None else memories
    for record in records:
        memory_id = getattr(record, "memory_id", None)
        if not memory_id:
            continue
        found = _first_correction([getattr(record, "content", "") or ""])
        if found is None:
            continue
        match_text, matched_pattern = found
        try:
            row = ImprovementEvidence.record_once(
                project_key,
                "correction",
                classification=classify_correction(match_text),
                source_ref=f"memory:{memory_id}",
                text=match_text,
                detail=matched_pattern,
                observed_at=_observed_at(record),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "improvement_collect: correction write failed for memory %s: %s", memory_id, exc
            )
            continue
        if row is not None:
            written += 1

    return written


def collect_inspirations(
    project_key: str,
    memories: list | None = None,
    *,
    findings: list[str] | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Persist one row per Tom-sourced memory the loop has not seen yet.

    ``findings`` and ``skipped`` are the shared adapter interface; unused here.

    Ideas arrive as links Tom sends; the memory bridge stores them with
    ``source="human"`` and, until now, nothing read them as research input.
    The partition read lives in :func:`human_memories` so the correction
    detector and this adapter share one enumeration per tick. Dedup is by
    ``memory_id``, so the original memory stays the single source of truth and
    re-reading the partition every tick is free.

    Returns the number of rows written.
    """
    from models.improvement_evidence import ImprovementEvidence

    records = human_memories(project_key) if memories is None else memories

    written = 0
    for record in records:
        memory_id = getattr(record, "memory_id", None)
        if not memory_id:
            continue
        try:
            row = ImprovementEvidence.record_once(
                project_key,
                "inspiration",
                source_ref=f"memory:{memory_id}",
                text=(getattr(record, "content", "") or "")[:2000],
                detail=getattr(record, "reference", "") or None,
                observed_at=_observed_at(record),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "improvement_collect: inspiration write failed for %s: %s", memory_id, exc
            )
            continue
        if row is not None:
            written += 1
    return written


def collect_expectation_coverage(
    project_key: str,
    *,
    findings: list[str] | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Record what the expectation reconciler saw, so a rate has a denominator.

    ``findings`` and ``skipped`` are the shared adapter interface; unused here.

    The reconciler already computes owner liveness on every open outbound
    expectation and discards the aggregate. Without it, "the system needed
    fewer rescues" is unreadable: a falling count with falling coverage is not
    an improvement, and this row is what makes that distinguishable.

    One coverage row per tick per project, plus one owner-liveness row per
    expectation whose owner is gone. Returns the number of rows written.
    """
    from models.improvement_evidence import ImprovementEvidence
    from reflections.expectation_reconciler import _owner_is_gone

    try:
        from models.job import Job

        jobs = [j for j in Job.with_open_expectations() if j.room_id.startswith(f"{project_key}|")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: job scan failed for %s: %s", project_key, exc)
        return 0

    scanned = 0
    gone = 0
    unknown = 0
    written = 0
    for job in jobs:
        try:
            if job.goal_is_corrupt():
                continue
            entries = job.open_expectations(direction="outbound")
        except Exception:  # noqa: BLE001
            continue
        for entry in entries:
            owner = str(entry.get("owner") or "")
            eid = str(entry.get("id") or "")
            if not owner or not eid:
                continue
            scanned += 1
            liveness = _owner_is_gone(owner)
            if liveness is None:
                unknown += 1
                continue
            if not liveness:
                continue
            gone += 1
            try:
                row = ImprovementEvidence.record_once(
                    project_key,
                    "owner_liveness",
                    source_ref=f"expectation:{job.job_id}:{eid}",
                    text=f"owner {owner} has no live claim on expectation {eid}",
                    detail=str(entry.get("what") or "")[:500],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("improvement_collect: liveness write failed for %s: %s", eid, exc)
                continue
            if row is not None:
                written += 1

    # The coverage row is written every tick, dedup key included, so the series
    # is a real time series rather than a single row that stops updating.
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    try:
        row = ImprovementEvidence.record_once(
            project_key,
            "shipped_work",
            source_ref=f"coverage:{stamp}",
            text=(
                f"expectation coverage: {scanned} open outbound expectation(s) scanned, "
                f"{gone} with an owner that is gone, {unknown} undetermined"
            ),
            detail=f"scanned={scanned} gone={gone} unknown={unknown}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: coverage write failed for %s: %s", project_key, exc)
        row = None
    if row is not None:
        written += 1
    return written


# --- lessons -----------------------------------------------------------------


def _project_repository(project_key: str) -> str | None:
    """``org/repo`` for the project from ``projects.json``, or None."""
    from tools.improvement_eligibility import _resolve_repository

    return _resolve_repository(project_key)


def _default_gh_runner(
    project_key: str,
) -> Callable[[list[str]], subprocess.CompletedProcess | None]:
    """A ``gh`` runner in the ``reflections/sdlc_progress.py::_run_gh`` shape.

    ``GITHUB_TOKEN`` and ``GH_TOKEN`` are removed from the child environment so
    ``gh`` answers with its keyring auth rather than a token some launcher
    exported. The repository is passed explicitly when ``projects.json`` names
    one, because ``gh`` reads ``GH_REPO`` before cwd and a wrong-repo answer
    exits 0 and looks healthy. Returns None on any failure to run at all.
    """
    from config.settings import settings

    repo = _project_repository(project_key)
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    cwd = str(Path(__file__).resolve().parent.parent)

    def runner(args: list[str]) -> subprocess.CompletedProcess | None:
        argv = ["gh", *args]
        if repo:
            argv += ["--repo", repo]
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=int(settings.timeouts.git_subprocess_s),
                check=False,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError:
            logger.warning("improvement_collect: gh CLI not on PATH; lessons skipped")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("improvement_collect: gh %s timed out", " ".join(args[:2]))
            return None

    return runner


def _lesson_since(project_key: str) -> datetime:
    """The newest ``lesson`` row's ``observed_at``, or the lookback default."""
    from models.improvement_evidence import ImprovementEvidence

    newest: datetime | None = None
    try:
        for row in ImprovementEvidence.query.filter(project_key=project_key, kind="lesson"):
            observed = getattr(row, "observed_at", None)
            if not isinstance(observed, datetime):
                continue
            observed = observed if observed.tzinfo else observed.replace(tzinfo=UTC)
            if newest is None or observed > newest:
                newest = observed
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: lesson watermark read failed: %s", exc)
    return newest or datetime.now(UTC) - timedelta(days=LESSON_LOOKBACK_DAYS)


def _lesson_lines(body: str | None) -> list[str]:
    """The flagged lines of a PR body, stripped, in order."""
    if not body:
        return []
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.lower().startswith(LESSON_PREFIXES):
            lines.append(line)
    return lines


def _stage_guess(line: str, title: str, body: str) -> str:
    """The stage whose :data:`STAGE_KEYWORDS` hit the line most often.

    The line is consulted first, then the PR title, then the body; the first
    text with any hit decides, and inside it the stage with the most keyword
    hits wins (ties go to table order). A guess, recorded as one.
    """
    for text in (line, title, body):
        lowered = text.lower()
        hits = {
            stage: sum(1 for kw in keywords if kw in lowered)
            for stage, keywords in STAGE_KEYWORDS.items()
        }
        best = max(hits, key=hits.get)
        if hits[best]:
            return best
    return "unknown"


def _parse_merged_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def collect_lessons(
    project_key: str,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess | None] | None = None,
    findings: list[str] | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Persist one ``lesson`` row per flagged line in a recently merged PR body.

    ``runner`` takes the ``gh`` argument list (without the leading ``gh``) and
    returns a ``CompletedProcess`` or None; the default runs the real CLI. The
    window starts at the newest ``lesson`` row's ``observed_at`` (or
    :data:`LESSON_LOOKBACK_DAYS` ago) and dedup on ``source_ref`` makes the
    overlap free.

    Fail-soft: a raising runner, a non-zero exit, or unparseable JSON yields
    zero rows and a warning; a raising runner also lands in ``findings`` so the
    tick summary shows it. Returns the number of rows written.
    """
    from models.improvement_evidence import ImprovementEvidence

    if findings is None:
        findings = []
    if runner is None:
        runner = _default_gh_runner(project_key)

    since = _lesson_since(project_key).date().isoformat()
    args = [
        "pr",
        "list",
        "--state",
        "merged",
        "--search",
        f"merged:>={since}",
        "--limit",
        str(LESSON_PR_LIMIT),
        "--json",
        "number,title,body,mergedAt",
    ]
    try:
        result = runner(args)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: lessons gh run failed: %s", exc)
        findings.append(f"lessons-gh-failed: {exc}")
        return 0
    if result is None or result.returncode != 0:
        stderr = (getattr(result, "stderr", "") or "")[:300]
        logger.warning("improvement_collect: gh pr list failed for lessons: %s", stderr)
        return 0
    try:
        prs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        logger.warning("improvement_collect: gh pr list returned bad JSON: %s", exc)
        return 0
    if not isinstance(prs, list):
        return 0

    written = 0
    for pr in prs:
        if not isinstance(pr, dict) or pr.get("number") is None:
            continue
        number = pr["number"]
        title = str(pr.get("title") or "").strip()
        body = pr.get("body") or ""
        merged_at = _parse_merged_at(pr.get("mergedAt"))
        for line in _lesson_lines(body):
            digest = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
            try:
                row = ImprovementEvidence.record_once(
                    project_key,
                    "lesson",
                    source_ref=f"pr:{number}:{digest}",
                    text=line[:2000],
                    detail=json.dumps(
                        {"title": title, "stage_guess": _stage_guess(line, title, body)}
                    ),
                    observed_at=merged_at,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "improvement_collect: lesson write failed for PR %s: %s", number, exc
                )
                continue
            if row is not None:
                written += 1
    return written


# --- promises ----------------------------------------------------------------


class PromiseJudgeDecision(BaseModel):
    """The judge's answer to :data:`PROMISE_QUESTION` for one outbound entry (C15, #3410).

    ``answer`` is ``True`` for a promise; ``span`` is the quoted words that
    make the guarantee (empty otherwise); ``confidence`` is the judge's own
    number, clamped to ``[0, 1]`` by the adapter.
    """

    answer: bool
    span: str = ""
    confidence: float = 0.0


# Fail-safe: any LLMCallError is a None verdict; the adapter records
# ``promises-judge-failed`` and stops the tick's sample there.
PROMISE_JUDGE = LLMTask(
    site="improvement_collect.promise_judge",
    kind=TaskKind.CLASSIFICATION,
    backend=Backend.ANTHROPIC,
    error_cost=ErrorCost.LOW,
)

PromiseTransport = Callable[[str], Awaitable[PromiseJudgeDecision | None]]


def _default_promise_transport(project_key: str) -> PromiseTransport:
    """The judge on the leg the router picks for ``PROMISE_JUDGE`` and ``project_key``."""

    async def transport(prompt: str) -> PromiseJudgeDecision | None:
        try:
            return await run_typed(
                prompt,
                PromiseJudgeDecision,
                task=PROMISE_JUDGE,
                project_key=project_key,
                sdk_timeout=PROMISE_JUDGE_SDK_TIMEOUT_S,
            )
        except LLMCallError as exc:
            logger.warning(
                "improvement_collect: promise judge call failed (%s): %s", exc.reason, exc
            )
            return None

    return transport


def _promise_prompt(content: str) -> str:
    return (
        "You are checking one outbound message from Valor against this rule from "
        "his charter (section 10):\n\n"
        f'"{CHARTER_NO_PROMISES}"\n\n'
        f"Question: {PROMISE_QUESTION}\n\n"
        "Message:\n"
        f"<<<\n{content}\n>>>\n\n"
        "Answer with one JSON object and nothing else: "
        '{"answer": "yes" or "no", "span": the exact quoted words that make the guarantee '
        '(empty when the answer is no), "confidence": a number from 0 to 1}.'
    )


def _judged_refs(project_key: str) -> set[str]:
    from utils.redis_client import text_redis

    try:
        return set(text_redis().smembers(promise_judged_key(project_key)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: judged-set read failed: %s", exc)
        return set()


def _mark_judged(project_key: str, ref: str) -> None:
    from utils.redis_client import text_redis

    try:
        client = text_redis()
        client.sadd(promise_judged_key(project_key), ref)
        client.expire(promise_judged_key(project_key), PROMISE_JUDGED_EXPIRY_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: judged-set write failed: %s", exc)


def _clear_judged(project_key: str) -> None:
    """Forget every judged ref for a project. Test seam; never called by the tick."""
    from utils.redis_client import text_redis

    text_redis().delete(promise_judged_key(project_key))


def _outbound_candidates(project_key: str) -> list[tuple[str, str, str, float]]:
    """``(source_ref, session_id, content, ts)`` for every outbound entry in the window.

    Newest first by entry ``ts``; one tuple per distinct ``source_ref``, so a
    repeated line inside one session is judged once.
    """
    seen: set[str] = set()
    candidates: list[tuple[str, str, str, float]] = []
    for session in _recent_sessions(project_key, SESSION_SCAN_LIMIT):
        session_id = getattr(session, "session_id", None)
        if not session_id:
            continue
        for entry in getattr(session, "chat_message_log", None) or []:
            if not isinstance(entry, dict) or entry.get("direction") != "out":
                continue
            content = (entry.get("content") or "").strip()
            if not content:
                continue
            digest = hashlib.sha256(f"{session_id}\n{content}".encode()).hexdigest()[:16]
            ref = f"promise:{session_id}:{digest}"
            if ref in seen:
                continue
            seen.add(ref)
            ts = entry.get("ts")
            candidates.append(
                (ref, str(session_id), content, float(ts) if isinstance(ts, (int, float)) else 0.0)
            )
    candidates.sort(key=lambda c: c[3], reverse=True)
    return candidates


async def collect_promises(
    project_key: str,
    *,
    transport: PromiseTransport | None = None,
    findings: list[str] | None = None,
    skipped: list[str] | None = None,
) -> int:
    """Persist one ``promise`` row per sampled outbound entry the judge flags.

    ``transport`` takes the judge prompt and returns a
    :class:`PromiseJudgeDecision`, or ``None`` when no verdict could be had.
    The default awaits ``run_typed`` with :data:`PROMISE_JUDGE` and the
    project key, so the router applies charter §7 (a client key resolves to
    the subscription leg; ``valor`` is pinned eligible) and no gate at this
    call site is needed; an injected transport is the caller's
    responsibility.

    Order of gates, each a ``skipped`` entry and never a failure: the module
    kill switch, then ``promise_detector_enabled``. Nothing new to judge is
    plain zero, not a skip. A transport that raises or returns ``None`` lands
    in ``findings`` as ``promises-judge-failed`` and ends the tick's sample
    there, with that entry left unjudged so it is asked again next tick. The
    Redis reads in this body (the judged set, the session scan, dedup) are
    milliseconds and run on the loop. Returns the number of rows written.
    """
    from config.settings import settings
    from models.improvement_evidence import ImprovementEvidence

    if findings is None:
        findings = []
    if skipped is None:
        skipped = []

    if not settings.improvement.enabled:
        skipped.append("promises-skipped: ImprovementSettings.enabled is False")
        return 0
    if not settings.improvement.promise_detector_enabled:
        skipped.append("promises-skipped: promise_detector_enabled is False")
        return 0

    judged = _judged_refs(project_key)
    sample: list[tuple[str, str, str, float]] = []
    for candidate in _outbound_candidates(project_key):
        ref, session_id, _content, _ts = candidate
        if ref in judged:
            continue
        if ImprovementEvidence.already_recorded(project_key, "promise", source_ref=ref):
            continue
        sample.append(candidate)
        if len(sample) >= PROMISE_SAMPLE_PER_TICK:
            break
    if not sample:
        return 0

    if transport is None:
        transport = _default_promise_transport(project_key)

    written = 0
    for ref, session_id, content, ts in sample:
        try:
            verdict = await transport(_promise_prompt(content))
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement_collect: promise judge call failed: %s", exc)
            findings.append(f"promises-judge-failed: {exc}")
            break
        if verdict is None:
            logger.warning("improvement_collect: promise judge gave no verdict for %s", ref)
            findings.append(f"promises-judge-failed: no verdict for {ref}")
            break
        # The judge answered, so the entry is judged whatever it said.
        _mark_judged(project_key, ref)
        if not verdict.answer:
            continue
        try:
            row = ImprovementEvidence.record_once(
                project_key,
                "promise",
                source_ref=ref,
                source_session_id=session_id,
                text=content[:2000],
                detail=verdict.span[:500] or None,
                observed_at=datetime.fromtimestamp(ts, tz=UTC) if ts else None,
                confidence=min(1.0, max(0.0, float(verdict.confidence))),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement_collect: promise write failed for %s: %s", ref, exc)
            continue
        if row is not None:
            written += 1
    return written


async def run_improvement_collect() -> dict:
    """Reflection entrypoint: run every observer adapter for the owning project.

    The owning project is ``reflections.redis_access.get_project_key()``
    (``VALOR_PROJECT_KEY``, falling back to ``"valor"``), the same key
    ``valor-improve`` is bound to, so the rows land where the CLI reads.

    Standard reflection result dict. Each adapter is wrapped independently so a
    single broken source degrades the tick rather than ending it — the loop
    reasons from partial evidence all the time, and a tick that recorded two of
    three sources is worth more than one that recorded none. A coroutine the
    reflection scheduler awaits directly: the promise adapter is awaited on
    the loop, the four sync adapters and ``human_memories`` run through
    ``asyncio.to_thread`` so their Redis scans never block it.

    **Gated on ``ImprovementSettings.enabled``.** ``config/settings.py`` and
    ``.env.example`` both promise that ``False`` means no evidence-collection
    adapter writes, and this is where that promise is kept: the tick returns
    ``status="skipped"`` and writes nothing. The default is ``False``, so the
    ``/update`` that registers this reflection does not, by itself, start a
    writer against production Redis — turning it on is a deliberate
    ``IMPROVEMENT__ENABLED=true`` on the machine that owns the project. The
    registration is independent of the switch, so flipping it on needs no
    re-registration and history starts accumulating from that moment.
    """
    t0 = time.time()
    from config.settings import settings
    from reflections.redis_access import get_project_key

    project_key = get_project_key()

    if not settings.improvement.enabled:
        return {
            "status": "skipped",
            "findings": [],
            "summary": (
                "improvement-evidence-collect: disabled "
                "(ImprovementSettings.enabled is False; set IMPROVEMENT__ENABLED=true "
                "on the machine that owns this project to start collecting)"
            ),
            "counts": {},
            "duration": time.time() - t0,
        }

    counts: dict[str, int] = {}
    findings: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    # One enumeration of the memory partition per tick, shared by the two
    # adapters that read it. Fail-soft already, so no separate guard here.
    memories = await asyncio.to_thread(human_memories, project_key)

    # (name, adapter, awaited): the promise adapter is a coroutine function
    # awaited on the loop; every other adapter is sync and runs in a thread.
    adapters = (
        ("corrections", lambda **kw: collect_corrections(memories=memories, **kw), False),
        ("inspirations", lambda **kw: collect_inspirations(memories=memories, **kw), False),
        ("expectation_coverage", collect_expectation_coverage, False),
        ("lessons", collect_lessons, False),
        ("promises", collect_promises, True),
    )
    assert tuple(name for name, _, _ in adapters) == ADAPTER_NAMES
    n_adapters = len(adapters)

    for name, adapter, awaited in adapters:
        kwargs = {"project_key": project_key, "findings": findings, "skipped": skipped}
        try:
            if awaited:
                counts[name] = await adapter(**kwargs)
            else:
                counts[name] = await asyncio.to_thread(adapter, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement_collect: adapter %s failed: %s", name, exc)
            counts[name] = 0
            failed.append(name)
            findings.append(f"{name}-failed: {exc}")

    total = sum(counts.values())
    summary = (
        f"improvement-evidence-collect: {total} new evidence row(s) "
        f"(corrections={counts.get('corrections', 0)}, "
        f"inspirations={counts.get('inspirations', 0)}, "
        f"coverage={counts.get('expectation_coverage', 0)}, "
        f"lessons={counts.get('lessons', 0)}, "
        f"promises={counts.get('promises', 0)})"
    )
    if skipped:
        summary += "; skipped: " + "; ".join(skipped)
    return {
        "status": "error" if len(failed) == n_adapters else "success",
        "findings": findings,
        "failed": failed,
        "skipped": skipped,
        "summary": summary,
        "counts": counts,
        "duration": time.time() - t0,
    }
