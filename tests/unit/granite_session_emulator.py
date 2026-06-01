"""Claude Code session emulator for granite-agent-loop tests.

This module is NOT a test file (no `test_` prefix -> pytest won't collect it).
It is a reusable fixture library that lets unit tests replay Claude Code's
stream-json behaviour deterministically, without spawning a real `claude`
subprocess or hitting ollama.

Why it exists
-------------
Claude Code sessions have peculiarities that the granite operator loop must
handle: a session can emit a numbered multiple-choice question, surface a
permission/feedback prompt, time out, emit a malformed line, or crash and
need to be respawned. Reproducing those live is slow and flaky. The emulator
gives each peculiarity a builder so a test can script the exact event
sequence and assert how `GraniteAgentLoop` / `GraniteRouter` react.

Event shapes
------------
The stream-json shapes below were cross-checked against the real
`claude -p --output-format stream-json --verbose` envelope and against how
`siteboon/claudecodeui` parses Claude Code output (see
`docs/features/granite-agent-loop.md` ## Testing & emulation):

* `{"type": "system", "subtype": "init", "session_id": "..."}` -- session
  handshake. The `session_id` is the value `--resume <uuid>` would need.
* `{"type": "assistant", "message": {"role": "assistant",
   "content": [{"type": "text"|"tool_use", ...}]}}` -- mid-turn output.
* `{"type": "user", "message": {"role": "user",
   "content": [{"type": "tool_result", "tool_use_id": "...", ...}]}}`.
* `{"type": "result", "subtype": "success", "result": "...",
   "session_id": "..."}` -- turn complete; `ClaudeSession` returns on this.
* synthetic operator events `{"type": "timeout"|"decode_error"|"broken_pipe"}`
  injected by `ClaudeSession` itself on failure modes.

Multiple-choice / feedback prompts
-----------------------------------
In headless `-p` mode Claude does NOT render an interactive TUI menu; a
question arrives as ordinary assistant/result *text* containing numbered
options. The canonical interactive TUI shape is `❯ N. text` (U+276F), which
`multiple_choice_text()` reproduces so a test can prove the operator
recognizes either rendering. Permission/approval prompts likewise do not pop
up under `--permission-mode bypassPermissions`; `feedback_prompt_turn()`
models the text a session *would* surface if one did, so the operator's
handling can be exercised regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A scripted turn is either the list of events to return from
# read_until_result(), or an Exception to raise from send_message() (crash).
Turn = list[dict] | Exception | type


# ---------------------------------------------------------------------------
# Event builders -- real stream-json shapes
# ---------------------------------------------------------------------------


def system_init_event(session_id: str = "sess-00000000") -> dict:
    """Session handshake. `session_id` is what `--resume` would consume."""
    return {"type": "system", "subtype": "init", "session_id": session_id}


def assistant_text_event(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def assistant_tool_use_event(name: str, tool_input: dict, tool_id: str = "tu-1") -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def tool_result_event(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        },
    }


def result_event(
    text: str = "ok", session_id: str = "sess-00000000", subtype: str = "success"
) -> dict:
    return {"type": "result", "subtype": subtype, "result": text, "session_id": session_id}


def timeout_event(reason: str = "per-line 30s deadline reached") -> dict:
    return {"type": "timeout", "reason": reason}


def decode_error_event(raw: str = "<garbage>", error: str = "Expecting value") -> dict:
    return {"type": "decode_error", "raw": raw, "error": error}


def broken_pipe_event(reason: str = "stdout EOF") -> dict:
    return {"type": "broken_pipe", "reason": reason}


# ---------------------------------------------------------------------------
# Peculiarity builders
# ---------------------------------------------------------------------------


def multiple_choice_text(question: str, options: list[str], selected: int | None = None) -> str:
    """Render a Claude Code multiple-choice prompt as text.

    Uses the interactive TUI marker `❯` (U+276F) on the selected option, the
    same shape siteboon/claudecodeui's option regex `/[❯\\s]*(\\d+)\\.\\s+(.+)/`
    keys on. In headless mode the arrow is usually absent; pass selected=None.
    """
    lines = [question]
    for i, opt in enumerate(options, start=1):
        marker = "❯ " if selected == i else "  "
        lines.append(f"{marker}{i}. {opt}")
    return "\n".join(lines)


def multiple_choice_turn(question: str, options: list[str]) -> list[dict]:
    """A turn whose result text is a numbered multiple-choice question.

    The operator (granite) is expected to recognize this and call
    `handle_choice`.
    """
    menu = multiple_choice_text(question, options)
    return [
        system_init_event(),
        assistant_text_event(menu),
        result_event(menu),
    ]


def feedback_prompt_turn(
    prompt: str = "Do you want to proceed?", options: tuple[str, ...] = ("Yes", "No")
) -> list[dict]:
    """A turn emulating a permission/feedback approval prompt.

    Modeled as text because under `--permission-mode bypassPermissions` no real
    approval prompt reaches stdout; this is the shape one would take if it did.
    """
    menu = multiple_choice_text(prompt, list(options))
    return [assistant_text_event(menu), result_event(menu)]


def crash_turn(reason: str = "Claude subprocess has exited") -> Exception:
    """A turn that crashes on send (BrokenPipeError) -- loop must restart()."""
    return BrokenPipeError(reason)


def crash_on_read_turn(reason: str = "stdout EOF") -> list[dict]:
    """A turn that survives send but yields only a broken_pipe synthetic event."""
    return [broken_pipe_event(reason)]


# ---------------------------------------------------------------------------
# FakeClaudeSession -- drop-in for agent.claude_session.ClaudeSession
# ---------------------------------------------------------------------------


@dataclass
class FakeClaudeSession:
    """Replays a scripted list of turns. API-compatible with ClaudeSession.

    Each (send_message, read_until_result) pair consumes one scripted turn.
    A turn that is an Exception is raised from send_message() to emulate a
    crash; a turn that is a list[dict] is returned from read_until_result().
    """

    script: list[Turn] = field(default_factory=list)
    task_list_id: str = "granite-poc-fake1234"
    model: str = "sonnet"
    session_id: str | None = None

    started: bool = field(default=False, init=False)
    stop_count: int = field(default=0, init=False)
    restart_count: int = field(default=0, init=False)
    resume_count: int = field(default=0, init=False)
    sent_messages: list[str] = field(default_factory=list, init=False)
    _cursor: int = field(default=0, init=False)
    _pending: list[dict] | None = field(default=None, init=False)
    _running: bool = field(default=False, init=False)

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self.started = True
        self._running = True

    def stop(self, timeout_s: float = 5.0) -> None:
        self.stop_count += 1
        self._running = False

    def restart(self) -> None:
        self.restart_count += 1
        self._running = True

    def resume(self) -> bool:
        """Context-preserving respawn. Returns True iff a session_id is known."""
        self.resume_count += 1
        self._running = True
        return self.session_id is not None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def pid(self) -> int | None:
        return 4242 if self._running else None

    # --- I/O ---------------------------------------------------------------

    def send_message(self, text: str) -> None:
        self.sent_messages.append(text)
        turn = self.script[self._cursor] if self._cursor < len(self.script) else result_event_turn()
        self._cursor += 1
        if isinstance(turn, type) and issubclass(turn, BaseException):
            raise turn("scripted crash")
        if isinstance(turn, BaseException):
            raise turn
        self._pending = turn

    def read_until_result(self, timeout: int = 120) -> list[dict]:
        events = self._pending if self._pending is not None else [result_event("(no script)")]
        self._pending = None
        return events

    # --- context manager ---------------------------------------------------

    def __enter__(self) -> FakeClaudeSession:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def result_event_turn(text: str = "(exhausted)") -> list[dict]:
    return [result_event(text)]


# ---------------------------------------------------------------------------
# session patching helper
# ---------------------------------------------------------------------------


def patch_sessions(monkeypatch, pm: FakeClaudeSession, dev: FakeClaudeSession) -> None:
    """Patch GraniteAgentLoop's ClaudeSession constructor.

    Dispatches on the configured model: the loop builds PM with model='opus'
    and Dev with model='sonnet'. Each returns the matching pre-scripted fake.
    """

    def factory(cfg):
        return pm if cfg.model == "opus" else dev

    monkeypatch.setattr("agent.granite_agent_loop.ClaudeSession", factory)


# ---------------------------------------------------------------------------
# FakeRouter -- scripted GraniteRouter
# ---------------------------------------------------------------------------


@dataclass
class FakeRouter:
    """Replays scripted RouterDecisions; records every route() call.

    A scripted entry that is an Exception is raised (to emulate
    GraniteRoutingError). When the script is exhausted a `done` decision is
    returned so loops terminate rather than hang.
    """

    decisions: list = field(default_factory=list)
    calls: list[dict] = field(default_factory=list, init=False)
    _cursor: int = field(default=0, init=False)

    def route(self, *, pm_events=None, dev_events=None, operator_events=None, task=None):
        from agent.granite_router import GraniteRoutingError, RouterDecision

        self.calls.append(
            {
                "pm_events": pm_events,
                "dev_events": dev_events,
                "operator_events": operator_events,
                "task": task,
            }
        )
        if self._cursor >= len(self.decisions):
            return RouterDecision(action="done", payload="(script exhausted)", target="none")
        entry = self.decisions[self._cursor]
        self._cursor += 1
        if isinstance(entry, BaseException):
            raise entry if not isinstance(entry, type) else entry("scripted routing error")
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise GraniteRoutingError("scripted routing error")
        return entry
