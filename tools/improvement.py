"""``valor-improve``: the only door through which a research session proposes
anything (Decision 9, Task 10). A thin ``argparse`` shell over the control
package -- every subcommand is a reader or a writer of one package function,
never a raw transition. ``--json`` on every subcommand; the human format is
the default.

The console script lives in ``.venv/bin`` only (Decision 14): the repo's own
``valor-*`` convention, and the research skill invokes it by full path.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

PROJECT_KEY = "valor"


def _emit(args, human: str, payload: dict) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload))
    else:
        print(human)


def _own_session():
    """The calling process's own AgentSession row, resolved through
    AGENT_SESSION_ID (the worker exports it to every harness subprocess).
    Returns None when unset (an operator at a terminal -- break-glass)."""
    import os

    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        return None
    from models.session_lifecycle import get_authoritative_session

    return get_authoritative_session(session_id)


def _acquire_lease(case_id: str):
    from config.settings import settings
    from tools.improvement_control.lease import default_lease

    lease = default_lease()
    lease_key = f"improve:{PROJECT_KEY}:{case_id}:lease"
    generation = lease.acquire(lease_key, ttl=settings.improvement.lease_ttl_seconds)
    return lease, lease_key, generation


def _validate_case_for_proposal(case_id: str) -> str | None:
    """Data Flow step 1: refuse before touching the lease or the journal
    when the case's own fields are not proposal-ready. Returns a reason
    code, or None when validation passes."""
    from models.improvement_case import PRIORITY_AREAS, ImprovementCase
    from models.improvement_charter import ImprovementCharter

    case = ImprovementCase.query.get(project_key=PROJECT_KEY, id=case_id)
    if case is None:
        return "CASE_NOT_FOUND"
    if not getattr(case, "ranking_rationale", None):
        return "MISSING_RANKING_RATIONALE"
    if getattr(case, "priority_area", None) not in PRIORITY_AREAS:
        return "INVALID_PRIORITY_AREA"
    pinned = ImprovementCharter.pinned(PROJECT_KEY)
    charter_digest = getattr(case, "charter_digest", None)
    if pinned is None or charter_digest != pinned.digest:
        return "CHARTER_DIGEST_STALE"
    return None


def cmd_propose(args) -> int:
    from tools.improvement_control.journal import read_head, transition
    from tools.improvement_control.scheduler_adapter import ALLOWED_ACTION_TYPES

    case_id = args.case
    payload_path = args.payload
    action_type = args.action_type
    if action_type not in ALLOWED_ACTION_TYPES:
        _emit(
            args,
            f"refused: INVALID_ACTION_TYPE ({action_type})",
            {"accepted": False, "reason": "INVALID_ACTION_TYPE"},
        )
        return 1
    try:
        payload_bytes = open(payload_path, "rb").read()
    except OSError as e:
        _emit(
            args,
            f"cannot read --payload {payload_path}: {e}",
            {"accepted": False, "reason": str(e)},
        )
        return 1
    if not payload_bytes.strip():
        _emit(args, "refused: EMPTY_PAYLOAD", {"accepted": False, "reason": "EMPTY_PAYLOAD"})
        return 1

    import hashlib

    payload_digest = "sha256:" + hashlib.sha256(payload_bytes).hexdigest()

    session = _own_session()
    agent_session_id = None
    action_id = args.action_id
    if session is not None:
        ec = getattr(session, "extra_context", None) or {}
        if not ec.get("action_id"):
            _emit(
                args,
                "refused: NOT_A_RESEARCH_SESSION",
                {"accepted": False, "reason": "NOT_A_RESEARCH_SESSION"},
            )
            return 1
        action_id = ec["action_id"]
        agent_session_id = session.session_id
    elif not action_id:
        # Break-glass: mint one so the proposal is admittable. The scheduler
        # adapter skips a journaled proposal with an empty action_id forever.
        action_id = uuid.uuid4().hex

    validation_reason = _validate_case_for_proposal(case_id)
    if validation_reason:
        _emit(
            args, f"refused: {validation_reason}", {"accepted": False, "reason": validation_reason}
        )
        return 1

    # Data Flow step 2: the payload lands in the verifying store BEFORE the
    # lease is taken, so an accepted proposal's journal entry and a refused
    # proposal's evidence row both carry a loadable `artifact_ref`.
    from models.verifying_artifact_store import VerifyingArtifactStore

    store = VerifyingArtifactStore()
    artifact_key = f"{case_id}-{action_id}-{payload_digest.split(':', 1)[-1][:16]}"
    try:
        artifact_ref = store.save(
            payload_bytes, key=artifact_key, model_class_name="ImprovementProposal"
        )
    except OSError as e:
        _emit(
            args,
            f"refused: ARTIFACT_WRITE_FAILED ({e})",
            {"accepted": False, "reason": "ARTIFACT_WRITE_FAILED"},
        )
        return 1

    lease, lease_key, generation = _acquire_lease(case_id)
    if generation is None:
        _emit(args, "refused: case busy", {"accepted": False, "reason": "CASE_BUSY"})
        return 1
    try:
        head = read_head(PROJECT_KEY, case_id)
        expected_revision = head.revision if head is not None else 0
        result = transition(
            PROJECT_KEY,
            case_id,
            expected_revision=expected_revision,
            generation=generation,
            event="action_proposed",
            payload_digest=payload_digest,
            action_id=action_id,
            agent_session_id=agent_session_id,
            action_type=action_type,
            artifact_ref=artifact_ref,
        )
    finally:
        lease.release(lease_key, generation)

    if not result.accepted:
        if session is not None:
            from datetime import UTC, datetime

            from models.improvement_evidence import ImprovementEvidence

            ImprovementEvidence.create(
                project_key=PROJECT_KEY,
                created_at=datetime.now(UTC),
                kind="other",
                classification="unknown",
                source_ref=f"propose-refused:{case_id}:{action_id}",
                text=f"intent_state:{result.reason}",
                detail=artifact_ref,
            )
        _emit(args, f"refused: {result.reason}", {"accepted": False, "reason": result.reason})
        return 1

    _project(case_id)
    _emit(
        args,
        f"accepted: revision={result.revision}",
        {
            "accepted": True,
            "revision": result.revision,
            "action_id": action_id,
            "artifact_ref": artifact_ref,
        },
    )
    return 0


def _project(case_id: str) -> None:
    """Data Flow step 5: copy the head onto ``ImprovementCase`` once a
    transition has been accepted. Every accepting writer in this CLI calls
    it after its lease is released."""
    from tools.improvement_control.projection import apply

    apply(PROJECT_KEY, case_id)


def cmd_propose_amendment(args) -> int:
    import hashlib
    from datetime import UTC, datetime

    from models.improvement_investigation import ImprovementInvestigation
    from tools.improvement_control.journal import read_head, transition

    case_id = args.case
    request_text = args.request
    digest = "sha256:" + hashlib.sha256(request_text.encode()).hexdigest()

    lease, lease_key, generation = _acquire_lease(case_id)
    if generation is None:
        _emit(args, "refused: case busy", {"accepted": False, "reason": "CASE_BUSY"})
        return 1
    try:
        head = read_head(PROJECT_KEY, case_id)
        expected_revision = head.revision if head is not None else 0
        result = transition(
            PROJECT_KEY,
            case_id,
            expected_revision=expected_revision,
            generation=generation,
            event="amendment_proposed",
            payload_digest=digest,
        )
    finally:
        lease.release(lease_key, generation)
    if result.accepted:
        _project(case_id)

    ImprovementInvestigation.create(
        project_key=PROJECT_KEY,
        created_at=datetime.now(UTC),
        case_id=case_id,
        kind="charter_amendment",
        state="awaiting_authorization",
        claims=json.dumps([{"claim": request_text, "url": "", "retrieved_at": ""}]),
    )

    from reflections.utilities import load_local_projects, send_eng_telegram

    projects = [p for p in load_local_projects() if p.get("slug") == "valor"]
    if not projects:
        _emit(
            args,
            "journaled and recorded; no 'valor' project entry to notify",
            {"accepted": bool(result.accepted), "notified": False},
        )
        return 1
    send_eng_telegram(
        projects[0],
        f"Improvement charter amendment proposed for case {case_id}: {request_text}",
        logger_prefix="[improve]",
    )
    _emit(
        args,
        "amendment journaled and Tom notified",
        {"accepted": bool(result.accepted), "notified": True},
    )
    return 0


def cmd_pause(args) -> int:
    from tools.improvement_control.journal import pause

    lease, lease_key, generation = _acquire_lease(args.case or "_ns")
    if generation is None and args.case:
        # A per-case pause is fenced by the case's generation; presenting 0
        # to the transition would be refused STALE_GENERATION with the real
        # cause (a live holder) hidden. The namespace pause is a direct hash
        # write with no fence, so it proceeds regardless of the lease.
        _emit(args, "paused: False (CASE_BUSY)", {"accepted": False, "reason": "CASE_BUSY"})
        return 1
    generation = generation or 0
    try:
        result = pause(
            PROJECT_KEY,
            args.case,
            generation=generation,
            reason=args.reason or "operator pause",
            by="operator",
        )
    finally:
        if generation:
            lease.release(lease_key, generation)
    if result.accepted and args.case:
        _project(args.case)
    human = "paused: True" if result.accepted else f"paused: False ({result.reason})"
    _emit(args, human, {"accepted": result.accepted, "reason": result.reason})
    return 0 if result.accepted else 1


def cmd_resume(args) -> int:
    from tools.improvement_control.intents import list_intents
    from tools.improvement_control.journal import read_head
    from tools.improvement_control.journal import resume as journal_resume
    from tools.improvement_control.recovery import cancel_wedge

    case_id = args.case
    blocking = [
        i.action_id
        for i in list_intents(PROJECT_KEY, case_id)
        if i.state == "reconciliation_required"
    ]
    if blocking and not args.force:
        for aid in blocking:
            print(aid, file=sys.stderr)
        return 1

    lease, lease_key, generation = _acquire_lease(case_id)
    if generation is None:
        _emit(args, "refused: case busy", {"accepted": False, "reason": "CASE_BUSY"})
        return 1
    resume_result = None
    try:
        if args.force:
            for aid in blocking:
                cancel_wedge(PROJECT_KEY, case_id, aid, generation=generation, by="operator")
        head = read_head(PROJECT_KEY, case_id)
        if head is not None and head.paused:
            resume_result = journal_resume(
                PROJECT_KEY, case_id, generation=generation, by="operator"
            )
    finally:
        lease.release(lease_key, generation)
    if resume_result is not None and not resume_result.accepted:
        _emit(
            args,
            f"refused: {resume_result.reason}",
            {"accepted": False, "reason": resume_result.reason},
        )
        return 1
    if resume_result is not None or blocking:
        _project(case_id)
    if resume_result is not None:
        _emit(args, "resumed", {"accepted": True, "cancelled": len(blocking)})
        return 0
    print(f"not paused; cancelled {len(blocking)} intent(s)")
    return 0


def cmd_doctor(args) -> int:
    """Paused heads, wedged intents, and outstanding reservations: the unit-1
    slot hash (each slot named with the case whose live intent holds it, or
    ``None`` for a slot no intent can release) and the open unit-2 window's
    reserved amount. The clean line is printed only when all three are empty."""
    try:
        from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
        from tools.improvement_control import keys
        from tools.improvement_control.intents import list_intents
        from tools.improvement_control.journal import read_head
        from tools.paid_inference_meter import status_dict as unit2_status_dict
        from utils.redis_client import text_redis
    except Exception as e:
        print(f"namespace unreachable: {e}")
        return 2

    # One guard covers the ORM query and every per-case and namespace read: a
    # namespace that becomes unreachable mid-loop reports `namespace
    # unreachable` with exit 2 (the break-glass drill, Success Criterion 2)
    # and never prints a partial "clean" or a partial wedged/paused list.
    try:
        cases = []
        for state in OPEN_CASE_STATES:
            cases.extend(ImprovementCase.query.filter(project_key=PROJECT_KEY, state=state))

        paused = []
        wedged = []
        holders: dict[str, str] = {}
        for case in cases:
            head = read_head(PROJECT_KEY, case.id)
            if head is not None and head.paused:
                paused.append(case.id)
            for intent in list_intents(PROJECT_KEY, case.id):
                if intent.state == "reconciliation_required":
                    wedged.append((case.id, intent.action_id))
                if intent.state in ("admitted", "materialized", "running"):
                    holders[intent.action_id] = case.id
        slots = text_redis().hgetall(keys.slots_key(PROJECT_KEY))
        unit2 = unit2_status_dict(PROJECT_KEY)
    except Exception as e:
        print(f"namespace unreachable: {e}")
        return 2

    reservations = {
        "slots": [
            {"action_id": aid, "case_id": holders.get(aid), "since": ts}
            for aid, ts in sorted(slots.items())
        ],
        "unit2": {"day_key": unit2["day_key"], "reserved_usd": unit2["reserved_usd"]},
    }
    outstanding = bool(slots) or unit2["reserved_usd"] > 0
    payload = {
        "paused": paused,
        "wedged": [list(w) for w in wedged],
        "reservations": reservations,
    }
    if not paused and not wedged and not outstanding:
        _emit(args, "no paused heads, no stale intents, no outstanding reservations", payload)
        return 0

    lines = []
    if paused:
        lines.append("Paused heads: " + ", ".join(paused))
    if wedged:
        lines.append("Wedged intents: " + ", ".join(f"{c}/{a}" for c, a in wedged))
    if outstanding:
        parts = [
            f"slot {row['action_id']} (case {row['case_id'] or 'none, no live intent holds it'})"
            for row in reservations["slots"]
        ]
        if unit2["reserved_usd"] > 0:
            parts.append(
                f"unit2 ${unit2['reserved_usd']:.2f} reserved in window {unit2['day_key']}"
            )
        lines.append("Outstanding reservations: " + "; ".join(parts))
    _emit(args, "\n".join(lines), payload)
    return 0


def cmd_case_show(args) -> int:
    from tools.improvement_control.journal import journal_tail, read_head

    head = read_head(PROJECT_KEY, args.case)
    tail = journal_tail(PROJECT_KEY, args.case, args.tail)
    if head is None:
        _emit(args, "no head for this case", {"head": None, "journal": tail})
        return 1
    _emit(
        args,
        f"state={head.state} revision={head.revision} paused={head.paused}",
        {"head": head.__dict__, "journal": tail},
    )
    return 0


def cmd_case_explain(args) -> int:
    from models.improvement_charter import ImprovementCharter
    from tools.improvement_control.intents import list_intents
    from tools.improvement_control.journal import journal_tail, read_head

    case_id = args.case
    head = read_head(PROJECT_KEY, case_id)
    intents = list_intents(PROJECT_KEY, case_id)
    blocking = [i.action_id for i in intents if i.state == "reconciliation_required"]
    tail = journal_tail(PROJECT_KEY, case_id, 1)
    last_event = tail[-1] if tail else None
    pinned = ImprovementCharter.pinned(PROJECT_KEY)
    charter_pinned = bool(pinned and head is not None)

    payload = {
        "state": head.state if head else None,
        "paused": head.paused if head else False,
        "pause_reason": head.pause_reason if head else None,
        "last_event": last_event,
        "intents": [i.__dict__ for i in intents],
        "blocking_action_ids": blocking,
        "charter_pinned": charter_pinned,
    }
    lines = []
    if head is not None:
        lines.append(f"state={head.state} paused={head.paused}")
    if blocking:
        for aid in blocking:
            lines.append(
                f"blocked by action_id={aid}; clear with: "
                f'"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" resume --case {case_id} --force'
            )
    _emit(args, "\n".join(lines) or "no blocking intents", payload)
    return 0


def cmd_budget(args) -> int:
    from config.settings import settings
    from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
    from tools import infrastructure_budget, paid_inference_meter
    from tools.improvement_control.intents import list_intents

    slot_count = 0
    for state in OPEN_CASE_STATES:
        for case in ImprovementCase.query.filter(project_key=PROJECT_KEY, state=state):
            for intent in list_intents(PROJECT_KEY, case.id):
                if intent.state in ("admitted", "materialized", "running"):
                    slot_count += 1

    unit2 = paid_inference_meter.status_dict(PROJECT_KEY)
    unit3 = infrastructure_budget.status_dict(project_key=PROJECT_KEY)
    unknown_receipts = paid_inference_meter.unknown_receipts(PROJECT_KEY)

    payload = {
        "unit1_slots_in_use": slot_count,
        "unit1_max_concurrent": settings.improvement.max_concurrent_research_sessions,
        "unit2": unit2,
        "unit3": unit3,
        "unit2_receipted_unknown": unknown_receipts,
    }
    # Charter §8: an unknown-metered receipt is printed in its own block with
    # the window it was charged to, so it never reads as zero spend.
    unknown_lines = [
        f"  window={r['day_key'] or '?'} usd={r['usd']} case={r['case_id'] or '-'} "
        f"receipt={r['source_ref']}"
        for r in unknown_receipts
    ]
    unknown_block = (
        "unit2 unknown-metered receipts (charged at reservation, actual spend unknown):\n"
        + "\n".join(unknown_lines)
        if unknown_lines
        else "unit2 unknown-metered receipts: none"
    )
    _emit(
        args,
        f"unit1: {slot_count}/{settings.improvement.max_concurrent_research_sessions} slots\n"
        f"unit2: reserved=${unit2['reserved_usd']:.2f} settled=${unit2['settled_usd']:.2f} "
        f"window={unit2['day_key']}\n"
        f"{unknown_block}\n"
        f"unit3: {unit3}",
        payload,
    )
    return 0


def cmd_export(args) -> int:
    from pathlib import Path

    from tools.improvement_control.export import export_namespace

    root = Path(args.root or ".")
    out = export_namespace(PROJECT_KEY, root)
    _emit(args, f"exported to {out}", {"path": str(out)})
    return 0


def cmd_import(args) -> int:
    from pathlib import Path

    from tools.improvement_control.export import import_namespace

    refusal = import_namespace(Path(args.archive), project_key=PROJECT_KEY, force=args.force)
    if refusal is not None:
        _emit(args, f"refused: {refusal.reason}", {"accepted": False, "reason": refusal.reason})
        return 1
    _emit(args, "imported", {"accepted": True})
    return 0


def cmd_replay_projection(args) -> int:
    from tools.improvement_control.projection import replay

    result = replay(PROJECT_KEY, args.case)
    _emit(
        args,
        f"revision={result.head_revision} fold_reached_head={result.fold_reached_head}",
        result.__dict__,
    )
    return 0


def cmd_release_compare(args) -> int:
    try:
        from models.improvement_release import ImprovementRelease
    except ImportError:
        _emit(args, "no release records yet, written by lane 6", {"releases": []})
        return 0
    rows = list(ImprovementRelease.query.filter(project_key=PROJECT_KEY))
    if not rows:
        _emit(args, "no release records yet, written by lane 6", {"releases": []})
        return 0
    _emit(args, f"{len(rows)} release record(s)", {"releases": [r.id for r in rows]})
    return 0


def cmd_ranking(args) -> int:
    """``ranking [--at REF]``: the latest ranking snapshot, or the one named.
    Thin: the reader and the renderer live in ``tools/improvement_ranking.py``
    (lane 5, #3217)."""
    from tools.improvement_ranking import cmd_ranking as run_ranking

    return run_ranking(args, project_key=PROJECT_KEY)


# --- lane 5 (#3217): the brief, investigations, model revisions, case open ---


def _refused(args, reason: str, message: str = "", **extra) -> int:
    payload = {"accepted": False, "reason": reason, "message": message or reason}
    payload.update(extra)
    _emit(args, f"refused: {reason}" + (f" ({message})" if message else ""), payload)
    return 1


def cmd_brief(args) -> int:
    """Print the research brief for one case (charter first, then the case)."""
    from tools.improvement_brief import build_brief

    try:
        text = build_brief(args.case, PROJECT_KEY)
    except LookupError as e:
        reason, _, message = str(e).partition(": ")
        return _refused(args, reason, message)
    _emit(args, text.rstrip("\n"), {"case_id": args.case, "brief": text})
    return 0


def _outcome_exit(args, outcome, human_ok: str) -> int:
    if not outcome.accepted:
        return _refused(
            args, outcome.reason, outcome.message, investigation_id=outcome.investigation_id
        )
    payload = {"accepted": True, "investigation_id": outcome.investigation_id, **outcome.extra}
    _emit(args, f"{human_ok}: {outcome.investigation_id}", payload)
    return 0


def cmd_investigation_open(args) -> int:
    from tools.improvement_investigations import open_investigation

    outcome = open_investigation(
        PROJECT_KEY,
        kind=args.kind,
        case_id=args.case,
        uncertainty=args.uncertainty,
        query=args.query,
        decision_affected=args.decision_affected,
        expected_information_value=args.expected_information_value,
        state=args.state,
    )
    return _outcome_exit(args, outcome, "opened")


def _load_json_arg(raw: str):
    """A JSON literal, or ``@path`` naming a file holding one."""
    if raw.startswith("@"):
        raw = open(raw[1:], encoding="utf-8").read()
    return json.loads(raw)


def cmd_investigation_record(args) -> int:
    from tools.improvement_investigations import InvestigationRefusedError, record_claims

    try:
        claims = _load_json_arg(args.claims)
        sources = _load_json_arg(args.sources) if args.sources else None
    except (OSError, ValueError) as e:
        return _refused(args, "INVALID_CLAIMS_JSON", str(e))
    try:
        recorded = record_claims(args.id, claims, sources)
    except InvestigationRefusedError as e:
        return _refused(args, e.reason, e.message, investigation_id=args.id)
    except ValueError as e:
        return _refused(args, "INVALID_CLAIMS", str(e), investigation_id=args.id)
    _emit(
        args,
        f"recorded {recorded} entr{'y' if recorded == 1 else 'ies'} on {args.id}",
        {"accepted": True, "investigation_id": args.id, "recorded": recorded},
    )
    return 0


def cmd_investigation_resolve(args) -> int:
    from tools.improvement_investigations import resolve

    detail = None
    if args.assumption_detail:
        try:
            detail = _load_json_arg(args.assumption_detail)
        except (OSError, ValueError) as e:
            return _refused(args, "INVALID_ASSUMPTION_DETAIL_JSON", str(e))
    outcome = resolve(
        args.id,
        interpretation=args.interpretation,
        provisional_assumption=args.assumption,
        assumption_detail=detail,
        disposition=args.disposition,
        resource_name=args.resource_name,
    )
    return _outcome_exit(args, outcome, "resolved")


def cmd_investigation_list(args) -> int:
    from tools.improvement_investigations import list_investigations, row_as_dict

    rows = list_investigations(PROJECT_KEY, case_id=args.case)
    lines = [
        f"{r.id} {r.kind} {r.state} stage={r.stage or 'draft'} case={r.case_id or '-'}: "
        f"{(r.query or '')[:80]}"
        for r in rows
    ]
    _emit(
        args,
        "\n".join(lines) or "no investigations",
        {"investigations": [row_as_dict(r) for r in rows]},
    )
    return 0


def _research_process_spec_values() -> dict:
    """The six ``ResearchProcessSpec`` values this lane pins (Provided to
    lane 6, item 1). ``ranking_module_digest`` is empty while the ranking
    module is absent."""
    import hashlib
    from pathlib import Path

    from config.settings import settings
    from tools.improvement_brief import brief_template_digest

    root = Path(__file__).resolve().parents[1]
    skill = root / ".claude" / "skills" / "improve-research" / "SKILL.md"
    ranking = root / "tools" / "improvement_ranking.py"
    return {
        "selection_rule": "ordinal-lexicographic-v1",
        "investigation_budget_split": {},
        "revision_cadence_seconds": settings.improvement.controller_tick_seconds,
        "planner_prompt_digest": brief_template_digest(),
        "skill_digest": hashlib.sha256(skill.read_bytes()).hexdigest() if skill.exists() else "",
        "extra": {
            "ranking_module_digest": (
                hashlib.sha256(ranking.read_bytes()).hexdigest() if ranking.exists() else ""
            )
        },
    }


def _research_process_spec_text(values: dict) -> str:
    """Canonical spec JSON through ``tools.improvement_ranking.process_spec_json``
    (bytes, decoded) when it is importable, else the same canonical form
    built here: ``json.dumps(values, sort_keys=True, separators=(",", ":"))``."""
    try:
        from tools.improvement_ranking import process_spec_json
    except ImportError:
        return json.dumps(values, sort_keys=True, separators=(",", ":"))
    encoded = process_spec_json(values)
    return encoded.decode("utf-8") if isinstance(encoded, bytes) else str(encoded)


def cmd_revise_model(args) -> int:
    """Write one ``ImprovementModelRevision`` for the case. A revision with no
    prediction is a note, and is refused ``EMPTY_PREDICTION``."""
    from datetime import UTC, datetime

    from models.improvement_case import ImprovementCase
    from models.improvement_model_revision import ImprovementModelRevision

    if not (args.prediction or "").strip():
        return _refused(args, "EMPTY_PREDICTION", "a revision with no prediction is a note")
    try:
        case = ImprovementCase.query.get(project_key=PROJECT_KEY, id=args.case)
    except Exception:
        case = None
    if case is None:
        return _refused(args, "CASE_NOT_FOUND", f"no case {args.case}")

    values = _research_process_spec_values()
    spec_text = _research_process_spec_text(values)
    digest = None
    try:
        from tools.improvement_recursion.process import research_process_digest

        digest = research_process_digest(values)
    except ImportError:
        digest = None

    current = [
        r for r in ImprovementModelRevision.query.filter(project_key=PROJECT_KEY, state="current")
    ]
    current.sort(key=lambda r: (int(getattr(r, "revision", 0) or 0), r.id))
    previous = current[-1] if current else None
    revision_number = max((int(getattr(r, "revision", 0) or 0) for r in current), default=0) + 1
    row = ImprovementModelRevision.create(
        project_key=PROJECT_KEY,
        created_at=datetime.now(UTC),
        state="current",
        revision=revision_number,
        summary=args.summary,
        rationale=args.rationale,
        prediction=args.prediction.strip(),
        evidence_ids=case.evidence_ids or "[]",
        supersedes_id=previous.id if previous is not None else None,
        research_process_digest=digest,
        research_process_spec=spec_text,
    )
    if previous is not None:
        previous.state = "superseded"
        previous.save()
    _emit(
        args,
        f"model revision {row.revision} written: {row.id}"
        + ("" if digest else " (process digest pending lane 6)"),
        {
            "accepted": True,
            "revision_id": row.id,
            "revision": row.revision,
            "research_process_digest": digest,
            "supersedes_id": row.supersedes_id,
        },
    )
    return 0


def cmd_case_open(args) -> int:
    """Open cases through the planner's ``open_cases``: the same clustering,
    novelty check, and ``case_opened`` journal write the tick runs. With
    ``--evidence-ids`` only those rows are considered; without it the
    planner's bounded recent scan runs. Refuses ``PLANNER_UNAVAILABLE`` when
    ``reflections.improvement_plan`` is absent and ``CHARTER_NOT_PINNED`` when
    no charter row exists."""
    from models.improvement_charter import ImprovementCharter

    try:
        from reflections.improvement_plan import open_cases
    except ImportError:
        return _refused(
            args, "PLANNER_UNAVAILABLE", "reflections.improvement_plan.open_cases is not built"
        )
    charter = ImprovementCharter.pinned(PROJECT_KEY)
    if charter is None:
        return _refused(args, "CHARTER_NOT_PINNED", "no charter row; run the planner tick first")

    if args.evidence_ids:
        from models.improvement_evidence import ImprovementEvidence

        wanted = [e.strip() for e in args.evidence_ids.split(",") if e.strip()]
        rows = []
        for eid in wanted:
            try:
                row = ImprovementEvidence.query.get(project_key=PROJECT_KEY, id=eid)
            except Exception:
                row = None
            if row is None:
                return _refused(args, "EVIDENCE_NOT_FOUND", f"no evidence row {eid}")
            rows.append(row)
        result = open_cases(PROJECT_KEY, charter, evidence=rows)
    else:
        result = open_cases(PROJECT_KEY, charter)

    payload = {
        "accepted": True,
        "opened": list(result.opened),
        "attached": list(result.attached),
        "intake": list(result.intake),
        "refused": list(result.refused),
        "findings": list(result.findings),
    }
    _emit(
        args,
        f"opened {len(result.opened)} case(s): {', '.join(result.opened) or 'none'}; "
        f"attached {len(result.attached)}, intake {len(result.intake)}, "
        f"refused {len(result.refused)}",
        payload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="valor-improve")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("propose")
    p.add_argument("--case", required=True)
    p.add_argument("--action-type", default="investigate")
    p.add_argument("--payload", required=True)
    p.add_argument("--action-id", default=None)
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("propose-amendment")
    p.add_argument("--case", required=True)
    p.add_argument("--request", required=True)
    p.set_defaults(func=cmd_propose_amendment)

    p = sub.add_parser("pause")
    p.add_argument("--case", default=None)
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("resume")
    p.add_argument("--case", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("doctor")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("case")
    case_sub = p.add_subparsers(dest="case_command", required=True)
    p_show = case_sub.add_parser("show")
    p_show.add_argument("--case", required=True)
    p_show.add_argument("--tail", type=int, default=10)
    p_show.set_defaults(func=cmd_case_show)
    p_explain = case_sub.add_parser("explain")
    p_explain.add_argument("--case", required=True)
    p_explain.set_defaults(func=cmd_case_explain)
    p_open = case_sub.add_parser("open")
    p_open.add_argument("--evidence-ids", default=None, help="comma-separated evidence ids")
    p_open.set_defaults(func=cmd_case_open)

    p = sub.add_parser("budget")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("export")
    p.add_argument("--root", default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import")
    p.add_argument("--archive", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("replay-projection")
    p.add_argument("--case", required=True)
    p.set_defaults(func=cmd_replay_projection)

    p = sub.add_parser("release")
    release_sub = p.add_subparsers(dest="release_command", required=True)
    p_compare = release_sub.add_parser("compare")
    p_compare.set_defaults(func=cmd_release_compare)

    # lane 5 (#3217): the planner's ranking snapshot
    p = sub.add_parser("ranking")
    p.add_argument("--at", default=None, help="a $CF: snapshot reference; default: the latest")
    p.set_defaults(func=cmd_ranking)

    # lane 5 (#3217): the research session's own subcommands
    p = sub.add_parser("brief")
    p.add_argument("--case", required=True)
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("investigation")
    inv_sub = p.add_subparsers(dest="investigation_command", required=True)
    p_iopen = inv_sub.add_parser("open")
    p_iopen.add_argument("--kind", required=True)
    p_iopen.add_argument("--case", default=None)
    p_iopen.add_argument("--uncertainty", required=True)
    p_iopen.add_argument("--query", required=True)
    p_iopen.add_argument("--decision-affected", required=True)
    p_iopen.add_argument("--expected-information-value", required=True)
    p_iopen.add_argument("--state", default="open")
    p_iopen.set_defaults(func=cmd_investigation_open)
    p_irecord = inv_sub.add_parser("record")
    p_irecord.add_argument("--id", required=True)
    p_irecord.add_argument("--claims", required=True, help="JSON list, or @path to one")
    p_irecord.add_argument("--sources", default=None, help="JSON list, or @path to one")
    p_irecord.set_defaults(func=cmd_investigation_record)
    p_iresolve = inv_sub.add_parser("resolve")
    p_iresolve.add_argument("--id", required=True)
    p_iresolve.add_argument("--interpretation", required=True)
    p_iresolve.add_argument("--assumption", default=None)
    p_iresolve.add_argument("--assumption-detail", default=None, help="JSON object, or @path")
    p_iresolve.add_argument("--disposition", default=None)
    p_iresolve.add_argument("--resource-name", default=None)
    p_iresolve.set_defaults(func=cmd_investigation_resolve)
    p_ilist = inv_sub.add_parser("list")
    p_ilist.add_argument("--case", default=None)
    p_ilist.set_defaults(func=cmd_investigation_list)

    p = sub.add_parser("revise-model")
    p.add_argument("--case", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--rationale", required=True)
    p.add_argument("--prediction", required=True)
    p.set_defaults(func=cmd_revise_model)

    # lane 5 (#3217): register the planner tick as one of lane 6's comparison
    # arms, only when lane 6's module is importable. Method-body import in
    # tools/improvement_plan_arm.py; nothing here imports lane 6 at module level.
    try:
        from tools.improvement_recursion.arms import register_arm_runner
    except ImportError:
        register_arm_runner = None
    if register_arm_runner is not None:
        from tools.improvement_plan_arm import PlannerArmRunner

        register_arm_runner(PlannerArmRunner())

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
