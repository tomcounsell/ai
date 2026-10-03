"""The fallback judgement leg: an open-weight model on a second host, through
OpenRouter's chat completions endpoint.

Pinned to one model and one host at one quantization
(`settings.OPEN_WEIGHT_PIN`), with OpenRouter told not to route elsewhere:
a response from any other model or provider is malformed. The questions,
labels, and rubrics go in the system message; the inputs, as a JSON object,
are the user message and nothing else. The answer is a strict JSON schema:
a short `notes` string comparing the inputs with the rubrics, written first
so the probabilities follow from it and never kept, then, per question, one
number per label: the model's stated probability.
A generative model's stated probabilities are a weaker signal than a
decision endpoint's, which is why its floors are its own.

The kernel sends every input: its estimate (bytes / 3) is a metering worst
case, not the host's count. OpenRouter refuses an input over the pinned
endpoint's context itself: with fallbacks off it answers `404` with no
usage, its `error.message` naming the endpoint among those its "Filter by
Context Length" removed (an 842,333-byte body, probed 2026-10-03). That is
`input_too_large`, billed nothing.

Effect class `read`. One POST is one attempt. `usage.cost` is OpenRouter's
reported charge; the port charges the larger of it and the tokens at the
pinned price.
"""

import json
from collections.abc import Mapping

from core import judgement, spending
from core.judgement import DATA_ONLY, JudgementTask, LegAnswer, LegError
from core.settings import (
    OPEN_WEIGHT_MAX_COMPLETION,
    OPEN_WEIGHT_MODEL,
    OPEN_WEIGHT_PIN,
    OPEN_WEIGHT_PROVIDER,
    OPEN_WEIGHT_PROVIDER_NAME,
    settings,
)

SYSTEM = (
    "You answer closed questions about the inputs the user sends. First, in `notes`, compare the inputs "
    "with each label's rubric in a few short sentences, checking the rubric's exceptions. Then, for each "
    "question, give a probability for every label, from 0 to 1, summing to 1: nearly all of it on the "
    "label whose rubric fits, split only as far as you are genuinely unsure between rubrics."
)


class OpenWeight:
    name = "open_weight"
    effect_class = "read"
    fixed_text = SYSTEM + DATA_ONLY

    def __init__(self, url: str, key: str, *, timeout_s: float | None = None):
        self.endpoint = url
        self._key = key
        self.model = OPEN_WEIGHT_PIN
        self.timeout_s = timeout_s or settings.open_weight_timeout_s
        # No output limit is sent; the endpoint's own is the worst case.
        self.max_output_tokens = OPEN_WEIGHT_MAX_COMPLETION

    def body(self, task: JudgementTask, inputs: Mapping[str, str]) -> dict:
        lines = [SYSTEM, ""]
        for q in task.questions:
            lines.append(f"Question {q.id}: {q.text}")
            lines += [f"  - {label}: {rubric}" for label, rubric in q.labels.items()]
        lines += ["", DATA_ONLY]
        schema = {
            "type": "object",
            "properties": {
                "notes": {"type": "string"},
                **{
                    q.id: {
                        "type": "object",
                        "properties": {label: {"type": "number"} for label in q.labels},
                        "required": list(q.labels),
                        "additionalProperties": False,
                    }
                    for q in task.questions
                },
            },
            "required": ["notes", *(q.id for q in task.questions)],
            "additionalProperties": False,
        }
        return {
            "model": OPEN_WEIGHT_MODEL,
            "messages": [
                {"role": "system", "content": "\n".join(lines)},
                {"role": "user", "content": json.dumps(dict(inputs))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "judgement", "strict": True, "schema": schema},
            },
            "provider": {
                "order": [OPEN_WEIGHT_PROVIDER],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            "temperature": 0,
            "usage": {"include": True},
        }

    def estimate(self, task: JudgementTask, inputs: Mapping[str, str]) -> int:
        """Input tokens as bytes / `bytes_per_token` (2). Over its 35
        calibration calls of 2026-10-02 the host billed 0.39 to 0.62 of
        bytes / 3, and at most 219 of the 400 output tokens a call allows.
        Over the 50 governance calls of 2026-10-03 it billed up to 1.36 times
        bytes / 3 on hunks of lock-file hashes
        (`tests/fixtures/judgement_hash_dense.json`)."""
        return judgement.estimate_tokens(json.dumps(self.body(task, inputs)))

    async def ask(self, task: JudgementTask, inputs: Mapping[str, str]) -> LegAnswer | LegError:
        got = await judgement.post(self.endpoint, self._key, self.body(task, inputs), self.timeout_s)
        if isinstance(got, LegError):
            return got
        status, raw = got
        if status == 404 and _over_context(raw):
            return LegError(
                "input_too_large", "none", status=404, what="OpenRouter: over the endpoint's context"
            )
        if status != 200:
            return LegError("http_status", "none", status=status, what=f"HTTP {status}")
        return decode(task, raw)


def _over_context(raw: bytes) -> bool:
    """Whether an error body says the pinned endpoint was removed for its
    context length."""
    try:
        message = json.loads(raw)["error"]["message"]
    except ValueError, UnicodeDecodeError, KeyError, TypeError:
        return False
    if not isinstance(message, str):
        return False
    for step in message.split(";"):
        _, found, removed = step.partition("Filter by Context Length removed ")
        if found and OPEN_WEIGHT_PROVIDER in (e.strip(" .") for e in removed.split(",")):
            return True
    return False


def _usage(payload) -> dict | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    tokens, out, cost = usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost")
    for v in (tokens, out):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return None
    if cost is not None and spending.usd_micros(cost) is None:
        return None
    return {"input_tokens": tokens, "output_tokens": out, "reported_usd": None if cost is None else str(cost)}


def decode(task: JudgementTask, raw: bytes) -> LegAnswer | LegError:
    try:
        payload = json.loads(raw)
    except ValueError, UnicodeDecodeError:
        return LegError("malformed", "unknown", status=200, what="the body is not JSON")
    usage = _usage(payload)
    billed = "usage" if usage else "unknown"

    def bad(what: str) -> LegError:
        return LegError("malformed", billed, status=200, usage=usage, what=what)

    if not isinstance(payload, dict):
        return bad("the body is not an object")
    if payload.get("model") != OPEN_WEIGHT_MODEL or payload.get("provider") != OPEN_WEIGHT_PROVIDER_NAME:
        return bad("the answering model or host is not the pinned one")
    try:
        choice = payload["choices"][0]
        if choice.get("finish_reason") not in ("stop", None):
            return bad("the answer was cut off")
        content = json.loads(choice["message"]["content"])
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return bad("no JSON answer in the message")
    if not isinstance(content, dict):
        return bad("the answer is not an object")
    probabilities = {}
    for q in task.questions:
        probs = content.get(q.id)
        if not isinstance(probs, dict):
            return bad(f"no answer to {q.id}")
        probabilities[q.id] = dict(probs)
    return LegAnswer(probabilities, {q.id: None for q in task.questions}, usage, OPEN_WEIGHT_PIN)
