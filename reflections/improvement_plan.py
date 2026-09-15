"""The planner tick: open cases from evidence, rank them, propose one action.

``run_improvement_planner`` is the function reflection registered as
``improvement-planner-tick`` (``scripts/update/reflection_register.py``); its
core, :func:`plan_tick`, is a pure controller pass with no LLM call, no
dispatch, and no evaluation. Judgment enters the loop in the research session
lane 3 dispatches; this module only decides what that session is asked about.

**Order per tick**, each step wrapped independently and reported in
``counts``; a step that raises becomes a finding, and a later step that
depends on it is skipped:

(a) ``ImprovementCharter.load_from_file`` then ``pinned``. No pinned charter
    ends the tick with ``status="error"`` and zero writes: a case with no
    digest cannot be ranked or proposed.
(b) No pause pre-read. A namespace or case pause (lane 3's break-glass)
    surfaces as a ``PAUSED`` refusal from ``transition()``; the tick records
    ``paused:<case_id>`` and skips that case for the tick, with no proposal
    for it.
(c) Keep-alive and unblock. Every ``awaiting_authorization`` investigation
    and every ``resource_acquisition`` investigation with disposition
    ``vault_request_written`` gets a ``save()`` so its 30-day TTL restarts
    from this tick (the disposition is the ``disposition`` key of the
    ``claims`` JSON envelope ``tools.improvement_investigations`` writes,
    with ``assumption_detail`` and ``sources`` read the same way as
    fallbacks). Every open case whose
    ``blocked_by`` names a vault item is checked against one
    ``tools.improvement_resources.probe()`` per tick (run only when some case
    is blocked): ``name = blocked_by.removeprefix("vault:")``, and a report
    entry in state ``verified`` clears the block after a ``case_unblocked``
    journal event. When the pinned digest differs from the digest the last
    tick recorded, ``tools.improvement_investigations.resolve_awaiting_on_new_digest``
    resolves the awaiting rows.
(d) :func:`open_cases`.
(e) ``rank`` + ``write_snapshot``. ``ranking_recorded`` is journaled once per
    case whose rank moved (entered, left, or changed position) on that case's
    own journal, carrying the snapshot reference as ``artifact_ref`` and the
    snapshot digest as ``payload_digest``; a tick where nothing moved journals
    nothing and reuses the previous snapshot reference. The tick's own
    cursor (``ImprovementControllerState``) is written by ORM ``save()``.
    A tick restricted to ``case_ids`` (lane 6's arm seam) ranks the named
    subset in memory and writes none of this: the chain records full ticks.
(f) :func:`propose_one_action` for the first unblocked, non-busy case, through
    ``tools.improvement.cmd_propose`` so lane 3's adapter admits it.
(g) The ``apply_verdict`` backstop for every ``complete`` evaluation whose
    case still reads ``evaluating``; ``tools.improvement_experiment`` is
    imported inside the step to keep the module free of it at import time.

**Identity rules** (``dedup_identity``): a ``correction`` row is its
``classification`` plus the normalized first eight words of ``text``; a
``lesson`` row is its ``stage_guess`` (from ``detail`` JSON) plus the same
prefix; every ``promise`` row shares the constant ``"unqualified-promises"``
(one case, growing evidence); an ``inspiration`` row without a URL is
``inspiration:`` plus the prefix. Only those four kinds cluster; the other
evidence kinds (shipped work, liveness, receipts, probes) are read by their
own consumers and never open a case.

**The priority_area rule table**: an ``architectural`` correction opens in
``orchestration`` and any other correction in ``other``; a ``lesson`` for
``do-build``/``do-patch`` opens in ``orchestration``, for
``do-test``/``do-pr-review`` in ``evaluators``, for
``do-plan``/``do-plan-critique`` in ``research_process``, for
``do-docs``/``do-merge`` in ``other``; a ``promise`` opens in ``personas``; an
inspiration cluster sourced from memory (``source_ref`` starting with
``memory:``) opens in ``memory``; a seeded row opens in the area its
``detail`` names.

**Seeded rows first (cold start).** Before the URL route and before
clustering, every pending row's ``detail`` is parsed as JSON (a parse failure
or a non-object is "not seeded", never an error). A row whose ``detail``
carries a ``seed`` key and a ``priority_area`` in ``PRIORITY_AREAS`` is a
one-row cluster that opens a case with that area and
``dedup_identity=f"seed:{seed}"``, bypassing both the URL-to-intake route and
``CASE_OPEN_MIN_EVIDENCE``. A seeded row that lacks ``priority_area`` (or names
one outside the tuple) takes the ordinary route. The novelty check still runs
on the seed identity, so re-seeding attaches to the existing case.

**The intake-pool rule.** An ``inspiration`` row with a URL opens an
``inspiration_intake`` investigation (``stage="draft"``, the URL in
``sources`` with the row id under ``evidence_id``), never a case, and the tick
never proposes for an investigation. It is worked by a research session
already dispatched for a case in the same ``priority_area`` (the brief lists
the pool), whose ``resolve()`` may call :func:`open_cases` with the extracted
substance; until then the pool waits, and the snapshot's ``intake_pool``
shows it waiting.

**Consumed ids, not a window.** A row is pending when its id is absent from
every case's ``evidence_ids`` and from every intake investigation's
``sources[*].evidence_id``. ``evidence_watermark`` on the cursor row is only
a scan bound (rows newer than ``watermark - EVIDENCE_TTL``), so a cluster
that accrues one row per tick still reaches two rows.

**The novelty check** runs before every open: any ``ImprovementCase`` (any
state) with the same ``dedup_identity`` refuses the open. A ``rejected`` match
appends the new ids and journals ``evidence_attached_to_rejected`` naming the
rejecting evaluation; an open match appends the ids and journals
``evidence_attached``. A resolved investigation whose ``interpretation``
contains the identity also refuses; its ids attach to that investigation's
case when it has one.

**TTL and index decisions.** This module adds no index and no TTL: cases
are immortal and read by state; evidence and investigations keep their
30-day TTLs; the cursor row is immortal (see
``models/improvement_controller_state.py``).

**Leases and state.** Every per-case journal write mints its generation
from that case's lease (``keys.lease_key``); a held lease is a
``lease_busy:<case_id>`` finding and the write is skipped this tick, never
written with a fixed generation. The tick moves no case state: a new case is
``observed`` (the row's initial value), ``case_unblocked`` changes no state,
and state moves belong to ``apply_verdict`` through ``journal.set_state``.

**No raw Redis.** Every control-namespace write goes through
``tools.improvement_control`` and every Popoto key through the ORM.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from models.improvement_case import OPEN_CASE_STATES, PRIORITY_AREAS, ImprovementCase
from models.improvement_evidence import ImprovementEvidence
from models.improvement_investigation import INVESTIGATION_STATES, ImprovementInvestigation
from tools.improvement_control import keys
from tools.improvement_control.journal import read_head, transition
from tools.improvement_control.lease import default_lease
from tools.improvement_control.projection import apply as project_case
from tools.improvement_ranking import (
    STARTING_PRIORITIES,
    compute_diff,
    load_snapshot,
    rank,
    snapshot_digest,
    write_snapshot,
)

logger = logging.getLogger("reflections.improvement_plan")

#: Rows a cluster needs before it opens a case. Provisional/tunable. A single
#: ``architectural`` correction and a seeded row both bypass it.
CASE_OPEN_MIN_EVIDENCE = 2

#: How many recent evidence rows one tick reads. Bounded on purpose;
#: consumed-id filtering makes the overlap free. Provisional/tunable.
EVIDENCE_SCAN_LIMIT = 500

#: The evidence row TTL, the scan bound's width below the watermark.
EVIDENCE_TTL = timedelta(seconds=int(getattr(ImprovementEvidence._meta, "ttl", 86400 * 30)))

IDENTITY_PREFIX_WORDS = 8
PROMISE_IDENTITY = "unqualified-promises"

#: The kinds that cluster into cases (module docstring, identity rules).
CLUSTERED_KINDS: frozenset[str] = frozenset({"correction", "lesson", "promise", "inspiration"})

#: The lesson rule table (module docstring).
LESSON_STAGE_AREAS: dict[str, str] = {
    "do-build": "orchestration",
    "do-patch": "orchestration",
    "do-test": "evaluators",
    "do-pr-review": "evaluators",
    "do-plan": "research_process",
    "do-plan-critique": "research_process",
    "do-docs": "other",
    "do-merge": "other",
}

#: Intent states under which a case is being worked and gets no proposal.
BUSY_INTENT_STATES: frozenset[str] = frozenset({"admitted", "materialized", "running"})


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class OpenResult:
    opened: list[str] = field(default_factory=list)
    attached: list[str] = field(default_factory=list)
    intake: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    scanned: int = 0
    pending: int = 0
    watermark: str | None = None


@dataclass
class TickResult:
    status: str
    findings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    snapshot_ref: str | None = None
    proposal: dict | None = None
    opened: list[str] = field(default_factory=list)
    charter_digest: str | None = None
    duration_seconds: float = 0.0

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "findings": list(self.findings),
            "counts": dict(self.counts),
            "snapshot_ref": self.snapshot_ref,
            "proposal": self.proposal,
            "opened": list(self.opened),
            "charter_digest": self.charter_digest,
            "duration_seconds": self.duration_seconds,
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _row_time(row) -> datetime | None:
    return _aware(getattr(row, "observed_at", None)) or _aware(getattr(row, "created_at", None))


def normalized_prefix(text: str | None, words: int = IDENTITY_PREFIX_WORDS) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())
    return "-".join(cleaned.split()[:words])


def seed_meta(row) -> dict | None:
    """``{"seed", "priority_area"}`` when the row is a seeded one-row cluster."""
    meta = _json_object(getattr(row, "detail", None))
    if not meta or "seed" not in meta:
        return None
    if meta.get("priority_area") not in PRIORITY_AREAS:
        return None
    return meta


def inspiration_url(row) -> str | None:
    meta = _json_object(getattr(row, "detail", None)) or {}
    url = meta.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        return url
    text = getattr(row, "text", None) or ""
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else None


def dedup_identity(row) -> str | None:
    """The cluster identity of an unseeded row, or ``None`` for a row that
    takes no cluster route (a URL inspiration, an unclustered kind)."""
    kind = getattr(row, "kind", None)
    if kind not in CLUSTERED_KINDS:
        return None
    text = getattr(row, "text", None)
    if kind == "correction":
        classification = getattr(row, "classification", None) or "unknown"
        return f"correction:{classification}:{normalized_prefix(text)}"
    if kind == "lesson":
        meta = _json_object(getattr(row, "detail", None)) or {}
        return f"lesson:{meta.get('stage_guess') or 'unknown'}:{normalized_prefix(text)}"
    if kind == "promise":
        return PROMISE_IDENTITY
    if inspiration_url(row):
        return None
    return f"inspiration:{normalized_prefix(text)}"


def priority_area_for(rows) -> str:
    """The rule table (module docstring) applied to one cluster."""
    first = rows[0]
    kind = getattr(first, "kind", None)
    if kind == "correction":
        architectural = any(getattr(r, "classification", None) == "architectural" for r in rows)
        return "orchestration" if architectural else "other"
    if kind == "lesson":
        meta = _json_object(getattr(first, "detail", None)) or {}
        return LESSON_STAGE_AREAS.get(meta.get("stage_guess") or "", "other")
    if kind == "promise":
        return "personas"
    if kind == "inspiration" and (getattr(first, "source_ref", None) or "").startswith("memory:"):
        return "memory"
    return "other"


def _passage(area: str) -> str:
    if area in STARTING_PRIORITIES:
        return f"charter §3 starting priority ({area})"
    return f"charter §3 eligible means ({area})" if area != "other" else "charter §3 (other)"


def _alternatives(rows, area: str) -> list[str]:
    kind = getattr(rows[0], "kind", None)
    alternatives = [
        "The rows cluster by wording rather than by cause; each may have a different root cause.",
        "The pattern reflects the observer's sampling window rather than a standing weakness.",
    ]
    if kind == "correction":
        alternatives.append(
            "The corrections are ordinary preference feedback, not evidence the journey was lost."
        )
    if kind == "promise":
        alternatives.append("The flagged messages were qualified in context the judge did not see.")
    if area == "other":
        alternatives.append("The weakness belongs to a named charter area once investigated.")
    return alternatives


# ---------------------------------------------------------------------------
# Journal writes under the case lease
# ---------------------------------------------------------------------------


def _journal(
    project_key: str,
    case_id: str,
    *,
    event: str,
    payload_digest: str,
    findings: list[str],
    artifact_ref: str = "",
) -> bool:
    """One fenced journal write, then the projection. False on any refusal.

    The generation comes from the case lease exactly as
    ``scheduler_adapter._tick_one_case`` mints it; a held lease is a
    ``lease_busy`` finding and no write.
    """
    from config.settings import settings

    lease = default_lease()
    lease_key = keys.lease_key(project_key, case_id)
    generation = lease.acquire(lease_key, ttl=settings.improvement.lease_ttl_seconds)
    if generation is None:
        findings.append(f"lease_busy:{case_id}")
        return False
    try:
        head = read_head(project_key, case_id)
        result = transition(
            project_key,
            case_id,
            expected_revision=head.revision if head is not None else 0,
            generation=generation,
            event=event,
            payload_digest=payload_digest,
            artifact_ref=artifact_ref,
        )
    finally:
        lease.release(lease_key, generation)
    if not result.accepted:
        if result.reason == "PAUSED":
            findings.append(f"paused:{case_id}")
        else:
            findings.append(f"{event} refused for {case_id}: {result.reason}")
        return False
    project_case(project_key, case_id)
    return True


# ---------------------------------------------------------------------------
# Case opening
# ---------------------------------------------------------------------------


def _all_cases(project_key: str) -> list[ImprovementCase]:
    return list(ImprovementCase.query.filter(project_key=project_key))


def _open_case_rows(project_key: str) -> list[ImprovementCase]:
    rows: list[ImprovementCase] = []
    for state in OPEN_CASE_STATES:
        rows.extend(ImprovementCase.query.filter(project_key=project_key, state=state))
    return rows


def _investigations(project_key: str) -> list[ImprovementInvestigation]:
    rows: list[ImprovementInvestigation] = []
    for state in INVESTIGATION_STATES:
        rows.extend(ImprovementInvestigation.query.filter(project_key=project_key, state=state))
    return rows


def _consumed_ids(cases, investigations) -> set[str]:
    consumed: set[str] = set()
    for case in cases:
        consumed.update(str(eid) for eid in _json_list(getattr(case, "evidence_ids", None)))
    for inv in investigations:
        if getattr(inv, "kind", None) != "inspiration_intake":
            continue
        for source in _json_list(getattr(inv, "sources", None)):
            if isinstance(source, dict) and source.get("evidence_id"):
                consumed.add(str(source["evidence_id"]))
    return consumed


def _scan_evidence(project_key: str, watermark: str | None) -> list:
    rows = ImprovementEvidence.recent(project_key, limit=EVIDENCE_SCAN_LIMIT)
    cutoff = None
    if watermark:
        try:
            cutoff = datetime.fromisoformat(watermark) - EVIDENCE_TTL
        except ValueError:
            cutoff = None
    if cutoff is None:
        return rows
    kept = []
    for row in rows:
        stamp = _row_time(row)
        if stamp is None or stamp >= cutoff:
            kept.append(row)
    return kept


def _attach(project_key: str, case, rows, *, result: OpenResult) -> None:
    existing = [str(e) for e in _json_list(case.evidence_ids)]
    new_ids = [r.id for r in rows if r.id not in existing]
    if not new_ids:
        return
    if case.state == "rejected":
        evaluation_ids = _json_list(getattr(case, "evaluation_ids", None))
        payload = {
            "case_id": case.id,
            "evidence_ids": new_ids,
            "rejecting_evaluation": evaluation_ids[-1] if evaluation_ids else None,
            "rejected_reason": getattr(case, "rejected_reason", None),
        }
        event = "evidence_attached_to_rejected"
    else:
        payload = {"case_id": case.id, "evidence_ids": new_ids}
        event = "evidence_attached"
    if not _journal(
        project_key, case.id, event=event, payload_digest=_digest(payload), findings=result.findings
    ):
        result.refused.append(case.id)
        return
    fresh = ImprovementCase.query.get(project_key=project_key, id=case.id) or case
    fresh.evidence_ids = json.dumps(existing + new_ids)
    fresh.updated_at = datetime.now(UTC).isoformat()
    fresh.save()
    result.attached.append(case.id)


def _open_one(project_key, charter, *, identity, area, rows, result: OpenResult) -> str:
    now = datetime.now(UTC)
    first = rows[0]
    kind = getattr(first, "kind", None) or "other"
    ids = [r.id for r in rows]
    title = ((getattr(first, "text", None) or identity).strip().splitlines() or [identity])[0][:80]
    rationale = (
        f"Opened from {len(rows)} {kind} row(s) under {_passage(area)}; ordered by the ordinal "
        f"rules in tools/improvement_ranking.py against charter {charter.digest[:23]}."
    )
    summary = "\n".join(
        f"- [{getattr(r, 'kind', '?')}/{getattr(r, 'classification', '?')}] "
        f"{(getattr(r, 'text', None) or '').strip()[:240]}"
        for r in rows
    )
    case = ImprovementCase.create(
        project_key=project_key,
        created_at=now,
        state="observed",
        priority="normal",
        priority_area=area,
        title=title,
        summary=summary,
        alternative_explanations=json.dumps(_alternatives(rows, area)),
        evidence_ids=json.dumps(ids),
        charter_version=int(getattr(charter, "version", 0) or 0),
        charter_digest=charter.digest,
        ranking_rationale=rationale,
        dedup_identity=identity,
        updated_at=now.isoformat(),
    )
    payload = {
        "case_id": case.id,
        "dedup_identity": identity,
        "priority_area": area,
        "evidence_ids": ids,
        "charter_digest": charter.digest,
        "title": title,
    }
    _journal(
        project_key,
        case.id,
        event="case_opened",
        payload_digest=_digest(payload),
        findings=result.findings,
    )
    result.opened.append(case.id)
    return case.id


def _open_intake(project_key, charter, row, url, *, result: OpenResult) -> None:
    now = datetime.now(UTC)
    inv = ImprovementInvestigation.create(
        project_key=project_key,
        created_at=now,
        kind="inspiration_intake",
        state="open",
        stage="draft",
        uncertainty=f"what {url} contributes, read under charter §4",
        query=url,
        sources=json.dumps(
            [
                {
                    "url": url,
                    "retrieved_at": None,
                    "title": (getattr(row, "text", None) or "")[:80],
                    "evidence_id": row.id,
                }
            ]
        ),
        decision_affected="whether this inspiration opens a case, and in which priority area",
        charter_digest=charter.digest,
    )
    result.intake.append(inv.id)


def open_cases(project_key: str, charter, *, evidence=None, watermark: str | None = None):
    """Cluster unconsumed evidence into cases; return an :class:`OpenResult`.

    ``evidence`` is the rows to consider (default: the bounded recent scan);
    ``watermark`` is the previous tick's scan bound. Seeded rows open first,
    URL inspirations become intake investigations, the rest cluster by
    identity and open at ``CASE_OPEN_MIN_EVIDENCE`` rows or one
    ``architectural`` correction. The novelty check runs before every open.
    """
    result = OpenResult()
    cases = _all_cases(project_key)
    investigations = _investigations(project_key)
    rows = list(evidence) if evidence is not None else _scan_evidence(project_key, watermark)
    result.scanned = len(rows)
    stamps = [s for s in (_aware(getattr(r, "created_at", None)) for r in rows) if s]
    result.watermark = max(stamps).isoformat() if stamps else watermark

    consumed = _consumed_ids(cases, investigations)
    pending = [r for r in rows if str(r.id) not in consumed]
    result.pending = len(pending)
    if not pending:
        return result

    by_identity: dict[str, ImprovementCase] = {}
    for case in cases:
        identity = getattr(case, "dedup_identity", None)
        if identity and identity not in by_identity:
            by_identity[identity] = case
    resolved = [
        inv
        for inv in investigations
        if getattr(inv, "state", None) == "resolved" and getattr(inv, "interpretation", None)
    ]

    def novelty_match(identity: str):
        case = by_identity.get(identity)
        if case is not None:
            return case
        for inv in resolved:
            if identity in inv.interpretation:
                case_id = getattr(inv, "case_id", None)
                return next((c for c in cases if c.id == case_id), None) or inv
        return None

    def open_or_attach(identity: str, area: str, cluster: list) -> None:
        match = novelty_match(identity)
        if match is None:
            case_id = _open_one(
                project_key, charter, identity=identity, area=area, rows=cluster, result=result
            )
            cases.append(ImprovementCase.query.get(project_key=project_key, id=case_id))
            by_identity[identity] = cases[-1]
            return
        if isinstance(match, ImprovementCase):
            _attach(project_key, match, cluster, result=result)
            return
        result.refused.append(match.id)
        result.findings.append(
            f"novelty check: identity {identity!r} answered by resolved investigation {match.id}"
        )

    # Seeded rows first (cold start).
    clusters: dict[str, list] = {}
    for row in pending:
        meta = seed_meta(row)
        if meta is not None:
            open_or_attach(f"seed:{meta['seed']}", meta["priority_area"], [row])
            continue
        if getattr(row, "kind", None) == "inspiration":
            url = inspiration_url(row)
            if url:
                _open_intake(project_key, charter, row, url, result=result)
                continue
        identity = dedup_identity(row)
        if identity is None:
            continue
        clusters.setdefault(identity, []).append(row)

    for identity, cluster in clusters.items():
        area = priority_area_for(cluster)
        architectural = any(
            getattr(r, "kind", None) == "correction"
            and getattr(r, "classification", None) == "architectural"
            for r in cluster
        )
        match = novelty_match(identity)
        if match is None and len(cluster) < CASE_OPEN_MIN_EVIDENCE and not architectural:
            continue
        open_or_attach(identity, area, cluster)
    return result


# ---------------------------------------------------------------------------
# Keep-alive, unblock, amendment hook
# ---------------------------------------------------------------------------


def _disposition(row) -> str | None:
    for name in ("claims", "assumption_detail", "sources"):
        obj = _json_object(getattr(row, name, None))
        if obj and obj.get("disposition"):
            return str(obj["disposition"])
    return None


def _keep_alive(project_key: str) -> int:
    rows = list(
        ImprovementInvestigation.query.filter(
            project_key=project_key, state="awaiting_authorization"
        )
    )
    for row in ImprovementInvestigation.query.filter(
        project_key=project_key, kind="resource_acquisition"
    ):
        if _disposition(row) == "vault_request_written" and row not in rows:
            rows.append(row)
    for row in rows:
        row.save()
    return len(rows)


def _unblock(project_key: str, *, probe, findings: list[str]) -> int:
    blocked = [c for c in _open_case_rows(project_key) if getattr(c, "blocked_by", None)]
    if not blocked:
        return 0
    if probe is None:
        from tools.improvement_resources import probe as default_probe

        probe = default_probe
    report = probe()
    cleared = 0
    for case in blocked:
        name = case.blocked_by.removeprefix("vault:")
        if (report.get(name) or {}).get("state") != "verified":
            continue
        payload = {"case_id": case.id, "resource": name, "blocked_by": case.blocked_by}
        if not _journal(
            project_key,
            case.id,
            event="case_unblocked",
            payload_digest=_digest(payload),
            findings=findings,
        ):
            continue
        fresh = ImprovementCase.query.get(project_key=project_key, id=case.id) or case
        fresh.blocked_by = None
        fresh.updated_at = datetime.now(UTC).isoformat()
        fresh.save()
        cleared += 1
    return cleared


def _resolve_amendments(project_key: str, charter, recorded_digest: str | None, findings) -> int:
    if not recorded_digest or recorded_digest == charter.digest:
        return 0
    from tools.improvement_investigations import resolve_awaiting_on_new_digest

    resolved = resolve_awaiting_on_new_digest(project_key, charter.digest)
    return int(resolved or 0) if not isinstance(resolved, list) else len(resolved)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _evidence_index(project_key: str, cases) -> dict:
    index = {r.id: r for r in ImprovementEvidence.recent(project_key, limit=EVIDENCE_SCAN_LIMIT)}
    for case in cases:
        for eid in _json_list(getattr(case, "evidence_ids", None)):
            if eid in index:
                continue
            try:
                row = ImprovementEvidence.query.get(project_key=project_key, id=eid)
            except Exception:  # noqa: BLE001 -- an expired or foreign id is simply absent
                row = None
            if row is not None:
                index[eid] = row
    return index


def _experiments(project_key: str) -> list:
    from models.improvement_experiment import EXPERIMENT_STATES, ImprovementExperiment

    rows = []
    for state in EXPERIMENT_STATES:
        rows.extend(ImprovementExperiment.query.filter(project_key=project_key, state=state))
    return rows


def _rank_and_record(
    project_key: str,
    charter,
    *,
    store,
    state,
    intake_pool: list[str],
    now: datetime,
    case_ids: list[str] | None,
    findings: list[str],
) -> tuple[list, str | None, set[str]]:
    """Rank the open set, write (or reuse) the snapshot, journal the moves.

    Returns ``(ranked, snapshot_ref, refused_case_ids)``. A ``case_ids``
    restriction (lane 6's arm seam) ranks the named subset in memory only:
    the snapshot chain records full ticks, so nothing is written, and the
    returned reference is the cursor's existing one (``None`` before any
    full tick).
    """
    from models.improvement_evaluation import ImprovementEvaluation
    from models.improvement_model_revision import ImprovementModelRevision

    open_rows = _open_case_rows(project_key)
    if case_ids is not None:
        wanted = set(case_ids)
        open_rows = [c for c in open_rows if c.id in wanted]
    # Race 2: a case whose head moved since the row was read is re-read once.
    refreshed = []
    for case in open_rows:
        head = read_head(project_key, case.id)
        if head is not None and head.revision != int(getattr(case, "revision", 0) or 0):
            case = ImprovementCase.query.get(project_key=project_key, id=case.id) or case
        refreshed.append(case)
    open_rows = refreshed

    all_cases = _all_cases(project_key)
    ranked = rank(
        open_rows,
        evidence=list(_evidence_index(project_key, open_rows).values()),
        investigations=_investigations(project_key),
        experiments=_experiments(project_key),
        charter=charter,
        evaluations=list(
            ImprovementEvaluation.query.filter(project_key=project_key, verdict="inconclusive")
        ),
        revisions=list(ImprovementModelRevision.query.filter(project_key=project_key)),
    )
    order = [r.as_dict() for r in ranked]

    previous_ref = getattr(state, "last_snapshot_ref", None) or None
    if case_ids is not None:
        return ranked, previous_ref, set()
    previous = None
    if previous_ref:
        try:
            previous = load_snapshot(previous_ref, store=store)
        except Exception as exc:  # noqa: BLE001 -- integrity or absence: diff from nothing
            findings.append(f"previous snapshot unreadable: {exc}")
            previous_ref = None
    unchanged = (
        previous is not None
        and previous.get("order") == order
        and previous.get("intake_pool") == intake_pool
        and previous.get("charter_digest") == charter.digest
    )
    if unchanged:
        return ranked, previous_ref, set()

    diff = compute_diff(order, previous, cases=all_cases)
    ref = write_snapshot(
        ranked,
        previous_ref=previous_ref,
        charter_digest=charter.digest,
        store=store,
        intake_pool=intake_pool,
        at=now,
        cases=all_cases,
    )
    digest = snapshot_digest(ref)
    existing_ids = {c.id for c in all_cases}
    moved_ids = list(diff["entered"])
    moved_ids += [m["case_id"] for m in diff["moved"]]
    moved_ids += [entry["case_id"] for entry in diff["left"] if entry["case_id"] in existing_ids]
    refused: set[str] = set()
    for case_id in moved_ids:
        ok = _journal(
            project_key,
            case_id,
            event="ranking_recorded",
            payload_digest=digest,
            artifact_ref=ref,
            findings=findings,
        )
        if not ok:
            refused.add(case_id)
    return ranked, ref, refused


# ---------------------------------------------------------------------------
# The single proposal
# ---------------------------------------------------------------------------


def action_id_for(case_id: str, snapshot_ref: str, action_kind: str, attempt: int = 0) -> str:
    """The deterministic action id for one (case, snapshot, kind) proposal.

    ``attempt`` is the number of intents the case already carries: an intent
    hash in any state (cancelled included) makes the adapter read a proposal
    under that id as already admitted, so a re-proposal after a cancelled
    dispatch mints the successor rather than a spent id. Attempt 0 is the
    original shape, so ids minted before this parameter existed are stable.
    """
    suffix = "" if attempt == 0 else str(attempt)
    return hashlib.sha256(f"{case_id}{snapshot_ref}{action_kind}{suffix}".encode()).hexdigest()[:16]


def _fresh_action_id(project_key: str, case_id: str, snapshot_ref: str, kind: str) -> str:
    """:func:`action_id_for` at the first attempt no intent on the case has spent."""
    from tools.improvement_control.intents import list_intents

    spent = {i.action_id for i in list_intents(project_key, case_id)}
    attempt = 0
    while action_id_for(case_id, snapshot_ref, kind, attempt) in spent:
        attempt += 1
    return action_id_for(case_id, snapshot_ref, kind, attempt)


def _last_journal_entry(project_key: str, case_id: str) -> dict | None:
    from tools.improvement_control.journal import journal_tail

    tail = journal_tail(project_key, case_id, 1)
    return tail[-1] if tail else None


def _pending_proposal(project_key: str, case_id: str) -> str | None:
    """The action id of an un-admitted ``action_proposed`` sitting last on
    the journal (what ``scheduler_adapter._unadmitted_proposal`` reads)."""
    from tools.improvement_control.intents import list_intents

    entry = _last_journal_entry(project_key, case_id)
    if not entry or entry.get("event") != "action_proposed" or not entry.get("action_id"):
        return None
    known = {i.action_id for i in list_intents(project_key, case_id)}
    return None if entry["action_id"] in known else entry["action_id"]


def _action_kind(case, experiments_by_case: dict[str, list]) -> str | None:
    states = {getattr(e, "state", None) for e in experiments_by_case.get(case.id, [])}
    if "proposed" in states and not states & {"frozen", "running"}:
        return "experiment"
    if case.state in ("observed", "investigating"):
        return "investigate"
    return None


def propose_one_action(
    project_key: str,
    ranked,
    *,
    snapshot_ref: str,
    refused_case_ids: set[str],
    findings: list[str],
) -> dict | None:
    """Propose for the first unblocked, non-busy case through
    ``tools.improvement.cmd_propose``. Returns the proposal record or None.

    The action id is ``sha256(case_id + snapshot_ref + action_kind)[:16]``,
    so a re-run of the same tick computes the same id; a case whose last
    journal entry is already that un-admitted proposal is reported as
    already proposed and nothing is written.
    """
    from tools import improvement as cli
    from tools.improvement_control.intents import list_intents

    if project_key != cli.PROJECT_KEY:
        findings.append(f"propose unavailable: valor-improve is bound to {cli.PROJECT_KEY!r}")
        return None
    experiments_by_case: dict[str, list] = {}
    for exp in _experiments(project_key):
        experiments_by_case.setdefault(getattr(exp, "case_id", None), []).append(exp)

    paused = {f.split(":", 1)[1] for f in findings if f.startswith("paused:")}
    for entry in ranked:
        if entry.blocked_by or entry.case_id in paused:
            continue
        if entry.case_id in refused_case_ids:
            findings.append(f"proposal skipped: ranking_recorded refused for {entry.case_id}")
            return None
        if any(i.state in BUSY_INTENT_STATES for i in list_intents(project_key, entry.case_id)):
            continue
        case = ImprovementCase.query.get(project_key=project_key, id=entry.case_id)
        if case is None:
            continue
        kind = _action_kind(case, experiments_by_case)
        if kind is None:
            continue
        pending = _pending_proposal(project_key, case.id)
        action_id = _fresh_action_id(project_key, case.id, snapshot_ref, kind)
        if pending is not None:
            return {
                "case_id": case.id,
                "action_id": pending,
                "kind": kind,
                "status": "already_proposed",
                "idempotent": pending == action_id,
            }
        payload = {
            "kind": kind,
            "case_id": case.id,
            "snapshot_ref": snapshot_ref,
            "position": entry.position,
            "ranking_rationale": case.ranking_rationale,
            "brief_hint": f"run valor-improve brief --case {case.id}",
        }
        return _propose_via_cli(cli, case.id, kind, action_id, payload, findings)
    return None


def _propose_via_cli(cli, case_id, kind, action_id, payload, findings) -> dict | None:
    import argparse

    action_type = "experiment" if kind == "experiment" else "investigate"
    tmp_dir = tempfile.mkdtemp(prefix="improve-propose-")
    path = Path(tmp_dir) / f"{case_id}-{action_id}.json"
    path.write_text(json.dumps(payload, sort_keys=True, indent=1), encoding="utf-8")
    ns = argparse.Namespace(
        case=case_id, action_type=action_type, payload=str(path), action_id=action_id, json=True
    )
    saved_session = os.environ.pop("AGENT_SESSION_ID", None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = cli.cmd_propose(ns)
    finally:
        if saved_session is not None:
            os.environ["AGENT_SESSION_ID"] = saved_session
        with contextlib.suppress(OSError):
            path.unlink()
            Path(tmp_dir).rmdir()
    try:
        reply = json.loads(out.getvalue().strip() or "{}")
    except ValueError:
        reply = {}
    if code != 0 or not reply.get("accepted"):
        findings.append(f"proposal refused for {case_id}: {reply.get('reason', code)}")
        return None
    return {
        "case_id": case_id,
        "action_id": action_id,
        "kind": kind,
        "status": "proposed",
        "revision": reply.get("revision"),
        "artifact_ref": reply.get("artifact_ref"),
    }


# ---------------------------------------------------------------------------
# The verdict backstop
# ---------------------------------------------------------------------------


def _verdict_backstop(project_key: str, findings: list[str]) -> int:
    from models.improvement_evaluation import ImprovementEvaluation
    from models.improvement_experiment import ImprovementExperiment

    pending = []
    for evaluation in ImprovementEvaluation.query.filter(project_key=project_key, state="complete"):
        experiment_id = getattr(evaluation, "experiment_id", None)
        if not experiment_id:
            continue
        experiment = ImprovementExperiment.query.get(project_key=project_key, id=experiment_id)
        case_id = getattr(experiment, "case_id", None) if experiment is not None else None
        if not case_id:
            continue
        case = ImprovementCase.query.get(project_key=project_key, id=case_id)
        if case is None or case.state != "evaluating":
            continue
        if evaluation.id in _json_list(getattr(case, "evaluation_ids", None)):
            continue
        pending.append(evaluation)
    if not pending:
        return 0
    from tools.improvement_experiment import apply_verdict

    applied = 0
    for evaluation in pending:
        try:
            apply_verdict(project_key, evaluation.id)
            applied += 1
        except Exception as exc:  # noqa: BLE001 -- one verdict never stops the rest
            findings.append(f"verdict backstop failed for {evaluation.id}: {exc}")
    return applied


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------


def plan_tick(
    project_key: str,
    *,
    process_spec=None,
    case_ids: list[str] | None = None,
    budget_cap=None,
    arm_run_id: str | None = None,
    probe=None,
    now: datetime | None = None,
    charter_path: str | os.PathLike | None = None,
    store=None,
) -> TickResult:
    """One planner pass. Pure controller logic: no LLM, no dispatch, no
    evaluation. See the module docstring for the step order.

    ``process_spec``, ``budget_cap``, and ``arm_run_id`` are lane 6's arm
    seam: the spec is recorded on the proposal when given, the run id tags
    the tick's findings, and ``case_ids`` restricts ranking and the proposal
    to the named opportunities. A restricted tick ranks in memory and leaves
    the ranking chain alone: no snapshot, no cursor write, no
    ``ranking_recorded``; its proposal cites the cursor's existing snapshot,
    and before any full tick it proposes nothing. ``probe`` replaces
    ``tools.improvement_resources.probe`` (tests inject a runner-bound one).
    ``charter_path`` and ``store`` are test seams; production leaves both
    default.
    """
    from models.improvement_charter import ImprovementCharter
    from models.improvement_controller_state import ImprovementControllerState

    t0 = time.time()
    now = now or datetime.now(UTC)
    result = TickResult(status="success")
    findings = result.findings
    counts = result.counts
    if arm_run_id:
        findings.append(f"arm_run_id:{arm_run_id}")
    del process_spec, budget_cap  # recorded by lane 6 from the records, not here

    # (a) charter
    try:
        loaded = (
            ImprovementCharter.load_from_file(charter_path, project_key=project_key)
            if charter_path is not None
            else ImprovementCharter.load_from_file(project_key=project_key)
        )
        if loaded is None:
            findings.append("charter file unreadable or unowned; ranking under the pinned row")
        charter = ImprovementCharter.pinned(project_key)
    except Exception as exc:  # noqa: BLE001
        findings.append(f"charter-failed: {exc}")
        charter = None
    if charter is None or not getattr(charter, "digest", None):
        result.status = "error"
        findings.append("no pinned charter; nothing ranked, nothing written")
        result.duration_seconds = round(time.time() - t0, 3)
        return result
    result.charter_digest = charter.digest

    # (b) no pause pre-read: PAUSED surfaces from transition() per case.

    state = ImprovementControllerState.get(project_key)
    recorded_digest = getattr(state, "charter_digest", None) if state is not None else None
    watermark = getattr(state, "evidence_watermark", None) if state is not None else None
    failed_steps: list[str] = []

    def step(name, fn, *args, **kwargs):
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- fail-soft per step
            logger.warning("improvement_plan: step %s failed: %s", name, exc)
            findings.append(f"{name}-failed: {exc}")
            failed_steps.append(name)
            return None
        return value

    # (c) keep-alive, unblock, amendments
    counts["kept_alive"] = step("keep_alive", _keep_alive, project_key) or 0
    counts["unblocked"] = (
        step("unblock", _unblock, project_key, probe=probe, findings=findings) or 0
    )
    counts["amendments_resolved"] = (
        step("amendments", _resolve_amendments, project_key, charter, recorded_digest, findings)
        or 0
    )

    # (d) open cases
    opened = step("open_cases", open_cases, project_key, charter, watermark=watermark)
    if opened is not None:
        result.opened = list(opened.opened)
        findings.extend(opened.findings)
        counts["cases_opened"] = len(opened.opened)
        counts["evidence_attached"] = len(opened.attached)
        counts["intake_opened"] = len(opened.intake)
        counts["evidence_scanned"] = opened.scanned
        counts["evidence_pending"] = opened.pending
        watermark = opened.watermark or watermark

    # (e) rank + snapshot + ranking_recorded
    from models.verifying_artifact_store import VerifyingArtifactStore

    store = store or VerifyingArtifactStore()
    intake_pool = [
        inv.id
        for inv in ImprovementInvestigation.query.filter(project_key=project_key, state="open")
        if getattr(inv, "kind", None) == "inspiration_intake"
    ]
    ranked_out = step(
        "ranking",
        _rank_and_record,
        project_key,
        charter,
        store=store,
        state=state,
        intake_pool=intake_pool,
        now=now,
        case_ids=case_ids,
        findings=findings,
    )
    if ranked_out is None:
        findings.append("proposal skipped: ranking step did not complete")
        counts["proposed"] = 0
    else:
        ranked, snapshot_ref, refused = ranked_out
        result.snapshot_ref = snapshot_ref
        counts["ranked"] = len(ranked)
        counts["ranking_refused"] = len(refused)
        if case_ids is None:
            state = state or ImprovementControllerState.get_or_create(project_key)
            step(
                "cursor",
                state.record,
                last_snapshot_ref=snapshot_ref,
                last_tick_at=now.isoformat(),
                charter_digest=charter.digest,
                evidence_watermark=watermark,
            )
        # (f) one proposal, always under a snapshot on record
        if snapshot_ref is None:
            findings.append("proposal skipped: restricted tick with no snapshot on record")
        else:
            result.proposal = step(
                "propose",
                propose_one_action,
                project_key,
                ranked,
                snapshot_ref=snapshot_ref,
                refused_case_ids=refused,
                findings=findings,
            )
        counts["proposed"] = 1 if result.proposal and result.proposal["status"] == "proposed" else 0

    # (g) verdict backstop
    counts["verdicts_applied"] = (
        step("verdict_backstop", _verdict_backstop, project_key, findings) or 0
    )

    if failed_steps:
        result.status = "error"
    elif [f for f in findings if not f.startswith("arm_run_id:")]:
        result.status = "partial"
    result.duration_seconds = round(time.time() - t0, 3)
    return result


def run_improvement_planner() -> dict:
    """Reflection entrypoint: one :func:`plan_tick` for the owning project.
    The owning project is ``reflections.redis_access.get_project_key()``, the
    key ``valor-improve`` is bound to; ``cmd_propose`` refuses any other.

    Gated on ``ImprovementSettings.enabled`` in the collect tick's shape:
    ``False`` returns ``status="skipped"`` and writes nothing.
    """
    t0 = time.time()
    from config.settings import settings
    from reflections.redis_access import get_project_key

    if not settings.improvement.enabled:
        return {
            "status": "skipped",
            "findings": [],
            "summary": (
                "improvement-planner-tick: disabled "
                "(ImprovementSettings.enabled is False; set IMPROVEMENT__ENABLED=true "
                "on the machine that owns this project to start planning)"
            ),
            "counts": {},
            "duration": time.time() - t0,
        }

    tick = plan_tick(get_project_key())
    proposal = tick.proposal or {}
    summary = (
        f"improvement-planner-tick: {tick.status} opened={tick.counts.get('cases_opened', 0)} "
        f"ranked={tick.counts.get('ranked', 0)} proposed={tick.counts.get('proposed', 0)}"
        + (f" (case {proposal.get('case_id')} {proposal.get('kind')})" if proposal else "")
    )
    return {
        "status": tick.status,
        "findings": list(tick.findings),
        "summary": summary,
        "counts": dict(tick.counts),
        "snapshot_ref": tick.snapshot_ref,
        "duration": time.time() - t0,
    }
