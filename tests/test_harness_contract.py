"""The harness contract: what every harness behind the port must do, run
against each real binary (Claude Code and Pi) under the real turn sandbox
profile, through the real gateway and a real Postgres, with a scripted
model provider on loopback (`tests/scripted_upstream.py`) so no model is
paid for.

A new harness is added by adding it to `HARNESSES`; it passes when every
case here does. Pi's cases go through the gateway's OpenAI route (task 3a)
and are skipped on a kernel without it. Cases that need the real binary
skip when it is not installed.

Live spend: none.
"""

import asyncio
import contextlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from core import corrections, db, runs, signals, tasks
from core.gateway import TURN_TOKEN, Gateway
from core.settings import settings
from harnesses import claude_code, pi
from tests import scripted
from tests.scripted_upstream import Hang, Run, Say, ScriptedUpstream, Task

pytestmark = pytest.mark.spend(usd=0)

FAKE_KEY = "sk-fake-" + "contract-stray-credential-0123456789"


@dataclass(frozen=True)
class Harness:
    name: str
    module: object
    model: str
    route: str  # the scripted upstream's route for this harness
    binary: str

    def available(self) -> str | None:
        if not Path(self.binary).exists():
            return f"{self.name} is not installed at {self.binary}"
        if self.name == "pi":
            if "openai_upstream" not in Gateway.__init__.__code__.co_varnames:
                return "the gateway has no OpenAI route yet (task 3a)"
            if pi.version(settings.pi) != pi.PINNED:
                return f"the installed Pi is not the pinned {pi.PINNED}"
        return None


HARNESSES = [
    Harness("claude_code", claude_code, "haiku", "anthropic", settings.claude),
    Harness("pi", pi, "gpt-6.1-sol", "openai", settings.pi),
]


@pytest.fixture(params=HARNESSES, ids=lambda h: h.name)
def harness(request) -> Harness:
    why = request.param.available()
    if why:
        pytest.skip(why)
    return request.param


class World:
    def __init__(self, h: Harness, dsn, task_id, brief, gateway, upstream):
        self.h, self.dsn, self.task_id, self.brief = h, dsn, task_id, brief
        self.gateway, self.upstream = gateway, upstream

    async def turn(self, prompt: str, resume: str | None = None, harness: dict | None = None):
        b = self.brief
        build = self.h.module.workspace_turn(
            prompt, cwd=b.workspace, resume=resume, model=self.h.model, harness=harness or b.harness
        )
        return await runs.run_turn(self.gateway, self.task_id, build, dsn=self.dsn)

    async def events(self, type_: str) -> list[dict]:
        async with await db.connect(self.dsn) as conn:
            rows = await (
                await conn.execute(
                    "SELECT payload FROM events WHERE task_id = %s AND type = %s ORDER BY id",
                    (self.task_id, type_),
                )
            ).fetchall()
        return [r[0] for r in rows]

    def calls(self) -> list:
        return [r for r in self.upstream.requests if r.route == self.h.route]


@contextlib.asynccontextmanager
async def world(h: Harness, dsn, tmp_path, script):
    task_id, brief = await scripted.provisioned(dsn, tmp_path)
    if h.name == "claude_code":
        upstream = await ScriptedUpstream(script, answers=_main_call).start()
        gateway = Gateway(dsn, upstream=upstream.url)
    else:
        upstream = await ScriptedUpstream(script).start()
        gateway = Gateway(dsn, openai_upstream=upstream.url)
    await gateway.start()
    try:
        yield World(h, dsn, task_id, brief, gateway, upstream)
    finally:
        await gateway.close()
        await upstream.stop()


def _main_call(body: dict) -> bool:
    """Claude Code makes small side calls (a title, a topic check) that carry
    no tools; the script answers only the conversation's own calls."""
    return bool(body.get("tools"))


def run(coro):
    return asyncio.run(coro)


def _signal_command(name: str, text: str) -> str:
    return f"mkdir -p .valor && printf %s '{text}' > .valor/{name}.md"


def test_a_turn_runs_ends_and_records_the_harness_version(harness, dsn, tmp_path):
    async def go():
        async with world(harness, dsn, tmp_path, [Say("ready")]) as w:
            ended = await w.turn("Say ready.")
            return ended, await w.events("turn.started"), await w.events("turn.ended")

    ended, started, over = run(go())
    assert ended["outcome"] == "done" and not ended["result"]["is_error"]
    assert "ready" in ended["result"]["text"]
    assert ended["result"]["session_id"]
    assert started[0]["harness"] == harness.name
    assert started[0]["harness_version"], "the version is recorded on every turn.started"
    assert len(over) == 1


def test_resume_carries_the_context(harness, dsn, tmp_path):
    async def go():
        async with world(harness, dsn, tmp_path, [Say("first"), Say("second")]) as w:
            one = await w.turn("The codeword is mango.")
            two = await w.turn("What was the codeword?", resume=one["result"]["session_id"])
            return one, two, w.calls()

    one, two, calls = run(go())
    assert two["outcome"] == "done" and not two["result"]["is_error"]
    assert two["result"]["session_id"] == one["result"]["session_id"]
    assert "mango" in calls[-1].text, "the second call carries the first turn's prompt"


def test_signals_a_turn_writes_are_collected(harness, dsn, tmp_path):
    async def go():
        script = [Run(_signal_command("question", "which port?")), Say("asked")]
        async with world(harness, dsn, tmp_path, script) as w:
            ended = await w.turn("Ask a question.")
            return ended, signals.collect(w.brief.workspace, ended["turn_id"])

    ended, found = run(go())
    assert ended["outcome"] == "done"
    assert found.question == "which port?"


def test_a_stop_mid_call_kills_and_reaps_the_group(harness, dsn, tmp_path):
    async def go():
        async with world(harness, dsn, tmp_path, [Hang(120)]) as w:
            turn = asyncio.create_task(w.turn("Keep working."))
            async with await db.connect(dsn) as conn:
                while not await (
                    await conn.execute(
                        "SELECT 1 FROM events WHERE task_id = %s AND type = 'gateway.opened'", (w.task_id,)
                    )
                ).fetchone():
                    await asyncio.sleep(0.1)
                await asyncio.sleep(1)
                await tasks.stop(conn, w.task_id, reason="test")
            ended = await asyncio.wait_for(turn, 60)
            started = (await w.events("turn.started"))[0]["turn_id"]
            alive = await asyncio.to_thread(
                lambda: subprocess.run(
                    ["pgrep", "-f", f"VALOR_TURN={started}"], capture_output=True, text=True, check=False
                ).stdout.split()
            )
            async with await db.connect(dsn) as conn:
                state = await tasks.status(conn, w.task_id)
            return ended, alive, state

    ended, alive, state = run(go())
    assert ended["outcome"] == "stopped"
    assert alive == []
    assert state["spent_usd_micros"] > 0 and tasks.audit(state) == []


def test_every_call_is_metered(harness, dsn, tmp_path):
    async def go():
        script = [Run("echo one"), Run("echo two"), Say("three")]
        async with world(harness, dsn, tmp_path, script) as w:
            ended = await w.turn("Run two commands, then answer.")
            turn_id = ended["turn_id"]
            opened = [e for e in await w.events("gateway.opened") if e.get("turn_id") == turn_id]
            charged = [e for e in await w.events("gateway.charged") if e.get("turn_id") == turn_id]
            return ended, opened, charged, w.calls()

    ended, opened, charged, calls = run(go())
    assert len(calls) == 3, "the script's three replies were three model calls"
    assert len(opened) == len(calls), "every call the provider received was metered"
    assert len(charged) == len(calls)
    assert ended["metered_usd_micros"] > 0
    assert ended["result"]["num_turns"] >= 3 or harness.name == "claude_code"


def test_a_stray_credential_is_never_used(harness, dsn, tmp_path, monkeypatch):
    home = tmp_path / "strayhome"
    (home / ".pi" / "agent").mkdir(parents=True)
    (home / ".pi" / "agent" / "auth.json").write_text(
        json.dumps({"valor": {"type": "api_key", "key": FAKE_KEY}})
    )
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(name, FAKE_KEY)
    monkeypatch.setenv("HOME", str(home))

    async def go():
        async with world(harness, dsn, tmp_path, [Say("ok")]) as w:
            ended = await w.turn("Say ok.")
            return ended, w.calls()

    ended, calls = run(go())
    assert ended["outcome"] == "done" and calls
    for call in calls:
        assert FAKE_KEY not in json.dumps(call.headers) and FAKE_KEY not in call.text
        assert TURN_TOKEN in (call.headers.get("Authorization", "") + call.headers.get("x-api-key", ""))


def test_the_machine_users_pi_credentials_are_unreadable_inside_a_turn(dsn, tmp_path):
    home = tmp_path / "home"
    (home / ".pi" / "agent").mkdir(parents=True)
    (home / ".pi" / "agent" / "auth.json").write_text(FAKE_KEY)
    profile = scripted.kws.profile(rw=[tmp_path / "work"], home=home, fresh=True)
    path = tmp_path / "p.sb"
    path.write_text(profile)
    out = subprocess.run(
        ["sandbox-exec", "-f", str(path), "/bin/cat", str(home / ".pi" / "agent" / "auth.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode != 0 and FAKE_KEY not in out.stdout


CANDIDATES = {
    "a pi extension": (".pi/extensions/evil.js", "console.log('EXTENSION-LOADED-MARKER')"),
    "a Claude Code hook": (
        ".claude/settings.json",
        json.dumps(
            {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo HOOK-MARKER"}]}]}}
        ),
    ),
    "a Pi system prompt": (".pi/SYSTEM.md", "SYSTEM-MD-MARKER"),
    "a Pi appended prompt": (".pi/APPEND_SYSTEM.md", "APPEND-MD-MARKER"),
    "an AGENTS.md": ("AGENTS.md", "AGENTS-MD-MARKER"),
    "a CLAUDE.md": ("CLAUDE.md", "CLAUDE-MD-MARKER"),
    "Pi settings": (".pi/settings.json", json.dumps({"defaultModel": "SETTINGS-MODEL-MARKER"})),
}


@pytest.mark.parametrize("name", list(CANDIDATES))
def test_candidate_configuration_never_reaches_the_model(harness, dsn, tmp_path, name):
    rel, body = CANDIDATES[name]

    async def go():
        async with world(harness, dsn, tmp_path, [Say("ok")]) as w:
            target = Path(w.brief.workspace) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
            ended = await w.turn("Say ok.")
            return ended, w.calls()

    ended, calls = run(go())
    assert ended["outcome"] == "done" and calls
    marker = next(m for m in body.replace("'", '"').split('"') if "MARKER" in m)
    for call in calls:
        assert marker not in call.text, f"{name} reached the model"
        assert call.body.get("model", "").startswith(
            "claude-haiku-4-5" if harness.name == "claude_code" else harness.model
        )


def test_an_unknown_session_id_fails_cleanly(harness, dsn, tmp_path):
    async def go():
        async with world(harness, dsn, tmp_path, []) as w:
            return await w.turn("Continue.", resume="00000000-0000-4000-8000-000000000000"), w.calls()

    ended, calls = run(go())
    assert ended["result"].get("is_error") or ended["outcome"] == "failed"
    assert calls == []


def test_corrections_reach_the_session(harness, dsn, tmp_path):
    async def go():
        async with world(harness, dsn, tmp_path, [Say("ok")]) as w:
            async with await db.connect(dsn) as conn:
                await corrections.record(conn, "Use tabs, never spaces.", by="tom", via="test")
            await w.turn("Continue.")
            return w.calls(), await w.events("turn.started")

    calls, started = run(go())
    assert started[0]["corrections"]
    assert "Use tabs, never spaces." in calls[0].text


def test_corrections_do_not_reach_a_claude_code_subagent(harness, dsn, tmp_path):
    """The recorded result, not a wish: Claude Code gives a subagent its own
    system prompt (`cc_is_subagent=true`) and only the prompt the parent
    wrote, so the Brief and the corrections in it stop at the main session.
    If a release changes this, the case fails and the docs that name the gap
    (`docs/persona.md`, `docs/harnesses.md`) change with it."""
    if harness.name != "claude_code":
        pytest.skip("Pi starts no subagents, so a correction has no second session to reach")

    async def go():
        script = [Task("Say sub."), Say("sub"), Say("done")]
        async with world(harness, dsn, tmp_path, script) as w:
            async with await db.connect(dsn) as conn:
                await corrections.record(conn, "Use tabs, never spaces.", by="tom", via="test")
            await w.turn("Delegate.")
            return w.calls()

    calls = run(go())
    main = [c for c in calls if "Use tabs, never spaces." in c.system_text]
    sub = [c for c in calls if "Say sub." in c.text and "Delegate." not in c.text]
    assert main, "the main session carries the correction"
    assert sub, "the subagent made its own call"
    assert all("Use tabs, never spaces." not in c.text for c in sub)


def test_a_compaction_mid_session_is_survived_and_recorded(dsn, tmp_path):
    h = HARNESSES[1]
    why = h.available()
    if why:
        pytest.skip(why)

    async def go():
        async with world(
            h, dsn, tmp_path, [Say("first"), Say("a summary of the work so far"), Say("second")]
        ) as w:
            w.upstream.report_input[:] = [pi.context_window(h.model) - 1000]
            one = await w.turn("Do the first thing.")
            two = await w.turn("Do the second thing.", resume=one["result"]["session_id"])
            return one, two, w.calls()

    one, two, calls = run(go())
    compactions = one["result"]["compaction"] + two["result"]["compaction"]
    assert compactions and compactions[0]["reason"] == "threshold", "Pi compacted on the usage it was told"
    assert len(calls) == 3, "the turn, its compaction's summary, then the next turn"
    assert two["outcome"] == "done" and not two["result"]["is_error"]
