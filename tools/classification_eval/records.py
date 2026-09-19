"""Record persistence, case claims, ``--audit``, and site discovery (#3410).

Records are ``ImprovementEvidence(kind="classifier_comparison")`` rows on
the ``valor`` partition, written and read only through the Popoto ORM.
Claims land on a ``probe`` investigation of case :data:`CASE_ID` through
``tools/improvement_investigations.py``. Site discovery is an AST walk over
the source roots for module-level ``LLMTask(kind=CLASSIFICATION, ...)``
declarations, so ``--audit`` imports no bridge module.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.llm.tasks import Backend, ErrorCost, LLMTask, TaskKind
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
    site_id: str, *, project_key: str = PROJECT_KEY
) -> tuple[str, dict[str, Any]] | None:
    """``(evidence id, record)`` for the newest record of ``site_id``, or ``None``."""
    newest: tuple[datetime, str, dict[str, Any]] | None = None
    for row, payload in _records(project_key):
        if payload.get("site") != site_id:
            continue
        stamp = getattr(row, "created_at", None) or datetime.min.replace(tzinfo=UTC)
        if newest is None or stamp > newest[0]:
            newest = (stamp, row.id, payload)
    return None if newest is None else (newest[1], newest[2])


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


def attach_claims(
    record: ComparisonRecord, evidence_id: str, *, project_key: str = PROJECT_KEY
) -> str:
    """Open a ``probe`` investigation on :data:`CASE_ID` and record the run's claims."""
    from tools.improvement_investigations import open_investigation, record_claims

    outcome = open_investigation(
        project_key,
        kind="probe",
        case_id=CASE_ID,
        uncertainty=f"does {record.site} clear its tier bar on a local backend",
        query=f"classification_eval {record.site} run {record.run_id}",
        decision_affected=f"declared backend of {record.site}",
        expected_information_value="the per-site landing decision and the PR body row",
    )
    if not outcome.accepted or outcome.investigation_id is None:
        raise RuntimeError(f"could not open investigation: {outcome.reason} {outcome.message}")
    record_claims(outcome.investigation_id, claims_for(record, evidence_id))
    return outcome.investigation_id


# --- --audit -----------------------------------------------------------------------------


def _audit_row(task: LLMTask, found: tuple[str, dict[str, Any]] | None) -> tuple[bool, str]:
    """``(ok, line)`` for one declared classification site."""
    backend = task.backend.value
    if task.client_only:
        return True, f"{task.site:<36} {backend:<10} client_only, no record required"
    if found is None:
        return False, f"{task.site:<36} {backend:<10} no record"
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
    if backend == Backend.OLLAMA.value:
        # The landed arm must be a candidate that clears the bar; a reference
        # arm on the same backend proves nothing about the landing.
        ok = any(name in verdicts and not verdicts[name] for name in landed)
        return ok, f"{prefix} {'PASS' if ok else 'MISS'} {summary}"
    # An ANTHROPIC landing needs an Anthropic arm measured (reference or
    # candidate); the candidates' misses are the reason it sits there.
    return True, f"{prefix} anthropic arm present; {summary}"


def audit(tasks: Sequence[LLMTask], *, project_key: str = PROJECT_KEY) -> int:
    """Print one row per classification site; exit 1 on any site without a
    record, any ``OLLAMA`` landing whose latest record misses a criterion,
    and any ``ANTHROPIC`` landing whose record has no Anthropic arm."""
    exit_code = 0
    for task in sorted(tasks, key=lambda t: t.site):
        ok, line = _audit_row(task, latest_record(task.site, project_key=project_key))
        print(line)
        if not ok:
            exit_code = 1
    print("audit:", "PASS" if exit_code == 0 else "FAIL")
    return exit_code


# --- declared sites ----------------------------------------------------------------------

_SOURCE_ROOTS = ("agent", "bridge", "worker", "tools", "reflections", "scripts")
_ENUMS = {"kind": TaskKind, "backend": Backend, "error_cost": ErrorCost}


def _task_from_call(node: ast.Call) -> LLMTask | None:
    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        value = kw.value
        if kw.arg in _ENUMS and isinstance(value, ast.Attribute):
            kwargs[kw.arg] = _ENUMS[kw.arg][value.attr]
        elif isinstance(value, ast.Constant):
            kwargs[kw.arg] = value.value
    if kwargs.get("kind") is not TaskKind.CLASSIFICATION:
        return None
    try:
        return LLMTask(**kwargs)
    except TypeError:
        return None


def declared_classification_tasks(repo_root: Path | None = None) -> list[LLMTask]:
    """Every module-level ``LLMTask(kind=CLASSIFICATION, ...)`` under the
    source roots, read from the AST so no bridge module is imported.

    This is the site discovery ``--audit`` walks. When ``agent/llm/tasks.py``
    grows a registry, this becomes a read of that registry.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    found: list[LLMTask] = []
    for top in _SOURCE_ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for node in tree.body:
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                    continue
                func = node.value.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name != "LLMTask":
                    continue
                task = _task_from_call(node.value)
                if task is not None:
                    found.append(task)
    return found
