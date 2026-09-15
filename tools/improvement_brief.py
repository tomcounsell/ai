"""The research brief: everything a dispatched research session reads before
it investigates anything (lane 5, #3217).

:func:`build_brief` renders, in this order and only this order: the pinned
charter's full text verbatim (its first non-blank line is the brief's first
non-blank line), a line naming the charter's version and digest, the case
(title, summary, priority area, ranking rationale, ranking position and
factors from the latest snapshot when one exists), the case's evidence rows,
prior answers in the same priority area (resolved investigations'
interpretations and rejected cases with their reason and evaluation ids),
the case's open investigations, the intake pool for the area (open
``inspiration_intake`` investigations with no case), the charter §9
resolution rule, the claim rule, this lane's candidate envelope, and the
``valor-improve`` subcommands the session may use.

Bounded: evidence is capped at :data:`BRIEF_MAX_EVIDENCE` rows and prior
answers at :data:`BRIEF_MAX_PRIOR_ANSWERS`, newest first, and the brief says
when it truncated. Deterministic: every list is sorted by recency then id, so
two renders of an unchanged case are byte-identical.

The static sections live in :data:`BRIEF_TEMPLATE`; its SHA-256
(:func:`brief_template_digest`) is the ``planner_prompt_digest`` on every
``ResearchProcessSpec`` this lane writes, so a wording change here is a
process change the recursion comparison can see.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from models.improvement_case import ImprovementCase
from models.improvement_charter import ImprovementCharter
from models.improvement_evidence import ImprovementEvidence
from models.improvement_investigation import ImprovementInvestigation

#: Caps, newest first. The brief states the truncation when it applies.
BRIEF_MAX_EVIDENCE = 40
BRIEF_MAX_PRIOR_ANSWERS = 20

NO_EVIDENCE_LINE = "No evidence rows are attached to this case"
NO_SNAPSHOT_LINE = "no ranking snapshot yet"

RESOLUTION_RULE = (
    "Charter §9 resolution rule: evidence and investigation first, then a guarded "
    "provisional assumption (charter passage, evidence, confidence, consequence, and the "
    "observation that would overturn it), then deferral plus an amendment request "
    "(`propose-amendment`) when the decision depends on authority the charter has not "
    "granted. An assumption cannot redefine the intended outcome, erase a requirement, "
    "grant authority, or increase a budget. No question goes to Tom on this route."
)

CLAIM_RULE = (
    "Claim rule: a claim is `{claim, url, retrieved_at}` with a non-blank claim, an "
    "http(s) URL, and a parseable ISO-8601 `retrieved_at`. Anything short of that is kept "
    "as a note (`is_claim: false`), never dropped and never promoted to a claim."
)

ENVELOPE = (
    "Candidate envelope for this lane: `retrieval_parameters` only. Keys and ranges: "
    "`limit` 1..50, `rrf_k` 1..200, `min_rrf_score` 0.0..1.0. The incumbent is exactly "
    '{"limit": 10}. Prior answer on this envelope: #2082, '
    "docs/features/hybrid-retrieval-eval.md. A candidate identical to the incumbent, a key "
    "outside the envelope, or a value outside its range is refused at freeze."
)

SUBCOMMANDS = (
    "valor-improve brief --case ID",
    "valor-improve investigation open --kind K --case ID --uncertainty ... --query ... "
    "--decision-affected ... --expected-information-value ...",
    "valor-improve investigation record --id ID --claims JSON_OR_@file",
    "valor-improve investigation resolve --id ID --interpretation ... "
    "[--assumption ... --assumption-detail JSON] [--disposition ... --resource-name ...]",
    "valor-improve investigation list [--case ID]",
    "valor-improve case open [--evidence-ids A,B]",
    "valor-improve revise-model --case ID --summary ... --rationale ... --prediction ...",
    "valor-improve propose --case ID --action-type investigate --payload FILE",
    "valor-improve propose-amendment --case ID --request ...",
    "valor-improve experiment freeze --case ID",
    "valor-improve experiment evaluate --id ID",
    "valor-improve experiment show --id ID",
    "valor-improve report --case ID",
)

#: The static text of the brief, hashed as ``planner_prompt_digest``.
BRIEF_TEMPLATE = "\n".join(
    [
        "## Resolution rule",
        RESOLUTION_RULE,
        "",
        "## Claim rule",
        CLAIM_RULE,
        "",
        "## Candidate envelope",
        ENVELOPE,
        "",
        "## Subcommands",
        *[f"- {line}" for line in SUBCOMMANDS],
    ]
)


def brief_template_text() -> str:
    return BRIEF_TEMPLATE


def brief_template_digest() -> str:
    """SHA-256 hex of :data:`BRIEF_TEMPLATE`."""
    return hashlib.sha256(BRIEF_TEMPLATE.encode()).hexdigest()


def charter_text(charter) -> str:
    """The charter's full text. A queried row hands back its ``$CF:`` store
    reference for the ``ContentField``; resolve it through the verifying store
    (an integrity failure propagates: a brief never opens with unverified
    charter bytes)."""
    raw = getattr(charter, "text", None) or ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if raw.startswith("$CF:"):
        store = ImprovementCharter._meta.fields["text"].store
        raw = store.load(raw).decode("utf-8")
    return raw


def _load_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _stamp(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat(timespec="seconds")
    return str(value or "")


def _recency_key(row):
    value = getattr(row, "observed_at", None) or getattr(row, "created_at", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return (value.timestamp(), row.id)
    return (0.0, row.id)


def _one_line(text, limit: int = 400) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _ranking_lines(case_id: str, project_key: str) -> list[str]:
    """Position and factors from the latest snapshot, through the ranking
    module when it is importable and has one; the sentinel otherwise."""
    try:
        from tools.improvement_ranking import latest_snapshot
    except ImportError:
        return [f"Ranking: {NO_SNAPSHOT_LINE}"]
    try:
        snapshot = latest_snapshot(project_key)
    except Exception:
        return [f"Ranking: {NO_SNAPSHOT_LINE}"]
    if snapshot is None:
        return [f"Ranking: {NO_SNAPSHOT_LINE}"]

    def pick(name):
        if isinstance(snapshot, dict):
            return snapshot.get(name)
        return getattr(snapshot, name, None)

    order = list(pick("order") or [])
    factors = pick("factors") or {}
    if case_id not in order:
        return [f"Ranking: not in the latest snapshot ({len(order)} case(s) ranked)"]
    position = order.index(case_id) + 1
    lines = [f"Ranking: position {position} of {len(order)}"]
    mine = factors.get(case_id) if isinstance(factors, dict) else None
    if isinstance(mine, dict):
        lines.append("Factors: " + ", ".join(f"{k}={mine[k]}" for k in sorted(mine)))
    return lines


def _evidence_section(case) -> list[str]:
    ids = [str(i) for i in _load_list(case.evidence_ids)]
    rows = []
    for eid in ids:
        try:
            row = ImprovementEvidence.query.get(project_key=case.project_key, id=eid)
        except Exception:
            row = None
        if row is not None:
            rows.append(row)
    if not rows:
        return [NO_EVIDENCE_LINE]
    rows.sort(key=_recency_key, reverse=True)
    total = len(rows)
    shown = rows[:BRIEF_MAX_EVIDENCE]
    lines = []
    if total > BRIEF_MAX_EVIDENCE:
        lines.append(f"({total} rows, newest first, showing {BRIEF_MAX_EVIDENCE} of {total})")
    else:
        lines.append(f"({total} row(s), newest first)")
    for row in shown:
        lines.append(
            f"- [{row.kind}] {_stamp(row.observed_at or row.created_at)} {row.id}: "
            f"{_one_line(row.text)}"
        )
    return lines


def _prior_answers_section(case) -> list[str]:
    area_cases = {
        c.id: c
        for c in ImprovementCase.query.filter(
            project_key=case.project_key, priority_area=case.priority_area
        )
    }
    entries: list[tuple[tuple[float, str], str]] = []
    for row in ImprovementInvestigation.query.filter(
        project_key=case.project_key, state="resolved"
    ):
        if row.case_id in area_cases:
            entries.append(
                (
                    _recency_key(row),
                    f"- investigation {row.id} ({row.kind}, case {row.case_id}): "
                    f"{_one_line(row.interpretation)}",
                )
            )
    for other in area_cases.values():
        if other.state == "rejected" and other.id != case.id:
            evals = ", ".join(str(e) for e in _load_list(other.evaluation_ids)) or "none"
            entries.append(
                (
                    _recency_key(other),
                    f"- rejected case {other.id} ({_one_line(other.title, 80)}): "
                    f"{_one_line(other.rejected_reason)} [evaluations: {evals}]",
                )
            )
    if not entries:
        return [f"No prior answers in priority area {case.priority_area}"]
    entries.sort(key=lambda e: e[0], reverse=True)
    total = len(entries)
    lines = []
    if total > BRIEF_MAX_PRIOR_ANSWERS:
        lines.append(
            f"({total} entries, newest first, showing {BRIEF_MAX_PRIOR_ANSWERS} of {total})"
        )
    lines.extend(text for _, text in entries[:BRIEF_MAX_PRIOR_ANSWERS])
    return lines


def _open_investigations_section(case) -> list[str]:
    rows = [
        r
        for state in ("open", "awaiting_authorization")
        for r in ImprovementInvestigation.query.filter(project_key=case.project_key, state=state)
        if r.case_id == case.id
    ]
    if not rows:
        return ["No open investigations for this case"]
    rows.sort(key=_recency_key, reverse=True)
    return [
        f"- {r.id} ({r.kind}, {r.state}, stage {r.stage or 'draft'}): "
        f"{_one_line(r.uncertainty)} | query: {_one_line(r.query, 200)}"
        for r in rows
    ]


def _intake_pool_section(case) -> list[str]:
    rows = [
        r
        for r in ImprovementInvestigation.query.filter(
            project_key=case.project_key, kind="inspiration_intake"
        )
        if r.state == "open" and not r.case_id
    ]
    if not rows:
        return ["No inspiration waiting in the intake pool"]
    rows.sort(key=_recency_key, reverse=True)
    return [f"- {r.id}: {_one_line(r.query, 200)}" for r in rows]


def build_brief(case_id: str, project_key: str) -> str:
    """Render the brief for ``case_id``. Raises ``LookupError`` with
    ``CASE_NOT_FOUND`` or ``CHARTER_NOT_PINNED``; never seeds a charter row."""
    try:
        case = ImprovementCase.query.get(project_key=project_key, id=case_id)
    except Exception:
        case = None
    if case is None:
        raise LookupError(f"CASE_NOT_FOUND: no case {case_id} in project {project_key}")
    charter = ImprovementCharter.pinned(project_key)
    if charter is None:
        raise LookupError(f"CHARTER_NOT_PINNED: no charter row in project {project_key}")

    parts: list[str] = [charter_text(charter).rstrip("\n"), ""]
    parts.append(f"Charter version {charter.version}, digest {charter.digest}")
    parts.append("")

    parts.append(f"## Case {case.id}")
    parts.append(f"Title: {_one_line(case.title, 200)}")
    parts.append(
        f"State: {case.state}" + (f" (blocked by {case.blocked_by})" if case.blocked_by else "")
    )
    parts.append(f"Priority area: {case.priority_area}")
    parts.append(f"Summary: {_one_line(case.summary, 2000)}")
    parts.append(f"Ranking rationale: {_one_line(case.ranking_rationale, 600)}")
    if case.charter_digest and case.charter_digest != charter.digest:
        parts.append(
            f"Charter digest on the case ({case.charter_digest}) differs from the pinned "
            "charter; propose will refuse until the tick re-ranks it"
        )
    parts.extend(_ranking_lines(case.id, project_key))
    parts.append("")

    parts.append("## Evidence")
    parts.extend(_evidence_section(case))
    parts.append("")

    parts.append(f"## Prior answers in priority area {case.priority_area}")
    parts.extend(_prior_answers_section(case))
    parts.append("")

    parts.append("## Open investigations for this case")
    parts.extend(_open_investigations_section(case))
    parts.append("")

    parts.append(f"## Intake pool for {case.priority_area}")
    parts.extend(_intake_pool_section(case))
    parts.append("")

    parts.append(BRIEF_TEMPLATE)
    return "\n".join(parts) + "\n"
