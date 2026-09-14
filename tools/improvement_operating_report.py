"""Charter section 2's progress report, generated rather than written (lane 7, #3274).

The report answers five questions from records, including the fifth — what
still prevents the intended result — assembled from recorded provisional
assumptions, ``unknown`` entries in the latest ``resource_probe`` row, and
acquisitions refused for want of a forecast. It is structurally impossible
for the fifth answer to come back empty while those inputs are non-empty.

Deliberately absent: sandbox count, uptime, and token volume. Charter section
2 names all three as things that do not establish improvement, so this module
has no function returning any of them, and a test asserts that absence.

Trial-session convention: task 9 records each sandbox trial session as an
evidence row whose ``source_ref`` starts with ``TRIAL_REF_PREFIX``. The read
is kind-agnostic and prefix-scoped in Python over the bounded recency window —
the same shape as ``already_recorded`` — so no new evidence kind and no new
index partition is needed to name which sessions ran remotely. From a
zero-sandbox position the first answer is "none", which is the primary case,
not the edge case: it is the state the first report is generated in.

Each answer degrades independently: a record source that raises degrades its
one answer to an explicit "could not be determined" and keeps the other four.
"""

from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)

#: Trial sessions are named by this ``source_ref`` prefix (see module docstring).
TRIAL_REF_PREFIX = "lane7-sandbox-trial-"

#: Recorded provisional assumptions, charter section 9's shape: evidence,
#: confidence, consequence, and the observation that would overturn each.
#: The scale question and the Decision 8 fuel answer live here rather than in
#: prose so the fifth answer cannot drift into optimism between runs.
#: Under the recorded mode-B auth verdict
#: (docs/infra/improvement-cloud-execution.md, Auth verdict) the sandbox is
#: phrased in verdict terms rather than mode-A terms: tasks 9/10 do not run,
#: phase 3 spends nothing, and the fifth answer carries the verdict.
PROVISIONAL_ASSUMPTIONS: tuple[dict, ...] = (
    {
        "statement": (
            "RSI sessions may consume Claude subscription capacity from a "
            "cloud sandbox by running the official Claude Code CLI on a "
            "remote host we operate. Under the recorded mode-B auth verdict "
            "(docs/infra/improvement-cloud-execution.md), Cloudflare "
            "Containers is not affirmed as such a host, so this stays an "
            "unexercised working position and no subscription-auth trial runs."
        ),
        "evidence": (
            "Anthropic supports the official CLI on remote hosts; token plus "
            "third-party harness is blocked, token plus official CLI is not. "
            "Tom's Decision 8 (2026-09-09, parent plan item 8) adopts this "
            "as the lane's working position within ordinary use, with no "
            "charter amendment."
        ),
        "confidence": "medium",
        "consequence": (
            "If wrong, sandbox sessions cannot authenticate and the "
            "first-month cloud expectation is unreachable by infrastructure "
            "work."
        ),
        "overturned_by": (
            "A provider treating remote subscription-CLI use as outside "
            "ordinary use, or metering showing it crowding out client-work "
            "capacity. Either pauses remote execution and proposes a "
            "charter amendment."
        ),
    },
    {
        "statement": (
            "A continuously-running sandbox fleet falls inside subscription "
            "limits' 'ordinary, individual usage' standard. Under the "
            "recorded mode-B auth verdict "
            "(docs/infra/improvement-cloud-execution.md) no such fleet runs, "
            "so this stays an untested reading with its overturn observation "
            "armed."
        ),
        "evidence": (
            "Undocumented either way; Decision 8 authorizes proceeding under "
            "this reading within ordinary use."
        ),
        "confidence": "low",
        "consequence": (
            "If wrong, the lane's honest output is the fifth answer, not a running fleet."
        ),
        "overturned_by": ("A rate limit or account action attributable to sandbox usage."),
    },
    {
        "statement": (
            "Auth verdict is MODE B (docs/infra/improvement-cloud-execution.md): "
            "Cloudflare Containers is not affirmed as a remote host we "
            "operate rather than a hosted service consuming the "
            "subscription, so tasks 9 and 10 do not run, phase 3 spends "
            "nothing, and the fifth answer of the section 2 progress report "
            "carries this verdict."
        ),
        "evidence": (
            "Task 4 vendor re-read: Cloudflare's Containers overview describes "
            "serverless containers run without managing infrastructure, spun "
            "up on demand by Worker code; no vendor statement permits "
            "subscription-CLI fleets in managed containers, so ambiguity "
            "resolves against us by task 4's own rule."
        ),
        "confidence": "high",
        "consequence": (
            "No subscription-auth trial runs; the lane ships the metered unit "
            "3, the teardown policy, the resolved resource position, the "
            "provider decision, and the section 2 report with this verdict as "
            "its fifth answer."
        ),
        "overturned_by": (
            "An affirmative vendor finding that the chosen runtime is a host "
            "we operate rather than a hosted service consuming the "
            "subscription on our behalf."
        ),
    },
)


#: The recorded mode-B auth verdict, assembled into the fifth answer on every
#: run. Tasks 9/10 do not run, phase 3 spends nothing, and this verdict is a
#: standing input alongside the recorded assumptions, the ``unknown`` probe
#: entries, and the refused acquisitions — so the fifth answer is non-empty
#: whenever any of those inputs is non-empty.
MODE_B_AUTH_VERDICT = (
    "Auth verdict MODE B (docs/infra/improvement-cloud-execution.md): "
    "Cloudflare Containers is not affirmed as a remote host we operate "
    "rather than a hosted service consuming the subscription, so no "
    "subscription-auth trial runs; tasks 9 and 10 do not run and phase 3 "
    "spends nothing."
)


def read_probe_row(project_key: str) -> dict | None:
    """The newest complete ``resource_probe`` record, or None."""
    from models.improvement_evidence import ImprovementEvidence

    for row in ImprovementEvidence.recent(project_key, limit=200):
        if row.kind == "resource_probe":
            return {
                "source_ref": row.source_ref,
                "text": row.text,
                "detail": row.detail,
                "created_at": str(row.created_at),
            }
    return None


def read_reservations(project_key: str) -> list[dict]:
    """Unit-3 ledger rows: admissions, refusals, releases, settlements."""
    from models.improvement_infrastructure_ledger import InfrastructureReservation

    return [
        {
            "resource": r.resource,
            "state": r.state,
            "amount_usd": r.amount_usd,
            "forecast_usd": r.forecast_usd,
            "settled_usd": r.settled_usd,
            "reason": r.reason,
            "window_key": r.window_key,
            "budget_week_start": r.budget_week_start,
            "budget_day_boundary": r.budget_day_boundary,
        }
        for r in InfrastructureReservation.query.filter(project_key=project_key)
    ]


def read_receipts(project_key: str) -> list[dict]:
    """``spend_receipt`` rows: settlements and booked overruns."""
    from models.improvement_evidence import ImprovementEvidence

    return [
        {
            "source_ref": r.source_ref,
            "text": r.text,
            "detail": r.detail,
        }
        for r in ImprovementEvidence.recent(project_key, limit=500)
        if r.kind == "spend_receipt"
    ]


def read_trial_records(project_key: str) -> list[dict]:
    """Sandbox trial session records, by the ``source_ref`` prefix convention."""
    from models.improvement_evidence import ImprovementEvidence

    return [
        {
            "source_ref": r.source_ref,
            "text": r.text,
            "detail": r.detail,
        }
        for r in ImprovementEvidence.recent(project_key, limit=500)
        if (r.source_ref or "").startswith(TRIAL_REF_PREFIX)
    ]


def read_assumptions() -> list[dict]:
    """The recorded provisional assumptions."""
    return [dict(a) for a in PROVISIONAL_ASSUMPTIONS]


def _answer_sessions(trials: list[dict]) -> str:
    if not trials:
        return (
            "No RSI session has run in a cloud sandbox yet. "
            "All recorded sessions ran on developer workstations."
        )
    names = sorted({t["source_ref"] for t in trials})
    return f"{len(names)} trial session(s) ran in a cloud sandbox: " + ", ".join(names)


def _answer_unattended(trials: list[dict]) -> str:
    if not trials:
        return (
            "Unattended operation is not demonstrated: no sandbox trial has "
            "run, so no credential-refresh boundary has been crossed and no "
            "induced crash has been survived."
        )
    recovered = [t for t in trials if "recovered" in (t["text"] or "")]
    if recovered:
        return (
            f"Unattended operation demonstrated across {len(recovered)} "
            "recovery record(s); see the trial records for the refresh "
            "boundaries crossed."
        )
    return (
        "Sandbox trials ran but no recovery record is on file: unattended "
        "operation across a credential-refresh boundary is not yet evidenced."
    )


def _answer_resources(probe: dict | None) -> str:
    if probe is None:
        return "Resource position unknown: no probe record is on file."
    verified = []
    import json as _json

    try:
        detail = _json.loads(probe["detail"] or "{}")
    except ValueError:
        detail = {}
    for name, entry in detail.items():
        if isinstance(entry, dict) and entry.get("state") == "verified":
            verified.append(name)
    if not verified:
        return f"No resource is verified. Latest probe ({probe['source_ref']}): {probe['text']}"
    return "Verified resources sustaining the loop: " + ", ".join(sorted(verified))


def _answer_cost(
    reservations: list[dict],
    receipts: list[dict],
    window_key: str,
    week_start: str,
    day_boundary: str,
    cap_usd: float,
) -> str:
    in_window = [r for r in reservations if r["window_key"] == window_key]
    reserved = sum(r["amount_usd"] or 0.0 for r in in_window if r["state"] == "reserved")
    settled_rows = [r for r in in_window if r["state"] == "settled"]
    settled = sum(r["settled_usd"] or 0.0 for r in settled_rows)
    return (
        f"Window {window_key} ({week_start} start, {day_boundary} boundary, "
        f"cap ${cap_usd:.2f}): reserved ${reserved:.2f}, settled "
        f"${settled:.2f} across {len(settled_rows)} settlement(s), "
        f"{len(receipts)} spend receipt(s) on file."
    )


def _answer_prevents(
    assumptions: list[dict],
    probe: dict | None,
    reservations: list[dict],
    auth_mode_blocked: bool,
) -> str:
    blockers: list[str] = []
    blockers.append(MODE_B_AUTH_VERDICT)
    for assumption in assumptions:
        blockers.append(
            f"Assumption ({assumption['confidence']} confidence): "
            f"{assumption['statement']} Overturned by: {assumption['overturned_by']}"
        )
    import json as _json

    if probe:
        try:
            detail = _json.loads(probe.get("detail") or "{}")
        except ValueError:
            detail = {}
        unknowns = sorted(
            name
            for name, entry in detail.items()
            if isinstance(entry, dict) and entry.get("state") == "unknown"
        )
        if unknowns:
            blockers.append(
                "Unverified resources (treated as absent for acquisition): " + ", ".join(unknowns)
            )
    refused = [r for r in reservations if r["state"] == "refused" and r["reason"]]
    for row in refused:
        blockers.append(f"Refused acquisition ({row['resource']}): {row['reason']}")
    if auth_mode_blocked:
        blockers.append(
            "No subscription-auth trial has run: under the recorded mode-B "
            "auth verdict (docs/infra/improvement-cloud-execution.md) tasks "
            "9 and 10 do not run, so the trial-dependent answers report "
            "absence rather than failing."
        )
    if not blockers:
        blockers.append(
            "No recorded blocker. Either the operating model has moved, or "
            "an assumption went unrecorded — the latter is a reporting defect."
        )
    return "What still prevents mostly-cloud operation: " + " ".join(blockers)


def generate_report(
    *,
    project_key: str = "default",
    window_key: str | None = None,
    settings=None,
    now=None,
) -> dict:
    """Generate the five answers from records.

    ``auth_mode_blocked`` is True when phase 3 has not run (mode B or an
    unlanded vault writer): the trial-dependent answers then report absence
    rather than failing. The report is re-runnable by design — regenerate
    after phase 3 lands and the two runs are comparable.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    from tools.infrastructure_budget import current_window

    if settings is None:
        from config.settings import settings as app_settings

        settings = app_settings
    improvement = settings.improvement
    cap = float(improvement.weekly_infrastructure_usd)
    week_start = str(improvement.budget_week_start)
    day_boundary = str(improvement.budget_day_boundary)
    moment = now or _datetime.now(_UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_UTC)
    current_key, _, _ = current_window(moment, week_start)
    window_key = window_key or current_key

    def guarded(name: str, fn):
        try:
            return True, fn()
        except Exception as exc:  # noqa: BLE001 — degrade one answer, keep four
            logger.warning("operating report: %s source failed: %s", name, exc)
            return False, None

    probe_ok, probe = guarded("probe", lambda: read_probe_row(project_key))
    reservations_ok, reservations = guarded("reservations", lambda: read_reservations(project_key))
    receipts_ok, receipts = guarded("receipts", lambda: read_receipts(project_key))
    trials_ok, trials = guarded("trials", lambda: read_trial_records(project_key))
    assumptions_ok, assumptions = guarded("assumptions", read_assumptions)

    answers: dict[str, str] = {}
    if not trials_ok:
        answers["sessions"] = "could not be determined: trial records unavailable"
        answers["unattended"] = "could not be determined: trial records unavailable"
    else:
        answers["sessions"] = _answer_sessions(trials)
        answers["unattended"] = _answer_unattended(trials)
    if not probe_ok:
        answers["resources"] = "could not be determined: probe record unavailable"
    else:
        answers["resources"] = _answer_resources(probe)
    if not reservations_ok or not receipts_ok:
        answers["cost"] = "could not be determined: ledger unavailable"
    else:
        answers["cost"] = _answer_cost(
            reservations, receipts, window_key, week_start, day_boundary, cap
        )
    if not assumptions_ok:
        answers["prevents"] = "could not be determined: assumptions unavailable"
    else:
        answers["prevents"] = _answer_prevents(
            assumptions,
            probe if probe_ok else {},
            reservations if reservations_ok else [],
            not trials_ok or not trials,
        )
        if not probe_ok:
            answers["prevents"] += " The probe record itself was unavailable."
        if not trials_ok:
            answers["prevents"] += " Trial records themselves were unavailable."
    answers["window_key"] = window_key
    answers["budget_week_start"] = week_start
    answers["budget_day_boundary"] = day_boundary
    return answers


def main(argv: list[str] | None = None) -> int:
    """Entry point the agent uses: ``python -m tools.improvement_operating_report``."""
    import json as _json

    parser = argparse.ArgumentParser(
        prog="improvement_operating_report",
        description=(
            "Charter section 2 progress report, generated from records: which "
            "sessions ran in cloud sandboxes, whether the loop continues "
            "unattended, what resources sustain it, what they cost against "
            "unit 3, and what still prevents mostly-cloud operation. "
            "Machine-generated so it cannot drift into optimistic prose."
        ),
    )
    parser.add_argument(
        "--project-key",
        default="default",
        help="evidence partition to read (default: %(default)s)",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    print(_json.dumps(generate_report(project_key=args.project_key), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
