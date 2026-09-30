"""The PydanticAI worker. Plan 05; seams §2.1, §2.4, §5.2; tech stack §5;
spike 08, lifted.

One Brief runs as one PydanticAI agent whose model is the gateway (base URL
and the Brief's own token), whose tools are the ones the Brief's capabilities
cover, and whose output type is the Brief's report schema. Every tool call is
two tool log rows through the kernel's door, the first durable before the
sandbox is touched. `ask` blocks the run on a Future the kernel resolves with
`answer`; `abort` cancels the run task. The last event of every run is a
`terminal`.

This module imports nothing from `kernel/`; the door is the `KernelAPI`
Protocol handed in at construction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from pydantic_ai import (
    Agent,
    CallToolsNode,
    ModelHTTPError,
    ModelRequestNode,
    ModelRetry,
    RunContext,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_graph import End

from ports.kernel import KernelAPI
from ports.sandbox import SandboxProvider
from schemas.brief import AgentClass, Brief, BriefToken
from schemas.ids import BriefId, QuestionId
from schemas.memory import BeliefProposal, EpisodeWrite
from schemas.records import ToolLogRecord
from schemas.report import ArtifactRef, Report, ScribeReport, Verdict
from schemas.sandbox import MOUNT_TARGET, SandboxHandle
from schemas.space import DataClass
from schemas.trace import Question, Terminal, TraceEvent

OUTPUT_TYPES: dict[str, type] = {
    "Report": Report,
    "Verdict": Verdict,
    "ScribeReport": ScribeReport,
}

DEFAULT_INSTRUCTION = "Carry out the node's contract and return the Report."
BASH_TIMEOUT = 600.0


def sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _inside_mount(path: str) -> str | None:
    """The normalized path when it sits under the mount, else None. A path
    that steps out by `..` is outside; a symlink escape is the port's to
    refuse (seams §2.2)."""
    if not path.startswith("/"):
        return None
    normalized = posixpath.normpath(path)
    if normalized == MOUNT_TARGET or normalized.startswith(MOUNT_TARGET + "/"):
        return normalized
    return None


def _telemetry_configured() -> bool:
    """True once infra/telemetry.py has configured Logfire in this process
    (tech stack §12). Telemetry, never the record."""
    try:
        import logfire

        return bool(logfire.DEFAULT_LOGFIRE_INSTANCE.config._initialized)
    except Exception:
        return False


def _model_for(brief: Brief, gateway_url: str) -> Model:
    """The gateway is the provider: the Brief's token is the API key and the
    gateway URL the base URL. The SDK's default retries stand, so a 403
    refusal ends the run on the first response and a relayed 429 is retried."""
    provider = AnthropicProvider(
        api_key=brief.gateway_token.get_secret_value(), base_url=gateway_url
    )
    return AnthropicModel(brief.model_ref, provider=provider)


def system_prompt(class_prompt: str, brief: Brief) -> str:
    """The class paragraph, then every context block in slice order under a
    heading naming its kind (seams §1.5). Nothing here carries a clock value,
    so the gateway's stable-prefix rule can apply."""
    parts = [class_prompt.strip()]
    for block in brief.context_slice.blocks:
        parts.append(f"## {block.kind}\n\n{block.text.strip()}")
    return "\n\n".join(parts) + "\n"


def refusal_reason(error: ModelHTTPError) -> str | None:
    """The gateway's refusal message from a 403 body, else None."""
    if error.status_code != 403:
        return None
    body = error.body
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"]
    return str(body) if body is not None else ""


@dataclass
class _Run:
    """Everything private to one run of one Brief."""

    brief: Brief
    handle: SandboxHandle
    token: BriefToken
    events: asyncio.Queue[TraceEvent]
    seq: int = 0
    seq_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[QuestionId, asyncio.Future[str]] = field(default_factory=dict)
    task: asyncio.Task | None = None
    hashes: dict[str, str] = field(default_factory=dict)
    terminal_written: bool = False

    async def next_seq(self) -> int:
        async with self.seq_lock:
            self.seq += 1
            return self.seq


class PydanticAIWorker:
    """Implements `ports.worker.Worker` on PydanticAI (tech stack §5)."""

    def __init__(
        self,
        sandbox: SandboxProvider,
        kernel: KernelAPI,
        gateway_url: str,
        prompts: Mapping[AgentClass, str],
    ):
        self.sandbox = sandbox
        self.kernel = kernel
        self.gateway_url = gateway_url
        self.prompts = dict(prompts)
        self._runs: dict[BriefId, _Run] = {}

    # -- the port's three messages -------------------------------------------

    async def run(
        self, brief: Brief, handle: SandboxHandle
    ) -> AsyncIterator[TraceEvent]:
        if brief.id in self._runs:
            raise RuntimeError(f"{brief.id} is already running")
        run = _Run(
            brief=brief,
            handle=handle,
            token=BriefToken(brief_id=brief.id, generation=brief.generation),
            events=asyncio.Queue(),
        )
        self._runs[brief.id] = run
        run.task = asyncio.create_task(self._drive(run), name=f"worker:{brief.id}")
        try:
            while True:
                event = await run.events.get()
                yield event
                if event.kind == "terminal":
                    break
        finally:
            if run.task is not None and not run.task.done():
                run.task.cancel()
                try:
                    await run.task
                except asyncio.CancelledError, Exception:
                    pass
            self._runs.pop(brief.id, None)

    async def answer(
        self, brief_id: BriefId, question_id: QuestionId, text: str
    ) -> None:
        run = self._runs.get(brief_id)
        if run is None:
            raise KeyError(f"{brief_id} is not running")
        future = run.pending.get(question_id)
        if future is None:
            raise KeyError(f"{brief_id} has no open question {question_id}")
        if not future.done():
            future.set_result(text)

    async def abort(self, brief_id: BriefId) -> None:
        run = self._runs.get(brief_id)
        if run is not None and run.task is not None:
            run.task.cancel()

    # -- the run task --------------------------------------------------------

    async def _drive(self, run: _Run) -> None:
        """Runs the agent to its end and pushes the terminal last, whatever
        ended it."""
        terminal: Terminal
        try:
            agent = self._agent(run)
            prompt = run.brief.instruction or DEFAULT_INSTRUCTION
            async with agent.iter(prompt, deps=run) as agent_run:
                async for node in agent_run:
                    if isinstance(node, ModelRequestNode):
                        # Every request streams so the gateway can cut it.
                        async with node.stream(agent_run.ctx) as stream:
                            async for _ in stream:
                                pass
                    elif isinstance(node, (CallToolsNode, End)):
                        pass
            terminal = Terminal(
                outcome="report", report=agent_run.result.output, error=None
            )
        except asyncio.CancelledError:
            terminal = Terminal(outcome="aborted", report=None, error=None)
        except ModelHTTPError as e:
            reason = refusal_reason(e)
            if reason is not None and "budget" in reason:
                error = "budget_exhausted"
            elif reason is not None:
                error = f"gateway refused: {reason}"
            else:
                error = f"gateway returned {e.status_code}"
            terminal = Terminal(outcome="failed", report=None, error=error)
        except Exception as e:  # any other end, including retries exhausted
            terminal = Terminal(
                outcome="failed", report=None, error=f"{type(e).__name__}: {e}"
            )
        for future in run.pending.values():
            if not future.done():
                future.cancel()
        await self._terminal(run, terminal)

    async def _terminal(self, run: _Run, terminal: Terminal) -> None:
        if not run.terminal_written:
            run.terminal_written = True
            record = self._row(run, "terminal", outcome=terminal.outcome)
            if terminal.report is not None:
                record = record.model_copy(
                    update={"report": terminal.report.model_dump(mode="json")}
                )
            try:
                await asyncio.shield(self.kernel.record_tool(run.token, record))
            except asyncio.CancelledError:
                pass
            except Exception:
                # A stale generation: the kernel writes this row itself
                # (plan 05, Design). The event still goes out.
                pass
        run.events.put_nowait(self._event(run, "terminal", terminal=terminal))

    # -- the agent -----------------------------------------------------------

    def _agent(self, run: _Run) -> Agent[_Run, Any]:
        brief = run.brief
        names = {c.name for c in brief.capabilities}
        agent: Agent[_Run, Any] = Agent(
            _model_for(brief, self.gateway_url),
            deps_type=_Run,
            output_type=OUTPUT_TYPES[brief.report_schema],
            system_prompt=system_prompt(self.prompts[brief.agent_class], brief),
        )
        if _telemetry_configured():
            agent.instrument = True
        worker = self

        if "read" in names:

            @agent.tool
            async def read(ctx: RunContext[_Run], path: str) -> str:
                """Read a file under /work. Returns its text."""
                return await worker._read(ctx.deps, path)

        if "write" in names:

            @agent.tool
            async def write(ctx: RunContext[_Run], path: str, content: str) -> str:
                """Write a file under /work. Returns the sha256 of what landed."""
                return await worker._write(ctx.deps, path, content)

            @agent.tool
            async def edit(ctx: RunContext[_Run], path: str, old: str, new: str) -> str:
                """Replace the first occurrence of old with new in a file under
                /work. Returns the sha256 of what landed."""
                return await worker._edit(ctx.deps, path, old, new)

        if "bash" in names:

            @agent.tool
            async def bash(ctx: RunContext[_Run], command: str) -> str:
                """Run a shell command in the sandbox, in /work."""
                return await worker._bash(ctx.deps, command)

        if "ask" in names:

            @agent.tool
            async def ask(ctx: RunContext[_Run], question: str) -> str:
                """Ask the supervisor one question. Blocks until the answer
                arrives; use it rather than guessing."""
                return await worker._ask(ctx.deps, question)

        if "memory.episodic.write" in names:

            @agent.tool
            async def write_episode(
                ctx: RunContext[_Run],
                kind: str,
                text: str,
                data_class: DataClass,
                provenance: list[int],
                regards: str | None = None,
            ) -> str:
                """Write one episode to the space's memory. Returns its id."""
                return await worker._write_episode(
                    ctx.deps, kind, text, data_class, provenance, regards
                )

        if "memory.operator.propose" in names:

            @agent.tool
            async def propose_belief(
                ctx: RunContext[_Run],
                statement: str,
                kind: str,
                domain: str,
                supporting_events: list[int],
                test: str,
                proposed_source_class: str,
            ) -> str:
                """Propose one belief about the person for review. Returns
                the proposal id."""
                return await worker._propose_belief(
                    ctx.deps,
                    statement,
                    kind,
                    domain,
                    supporting_events,
                    test,
                    proposed_source_class,
                )

        if brief.report_schema == "Report":

            @agent.output_validator
            async def hash_artifacts(ctx: RunContext[_Run], output: Report) -> Report:
                return await worker._hash_artifacts(ctx.deps, output)

        return agent

    # -- rows ----------------------------------------------------------------

    def _row(self, run: _Run, event: str, **fields: Any) -> ToolLogRecord:
        return ToolLogRecord(
            brief_id=run.brief.id,
            generation=run.brief.generation,
            space_id=run.brief.space,
            event=event,
            **fields,
        )

    def _event(self, run: _Run, kind: str, **fields: Any) -> TraceEvent:
        return TraceEvent(
            brief_id=run.brief.id,
            generation=run.brief.generation,
            at=datetime.now(UTC),
            kind=kind,
            question=fields.get("question"),
            terminal=fields.get("terminal"),
        )

    async def _start(self, run: _Run, tool: str, input: dict[str, Any]) -> int:
        """The `tool.start` row, durable before the port is touched."""
        seq = await run.next_seq()
        await self.kernel.record_tool(
            run.token,
            self._row(
                run,
                "tool.start",
                seq=seq,
                tool=tool,
                input=input,
                input_sha256=sha256(_canonical(input)),
            ),
        )
        return seq

    async def _end(self, run: _Run, seq: int, tool: str, t0: float, **fields: Any):
        await self.kernel.record_tool(
            run.token,
            self._row(
                run,
                "tool.end",
                seq=seq,
                tool=tool,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                **fields,
            ),
        )

    async def _host_hash(self, run: _Run, path: str) -> str | None:
        try:
            data = await self.sandbox.read(run.handle, path)
        except FileNotFoundError:
            return None
        digest = sha256(data)
        run.hashes[path] = digest
        return digest

    # -- the sandbox tools ---------------------------------------------------

    def _mount_path(self, path: str) -> str:
        inside = _inside_mount(path)
        if inside is None:
            raise ModelRetry(
                f"{path!r} is outside {MOUNT_TARGET}; read, write, and edit "
                f"take paths under {MOUNT_TARGET} only"
            )
        return inside

    async def _read(self, run: _Run, path: str) -> str:
        path = self._mount_path(path)
        seq = await self._start(run, "read", {"path": path})
        t0 = time.perf_counter()
        try:
            data = await self.sandbox.read(run.handle, path)
            status, out = 0, data.decode(errors="replace")
        except FileNotFoundError as e:
            status, out = 1, f"not found: {e}"
        await self._end(
            run, seq, "read", t0, exit_status=status, stdout_sha256=sha256(out)
        )
        return out

    async def _write(self, run: _Run, path: str, content: str) -> str:
        path = self._mount_path(path)
        seq = await self._start(
            run, "write", {"path": path, "content_sha256": sha256(content)}
        )
        t0 = time.perf_counter()
        try:
            await self.sandbox.write(run.handle, path, content.encode())
        except (PermissionError, OSError) as e:
            await self._end(
                run, seq, "write", t0, exit_status=1, stderr_sha256=sha256(str(e))
            )
            return f"write refused: {e}"
        digest = await self._host_hash(run, path)
        await self._end(
            run,
            seq,
            "write",
            t0,
            exit_status=0,
            artifact=path,
            artifact_sha256=digest,
        )
        return f"wrote {path} sha256={digest}"

    async def _edit(self, run: _Run, path: str, old: str, new: str) -> str:
        path = self._mount_path(path)
        seq = await self._start(
            run,
            "edit",
            {
                "path": path,
                "old_sha256": sha256(old),
                "new_sha256": sha256(new),
                "old_length": len(old),
                "new_length": len(new),
            },
        )
        t0 = time.perf_counter()
        try:
            before = (await self.sandbox.read(run.handle, path)).decode(
                errors="replace"
            )
        except FileNotFoundError:
            await self._end(
                run, seq, "edit", t0, exit_status=1, stdout_sha256=sha256("not found")
            )
            return f"not found: {path}"
        if old not in before:
            await self._end(
                run,
                seq,
                "edit",
                t0,
                exit_status=1,
                stdout_sha256=sha256("old text not found"),
            )
            return "old text not found"
        try:
            await self.sandbox.write(
                run.handle, path, before.replace(old, new, 1).encode()
            )
        except (PermissionError, OSError) as e:
            await self._end(
                run, seq, "edit", t0, exit_status=1, stderr_sha256=sha256(str(e))
            )
            return f"edit refused: {e}"
        digest = await self._host_hash(run, path)
        await self._end(
            run,
            seq,
            "edit",
            t0,
            exit_status=0,
            artifact=path,
            artifact_sha256=digest,
        )
        return f"edited {path} sha256={digest}"

    async def _bash(self, run: _Run, command: str) -> str:
        seq = await self._start(run, "bash", {"command": command})
        t0 = time.perf_counter()
        result = await self.sandbox.exec(run.handle, command, timeout=BASH_TIMEOUT)
        await self._end(
            run,
            seq,
            "bash",
            t0,
            exit_status=result.exit_status,
            stdout_sha256=sha256(result.stdout),
            stderr_sha256=sha256(result.stderr),
        )
        tail = " (timed out)" if result.timed_out else ""
        return f"exit {result.exit_status}{tail}\n{result.stdout}{result.stderr}"

    # -- ask -----------------------------------------------------------------

    async def _ask(self, run: _Run, question: str) -> str:
        seq = await self._start(run, "ask", {"question_sha256": sha256(question)})
        t0 = time.perf_counter()
        question_id = await self.kernel.raise_question(run.token, question, seq)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        run.pending[question_id] = future
        run.events.put_nowait(
            self._event(
                run,
                "question",
                question=Question(question_id=question_id, text=question, tool_seq=seq),
            )
        )
        try:
            text = await future
        finally:
            run.pending.pop(question_id, None)
        await self.kernel.record_tool(
            run.token,
            self._row(
                run,
                "answer",
                seq=seq,
                tool="ask",
                question_id=question_id,
                text=text,
            ),
        )
        await self._end(run, seq, "ask", t0, exit_status=0, stdout_sha256=sha256(text))
        return text

    # -- the memory family ---------------------------------------------------

    async def _write_episode(
        self, run: _Run, kind, text, data_class, provenance, regards
    ) -> str:
        try:
            write = EpisodeWrite(
                space=run.brief.space,
                kind=kind,
                text=text,
                provenance=provenance,
                data_class=data_class,
                regards=regards,
            )
        except ValidationError as e:
            raise ModelRetry(str(e)) from None
        seq = await self._start(
            run,
            "write_episode",
            {
                "kind": write.kind,
                "data_class": write.data_class,
                "regards": write.regards,
                "provenance": write.provenance,
                "text_sha256": sha256(write.text),
            },
        )
        t0 = time.perf_counter()
        episode_id = await self.kernel.write_episode(run.token, write)
        await self._end(
            run,
            seq,
            "write_episode",
            t0,
            exit_status=0,
            stdout_sha256=sha256(episode_id),
        )
        return episode_id

    async def _propose_belief(
        self,
        run: _Run,
        statement,
        kind,
        domain,
        supporting_events,
        test,
        proposed_source_class,
    ) -> str:
        try:
            proposal = BeliefProposal(
                space=run.brief.space,
                statement=statement,
                kind=kind,
                domain=domain,
                supporting_events=supporting_events,
                test=test,
                proposed_source_class=proposed_source_class,
            )
        except ValidationError as e:
            raise ModelRetry(str(e)) from None
        seq = await self._start(
            run,
            "propose_belief",
            {
                "statement_sha256": sha256(proposal.statement),
                "kind": proposal.kind,
                "domain": proposal.domain,
                "supporting_events": proposal.supporting_events,
            },
        )
        t0 = time.perf_counter()
        proposal_id = await self.kernel.propose_belief(run.token, proposal)
        await self._end(
            run,
            seq,
            "propose_belief",
            t0,
            exit_status=0,
            stdout_sha256=sha256(proposal_id),
        )
        return proposal_id

    # -- report time ---------------------------------------------------------

    async def _hash_artifacts(self, run: _Run, report: Report) -> Report:
        """Every artifact's hash is the bridge's, taken on the host side and
        recorded as a `read` invocation with the artifact set (seams §1.6,
        §5.2). A path that does not exist is a retry naming it."""
        refs: list[ArtifactRef] = []
        missing: list[str] = []
        for ref in report.artifact_refs:
            inside = _inside_mount(ref.path)
            if inside is None:
                missing.append(ref.path)
                continue
            seq = await self._start(run, "read", {"path": inside, "artifact": True})
            t0 = time.perf_counter()
            digest = await self._host_hash(run, inside)
            if digest is None:
                await self._end(
                    run, seq, "read", t0, exit_status=1, stdout_sha256=sha256("")
                )
                missing.append(ref.path)
                continue
            await self._end(
                run,
                seq,
                "read",
                t0,
                exit_status=0,
                stdout_sha256=digest,
                artifact=inside,
                artifact_sha256=digest,
            )
            refs.append(ref.model_copy(update={"path": inside, "sha256": digest}))
        if missing:
            raise ModelRetry(
                "these artifact paths do not exist under /work: "
                + ", ".join(sorted(missing))
            )
        return report.model_copy(update={"artifact_refs": refs})
