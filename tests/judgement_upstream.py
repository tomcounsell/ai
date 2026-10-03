"""A real local HTTP upstream speaking Jev's and OpenRouter's wire formats, for
the judgement tests and the emulator's forced arms.

Its 200 bodies have the shape of the responses recorded live in
`tests/fixtures/judgement_jev.json` and `judgement_open_weight.json`
(`record_judgement.py`), with the answers and usage put in per request. It
runs on its own thread and event loop, so synchronous tests and each
`asyncio.run` share one server. It records every request it gets.

Two kinds of path, each ending in the leg (`jev` or `open_weight`):

- `/fixed/<answer>/<leg>`: every question answered with 0.95 on one label:
  `precise` or `thin` for the request judge (thin is `one_line_ask`),
  `true` or `false` for a boolean. The emulator's forced arms use this.
- `/s/<script>/<leg>`: the next reply a test queued for that script (or its
  default): `{"probs": {question: {label: p}}}`, or `{"status": 500}` (with `"headers"`, sent with it),
  `{"delay": seconds}`, `{"body": "raw text"}`, `{"model": "other"}`,
  `{"provider": "other"}`, `{"usage": None}`, `{"cost": 0.001}`,
  `{"drop_label": "label"}`, `{"choice": "label"}`, and `{"by_path": {path:
  probs}}` and `{"fail_paths": [path]}` (answers by the `path` input, for
  concurrent per-hunk calls), combinable.

    python -m tests.judgement_upstream --answer thin [--port 0]

prints the two leg URLs, for `VALOR_JEV_URL` and `VALOR_OPEN_WEIGHT_URL`,
and serves until killed.
"""

import argparse
import asyncio
import itertools
import json
import math
import threading
from pathlib import Path
from typing import Any

from aiohttp import web

from core.judgement import LOOPBACK_KEY, JudgementPort
from core.settings import JEV_MODEL, OPEN_WEIGHT_MODEL, OPEN_WEIGHT_PROVIDER_NAME

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED = {"precise": "precise", "thin": "one_line_ask", "true": "true", "false": "false"}


def _template(name: str, fallback: dict) -> dict:
    path = FIXTURES / name
    return json.loads(path.read_text()) if path.exists() else fallback


JEV_TEMPLATE = _template(
    "judgement_jev.json",
    {"model": JEV_MODEL, "answers": {}, "usage": {"input_tokens": 0, "output_tokens": 0}},
)
OW_TEMPLATE = _template(
    "judgement_open_weight.json",
    {
        "model": OPEN_WEIGHT_MODEL,
        "provider": OPEN_WEIGHT_PROVIDER_NAME,
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0},
    },
)


def questions_of(leg: str, body: dict) -> dict[str, list[str]]:
    """Each question's labels, read from the request."""
    if leg == "jev":
        out = {}
        for qid, q in body["questions"].items():
            out[qid] = list(q["criteria"]) if q["type"] == "choice" else ["true", "false"]
        return out
    schema = body["response_format"]["json_schema"]["schema"]
    return {
        qid: list(spec["properties"])
        for qid, spec in schema["properties"].items()
        if spec["type"] == "object"
    }


def fixed_probs(answer: str, labels: list[str]) -> dict[str, float]:
    pick = FIXED.get(answer, answer)
    if pick not in labels:
        pick = labels[-1]
    rest = [label for label in labels if label != pick]
    return {label: (0.95 if label == pick else round(0.05 / len(rest), 6)) for label in labels}


class Upstream:
    def __init__(self, port: int = 0):
        self.requests: list[dict[str, Any]] = []
        self.scripts: dict[str, list[dict]] = {}
        self.defaults: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self._ready = threading.Event()
        self._port = port
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._serve, daemon=True).start()
        self._ready.wait()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_post("/fixed/{answer}/{leg}", self.handle)
        app.router.add_post("/s/{script}/{leg}", self.handle)
        runner = web.AppRunner(app)
        self.loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", self._port)
        self.loop.run_until_complete(site.start())
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self._ready.set()
        self.loop.run_forever()

    # -- steering ----------------------------------------------------------------

    def script(self, *replies: dict, default: dict | None = None) -> str:
        """A new script: these replies in order, then `default` (or 0.95 on
        the first label) for every later request."""
        sid = f"s{next(self._ids)}"
        self.scripts[sid] = list(replies)
        self.defaults[sid] = default or {}
        return sid

    def urls(self, *, script: str | None = None, fixed: str | None = None) -> tuple[str, str]:
        base = f"{self.url}/s/{script}" if script else f"{self.url}/fixed/{fixed}"
        return f"{base}/jev", f"{base}/open_weight"

    def port(self, *, script: str | None = None, fixed: str | None = None, timeouts: tuple = (5, 5)):
        from tools.jev import Jev
        from tools.open_weight import OpenWeight

        jev, ow = self.urls(script=script, fixed=fixed)
        return JudgementPort(
            {
                "jev": Jev(jev, LOOPBACK_KEY, timeout_s=timeouts[0]),
                "open_weight": OpenWeight(ow, LOOPBACK_KEY, timeout_s=timeouts[1]),
            }
        )

    def seen(self, script: str) -> list[dict]:
        return [r for r in self.requests if r["script"] == script]

    # -- serving -----------------------------------------------------------------

    async def handle(self, request: web.Request) -> web.Response:
        leg = request.match_info["leg"]
        raw = await request.read()
        body = json.loads(raw)
        script = request.match_info.get("script")
        self.requests.append(
            {
                "script": script,
                "leg": leg,
                "body": body,
                "authorization": request.headers.get("Authorization"),
            }
        )
        if script is not None:
            queue = self.scripts.get(script, [])
            spec = queue.pop(0) if queue else dict(self.defaults.get(script, {}))
        else:
            spec = {"fixed": request.match_info["answer"]}
        if spec.get("delay"):
            await asyncio.sleep(spec["delay"])
        inputs = body["state"] if leg == "jev" else json.loads(body["messages"][1]["content"])
        if inputs.get("path") is not None and inputs.get("path") in spec.get("fail_paths", ()):
            return web.Response(status=503, text="down")
        if "status" in spec:
            return web.Response(
                status=spec["status"],
                text=spec.get("text", '{"detail": {"message": "nope"}}'),
                headers=spec.get("headers"),
            )
        if "body" in spec:
            return web.Response(status=200, text=spec["body"], content_type="application/json")
        labels = questions_of(leg, body)
        probs = spec.get("probs") or {}
        if spec.get("by_path"):
            probs = spec["by_path"].get(inputs.get("path"), probs)
        answers = {}
        for qid, ls in labels.items():
            p = probs.get(qid) or fixed_probs(spec.get("fixed", ls[0]), ls)
            if spec.get("drop_label"):
                p = {k: v for k, v in p.items() if k != spec["drop_label"]}
            answers[qid] = p
        # Billed tokens as the providers bill them, measured on the calibration
        # calls of 2026-10-02: Jev more than the body's bytes / 3 (its own
        # prompt around the request), the fallback's host about half of it.
        if leg == "jev":
            tokens = math.ceil(len(raw) / 3 * 1.2) + 190
        else:
            tokens = math.ceil(len(raw) / 3 * 0.6)
        if leg == "jev":
            out = json.loads(json.dumps(JEV_TEMPLATE))
            out["model"] = spec.get("model", JEV_MODEL)
            out["answers"] = {}
            for qid, p in answers.items():
                if set(labels[qid]) == {"true", "false"}:
                    out["answers"][qid] = {"type": "noul", "noul": p.get("true")}
                else:
                    pick = spec.get("choice") or max(p, key=p.get)
                    out["answers"][qid] = {
                        "type": "choice",
                        "choice": pick,
                        "probabilities": p,
                        "confidence": 0.5,
                    }
            out["usage"] = {"input_tokens": tokens, "output_tokens": 12}
        else:
            out = json.loads(json.dumps(OW_TEMPLATE))
            out["model"] = spec.get("model", OPEN_WEIGHT_MODEL)
            out["provider"] = spec.get("provider", OPEN_WEIGHT_PROVIDER_NAME)
            out["choices"][0]["message"]["content"] = json.dumps({"notes": "compared", **answers})
            out["usage"] = {
                "prompt_tokens": tokens,
                "completion_tokens": 30,
                "cost": spec.get("cost", round(tokens * 0.14e-6 + 30 * 0.8e-6, 10)),
            }
        if "usage" in spec and spec["usage"] is None:
            out.pop("usage", None)
        return web.json_response(out)


_SHARED: Upstream | None = None


def shared() -> Upstream:
    global _SHARED
    if _SHARED is None:
        _SHARED = Upstream()
    return _SHARED


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--answer", required=True, choices=["precise", "thin", "true", "false"])
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    up = Upstream(args.port)
    jev, ow = up.urls(fixed=args.answer)
    print(json.dumps({"VALOR_JEV_URL": jev, "VALOR_OPEN_WEIGHT_URL": ow}), flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
