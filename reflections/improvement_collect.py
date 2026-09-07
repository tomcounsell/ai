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
2. **Inspiration.** Tom sends ideas as links; the memory bridge stores them as
   ``Memory`` rows with ``source="human"`` and nothing reads them as research
   input. This adapter enumerates the partition (``fetch_all_records``, not
   ``search`` — see its docstring for why search cannot enumerate) and records
   each one once, preserving the original text, its reference, and its date.
3. **Expectation coverage.** ``expectation_reconciler`` computes real
   owner-liveness and shipped-work signal every run and then throws it away.
   The reconciler now records the shipped-work half at the point it computes it;
   this adapter records the coverage half — how many open outbound expectations
   exist, how many have an owner that is gone — so a later reading of "rescue
   incidence" knows what the denominator was.

**No question path.** Nothing here asks a human anything. Uncertainty the
adapters cannot resolve is recorded as evidence with ``classification="unknown"``
and left for research to resolve from memory and the open web.

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
from pathlib import Path

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
    """The most recent sessions for a project, newest first, bounded."""
    from models.agent_session import AgentSession

    try:
        rows = list(AgentSession.query.filter(project_key=project_key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: session scan failed for %s: %s", project_key, exc)
        return []
    rows.sort(
        key=lambda s: getattr(s, "created_at", None) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    return rows[:limit]


def collect_corrections(project_key: str) -> int:
    """Persist one ``ImprovementEvidence`` row per newly detected correction.

    Reads the recent session window and scans each session's transcript for the
    correction patterns. Dedup is per ``(kind, source_session_id)``, so a
    session that produced a correction contributes one row however many ticks
    re-read it — the loop wants to know that a session needed correcting, not
    how many times a regex matched inside it.

    Returns the number of rows written.
    """
    from models.improvement_evidence import ImprovementEvidence
    from reflections.utilities import CORRECTION_PATTERNS

    written = 0
    for session in _recent_sessions(project_key, SESSION_SCAN_LIMIT):
        session_id = getattr(session, "session_id", None)
        if not session_id:
            continue
        log_path = getattr(session, "log_path", None)
        if not log_path:
            continue
        try:
            path = Path(log_path)
            if not path.exists():
                continue
            content = path.read_text()
        except OSError as exc:
            logger.debug("improvement_collect: unreadable transcript %s: %s", log_path, exc)
            continue

        match_text: str | None = None
        matched_pattern: str | None = None
        for line in content.splitlines():
            if "USER:" not in line and "user:" not in line:
                continue
            for pattern in CORRECTION_PATTERNS:
                if pattern.search(line):
                    match_text = line.strip()[:500]
                    matched_pattern = pattern.pattern
                    break
            if match_text:
                break
        if not match_text:
            continue

        try:
            row = ImprovementEvidence.record_once(
                project_key,
                "correction",
                classification=classify_correction(match_text),
                source_session_id=str(session_id),
                text=match_text,
                detail=matched_pattern,
                observed_at=getattr(session, "created_at", None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "improvement_collect: correction write failed for %s: %s", session_id, exc
            )
            continue
        if row is not None:
            written += 1
    return written


def collect_inspirations(project_key: str) -> int:
    """Persist one row per Tom-sourced memory the loop has not seen yet.

    Ideas arrive as links Tom sends; the memory bridge stores them with
    ``source="human"`` and, until now, nothing read them as research input.
    ``source`` is a plain ``StringField``, not a queryable dimension, so the
    match happens in Python after enumerating the partition. Dedup is by
    ``memory_id``, so the original memory stays the single source of truth and
    re-reading the partition every tick is free.

    Returns the number of rows written.
    """
    from models.improvement_evidence import ImprovementEvidence
    from models.memory import SOURCE_HUMAN
    from tools.memory_search import fetch_all_records

    try:
        records = fetch_all_records(project_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement_collect: memory fetch failed for %s: %s", project_key, exc)
        return 0

    human = [r for r in records if getattr(r, "source", None) == SOURCE_HUMAN]
    human.sort(key=lambda r: getattr(r, "created_at", None) or 0, reverse=True)

    written = 0
    for record in human[:MEMORY_SCAN_LIMIT]:
        memory_id = getattr(record, "memory_id", None)
        if not memory_id:
            continue
        if getattr(record, "superseded_by", ""):
            # A consolidated memory is represented by its replacement; taking
            # both would double-count one idea.
            continue
        created = getattr(record, "created_at", None)
        observed_at = None
        if isinstance(created, datetime):
            observed_at = created
        elif isinstance(created, (int, float)) and created:
            observed_at = datetime.fromtimestamp(created, tz=UTC)
        try:
            row = ImprovementEvidence.record_once(
                project_key,
                "inspiration",
                source_ref=f"memory:{memory_id}",
                text=(getattr(record, "content", "") or "")[:2000],
                detail=getattr(record, "reference", "") or None,
                observed_at=observed_at,
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

    The tick runs whether or not ``ImprovementSettings.enabled`` is set: the
    controller stays off, but its evidence keeps accumulating, so turning the
    controller on later starts from history rather than from nothing.
    """
    t0 = time.time()
    from config.memory_defaults import DEFAULT_PROJECT_KEY

    project_key = DEFAULT_PROJECT_KEY
    counts: dict[str, int] = {}
    findings: list[str] = []

    for name, adapter in (
        ("corrections", collect_corrections),
        ("inspirations", collect_inspirations),
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
