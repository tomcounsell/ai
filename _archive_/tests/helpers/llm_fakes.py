"""The one ``run_typed`` fake every migrated classification site's tests use (#3410, Risk 6).

A migrated site calls ``agent.llm.run_typed`` through a module-scope import,
so its tests patch ``<module>.run_typed`` with a :class:`FakeRunTyped`. The
fake records every call (prompt, output type, and every keyword the site
passed, ``task`` and ``project_key`` included), answers with a configured
instance of the site's output type, or raises the :class:`LLMCallError`
the site's fail-safe must absorb. One fake rather than one per file keeps
every test on the wrapper's real contract: a call that reaches no fake is
a test that reaches no leg.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from agent.llm import LLMCallError
from agent.llm.errors import Reason


@dataclass
class RecordedCall:
    """One ``run_typed`` invocation as the fake saw it."""

    prompt: str
    output_type: type[BaseModel]
    kwargs: dict[str, Any]

    @property
    def task(self):
        return self.kwargs.get("task")

    @property
    def project_key(self):
        return self.kwargs.get("project_key")


Responder = Callable[[RecordedCall], BaseModel | Awaitable[BaseModel]]


@dataclass
class FakeRunTyped:
    """A ``run_typed`` stand-in.

    ``result`` is returned as-is (it must already be an instance of the
    site's output type). ``error`` is raised instead when set. ``responder``
    takes precedence over both and is called with the :class:`RecordedCall`,
    so a test can answer per prompt or raise mid-sequence. ``on_call`` runs
    before the answer is produced, for tests that need a side effect (a
    contextvar the real leg would set, a clock advance).
    """

    result: BaseModel | None = None
    error: LLMCallError | None = None
    responder: Responder | None = None
    on_call: Callable[[RecordedCall], None] | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    async def __call__(self, prompt: str, output_type: type[BaseModel], **kwargs: Any) -> BaseModel:
        call = RecordedCall(prompt=prompt, output_type=output_type, kwargs=dict(kwargs))
        self.calls.append(call)
        if self.on_call is not None:
            self.on_call(call)
        if self.responder is not None:
            answer = self.responder(call)
            if hasattr(answer, "__await__"):
                answer = await answer
            return answer
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("FakeRunTyped has neither a result, an error nor a responder")
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last(self) -> RecordedCall:
        return self.calls[-1]


def failing(reason: Reason = "transport", message: str = "fake leg failure") -> FakeRunTyped:
    """A fake whose every call raises ``LLMCallError(reason=reason)``."""
    return FakeRunTyped(error=LLMCallError(message, reason=reason))


# ---------------------------------------------------------------------------
# The local encoder leg's runtime fake (#3420)
# ---------------------------------------------------------------------------


@dataclass
class _FakeEncoding:
    ids: list[int]
    attention_mask: list[int]


class _FakeTokenizer:
    """Whitespace tokenizer honoring ``enable_truncation`` like the real one."""

    def __init__(self, max_length: int) -> None:
        self.max_length = max_length
        self.encoded: list[list[int]] = []

    def enable_truncation(self, max_length: int, **_: Any) -> None:
        self.max_length = max_length

    def encode(self, text: str) -> _FakeEncoding:
        ids = list(range(1, len(text.split()) + 1))[: self.max_length]
        self.encoded.append(ids)
        return _FakeEncoding(ids=ids, attention_mask=[1] * len(ids))


class _FakeSession:
    """Stands in for ``onnxruntime.InferenceSession``: counts runs, returns the scripted CLS."""

    def __init__(self, owner: FakeEncoderRuntime) -> None:
        self._owner = owner

    def run(self, output_names, feed):
        import numpy as np  # noqa: PLC0415

        self._owner.runs += 1
        self._owner.feeds.append(feed)
        if self._owner.error is not None:
            raise self._owner.error
        seq_len = int(feed["input_ids"].shape[1])
        hidden = np.zeros((1, seq_len, len(self._owner.vector)), dtype=np.float32)
        hidden[0, 0, :] = np.asarray(self._owner.vector, dtype=np.float32)
        return [hidden]


@dataclass
class FakeEncoderRuntime:
    """A stand-in for ``agent.llm.backends.local_encoder.load_runtime()``.

    Install with ``monkeypatch.setattr(local_encoder, "load_runtime", lambda:
    fake)``. ``vector`` is the CLS vector the fake session emits (384 wide by
    default: a unit vector on axis 0, so the leg's L2 normalization is a
    no-op and a head's scores are ``W[0] + b``); ``error`` makes every run
    raise; ``runs`` counts ``session.run`` calls (the shape-rule tests assert
    it stays 0); ``feeds`` keeps every feed dict; the tokenizer truncates at
    512 ids like the configured real one.
    """

    vector: list[float] = field(default_factory=lambda: [1.0] + [0.0] * 383)
    error: Exception | None = None
    runs: int = 0
    feeds: list[dict] = field(default_factory=list)
    tokenizer: _FakeTokenizer = field(default_factory=lambda: _FakeTokenizer(max_length=512))

    def __post_init__(self) -> None:
        self.session = _FakeSession(self)

    @property
    def call_count(self) -> int:
        return self.runs
