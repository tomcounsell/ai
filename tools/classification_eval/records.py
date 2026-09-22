"""Record persistence, case claims, ``--audit``, and site discovery (#3410).

Records are ``ImprovementEvidence(kind="classifier_comparison")`` rows on
the ``valor`` partition, written and read only through the Popoto ORM.
Claims land on a ``probe`` investigation of case :data:`CASE_ID` through
``tools/improvement_investigations.py``. Site discovery reads the registry
``agent.llm.tasks.declared_sites`` (a static AST walk), so ``--audit``
imports no bridge module.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from agent.llm.tasks import Backend, LLMTask, TaskKind
from tools.classification_eval.core import (
    CASE_ID,
    EVIDENCE_KIND,
    ISSUE_URL,
    PROJECT_KEY,
    ComparisonRecord,
    evaluate_bar,
    render_report,
)

# --- records ---------------------------------------------------------------------------


def write_record(record: ComparisonRecord, *, project_key: str = PROJECT_KEY) -> str:
    """Write the record as one ``classifier_comparison`` evidence row; returns its id."""
    from models.improvement_evidence import ImprovementEvidence

    payload = record.as_dict()
    row = ImprovementEvidence.record_once(
        project_key,
        EVIDENCE_KIND,
        source_ref=f"classification_eval:{record.site}:{record.run_id}",
        text=render_report(payload).splitlines()[0],
        detail=json.dumps(payload, sort_keys=True),
        observed_at=datetime.now(UTC),
    )
    if row is None:
        raise RuntimeError(f"record for run {record.run_id} already exists")
    return row.id


def _records(project_key: str) -> Iterable[tuple[Any, dict[str, Any]]]:
    from models.improvement_evidence import ImprovementEvidence

    for row in ImprovementEvidence.query.filter(project_key=project_key, kind=EVIDENCE_KIND):
        try:
            payload = json.loads(row.detail or "")
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and "site" in payload:
            yield row, payload


def latest_record(
    site_id: str,
    *,
    project_key: str = PROJECT_KEY,
    where: Callable[[dict[str, Any]], bool] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """``(evidence id, record)`` for the newest record of ``site_id`` that
    satisfies ``where`` (every record when ``None``), or ``None``."""
    newest: tuple[datetime, str, dict[str, Any]] | None = None
    for row, payload in _records(project_key):
        if payload.get("site") != site_id:
            continue
        if where is not None and not where(payload):
            continue
        stamp = getattr(row, "created_at", None) or datetime.min.replace(tzinfo=UTC)
        if newest is None or stamp > newest[0]:
            newest = (stamp, row.id, payload)
    return None if newest is None else (newest[1], newest[2])


def is_landed(record: Mapping[str, Any]) -> bool:
    """True for a fit record whose run was allowed to touch the served head and did."""
    return bool((record.get("fit") or {}).get("landed"))


def claims_for(record: ComparisonRecord, evidence_id: str) -> list[dict[str, str]]:
    """The claims the CLI records on the case investigation: one per candidate arm."""
    payload = record.as_dict()
    now = datetime.now(UTC).isoformat()
    claims = []
    for name in payload["candidates"]:
        failed = evaluate_bar(payload, name)
        arm = payload["candidates"][name]
        agreement = (arm.get("agreement") or {}).get("mean")
        claims.append(
            {
                "claim": (
                    f"{record.site} candidate={name} evidence={evidence_id}"
                    f" agreement={'n/a' if agreement is None else f'{agreement:.3f}'}"
                    f" p95_c4={arm['p95_c4']:.3f}s error_rate={arm['error_rate']:.3f}"
                    f" n={record.n} n_real={record.n_real} contended={record.contended}"
                    f" bar={'PASS' if not failed else 'MISS ' + ','.join(failed)}"
                ),
                "url": ISSUE_URL,
                "retrieved_at": now,
                "title": f"classification_eval {record.site}",
            }
        )
    return claims


def _record_on_case(
    site_id: str, query: str, claims: list[dict[str, str]], *, project_key: str
) -> str:
    """Open a ``probe`` investigation on :data:`CASE_ID` and record ``claims``
    on it; the one path every runner claim takes."""
    from tools.improvement_investigations import open_investigation, record_claims

    outcome = open_investigation(
        project_key,
        kind="probe",
        case_id=CASE_ID,
        uncertainty=f"does {site_id} clear its tier bar on a local backend",
        query=query,
        decision_affected=f"declared backend of {site_id}",
        expected_information_value="the per-site landing decision and the PR body row",
    )
    if not outcome.accepted or outcome.investigation_id is None:
        raise RuntimeError(f"could not open investigation: {outcome.reason} {outcome.message}")
    record_claims(outcome.investigation_id, claims)
    return outcome.investigation_id


def attach_claims(
    record: ComparisonRecord, evidence_id: str, *, project_key: str = PROJECT_KEY
) -> str:
    """Record the run's claims (one per candidate arm) on the case investigation."""
    return _record_on_case(
        record.site,
        f"classification_eval {record.site} run {record.run_id}",
        claims_for(record, evidence_id),
        project_key=project_key,
    )


def attach_precheck_claim(
    site_id: str, agreement: float, bar: float, *, project_key: str = PROJECT_KEY
) -> str:
    """Record a ``precheck_below_bar`` skip on the case (#3420): the site's
    five-fold agreement on lane A's fixtures sat more than the margin under
    its bar, so the fit ran no reference call and wrote no record."""
    from tools.classification_eval.fit import FIT_ISSUE_URL, PRECHECK_MARGIN

    claim = {
        "claim": (
            f"{site_id} candidate=local_encoder precheck_below_bar"
            f" precheck_agreement={agreement:.3f} bar={bar:.2f}"
            f" gate={bar - PRECHECK_MARGIN:.2f}; fit skipped, zero reference calls, no record"
        ),
        "url": FIT_ISSUE_URL,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "title": f"classification_eval {site_id} precheck",
    }
    return _record_on_case(
        site_id, f"classification_eval {site_id} precheck", [claim], project_key=project_key
    )


# --- --audit -----------------------------------------------------------------------------


def committed_head_run_id(site_id: str) -> str | None:
    """The ``run_id`` of the served head file for ``site_id``, or ``None``
    when no head is committed; read uncached through the leg's validating
    loader so the audit judges the bytes on disk. A head the loader refuses
    raises its ``LLMCallError`` (the audit renders that as a MISS row)."""
    from agent.llm.backends import local_encoder as leg
    from tools.classification_eval.fit import served_head_path

    path = served_head_path(site_id)
    if not path.exists():
        return None
    return str(leg.load_head(path).run_id)


def _audit_row(task: LLMTask, found: tuple[str, dict[str, Any]] | None) -> tuple[bool, str]:
    """``(ok, line)`` for one declared classification site.

    Every backend other than ``ANTHROPIC`` is a local landing: the record
    must carry a candidate arm on the declared backend that clears the bar
    (a reference arm on the same backend proves nothing about the landing).
    A ``LOCAL_ENCODER`` landing is judged on its latest *landed* record
    (``found`` is that record; measure-only records are skipped by
    :func:`audit`), needs both its ``local_encoder`` and its ``anthropic``
    arm clear, and needs the committed head's ``run_id`` to equal the
    record's ``fit.head_run_id``; a committed head the leg's loader refuses
    is a MISS row naming the error, and the walk continues to the next site.
    """
    backend = task.backend.value
    encoder = task.backend is Backend.LOCAL_ENCODER
    if task.client_only:
        return True, f"{task.site:<36} {backend:<10} client_only, no record required"
    if found is None:
        return False, f"{task.site:<36} {backend:<10} no {'landed ' if encoder else ''}record"
    evidence_id, record = found
    prefix = (
        f"{task.site:<36} {backend:<10} record={evidence_id}"
        f" contended={str(record['contended']).lower()}"
        f" n_real={record['n_real']}/{int(record['minimum_n']) / 2:g}"
        f" n={record['n']}/{record['minimum_n']}"
        f"{' latency-only' if record.get('latency_only') else ''}"
    )
    verdicts = {name: evaluate_bar(record, name) for name in record["candidates"]}
    summary = " ".join(
        f"{name}={'PASS' if not failed else 'MISS ' + ','.join(failed)}"
        for name, failed in verdicts.items()
    )
    arms = dict(record["candidates"])
    if record.get("reference"):
        arms[record["reference"]["name"]] = record["reference"]
    landed = [name for name, arm in arms.items() if arm.get("backend") == backend]
    if not landed:
        return False, f"{prefix} no {backend} arm in the record; {summary}"
    if task.backend is Backend.ANTHROPIC:
        # An ANTHROPIC landing needs an Anthropic arm measured (reference or
        # candidate); the candidates' misses are the reason it sits there.
        return True, f"{prefix} anthropic arm present; {summary}"
    ok = any(name in verdicts and not verdicts[name] for name in landed)
    if not encoder:
        return ok, f"{prefix} {'PASS' if ok else 'MISS'} {summary}"
    fallback = verdicts.get(Backend.ANTHROPIC.value)
    if fallback is None or fallback:
        ok = False
        summary += " (the anthropic fallback arm must clear the bar on the landed record)"
    from agent.llm.errors import LLMCallError

    head_run_id = (record.get("fit") or {}).get("head_run_id")
    try:
        committed = committed_head_run_id(task.site)
    except LLMCallError as e:
        return False, f"{prefix} MISS {summary} head unreadable: {e}"
    if committed is None:
        ok = False
        summary += " no head file"
    elif committed != head_run_id:
        ok = False
        summary += f" head run_id {committed} != record fit.head_run_id {head_run_id}"
    else:
        summary += f" head={committed}"
    return ok, f"{prefix} {'PASS' if ok else 'MISS'} {summary}"


def audit(tasks: Sequence[LLMTask], *, project_key: str = PROJECT_KEY) -> int:
    """Print one row per classification site; exit 1 on any site without a
    record, any local landing whose judged record misses a criterion (the
    latest record for ``OLLAMA``, the latest *landed* record for
    ``LOCAL_ENCODER``, whose committed head must also match it), and any
    ``ANTHROPIC`` landing whose record has no Anthropic arm."""
    exit_code = 0
    for task in sorted(tasks, key=lambda t: t.site):
        where = is_landed if task.backend is Backend.LOCAL_ENCODER else None
        ok, line = _audit_row(task, latest_record(task.site, project_key=project_key, where=where))
        print(line)
        if not ok:
            exit_code = 1
    print("audit:", "PASS" if exit_code == 0 else "FAIL")
    return exit_code


# --- declared sites ----------------------------------------------------------------------


def declared_classification_tasks() -> list[LLMTask]:
    """Every declared ``LLMTask`` with ``kind=CLASSIFICATION``, from the site
    registry ``agent.llm.tasks.declared_sites`` (a static AST walk over the
    source roots, so ``--audit`` imports no bridge module)."""
    from agent.llm.tasks import declared_sites

    return [d.task for d in declared_sites() if d.task.kind is TaskKind.CLASSIFICATION]
