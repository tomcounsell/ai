"""The fallback judgement leg: Google's Decisions API, `gemini-decision-1`.

`POST {model, input: [{type: "text", text}], questions}` with the key in
`x-goog-api-key`; the answer is `{answers: {id: {type: "noul", noul,
confidence} | {type: "choice", choice, probabilities, confidence}}, usage:
{total_input_tokens, total_output_tokens, ...}}`, naming no model (probed
2026-11-16, `tests/fixtures/judgement_gemini.json`;
https://aistudio.google.com/docs/decisions-api). The API is beta, on
`v1beta`. Every question of a task goes in one call; the inputs, as one JSON
object, are the one text block, and the question text and rubrics are the
only instructions.

Its limit is 262,144 input tokens. The kernel sends every input, and Google
refuses one over it with `400 {"error": {"message": "The input token count
exceeds the maximum number of tokens allowed 262144.", "code":
"invalid_request"}}` (probed 2026-11-16): `input_too_large`, billed nothing.

Effect class `read`: it asks a question and changes nothing. One POST is one
attempt. The port asks it beside Jev on every judgement
(`core/judgement.py`).
"""

import json
import math
from collections.abc import Mapping

from core import judgement
from core.judgement import DATA_ONLY, JudgementTask, Kind, LegAnswer, LegError
from core.settings import GEMINI_DECISION_MODEL, settings

# Jev's estimate (`tools.jev.Jev.estimate`), not yet fitted to Google's
# count: the probe of 2026-11-16 billed 235 tokens on an estimate of about 800.
OVERHEAD_RATIO = 1.25
OVERHEAD_FIXED = 300
OVERHEAD_PER_QUESTION = 50
TOO_LARGE = "The input token count exceeds"


class GeminiDecisions:
    name = "gemini"
    effect_class = "read"
    fixed_text = DATA_ONLY

    def __init__(self, url: str, key: str, *, timeout_s: float | None = None):
        self.endpoint = url
        self._key = key
        self.model = GEMINI_DECISION_MODEL
        self.timeout_s = timeout_s or settings.gemini_timeout_s
        self.max_output_tokens = 0  # "output token usage is always 0"

    def body(self, task: JudgementTask, inputs: Mapping[str, str]) -> dict:
        questions = {}
        for q in task.questions:
            if q.kind in (Kind.CHOICE, Kind.PICK):
                criteria = dict(q.labels)
                kind = "choice"
            else:
                criteria = {"true_description": q.labels["true"], "false_description": q.labels["false"]}
                kind = "noul"
            questions[q.id] = {"type": kind, "instructions": f"{q.text}\n\n{DATA_ONLY}", "criteria": criteria}
        return {
            "model": self.model,
            "input": [{"type": "text", "text": json.dumps(dict(inputs))}],
            "questions": questions,
        }

    def estimate(self, task: JudgementTask, inputs: Mapping[str, str]) -> int:
        body = judgement.estimate_tokens(json.dumps(self.body(task, inputs)))
        return math.ceil(body * OVERHEAD_RATIO) + OVERHEAD_FIXED + OVERHEAD_PER_QUESTION * len(task.questions)

    async def ask(self, task: JudgementTask, inputs: Mapping[str, str]) -> LegAnswer | LegError:
        headers = {"x-goog-api-key": self._key}
        got = await judgement.post(self.endpoint, headers, self.body(task, inputs), self.timeout_s)
        if isinstance(got, LegError):
            return got
        status, raw = got
        if status == 400 and _too_large(raw):
            return LegError("input_too_large", "none", status=400, what="Gemini: over the input token limit")
        if status != 200:
            return LegError("http_status", "none", status=status, what=f"HTTP {status}")
        return decode(task, raw)


def _too_large(raw: bytes) -> bool:
    try:
        message = json.loads(raw)["error"]["message"]
    except ValueError, UnicodeDecodeError, KeyError, TypeError:
        return False
    return isinstance(message, str) and message.startswith(TOO_LARGE)


def _usage(payload) -> dict | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    tokens, out = usage.get("total_input_tokens"), usage.get("total_output_tokens", 0)
    for v in (tokens, out):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return None
    return {"input_tokens": tokens, "output_tokens": out, "reported_usd": None}


def decode(task: JudgementTask, raw: bytes) -> LegAnswer | LegError:
    """A 200 body to an answer; total: anything unreadable is `malformed`,
    charged by its usage when the body has one."""
    try:
        payload = json.loads(raw)
    except ValueError, UnicodeDecodeError:
        return LegError("malformed", "unknown", status=200, what="the body is not JSON")
    usage = _usage(payload)
    billed = "usage" if usage else "unknown"

    def bad(what: str) -> LegError:
        return LegError("malformed", billed, status=200, usage=usage, what=what)

    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        return bad("no answers")
    probabilities: dict[str, dict] = {}
    choices: dict[str, str | None] = {}
    for q in task.questions:
        a = payload["answers"].get(q.id)
        if not isinstance(a, dict):
            return bad(f"no answer to {q.id}")
        if q.kind in (Kind.CHOICE, Kind.PICK):
            probs = a.get("probabilities")
            if not isinstance(probs, dict):
                return bad(f"no probabilities for {q.id}")
            probabilities[q.id] = dict(probs)
            choice = a.get("choice")
            choices[q.id] = choice if isinstance(choice, str) and choice in q.labels else None
        else:
            p = a.get("noul")
            if isinstance(p, bool) or not isinstance(p, int | float) or not 0 <= p <= 1:
                return bad(f"no probability for {q.id}")
            probabilities[q.id] = {"true": float(p), "false": 1.0 - float(p)}
            choices[q.id] = None
    return LegAnswer(probabilities, choices, usage, GEMINI_DECISION_MODEL)
