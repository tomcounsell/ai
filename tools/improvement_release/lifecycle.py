"""Release lifecycle: the sole writer of ``ImprovementRelease`` state (#3218, lane 6).

    | From                        | Event          | To                                   |
    |-----------------------------|----------------|--------------------------------------|
    | (none)                      | ``propose``    | ``proposed``                         |
    | ``proposed``                | ``approve``    | ``approved``                         |
    | ``approved``                | ``open_pr``    | ``approved`` (records the PR)        |
    | ``approved``                | ``expose``     | ``observing``                        |
    | ``observing``               | ``close_window`` | ``accepted`` on ``held``, else stays |
    | ``observing``, ``accepted`` | ``rollback``   | ``rolled_back``                      |
    | ``proposed``, ``approved``  | ``withdraw``   | ``withdrawn``                        |

Every public function checks its preconditions first and raises
:class:`ReleaseRefused` with a code from :data:`REFUSAL_CODES`. A refusal
writes nothing; the exceptions are ``rollback``'s two refusals after a step
has run, ``ROLLBACK_PUSH_REFUSED`` and ``ROLLBACK_STEP_FAILED``, which record
the attempt (its transcript, and for a refused push the orphaned revert
commit) on the row before raising, because a revert that exists and never
reached the target branch is exactly the fact the record must carry.

:func:`_transition` re-reads the row immediately before ``save()``, refuses
``WRONG_STATE`` when the state moved under the caller, and merges any history
event the re-read row carries that the caller's copy lacks, so an interleaved
double-write keeps both events (Race 1). ``outcome.history`` is bounded at
:data:`HISTORY_MAX` entries; past that the oldest entry is dropped and
``history_truncated`` is set once.

Exposure is anchored on the merge (``exposed_at = mergedAt``), never on the
call: the baseline is frozen over ``[mergedAt - baseline_days, mergedAt)``
and the observation window ends at ``mergedAt + window_days``. Evidence
rows expire after ``EVIDENCE_TTL_DAYS``; ``expose`` refuses to freeze a
baseline whose oldest rows have expired and ``close_window`` scores a
window whose early rows have expired as ``undetermined``.

``close_window`` recommends a rollback; it never performs one. ``rollback``
is operator-invoked, builds its worktree from a freshly fetched
``origin/<target>``, and transitions only after the push returned 0 and
``ls-remote`` confirms the remote head is the revert commit.

Every JSON field on the row is written with ``json.dumps`` and read back
through ``evaluation_read.json_field``; popoto hands a plain ``Field`` back
as a string.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config.settings import settings
from models.improvement_charter import ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from models.improvement_release import RELEASE_KINDS, ImprovementRelease
from tools.improvement_eval.runner import read_content
from tools.improvement_release.denylist import (
    InvalidSurface,
    SurfaceDenied,
    normalize_surface,
    refuse_denied,
)
from tools.improvement_release.evaluation_read import effect_of, json_field
from tools.improvement_release.lineage import load_primary_endpoint
from tools.improvement_release.observation import (
    EVIDENCE_TTL_DAYS,
    OBSERVATION_METRICS,
    VERDICT_HELD,
    VERDICT_UNDETERMINED,
    compare_windows,
    falsifier,
    measure,
)
from tools.improvement_release.promotion import promotion_gate
from tools.improvement_release.rows import aware
from tools.improvement_release.runner import Runner, SubprocessRunner, run_step, tail

logger = logging.getLogger(__name__)

#: The closed refusal vocabulary. ``EVIDENCE_EXPIRED`` is also an
#: ``outcome.reason`` at ``close_window``: one name, one meaning, two surfaces.
REFUSAL_CODES: tuple[str, ...] = (
    "EVALUATION_NOT_ACCEPT",
    "CHARTER_DRIFT",
    "SURFACE_DENIED",
    "MANIFEST_LACKS_BASE_REVISION",
    "BASE_REVISION_CONFLICT",
    "CANDIDATE_REF_CONFLICT",
    "DRILL_REQUIRED",
    "DRILL_STALE",
    "APPROVER_NOT_HUMAN",
    "PR_NOT_MERGED",
    "EVIDENCE_EXPIRED",
    "WINDOW_OPEN",
    "WINDOW_EXCEEDS_EVIDENCE_TTL",
    "WRONG_STATE",
    "EVALUATOR_RELEASE_NEEDS_CALIBRATION",
    "ROLLBACK_PUSH_REFUSED",
    "INVALID_ROLLBACK_PLAN",
    "INVALID_OBSERVATION_PLAN",
    "INVALID_KIND",
    "CANDIDATE_REF_UNRESOLVED",
    "NOT_FOUND",
    "PR_CREATE_FAILED",
    "MERGE_SHA_INVALID",
    "ROLLBACK_STEP_FAILED",
)

#: Names that are never a human approver. Compared case-insensitively, stripped.
AGENT_IDENTITIES = frozenset({"valor", "valor-engels", "agent", "system", "claude"})

#: ``outcome.history`` keeps at most this many events.
HISTORY_MAX = 50

#: ``baseline_window_days + window_days`` may not exceed this, so a prompt
#: ``expose`` reads a baseline inside the evidence TTL with two days of slack.
WINDOW_SUM_MAX_DAYS = EVIDENCE_TTL_DAYS - 2

#: The one rollback plan shape a release may declare.
ROLLBACK_PLAN_KIND = "git_revert"
ROLLBACK_PROPAGATION = "/update"

#: Recorded exposure plan: the merged PR reaches the fleet through ``/update``.
EXPOSURE_PLAN = {"unit": "fleet", "mechanism": "merged PR via /update"}

ROLLBACK_PROPAGATION_NOTE = "requires /update on fleet machines"

#: Local ref prefix that keeps a rollback's revert commit reachable after its
#: worktree is removed: ``refs/improvement-rollback/<release id>``.
ROLLBACK_REF_PREFIX = "refs/improvement-rollback/"

#: Bytes of the rollback transcript persisted on the row (the last ones).
ROLLBACK_TRANSCRIPT_BYTES = 16384

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PR_NUMBER = re.compile(r"/pull/(\d+)")


class ReleaseRefused(Exception):  # noqa: N818 -- plan-mandated name (#3218)
    """A lifecycle precondition failed. ``code`` is from :data:`REFUSAL_CODES`."""

    def __init__(self, code: str, detail: str = ""):
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


def _git_timeout() -> float:
    """One bound for every ``git`` and ``gh`` subprocess outside the drill's verify step."""
    return settings.timeouts.git_subprocess_s


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=_json_default)


def _dict_field(release: Any, name: str) -> dict:
    value = json_field(getattr(release, name, None))
    return value if isinstance(value, dict) else {}


def _list_field(release: Any, name: str) -> list:
    value = json_field(getattr(release, name, None))
    return value if isinstance(value, list) else []


def get_release(release_id: str, project_key: str) -> ImprovementRelease:
    """The release row, or ``ReleaseRefused("NOT_FOUND")``."""
    row = ImprovementRelease.query.filter(project_key=project_key, id=str(release_id)).first()
    if row is None:
        raise ReleaseRefused("NOT_FOUND", f"no release {release_id!r} under {project_key!r}")
    return row


def _outcome(release: Any) -> dict:
    """The row's ``outcome`` as a dict with a ``history`` list; a fresh row has ``None``."""
    outcome = _dict_field(release, "outcome")
    history = outcome.get("history")
    outcome["history"] = history if isinstance(history, list) else []
    return outcome


def _append_history(outcome: dict, event: str, at: datetime, **fields: Any) -> dict:
    """Append ``{event, at, **fields}``, bounded at :data:`HISTORY_MAX`."""
    history = outcome.setdefault("history", [])
    entry = {"event": event, "at": at.isoformat(), **fields}
    history.append(entry)
    while len(history) > HISTORY_MAX:
        history.pop(0)
        outcome["history_truncated"] = True
    return entry


def _merge_history(outcome: dict, current: dict) -> None:
    """Fold the stored row's history events into ``outcome`` (Race 1).

    Events present on the re-read row and absent from the caller's dict were
    written by another caller since this one read the row. They are kept,
    ordered by ``at`` (a stable sort, so same-stamp events keep their
    order), and the :data:`HISTORY_MAX` bound is re-applied.
    """
    history = outcome.setdefault("history", [])
    missing = [entry for entry in current.get("history", []) if entry not in history]
    if not missing:
        return
    history.extend(missing)
    history.sort(key=lambda entry: str(entry.get("at") or ""))
    while len(history) > HISTORY_MAX:
        history.pop(0)
        outcome["history_truncated"] = True


def _merge_outcome(outcome: dict, current: dict, *, base: dict) -> dict:
    """Three-way merge of the caller's ``outcome`` onto the re-read row's (Race 1).

    ``base`` is the outcome the caller read before its edits, ``current`` the
    row as re-read before the save. The result starts from ``current``, so
    every key another caller wrote since survives; a key the caller added or
    changed relative to ``base`` takes the caller's value, so its own edits
    survive too (a repeated write such as a second ``rollback_attempt`` still
    overwrites). History is folded together by :func:`_merge_history`.
    """
    merged = {key: value for key, value in current.items() if key != "history"}
    for key, value in outcome.items():
        if key != "history" and (key not in base or base[key] != value):
            merged[key] = value
    merged["history"] = list(outcome.get("history") or [])
    _merge_history(merged, current)
    return merged


def _require_state(release: Any, allowed: tuple[str, ...], event: str) -> None:
    state = getattr(release, "state", None)
    if state not in allowed:
        raise ReleaseRefused(
            "WRONG_STATE",
            f"{event} needs state in {list(allowed)}; release {release.id} is {state!r}",
        )


def _transition(
    release: ImprovementRelease,
    *,
    to: str,
    allowed_from: tuple[str, ...],
    event: str,
    detail: Any = None,
    now: datetime | None = None,
    outcome: dict | None = None,
    extra_events: list[dict] | None = None,
    **fields: Any,
) -> ImprovementRelease:
    """Write ``state = to`` after re-reading the row (Race 1).

    ``outcome`` is the caller's already-updated dict (defaults to the row's);
    the transition event lands on it, then ``extra_events`` in order, and the
    row is saved once. The dict is first merged onto the re-read row's
    (:func:`_merge_outcome`): keys and history events another writer landed
    since this caller read the row survive beside the caller's own edits,
    which recovers a second writer whose re-read lands after the first
    writer's save. The sub-millisecond interleave (both re-read, then both
    save) is still a lost write; Popoto has no compare-and-set, and the last
    save wins.
    """
    at = _now(now)
    current = get_release(release.id, release.project_key)
    if current.state not in allowed_from:
        raise ReleaseRefused(
            "WRONG_STATE",
            f"{event} needs state in {list(allowed_from)}; release {release.id} is "
            f"{current.state!r} (re-read before save)",
        )
    base = _outcome(release)
    outcome = _merge_outcome(outcome if outcome is not None else base, _outcome(current), base=base)
    entry_fields = {"from": current.state, "to": to, **fields}
    if detail is not None:
        entry_fields["detail"] = detail
    _append_history(outcome, event, at, **entry_fields)
    for extra in extra_events or []:
        _append_history(outcome, extra.pop("event"), at, **extra)
    release.state = to
    release.outcome = _dump(outcome)
    if release.save() is False:
        raise RuntimeError("ImprovementRelease.save() returned False")
    return release


def _save_outcome_only(
    release: ImprovementRelease, outcome: dict, *, fields: tuple[str, ...] = ("outcome",)
) -> ImprovementRelease:
    """Write ``fields`` (``outcome`` and any other column set) after re-reading the row (Race 1).

    For a caller that records an event without moving the state: ``outcome``
    is merged onto the re-read row's (:func:`_merge_outcome`), so keys and
    history events another writer landed since this caller read the row
    survive beside the caller's own edits, and the save is partial
    (``update_fields=fields``), so a transition another caller landed keeps
    its state. ``fields`` names every column written, ``outcome`` included; a
    caller that also set another column on ``release`` (``open_pr`` and
    ``exposure``) lists it here.
    """
    current = get_release(release.id, release.project_key)
    merged = _merge_outcome(outcome, _outcome(current), base=_outcome(release))
    release.outcome = _dump(merged)
    if release.save(update_fields=list(fields)) is False:
        raise RuntimeError("ImprovementRelease.save() returned False")
    return release


def _observation_plan(release: Any) -> tuple[dict, int, int]:
    plan = _dict_field(release, "observation")
    return plan, int(plan.get("window_days") or 0), int(plan.get("baseline_window_days") or 0)


def _evaluation_of(release: Any) -> ImprovementEvaluation | None:
    evaluation_id = getattr(release, "evaluation_id", None)
    if not evaluation_id:
        return None
    return ImprovementEvaluation.query.filter(
        project_key=release.project_key, id=str(evaluation_id)
    ).first()


def _experiment_of(project_key: str, evaluation: Any) -> ImprovementExperiment | None:
    experiment_id = getattr(evaluation, "experiment_id", None)
    if not experiment_id:
        return None
    return ImprovementExperiment.query.filter(
        project_key=project_key, id=str(experiment_id)
    ).first()


def _manifest_of(experiment: Any) -> dict:
    if experiment is None:
        return {}
    text = read_content(experiment, "manifest")
    if not text:
        return {}
    try:
        manifest = json.loads(text)
    except ValueError:
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _pinned_digest(project_key: str) -> str | None:
    charter = ImprovementCharter.pinned(project_key)
    return getattr(charter, "digest", None) if charter is not None else None


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def _validate_rollback_plan(plan: Any) -> dict:
    if not isinstance(plan, dict):
        raise ReleaseRefused("INVALID_ROLLBACK_PLAN", "rollback plan is not an object")
    if plan.get("kind") != ROLLBACK_PLAN_KIND:
        raise ReleaseRefused(
            "INVALID_ROLLBACK_PLAN",
            f"kind must be {ROLLBACK_PLAN_KIND!r}, got {plan.get('kind')!r}",
        )
    verify = plan.get("verify", [])
    if not isinstance(verify, list) or not all(isinstance(c, str) and c.strip() for c in verify):
        raise ReleaseRefused("INVALID_ROLLBACK_PLAN", "verify must be a list of command strings")
    if plan.get("propagation") != ROLLBACK_PROPAGATION:
        raise ReleaseRefused(
            "INVALID_ROLLBACK_PLAN", f"propagation must be {ROLLBACK_PROPAGATION!r}"
        )
    return {"kind": ROLLBACK_PLAN_KIND, "verify": list(verify), "propagation": ROLLBACK_PROPAGATION}


def _validate_observation_plan(plan: Any) -> dict:
    if not isinstance(plan, dict):
        raise ReleaseRefused("INVALID_OBSERVATION_PLAN", "observation plan is not an object")
    window_days = plan.get("window_days")
    baseline_days = plan.get("baseline_window_days")
    for name, value in (("window_days", window_days), ("baseline_window_days", baseline_days)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ReleaseRefused("INVALID_OBSERVATION_PLAN", f"{name} must be an integer >= 1")
    if window_days + baseline_days > WINDOW_SUM_MAX_DAYS:
        raise ReleaseRefused(
            "WINDOW_EXCEEDS_EVIDENCE_TTL",
            f"baseline_window_days + window_days = {window_days + baseline_days} exceeds "
            f"{WINDOW_SUM_MAX_DAYS} (evidence expires after {EVIDENCE_TTL_DAYS} days)",
        )
    metrics = plan.get("metrics")
    if not isinstance(metrics, list) or not metrics or not set(metrics) <= set(OBSERVATION_METRICS):
        raise ReleaseRefused(
            "INVALID_OBSERVATION_PLAN",
            f"metrics must be a non-empty subset of {list(OBSERVATION_METRICS)}",
        )
    return {
        "window_days": window_days,
        "baseline_window_days": baseline_days,
        "metrics": list(metrics),
    }


def _validate_surfaces(surfaces: Any) -> list[str]:
    if not isinstance(surfaces, list | tuple) or not surfaces:
        raise ReleaseRefused("SURFACE_DENIED", "a release declares at least one surface")
    try:
        refuse_denied(list(surfaces))
    except InvalidSurface as exc:
        raise ReleaseRefused("SURFACE_DENIED", str(exc)) from exc
    except SurfaceDenied as exc:
        raise ReleaseRefused("SURFACE_DENIED", str(exc)) from exc
    return [normalize_surface(surface) for surface in surfaces]


def _resolve_conflict(name: str, manifest_value: Any, argument: Any, code: str) -> str | None:
    manifest_value = manifest_value if isinstance(manifest_value, str) and manifest_value else None
    argument = argument if isinstance(argument, str) and argument else None
    if manifest_value and argument and manifest_value != argument:
        raise ReleaseRefused(
            code, f"manifest {name} {manifest_value!r} differs from argument {argument!r}"
        )
    return manifest_value or argument


def propose(
    *,
    evaluation_id: str,
    kind: str,
    candidate_ref: str | None,
    surfaces: list[str],
    rollback_plan: dict,
    observation: dict,
    project_key: str = "valor",
    base_revision: str | None = None,
    calibration_ref: str | None = None,
    argument: str | None = None,
    runner: Runner | None = None,
    now: datetime | None = None,
    repo: str | None = None,
) -> ImprovementRelease:
    """Write one ``ImprovementRelease(state="proposed")`` after the eight gates.

    Gates, in order: (1) the evaluation is ``complete`` with verdict ``accept``;
    (2) its ``charter_digest`` is the pinned charter's; (3) ``kind`` is valid and
    an ``evaluator`` release carries ``calibration_ref`` and ``argument``;
    (4) surfaces are non-empty, normalized, and none denied; (5)
    ``base_revision`` from the manifest or the argument, distinct conflict
    codes; (6) ``candidate_ref`` likewise, then resolved through
    ``git rev-parse --verify``; (7) the rollback plan shape; (8) the
    observation plan shape and the TTL cap. Nothing is written on refusal.
    """
    at = _now(now)
    evaluation = ImprovementEvaluation.query.filter(
        project_key=project_key, id=str(evaluation_id)
    ).first()
    if evaluation is None:
        raise ReleaseRefused("EVALUATION_NOT_ACCEPT", f"no evaluation {evaluation_id!r}")
    if evaluation.state != "complete" or evaluation.verdict != "accept":
        raise ReleaseRefused(
            "EVALUATION_NOT_ACCEPT",
            f"evaluation {evaluation_id} is {evaluation.state!r}/{evaluation.verdict!r}",
        )

    pinned = _pinned_digest(project_key)
    if pinned is None or evaluation.charter_digest != pinned:
        raise ReleaseRefused(
            "CHARTER_DRIFT",
            f"evaluation ran under {evaluation.charter_digest!r}; pinned charter is {pinned!r}",
        )

    if kind not in RELEASE_KINDS:
        raise ReleaseRefused("INVALID_KIND", f"kind must be one of {list(RELEASE_KINDS)}")
    if kind == "evaluator" and (not calibration_ref or not (argument or "").strip()):
        raise ReleaseRefused(
            "EVALUATOR_RELEASE_NEEDS_CALIBRATION",
            "an evaluator release needs a calibration_ref and a written argument",
        )

    normalized_surfaces = _validate_surfaces(surfaces)

    experiment = _experiment_of(project_key, evaluation)
    manifest = _manifest_of(experiment)
    resolved_base = _resolve_conflict(
        "base_revision", manifest.get("base_revision"), base_revision, "BASE_REVISION_CONFLICT"
    )
    if not resolved_base:
        raise ReleaseRefused(
            "MANIFEST_LACKS_BASE_REVISION",
            "the manifest carries no base_revision and none was given",
        )
    resolved_ref = _resolve_conflict(
        "candidate_ref", manifest.get("candidate_ref"), candidate_ref, "CANDIDATE_REF_CONFLICT"
    )
    if not resolved_ref or resolved_ref.startswith("-"):
        raise ReleaseRefused("CANDIDATE_REF_UNRESOLVED", "candidate_ref is empty")
    run = runner or SubprocessRunner()
    verify = run(["git", "rev-parse", "--verify", resolved_ref], cwd=repo, timeout=_git_timeout())
    if verify.returncode != 0:
        raise ReleaseRefused(
            "CANDIDATE_REF_UNRESOLVED",
            f"git rev-parse --verify {resolved_ref!r} exited {verify.returncode}: "
            f"{tail(verify.stderr, 300)}",
        )

    plan = _validate_rollback_plan(rollback_plan)
    observation_plan = _validate_observation_plan(observation)

    outcome: dict = {"history": [], "rollback_plan_written_at": at.isoformat()}
    _append_history(
        outcome,
        "proposed",
        at,
        to="proposed",
        detail={"evaluation_id": str(evaluation.id), "kind": kind},
    )
    release = ImprovementRelease(
        project_key=project_key,
        created_at=at,
        state="proposed",
        kind=kind,
        evaluation_id=str(evaluation.id),
        case_id=getattr(experiment, "case_id", None),
        surfaces=_dump(normalized_surfaces),
        candidate_ref=resolved_ref,
        base_revision=resolved_base,
        exposure=_dump(
            {
                **EXPOSURE_PLAN,
                "experiment_id": str(experiment.id) if experiment is not None else None,
                "calibration_ref": calibration_ref,
                "argument": argument,
            }
        ),
        rollback_plan=_dump(plan),
        observation=_dump(observation_plan),
        charter_digest=pinned,
        outcome=_dump(outcome),
    )
    if release.save() is False:
        raise RuntimeError("ImprovementRelease.save() returned False")
    return release


# ---------------------------------------------------------------------------
# approve / withdraw
# ---------------------------------------------------------------------------


def _check_drill(release: Any, outcome: dict) -> dict:
    from tools.improvement_release.drill import DrillRefused, drill_record

    try:
        drill = drill_record(release)
    except DrillRefused as exc:
        raise ReleaseRefused("DRILL_REQUIRED", f"unreadable drill record: {exc}") from exc
    if not drill or drill.get("result") != "pass":
        raise ReleaseRefused(
            "DRILL_REQUIRED",
            f"release {release.id} has no passing rollback drill"
            + (f" (last result {drill.get('result')!r})" if drill else ""),
        )
    drilled_at = aware(drill.get("drilled_at"))
    written_at = aware(outcome.get("rollback_plan_written_at"))
    if drilled_at is None:
        raise ReleaseRefused("DRILL_STALE", "the drill record carries no drilled_at")
    if written_at is not None and drilled_at < written_at:
        raise ReleaseRefused(
            "DRILL_STALE",
            f"drilled at {drilled_at.isoformat()}, before the rollback plan was written at "
            f"{written_at.isoformat()}",
        )
    return drill


def approve(
    release_id: str, *, approved_by: str, project_key: str, now: datetime | None = None
) -> ImprovementRelease:
    """``proposed`` → ``approved``: passed drill, human approver, no charter drift."""
    at = _now(now)
    release = get_release(release_id, project_key)
    _require_state(release, ("proposed",), "approve")
    outcome = _outcome(release)
    _check_drill(release, outcome)

    name = (approved_by or "").strip()
    if not name or name.lower() in AGENT_IDENTITIES:
        raise ReleaseRefused("APPROVER_NOT_HUMAN", f"approved_by {approved_by!r} is not a human")

    pinned = _pinned_digest(project_key)
    if pinned is None or release.charter_digest != pinned:
        raise ReleaseRefused(
            "CHARTER_DRIFT",
            f"release admitted under {release.charter_digest!r}; pinned charter is {pinned!r}",
        )

    _plan, window_days, _baseline_days = _observation_plan(release)
    release.promotion_gate = _dump(promotion_gate(project_key).as_dict())
    release.approved_by = name
    release.approved_at = at
    release.observation_window_ends_at = at + timedelta(days=window_days)
    return _transition(
        release,
        allowed_from=("proposed",),
        to="approved",
        event="approved",
        now=at,
        outcome=outcome,
        detail={
            "approved_by": name,
            "provisional_window_ends_at": release.observation_window_ends_at.isoformat(),
        },
    )


def withdraw(
    release_id: str, *, project_key: str, reason: str, now: datetime | None = None
) -> ImprovementRelease:
    """``proposed`` or ``approved`` → ``withdrawn``, with the reason recorded."""
    at = _now(now)
    release = get_release(release_id, project_key)
    _require_state(release, ("proposed", "approved"), "withdraw")
    outcome = _outcome(release)
    outcome["withdrawn"] = {"reason": reason, "at": at.isoformat()}
    return _transition(
        release,
        allowed_from=("proposed", "approved"),
        to="withdrawn",
        event="withdrawn",
        now=at,
        outcome=outcome,
        detail={"reason": reason},
    )


# ---------------------------------------------------------------------------
# open_pr / expose
# ---------------------------------------------------------------------------


def _pr_body(release: Any, evaluation: Any, experiment: Any, endpoint: str | None) -> str:
    from tools.improvement_release.evaluation_read import interval_of

    drill = _dict_field(release, "rollback_drill")
    lines = [
        f"## Improvement release {release.id}",
        "",
        f"- kind: `{release.kind}`",
        f"- surfaces: {', '.join(f'`{s}`' for s in _list_field(release, 'surfaces'))}",
        f"- candidate_ref: `{release.candidate_ref}`",
        f"- base_revision: `{release.base_revision}`",
        "",
        "### Evaluation",
        f"- id: `{getattr(evaluation, 'id', None)}`",
        f"- verdict: `{getattr(evaluation, 'verdict', None)}`",
        f"- primary endpoint: `{endpoint}`",
        f"- effect: `{effect_of(evaluation, endpoint) if endpoint else None}`",
        f"- confidence interval: `{interval_of(evaluation, endpoint) if endpoint else None}`",
        f"- correction: `{getattr(evaluation, 'correction', None)}`",
        f"- contract digest: `{getattr(experiment, 'contract_digest', None)}`",
        f"- charter digest: `{release.charter_digest}`",
        "",
        "### Rollback plan",
        "```json",
        _dump(_dict_field(release, "rollback_plan")),
        "```",
        "",
        "### Drill record",
        f"- result: `{drill.get('result')}` at `{drill.get('drilled_at')}`",
        f"- exercised: {drill.get('exercised')}",
        f"- not exercised: {drill.get('not_exercised')}",
        "",
        "### Observation plan",
        "```json",
        _dump(_dict_field(release, "observation")),
        "```",
        "",
        f"Approved by {release.approved_by} at {release.approved_at}. Refs #3218.",
    ]
    return "\n".join(lines)


def open_pr(
    release_id: str,
    *,
    project_key: str,
    runner: Runner,
    base: str = "main",
    title: str | None = None,
    repo: str | None = None,
    now: datetime | None = None,
) -> ImprovementRelease:
    """Run ``gh pr create`` from ``candidate_ref`` and record the PR; no state change.

    Merging is the pipeline's and a human's; this function never calls
    ``gh pr merge``.
    """
    at = _now(now)
    release = get_release(release_id, project_key)
    _require_state(release, ("approved",), "open_pr")
    evaluation = _evaluation_of(release)
    experiment = _experiment_of(project_key, evaluation)
    endpoint = load_primary_endpoint(experiment)
    body = _pr_body(release, evaluation, experiment, endpoint)
    title = title or f"Improvement release {release.id}: {release.kind} ({release.candidate_ref})"

    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="release-pr-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(body)
        body_path = handle.name
    try:
        result = runner(
            [
                "gh",
                "pr",
                "create",
                "--head",
                release.candidate_ref,
                "--base",
                base,
                "--title",
                title,
                "--body-file",
                body_path,
            ],
            cwd=repo,
            timeout=_git_timeout(),
        )
    finally:
        Path(body_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise ReleaseRefused(
            "PR_CREATE_FAILED", f"gh pr create exited {result.returncode}: {tail(result.stderr)}"
        )
    match = _PR_NUMBER.search(result.stdout or "")
    if match is None:
        raise ReleaseRefused(
            "PR_CREATE_FAILED", f"gh pr create printed no PR URL: {tail(result.stdout, 300)!r}"
        )
    exposure = _dict_field(release, "exposure")
    exposure["pr_number"] = int(match.group(1))
    exposure["pr_url"] = (result.stdout or "").strip().splitlines()[-1].strip()
    exposure["pr_base"] = base
    release.exposure = _dump(exposure)
    outcome = _outcome(release)
    _append_history(
        outcome, "pr_opened", at, pr_number=exposure["pr_number"], pr_url=exposure["pr_url"]
    )
    return _save_outcome_only(release, outcome, fields=("exposure", "outcome"))


def _pr_view(runner: Runner, pr_number: int, *, repo: str | None) -> dict:
    result = runner(
        ["gh", "pr", "view", str(pr_number), "--json", "mergeCommit,mergedAt,state"],
        cwd=repo,
        timeout=_git_timeout(),
    )
    if result.returncode != 0:
        raise ReleaseRefused(
            "PR_NOT_MERGED",
            f"gh pr view {pr_number} exited {result.returncode}: {tail(result.stderr)}",
        )
    try:
        payload = json.loads(result.stdout or "")
    except ValueError as exc:
        raise ReleaseRefused("PR_NOT_MERGED", f"gh pr view printed no JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseRefused("PR_NOT_MERGED", "gh pr view payload is not an object")
    return payload


def expose(
    release_id: str,
    *,
    project_key: str,
    runner: Runner,
    now: datetime | None = None,
    repo: str | None = None,
) -> ImprovementRelease:
    """``approved`` → ``observing`` once the PR is merged; exposure anchored on the merge."""
    at = _now(now)
    release = get_release(release_id, project_key)
    _require_state(release, ("approved",), "expose")
    exposure = _dict_field(release, "exposure")
    pr_number = exposure.get("pr_number")
    if not pr_number:
        raise ReleaseRefused("PR_NOT_MERGED", f"release {release.id} has no PR; run open_pr first")

    payload = _pr_view(runner, int(pr_number), repo=repo)
    if payload.get("state") != "MERGED":
        raise ReleaseRefused("PR_NOT_MERGED", f"PR #{pr_number} state is {payload.get('state')!r}")
    merge_commit = payload.get("mergeCommit")
    merge_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not isinstance(merge_sha, str) or not _HEX40.match(merge_sha):
        raise ReleaseRefused(
            "MERGE_SHA_INVALID", f"mergeCommit.oid {merge_sha!r} is not a 40-hex SHA"
        )
    merged_at = aware(payload.get("mergedAt"))
    if merged_at is None:
        raise ReleaseRefused("PR_NOT_MERGED", f"mergedAt {payload.get('mergedAt')!r} unparsable")

    _plan, window_days, baseline_days = _observation_plan(release)
    baseline_start = merged_at - timedelta(days=baseline_days)
    if at - baseline_start > timedelta(days=EVIDENCE_TTL_DAYS):
        raise ReleaseRefused(
            "EVIDENCE_EXPIRED",
            f"baseline starts {baseline_start.isoformat()}, more than {EVIDENCE_TTL_DAYS} days "
            f"before {at.isoformat()}; its oldest evidence rows have expired",
        )

    baseline = measure(project_key, baseline_start, merged_at)
    previous_end = aware(release.observation_window_ends_at)
    new_end = merged_at + timedelta(days=window_days)

    exposure["merge_sha"] = merge_sha
    exposure["merged_at"] = merged_at.isoformat()
    release.exposure = _dump(exposure)
    release.exposed_at = merged_at
    release.observation_window_ends_at = new_end
    outcome = _outcome(release)
    outcome["baseline"] = json.loads(_dump(baseline))
    return _transition(
        release,
        allowed_from=("approved",),
        to="observing",
        event="exposed",
        now=at,
        outcome=outcome,
        merged_at=merged_at.isoformat(),
        expose_called_at=at.isoformat(),
        merge_sha=merge_sha,
        extra_events=[
            {
                "event": "window_restamped",
                "from": previous_end.isoformat() if previous_end else None,
                "to": new_end.isoformat(),
            }
        ],
    )


# ---------------------------------------------------------------------------
# close_window
# ---------------------------------------------------------------------------


def close_window(
    release_id: str,
    *,
    project_key: str,
    now: datetime | None = None,
    force: bool = False,
    reason: str | None = None,
) -> ImprovementRelease:
    """Score the observation window; ``accepted`` on ``held``, else stay ``observing``.

    Never performs a rollback: a regressed or undetermined window sets
    ``outcome.rollback_recommended`` for a human to act on.
    """
    at = _now(now)
    release = get_release(release_id, project_key)
    _require_state(release, ("observing",), "close_window")
    exposed_at = aware(release.exposed_at)
    ends_at = aware(release.observation_window_ends_at)
    if exposed_at is None or ends_at is None:
        raise ReleaseRefused("WRONG_STATE", f"release {release.id} has no exposure timestamps")
    outcome = _outcome(release)

    if at < ends_at:
        if not force or not (reason or "").strip():
            raise ReleaseRefused(
                "WINDOW_OPEN",
                f"window ends {ends_at.isoformat()}; force=True with a reason closes it early",
            )
        outcome["forced"] = {"reason": reason.strip(), "at": at.isoformat()}

    _plan, window_days, baseline_days = _observation_plan(release)
    closed_at = at
    window_end = min(at, ends_at)
    outcome["closed_at"] = closed_at.isoformat()
    outcome["window_shortfall_days"] = max(0, window_days - (closed_at - exposed_at).days)
    outcome["falsifier"] = falsifier(window_days)
    outcome["claim_level_2_supported"] = False

    if at - exposed_at > timedelta(days=EVIDENCE_TTL_DAYS):
        outcome["window"] = None
        outcome["deltas"] = None
        outcome["detection_declined"] = None
        outcome["verdict"] = VERDICT_UNDETERMINED
        outcome["reason"] = "EVIDENCE_EXPIRED"
    else:
        window = measure(project_key, exposed_at, window_end)
        baseline = outcome.get("baseline") if isinstance(outcome.get("baseline"), dict) else {}
        comparison = compare_windows(
            baseline, window, window_days=window_days, baseline_days=baseline_days
        )
        outcome["window"] = json.loads(_dump(window))
        outcome["deltas"] = comparison["deltas"]
        outcome["baseline_band"] = comparison["baseline_band"]
        outcome["detection_declined"] = comparison["detection_declined"]
        outcome["verdict"] = comparison["verdict"]
        outcome["reason"] = comparison["reason"]
        if comparison["verdict"] == VERDICT_HELD and outcome["window_shortfall_days"] == 0:
            evaluation = _evaluation_of(release)
            endpoint = load_primary_endpoint(_experiment_of(project_key, evaluation))
            effect = effect_of(evaluation, endpoint) if endpoint else None
            outcome["claim_level_2_supported"] = isinstance(effect, int | float) and effect > 0

    if outcome["verdict"] == VERDICT_HELD:
        outcome["rollback_recommended"] = False
        return _transition(
            release,
            allowed_from=("observing",),
            to="accepted",
            event="window_closed",
            now=at,
            outcome=outcome,
            detail={"verdict": outcome["verdict"], "reason": outcome["reason"]},
        )
    outcome["rollback_recommended"] = True
    _append_history(
        outcome,
        "window_closed",
        at,
        detail={"verdict": outcome["verdict"], "reason": outcome["reason"]},
    )
    return _save_outcome_only(release, outcome)


def due_windows(project_key: str, now: datetime | None = None) -> list[ImprovementRelease]:
    """Observing releases whose window has ended, oldest end first."""
    at = _now(now)
    rows = list(ImprovementRelease.query.filter(project_key=project_key, state="observing"))
    due = [r for r in rows if (aware(r.observation_window_ends_at) or at) <= at]
    due.sort(key=lambda r: aware(r.observation_window_ends_at) or at)
    return due


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def _resolve_repo(runner: Runner, repo: str | None) -> str:
    if repo is not None:
        return str(Path(repo).resolve())
    result = runner(["git", "rev-parse", "--show-toplevel"], cwd=None, timeout=_git_timeout())
    top = (result.stdout or "").strip()
    if result.returncode != 0 or not top:
        raise ReleaseRefused(
            "ROLLBACK_STEP_FAILED", "cwd is not inside a git repository and no repo was given"
        )
    return top


def _check_branch(runner: Runner, branch: str | None, *, repo: str) -> None:
    """Refuse an option-shaped or malformed ``--branch`` before it reaches ``git fetch``.

    ``--branch=--prune`` would otherwise run a pruning fetch before failing;
    ``git check-ref-format --branch`` is the authority on the rest.
    """
    if branch is None:
        return
    if not branch.strip() or branch.startswith("-"):
        raise ReleaseRefused("ROLLBACK_STEP_FAILED", f"branch {branch!r} is not a branch name")
    check = runner(
        ["git", "check-ref-format", "--branch", branch], cwd=repo, timeout=_git_timeout()
    )
    if check.returncode != 0:
        raise ReleaseRefused(
            "ROLLBACK_STEP_FAILED",
            f"git check-ref-format --branch {branch!r} exited {check.returncode}",
        )


def rollback_ref(release_id: str) -> str:
    """The local ref that keeps a rollback's revert commit reachable past its worktree."""
    return f"{ROLLBACK_REF_PREFIX}{release_id}"


def rollback(
    release_id: str,
    *,
    project_key: str,
    reason: str,
    runner: Runner,
    branch: str | None = None,
    root: str | Path | None = None,
    issue: int = 3218,
    now: datetime | None = None,
    repo: str | None = None,
) -> ImprovementRelease:
    """Revert the merge on a freshly fetched ``origin/<target>`` and push it.

    ``target = branch or "main"``. The worktree slot is
    ``<retention root>/drills/<release id>/<timestamp>``, refused exactly as
    the drill refuses it (``CHECKOUT_PATH`` in the detail) before any step
    runs; a ``--branch`` is validated before the fetch. The worktree comes
    from ``origin/<target>`` after ``git fetch origin <target>``; when the
    fetch of a named branch fails (the remote has no such branch) the
    worktree comes from ``origin/main`` and the record carries ``pushed_to:
    <branch>``. Once the revert is committed, ``refs/improvement-rollback/<release
    id>`` in the repository points at it, so the commit outlives the worktree
    whatever the push does. The release transitions to ``rolled_back`` only
    after ``git push`` returned 0 and ``git ls-remote origin refs/heads/<target>``
    resolves to the revert commit; anything else appends
    ``rollback_push_refused`` to the history, leaves the state, and raises
    ``ROLLBACK_PUSH_REFUSED``.

    The transcript of every step is persisted, bounded at
    :data:`ROLLBACK_TRANSCRIPT_BYTES`: under ``outcome.rollback.transcript``
    on success, and under ``outcome.rollback_attempt`` (with the refusal code
    and detail) when a step failed or the push was refused, beside a
    ``rollback_step_failed`` or ``rollback_push_refused`` history event.
    """
    from tools.improvement_release.drill import (
        DrillRefused,
        add_detached_worktree,
        assert_restored,
        drill_root,
        refuse_checkout_path,
        remove_worktree,
    )

    at = _now(now)
    release = get_release(release_id, project_key)
    _require_state(release, ("observing", "accepted"), "rollback")
    exposure = _dict_field(release, "exposure")
    merge_sha = exposure.get("merge_sha")
    if not isinstance(merge_sha, str) or not _HEX40.match(merge_sha):
        raise ReleaseRefused("ROLLBACK_STEP_FAILED", "the release records no merge_sha to revert")
    if not (reason or "").strip():
        raise ReleaseRefused("ROLLBACK_STEP_FAILED", "a rollback needs a reason")
    try:
        worktree = refuse_checkout_path(drill_root(release.id, root=root, now=at), root=root)
    except DrillRefused as exc:
        raise ReleaseRefused("ROLLBACK_STEP_FAILED", f"{exc.code}: {exc.detail}") from exc
    surfaces = _list_field(release, "surfaces")
    target = branch or "main"
    parent = target
    transcript: list[str] = []
    steps: list[dict] = []
    outcome = _outcome(release)
    repo_path = _resolve_repo(runner, repo)
    _check_branch(runner, branch, repo=repo_path)

    def step(argv: list[str], *, cwd: str | None, name: str) -> dict:
        record = run_step(
            runner, argv, cwd=cwd, timeout=_git_timeout(), transcript=transcript, name=name
        )
        steps.append(record)
        return record

    def fail(detail: str) -> ReleaseRefused:
        transcript.append(f"[rollback refused: {detail}]")
        return ReleaseRefused("ROLLBACK_STEP_FAILED", detail)

    def persist_failure(exc: ReleaseRefused) -> None:
        if exc.code == "ROLLBACK_STEP_FAILED":
            _append_history(outcome, "rollback_step_failed", at, target=target, detail=exc.detail)
        outcome["rollback_attempt"] = {
            "at": at.isoformat(),
            "code": exc.code,
            "detail": exc.detail,
            "target": target,
            "steps": steps,
            "transcript": tail("\n".join(transcript), ROLLBACK_TRANSCRIPT_BYTES),
        }
        _save_outcome_only(release, outcome)

    try:
        fetch = step(["git", "fetch", "origin", target], cwd=repo_path, name="fetch")
        if fetch["returncode"] != 0:
            if branch is None:
                raise fail(f"git fetch origin {target} exited {fetch['returncode']}")
            parent = "main"
            fetch = step(["git", "fetch", "origin", parent], cwd=repo_path, name="fetch_main")
            if fetch["returncode"] != 0:
                raise fail(f"git fetch origin {parent} exited {fetch['returncode']}")
    except ReleaseRefused as exc:
        persist_failure(exc)
        raise

    record: dict | None = None
    failure: ReleaseRefused | None = None
    try:
        add = add_detached_worktree(
            runner, repo=repo_path, ref=f"origin/{parent}", path=worktree, transcript=transcript
        )
        steps.append(add)
        if add["returncode"] != 0:
            raise fail(f"git worktree add exited {add['returncode']}: {add['stderr_tail']}")
        cwd = str(worktree)

        parent_step = step(["git", "rev-parse", f"origin/{parent}"], cwd=cwd, name="parent_sha")
        parent_sha = parent_step["stdout_tail"].strip()

        parents = step(
            ["git", "rev-list", "--parents", "-n", "1", merge_sha], cwd=cwd, name="parents"
        )
        is_merge = len(parents["stdout_tail"].split()) > 2
        revert_argv = ["git", "revert", "--no-commit"]
        if is_merge:
            revert_argv += ["-m", "1"]
        revert = step([*revert_argv, merge_sha], cwd=cwd, name="revert")
        if revert["returncode"] != 0:
            raise fail(f"git revert exited {revert['returncode']}: {revert['stderr_tail']}")
        message = f"Roll back improvement release {release.id}: {reason.strip()} (Refs #{issue})"
        commit = step(["git", "commit", "-m", message], cwd=cwd, name="commit")
        if commit["returncode"] != 0:
            raise fail(f"git commit exited {commit['returncode']}: {commit['stderr_tail']}")
        head = step(["git", "rev-parse", "HEAD"], cwd=cwd, name="revert_sha")
        revert_sha = head["stdout_tail"].strip()
        if not _HEX40.match(revert_sha):
            raise fail(f"git rev-parse HEAD answered {revert_sha!r}, not a SHA")
        # The revert outlives the worktree: a lightweight ref in the repository
        # keeps it reachable after `git worktree remove`, whatever the push does.
        keep = step(
            ["git", "update-ref", rollback_ref(release.id), revert_sha],
            cwd=repo_path,
            name="rollback_ref",
        )
        if keep["returncode"] != 0:
            raise fail(f"git update-ref exited {keep['returncode']}: {keep['stderr_tail']}")

        verification = {"base_revision": release.base_revision, "surfaces": surfaces}
        if release.base_revision and surfaces:
            verification.update(
                assert_restored(
                    runner,
                    worktree=cwd,
                    base_revision=release.base_revision,
                    surfaces=surfaces,
                    transcript=transcript,
                )
            )
        verification["note"] = (
            "informational: main has moved since base_revision, so a difference is expected"
        )

        # Fully qualified: from a detached worktree, "HEAD:<name>" is refused with
        # "not a full refname" whenever refs/heads/<name> does not exist yet on
        # the remote, which is exactly the --branch <new-name> case.
        push = step(["git", "push", "origin", f"HEAD:refs/heads/{target}"], cwd=cwd, name="push")
        refused_detail = None
        if push["returncode"] != 0:
            refused_detail = f"git push origin HEAD:refs/heads/{target} exited {push['returncode']}"
        else:
            remote = step(
                ["git", "ls-remote", "origin", f"refs/heads/{target}"], cwd=cwd, name="ls_remote"
            )
            remote_sha = (remote["stdout_tail"].split() or [""])[0]
            if remote_sha != revert_sha:
                refused_detail = (
                    f"refs/heads/{target} resolves to {remote_sha!r} after the push, "
                    f"not the revert {revert_sha}"
                )
        if refused_detail is not None:
            _append_history(
                outcome,
                "rollback_push_refused",
                at,
                stderr=tail(push["stderr_tail"]),
                revert_sha=revert_sha,
                parent_sha=parent_sha,
                target=target,
                rollback_ref=rollback_ref(release.id),
                detail=refused_detail,
            )
            transcript.append(f"[rollback refused: {refused_detail}]")
            raise ReleaseRefused("ROLLBACK_PUSH_REFUSED", f"{refused_detail}; revert {revert_sha}")

        record = {
            "reason": reason.strip(),
            "merge_sha": merge_sha,
            "merge_commit_parents": len(parents["stdout_tail"].split()) - 1,
            "revert_sha": revert_sha,
            "parent_sha": parent_sha,
            "pushed_to": target,
            "worktree_ref": f"origin/{parent}",
            "rollback_ref": rollback_ref(release.id),
            "verification": verification,
            "propagation": ROLLBACK_PROPAGATION_NOTE,
            "steps": steps,
            "rolled_back_at": at.isoformat(),
        }
        if branch is not None:
            record["pr_command"] = (
                f"gh pr create --head {branch} --base main --title "
                f"{json.dumps(f'Roll back improvement release {release.id}')} "
                f"--body {json.dumps(message)}"
            )
            record["propagation"] = f"{ROLLBACK_PROPAGATION_NOTE}; open the PR from {branch}"
    except ReleaseRefused as exc:
        failure = exc
    finally:
        remove_worktree(runner, repo=repo_path, path=worktree, transcript=transcript, root=root)
    if failure is not None:
        persist_failure(failure)
        raise failure
    record["transcript"] = tail("\n".join(transcript), ROLLBACK_TRANSCRIPT_BYTES)
    outcome["rollback"] = record
    return _transition(
        release,
        allowed_from=("observing", "accepted"),
        to="rolled_back",
        event="rolled_back",
        now=at,
        outcome=outcome,
        revert_sha=revert_sha,
        parent_sha=parent_sha,
        pushed_to=target,
    )


__all__ = [
    "AGENT_IDENTITIES",
    "EXPOSURE_PLAN",
    "HISTORY_MAX",
    "REFUSAL_CODES",
    "ROLLBACK_REF_PREFIX",
    "ROLLBACK_TRANSCRIPT_BYTES",
    "WINDOW_SUM_MAX_DAYS",
    "ReleaseRefused",
    "approve",
    "close_window",
    "due_windows",
    "expose",
    "get_release",
    "open_pr",
    "propose",
    "rollback",
    "rollback_ref",
    "withdraw",
]
