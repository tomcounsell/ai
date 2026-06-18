# Pluggable Builder Harness

**Status:** Shipped (plan #1725, tracking issue #1725)

## Overview

The granite container's dev-relay path is now backed by a `BuilderHarness`
abstraction rather than a hardwired PTY + `claude` TUI. The existing claude path
becomes `PtyClaudeBuilder` (zero behavior change); a new `PiSubprocessBuilder`
runs the Pi coding agent as a subprocess. The Opus PM selects the builder per
task by emitting `[/dev:pi]` or `[/dev:claude]` (bare `[/dev]` defaults to
claude).

## The `BuilderHarness` Seam

`agent/granite_container/builder.py` defines the `BuilderHarness` protocol:

```python
class BuilderHarness(Protocol):
    @property
    def name(self) -> str: ...
    def prepare(self, spec: Any) -> None: ...
    def run_turn(self, prompt: str) -> str: ...
    def close(self) -> None: ...
```

The container calls `builder.run_turn(payload)` instead of inlining the
PTY+transcript logic. The two implementations share no code; the protocol is
the entire seam.

**Ownership boundaries** (Risk 5 in the plan):

- The **builder** returns the final assistant text (or `""` on timeout/crash/empty).
- The **container caller** (`_route_pm_classification`) owns:
  - `_last_dev_report = <builder-returned-text>` — used by the wrap-up guard.
  - The empty-return fallback gate: empty return → bump `transcript_fallback_count`, substitute `DEV_REPORT_UNAVAILABLE`.

This keeps the fallback gate harness-agnostic. An empty return from claude (empty
transcript read) and an empty return from Pi (timeout/crash/thinking-only output)
hit the identical caller-owned handling.

## `PtyClaudeBuilder`

The existing dev-relay branch extracted behind the protocol. Owns the dev
`PTYDriver`, the `_cycle_idle` cadence, and the JSONL transcript cursor reads.

Preserved surfaces that plan #1721 (lossless checkpoint) depends on — these
identifiers must remain stable:

- `dev_transcript` (= `result.dev_transcript_path`)
- `dev_baseline` (= `text_bearing_count(dev_transcript)`)
- `text_bearing_count`
- `last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)`

Bare `[/dev]` and `[/dev:claude]` route here. Behavior is identical to
pre-refactor; regression tests assert byte-identical relay for representative turns.

## `PiSubprocessBuilder`

Runs Pi (`pi -p --mode json`) as a one-shot subprocess. No PTY, no idle
heuristic, no startup parser.

### Invocation

```python
proc = subprocess.Popen(
    [
        "pi", "-p", "--mode", "json",
        "--append-system-prompt", str(rails_path),      # canonical _prime-rails.md
        "--append-system-prompt", str(persona_path),    # config/personas/granite/pi_dev_rails.md
        "--provider", provider,
        "--model", model,
        "--tools", "read,bash,edit,write",
    ],
    cwd=builder_cwd,          # == self._dev_pty.cwd (see Builder CWD Grounding below)
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    start_new_session=True,   # own process group for clean group kill on timeout
)
out, err = proc.communicate(input=payload, timeout=PI_SUBPROCESS_TIMEOUT_S)
```

On `TimeoutExpired`: `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` reaps Pi
and every `bash`/`edit`/`write` tool subprocess it spawned, then `return ""`.

### Pi NDJSON Envelope

Pi emits an NDJSON event stream to stdout. The terminal event is `agent_end`,
which carries a `messages` array. Extraction (`parse_pi_final_text`):

1. Parse each line as JSON.
2. Find the last `{"type": "agent_end", "messages": [...]}` event.
3. From the final assistant message's `content` array, concatenate all entries
   where `type == "text"`.
4. Drop `type == "thinking"` entries.

Edge cases (all return `""`):

- No `agent_end` event (timeout or crash before completion).
- Thinking-only final message (no `type == "text"` entries).
- Empty stream.

`parse_pi_final_text(stream: str) -> str` is a pure function unit-tested against
a captured real Pi envelope as a fixture.

### Two-Flag Priming Mechanism

Pi cannot run slash commands, so priming uses `--append-system-prompt` twice:

1. **First flag** → `.claude/commands/granite/_prime-rails.md` — the canonical
   WORKER rails shared with the PM, Dev, and Teammate prime commands.
2. **Second flag** → `config/personas/granite/pi_dev_rails.md` — the Pi-tuned
   dev-persona delta (worktree discipline, narrow tests, report in natural language).

This guarantees one source of rails truth with no drift. The Pi persona file
header reads: "Rails are loaded separately from `.claude/commands/granite/_prime-rails.md`
via a prior `--append-system-prompt`; do not duplicate them here."

### Builder CWD Grounding (Risk 6)

The container has no `worktree` or `working_dir` attribute. The single source
of truth for the directory the Dev actually runs in is the dev PTY's own `cwd`:
`PTYDriver.__init__` stores `self.cwd` and spawns pexpect with `cwd=self.cwd`.

`PiSubprocessBuilder` receives `builder_cwd = self._dev_pty.cwd` — the same
directory the claude builder runs in — guarded so it is never falsy:

```python
if not builder_cwd:
    raise ValueError("builder_cwd is falsy — cannot spawn Pi with cwd=None")
```

Spawning with `cwd=None` would inherit the repo root, defeating worktree
isolation.

**Sandbox caveat:** in the self-spawned sandbox path the cwd is an empty
tempdir. `[/dev:pi]` is only meaningful on the production bridge path where
a real worktree cwd is provided.

### Timeout Policy

`PI_SUBPROCESS_TIMEOUT_S = 600` (10 minutes) is the per-turn execution bound.

Do NOT use `CYCLE_IDLE_TIMEOUT_S` (12 hours) — that is a PTY-idle sanity
ceiling, not a per-turn bound. Passing the 12-hour value would allow a runaway
Pi subprocess to pin a worker turn for half a day.

### Model Policy

Tests use local `ollama/gemma4:31b` (free). The cross-vendor demo uses
`--provider google --model gemini-2.5-pro`. Never rely on Pi's effective
default — spike-3 showed the default may resolve to a different local model
regardless of the configured provider (see plan §Spike Results).

## `[/dev:<harness>]` Selector

`granite_classifier.py` parses the optional harness suffix in both regex paths:

- **Strict** (`PREFIX_TOKEN_RE`): `^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`
- **Fallback** (`PREFIX_TOKEN_FALLBACK_RE`): `\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]`

The fallback must capture the harness group or a PM emitting `[/dev:pi]`
mid-line (with leading whitespace or trailing text) would silently drop `:pi`
and route to claude with `compliance_miss=True` and no error signal.

`ClassificationResult` gains a `harness: str | None` field. `None` or `"claude"`
route to `PtyClaudeBuilder`; `"pi"` routes to `PiSubprocessBuilder`; an unknown
harness routes a compliance nudge back to PM (observable), not a crash.

### Concrete Task-Shape Rubric

The following rubric is inserted into `.claude/commands/granite/prime-pm-role.md`
(under "What you DO", developer-routing section). It is the non-circular
standard the PM-routing acceptance tests assert against:

> **Choosing a builder harness.** When you route to the developer, you may name
> the builder harness with `[/dev:<harness>]`. Default is claude (bare `[/dev]`
> ≡ `[/dev:claude]`). Pick by **task shape**:
>
> - **`[/dev:pi]`** — one-shot, self-contained, structured edits that complete
>   in a single turn with no back-and-forth: a single-file or few-file change
>   with a clear spec, a focused refactor, a well-scoped bug fix, generating a
>   file from a precise description, or a mechanical transformation. Pi is a
>   stateless single-turn subprocess builder — give it everything it needs in
>   one instruction.
>
> - **`[/dev]` / `[/dev:claude]`** — interactive, multi-step, or exploratory
>   work that needs iteration across turns: multi-file features requiring
>   investigation, work where the developer must run tests and react to failures,
>   anything needing the full `/do-*` SDLC skill suite, or tasks where you
>   expect to relay several rounds with the developer. Claude is the persistent
>   interactive TUI builder.
>
> - **When unsure, default to `[/dev]` (claude).** Pi is an optimization for
>   cleanly-specifiable single-turn work, not the default.
>
> - After a `[/dev:pi]` turn, **re-read the resulting diff yourself before
>   reporting `[/complete]`** — Pi is a non-claude builder and is not
>   slash-rails-primed the way claude is; you are the verification layer for
>   its output.

The token-shape documentation at `prime-pm-role.md:30` is updated to the
harness-aware strict form:
`^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`

## Process-Group Kill on Timeout

Pi is launched with `start_new_session=True`. This creates a new session so
Pi and all tool subprocesses it spawns share the same process group ID (pgid).

On `TimeoutExpired`:

```python
os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
proc.communicate()  # drain buffers
logger.warning("Pi builder turn timed out after %ss; killed process group", PI_SUBPROCESS_TIMEOUT_S)
return ""
```

Using `Popen` + `communicate(timeout=...)` (rather than `subprocess.run`) holds
the proc handle for `os.getpgid` and for `close()`-time reaping.

## Empty-Return Degradation

A Pi turn that returns `""` (timeout, non-zero exit, or thinking-only output)
follows the same caller-owned path as an empty claude transcript read:

1. `transcript_fallback_count` is bumped.
2. `DEV_REPORT_UNAVAILABLE` is substituted as the dev text written to PM.
3. PM receives a seed and writes a `[/user]` summary via the wrap-up guard.
4. The human always receives at least `OPERATOR_TERMINAL_MESSAGE`.

The builder never touches `_last_dev_report`, `transcript_fallback_count`, or
`DEV_REPORT_UNAVAILABLE` — those are caller-owned.

## RPC Fast-Follow

`--mode rpc` is the right long-term multi-turn Pi interface (persistent
`stdin`/`stdout` JSONL process with `prompt`/`steer`/`follow-up`/`abort`
commands). It is out of scope for this PoC (filed as a separate slug). The
`prepare()` and `close()` methods in the `BuilderHarness` protocol are included
because `PtyClaudeBuilder` genuinely needs them; `PiSubprocessBuilder` implements
them as no-ops (no long-lived process in the single-turn `-p` mode).

## Quick Start / Example Routing Decision

| Task | Builder | Token |
|------|---------|-------|
| Add a docstring to one function | Pi | `[/dev:pi]` |
| Rename a constant across 3 files | Pi | `[/dev:pi]` |
| Implement a multi-file feature with tests | Claude | `[/dev]` |
| Debug a failing test interactively | Claude | `[/dev]` |
| Generate a config file from a precise spec | Pi | `[/dev:pi]` |
| Run `/do-test` and patch failures | Claude | `[/dev]` |

## Files

| File | Purpose |
|------|---------|
| `agent/granite_container/builder.py` | `BuilderHarness` protocol, `PtyClaudeBuilder`, `PiSubprocessBuilder`, `parse_pi_final_text`, `PI_SUBPROCESS_TIMEOUT_S` |
| `agent/granite_container/granite_classifier.py` | Harness-aware regexes; `harness` field on `ClassificationResult` |
| `agent/granite_container/container.py` | `_get_builder(harness, result)` wires the builder seam |
| `config/personas/granite/pi_dev_rails.md` | Pi-tuned dev-persona delta (no rails copy; rails come from `_prime-rails.md`) |
| `.claude/commands/granite/prime-pm-role.md` | `[/dev:<harness>]` selector rubric added |
| `tests/unit/test_pi_builder.py` | `parse_pi_final_text` unit tests + `PiSubprocessBuilder` with mocked subprocess |
| `tests/unit/granite_container/test_container_builder_gate.py` | Caller-owned fallback gate tests (harness-agnostic stub) |
| `tests/integration/test_pi_builder_e2e.py` | Real Pi invocation against local ollama in a temp worktree |

## See Also

- [Granite PTY Production](granite-pty-production.md) — production wiring; dev relay now goes through `BuilderHarness`
- [Granite Interactive TUI](granite-interactive-tui.md) — classification taxonomy; `[/dev]` token now accepts optional `:<harness>` suffix
- [PTY Driver](pty-driver.md) — `PTYDriver` is the claude builder's substrate; `PiSubprocessBuilder` bypasses it entirely
