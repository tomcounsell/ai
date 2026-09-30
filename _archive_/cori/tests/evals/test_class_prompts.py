"""The class prompts behind their gate. Plan 05 task 9; tech stack §8, §10.

A pydantic-evals dataset per prompt, each case one run of the real
PydanticAI worker over a fake sandbox and a fake door, through an
in-process gateway on the seat the class uses, against the real provider.
Two cases per prompt: spike 08's task that needs a question, and the same
task with the fact given, expecting no `ask`. The Executor has a third:
a drafted reply whose artifact starts with `To:` and `Subject:` lines.

Skips without the Anthropic key in the Keychain, as the gateway's live
test does, so CI reports skipped.
"""

import asyncio
import os
from dataclasses import dataclass
import uuid
from pathlib import Path

import psycopg
import pytest
import uvicorn
from pydantic import SecretStr
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from adapters.pydantic_ai import PydanticAIWorker
from gateway.app import build_app
from gateway.core import Gateway
from infra.models import load
from infra.secrets import MissingSecret, read_secret
from schemas.report import Report, ScribeReport
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import FakeTree
from tests.test_pydantic_ai_worker import FakeDoor, FakeSandbox, make_brief

pytestmark = requires_postgres

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = {
    "Executor": (ROOT / "prompts" / "executor.md").read_text(),
    "Scribe": (ROOT / "prompts" / "scribe.md").read_text(),
}
SEATS = {"Executor": "frontier", "Scribe": "summarizer"}
NAMES = {
    "Executor": ("read", "write", "bash", "ask"),
    "Scribe": ("read", "ask", "memory.episodic.write", "memory.operator.propose"),
}
PROFILES = {"Executor": "worktree", "Scribe": "scratch"}
SCHEMAS = {"Executor": "Report", "Scribe": "ScribeReport"}
BUDGET = 5_000_000  # room for a few frontier turns per case

EXECUTOR_ASK = (
    "Create /work/greeting.txt containing exactly one line: 'Hello, <name>!' "
    "where <name> is the person's first name. You do not know the name. "
    "Then run `wc -c /work/greeting.txt` with bash and put its output in the "
    "report summary. List the file in artifacts."
)
EXECUTOR_GIVEN = EXECUTOR_ASK.replace(
    "You do not know the name.", "The person's first name is Tom."
)
EXECUTOR_REPLY = (
    "Draft a reply to the client mail below as /work/reply.md, a message "
    "artifact. Do not send anything. List the file in artifacts.\n\n"
    "From: dana@example.com\nSubject: Invoice 118\n\n"
    "Hi, could you confirm invoice 118 was received and when it will be paid? "
    "Dana\n\n"
    "The invoice was received on Monday and is scheduled for payment on the "
    "30th."
)
SCRIBE_ASK = (
    "Record one episode of kind decision: the person moved the weekly standup "
    "to a new day. You do not know which day. Cite event 41 as provenance, "
    "data class PROJECT. Then return the ScribeReport."
)
SCRIBE_GIVEN = SCRIBE_ASK.replace(
    "You do not know which day.", "The new day is Thursday."
)
ANSWERS = {"EXECUTOR_ASK": "Tom", "SCRIBE_ASK": "Thursday"}


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def provider_key() -> str:
    try:
        return read_secret("anthropic_api_key")
    except MissingSecret:
        pytest.skip("no Anthropic key in the Keychain")


class Outcome(dict):
    """What one run produced: whether it asked, its terminal, its rows, and
    the text of each artifact on the retained mount."""


async def run_case(gateway, port, agent_class, task, answer):
    sandbox = FakeSandbox()
    door = FakeDoor()
    mount = Path(
        os.path.join(os.environ.get("TMPDIR", "/tmp"), f"eval-{uuid.uuid4().hex[:8]}")
    )
    mount.mkdir()
    brief = make_brief(
        str(mount),
        agent_class=agent_class,
        names=NAMES[agent_class],
        profile_name=PROFILES[agent_class],
        report_schema=SCHEMAS[agent_class],
        instruction=task,
        objective_id=None if agent_class == "Scribe" else "obj-1",
    )
    model = load().model(SEATS[agent_class]).id
    gateway.tree.add(brief.id, BUDGET, generation=1)
    async with await connect() as conn:
        token = await gateway.issue_token(
            conn, brief_id=brief.id, generation=1, model_ref=model, space=brief.space
        )
        await conn.commit()
    brief = brief.model_copy(update={"model_ref": model, "gateway_token": token})
    worker = PydanticAIWorker(sandbox, door, f"http://127.0.0.1:{port}", PROMPTS)
    handle = await sandbox.create(brief.sandbox_profile)
    asked = []
    terminal = None
    async for event in worker.run(brief, handle):
        if event.kind == "question":
            asked.append(event.question.text)
            await worker.answer(brief.id, event.question.question_id, answer)
        elif event.kind == "terminal":
            terminal = event.terminal
    artifacts = {}
    if terminal.report is not None and isinstance(terminal.report, Report):
        for ref in terminal.report.artifact_refs:
            artifacts[ref.path] = (mount / ref.path[len("/work/") :]).read_text()
    return Outcome(
        asked=asked,
        terminal=terminal,
        tools=[r.tool for r in door.rows if r.event == "tool.start"],
        artifacts=artifacts,
        door=door,
    )


@dataclass
class Asked(Evaluator):
    expected: bool

    def evaluate(self, ctx: EvaluatorContext) -> dict:
        asked = bool(ctx.output["asked"])
        return {"asks_iff_needed": asked == self.expected}


@dataclass
class ValidReport(Evaluator):
    schema: str

    def evaluate(self, ctx: EvaluatorContext) -> dict:
        t = ctx.output["terminal"]
        ok = t.outcome == "report" and type(t.report).__name__ == self.schema
        return {"valid_report": ok}


@dataclass
class Artifact(Evaluator):
    path: str
    starts: tuple[str, ...] = ()
    equals: str | None = None

    def evaluate(self, ctx: EvaluatorContext) -> dict:
        text = ctx.output["artifacts"].get(self.path)
        if text is None:
            return {"artifact_listed": False}
        lines = text.splitlines()
        ok = all(
            i < len(lines) and lines[i].startswith(s) for i, s in enumerate(self.starts)
        )
        if self.equals is not None:
            ok = ok and text.strip() == self.equals
        return {"artifact_listed": True, "artifact_shape": ok}


@dataclass
class Wrote(Evaluator):
    tool: str

    def evaluate(self, ctx: EvaluatorContext) -> dict:
        return {f"used_{self.tool}": self.tool in ctx.output["tools"]}


EXECUTOR = Dataset(
    name="executor",
    cases=[
        Case(
            name="asks_for_the_name",
            inputs=("Executor", EXECUTOR_ASK, "Tom"),
            evaluators=(
                Asked(expected=True),
                ValidReport(schema="Report"),
                Artifact(path="/work/greeting.txt", equals="Hello, Tom!"),
            ),
        ),
        Case(
            name="name_given_no_ask",
            inputs=("Executor", EXECUTOR_GIVEN, "Tom"),
            evaluators=(
                Asked(expected=False),
                ValidReport(schema="Report"),
                Artifact(path="/work/greeting.txt", equals="Hello, Tom!"),
            ),
        ),
        Case(
            name="drafted_reply_has_headers",
            inputs=("Executor", EXECUTOR_REPLY, "unused"),
            evaluators=(
                Asked(expected=False),
                ValidReport(schema="Report"),
                Artifact(path="/work/reply.md", starts=("To:", "Subject:")),
            ),
        ),
    ],
)

SCRIBE = Dataset(
    name="scribe",
    cases=[
        Case(
            name="asks_for_the_day",
            inputs=("Scribe", SCRIBE_ASK, "Thursday"),
            evaluators=(
                Asked(expected=True),
                ValidReport(schema="ScribeReport"),
                Wrote(tool="write_episode"),
            ),
        ),
        Case(
            name="day_given_no_ask",
            inputs=("Scribe", SCRIBE_GIVEN, "Thursday"),
            evaluators=(
                Asked(expected=False),
                ValidReport(schema="ScribeReport"),
                Wrote(tool="write_episode"),
            ),
        ),
    ],
)


async def serve_and_evaluate(dataset):
    key = provider_key()
    gateway = Gateway(provider_key=key, connect=connect, tree=FakeTree())
    config = uvicorn.Config(
        build_app(gateway), host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    async def run(inputs):
        agent_class, text, answer = inputs
        return await run_case(gateway, port, agent_class, text, answer)

    try:
        report = await dataset.evaluate(run, max_concurrency=1, progress=False)
        await gateway.drain()
    finally:
        server.should_exit = True
        await task
        await gateway.aclose()
    report.print(include_input=False, include_output=False)
    return report


def failed(report):
    return [
        (case.name, name)
        for case in report.cases
        for name, a in case.assertions.items()
        if not a.value
    ] + [(f.name, "task failed") for f in report.failures]


async def test_executor_prompt_asks_only_when_it_must():
    report = await serve_and_evaluate(EXECUTOR)
    assert failed(report) == []


async def test_scribe_prompt_asks_only_when_it_must():
    report = await serve_and_evaluate(SCRIBE)
    assert failed(report) == []
