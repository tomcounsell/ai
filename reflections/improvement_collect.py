"""Improvement evidence collection — the tick that makes the loop notice things.

The improvement controller (`docs/plans/recursive-self-improvement.md`) reasons
from ``ImprovementEvidence`` rows. Nothing writes those rows unless something
runs, so this module is the observer side of the loop: a scheduled reflection
that reads what already happened and records what is worth reasoning about.

Three adapters, each independently fail-soft — one broken source must never
stop the other two:

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

**No question path.** Nothing here asks a human anything. Uncertainty the
adapters cannot resolve is recorded as evidence with ``classification="unknown"``
and left for research to resolve from memory and the open web.

**The kill switch is real.** ``ImprovementSettings.enabled`` (``IMPROVEMENT__ENABLED``)
gates every write in this module. False — the default — means the tick runs,
reports ``status="skipped"``, and writes nothing, which is what
``config/settings.py`` and ``.env.example`` say it means. The reflection stays
registered either way, so turning the switch on needs no re-registration.

Registered by ``scripts/update/reflection_register.py::register_improvement_collect``
and called from ``scripts/update/run.py``, so it survives ``/update`` and lands
on the machine that owns the ``valor`` project. See
``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime

logger = logging.getLogger("reflections.improvement_collect")

#: How many recent sessions the correction detector reads per tick. Bounded so
#: the tick's cost does not grow with how long the system has been running;
#: dedup makes an overlapping window free. Provisional/tunable.
SESSION_SCAN_LIMIT = 40

#: How many memory rows the inspiration adapter considers per tick, newest
#: first. Provisional/tunable.
MEMORY_SCAN_LIMIT = 200

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


def collect_corrections(project_key: str, memories: list | None = None) -> int:
    """Persist one ``ImprovementEvidence`` row per newly detected correction.

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


def collect_inspirations(project_key: str, memories: list | None = None) -> int:
    """Persist one row per Tom-sourced memory the loop has not seen yet.

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


def collect_expectation_coverage(project_key: str) -> int:
    """Record what the expectation reconciler saw, so a rate has a denominator.

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


def run_improvement_collect() -> dict:
    """Reflection entrypoint: run every observer adapter for the owning project.

    Standard reflection result dict. Each adapter is wrapped independently so a
    single broken source degrades the tick rather than ending it — the loop
    reasons from partial evidence all the time, and a tick that recorded two of
    three sources is worth more than one that recorded none.

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
    from config.memory_defaults import DEFAULT_PROJECT_KEY
    from config.settings import settings

    project_key = DEFAULT_PROJECT_KEY

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

    # One enumeration of the memory partition per tick, shared by the two
    # adapters that read it. Fail-soft already, so no separate guard here.
    memories = human_memories(project_key)

    for name, adapter in (
        ("corrections", lambda pk: collect_corrections(pk, memories=memories)),
        ("inspirations", lambda pk: collect_inspirations(pk, memories=memories)),
        ("expectation_coverage", collect_expectation_coverage),
    ):
        try:
            counts[name] = adapter(project_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement_collect: adapter %s failed: %s", name, exc)
            counts[name] = 0
            findings.append(f"{name}-failed: {exc}")

    total = sum(counts.values())
    summary = (
        f"improvement-evidence-collect: {total} new evidence row(s) "
        f"(corrections={counts.get('corrections', 0)}, "
        f"inspirations={counts.get('inspirations', 0)}, "
        f"coverage={counts.get('expectation_coverage', 0)})"
    )
    return {
        "status": "error" if len(findings) == 3 else "success",
        "findings": findings,
        "summary": summary,
        "counts": counts,
        "duration": time.time() - t0,
    }
