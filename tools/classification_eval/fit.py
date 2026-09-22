"""The fit path of the comparison runner (#3420, lane B).

``python -m tools.classification_eval --site <id> --fit --candidate
local_encoder,anthropic`` distills a per-site linear head from the site's
reference arm and measures it by lane A's bar:

1. :func:`split_by_digest` divides the drawn inputs into a held-out split
   (the site minimum, at least half real, chosen by content digest before
   any label exists) and a training split (everything else). The split is
   a pure function of the input set, so a saved draw replays it anywhere.
2. :func:`label_training_split` labels the training split with the
   reference arm at the runner's concurrency-4 gate; an arm error drops the
   input, and an error rate over :data:`MAX_ERROR_RATE` aborts the fit.
3. :func:`fit_head` fits a multinomial logistic regression on the leg's own
   embeddings (numpy, zero init, :data:`FIT_EPOCHS` full-batch epochs at
   :data:`FIT_LR` with L2 :data:`FIT_L2`; deterministic, bit-identical on a
   re-run). :func:`cv_agreement` runs the same routine five-fold for the
   free ``--precheck``.
4. :func:`write_head` writes the head to the staging path
   (``data/classification_eval/heads/<site>.<run_id>.json``, gitignored).
5. ``compare`` runs unchanged on the held-out split only, with the
   ``local_encoder`` arm reading the staged head by path; the record gains
   a ``fit`` block with ``landed``.
6. Under ``land=True`` only, :func:`run_fit` applies the landing rule: the
   staged head is copied to the served path when ``evaluate_bar`` is empty
   for both the ``local_encoder`` and the ``anthropic`` arm; any miss deletes
   a served head (no orphan weights) and names the arm in ``fit.miss_arms``.
   Without ``land`` the served path is never written or deleted, so a
   measure-only run on a landed site is a drift check.

The precheck gate (:data:`PRECHECK_MARGIN`) skips a site whose five-fold
agreement on lane A's fixtures sits more than the margin under its bar:
zero reference calls, no record, one ``precheck_below_bar`` claim on the
case. Every refusal, the encoder runtime included
(:func:`ensure_encoder_runtime`), fires before the first reference call.
:func:`preflight` says whether this machine's memory store can meet the
held-out real-message need before any spend.

What the encoder sees: :func:`encoder_text` is the row's ``encoder_text``
composition or the bare message, and :func:`measured_site` measures both
landing arms on that text with the row's ``encoder_system`` as the
``anthropic`` arm's system prompt, the shape the landed call serves. Lane
A's ``candidate_prompt`` and ``candidate_system`` belong to the generative
arm and never reach this path.

The encoder leg (``agent/llm/backends/local_encoder.py``) is imported
inside the functions that need it, so this package imports without the
``classification-local`` extra.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tools.classification_eval.core import (
    LATENCY_CONCURRENCY,
    MAX_ERROR_RATE,
    PROJECT_KEY,
    TIER_BAR,
    Arm,
    ComparisonRecord,
    Input,
    ShortfallError,
    Site,
    compare,
    evaluate_bar,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

logger = logging.getLogger(__name__)

FIT_ISSUE_URL = "https://github.com/tomcounsell/ai/issues/3420"

PRECHECK_MARGIN = 0.10
"""A site whose precheck agreement is under ``bar - PRECHECK_MARGIN`` skips the fit."""

FIT_EPOCHS = 300
FIT_LR = 0.5
FIT_L2 = 1e-3
"""The spike's settings (Spike 3); recorded into every head's ``fit_settings``."""

ROUTING_REAL_NEED = 100
"""Half the routing sites' minimum: the real messages their held-out split needs."""

STAGED_HEADS_DIR = Path(__file__).resolve().parents[2] / "data" / "classification_eval" / "heads"
"""Where a fit writes its head before (and regardless of) landing; gitignored."""

PRECHECK_EXCLUDED_SITES = frozenset({"job_router.route"})
"""C12: its ``job_id`` answer is an open set per call, not a classification."""


class FitRefusalError(Exception):
    """A refusal before any reference spend (exit 2): an empty training
    split, contention, a landing without the ``anthropic`` candidate, the
    precheck gate, an encoder runtime this machine cannot load."""


class FitError(Exception):
    """An abort after labeling started (exit 1): the reference error rate on
    the training split, or labels collapsing to one class. No head is
    written, staged or served."""


# --- the split -----------------------------------------------------------------------


def _digest(inp: Input) -> str:
    return hashlib.sha256(inp.text.encode()).hexdigest()


def split_by_digest(inputs: Sequence[Input], minimum_n: int) -> tuple[list[Input], list[Input]]:
    """``(held_out, train)``: a pure function of the input set.

    Inputs sort by ``sha256(text)``. The held-out split takes real inputs
    first, by digest, up to ``ceil(minimum_n / 2)``, then fixtures and the
    remaining real inputs by digest until it holds ``minimum_n``; everything
    else is the training split, in digest order. Raises
    :class:`ShortfallError` when the set cannot supply ``minimum_n`` held-out
    inputs with at least half of them real, before any arm runs.
    """
    ordered = sorted(inputs, key=_digest)
    half = ceil(minimum_n / 2)
    real_idx = [i for i, inp in enumerate(ordered) if inp.source == "real"]
    n_real = len(real_idx)
    if n_real < half or len(ordered) < minimum_n:
        raise ShortfallError(
            f"held-out split needs {minimum_n} inputs with at least {half} real;"
            f" this draw has {len(ordered)} inputs ({n_real} real); short by"
            f" {max(0, half - n_real)} real and {max(0, minimum_n - len(ordered))} in total."
            " No arm ran, no record written."
        )
    held: set[int] = set(real_idx[:half])
    for i in range(len(ordered)):
        if len(held) >= minimum_n:
            break
        held.add(i)
    held_out = [inp for i, inp in enumerate(ordered) if i in held]
    train = [inp for i, inp in enumerate(ordered) if i not in held]
    return held_out, train


# --- labeling -------------------------------------------------------------------------


async def label_training_split(
    reference: Arm, train: Sequence[Input], site: Site
) -> list[tuple[Input, str]]:
    """The training split labeled by the reference arm (the site's prompt
    verbatim, its ``system``, its output type) at :data:`LATENCY_CONCURRENCY`,
    in training order. An arm error drops that input (the caller counts the
    drop as ``len(train) - len(result)``); an error rate over
    :data:`MAX_ERROR_RATE` raises :class:`FitError` so no head is written."""
    import asyncio

    gate = asyncio.Semaphore(LATENCY_CONCURRENCY)

    async def one(inp: Input) -> str | None:
        async with gate:
            try:
                output, *_ = await reference.call(site.prompt(inp), site.system, site.output_type)
            except Exception as e:
                logger.warning("classification_eval fit: reference arm errored: %s", e)
                return None
            return site.label(output)

    labels = await asyncio.gather(*(one(inp) for inp in train))
    errors = sum(1 for label in labels if label is None)
    if train and errors / len(train) > MAX_ERROR_RATE:
        raise FitError(
            f"reference arm error rate {errors / len(train):.3f} on the training split"
            f" ({errors}/{len(train)}) exceeds {MAX_ERROR_RATE:.3f}; no head written"
        )
    return [(inp, label) for inp, label in zip(train, labels, strict=True) if label is not None]


# --- the fit --------------------------------------------------------------------------


@dataclass(frozen=True)
class Weights:
    """A fitted head's parameters: ``W`` is ``dim x k`` and ``b`` is ``k``,
    columns in ``classes`` order."""

    W: np.ndarray
    b: np.ndarray


def _one_hot(labels: Sequence[str], classes: Sequence[str]) -> np.ndarray:
    import numpy as np

    index = {name: j for j, name in enumerate(classes)}
    one_hot = np.zeros((len(labels), len(classes)), dtype=np.float64)
    for i, label in enumerate(labels):
        one_hot[i, index[label]] = 1.0
    return one_hot


def _softmax(logits: np.ndarray) -> np.ndarray:
    import numpy as np

    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    import numpy as np

    rows = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return rows / norms


def fit_head(vectors: np.ndarray, labels: Sequence[str], classes: Sequence[str]) -> Weights:
    """Multinomial logistic regression by full-batch gradient descent: zero
    init, :data:`FIT_EPOCHS` epochs, learning rate :data:`FIT_LR`, L2
    :data:`FIT_L2` on ``W``, inputs L2-normalized. No randomness and no early
    stopping, so a second fit on identical inputs and labels is bit-identical.
    """
    import numpy as np

    x = _normalize_rows(vectors)
    y = _one_hot(labels, classes)
    n, dim = x.shape
    w = np.zeros((dim, len(classes)), dtype=np.float64)
    b = np.zeros(len(classes), dtype=np.float64)
    for _ in range(FIT_EPOCHS):
        residual = (_softmax(x @ w + b) - y) / n
        w -= FIT_LR * (x.T @ residual + FIT_L2 * w)
        b -= FIT_LR * residual.sum(axis=0)
    return Weights(W=w, b=b)


def predict(vectors: np.ndarray, weights: Weights, classes: Sequence[str]) -> list[str]:
    """The argmax class per row of ``vectors`` under ``weights``."""
    logits = _normalize_rows(vectors) @ weights.W + weights.b
    return [classes[int(j)] for j in logits.argmax(axis=1)]


def cv_agreement(
    vectors: np.ndarray,
    labels: Sequence[str],
    classes: Sequence[str],
    *,
    folds: int = 5,
    seed: int = 0,
) -> float:
    """Five-fold cross-validated agreement of :func:`fit_head` with ``labels``:
    the number the precheck gate reads. Every fold fits through the same
    :func:`fit_head` the landing run uses, never a separate routine."""
    import numpy as np

    n = len(labels)
    order = np.random.default_rng(seed).permutation(n)
    agreed = 0
    scored = 0
    for fold in range(folds):
        held = order[fold::folds]
        if len(held) == 0:
            continue
        mask = np.ones(n, dtype=bool)
        mask[held] = False
        train_labels = [labels[i] for i in np.flatnonzero(mask)]
        if len(set(train_labels)) < 2:
            continue
        weights = fit_head(vectors[mask], train_labels, classes)
        predicted = predict(vectors[held], weights, classes)
        agreed += sum(1 for i, p in zip(held, predicted, strict=True) if labels[i] == p)
        scored += len(held)
    return agreed / scored if scored else 0.0


def majority_baseline(labels: Sequence[str]) -> float:
    """The share of the most common label: what a constant answer scores."""
    if not labels:
        return 0.0
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return max(counts.values()) / len(labels)


# --- head files ------------------------------------------------------------------------


def staged_head_path(site_id: str, run_id: str) -> Path:
    """``data/classification_eval/heads/<site>.<run_id>.json``: where a fit
    writes its head; the served path is touched only under ``--land``."""
    return STAGED_HEADS_DIR / f"{site_id}.{run_id}.json"


def served_head_path(site_id: str) -> Path:
    """The leg's served head path (``agent/llm/backends/heads/<site>.json``),
    re-exported so the runner and the audit share one name for it."""
    from agent.llm.backends import local_encoder as leg

    return leg.served_head_path(site_id)


def write_head(path: Path, head: Any) -> None:
    """Write ``head.to_dict()`` atomically (tmp file, then ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(head.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _install_served_head(staged: Path, served: Path) -> None:
    """Copy the staged head into the served path with an atomic rename."""
    served.parent.mkdir(parents=True, exist_ok=True)
    tmp = served.with_name(f".{served.name}.{uuid.uuid4().hex}.tmp")
    shutil.copyfile(staged, tmp)
    os.replace(tmp, served)


def encoder_text(site: Site, inp: Input) -> str:
    """The text the encoder embeds for ``inp``: the row's ``encoder_text``
    when the landing builder set one (the message-first composition function
    a context-bearing site shares with its call site), else the message text
    itself. Never the reference prompt and never the row's
    ``candidate_prompt``: that is lane A's prompt for a generative arm, and
    on C2, C6 and C9 it carries the instruction block, which would dominate
    a CLS embedding and push the message past the 512-token window (the
    spike embedded the bare text). :func:`measured_site` hands ``compare``
    this same function, so the measurement embeds byte-for-byte what the fit
    embedded."""
    if site.encoder_text is not None:
        return site.encoder_text(inp)
    return inp.text


def measured_site(site: Site) -> Site:
    """``site`` in the shape a ``LOCAL_ENCODER`` landing serves, for
    ``compare``: the candidate prompt is :func:`encoder_text`, the candidate
    system is the row's ``encoder_system`` (``None`` falls through to the
    row's ``system`` in ``compare``), and the candidate output type is the
    site's own. Both landing arms then receive what the served call passes:
    the ``local_encoder`` arm the text the head was fit on, the ``anthropic``
    arm that text with the instruction block as its system prompt, which is
    the restructured fallback shape. Lane A's ``candidate_*`` fields never
    reach the encoder lane's measurement."""
    return replace(
        site,
        candidate_prompt=partial(encoder_text, site),
        candidate_system=site.encoder_system,
        candidate_output_type=None,
    )


def _embed_all(site: Site, inputs: Sequence[Input]) -> np.ndarray:
    import numpy as np

    from agent.llm.backends import local_encoder as leg

    return np.stack(
        [np.asarray(leg.embed(encoder_text(site, inp)), dtype=np.float64) for inp in inputs]
    )


def ensure_encoder_runtime() -> None:
    """Load and verify the encoder runtime (extra importable, weights present
    with their pinned sha256, session built) and raise
    :class:`FitRefusalError` when the leg refuses, so a fit on a machine that
    cannot embed stops before its first reference call."""
    from agent.llm.backends import local_encoder as leg
    from agent.llm.errors import LLMCallError

    try:
        leg.load_runtime()
    except LLMCallError as e:
        raise FitRefusalError(f"encoder runtime unavailable, no reference call made: {e}") from e


def build_head(
    site: Site,
    weights: Weights,
    classes: Sequence[str],
    *,
    run_id: str,
    n_train: int,
    n_train_real: int,
    reference_model: str,
) -> Any:
    """A ``Head`` (the leg's dataclass) carrying the weights and the fit's provenance."""
    from agent.llm.backends import local_encoder as leg
    from config.models import LOCAL_ENCODER_FILES, LOCAL_ENCODER_MODEL, LOCAL_ENCODER_REVISION

    return leg.Head(
        site=site.id,
        classes=list(classes),
        W=[[float(v) for v in row] for row in weights.W],
        b=[float(v) for v in weights.b],
        embedding_model=LOCAL_ENCODER_MODEL,
        embedding_revision=LOCAL_ENCODER_REVISION,
        embedding_sha256=LOCAL_ENCODER_FILES["onnx/model_int8.onnx"],
        run_id=run_id,
        n_train=n_train,
        n_train_real=n_train_real,
        reference_model=reference_model,
        created_at=datetime.now(UTC).isoformat(),
        fit_settings={"epochs": FIT_EPOCHS, "lr": FIT_LR, "l2": FIT_L2, "normalized": True},
    )


# --- run_fit ------------------------------------------------------------------------


@dataclass
class FitOutcome:
    """What one ``--fit`` run produced; ``skipped`` names the gate that stopped it."""

    run_id: str
    skipped: str | None = None
    record: ComparisonRecord | None = None
    evidence_id: str | None = None
    staged_path: Path | None = None
    train_agreement: float | None = None
    landed: bool = False
    miss_arms: list[str] = field(default_factory=list)


def precheck_gate(site: Site, precheck_agreement: float) -> bool:
    """True when ``precheck_agreement`` is under ``bar - PRECHECK_MARGIN``."""
    return precheck_agreement < TIER_BAR[site.tier] - PRECHECK_MARGIN


def landing_verdicts(payload: Mapping[str, Any]) -> dict[str, list[str]]:
    """``evaluate_bar`` for both landing arms on one held-out record: an arm
    absent from the record misses on ``missing``."""
    verdicts: dict[str, list[str]] = {}
    for name in ("local_encoder", "anthropic"):
        verdicts[name] = (
            evaluate_bar(payload, name) if name in payload["candidates"] else ["missing"]
        )
    return verdicts


async def run_fit(
    site: Site,
    inputs: Sequence[Input],
    *,
    reference: Arm,
    candidates: Sequence[str],
    build_arm: Callable[[str, Path], Arm],
    contended: bool,
    land: bool = False,
    precheck_agreement: float | None = None,
    run_id: str | None = None,
    project_key: str = PROJECT_KEY,
    attach: bool = True,
    out: Callable[[str], None] = print,
) -> FitOutcome:
    """The fit path, steps 1 to 6 of the module docstring.

    ``build_arm(name, staged_head_path)`` builds each candidate arm named in
    ``candidates`` (the ``local_encoder`` arm reads the staged head by path).
    Refusals before any reference call: the input minimums
    (:class:`ShortfallError`), an empty training split, ``contended`` (Race 2
    and 3), ``land`` without an ``anthropic`` candidate (Risk 3), the
    precheck gate when ``precheck_agreement`` is given (one claim on the
    case, no record), and an encoder runtime this machine cannot load
    (:func:`ensure_encoder_runtime`). ``land`` is the only way the served
    head is written or deleted.
    """
    from tools.classification_eval.records import (
        attach_claims,
        attach_precheck_claim,
        write_record,
    )

    run_id = run_id or uuid.uuid4().hex
    if land and "anthropic" not in candidates:
        raise FitRefusalError(
            "--land needs the anthropic candidate arm: the restructured-shape fallback is"
            " part of the landing (Risk 3); add --candidate local_encoder,anthropic"
        )
    if "local_encoder" not in candidates:
        raise FitRefusalError("--fit measures the local_encoder arm; add --candidate local_encoder")
    held_out, train = split_by_digest(inputs, site.minimum_n)
    if not train:
        raise FitRefusalError(
            f"the draw holds exactly the {site.minimum_n} held-out inputs the minimum needs"
            " and nothing to train on; draw more (raise --real-limit or add fixtures)."
            " No arm ran, no record written."
        )
    if contended:
        raise FitRefusalError(
            "com.valor services are loaded; a fit changes what serves (Race 2) and the"
            " record could not clear the bar (Race 3). Stop them with"
            " ./scripts/valor-service.sh stop and re-run."
        )
    if precheck_agreement is not None and precheck_gate(site, precheck_agreement):
        gate = TIER_BAR[site.tier] - PRECHECK_MARGIN
        out(
            f"precheck_below_bar: {site.id} precheck agreement {precheck_agreement:.3f} <"
            f" {gate:.3f} (bar {TIER_BAR[site.tier]:.2f} - margin {PRECHECK_MARGIN:.2f});"
            " no reference call, no record"
        )
        if attach:
            attach_precheck_claim(
                site.id, precheck_agreement, TIER_BAR[site.tier], project_key=project_key
            )
        return FitOutcome(run_id=run_id, skipped="precheck_below_bar")
    ensure_encoder_runtime()

    labeled = await label_training_split(reference, train, site)
    n_train = len(labeled)
    n_train_real = sum(1 for inp, _ in labeled if inp.source == "real")
    out(
        f"training split: {n_train} labeled by {reference.name} ({reference.model}),"
        f" {len(train) - n_train} reference errors dropped, {n_train_real} real"
    )
    classes = sorted({label for _, label in labeled})
    if len(classes) < 2:
        raise FitError(
            f"training labels collapsed to one class {classes}; no head written"
            " (a one-class head answers that class for everything)"
        )
    vectors = _embed_all(site, [inp for inp, _ in labeled])
    labels = [label for _, label in labeled]
    weights = fit_head(vectors, labels, classes)
    train_agreement = sum(
        1 for p, label in zip(predict(vectors, weights, classes), labels, strict=True) if p == label
    ) / len(labels)
    out(
        f"training-split agreement {train_agreement:.3f} over {n_train}"
        " (a sanity line; the claim is the held-out record)"
    )
    head = build_head(
        site,
        weights,
        classes,
        run_id=run_id,
        n_train=n_train,
        n_train_real=n_train_real,
        reference_model=reference.model,
    )
    staged = staged_head_path(site.id, run_id)
    write_head(staged, head)
    out(f"staged head: {staged}")

    record = await compare(
        measured_site(site),
        held_out,
        reference=reference,
        candidates=[build_arm(name, staged) for name in candidates],
        contended=contended,
    )
    record.run_id = run_id
    record.fit = {
        "head_run_id": run_id,
        "n_train": n_train,
        "n_train_real": n_train_real,
        "split": "digest",
        "landed": False,
        "miss_arms": [],
        "miss_criteria": {},
    }
    outcome = FitOutcome(
        run_id=run_id, record=record, staged_path=staged, train_agreement=train_agreement
    )
    if land:
        verdicts = landing_verdicts(record.as_dict())
        served = served_head_path(site.id)
        if all(not failed for failed in verdicts.values()):
            _install_served_head(staged, served)
            record.fit["landed"] = True
            outcome.landed = True
            out(f"landed: {served} (head {run_id})")
        else:
            misses = [name for name, failed in verdicts.items() if failed]
            record.fit["miss_arms"] = misses
            record.fit["miss_criteria"] = {name: verdicts[name] for name in misses}
            outcome.miss_arms = misses
            if served.exists():
                served.unlink()
                out(f"landing MISS: removed served head {served}")
    evidence_id = write_record(record, project_key=project_key)
    outcome.evidence_id = evidence_id
    if attach:
        attach_claims(record, evidence_id, project_key=project_key)
    return outcome


# --- --preflight ------------------------------------------------------------------------


def preflight(
    real_limit: int = 2000,
    *,
    sites: Mapping[str, Site] | None = None,
    real: Callable[..., list[Input]] | None = None,
    project_key: str = PROJECT_KEY,
    out: Callable[[str], None] = print,
) -> int:
    """Say whether this machine's memory store can satisfy the held-out real
    need before any spend: the real-message count, then per site the need,
    ``n_train``, and ``n_train_real`` from :func:`split_by_digest` on the
    actual draw. Exit 1 when the store is under the routing sites' need
    (:data:`ROUTING_REAL_NEED`). ``sites`` and ``real`` are the test seams."""
    from tools.classification_eval.arms import real_messages, site_inputs

    loader = real or real_messages
    if sites is None:
        from tools.classification_eval.sites import SITES

        sites = SITES
    available = len(loader(real_limit, project_key=project_key))
    out(f"real messages available: {available}")
    for site_id in sorted(sites):
        site = sites[site_id]
        if site.task.client_only:
            continue
        draw = site_inputs(site, real_limit, project_key=project_key, default=loader)
        n_fixtures = sum(1 for inp in draw if inp.source == "fixture")
        n_real = len(draw) - n_fixtures
        need = ceil(site.minimum_n / 2)
        head = f"{site_id:<36} needs >= {need} real in the held-out split"
        try:
            _, train = split_by_digest(draw, site.minimum_n)
        except ShortfallError:
            out(f"{head}; this store has {n_real}: short by {max(need - n_real, 0)}")
            continue
        n_train_real = sum(1 for inp in train if inp.source == "real")
        out(
            f"{head}; this store has {n_real}: ok;"
            f" n_train = {len(train)} ({n_fixtures} fixtures + {n_real} real - {site.minimum_n});"
            f" n_train_real = {n_train_real}"
        )
    if available < ROUTING_REAL_NEED:
        out(
            f"preflight: FAIL (routing sites need >= {ROUTING_REAL_NEED} real in the held-out"
            f" split; this store has {available})"
        )
        return 1
    out("preflight: PASS")
    return 0


# --- --precheck -------------------------------------------------------------------------


@dataclass(frozen=True)
class PrecheckRow:
    site: str
    n: int
    agreement: float
    majority: float
    bar: float

    @property
    def gate(self) -> float:
        return self.bar - PRECHECK_MARGIN

    @property
    def mark(self) -> str:
        return "precheck_below_bar" if self.agreement < self.gate else "fit"


def precheck_rows(
    sites: Mapping[str, Site], *, project_key: str = PROJECT_KEY
) -> tuple[list[PrecheckRow], list[str]]:
    """One row per site with a lane A record (C12 and ``client_only`` sites
    excluded): the reference labels of the record's fixture prefix paired
    with ``site.fixtures()``, embedded through the leg, scored five-fold
    through :func:`fit_head`. Zero spend. Returns the rows ordered by
    agreement relative to the bar, highest first, and the sites skipped
    with the reason."""
    from tools.classification_eval.records import latest_record

    rows: list[PrecheckRow] = []
    skipped: list[str] = []
    for site_id in sorted(sites):
        site = sites[site_id]
        if site_id in PRECHECK_EXCLUDED_SITES or site.task.client_only:
            continue
        found = latest_record(site_id, project_key=project_key)
        if found is None or not found[1].get("reference"):
            skipped.append(f"{site_id}: no lane A record with a reference arm")
            continue
        record = found[1]
        fixtures = site.fixtures()
        # NOTE: labels pair with fixtures by position under a count guard, not
        # by id -- left as-is because lane A's records carry per-input labels
        # only (no text or digest per label), so position is the only
        # alignment those records can offer; a fixture edit that keeps the
        # count is the precheck's known blind spot until records carry digests.
        labels = list(record["reference"]["labels"][: int(record["n_fixture"])])
        if len(labels) != len(fixtures):
            n_fixture = record["n_fixture"]
            skipped.append(
                f"{site_id}: record n_fixture {n_fixture} != {len(fixtures)} fixtures today"
            )
            continue
        pairs = [(inp, label) for inp, label in zip(fixtures, labels, strict=True) if label]
        classes = sorted({label for _, label in pairs})
        if len(classes) < 2 or len(pairs) < 2:
            skipped.append(f"{site_id}: reference labels collapse to {classes}")
            continue
        vectors = _embed_all(site, [inp for inp, _ in pairs])
        paired_labels = [label for _, label in pairs]
        rows.append(
            PrecheckRow(
                site=site_id,
                n=len(pairs),
                agreement=cv_agreement(vectors, paired_labels, classes),
                majority=majority_baseline(paired_labels),
                bar=TIER_BAR[site.tier],
            )
        )
    rows.sort(key=lambda row: (row.agreement - row.bar, row.site), reverse=True)
    return rows, skipped


def render_precheck(rows: Sequence[PrecheckRow], skipped: Sequence[str]) -> str:
    """The precheck table as markdown, paste-able into the PR body."""
    lines = [
        "| site | n | cv_agreement | majority | bar | gate | mark |",
        "|------|---|--------------|----------|-----|------|------|",
    ]
    lines.extend(
        f"| {row.site} | {row.n} | {row.agreement:.3f} | {row.majority:.3f}"
        f" | {row.bar:.2f} | {row.gate:.2f} | {row.mark} |"
        for row in rows
    )
    lines.extend(f"skipped: {reason}" for reason in skipped)
    return "\n".join(lines)


def precheck(
    *,
    sites: Mapping[str, Site] | None = None,
    project_key: str = PROJECT_KEY,
    out: Callable[[str], None] = print,
) -> int:
    """The ``--precheck`` subcommand: print :func:`render_precheck` for every
    site with a lane A record on this machine. Exit 0 whenever at least one
    row was scored."""
    if sites is None:
        from tools.classification_eval.sites import SITES

        sites = SITES
    rows, skipped = precheck_rows(sites, project_key=project_key)
    out(render_precheck(rows, skipped))
    return 0 if rows else 1
