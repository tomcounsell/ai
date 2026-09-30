"""The qualified-result report and the seeded-input rule (lane 5, #3217, task 6).

:func:`build_report` renders one case from records alone, in the plan's
order: the case and its charter digest; the ranking positions it held (the
snapshot chain); the investigations (claims with URLs and dates,
assumptions); the model revisions with their predictions; the experiment
(hypothesis, mechanism, falsifier, contract digest, envelope, candidate
versus incumbent); the evaluation (verdict, effect and interval per
endpoint, correction, blinding, trials, identity scan, judge calibration);
then three mandatory sections whose content is derived, never authored:

- **What was measured**: endpoints, corpus digest, query count, holdout
  partition, read from the frozen protocol.
- **What this does not establish**: always non-empty. No claim above "loop
  operational"; the sample size and margin; that a retrieval-parameter
  gain says nothing about agent behavior; every ``metering="estimated"``
  spend receipt on the case; the novelty rule's limit; and the
  seeded-inputs line naming every case any of whose evidence
  :func:`is_seeded` reports, with its marker text.
- **What would change the answer**: the falsifier, the overturning
  observation of every assumption cited, and a larger sample.

:func:`is_seeded` is the single definition of "the builder planted this":
``source_ref`` starting with ``seed:`` (rows the integration tests write
directly) or a ``detail`` JSON object carrying a ``seed`` key (rows the
inspiration adapter wrote from a seeded memory). Nothing else counts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from models.improvement_case import ImprovementCase
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_evidence import ImprovementEvidence
from models.improvement_experiment import ImprovementExperiment
from models.improvement_investigation import ImprovementInvestigation
from models.improvement_model_revision import ImprovementModelRevision

#: How far back the ranking-position walk follows ``previous_ref``.
SNAPSHOT_WALK_LIMIT = 100

HEADING_MEASURED = "What was measured"
HEADING_LIMITS = "What this does not establish"
HEADING_CHANGE = "What would change the answer"

NO_EVIDENCE_LINE = "No evidence rows are attached to this case."


# ---------------------------------------------------------------------------
# Seeded inputs
# ---------------------------------------------------------------------------


def _detail_object(evidence) -> dict | None:
    raw = getattr(evidence, "detail", None)
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def seed_marker(evidence) -> str | None:
    """The seed marker text on a seeded row, or ``None`` for an observed one."""
    source_ref = getattr(evidence, "source_ref", None)
    if isinstance(source_ref, str) and source_ref.startswith("seed:"):
        return source_ref
    detail = _detail_object(evidence)
    if detail is not None and "seed" in detail:
        return str(detail["seed"])
    return None


def is_seeded(evidence) -> bool:
    """True when the builder planted this row rather than the observer collecting it."""
    return seed_marker(evidence) is not None


def _json_list(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _evidence_rows(project_key: str, case) -> list:
    rows = []
    for eid in _json_list(getattr(case, "evidence_ids", None)):
        try:
            row = ImprovementEvidence.query.get(project_key=project_key, id=eid)
        except Exception:  # noqa: BLE001 -- an unkeyable id is a missing row
            row = None
        if row is not None:
            rows.append(row)
    return rows


def seeded_cases(project_key: str) -> list[tuple[str, str]]:
    """``(case_id, seed_marker)`` for every case any of whose evidence is seeded,
    ordered by case id; one entry per case, the first marker found."""
    found: list[tuple[str, str]] = []
    for case in ImprovementCase.query.filter(project_key=project_key):
        for row in _evidence_rows(project_key, case):
            marker = seed_marker(row)
            if marker is not None:
                found.append((case.id, marker))
                break
    return sorted(found)


# ---------------------------------------------------------------------------
# Record readers
# ---------------------------------------------------------------------------


def _created(row) -> str:
    value = getattr(row, "created_at", None)
    return value.isoformat() if isinstance(value, datetime) else ""


def _one_line(text, limit: int = 240) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _claims_and_notes(raw) -> tuple[list[dict], list]:
    """Claims and notes off the ``claims`` field: the ``{"claims", "notes"}``
    envelope task 4 writes, or lane 3's bare list of claims."""
    envelope = _json_object(raw)
    if envelope:
        claims = [c for c in envelope.get("claims", []) if isinstance(c, dict)]
        return claims, list(envelope.get("notes", []))
    return [c for c in _json_list(raw) if isinstance(c, dict)], []


def _investigations(project_key: str, case_id: str) -> list:
    rows = []
    for state in ("open", "awaiting_authorization", "resolved", "abandoned", "expired"):
        rows.extend(
            r
            for r in ImprovementInvestigation.query.filter(project_key=project_key, state=state)
            if getattr(r, "case_id", None) == case_id
        )
    return sorted(rows, key=_created)


def _experiments(project_key: str, case_id: str) -> list:
    rows = [
        e
        for e in ImprovementExperiment.query.filter(project_key=project_key)
        if getattr(e, "case_id", None) == case_id
    ]
    return sorted(rows, key=_created)


def _evaluations(project_key: str, experiment_ids: set[str]) -> list:
    rows = []
    for state in ("complete", "invalidated", "pending"):
        rows.extend(
            e
            for e in ImprovementEvaluation.query.filter(project_key=project_key, state=state)
            if getattr(e, "experiment_id", None) in experiment_ids
        )
    return sorted(rows, key=_created)


def _revisions_citing(project_key: str, evidence_ids: set[str]) -> list:
    rows = [
        r
        for r in ImprovementModelRevision.query.filter(project_key=project_key)
        if evidence_ids & set(_json_list(getattr(r, "evidence_ids", None)))
    ]
    return sorted(rows, key=_created)


def _estimated_receipts(project_key: str, case_id: str) -> list[dict]:
    receipts = []
    for row in ImprovementEvidence.query.filter(project_key=project_key, kind="spend_receipt"):
        detail = _detail_object(row) or {}
        if detail.get("case_id") == case_id and detail.get("metering") == "estimated":
            receipts.append(detail)
    return receipts


def _ranking_history(project_key: str, case_id: str, *, store) -> list[str]:
    """Lines for every snapshot in the chain that names the case, newest first."""
    from tools.improvement_ranking import latest_snapshot, load_snapshot, snapshot_digest

    lines: list[str] = []
    try:
        doc = latest_snapshot(project_key, store=store)
    except Exception as exc:  # noqa: BLE001 -- integrity or absence: reported, never raised
        return [f"ranking history unavailable: {exc}"]
    hops = 0
    while doc is not None and hops < SNAPSHOT_WALK_LIMIT:
        hops += 1
        at = doc.get("at", "?")
        for entry in doc.get("order", []):
            if entry.get("case_id") == case_id:
                factors = entry.get("factors") or {}
                factor_text = ", ".join(f"{k}={v}" for k, v in sorted(factors.items()))
                blocked = f" blocked_by={entry['blocked_by']}" if entry.get("blocked_by") else ""
                lines.append(f"- position {entry.get('position')} at {at}{blocked} ({factor_text})")
        for left in (doc.get("diff") or {}).get("left", []):
            if left.get("case_id") == case_id:
                lines.append(f"- left the open set at {at}: {left.get('reason')}")
        for moved in (doc.get("diff") or {}).get("moved", []):
            if moved.get("case_id") == case_id:
                lines.append(
                    f"- moved {moved.get('from')} -> {moved.get('to')} at {at}: {moved.get('why')}"
                )
        previous = doc.get("previous_ref")
        if not previous:
            break
        try:
            doc = load_snapshot(previous, store=store)
        except Exception as exc:  # noqa: BLE001 -- the chain ends where it cannot be verified
            lines.append(f"- earlier snapshot {snapshot_digest(previous)[:16]} unreadable: {exc}")
            break
    return lines or ["- no ranking snapshot names this case"]


def _manifest(experiment) -> dict | None:
    from tools.improvement_eval.runner import read_content

    try:
        text = read_content(experiment, "manifest")
    except Exception as exc:  # noqa: BLE001 -- an unverifiable manifest is reported, never raised
        return {"unverifiable": str(exc)}
    return _json_object(text) if text else None


def _protocol(experiment, *, store) -> dict | None:
    from tools.improvement_eval.runner import load_protocol

    try:
        return load_protocol(experiment, store=store)
    except Exception:  # noqa: BLE001 -- a protocol that cannot load is "nothing measured"
        return None


def _identity_scan(evaluation) -> str:
    for line in (getattr(evaluation, "notes", None) or "").splitlines():
        if line.startswith("blinding leak"):
            return line
    blinded = getattr(evaluation, "blinded", None)
    if blinded is True:
        return "identity scan: no identity token reached a judge"
    if blinded is False:
        return "identity scan: a leak was recorded"
    return "identity scan: no judged trials, nothing to scan"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def section(text: str, heading: str) -> str:
    """The body under ``## heading`` up to the next ``## `` heading."""
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    nxt = text.find("\n## ", body_start)
    return text[body_start:] if nxt < 0 else text[body_start:nxt]


def _case_section(project_key: str, case, evidence_rows: list) -> list[str]:
    lines = [
        "## Case",
        f"- id: {case.id}",
        f"- title: {_one_line(case.title)}",
        f"- state: {case.state}",
        f"- priority_area: {case.priority_area}",
        f"- charter_digest: {case.charter_digest or 'unpinned'}",
        f"- dedup_identity: {case.dedup_identity or '-'}",
        f"- blocked_by: {case.blocked_by or '-'}",
        f"- rejected_reason: {_one_line(case.rejected_reason) or '-'}",
        f"- ranking_rationale: {_one_line(case.ranking_rationale) or '-'}",
    ]
    if not evidence_rows:
        lines.append(f"- evidence: {NO_EVIDENCE_LINE}")
    else:
        lines.append(f"- evidence ({len(evidence_rows)} rows):")
        for row in evidence_rows:
            marker = seed_marker(row)
            seeded = f" [seeded: {marker}]" if marker else ""
            lines.append(
                f"  - {row.id} {row.kind}/{row.classification} {row.source_ref or '-'}"
                f"{seeded}: {_one_line(row.text, 120)}"
            )
    return lines


def _investigation_section(rows: list) -> tuple[list[str], list[str], list[str]]:
    """Lines, the overturning observations cited, and the assumptions."""
    lines = ["## Investigations"]
    overturning: list[str] = []
    assumptions: list[str] = []
    if not rows:
        lines.append("- none")
        return lines, overturning, assumptions
    for row in rows:
        lines.append(
            f"- {row.id} {row.kind} {row.state} stage={row.stage or 'draft'}: "
            f"{_one_line(row.query)}"
        )
        claims, notes = _claims_and_notes(getattr(row, "claims", None))
        for claim in claims:
            lines.append(
                f"  - claim: {_one_line(claim.get('claim'))} "
                f"({claim.get('url', '-')}, retrieved {claim.get('retrieved_at', '-')})"
            )
        for note in notes:
            text = note.get("claim") if isinstance(note, dict) else note
            lines.append(f"  - note: {_one_line(text)}")
        if getattr(row, "provisional_assumption", None):
            detail = _json_object(getattr(row, "assumption_detail", None))
            lines.append(f"  - assumption: {_one_line(row.provisional_assumption)}")
            assumptions.append(str(row.provisional_assumption))
            observation = detail.get("overturning_observation")
            if observation:
                lines.append(f"    overturned by: {_one_line(observation)}")
                overturning.append(str(observation))
        if getattr(row, "interpretation", None):
            lines.append(f"  - interpretation: {_one_line(row.interpretation)}")
    return lines, overturning, assumptions


def _revision_section(rows: list) -> list[str]:
    lines = ["## Model revisions"]
    if not rows:
        lines.append("- none cite this case's evidence")
        return lines
    for row in rows:
        lines.append(
            f"- {row.id} rev {getattr(row, 'revision', '?')} {row.state}: {_one_line(row.summary)}"
        )
        lines.append(f"  - prediction: {_one_line(getattr(row, 'prediction', None)) or '-'}")
        digest = getattr(row, "research_process_digest", None)
        if digest:
            lines.append(f"  - research_process_digest: {digest}")
    return lines


def _experiment_section(rows: list) -> tuple[list[str], list[str], list[dict]]:
    """Lines, the falsifiers, and the manifests (one per experiment)."""
    lines = ["## Experiment"]
    falsifiers: list[str] = []
    manifests: list[dict] = []
    if not rows:
        lines.append("- no experiment has been proposed for this case")
        return lines, falsifiers, manifests
    from tools.improvement_experiment import experiment_notes

    for row in rows:
        manifest = _manifest(row) or {}
        manifests.append(manifest)
        notes = experiment_notes(row)
        if row.falsifier:
            falsifiers.append(str(row.falsifier))
        candidate = manifest.get("candidate") or notes.get("candidate") or {}
        lines.extend(
            [
                f"- {row.id} state={row.state}",
                f"  - hypothesis: {_one_line(row.hypothesis)}",
                f"  - mechanism: {_one_line(row.mechanism)}",
                f"  - falsifier: {_one_line(row.falsifier)}",
                f"  - contract_digest: {row.contract_digest or 'unfrozen'}",
                f"  - envelope: {manifest.get('envelope') or notes.get('envelope') or '-'}",
                f"  - candidate: {json.dumps(candidate, sort_keys=True)}",
                f"  - incumbent: {json.dumps(manifest.get('incumbent') or {}, sort_keys=True)}",
                f"  - base_revision: {manifest.get('base_revision') or '-'}"
                f" candidate_ref: {manifest.get('candidate_ref') or '-'}",
            ]
        )
        if manifest.get("unverifiable"):
            lines.append(f"  - manifest unverifiable: {manifest['unverifiable']}")
        prior = notes.get("prior_answers") or []
        if prior:
            lines.append(
                "  - prior answers: "
                + "; ".join(
                    f"{p.get('ref')} ({p.get('why')})" for p in prior if isinstance(p, dict)
                )
            )
    return lines, falsifiers, manifests


def _evaluation_section(rows: list) -> list[str]:
    from tools.improvement_eval.runner import calibration_record

    lines = ["## Evaluation"]
    if not rows:
        lines.append("- no evaluation has run for this case")
        return lines
    for row in rows:
        verdict = "invalidated" if row.state == "invalidated" else (row.verdict or "-")
        lines.append(f"- {row.id} state={row.state} verdict={verdict}")
        lines.append(
            f"  - trials: {row.trials}; blinded: {row.blinded}; correction: {row.correction or '-'}"
        )
        effect = _json_object(getattr(row, "effect", None))
        interval = _json_object(getattr(row, "confidence_interval", None))
        for endpoint in sorted(set(effect) | set(interval)):
            ci = interval.get(endpoint) or {}
            lines.append(
                f"  - {endpoint}: effect {effect.get(endpoint, '-')} "
                f"interval [{ci.get('lower', '-')}, {ci.get('upper', '-')}] "
                f"n={ci.get('n', '-')} adjusted_p={ci.get('adjusted_p_value', '-')}"
            )
        lines.append(f"  - {_identity_scan(row)}")
        calibration = calibration_record(row)
        if calibration:
            lines.append(
                "  - judge calibration: "
                + ", ".join(f"{k}={v}" for k, v in sorted(calibration.items()))
            )
        else:
            lines.append("  - judge calibration: not recorded (injected roster or no judge ran)")
        for line in (row.notes or "").splitlines():
            if line.strip() and not line.startswith("calibration: "):
                lines.append(f"  - note: {_one_line(line)}")
    return lines


def _measured_section(
    protocol: dict | None, manifest: dict
) -> tuple[list[str], int | None, float | None]:
    lines = [f"## {HEADING_MEASURED}"]
    if protocol is None:
        lines.append("- No experiment has been frozen for this case; nothing was measured.")
        return lines, None, None
    queries = protocol.get("queries") or []
    endpoints = protocol.get("endpoints") or []
    thresholds = protocol.get("thresholds") or {}
    margins = [float(t.get("margin", 0.0)) for t in thresholds.values() if isinstance(t, dict)]
    margin = max(margins) if margins else None
    corpus_digest = (protocol.get("baseline") or {}).get("corpus_digest") or manifest.get(
        "corpus_digest"
    )
    lines.extend(
        [
            f"- endpoints: {', '.join(endpoints) or '-'}",
            f"- corpus_digest: {corpus_digest or '-'}",
            f"- queries: {len(queries)} (batch_size {protocol.get('batch_size')})",
            f"- holdout_partition: {protocol.get('holdout_partition') or '-'}",
            "- thresholds: "
            + ", ".join(
                f"{name} margin {t.get('margin')} alpha {t.get('alpha')}"
                for name, t in sorted(thresholds.items())
                if isinstance(t, dict)
            ),
            f"- incumbent {json.dumps(protocol.get('incumbent') or {}, sort_keys=True)}"
            f" vs candidate {json.dumps(protocol.get('candidate') or {}, sort_keys=True)}",
        ]
    )
    return lines, len(queries), margin


def _limits_section(
    project_key: str,
    case,
    *,
    n_queries: int | None,
    margin: float | None,
    receipts: list[dict],
) -> list[str]:
    lines = [
        f"## {HEADING_LIMITS}",
        '- No claim above "loop operational": this report shows that the cycle ran on '
        "records, never that the system improved.",
    ]
    if n_queries is None:
        lines.append("- No sample was drawn; no verdict here rests on a measurement.")
    else:
        lines.append(
            f"- The sample is {n_queries} known-item queries against a margin of {margin} "
            "per endpoint (alpha 0.05). A sample this size is a placeholder, not a powered "
            "design; small effects read as inconclusive."
        )
    lines.append(
        "- A retrieval-parameter gain says nothing about agent behavior: the endpoints "
        "score ranked memory ids, never what an agent did with them."
    )
    if receipts:
        for receipt in receipts:
            lines.append(
                f'- Spend receipt settled at metering="estimated": purpose '
                f"{receipt.get('purpose')}, usd {receipt.get('usd')}, "
                f"day {receipt.get('day_key')}; "
                "the true cost is unknown until the reconcile pass corrects it."
            )
    else:
        lines.append(
            '- No spend receipt on this case settled at metering="estimated"; any cost '
            "not receipted here is unmetered, not free."
        )
    lines.append(
        "- The novelty check compares a dedup identity (classification plus a normalized "
        "eight-word prefix); a rejected idea reworded past that prefix is not caught."
    )
    seeded = seeded_cases(project_key)
    if seeded:
        lines.append(
            "- Seeded inputs: "
            + "; ".join(f'case {cid} (seed marker "{marker}")' for cid, marker in seeded)
            + ". The builder planted these; the observer collected every other row."
        )
    else:
        lines.append(
            "- Seeded inputs: no case in this project carries a seeded evidence row; "
            "every row was collected by the observer."
        )
    return lines


def _change_section(
    falsifiers: list[str], overturning: list[str], n_queries: int | None
) -> list[str]:
    lines = [f"## {HEADING_CHANGE}"]
    if falsifiers:
        for falsifier in falsifiers:
            lines.append(f"- The falsifier: {_one_line(falsifier)}")
    else:
        lines.append("- No falsifier is on record; no experiment was proposed.")
    for observation in overturning:
        lines.append(f"- An assumption's overturning observation: {_one_line(observation)}")
    if not overturning:
        lines.append("- No provisional assumption is cited on this case.")
    if n_queries is None:
        lines.append("- A larger sample: any sample at all, under the same contract shape.")
    else:
        lines.append(
            f"- A larger sample: more than {n_queries} known-item queries under the same "
            "contract shape, which is what would move an inconclusive verdict."
        )
    return lines


def build_report(case_id: str, project_key: str, *, store=None) -> str:
    """The qualified-result report for one case, from records alone.

    Raises ``LookupError("CASE_NOT_FOUND: ...")`` for a missing case; the
    CLI prints the reason and exits 1.
    """
    try:
        case = ImprovementCase.query.get(project_key=project_key, id=case_id)
    except Exception:  # noqa: BLE001 -- an unkeyable id is a missing case
        case = None
    if case is None:
        raise LookupError(f"CASE_NOT_FOUND: no case {case_id} in project {project_key}")

    evidence_rows = _evidence_rows(project_key, case)
    investigations = _investigations(project_key, case.id)
    experiments = _experiments(project_key, case.id)
    evaluations = _evaluations(project_key, {e.id for e in experiments})
    revisions = _revisions_citing(project_key, set(_json_list(case.evidence_ids)))

    lines = [
        f"# Qualified-result report: case {case.id}",
        "Generated from records; every line below is derived, none is authored.",
        "",
    ]
    lines.extend(_case_section(project_key, case, evidence_rows))
    lines.append("")
    lines.append("## Ranking positions")
    lines.extend(_ranking_history(project_key, case.id, store=store))
    lines.append("")
    inv_lines, overturning, _assumptions = _investigation_section(investigations)
    lines.extend(inv_lines)
    lines.append("")
    lines.extend(_revision_section(revisions))
    lines.append("")
    exp_lines, falsifiers, manifests = _experiment_section(experiments)
    lines.extend(exp_lines)
    lines.append("")
    lines.extend(_evaluation_section(evaluations))
    lines.append("")

    frozen = [e for e in experiments if e.state in ("frozen", "running", "complete", "aborted")]
    protocol = _protocol(frozen[-1], store=store) if frozen else None
    manifest = manifests[experiments.index(frozen[-1])] if frozen else {}
    measured_lines, n_queries, margin = _measured_section(protocol, manifest)
    lines.extend(measured_lines)
    lines.append("")
    lines.extend(
        _limits_section(
            project_key,
            case,
            n_queries=n_queries,
            margin=margin,
            receipts=_estimated_receipts(project_key, case.id),
        )
    )
    lines.append("")
    lines.extend(_change_section(falsifiers, overturning, n_queries))
    return "\n".join(lines) + "\n"
