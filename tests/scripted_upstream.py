"""A scripted model provider on loopback: the upstream the gateway forwards
to, standing in for Anthropic and OpenAI so a real harness runs a whole turn
at no cost.

One HTTP server speaks Anthropic Messages (`POST /v1/messages`) and OpenAI
Responses (`POST /v1/responses`, also under the gateway's `/openai` prefix),
both as server-sent events whose event shapes follow the recorded provider
traffic (`tests/fixtures/`). Each model call pops the next reply of the
script: `Say` (a text reply), `Run` (a call of the shell tool), `Hang` (the
start of a text reply, then nothing for a long time), `Task` (a call of the
subagent tool). An empty script answers `Say("done")`. Every request body,
its path, and the headers it arrived with are kept in `requests`.

Usage numbers are made up but consistent: input tokens are the body's
bytes over four, output tokens the reply's characters over four.
"""

import asyncio
import json
from dataclasses import dataclass, field

from aiohttp import web


@dataclass
class Say:
    text: str


@dataclass
class Run:
    """A call of the shell tool: Claude Code's `Bash`, Pi's `bash`."""

    command: str


@dataclass
class Hang:
    """Part of a text reply, then a stall of `seconds` inside the stream."""

    seconds: float = 120
    text: str = "working"


@dataclass
class Task:
    """A call of Claude Code's `Task` tool: a subagent."""

    prompt: str
    description: str = "subagent"
    subagent_type: str = "general-purpose"


@dataclass
class Request:
    route: str  # "anthropic" or "openai"
    path: str
    headers: dict[str, str]
    body: dict
    reply: object = None

    @property
    def system_text(self) -> str:
        """Everything the request told the model as its system prompt (and,
        for OpenAI, its `instructions` or a system or developer message)."""
        body = self.body
        out = []
        system = body.get("system")
        if isinstance(system, str):
            out.append(system)
        elif isinstance(system, list):
            out += [b.get("text", "") for b in system if isinstance(b, dict)]
        if isinstance(body.get("instructions"), str):
            out.append(body["instructions"])
        for m in body.get("input") or []:
            if isinstance(m, dict) and m.get("role") in ("system", "developer"):
                out.append(_flatten(m.get("content")))
        return "\n".join(out)

    @property
    def text(self) -> str:
        """The request body whole, as text: what the model was sent."""
        return json.dumps(self.body, ensure_ascii=False)


def _flatten(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _tokens(n: int) -> int:
    return max(1, n // 4)


@dataclass
class ScriptedUpstream:
    script: list = field(default_factory=list)
    requests: list[Request] = field(default_factory=list)
    # Which Anthropic requests the script answers: Claude Code makes side
    # calls (a title, a quota probe) the script must not be eaten by.
    answers: callable = lambda body: True
    # Input tokens to report instead of the body's own count, for the next
    # replies in order (a harness compacts on the usage it is told).
    report_input: list = field(default_factory=list)
    url: str = ""
    _runner: web.AppRunner | None = None
    _ids: int = 0

    async def start(self) -> ScriptedUpstream:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        for prefix in ("", "/openai"):
            app.router.add_post(f"{prefix}/v1/messages", self._messages)
            app.router.add_post(f"{prefix}/v1/responses", self._responses)
        app.router.add_post("/v1/messages/count_tokens", self._count)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    def next_reply(self, body: dict, route: str):
        if route == "anthropic" and not self.answers(body):
            return Say("ok")
        return self.script.pop(0) if self.script else Say("done")

    async def _count(self, request: web.Request) -> web.Response:
        await request.read()
        return web.json_response({"input_tokens": 10})

    # -- Anthropic Messages ---------------------------------------------------------------

    async def _messages(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        reply = self.next_reply(body, "anthropic")
        self.requests.append(Request("anthropic", request.path, dict(request.headers), body, reply))
        self._ids += 1
        model, ident = body.get("model", "claude-opus-5-5"), f"msg_{self._ids:04d}"
        used = self.report_input.pop(0) if self.report_input else _tokens(len(json.dumps(body)))
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        usage = {"input_tokens": used, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        await response.write(
            _sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": ident,
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {**usage, "output_tokens": 1},
                    },
                },
            )
        )
        stop, out = "end_turn", 0
        if isinstance(reply, (Say, Hang)):
            await response.write(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            text = reply.text
            await response.write(
                _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": text},
                    },
                )
            )
            out = _tokens(len(text))
            if isinstance(reply, Hang):
                await asyncio.sleep(reply.seconds)
            await response.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        else:
            if isinstance(reply, Run):
                name, args = "Bash", {"command": reply.command, "description": "run"}
            else:
                name, args = "Task", {
                    "description": reply.description,
                    "prompt": reply.prompt,
                    "subagent_type": reply.subagent_type,
                }  # fmt: skip
            await response.write(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": f"toolu_{self._ids:04d}",
                            "name": name,
                            "input": {},
                        },
                    },
                )
            )
            partial = json.dumps(args)
            await response.write(
                _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "input_json_delta", "partial_json": partial},
                    },
                )
            )
            await response.write(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
            stop, out = "tool_use", _tokens(len(partial))
        await response.write(
            _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {**usage, "output_tokens": out},
                },
            )
        )
        await response.write(_sse("message_stop", {"type": "message_stop"}))
        await response.write_eof()
        return response

    # -- OpenAI Responses -----------------------------------------------------------------

    async def _responses(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        reply = self.next_reply(body, "openai")
        self.requests.append(Request("openai", request.path, dict(request.headers), body, reply))
        self._ids += 1
        model, ident = body.get("model", "gpt-6.1-sol"), f"resp_{self._ids:04d}"
        used = self.report_input.pop(0) if self.report_input else _tokens(len(json.dumps(body)))
        base = {"id": ident, "object": "response", "model": model, "created_at": 1760000000}
        seq = iter(range(1000))
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)

        async def send(kind: str, **data):
            await response.write(_sse(kind, {"type": kind, "sequence_number": next(seq), **data}))

        await send("response.created", response={**base, "status": "in_progress", "output": []})
        await send("response.in_progress", response={**base, "status": "in_progress", "output": []})
        if isinstance(reply, (Say, Hang)):
            item = {"type": "message", "id": f"msg_{self._ids:04d}", "role": "assistant"}
            await send(
                "response.output_item.added",
                output_index=0,
                item={**item, "status": "in_progress", "content": []},
            )
            await send(
                "response.content_part.added",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                part={"type": "output_text", "text": "", "annotations": []},
            )
            await send(
                "response.output_text.delta",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                delta=reply.text,
            )
            if isinstance(reply, Hang):
                await asyncio.sleep(reply.seconds)
            part = {"type": "output_text", "text": reply.text, "annotations": []}
            await send(
                "response.output_text.done",
                item_id=item["id"],
                output_index=0,
                content_index=0,
                text=reply.text,
            )
            await send(
                "response.content_part.done", item_id=item["id"], output_index=0, content_index=0, part=part
            )
            done = {**item, "status": "completed", "content": [part]}
            out = _tokens(len(reply.text))
        else:
            if isinstance(reply, Run):
                name, args = "bash", json.dumps({"command": reply.command})
            else:
                raise TypeError("Pi starts no subagents")
            item = {
                "type": "function_call",
                "id": f"fc_{self._ids:04d}",
                "call_id": f"call_{self._ids:04d}",
                "name": name,
            }
            await send(
                "response.output_item.added",
                output_index=0,
                item={**item, "arguments": "", "status": "in_progress"},
            )
            await send(
                "response.function_call_arguments.delta", item_id=item["id"], output_index=0, delta=args
            )
            await send(
                "response.function_call_arguments.done", item_id=item["id"], output_index=0, arguments=args
            )
            done = {**item, "arguments": args, "status": "completed"}
            out = _tokens(len(args))
        await send("response.output_item.done", output_index=0, item=done)
        usage = {
            "input_tokens": used,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": out,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": used + out,
        }
        await send(
            "response.completed", response={**base, "status": "completed", "output": [done], "usage": usage}
        )
        await response.write_eof()
        return response
