"""The three-day assumption digest: a status report to Telegram (lane 5, #3217).

Charter §11 keeps the digest "as a status report, not a request for research
direction", and it "must not imply that Tom's silence validates an
assumption". So this module reads records and sends one plain message. It
writes no case state, binds to no message id, and imports nothing that could
turn a line into a question. The closing line is fixed
(:data:`CLOSING_LINE`) and every rendered line outside a quoted assumption
body is a statement.

**What one digest carries**, in order:

1. Provisional assumptions on investigations resolved since the watermark,
   grouped by the case's ``priority_area`` (charter §3 order), each with its
   charter passage, confidence, consequence, and overturning observation.
2. Vault requests: ``resource_acquisition`` investigations whose disposition
   is ``vault_request_written`` and whose case still carries a ``vault:``
   block. These are standing status, rendered every digest until the case
   unblocks; they are phrased as what the adapter expects, and they neither
   scope by nor advance the watermark.
3. Resources acquired: ``resource_acquired`` evidence rows created since the
   watermark (``tools/vault_write.py`` writes them).
4. Infrastructure overruns: the payloads lane 7's teardown ladder handed to
   :func:`on_escalation` since the last delivered digest.

**The watermark** (``ImprovementControllerState.digest_watermark``) is the
newest timestamp the digest actually rendered, never "now" (Race 4): a row
resolved between the read and the send is newer than the watermark and
appears next time. It is written only after the sender reports delivery, so
a digest that did not go out is re-sent whole on the next run.

**The overrun sink is process-local.** :func:`on_escalation` appends to a
list in this interpreter's memory; a process restart drops what it holds.
That is acceptable because the ``spend_receipt`` row the ladder writes is the
record of the overrun, and this sink only lets the next digest in the same
process mention it. Payloads are drained after a successful send and kept
across a failed one.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

LOGGER_PREFIX = "improvement_assumption_digest"

#: The fixed closing line (charter §11). Byte-exact; tests pin it.
CLOSING_LINE = (
    "This is a status report. It asks nothing. Silence validates none of the "
    "above; each assumption stands until evidence overturns it."
)

#: Overrun payloads waiting for the next delivered digest. Process-local.
_PENDING_OVERRUNS: list[dict] = []


def on_escalation(payload: dict) -> None:
    """The optional second sink ``tools.infrastructure_budget.apply_teardown``
    calls with ``{resource, action, window_key | failure_mode, forecast_usd}``.

    Appends a copy to the process-local pending list the next digest drains.
    """
    _PENDING_OVERRUNS.append(dict(payload))


def pending_overruns() -> list[dict]:
    """A copy of the payloads waiting for the next delivered digest."""
    return [dict(p) for p in _PENDING_OVERRUNS]


def drain_overruns() -> list[dict]:
    """Remove and return every pending payload."""
    drained = list(_PENDING_OVERRUNS)
    del _PENDING_OVERRUNS[:]
    return drained


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _aware(stamp) -> datetime | None:
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(stamp, datetime):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _json(raw, default):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _case(project_key: str, case_id):
    from models.improvement_case import ImprovementCase

    if not case_id:
        return None
    try:
        return ImprovementCase.query.filter(project_key=project_key, id=str(case_id)).first()
    except Exception:  # noqa: BLE001
        return None


@dataclass
class DigestInputs:
    """Everything one digest renders, plus the newest stamp it covers."""

    project_key: str
    since: datetime | None
    assumptions: list[dict] = field(default_factory=list)
    vault_requests: list[dict] = field(default_factory=list)
    resources_acquired: list[dict] = field(default_factory=list)
    overruns: list[dict] = field(default_factory=list)
    newest_rendered: datetime | None = None

    @property
    def empty(self) -> bool:
        return not (
            self.assumptions or self.vault_requests or self.resources_acquired or self.overruns
        )

    def counts(self) -> dict:
        return {
            "assumptions": len(self.assumptions),
            "vault_requests": len(self.vault_requests),
            "resources_acquired": len(self.resources_acquired),
            "overruns": len(self.overruns),
        }


def _newer(stamp: datetime | None, since: datetime | None) -> bool:
    return stamp is not None and (since is None or stamp > since)


def collect(project_key: str, *, since: datetime | None) -> DigestInputs:
    """Read the records one digest renders. Reads only; no row is written."""
    from models.improvement_evidence import ImprovementEvidence
    from models.improvement_investigation import ImprovementInvestigation
    from tools.improvement_investigations import disposition_of

    inputs = DigestInputs(project_key=project_key, since=since)
    newest: datetime | None = None

    resolved = list(
        ImprovementInvestigation.query.filter(project_key=project_key, state="resolved")
    )
    for row in resolved:
        resolved_at = _aware(getattr(row, "resolved_at", None))
        case = _case(project_key, getattr(row, "case_id", None))
        assumption = getattr(row, "provisional_assumption", None)
        if assumption and _newer(resolved_at, since):
            detail = _json(getattr(row, "assumption_detail", None), {}) or {}
            inputs.assumptions.append(
                {
                    "investigation_id": str(row.id),
                    "case_id": getattr(case, "id", None),
                    "case_title": getattr(case, "title", None),
                    "priority_area": getattr(case, "priority_area", None) or "other",
                    "assumption": str(assumption),
                    "charter_passage": str(detail.get("charter_passage") or "not recorded"),
                    "confidence": str(detail.get("confidence") or "not recorded"),
                    "consequence": str(detail.get("consequence") or "not recorded"),
                    "overturning_observation": str(
                        detail.get("overturning_observation") or "not recorded"
                    ),
                    "resolved_at": resolved_at,
                }
            )
            if newest is None or resolved_at > newest:
                newest = resolved_at
        if getattr(row, "kind", None) == "resource_acquisition":
            disposition, resource_name = disposition_of(row)
            blocked_by = getattr(case, "blocked_by", None) or ""
            if disposition == "vault_request_written" and blocked_by.startswith("vault:"):
                inputs.vault_requests.append(
                    {
                        "investigation_id": str(row.id),
                        "resource_name": resource_name or "unknown",
                        "title": _vault_title(resource_name),
                        "case_id": getattr(case, "id", None),
                        "case_title": getattr(case, "title", None),
                        "blocked_by": blocked_by,
                    }
                )

    acquired = list(
        ImprovementEvidence.query.filter(project_key=project_key, kind="resource_acquired")
    )
    for row in acquired:
        created_at = _aware(getattr(row, "created_at", None))
        if not _newer(created_at, since):
            continue
        inputs.resources_acquired.append(
            {
                "title": getattr(row, "text", None) or "(untitled)",
                "fingerprint": getattr(row, "detail", None) or "(no fingerprint)",
                "created_at": created_at,
            }
        )
        if newest is None or created_at > newest:
            newest = created_at

    inputs.assumptions.sort(key=lambda a: (a["resolved_at"], a["investigation_id"]))
    inputs.vault_requests.sort(key=lambda v: v["investigation_id"])
    inputs.resources_acquired.sort(key=lambda r: (r["created_at"], r["title"]))
    inputs.overruns = pending_overruns()
    inputs.newest_rendered = newest
    return inputs


def _vault_title(resource_name: str | None) -> str:
    from tools.improvement_investigations import VAULT_ITEM_TITLES

    if not resource_name:
        return "unnamed credential"
    return VAULT_ITEM_TITLES.get(
        resource_name, resource_name.replace("_", " ").title() + " credential"
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _quoted(text: str) -> str:
    """User-authored text inside double quotes, its own quotes flattened so
    the quoted span stays one span."""
    return '"' + str(text).replace('"', "'") + '"'


def _stamp(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if value else "the beginning"


def _area_order() -> list[str]:
    from models.improvement_case import PRIORITY_AREAS

    return list(PRIORITY_AREAS)


def _render_assumptions(inputs: DigestInputs) -> list[str]:
    lines = [f"Provisional assumptions recorded since {_stamp(inputs.since)}:"]
    by_area: dict[str, list[dict]] = {}
    for entry in inputs.assumptions:
        by_area.setdefault(entry["priority_area"], []).append(entry)
    known = _area_order()
    areas = [a for a in known if a in by_area] + sorted(a for a in by_area if a not in known)
    for area in areas:
        lines.append("")
        lines.append(f"[{area}]")
        for entry in by_area[area]:
            where = f"investigation {entry['investigation_id']}"
            if entry["case_title"]:
                where += f", case {_quoted(entry['case_title'])}"
            elif entry["case_id"]:
                where += f", case {entry['case_id']}"
            lines.append(f"- {_quoted(entry['assumption'])} ({where})")
            lines.append(f"  charter: {_quoted(entry['charter_passage'])}")
            lines.append(f"  confidence: {entry['confidence']}")
            lines.append(f"  consequence: {_quoted(entry['consequence'])}")
            lines.append(f"  overturned by: {_quoted(entry['overturning_observation'])}")
    return lines


def _render_vault_requests(inputs: DigestInputs) -> list[str]:
    from tools.improvement_resources import VAULT

    lines = ["Vault requests:"]
    for entry in inputs.vault_requests:
        case_text = (
            f"case {_quoted(entry['case_title'])}"
            if entry["case_title"]
            else f"case {entry['case_id']}"
        )
        lines.append(
            f"- The adapter expects the item {_quoted(entry['title'])} in the {VAULT} vault "
            f"(resource {entry['resource_name']}). {case_text} stays blocked by "
            f"{entry['blocked_by']} until the probe verifies it "
            f"(investigation {entry['investigation_id']})."
        )
    return lines


def _render_resources(inputs: DigestInputs) -> list[str]:
    lines = ["Resources acquired:"]
    for entry in inputs.resources_acquired:
        lines.append(f"- {entry['title']} ({entry['fingerprint']}), {_stamp(entry['created_at'])}")
    return lines


def _render_overruns(inputs: DigestInputs) -> list[str]:
    lines = ["Infrastructure overruns:"]
    for payload in inputs.overruns:
        resource = payload.get("resource") or "unnamed resource"
        action = str(payload.get("action") or "unknown").replace("_", " ")
        try:
            forecast = f"${float(payload.get('forecast_usd') or 0.0):.2f}"
        except (TypeError, ValueError):
            forecast = "an unstated amount"
        detail = ""
        if payload.get("window_key"):
            detail = f", booked against window {payload['window_key']}"
        if payload.get("failure_mode"):
            detail += f", export verification {payload['failure_mode']}"
        lines.append(f"- {resource}: {action}, forecast {forecast}{detail}")
    return lines


def render_digest(inputs: DigestInputs, *, now: datetime) -> str:
    """The whole message. Sections with nothing to say are omitted; the
    closing line is always last."""
    blocks: list[list[str]] = [
        [f"Improvement assumption digest for {inputs.project_key}, {_stamp(now)}."]
    ]
    if inputs.assumptions:
        blocks.append(_render_assumptions(inputs))
    if inputs.vault_requests:
        blocks.append(_render_vault_requests(inputs))
    if inputs.resources_acquired:
        blocks.append(_render_resources(inputs))
    if inputs.overruns:
        blocks.append(_render_overruns(inputs))
    blocks.append([CLOSING_LINE])
    return "\n\n".join("\n".join(block) for block in blocks)


# ---------------------------------------------------------------------------
# Reflection entrypoint
# ---------------------------------------------------------------------------


def _read_watermark(project_key: str) -> datetime | None:
    from models.improvement_controller_state import ImprovementControllerState

    state = ImprovementControllerState.get(project_key)
    return _aware(getattr(state, "digest_watermark", None)) if state is not None else None


def _write_watermark(project_key: str, newest: datetime) -> None:
    from models.improvement_controller_state import ImprovementControllerState

    ImprovementControllerState.get_or_create(project_key).record(
        digest_watermark=newest.isoformat()
    )


def run_improvement_assumption_digest(
    *, sender=None, now: datetime | None = None, project_key: str | None = None
) -> dict:
    """Reflection entrypoint, registered as ``improvement-assumption-digest``.

    Gated on ``ImprovementSettings.enabled`` like the other improvement
    reflections: ``False`` returns ``status="skipped"`` and reads nothing.
    ``sender(message, *, logger_prefix) -> bool`` defaults to
    ``send_host_eng_telegram``; a sender answering ``False`` records
    ``digest-not-delivered`` and leaves the watermark and the pending
    overruns untouched. An empty digest sends nothing and is a success.
    """
    t0 = time.time()
    from config.memory_defaults import DEFAULT_PROJECT_KEY
    from config.settings import settings

    if not settings.improvement.enabled:
        return {
            "status": "skipped",
            "findings": [],
            "summary": (
                "improvement-assumption-digest: disabled "
                "(ImprovementSettings.enabled is False; set IMPROVEMENT__ENABLED=true "
                "on the machine that owns this project to start the digest)"
            ),
            "counts": {},
            "duration": time.time() - t0,
        }

    project_key = project_key or DEFAULT_PROJECT_KEY
    now = now or datetime.now(UTC)
    if sender is None:
        from reflections.utilities import send_host_eng_telegram

        sender = send_host_eng_telegram

    try:
        since = _read_watermark(project_key)
        inputs = collect(project_key, since=since)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: read failed for %s: %s", LOGGER_PREFIX, project_key, exc)
        return {
            "status": "error",
            "findings": [f"digest read failed: {exc}"],
            "summary": f"improvement-assumption-digest: read failed ({exc})",
            "counts": {},
            "duration": time.time() - t0,
        }

    counts = inputs.counts()
    if inputs.empty:
        return {
            "status": "success",
            "findings": [],
            "summary": "improvement-assumption-digest: nothing new since "
            f"{_stamp(since)}; nothing sent",
            "counts": counts,
            "sent": False,
            "watermark": since.isoformat() if since else None,
            "duration": time.time() - t0,
        }

    message = render_digest(inputs, now=now)
    delivered = bool(sender(message, logger_prefix=LOGGER_PREFIX))
    if not delivered:
        logger.warning("%s: digest not delivered; watermark left at %s", LOGGER_PREFIX, since)
        return {
            "status": "error",
            "findings": ["digest-not-delivered"],
            "summary": "improvement-assumption-digest: not delivered; "
            f"{counts['assumptions']} assumption(s) will be re-sent",
            "counts": counts,
            "sent": False,
            "watermark": since.isoformat() if since else None,
            "duration": time.time() - t0,
        }

    del _PENDING_OVERRUNS[: len(inputs.overruns)]
    watermark = since
    if inputs.newest_rendered is not None and (since is None or inputs.newest_rendered > since):
        _write_watermark(project_key, inputs.newest_rendered)
        watermark = inputs.newest_rendered
    return {
        "status": "success",
        "findings": [],
        "summary": (
            f"improvement-assumption-digest: sent assumptions={counts['assumptions']} "
            f"vault_requests={counts['vault_requests']} "
            f"resources_acquired={counts['resources_acquired']} overruns={counts['overruns']}"
        ),
        "counts": counts,
        "sent": True,
        "watermark": watermark.isoformat() if watermark else None,
        "duration": time.time() - t0,
    }
