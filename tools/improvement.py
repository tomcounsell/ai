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

PROJECT_KEY = "valor"


def _project_key(args) -> str:
    return getattr(args, "project_key", None) or PROJECT_KEY


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

    validation_reason = _validate_case_for_proposal(case_id)
    if validation_reason:
        _emit(
            args, f"refused: {validation_reason}", {"accepted": False, "reason": validation_reason}
        )
        return 1

    # Data Flow step 2, blocker fix (#3315 review): the payload is written and
    # re-hashed through the verifying store BEFORE the lease is taken, so a
    # refused proposal's content is still on disk and loadable via the
    # reference kept on the evidence row below -- previously only the digest
    # was journaled and the content itself existed nowhere.
    from models.verifying_artifact_store import VerifyingArtifactStore

    store = VerifyingArtifactStore()
    artifact_key = f"{case_id}-{action_id or 'noaction'}-{payload_digest.split(':', 1)[-1][:16]}"
    artifact_ref = store.save(
        payload_bytes, key=artifact_key, model_class_name="ImprovementProposal"
    )

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
            action_id=action_id or "",
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
    _emit(args, f"paused: {result.accepted}", {"accepted": result.accepted})
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
    try:
        if args.force:
            for aid in blocking:
                cancel_wedge(PROJECT_KEY, case_id, aid, generation=generation, by="operator")
        head = read_head(PROJECT_KEY, case_id)
        if head is not None and head.paused:
            journal_resume(PROJECT_KEY, case_id, generation=generation, by="operator")
            _emit(args, "resumed", {"accepted": True, "cancelled": len(blocking)})
            return 0
    finally:
        lease.release(lease_key, generation)
    print(f"not paused; cancelled {len(blocking)} intent(s)")
    return 0


def cmd_doctor(args) -> int:
    try:
        from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
        from tools.improvement_control.intents import list_intents
        from tools.improvement_control.journal import read_head
    except Exception as e:
        print(f"namespace unreachable: {e}")
        return 2

    # Blocker fix (#3315 review): `read_head`/`list_intents` used to run
    # OUTSIDE this guard, so a control-namespace `ConnectionError` mid-loop
    # raised a traceback (exit 1) instead of reporting the same break-glass
    # message the import-time guard above already gives (issue acceptance
    # criterion 4 / plan Success Criterion 2's outage drill). One guard now
    # covers the ORM query and every per-case read, so a reachable-then-lost
    # namespace never gets to print a partial "clean" or wedged/paused list.
    try:
        cases = []
        for state in OPEN_CASE_STATES:
            cases.extend(ImprovementCase.query.filter(project_key=PROJECT_KEY, state=state))

        paused = []
        wedged = []
        for case in cases:
            head = read_head(PROJECT_KEY, case.id)
            if head is not None and head.paused:
                paused.append(case.id)
            for intent in list_intents(PROJECT_KEY, case.id):
                if intent.state == "reconciliation_required":
                    wedged.append((case.id, intent.action_id))
    except Exception as e:
        print(f"namespace unreachable: {e}")
        return 2

    if not paused and not wedged:
        _emit(
            args,
            "no paused heads, no stale intents, no outstanding reservations",
            {"paused": [], "wedged": []},
        )
        return 0

    lines = []
    if paused:
        lines.append("Paused heads: " + ", ".join(paused))
    if wedged:
        lines.append("Wedged intents: " + ", ".join(f"{c}/{a}" for c, a in wedged))
    _emit(args, "\n".join(lines), {"paused": paused, "wedged": [list(w) for w in wedged]})
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
    unknown_receipts = []
    for state in OPEN_CASE_STATES:
        for case in ImprovementCase.query.filter(project_key=PROJECT_KEY, state=state):
            for intent in list_intents(PROJECT_KEY, case.id):
                if intent.state in ("admitted", "materialized", "running"):
                    slot_count += 1

    unit2 = paid_inference_meter.status_dict(PROJECT_KEY)
    unit3 = infrastructure_budget.status_dict(project_key=PROJECT_KEY)

    payload = {
        "unit1_slots_in_use": slot_count,
        "unit1_max_concurrent": settings.improvement.max_concurrent_research_sessions,
        "unit2": unit2,
        "unit3": unit3,
        "unit2_receipted_unknown": unknown_receipts,
    }
    _emit(
        args,
        f"unit1: {slot_count}/{settings.improvement.max_concurrent_research_sessions} slots\n"
        f"unit2: reserved=${unit2['reserved_usd']:.2f} settled=${unit2['settled_usd']:.2f} "
        f"window={unit2['day_key']}\n"
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
