"""The local encoder leg of :func:`agent.llm.run_typed` (#3420, lane B).

A supervised local classifier: a pinned sentence-embedding model
(``config.models.LOCAL_ENCODER_MODEL``, int8 ONNX, served in-process on
CPU through ``onnxruntime``) and one committed linear head per landed
site, fit by ``tools/classification_eval`` on the site's reference-arm
labels. The call embeds ``prompt``, applies the head named by
``route.model`` (the site id), and returns a validated instance of
``output_type`` in a few milliseconds. No daemon, no GPU, no request.

Leg protocol (``agent/llm/backends/__init__.py``), as this leg meets it::

    async def call(prompt, output_type, route, *,
                   system, sdk_timeout, slot_timeout, max_retries,
                   deadline=None, stack) -> BaseModel

* ``bound_to_deadline`` runs first (fallback calls only): under 0.5 s of
  remainder raises ``LLMCallError(reason="timeout")`` before any load.
* ``system`` is accepted and **ignored**: the head was fit on the text
  alone, and the instruction block is carried for the Anthropic fallback,
  which receives it as its system prompt. ``slot_timeout``,
  ``max_retries`` and ``stack`` are accepted and unused: there is no
  semaphore, no SDK, and no third-party symbol taken from the stack. There
  is no request timer because there is no request; the CPU work is
  bounded by the 512-token truncation window, and it runs inside
  ``asyncio.to_thread`` so the event loop never blocks.
* Every failure is an :class:`LLMCallError`: ``transport`` for a missing
  extra, missing or mismatched weights, a missing head, or an exception
  inside the ONNX run (the one broad handler here, cause chained, one
  ``logger.error``); ``validation`` for an empty prompt, a malformed head,
  or an output type whose shape the head cannot answer. The wrapper's
  Anthropic fallback then runs inside the caller's budget.

Import-safety contract (#3001, #3525): module scope is stdlib and our own
code only. ``onnxruntime``, ``tokenizers`` and ``numpy`` are imported
inside :func:`_build_runtime`, :func:`embed` and :func:`scores`, so
``import agent.llm`` succeeds on a machine without the
``classification-local`` extra and the failure surfaces at the call.
:func:`load_runtime` (memoized once per process under a
``threading.Lock``, Race 1) is the test seam:
``monkeypatch.setattr(local_encoder, "load_runtime", lambda: fake)`` with
``tests.helpers.llm_fakes.FakeEncoderRuntime``. The leg never downloads:
``scripts/download_local_encoder_models.py`` fetches the weights at
``/update`` and this module verifies every file's sha256 against
``config.models.LOCAL_ENCODER_FILES`` at load and refuses on a mismatch.

Head file format (``agent/llm/backends/heads/<site>.json``, exactly
:meth:`Head.to_dict` written with ``indent=2, sort_keys=True`` plus one
trailing newline, as ``tools/classification_eval/fit.py::write_head``
writes it and ``tests/unit/test_classifier_heads.py`` pins it):

``site``, ``classes`` (the output field's values as the runner's ``label``
reducer renders them: ``"True"``/``"False"`` for a ``bool`` field, the
literal strings otherwise), ``W`` (``LOCAL_ENCODER_DIM`` rows of ``k``
floats, ``W[i][j]`` for dimension ``i`` and class ``j``), ``b`` (``k``
floats), and provenance: ``embedding_model``, ``embedding_revision``,
``embedding_sha256`` (the pinned int8 model's digest), ``run_id``,
``n_train``, ``n_train_real``, ``reference_model``, ``created_at`` (ISO
8601 UTC), ``fit_settings``. :func:`load_head` refuses fewer than two
classes, a ``W`` or ``b`` of the wrong shape, or a digest that is not the
pin; the score is ``softmax(vector @ W + b)``.

Output-type shape rule (:func:`_shape`): exactly one field typed ``bool``
or ``Literal[...]`` whose rendered values equal ``set(head.classes)``; an
optional ``confidence: float`` field receives the winning softmax score;
every other field must have a default. Enforced here at call time
(``validation``, no ONNX run) and statically by
``tests/unit/test_classifier_heads.py`` over every committed head.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

from agent.llm.backends import bound_to_deadline
from agent.llm.errors import LLMCallError
from config.models import (
    LOCAL_ENCODER_DIM,
    LOCAL_ENCODER_FILES,
    local_encoder_models_dir,
    sha256_file,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np
    from pydantic import BaseModel

    from agent.anthropic_client import LLMStack
    from agent.llm.router import Route

logger = logging.getLogger(__name__)

LEG = "local_encoder"
MODEL_FILE = "onnx/model_int8.onnx"
TOKENIZER_FILE = "tokenizer.json"
MAX_TOKENS = 512
DOWNLOAD_SCRIPT = "scripts/download_local_encoder_models.py"

HEADS_DIR = Path(__file__).parent / "heads"
"""Where the committed, serving heads live: one ``<site>.json`` per landed site."""


def served_head_path(site: str) -> Path:
    """The serving head for ``site``; the only head the live path ever reads."""
    return HEADS_DIR / f"{site}.json"


# ---------------------------------------------------------------------------
# Heads
# ---------------------------------------------------------------------------


@dataclass
class Head:
    """One site's linear head over the pinned embedding, with its provenance."""

    site: str
    classes: list[str]
    W: list[list[float]]
    b: list[float]
    embedding_model: str
    embedding_revision: str
    embedding_sha256: str
    run_id: str
    n_train: int
    n_train_real: int
    reference_model: str
    created_at: str
    fit_settings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Head:
        return cls(**{f: d[f] for f in cls.__dataclass_fields__ if f in d})


def _validate_head(head: Head, where: str) -> Head:
    k = len(head.classes)
    if k < 2:
        raise LLMCallError(
            f"{where}: a head needs at least two classes, got {k}", reason="validation"
        )
    if len(set(head.classes)) != k:
        raise LLMCallError(f"{where}: duplicate classes {head.classes}", reason="validation")
    if len(head.W) != LOCAL_ENCODER_DIM or any(len(row) != k for row in head.W):
        raise LLMCallError(
            f"{where}: W must be {LOCAL_ENCODER_DIM} x {k}, got "
            f"{len(head.W)} x {len(head.W[0]) if head.W else 0}",
            reason="validation",
        )
    if len(head.b) != k:
        raise LLMCallError(
            f"{where}: b must have {k} entries, got {len(head.b)}", reason="validation"
        )
    pinned = LOCAL_ENCODER_FILES[MODEL_FILE]
    if head.embedding_sha256 != pinned:
        raise LLMCallError(
            f"{where}: head was fit on embedding sha256 {head.embedding_sha256[:12]}, "
            f"the pinned model is {pinned[:12]}; refit the head",
            reason="validation",
        )
    return head


def load_head(path: Path) -> Head:
    """Read and validate one head file; uncached. Malformed -> ``validation``."""
    try:
        data = json.loads(Path(path).read_text())
        head = Head.from_dict(data)
    except (OSError, ValueError, TypeError) as e:
        raise LLMCallError(f"{path}: unreadable head file: {e}", reason="validation") from e
    return _validate_head(head, str(path))


_LOCK = threading.Lock()
"""Guards the one-time runtime build (Race 1); taken inside ``asyncio.to_thread``."""
_HEADS_LOCK = threading.Lock()
"""Guards the head cache; its own lock so ``served_head`` (event-loop thread, a small
JSON read) never waits behind a runtime build in progress on a worker thread."""
_RUNTIME: Runtime | None = None
_HEADS: dict[str, Head] = {}


def served_head(site: str) -> Head:
    """The serving head for ``site``, memoized per process. Missing -> ``transport``."""
    head = _HEADS.get(site)
    if head is not None:
        return head
    path = served_head_path(site)
    if not path.is_file():
        raise LLMCallError(
            f"{LEG} leg: no head for site {site} at agent/llm/backends/heads/{site}.json",
            reason="transport",
        )
    with _HEADS_LOCK:
        if site not in _HEADS:
            _HEADS[site] = load_head(path)
    return _HEADS[site]


# ---------------------------------------------------------------------------
# Runtime (the ONNX session and the tokenizer)
# ---------------------------------------------------------------------------


@dataclass
class Runtime:
    """The process-wide ONNX session and tokenizer; ``session.run`` is thread-safe."""

    session: Any
    tokenizer: Any


def _verified_paths(models_dir: Path) -> dict[str, Path]:
    """Every pinned file, present with its pinned sha256, else ``transport``."""
    paths: dict[str, Path] = {}
    for filename, expected in LOCAL_ENCODER_FILES.items():
        path = models_dir / filename
        if not path.is_file():
            raise LLMCallError(
                f"{LEG} leg: weights file {filename} missing under {models_dir}; "
                f"run {DOWNLOAD_SCRIPT}",
                reason="transport",
            )
        actual = sha256_file(path)
        if actual != expected:
            raise LLMCallError(
                f"{LEG} leg: weights file {filename} sha256 {actual[:12]} != pinned "
                f"{expected[:12]} under {models_dir}; run {DOWNLOAD_SCRIPT}",
                reason="transport",
            )
        paths[filename] = path
    return paths


def _build_runtime() -> Runtime:
    """Verify the weights, import the extra, build the session and tokenizer once.

    The weights are checked first so a machine with neither reports the
    precise file and the download script, not only the missing extra.
    """
    paths = _verified_paths(local_encoder_models_dir())
    try:
        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415
    except ImportError as e:
        raise LLMCallError(
            f"classification-local extra not installed ({e}); run uv sync --all-extras",
            reason="transport",
        ) from e

    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    session = ort.InferenceSession(
        str(paths[MODEL_FILE]), sess_options=options, providers=["CPUExecutionProvider"]
    )
    tokenizer = Tokenizer.from_file(str(paths[TOKENIZER_FILE]))
    tokenizer.enable_truncation(MAX_TOKENS)
    return Runtime(session=session, tokenizer=tokenizer)


def load_runtime() -> Runtime:
    """The memoized runtime; a burst of first calls builds it exactly once (Race 1).

    Runs inside ``asyncio.to_thread``, so the guard is a ``threading.Lock``.
    """
    global _RUNTIME
    runtime = _RUNTIME
    if runtime is not None:
        return runtime
    with _LOCK:
        if _RUNTIME is None:
            _RUNTIME = _build_runtime()
        return _RUNTIME


def _reset_for_tests() -> None:
    """Forget the memoized runtime and heads so a test starts from a fresh process."""
    global _RUNTIME
    with _LOCK:
        _RUNTIME = None
    with _HEADS_LOCK:
        _HEADS.clear()


# ---------------------------------------------------------------------------
# Embedding and scoring (sync, CPU-bound; called through ``asyncio.to_thread``)
# ---------------------------------------------------------------------------


def embed(text: str) -> np.ndarray:
    """The L2-normalized CLS vector for ``text`` (float32, ``(LOCAL_ENCODER_DIM,)``)."""
    if not text or not text.strip():
        raise LLMCallError(f"{LEG} leg: empty prompt", reason="validation")
    runtime = load_runtime()

    import numpy as np  # noqa: PLC0415

    try:
        encoding = runtime.tokenizer.encode(text)
        input_ids = np.array([encoding.ids], dtype=np.int64)
        feed = {
            "input_ids": input_ids,
            "attention_mask": np.array([encoding.attention_mask], dtype=np.int64),
            "token_type_ids": np.zeros_like(input_ids),
        }
        hidden = runtime.session.run(None, feed)[0][0]
    except Exception as e:
        logger.error("[agent.llm] %s leg transport: ONNX run failed: %s", LEG, e, exc_info=True)
        raise LLMCallError(f"{LEG} leg failed (transport): {e}", reason="transport") from e

    cls = np.asarray(hidden[0], dtype=np.float32)
    norm = float(np.linalg.norm(cls))
    return cls / norm if norm > 0.0 else cls


def scores(vector: Any, head: Head) -> dict[str, float]:
    """Softmax over ``vector @ W + b``, keyed by class."""
    import numpy as np  # noqa: PLC0415

    logits = np.asarray(vector, dtype=np.float64) @ np.asarray(head.W, dtype=np.float64)
    logits = logits + np.asarray(head.b, dtype=np.float64)
    logits = logits - logits.max()
    probs = np.exp(logits)
    probs = probs / probs.sum()
    return {cls: float(p) for cls, p in zip(head.classes, probs, strict=True)}


def _classify_sync(text: str, head: Head) -> dict[str, float]:
    return scores(embed(text), head)


# ---------------------------------------------------------------------------
# The output-type shape rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Shape:
    label_field: str
    values: dict[str, Any]
    """Rendered label -> the value the output type takes for it."""
    confidence: str | None


def _shape(output_type: type[BaseModel], head: Head) -> _Shape:
    """One closed-set field matching the head's classes; ``confidence`` optional; rest defaulted."""
    closed: list[tuple[str, dict[str, Any]]] = []
    confidence: str | None = None
    for name, info in output_type.model_fields.items():
        annotation = info.annotation
        if annotation is bool:
            closed.append((name, {"True": True, "False": False}))
        elif get_origin(annotation) is Literal:
            closed.append((name, {str(arg): arg for arg in get_args(annotation)}))
        elif name == "confidence" and annotation is float:
            confidence = name
        elif info.is_required():
            raise LLMCallError(
                f"{LEG} leg: {output_type.__name__}.{name} is required and not a closed set; "
                "every field besides the label and confidence needs a default",
                reason="validation",
            )
    if len(closed) != 1:
        raise LLMCallError(
            f"{LEG} leg: {output_type.__name__} must have exactly one bool or Literal field, "
            f"found {[name for name, _ in closed]}",
            reason="validation",
        )
    name, values = closed[0]
    if set(values) != set(head.classes):
        raise LLMCallError(
            f"{LEG} leg: {output_type.__name__}.{name} takes {sorted(values)} but the head "
            f"for {head.site} was fit on {sorted(head.classes)}",
            reason="validation",
        )
    return _Shape(label_field=name, values=values, confidence=confidence)


async def classify(text: str, output_type: type[BaseModel], head: Head) -> BaseModel:
    """Embed, score, and build ``output_type``; the shape rule runs before any ONNX work."""
    shape = _shape(output_type, head)
    probs = await asyncio.to_thread(_classify_sync, text, head)
    winner = max(probs, key=probs.__getitem__)
    kwargs: dict[str, Any] = {shape.label_field: shape.values[winner]}
    if shape.confidence is not None:
        kwargs[shape.confidence] = probs[winner]
    return output_type(**kwargs)


# ---------------------------------------------------------------------------
# The leg
# ---------------------------------------------------------------------------


async def call(
    prompt: str,
    output_type: type[BaseModel],
    route: Route,
    *,
    system: str | None,
    sdk_timeout: float,
    slot_timeout: float | None,
    max_retries: int | None,
    deadline: float | None = None,
    stack: LLMStack | None,
) -> BaseModel:
    """Classify ``prompt`` with the head for site ``route.model``.

    ``system``, ``slot_timeout``, ``max_retries`` and ``stack`` are part of
    the leg protocol and unused here (see the module docstring).
    """
    bound_to_deadline(sdk_timeout, deadline, monotonic(), leg=LEG)
    head = served_head(route.model)
    return await classify(prompt, output_type, head)
