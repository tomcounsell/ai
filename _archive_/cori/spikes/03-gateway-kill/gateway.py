"""Minimal LLM gateway: FastAPI + httpx streaming proxy in front of the
Anthropic Messages API, with a tiny in-memory kernel.

- Per-Brief bearer tokens issued by the kernel; the provider key never leaves
  this process.
- Budget check before forward (token budget per brief; input estimated with
  the provider's count_tokens endpoint, output charged from stream usage).
- Request, response, and usage appended to a JSONL audit log.
- revoke(token) is the kill: an in-flight stream is cut at the next chunk and
  every later call is refused with 403.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

UPSTREAM = "https://api.anthropic.com"
AUDIT_PATH = os.environ.get(
    "CORI_AUDIT_LOG", os.path.join(os.path.dirname(__file__), "audit.jsonl")
)


@dataclass
class Brief:
    brief_id: str
    token: str
    token_budget: int
    tokens_used: int = 0
    revoked_at: float | None = None
    calls: int = 0
    chunks: int = 0  # chunks streamed on the current call
    refused: int = 0


@dataclass
class Kernel:
    provider_key: str
    briefs: dict[str, Brief] = field(default_factory=dict)  # by token

    def issue(self, brief_id: str, token_budget: int) -> str:
        tok = "brief_" + secrets.token_urlsafe(18)
        self.briefs[tok] = Brief(brief_id, tok, token_budget)
        return tok

    def revoke(self, brief_id: str) -> float:
        t = time.perf_counter()
        for b in self.briefs.values():
            if b.brief_id == brief_id and b.revoked_at is None:
                b.revoked_at = t
        return t

    def by_brief(self, brief_id: str) -> Brief | None:
        return next((b for b in self.briefs.values() if b.brief_id == brief_id), None)


def audit(rec: dict) -> None:
    rec = {"ts": time.time(), "t": time.perf_counter(), **rec}
    with open(AUDIT_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


def build_app(kernel: Kernel) -> FastAPI:
    app = FastAPI()
    client = httpx.AsyncClient(base_url=UPSTREAM, timeout=120)

    def token_of(req: Request) -> str | None:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:]
        return req.headers.get("x-api-key")

    @app.post("/kernel/issue")
    async def issue(req: Request):
        body = await req.json()
        return {"token": kernel.issue(body["brief_id"], body["token_budget"])}

    @app.post("/kernel/revoke/{brief_id}")
    async def revoke(brief_id: str):
        t = kernel.revoke(brief_id)
        audit({"event": "revoke", "brief": brief_id})
        return {"revoked_at": t}

    @app.get("/kernel/status/{brief_id}")
    async def status(brief_id: str):
        b = kernel.by_brief(brief_id)
        return (
            {} if b is None else {k: v for k, v in b.__dict__.items() if k != "token"}
        )

    async def count_input(body: dict) -> int:
        payload = {
            k: body[k] for k in ("model", "system", "messages", "tools") if k in body
        }
        r = await client.post(
            "/v1/messages/count_tokens",
            json=payload,
            headers={
                "x-api-key": kernel.provider_key,
                "anthropic-version": "2023-06-01",
            },
        )
        r.raise_for_status()
        return int(r.json()["input_tokens"])

    @app.post("/v1/messages")
    async def messages(req: Request):
        tok = token_of(req)
        b = kernel.briefs.get(tok or "")
        t_in = time.perf_counter()
        if b is None:
            audit({"event": "refused", "reason": "unknown_token"})
            return JSONResponse({"error": {"type": "authentication_error"}}, 401)
        if b.revoked_at is not None:
            b.refused += 1
            audit(
                {
                    "event": "refused",
                    "brief": b.brief_id,
                    "reason": "revoked",
                    "since_revoke_ms": (t_in - b.revoked_at) * 1000,
                }
            )
            return JSONResponse(
                {"error": {"type": "permission_error", "message": "brief revoked"}}, 403
            )
        body = await req.json()
        t0 = time.perf_counter()
        est_in = await count_input(body)
        count_ms = (time.perf_counter() - t0) * 1000
        max_out = int(body.get("max_tokens", 0))
        if b.tokens_used + est_in + max_out > b.token_budget:
            b.refused += 1
            audit(
                {
                    "event": "refused",
                    "brief": b.brief_id,
                    "reason": "budget",
                    "used": b.tokens_used,
                    "estimated_input": est_in,
                    "max_tokens": max_out,
                    "budget": b.token_budget,
                }
            )
            return JSONResponse(
                {"error": {"type": "permission_error", "message": "budget exceeded"}},
                403,
            )
        b.calls += 1
        b.chunks = 0
        call = b.calls
        audit(
            {
                "event": "request",
                "brief": b.brief_id,
                "call": call,
                "estimated_input": est_in,
                "count_tokens_ms": count_ms,
                "body": body,
            }
        )
        headers = {
            "x-api-key": kernel.provider_key,
            "anthropic-version": req.headers.get("anthropic-version", "2023-06-01"),
            "content-type": "application/json",
        }
        if "anthropic-beta" in req.headers:
            headers["anthropic-beta"] = req.headers["anthropic-beta"]
        body["stream"] = True

        async def gen():
            usage = {}
            blocks: list[dict] = []
            stop_reason = None
            cut = False
            buf = ""
            upstream = client.stream("POST", "/v1/messages", json=body, headers=headers)
            async with upstream as r:
                if r.status_code != 200:
                    err = await r.aread()
                    audit(
                        {
                            "event": "upstream_error",
                            "brief": b.brief_id,
                            "call": call,
                            "status": r.status_code,
                            "body": err.decode(),
                        }
                    )
                    yield err
                    return
                async for chunk in r.aiter_bytes():
                    if b.revoked_at is not None:
                        cut = True
                        audit(
                            {
                                "event": "stream_cut",
                                "brief": b.brief_id,
                                "call": call,
                                "since_revoke_ms": (time.perf_counter() - b.revoked_at)
                                * 1000,
                                "chunks_before_cut": b.chunks,
                            }
                        )
                        yield b'event: error\ndata: {"type":"error","error":{"type":"permission_error","message":"brief revoked"}}\n\n'
                        break
                    b.chunks += 1
                    yield chunk
                    buf += chunk.decode()
                    while "\n\n" in buf:
                        evt, buf = buf.split("\n\n", 1)
                        for line in evt.splitlines():
                            if line.startswith("data: "):
                                d = json.loads(line[6:])
                                typ = d.get("type")
                                if typ == "message_start":
                                    usage.update(d["message"]["usage"])
                                elif typ == "content_block_start":
                                    cb = dict(d["content_block"])
                                    if cb["type"] == "tool_use":
                                        cb["_json"] = ""
                                    blocks.append(cb)
                                elif typ == "content_block_delta":
                                    delta = d["delta"]
                                    if delta["type"] == "text_delta":
                                        blocks[d["index"]]["text"] += delta["text"]
                                    elif delta["type"] == "input_json_delta":
                                        blocks[d["index"]]["_json"] += delta[
                                            "partial_json"
                                        ]
                                elif typ == "message_delta":
                                    usage.update(d.get("usage", {}))
                                    stop_reason = d["delta"].get("stop_reason")
            for cb in blocks:
                if cb.get("type") == "tool_use":
                    cb["input"] = json.loads(cb.pop("_json") or "{}")
            billed = (
                usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("output_tokens", 0)
            )
            b.tokens_used += billed
            audit(
                {
                    "event": "response",
                    "brief": b.brief_id,
                    "call": call,
                    "cut": cut,
                    "stop_reason": stop_reason,
                    "usage": usage,
                    "billed_total": billed,
                    "content": blocks,
                    "latency_ms": (time.perf_counter() - t_in) * 1000,
                }
            )

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
