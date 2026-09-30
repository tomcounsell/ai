"""The smallest PydanticAI loop with the five tools of tech stack §5.

read, write, edit, and bash are bridged to the Sandbox port and every
invocation is written to the tool log before it runs and after it returns,
with exit status and hashes of output and artifact. `ask` emits a question
trace event and blocks the worker until answer() arrives; abort() during the
wait ends the run with a terminal event and nothing else.

Every model request goes through the gateway by base URL and streams, so
the gateway's stream-cut kill from spike 03 still applies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent, CallToolsNode, ModelRequestNode, RunContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_graph import End

from sandbox import Sandbox


def sha(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


class Report(BaseModel):
    """The terminal TraceEvent carries the Report (tech stack §5)."""

    summary: str
    artifacts: list[str]


class ToolLog:
    """JSONL, one record per line, written by the kernel process."""

    def __init__(self, path: Path, brief_id: str):
        self.path = path
        self.brief_id = brief_id
        self.seq = 0

    def write(self, **rec) -> dict:
        rec = {"t": time.time(), "brief": self.brief_id, **rec}
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def start(self, tool: str, input: dict) -> int:
        self.seq += 1
        self.write(
            event="tool.start",
            seq=self.seq,
            tool=tool,
            input=input,
            input_sha256=sha(json.dumps(input, sort_keys=True)),
        )
        return self.seq

    def end(self, seq: int, tool: str, **rest) -> None:
        self.write(event="tool.end", seq=seq, tool=tool, **rest)


@dataclass
class Worker:
    brief_id: str
    sandbox: Sandbox
    log: ToolLog
    events: list[dict] = field(default_factory=list)  # trace events, in order
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    _task: asyncio.Task | None = None

    def emit(self, kind: str, **rest) -> dict:
        ev = {"kind": kind, "t": time.time(), **rest}
        self.events.append(ev)
        return ev

    # the five tools, each a plain function that calls the Sandbox port

    def _artifact(self, path: str) -> str | None:
        try:
            return sha(self.sandbox.read(path))
        except FileNotFoundError:
            return None

    def read(self, path: str) -> str:
        seq = self.log.start("read", {"path": path})
        t0 = time.perf_counter()
        try:
            data = self.sandbox.read(path)
            status, out = 0, data
        except FileNotFoundError as e:
            status, out = 1, str(e)
        self.log.end(
            seq,
            "read",
            exit_status=status,
            stdout_sha256=sha(out),
            ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return out

    def write(self, path: str, content: str) -> str:
        seq = self.log.start("write", {"path": path, "content_sha256": sha(content)})
        t0 = time.perf_counter()
        self.sandbox.write(path, content)
        self.log.end(
            seq,
            "write",
            exit_status=0,
            artifact=path,
            artifact_sha256=self._artifact(path),
            ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return f"wrote {path}"

    def edit(self, path: str, old: str, new: str) -> str:
        seq = self.log.start("edit", {"path": path, "old": old, "new": new})
        t0 = time.perf_counter()
        before = self.sandbox.read(path)
        if old not in before:
            self.log.end(
                seq, "edit", exit_status=1, stdout_sha256=sha("old text not found")
            )
            return "old text not found"
        self.sandbox.write(path, before.replace(old, new, 1))
        self.log.end(
            seq,
            "edit",
            exit_status=0,
            artifact=path,
            artifact_sha256=self._artifact(path),
            ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return f"edited {path}"

    def bash(self, command: str) -> str:
        seq = self.log.start("bash", {"command": command})
        t0 = time.perf_counter()
        r = self.sandbox.exec(command)
        self.log.end(
            seq,
            "bash",
            exit_status=r.exit_status,
            stdout_sha256=sha(r.stdout),
            stderr_sha256=sha(r.stderr),
            ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return f"exit {r.exit_status}\n{r.stdout}{r.stderr}"

    async def ask(self, question: str) -> str:
        qid = f"q{len(self.pending) + 1}"
        seq = self.log.start("ask", {"question_id": qid, "question": question})
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[qid] = fut
        self.emit("question", question_id=qid, text=question)
        self.log.write(event="question", question_id=qid, text=question)
        answer = await fut  # blocks here; abort() cancels the run task
        self.log.write(event="answer", question_id=qid, text=answer)
        self.log.end(seq, "ask", exit_status=0, stdout_sha256=sha(answer))
        return answer

    # the port's two messages into a running worker

    async def answer(self, question_id: str, text: str) -> None:
        self.pending[question_id].set_result(text)

    async def abort(self) -> None:
        if self._task is not None:
            self._task.cancel()

    # run

    async def run(self, agent: Agent, prompt: str) -> Report | None:
        self._task = asyncio.current_task()
        try:
            async with agent.iter(prompt, deps=self) as run:
                async for node in run:
                    if isinstance(node, ModelRequestNode):
                        # stream every model request so the gateway can cut it
                        async with node.stream(run.ctx) as stream:
                            async for _ in stream:
                                pass
                    elif isinstance(node, CallToolsNode):
                        pass
                    elif isinstance(node, End):
                        pass
            report = run.result.output
            self.emit("terminal", outcome="report", report=report.model_dump())
            self.log.write(
                event="terminal", outcome="report", report=report.model_dump()
            )
            return report
        except asyncio.CancelledError:
            self.emit("terminal", outcome="aborted")
            self.log.write(event="terminal", outcome="aborted")
            return None


def build_agent(gateway_url: str, token: str, model: str) -> Agent:
    provider = AnthropicProvider(api_key=token, base_url=gateway_url)
    agent = Agent(
        AnthropicModel(model, provider=provider),
        deps_type=Worker,
        output_type=Report,
        system_prompt=(
            "You are an Executor working in a sandbox whose working directory is /work. "
            "Use the tools. When the task needs a fact you do not have, call ask "
            "rather than guessing. When done, return the Report."
        ),
    )

    @agent.tool
    def read(ctx: RunContext[Worker], path: str) -> str:
        """Read a file in the sandbox."""
        return ctx.deps.read(path)

    @agent.tool
    def write(ctx: RunContext[Worker], path: str, content: str) -> str:
        """Write a file in the sandbox."""
        return ctx.deps.write(path, content)

    @agent.tool
    def edit(ctx: RunContext[Worker], path: str, old: str, new: str) -> str:
        """Replace the first occurrence of old with new in a sandbox file."""
        return ctx.deps.edit(path, old, new)

    @agent.tool
    def bash(ctx: RunContext[Worker], command: str) -> str:
        """Run a shell command in the sandbox."""
        return ctx.deps.bash(command)

    @agent.tool
    async def ask(ctx: RunContext[Worker], question: str) -> str:
        """Ask the supervisor a question. Blocks until an answer arrives."""
        return await ctx.deps.ask(question)

    return agent
