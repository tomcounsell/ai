"""Investigations: the research session's bounded acts of finding something out
(lane 5, #3217).

Every function here is the Python seam behind ``valor-improve investigation
open|record|resolve|list``. Expected refusals come back as reason codes on an
:class:`Outcome`; only a caller's own boundary mistakes raise.

**The eight kinds and what each records.**

- ``web_research``: the query, the URLs read, their retrieval dates, and the
  claims taken from them; run with ``WebSearch`` / ``WebFetch``.
- ``memory_retrieval``: the ``memory_search`` query and the memory ids read.
- ``trace_analysis``: the session or event ids read and what they showed.
- ``probe``: the command run (bounded, recorded verbatim) and its observed
  result.
- ``resource_acquisition``: provider, documentation URLs and dates, price,
  the terms that matter to charter §7 (training on inputs, retention), what
  an adapter would cost, and one disposition from
  :data:`RESOURCE_DISPOSITIONS`.
- ``inspiration_intake``: the source URL, the extraction route
  (``valor-youtube-transcribe``, ``valor-ingest``, ``WebFetch``), the extracted
  substance, and whether the source was accessible. It is the one kind that
  opens with no case: the intake pool waits for a session already dispatched
  in the same priority area.
- ``skill_acquisition``: the observed gap (evidence ids), the candidates found
  with URLs and dates, the vetting result, the integration made, and the
  evaluation disposition (in this lane always ``deferred: no agent-run arm``).
- ``charter_amendment``: opened through lane 3's ``propose-amendment``; the
  only kind whose ``state`` may be ``awaiting_authorization``, and the tick
  resolves every awaiting row when the pinned charter digest changes
  (:func:`resolve_awaiting_on_new_digest`).

**The claim rule.** A claim is ``{claim, url, retrieved_at}`` with non-blank
text, an ``http(s)`` URL, and a parseable ISO-8601 ``retrieved_at``. Anything
short of that is stored under ``notes`` in the ``claims`` JSON with
``is_claim: false``: never dropped, never promoted. The ``claims`` field is a
JSON object ``{"claims": [...], "notes": [...], "disposition": ...,
"resource_name": ...}``; :func:`disposition_of` reads the last two.

**The assumption guard.** A ``provisional_assumption`` needs a complete
``assumption_detail`` (``charter_passage``, ``confidence``, ``consequence``,
``overturning_observation``), and its ``consequence`` is refused when it
matches any of the four patterns charter §9 forbids an assumption to do:
redefine the intended outcome, erase a requirement, grant authority, or
increase a budget (:data:`ASSUMPTION_REFUSAL_PATTERNS`). The refusal tells
the session to defer the decision and use ``propose-amendment``. This is a
text rule and catches only the phrasing it names; the ``serves_charter``
judge and the assumption digest are the backstops.

**Case writes.** ``ImprovementCase.state`` moves only through the control
journal: the first investigation on an ``observed`` case runs
``journal.set_state`` under the case's own lease and then
``projection.apply``; ``resolve()`` never changes case state. The durable
distillate (an assumption's summary, a vault request, an amendment request)
is appended to ``case.summary`` with a plain ``save()`` because the case is
immortal and this row carries a 30-day TTL. A ``vault_request_written``
disposition also sets ``case.blocked_by = "vault:{resource_name}"`` and
refuses any name outside ``tools.improvement_resources.RESOURCES``.

**Schema decisions.** No new index: ``stage``, ``prior_answers``, and the
disposition envelope are read off rows the caller already holds by id or by
the ``state`` index. ``resolved_at`` is a plain ``DatetimeField`` the digest
reads as its watermark.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

from models.improvement_case import ImprovementCase
from models.improvement_investigation import (
    INVESTIGATION_KINDS,
    INVESTIGATION_STATES,
    ImprovementInvestigation,
)

logger = logging.getLogger(__name__)

#: The ten stages, in lifecycle order. The first seven advance one at a time;
#: the last three are exits reachable from any non-terminal stage.
LINEAR_STAGES: tuple[str, ...] = (
    "draft",
    "deduplicated",
    "policy_checked",
    "running",
    "recorded",
    "interpreted",
    "applied",
)
EXIT_STAGES: tuple[str, ...] = ("cancelled", "superseded", "failed")
INVESTIGATION_STAGES: tuple[str, ...] = LINEAR_STAGES + EXIT_STAGES
_TERMINAL_STAGES: frozenset[str] = frozenset(EXIT_STAGES) | {"applied"}

#: How a ``resource_acquisition`` investigation ends.
RESOURCE_DISPOSITIONS: tuple[str, ...] = (
    "prepared",
    "keyless_integrated",
    "vault_request_written",
    "unsuitable",
)

#: The four ``assumption_detail`` keys that make an assumption checkable.
REQUIRED_ASSUMPTION_KEYS: tuple[str, ...] = (
    "charter_passage",
    "confidence",
    "consequence",
    "overturning_observation",
)

#: The charter §9 rule as a text rule: a consequence phrased in any of these
#: ways is not something an assumption may do. Each pattern is named by the
#: charter clause it enforces.
ASSUMPTION_REFUSAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "redefines the intended outcome",
        re.compile(r"\b(redefin\w*|replac\w*|narrow\w*)\b.*\b(outcome|goal|north star)\b", re.I),
    ),
    (
        "erases a requirement",
        re.compile(
            r"\b(requirement|rule|constraint)\b.*\b(no longer|dropped|removed|waived|erased)\b"
            r"|\b(drop|remove|waive|erase)\w*\b.*\b(requirement|rule|constraint)\b",
            re.I,
        ),
    ),
    (
        "grants authority",
        re.compile(
            r"\b(authoriz\w*|permitted|allowed)\b.*\b(itself|session|controller|valor)\b"
            r"|\b(session|controller|valor)\b.*\b(is|are) (authorized|permitted|allowed)\b",
            re.I,
        ),
    ),
    (
        "increases a budget",
        re.compile(
            r"\b(budget|ceiling|cap|spend)\b.*\b(rais\w*|increas\w*|doubl\w*|lift\w*)\b"
            r"|\b(rais\w*|increas\w*|doubl\w*|lift\w*)\b.*\b(budget|ceiling|cap|spend)\b",
            re.I,
        ),
    ),
)

#: The vault item title the adapter expects for each resource that needs one.
#: The probe matches titles by ``_VAULT_TITLE_KEYWORDS``, so the request text
#: states the exact title Tom must use.
VAULT_ITEM_TITLES: dict[str, str] = {"meta_model_api": "Meta Model API key"}

_PREFIX_WORDS = 8


class InvestigationRefusedError(Exception):
    """A :func:`record_claims` refusal, carrying its reason code."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


@dataclass(frozen=True)
class Outcome:
    """The result of one investigation write. Never an exception for an
    expected refusal: ``reason`` is the code and ``message`` the sentence."""

    accepted: bool
    reason: str
    investigation_id: str | None = None
    message: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _refuse(reason: str, message: str = "", investigation_id: str | None = None) -> Outcome:
    return Outcome(False, reason, investigation_id, message or reason)


def _now() -> datetime:
    return datetime.now(UTC)


def normalized_prefix(text: str | None, words: int = _PREFIX_WORDS) -> str:
    """The first ``words`` alphanumeric tokens of ``text``, lower-cased and
    space-joined: the string the novelty check compares."""
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return " ".join(tokens[:words])


def _load_json(raw, default):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _claims_envelope(row) -> dict:
    """The ``claims`` field as an object, absorbing lane 3's bare-list shape."""
    parsed = _load_json(getattr(row, "claims", None), {})
    if isinstance(parsed, list):
        parsed = {"claims": parsed}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault("claims", [])
    parsed.setdefault("notes", [])
    return parsed


def disposition_of(row) -> tuple[str | None, str | None]:
    """``(disposition, resource_name)`` recorded on a resolved
    ``resource_acquisition`` row, or ``(None, None)``."""
    env = _claims_envelope(row)
    return env.get("disposition"), env.get("resource_name")


def _find(investigation_id: str):
    """Rows are keyed by ``(id, project_key)``; the CLI hands over only the id,
    so resolve across the partitions this process can see."""
    if not investigation_id:
        return None
    try:
        rows = list(ImprovementInvestigation.query.filter(id=investigation_id))
    except Exception:
        return None
    return rows[0] if rows else None


def _case(project_key: str, case_id: str | None):
    """The case row, or None for a missing id (an id popoto cannot key raises
    ``ModelException`` inside ``get``; that is a missing case too)."""
    if not case_id:
        return None
    try:
        return ImprovementCase.query.get(project_key=project_key, id=case_id)
    except Exception:
        return None


def _append_summary(case, text: str) -> None:
    current = (case.summary or "").rstrip()
    case.summary = f"{current}\n\n{text}" if current else text
    case.save()


def _lease(project_key: str, case_id: str):
    from config.settings import settings
    from tools.improvement_control import keys
    from tools.improvement_control.lease import default_lease

    lease = default_lease()
    key = keys.lease_key(project_key, case_id)
    generation = lease.acquire(key, ttl=settings.improvement.lease_ttl_seconds)
    return lease, key, generation


def _prior_answers(project_key: str, case_id: str | None, query: str) -> list[str]:
    """The novelty check: resolved investigations on the same case whose
    query shares the normalized prefix, and rejected cases whose
    ``dedup_identity`` normalizes to it. A string heuristic (Risk 2)."""
    identity = normalized_prefix(query)
    found: list[str] = []
    if not identity:
        return found
    if case_id:
        for row in ImprovementInvestigation.query.filter(project_key=project_key, state="resolved"):
            if row.case_id == case_id and normalized_prefix(row.query) == identity:
                found.append(row.id)
    for case in ImprovementCase.query.filter(project_key=project_key, state="rejected"):
        dedup = case.dedup_identity or ""
        if dedup and (dedup == identity or normalized_prefix(dedup) == identity):
            found.append(case.id)
    return sorted(found)


def open_investigation(
    project_key: str,
    *,
    kind: str,
    case_id: str | None,
    uncertainty: str,
    query: str,
    decision_affected: str,
    expected_information_value: str,
    expires_at: datetime | None = None,
    state: str = "open",
) -> Outcome:
    """Open one investigation on ``case_id`` and journal ``investigation_opened``.

    Refuses ``INVALID_KIND``, ``INVALID_STATE``, ``AWAITING_REQUIRES_AMENDMENT``
    (``awaiting_authorization`` on any kind but ``charter_amendment``),
    ``CASE_REQUIRED`` (every kind but ``inspiration_intake`` needs a case),
    ``CASE_NOT_FOUND``, ``CASE_BUSY`` (the case lease is held), and the
    journal's own reason on a refused transition. Nothing is written on any
    refusal. ``stage`` opens at ``deduplicated`` when the novelty check found
    prior answers and ``draft`` otherwise; an ``observed`` case moves to
    ``investigating`` through ``set_state`` and ``projection.apply``.
    """
    if kind not in INVESTIGATION_KINDS:
        return _refuse("INVALID_KIND", f"{kind!r} is not one of {INVESTIGATION_KINDS}")
    if state not in INVESTIGATION_STATES:
        return _refuse("INVALID_STATE", f"{state!r} is not one of {INVESTIGATION_STATES}")
    if state == "awaiting_authorization" and kind != "charter_amendment":
        return _refuse(
            "AWAITING_REQUIRES_AMENDMENT",
            "only a charter_amendment investigation may await authorization",
        )
    if not case_id and kind != "inspiration_intake":
        return _refuse("CASE_REQUIRED", f"a {kind} investigation needs a case id")
    case = _case(project_key, case_id)
    if case_id and case is None:
        return _refuse("CASE_NOT_FOUND", f"no case {case_id} in project {project_key}")

    from models.improvement_charter import ImprovementCharter

    pinned = ImprovementCharter.pinned(project_key)
    prior = _prior_answers(project_key, case_id, query)
    fields = dict(
        project_key=project_key,
        created_at=_now(),
        kind=kind,
        state=state,
        case_id=case_id,
        uncertainty=uncertainty,
        query=query,
        decision_affected=decision_affected,
        expected_information_value=expected_information_value,
        expires_at=expires_at,
        stage="deduplicated" if prior else "draft",
        prior_answers=json.dumps(prior),
        charter_digest=pinned.digest if pinned is not None else None,
    )

    if case is None:
        row = ImprovementInvestigation.create(**fields)
        return Outcome(True, "OK", row.id, "opened", {"stage": row.stage, "prior_answers": prior})

    from tools.improvement_control.journal import read_head, set_state, transition
    from tools.improvement_control.projection import apply

    lease, key, generation = _lease(project_key, case_id)
    if generation is None:
        return _refuse("CASE_BUSY", f"case {case_id} lease is held")
    try:
        head = read_head(project_key, case_id)
        payload = json.dumps(
            {"kind": kind, "case_id": case_id, "query": query, "state": state}, sort_keys=True
        )
        result = transition(
            project_key,
            case_id,
            expected_revision=head.revision if head is not None else 0,
            generation=generation,
            event="investigation_opened",
            payload_digest="sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
        )
        if not result.accepted:
            return _refuse(result.reason, f"journal refused investigation_opened: {result.reason}")
        row = ImprovementInvestigation.create(**fields)
        current_state = (head.state if head is not None and head.state else None) or case.state
        if current_state == "observed":
            moved = set_state(
                project_key,
                case_id,
                generation=generation,
                state="investigating",
                by="open_investigation",
            )
            if not moved.accepted:
                logger.warning(
                    "[improvement] case %s stays %s: set_state refused %s",
                    case_id,
                    current_state,
                    moved.reason,
                )
    finally:
        lease.release(key, generation)
    apply(project_key, case_id)

    if kind == "charter_amendment":
        _append_summary(
            _case(project_key, case_id),
            f"Amendment request (investigation {row.id}): {query}",
        )
    return Outcome(True, "OK", row.id, "opened", {"stage": row.stage, "prior_answers": prior})


def _parse_retrieved_at(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_http_url(value) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def classify_claim(entry) -> tuple[bool, str]:
    """``(is_claim, reason)``: why an entry is a note when it is one."""
    if not isinstance(entry, dict):
        return False, "not an object"
    if not isinstance(entry.get("claim"), str) or not entry["claim"].strip():
        return False, "blank claim"
    if not _is_http_url(entry.get("url")):
        return False, "missing or non-http url"
    if not _parse_retrieved_at(entry.get("retrieved_at")):
        return False, "missing or unparseable retrieved_at"
    return True, ""


def record_claims(investigation_id: str, claims, sources=None) -> int:
    """Store every entry of ``claims`` on the investigation and return how
    many were stored (claims plus notes; nothing is dropped).

    ``None`` or a non-list raises ``ValueError`` at the boundary. The row is
    re-read and the call refuses with :class:`InvestigationRefusedError`
    ``INVESTIGATION_NOT_FOUND`` or ``INVESTIGATION_NOT_OPEN`` (Race 3) before
    anything is written; on a live open row an empty list is a no-op
    returning 0. Valid
    claims also land in ``sources``, as do the explicit ``sources`` entries.
    ``stage`` moves to ``recorded`` when the row is earlier in the order.
    """
    if claims is None:
        raise ValueError("claims is None; pass a list (an empty list is a no-op)")
    if not isinstance(claims, list):
        raise ValueError(f"claims must be a list, not {type(claims).__name__}")
    if sources is not None and not isinstance(sources, list):
        raise ValueError("sources must be a list when given")

    row = _find(investigation_id)
    if row is None:
        raise InvestigationRefusedError(
            "INVESTIGATION_NOT_FOUND", f"no investigation {investigation_id}"
        )
    if row.state != "open":
        raise InvestigationRefusedError(
            "INVESTIGATION_NOT_OPEN",
            f"investigation {investigation_id} is {row.state}; open a new one citing it",
        )
    if not claims and not sources:
        return 0

    env = _claims_envelope(row)
    stored_sources = _load_json(row.sources, [])
    if not isinstance(stored_sources, list):
        stored_sources = []
    known_urls = {s.get("url") for s in stored_sources if isinstance(s, dict)}
    for entry in claims:
        is_claim, why = classify_claim(entry)
        base = entry if isinstance(entry, dict) else {"claim": str(entry)}
        record = {
            "claim": base.get("claim"),
            "url": base.get("url"),
            "retrieved_at": base.get("retrieved_at"),
            "is_claim": is_claim,
        }
        if is_claim:
            env["claims"].append(record)
            if record["url"] not in known_urls:
                known_urls.add(record["url"])
                stored_sources.append(
                    {
                        "url": record["url"],
                        "retrieved_at": record["retrieved_at"],
                        "title": base.get("title") or "",
                    }
                )
        else:
            record["note_reason"] = why
            env["notes"].append(record)
    for source in sources or []:
        if isinstance(source, dict) and source.get("url") and source["url"] not in known_urls:
            known_urls.add(source["url"])
            stored_sources.append(
                {
                    "url": source["url"],
                    "retrieved_at": source.get("retrieved_at"),
                    "title": source.get("title") or "",
                }
            )
    row.claims = json.dumps(env, sort_keys=True)
    row.sources = json.dumps(stored_sources)
    if row.stage in LINEAR_STAGES and LINEAR_STAGES.index(row.stage) < LINEAR_STAGES.index(
        "recorded"
    ):
        row.stage = "recorded"
    row.save()
    return len(claims)


def _coerce_detail(detail) -> dict | None:
    if detail is None:
        return None
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    return detail if isinstance(detail, dict) else None


def check_assumption(detail) -> Outcome:
    """The assumption guard on its own: completeness, then the four patterns."""
    parsed = _coerce_detail(detail)
    if parsed is None:
        return _refuse(
            "ASSUMPTION_DETAIL_INCOMPLETE",
            "assumption_detail must be an object carrying " + ", ".join(REQUIRED_ASSUMPTION_KEYS),
        )
    missing = [k for k in REQUIRED_ASSUMPTION_KEYS if not str(parsed.get(k) or "").strip()]
    if missing:
        return _refuse(
            "ASSUMPTION_DETAIL_INCOMPLETE", "assumption_detail lacks " + ", ".join(missing)
        )
    consequence = str(parsed["consequence"])
    for name, pattern in ASSUMPTION_REFUSAL_PATTERNS:
        if pattern.search(consequence):
            return _refuse(
                "ASSUMPTION_EXCEEDS_AUTHORITY",
                f"the consequence {name}; an assumption cannot (charter §9). Defer the "
                "decision and use propose-amendment.",
            )
    return Outcome(True, "OK", None, "assumption admissible", {"detail": parsed})


def _vault_request_text(investigation_id: str, resource_name: str) -> str:
    from tools.improvement_resources import VAULT

    title = VAULT_ITEM_TITLES.get(
        resource_name, resource_name.replace("_", " ").title() + " credential"
    )
    return (
        f"Vault request (investigation {investigation_id}): add the item '{title}' to the "
        f"{VAULT} vault. Fingerprint field: 'credential', verified by SHA-256 fingerprint "
        f"through tools.improvement_resources.probe (resource {resource_name}). Terms: the "
        "credential is confined to open-source work under charter §7 and is never used on a "
        "client path. No controller module places it."
    )


def resolve(
    investigation_id: str,
    *,
    interpretation: str,
    provisional_assumption: str | None = None,
    assumption_detail=None,
    disposition: str | None = None,
    resource_name: str | None = None,
) -> Outcome:
    """Close the investigation: ``stage="interpreted"``, ``state="resolved"``,
    ``resolved_at`` now. Case state is untouched.

    Refuses ``INVESTIGATION_NOT_FOUND``, ``INVESTIGATION_NOT_OPEN`` (only
    ``open`` and ``awaiting_authorization`` rows resolve),
    ``ASSUMPTION_DETAIL_INCOMPLETE``, ``ASSUMPTION_EXCEEDS_AUTHORITY``,
    ``INVALID_DISPOSITION``, ``RESOURCE_NAME_REQUIRED``, ``UNKNOWN_RESOURCE``,
    and ``CASE_NOT_FOUND`` (a vault request needs the case to block). An
    assumption appends its summary to ``case.summary``; a
    ``vault_request_written`` disposition appends the request text and sets
    ``case.blocked_by``.
    """
    row = _find(investigation_id)
    if row is None:
        return _refuse("INVESTIGATION_NOT_FOUND", f"no investigation {investigation_id}")
    if row.state not in ("open", "awaiting_authorization"):
        return _refuse(
            "INVESTIGATION_NOT_OPEN", f"investigation {investigation_id} is {row.state}", row.id
        )

    detail: dict | None = None
    if provisional_assumption:
        guard = check_assumption(assumption_detail)
        if not guard.accepted:
            return Outcome(False, guard.reason, row.id, guard.message)
        detail = guard.extra["detail"]

    if disposition is not None and disposition not in RESOURCE_DISPOSITIONS:
        return _refuse(
            "INVALID_DISPOSITION",
            f"{disposition!r} is not one of {RESOURCE_DISPOSITIONS}",
            row.id,
        )
    case = _case(row.project_key, row.case_id)
    if disposition == "vault_request_written":
        from tools.improvement_resources import RESOURCES

        if not resource_name:
            return _refuse(
                "RESOURCE_NAME_REQUIRED", "vault_request_written needs --resource-name", row.id
            )
        if resource_name not in RESOURCES:
            return _refuse(
                "UNKNOWN_RESOURCE", f"{resource_name!r} is not one of {RESOURCES}", row.id
            )
        if case is None:
            return _refuse("CASE_NOT_FOUND", "a vault request needs a case to block", row.id)

    env = _claims_envelope(row)
    if disposition is not None:
        env["disposition"] = disposition
        env["resource_name"] = resource_name
    row.claims = json.dumps(env, sort_keys=True)
    row.interpretation = interpretation
    row.provisional_assumption = provisional_assumption or None
    row.assumption_detail = json.dumps(detail, sort_keys=True) if detail is not None else None
    row.stage = "interpreted"
    row.state = "resolved"
    row.resolved_at = _now()
    row.save()

    if case is not None and provisional_assumption:
        _append_summary(
            case,
            f"Provisional assumption (investigation {row.id}): {provisional_assumption} "
            f"[charter: {detail['charter_passage']}; confidence: {detail['confidence']}; "
            f"consequence: {detail['consequence']}; overturned by: "
            f"{detail['overturning_observation']}]",
        )
        case = _case(row.project_key, row.case_id)
    if case is not None and disposition == "vault_request_written":
        case.blocked_by = f"vault:{resource_name}"
        _append_summary(case, _vault_request_text(row.id, resource_name))

    return Outcome(
        True,
        "OK",
        row.id,
        "resolved",
        {
            "disposition": disposition,
            "blocked_by": f"vault:{resource_name}"
            if resource_name and disposition == "vault_request_written"
            else None,
        },
    )


def transition_investigation(
    investigation_id: str, stage: str | None = None, *, state: str | None = None
) -> Outcome:
    """Advance ``stage`` by exactly one step in :data:`LINEAR_STAGES`, or into
    an exit stage from any non-terminal stage; move ``state`` when given.

    Refuses ``INVESTIGATION_NOT_FOUND``, ``UNKNOWN_STAGE``, ``STAGE_TERMINAL``
    (nothing follows an exit or ``applied``), ``STAGE_SKIPPED`` (the linear
    order admits no jump), ``INVALID_STATE``, and
    ``AWAITING_REQUIRES_AMENDMENT``.
    """
    row = _find(investigation_id)
    if row is None:
        return _refuse("INVESTIGATION_NOT_FOUND", f"no investigation {investigation_id}")
    if stage is None and state is None:
        return _refuse("INVALID_ARGUMENT", "pass a stage, a state, or both", row.id)
    if stage is not None:
        if stage not in INVESTIGATION_STAGES:
            return _refuse(
                "UNKNOWN_STAGE", f"{stage!r} is not one of {INVESTIGATION_STAGES}", row.id
            )
        current = row.stage or "draft"
        if current in _TERMINAL_STAGES:
            return _refuse("STAGE_TERMINAL", f"stage {current} admits no successor", row.id)
        if (
            stage in LINEAR_STAGES
            and LINEAR_STAGES.index(stage) != LINEAR_STAGES.index(current) + 1
        ):
            return _refuse(
                "STAGE_SKIPPED",
                f"{current} advances to {LINEAR_STAGES[LINEAR_STAGES.index(current) + 1]}, "
                f"not {stage}",
                row.id,
            )
    if state is not None:
        if state not in INVESTIGATION_STATES:
            return _refuse(
                "INVALID_STATE", f"{state!r} is not one of {INVESTIGATION_STATES}", row.id
            )
        if state == "awaiting_authorization" and row.kind != "charter_amendment":
            return _refuse(
                "AWAITING_REQUIRES_AMENDMENT",
                "only a charter_amendment investigation may await authorization",
                row.id,
            )
    if stage is not None:
        row.stage = stage
    if state is not None:
        row.state = state
    row.save()
    return Outcome(True, "OK", row.id, "advanced", {"stage": row.stage, "state": row.state})


def resolve_awaiting_on_new_digest(project_key: str, new_digest: str) -> list[str]:
    """The amendment-resolution hook the planner tick calls when the pinned
    charter digest changed: every ``awaiting_authorization`` row not already
    carrying ``new_digest`` resolves with the interpretation
    ``charter digest changed to {new_digest}``. Returns the resolved ids."""
    resolved: list[str] = []
    for row in ImprovementInvestigation.query.filter(
        project_key=project_key, state="awaiting_authorization"
    ):
        if row.charter_digest == new_digest:
            continue
        row.interpretation = f"charter digest changed to {new_digest}"
        row.stage = "interpreted"
        row.state = "resolved"
        row.resolved_at = _now()
        row.save()
        resolved.append(row.id)
    return sorted(resolved)


def _recency(row) -> tuple[float, str]:
    value = getattr(row, "created_at", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.timestamp(), row.id
    return 0.0, row.id


def list_investigations(project_key: str, case_id: str | None = None) -> list:
    """Every investigation in the partition, newest first, optionally for one case."""
    rows = list(ImprovementInvestigation.query.filter(project_key=project_key))
    if case_id is not None:
        rows = [r for r in rows if r.case_id == case_id]
    return sorted(rows, key=_recency, reverse=True)


def row_as_dict(row) -> dict:
    """A JSON-ready view of one investigation row for the CLI."""
    out = {}
    for name in (
        "id",
        "project_key",
        "kind",
        "state",
        "stage",
        "case_id",
        "uncertainty",
        "query",
        "decision_affected",
        "expected_information_value",
        "interpretation",
        "provisional_assumption",
        "charter_digest",
    ):
        out[name] = getattr(row, name, None)
    out["claims"] = _claims_envelope(row)
    out["sources"] = _load_json(row.sources, [])
    out["prior_answers"] = _load_json(row.prior_answers, [])
    out["assumption_detail"] = _load_json(row.assumption_detail, None)
    for name in ("created_at", "expires_at", "resolved_at"):
        value = getattr(row, name, None)
        out[name] = value.isoformat() if isinstance(value, datetime) else value
    return out
