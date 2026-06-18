---
status: docs_complete
type: feature
appetite: Medium
owner: Valor
created: 2026-06-18
tracking: https://github.com/tomcounsell/ai/issues/1725
last_comment_id:
revision_applied: true
---

# Pluggable Builder Harness — Pi as Builder #1

## Problem

The granite container (`agent/granite_container/`) runs a two-PTY driver↔builder loop: an Opus **PM** session (a `claude` TUI under pexpect) routes work to a **Dev** session (a second `claude` TUI) and relays between them. The Dev side — the *builder* — is hardwired to `claude` in five places, so the only coding harness granite can ever drive is Claude Code.

This is more rigid than it needs to be. The PM/driver is the smart, model-heavy layer; the builder is an executor whose contract is thin ("given an instruction, do the work, produce a final report"). There is no reason the builder must be the same harness as the driver. Driving a *different* builder (Pi, and later others) is granite's genuine differentiator over plain subagents: the Opus driver can pick the right builder per task, cheaply, without the whole system being one vendor's TUI.

**Current behavior:**
- The Dev is spawned as `pexpect.spawn("claude", ...)` (`pty_driver.py:390`), primed via the `/granite:prime-dev-role` slash command, detected idle via claude's TUI bottom-bar/glyph heuristic, and its reply is read verbatim from claude's JSONL transcript (`last_assistant_text` with a `text_bearing_count` baseline) — the dev branch of `Container._route_pm_classification` (`container.py:1308-1359`).
- The routing classifier only knows `[/dev]`, `[/user]`, `[/complete]` (`granite_classifier.py`, `^\[/(dev|user|complete)\]\s*$`). There is no way for the PM to express *which* builder a task should go to.
- Adding any non-claude builder today means forking five coupled code paths with no seam to plug into.

**Desired outcome:**
- A `BuilderHarness` protocol is the single seam the container talks to for builder turns. The existing claude path becomes one implementation (`PtyClaudeBuilder`) behind it with **zero behavior change**.
- A second implementation, `PiSubprocessBuilder`, drives the Pi coding agent (`pi -p --mode json`) as a builder. Pi is a subprocess, not a PTY — no startup parser, no idle heuristic, structured response extraction.
- The Opus PM selects the builder per task by emitting `[/dev:pi]` / `[/dev:claude]` (bare `[/dev]` → default claude). This is also a hardening of the routing sentinel.
- End-to-end proof: a real task routed to `[/dev:pi]` is executed by Pi and its result relayed back to the user through the normal granite flow.

## Freshness Check

**Baseline commit:** `91289fc3`
**Issue filed at:** N/A — plan initiated from a live design discussion on 2026-06-18 (no pre-existing issue; tracking issue created with this plan).
**Disposition:** Overlap

**File:line references re-verified (read directly during the design discussion at baseline):**
- `agent/granite_container/pty_driver.py:390` — `pexpect.spawn("claude", args, ...)` with `--permission-mode bypassPermissions`, optional `--session-id`/`--model` — confirmed present.
- `agent/granite_container/container.py:1308-1359` — dev branch of `_route_pm_classification`: `_cycle_idle(dev)` → `dev_pty.write(dev_prompt)` → `text_bearing_count` baseline → `_cycle_idle(dev)` → `last_assistant_text(dev_transcript, baseline)` → write to PM PTY — confirmed present.
- `granite_classifier.py` `classify_pm_prefix` regex `^\[/(dev|user|complete)\]\s*$` — confirmed present.
- `.claude/commands/granite/prime-pm-role.md:15` — explicitly forbids PM custom tools; `:25-30` — prefix-token output contract — confirmed present.

**Active plans in `docs/plans/` overlapping this area:** `granite_lossless_checkpoint_resume.md` (#1721, status: Ready). It touches the **same** `Container` dev-shuttle area but for an orthogonal concern (lossless checkpoint/resume of granite sessions). No goal conflict, but both modify the dev-relay path — **coordination note:** whichever merges second rebases onto the other; the `BuilderHarness` extraction should preserve the transcript-cursor surfaces #1721 relies on. See Risks.

**Notes:** This plan introduces a seam that does not exist yet; there is no stale premise to correct. The Pi interface facts below were validated by live spikes (see Spike Results), not assumed.

## Prior Art

No prior issues or merged PRs found for pluggable/multi-harness builders (`gh issue list --state closed --search "pluggable builder harness pi codex"` and `gh pr list --state merged --search "builder harness abstraction granite"` both empty). This is a greenfield concept for the granite system. Relevant context:

- **#1681** — zero-LLM shuttle: removed the `summarize_for_pm`/`extract_dev_prompt` ollama calls, making the Dev→PM relay verbatim. The `BuilderHarness.run_turn` contract inherits this: it returns the builder's final text verbatim.
- **#1647** — wrap-up guard: the completion safety net. Unchanged by this work, but the guard's `_last_dev_report` seed must keep working when the last turn ran on a non-claude builder.
- **#1692** — persona priming moved out of `PairSpawnSpec` into slash-command priming at spawn. The Pi builder needs an equivalent priming mechanism that is *not* a slash command (see Technical Approach).
- **#1721** — granite lossless checkpoint resume (active, overlapping — see Freshness Check).

## Research

External research (WebSearch) plus live CLI spikes against the installed `pi` (v0.67.68).

**Queries used:**
- "Pi CLI AI coding assistant --print --mode json rpc non-interactive automation"

**Key findings:**
- **Pi is `@mariozechner/pi-coding-agent`** (repos: `badlogic/pi-mono`, `earendil-works/pi`; site `pi.dev`). It exposes four modes: interactive, **print/json**, **RPC**, and an SDK. Non-interactive modes (`-p`, `--mode json`, `--mode rpc`) **do not show a trust prompt** — confirming no startup-parser equivalent is needed for a Pi builder. (Source: npmjs.com/package/@mariozechner/pi-coding-agent, pi.dev, github.com/badlogic/pi-mono)
- **Print/JSON mode** (`pi -p --mode json "prompt"`): sends one message, streams an NDJSON event stream to stdout, exits. This is the simplest builder interface and the basis for the PoC. (Source: github.com/badlogic/pi-mono/.../coding-agent/README.md)
- **RPC mode** (`pi --mode rpc`): a long-lived process speaking **LF-delimited JSONL** over stdin/stdout, with commands `prompt`, `steer`, `follow-up`, `abort`, `get-state`, `compact` and streaming events. This is the natural **multi-turn persistent builder** interface — and `steer`/`follow-up` map directly onto granite's existing steering inbox. Documented at github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md. **This supersedes the earlier `--session`-subprocess-per-turn idea for multi-turn** — RPC is the better fast-follow path.

**How it informs the plan:** PoC builds on `-p --mode json` (single-turn, stateless, trivially testable). The multi-turn fast-follow is `--mode rpc`, not subprocess-per-turn — noted in No-Gos as a separate slug so the PoC stays small but the right long-term shape is recorded.

## Spike Results

### spike-1: Pi CLI surface and non-interactive modes
- **Assumption**: "Pi has a non-interactive mode that avoids the PTY idle-scraping problem."
- **Method**: code-read (`pi --help`) + web-research.
- **Finding**: Confirmed. `--print, -p` (non-interactive, process-and-exit), `--mode text|json|rpc`, `--session <path>`/`--continue`/`--resume` (session persistence), `--append-system-prompt <text|@file>` (priming via flag), `--skill <path>` (skill loading), `--tools read,bash,edit,write,grep,find,ls`, `--provider`/`--model`. Pi's flags map ~1:1 onto the five claude coupling points.
- **Confidence**: high
- **Impact on plan**: The Pi builder is a `subprocess`, not a PTY. Startup parser and idle heuristic are eliminated for this builder; priming is a flag, not a slash command.

### spike-2: Pi `--mode json` response envelope
- **Assumption**: "Pi's structured output gives a clean, deterministic way to extract the final assistant text."
- **Method**: prototype — ran `pi -p --mode json --no-tools --no-session --thinking off "Reply with exactly: PONG"`.
- **Finding**: Confirmed. Output is an **NDJSON event stream** (`session`, `agent_start`, `turn_start`, `message_start/update/end`, `text_start/delta/end`, `thinking_*`, `turn_end`, `agent_end`). The terminal **`agent_end`** event carries the full `messages` array; the final assistant message's `content[]` contains entries of `type:"text"` (the answer) and `type:"thinking"` (reasoning). Usage/cost ride in each assistant message's `usage` field. Extraction: parse NDJSON, take the final assistant message, concatenate `type=="text"` content, drop `type=="thinking"`.
- **Confidence**: high
- **Impact on plan**: Response extraction is a small deterministic NDJSON parser, unit-testable against a captured fixture. No ANSI scraping, no `text_bearing_count` baseline, no transcript tailing.

### spike-3: Pi default model resolution
- **Assumption**: "`--provider google` selects a Google model."
- **Method**: prototype (observed `provider`/`model` in the spike-2 envelope).
- **Finding**: **Surprise.** Despite the default `--provider google`, the run resolved to **local `ollama/gemma4:31b`** (the machine's configured default). Cost fields were all zero (local).
- **Confidence**: high
- **Impact on plan**: The adapter **must pass explicit `--provider` and `--model`** for the cloud demo; it cannot rely on Pi's effective default. Tests intentionally use local ollama (free); the demo uses an explicit cloud model.

## Data Flow

1. **Entry point**: User message → bridge → worker → `session_executor` constructs `BridgeAdapter` → `Container.run()` steady-state loop.
2. **PM turn**: PM TUI emits a turn; `_cycle_idle(pm)` → `last_assistant_text(pm_transcript)` → `classify_pm_prefix(pm_text)`.
3. **Classification** (changed): regex now yields `(destination, harness, payload)`. For `[/dev:pi]`, `destination="dev"`, `harness="pi"`.
4. **Builder dispatch** (changed): `_route_pm_classification` resolves a `BuilderHarness` for `harness` (default `claude`) and calls `builder.run_turn(payload)` instead of inlining the PTY+transcript logic.
   - `PtyClaudeBuilder.run_turn` = today's `_cycle_idle(dev)` → write → baseline → `_cycle_idle(dev)` → `last_assistant_text`.
   - `PiSubprocessBuilder.run_turn` = `subprocess.Popen(["pi","-p","--mode","json","--append-system-prompt",<rails-file>,"--append-system-prompt",<pi-persona-file>,"--provider",P,"--model",M,"--tools","read,bash,edit,write"], cwd=builder_cwd, start_new_session=True, ...)` → `communicate(input=payload, timeout=PI_SUBPROCESS_TIMEOUT_S)` (timeout → `killpg` the group, `return ""`) → parse NDJSON → final assistant text. **`builder_cwd` is the dev PTY's resolved cwd — `self._dev_pty.cwd` — NOT a nonexistent `worktree`/`working_dir` attribute (see Risk 6).**
5. **Relay back**: builder's returned text is interpreted **by the container caller** (not the builder): the caller applies the empty-return fallback gate (empty → bump `transcript_fallback_count`, seed `DEV_REPORT_UNAVAILABLE`), assigns `_last_dev_report` to the returned text, and writes it to PM PTY. This ownership is harness-agnostic — see Risk 5.
6. **Output**: PM eventually emits `[/user]`/`[/complete]` → delivered to the user via the existing callbacks (unchanged).

## Architectural Impact

- **New dependencies**: `pi` CLI (already installed, v0.67.68) as a runtime dependency for the Pi builder path. No new Python packages (stdlib `subprocess`/`json`).
- **Interface changes**: new `BuilderHarness` protocol; `classify_pm_prefix` return shape gains a `harness` field (additive — default `None`/`"claude"`); `_route_pm_classification` dev branch delegates to a builder instance.
- **Coupling**: **decreases** container↔claude coupling — the container no longer hardcodes the claude PTY for dev turns; it talks to an abstraction.
- **Data ownership**: unchanged. Transcripts, session events, and the wrap-up guard stay owned by the container.
- **Reversibility**: high. `PtyClaudeBuilder` is behavior-preserving; bare `[/dev]` keeps the exact current path. Removing the Pi builder is deleting one class + one persona file + the harness-token branch.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment on the protocol shape; confirm PoC-vs-fast-follow boundary)
- Review rounds: 1 (the `PtyClaudeBuilder` extraction must be reviewed for behavior preservation)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `pi` CLI installed | `command -v pi` | Pi builder subprocess target |
| Pi version ≥ 0.67 | `pi --version` | `--mode json` NDJSON envelope shape |
| Local ollama model for tests | `ollama list \| grep -q gemma4:31b && echo ok` | Free model for unit/integration tests |
| Cloud key for demo (gemini, OQ2) | `python -c "from dotenv import dotenv_values; v=dotenv_values('.env'); assert v.get('GEMINI_API_KEY'), 'GEMINI_API_KEY required for the cross-vendor demo (gemini-2.5-pro)'"` | Real cross-vendor model for end-to-end demo (`--provider google --model gemini-2.5-pro`) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/pluggable_builder_harness_pi.md`

## Solution

### Key Elements

- **`BuilderHarness` protocol** (`agent/granite_container/builder.py`): the single seam the container uses for builder turns. Minimal contract: `name`, `prepare(spec)` (idempotent setup/priming), `run_turn(prompt: str) -> str` (do the work, return final text verbatim), `close()`.
- **`PtyClaudeBuilder`**: wraps the existing dev-PTY + JSONL logic behind the protocol. Pure extraction — no behavior change. Owns the dev `PTYDriver`, the `_cycle_idle` cadence, and `last_assistant_text` baseline reads.
- **`PiSubprocessBuilder`**: runs Pi as a one-shot subprocess (`-p --mode json`) in the session worktree, primed via `--append-system-prompt @<persona-file>`, with an explicit `--provider/--model`. Parses the NDJSON envelope to the final assistant text.
- **Harness-aware classifier**: `classify_pm_prefix` parses `[/dev:<harness>]` (and plain `[/dev]`), returning the selected harness.
- **Builder registry / resolver**: maps a harness name → `BuilderHarness` instance, with `claude` as the default and a clear error for an unknown harness (routed back to PM as a compliance nudge, not a crash).
- **Pi dev persona file** (`config/personas/granite/pi_dev_rails.md` — **note `config/personas/granite/` does not yet exist; the build creates it**): the Pi-tuned dev-persona text Pi receives via `--append-system-prompt`, since it cannot run the `/granite:prime-dev-role` slash command. **Rails single-source-of-truth — OQ1 RESOLVED (round 3): pass `--append-system-prompt` twice.** `pi --help` confirms `--append-system-prompt <text>` "Append text **or file contents** to the system prompt (can be used multiple times)" and accepts a **file path** (verified live: `pi -p --append-system-prompt <path>` appended the file contents and ran). So the canonical rails are NOT forked: the first `--append-system-prompt` flag points at `.claude/commands/granite/_prime-rails.md` (the single source of rails truth, shared with the claude PM/Dev/Teammate primes), and the second points at `config/personas/granite/pi_dev_rails.md`, which contains ONLY the Pi-tuned dev-persona delta (a faithful translation of `prime-dev-role.md`'s dev behaviors: worktree discipline, narrow tests, report in natural language — NOT a copy of the rails). The Pi persona file's header carries a one-line note: "Rails are loaded separately from `.claude/commands/granite/_prime-rails.md` via a prior `--append-system-prompt`; do not duplicate them here." This guarantees one source of rails truth with no drift.

### Flow

PM turn → `classify_pm_prefix` returns `(dev, "pi", payload)` → resolver returns `PiSubprocessBuilder` → `builder.run_turn(payload)` runs `pi -p --mode json` in the worktree → NDJSON parsed to final text → text written to PM PTY → PM continues → `[/user]`/`[/complete]` → user.

### Technical Approach

- **Extraction first, additively.** Land `BuilderHarness` + `PtyClaudeBuilder` as a behavior-preserving refactor in its own commit, fully covered by regression tests, *before* adding Pi. This keeps the risky change (claude path) separable from the new feature (Pi).
- **Classifier change is additive — and must touch BOTH regexes.** `classify_pm_prefix` (`agent/granite_container/granite_classifier.py`) has *two* regexes, and the harness group must be added to **both** or the suffix is silently dropped on the drift path:
  - **Strict** `PREFIX_TOKEN_RE` (`:119`) becomes `^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`. Threaded through the strict branch (`m.group(2)` → harness) at `:200-214`.
  - **Fallback** `PREFIX_TOKEN_FALLBACK_RE` (`:120`, used via `.search(pm_tail[:200])` at `:223`) becomes `\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]`. The fallback branch at `:224-230` must read `harness=fallback.group(2)` — **not just `fallback.group(1)`**, which today reads only `(dev|user|complete)` and would drop the `:pi` suffix when the PM emits `[/dev:pi]` mid-line, with leading whitespace, or with trailing text (exactly the drift the fallback exists to tolerate). Dropping it routes Pi-intended work to claude with `compliance_miss=True` and **no error signal**.
  - Both unknown-return branches (`:192-198` empty-first-line, `:232-237` no-match) return `harness=None`.
  - The harness group defaults to `None` → treated as `claude`. Existing `[/dev]` callers and all current tests are unaffected; the new `harness` field is additive on `ClassificationResult`.
- **Pi priming via flag, not slash.** `--append-system-prompt @<persona-file>` on every invocation (stateless priming is fine for single-turn). The persona file is the "skill translation" deliverable.
- **Pi runs in the claude Dev's exact cwd (BLOCKER, round 3).** `Container` has **no** `worktree`/`working_dir` attribute. The effective cwd is resolved at spawn time as `self.cwd or (self._sandbox[0] if self._sandbox else "")` (`container.py:897`), and in the self-spawned path it can fall back to a sandbox tempdir (`_make_sandbox_cwd()`, `container.py:617-619`) — and in the prewarmed-pool path neither `self.cwd` nor `self._sandbox` is touched (early return at `:612-615`). The single source of truth for the dir the claude Dev actually runs in is the **dev PTY's own `cwd`**: `PTYDriver.__init__` stores `self.cwd` (`pty_driver.py:322`) and spawns `pexpect` with `cwd=self.cwd` (`:397`). This value is correct in **both** paths — prewarmed (pool constructs the dev PTY with `cwd=working_dir`, `pty_pool.py:445-447`; adapter passes the identical `working_dir`, `bridge_adapter.py:424/443`) and self-spawned (`container.py:624` constructs the dev PTY with the resolved sandbox/real cwd). Therefore `PiSubprocessBuilder` receives `builder_cwd = self._dev_pty.cwd` — the *same directory the claude builder would use* — guarded so it is never `None`/empty (no-None guard: if `builder_cwd` is falsy, raise rather than silently `Popen(cwd=None)` inheriting the repo root and defeating Risk 2's isolation). Because `PtyClaudeBuilder` owns the dev PTY, the seam exposes this cwd to the resolver, which passes it into `PiSubprocessBuilder`. `--tools read,bash,edit,write` only. **Sandbox caveat:** in the self-spawned sandbox path the cwd is a tempdir with no git checkout, so the PM's "re-read the diff" verification has nothing to read — Pi-builder runs are therefore only meaningful when the container has a real worktree cwd (the production bridge path always does; the sandbox path is a test/no-worktree fallback and is out of scope for the `[/dev:pi]` demo). See Risk 6.
- **Explicit model.** Adapter passes `--provider`/`--model` from config; tests pass the local ollama model (`--provider google --model ollama/gemma4:31b`, free), the demo passes the cloud model. Never rely on Pi's default (spike-3). **Cloud demo model — OQ2 RESOLVED (round 3): `--provider google --model gemini-2.5-pro`.** Gemini is chosen over anthropic for the demo because the whole point of the pluggable harness is *genuine cross-vendor* execution — driving a non-Anthropic builder under the Opus PM is the differentiator; an anthropic builder would only prove the plumbing, not the cross-vendor claim. `gemini-2.5-pro` is a stable (non-preview), tool-capable model (`pi --list-models` shows tools=`yes`), which the builder needs for `bash/edit/write`. Cheaper fallback if cost/latency matters in the demo: `--provider google --model gemini-2.5-flash` (also tool-capable). Requires `GEMINI_API_KEY` in `.env` (already covered by the Prerequisites check). These model ids are provisional demo constants — pin them in the plan's demo step, not hardcoded in `PiSubprocessBuilder` (the adapter takes provider/model as config).
- **NDJSON parser is the only fragile surface.** Implement it as a small pure function (`parse_pi_final_text(stream: str) -> str`) with a captured real envelope as a fixture; handle: no `agent_end` (timeout/crash → return `""` so the existing `DEV_REPORT_UNAVAILABLE` fallback fires), multiple text blocks (concatenate), thinking-only output (return `""`).
- **Per-turn timeout, not the PTY ceiling — with process-group kill.** `PiSubprocessBuilder.run_turn` bounds the subprocess at `PI_SUBPROCESS_TIMEOUT_S = 10 * 60` (provisional). Use `subprocess.Popen(..., start_new_session=True)` + `proc.communicate(input=payload, timeout=PI_SUBPROCESS_TIMEOUT_S)` (not bare `subprocess.run`) so the handle is held for `os.killpg(os.getpgid(proc.pid), SIGKILL)` on `TimeoutExpired` — this reaps Pi **and** its `bash/edit/write` tool subtree, not just the direct process (see Race 1). On timeout → `logger.warning` + `return ""`. **Do NOT pass `CYCLE_IDLE_TIMEOUT_S`** — that is a 12-hour PTY-idle ceiling, not a turn bound (see Race 1).
- **Caller owns the empty-return interpretation.** `run_turn` returns only the final text (or `""` on timeout/crash/empty). The container caller — not the builder — applies the `_last_dev_report` assignment and the empty-return fallback gate (`transcript_fallback_count` bump + `DEV_REPORT_UNAVAILABLE` substitution). This keeps the gate harness-agnostic; the Pi path and the claude path hit the identical caller-side handling. See Risk 5 for the precise ownership boundary.
- **Protocol surface vs. the No-Go (round-2 NON-BLOCKING).** `BuilderHarness` declares `prepare(spec)` and `close()`, but the single-turn `-p` PoC needs neither for Pi (stateless priming via `--append-system-prompt`; no long-lived process to close beyond the per-turn `killpg`). To avoid speculative surface that contradicts the "design against exactly two implementations" Rabbit Hole: keep `prepare`/`close` in the protocol **only because `PtyClaudeBuilder` genuinely uses them** (the dev PTY has real setup/teardown) — for `PiSubprocessBuilder` they are no-ops (`prepare` returns immediately; `close` reaps any live child via `killpg`). Do not add RPC-lifecycle hooks (`steer`/`follow-up`/persistent-process) to the protocol now; those land with the `--mode rpc` fast-follow slug. The two methods are justified by the claude implementation, not by anticipated Pi multi-turn.
- **Driver verification is load-bearing.** A non-claude builder cannot be trusted on rails compliance the way slash-priming implies. For the PoC, the PM persona is instructed to re-read the diff after a `[/dev:pi]` turn before reporting `[/complete]`. Full adversarial verification is a fast-follow.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `PiSubprocessBuilder.run_turn` must not swallow subprocess failures silently: a non-zero exit, timeout (`subprocess.TimeoutExpired`), or unparseable stream must log `logger.warning` and return `""` (triggering the container's existing `DEV_REPORT_UNAVAILABLE` path). Test asserts the log + empty return, not a swallow.
- [ ] The builder resolver's unknown-harness branch must route a compliance nudge back to PM (observable), not raise. Test asserts the nudge path.
- [ ] No new bare `except Exception: pass` introduced; the extraction preserves existing handlers in the dev branch verbatim.

### Empty/Invalid Input Handling
- [ ] `parse_pi_final_text("")`, a stream with no `agent_end`, and a thinking-only final message each return `""` (not None, not a crash). Unit tests for each.
- [ ] Empty `[/dev:pi]` payload follows the existing empty-`[/dev]` compliance-nudge path (no subprocess spawned).
- [ ] Empty builder output does not trigger a silent loop — it writes `DEV_REPORT_UNAVAILABLE` to PM and continues, same as the claude path on empty transcript.

### Error State Rendering
- [ ] When Pi fails entirely, the user still gets a message via the wrap-up guard / `DEV_REPORT_UNAVAILABLE` seed — test the end-to-end "Pi exits non-zero" path delivers *something* to the user callback.
- [ ] Pi `stderr` on failure is captured into the log line, not dropped.

## Test Impact

- [ ] `tests/unit/granite_container/test_granite_classifier.py` (360 lines; the real path — **NOT** `tests/unit/test_granite_classifier.py`, which does not exist) — UPDATE: add **strict** cases for `[/dev:pi]`, `[/dev:claude]`, `[/dev]` (default), `[/dev:unknown]`; assert the new `harness` field and that existing bare-token cases still classify identically. **Critically, add FALLBACK-path cases** (`[/dev:pi]` mid-line, with leading whitespace, with trailing text — e.g. `output: [/dev:pi] please build`) asserting `harness=="pi"` *and* `compliance_miss=True`. This file's existing fallback tests (`test_fallback_*`, line ~29-99) assert the destination recovers but **never check the harness** — they would lock in the round-2 BLOCKER (dropped `:pi` on drift) green if not extended. The new fallback cases are the regression guard.
- [ ] Tests asserting `_route_pm_classification` dev-branch behavior — UPDATE: re-point at the `BuilderHarness` seam; assert `PtyClaudeBuilder.run_turn` produces byte-identical relay behavior to the pre-refactor path (regression guard).
- [ ] New (Risk 5 — caller-owned fallback gate): `tests/.../test_container*.py` — assert that a `builder.run_turn` returning a non-empty string sets `_last_dev_report` to that exact string (NOT `DEV_REPORT_UNAVAILABLE`) for **both** harnesses; and that a `run_turn` returning `""` bumps `transcript_fallback_count` and seeds `DEV_REPORT_UNAVAILABLE`. Use a stub `BuilderHarness` so the test is harness-agnostic and proves the gate lives in the container caller, not the builder.
- [ ] `tests/.../test_container*.py` two-PTY/loop tests — UPDATE only if they construct the dev relay directly; the goal is no behavioral diff for the claude path.
- [ ] New: `tests/unit/test_pi_builder.py` — `parse_pi_final_text` against the captured fixture + edge cases; `PiSubprocessBuilder` with a mocked subprocess and a real-envelope fixture.
- [ ] New: `tests/integration/test_pi_builder_e2e.py` — real `pi -p --mode json` against local ollama in a temp worktree, asserting a file write happens and the final text is relayed. Marked appropriately so CI runs it on the local model only.
- [ ] New (NIT — PM routing judgment): a persona-routing check on PM output — given the `prime-pm-role.md` selector guidance, assert the PM emits `[/dev:pi]` for a pi-appropriate prompt and `[/dev]`/`[/dev:claude]` for a claude-appropriate one (classifier-level assertion on two representative PM outputs; or a persona-doc review checkpoint per Open Questions).
- [ ] New (NIT — Pi timeout): `PiSubprocessBuilder.run_turn` on a slow/hanging subprocess raises `subprocess.TimeoutExpired` internally at `PI_SUBPROCESS_TIMEOUT_S`, is caught, logs a warning, and returns `""` (driving the caller's `DEV_REPORT_UNAVAILABLE` path). Assert it does **not** use `CYCLE_IDLE_TIMEOUT_S`, **and** that on timeout it calls `os.killpg` on the child's process group (mock `os.killpg`/`os.getpgid`; assert `start_new_session=True` was passed to `Popen`) — proving the tool subtree is reaped, not orphaned.

- [ ] New (Risk 6 — builder cwd grounding): assert `PiSubprocessBuilder` is constructed with `builder_cwd == container._dev_pty.cwd` (the same dir the claude builder runs in), and that a falsy/`None` `builder_cwd` **raises** rather than spawning `Popen(cwd=None)` (which would inherit the repo root and defeat worktree isolation). Mock `Popen`; assert the `cwd=` kwarg equals the dev PTY's cwd; assert the falsy-cwd guard raises.
- [ ] New (Risk 7 — selector rubric is non-circular): the PM-routing acceptance check asserts the PM emits `[/dev:pi]` for a pi-appropriate prompt (one-shot structured edit) and `[/dev]`/`[/dev:claude]` for a claude-appropriate prompt (interactive/multi-turn), measured against the concrete rubric inserted into `prime-pm-role.md` (classifier-level assertion on two representative PM outputs per OQ4 option (b)). Also assert `prime-pm-role.md:30`'s documented regex matches the harness-aware classifier form.

If any current granite test asserts the literal absence of a `harness` concept, that assertion is updated to allow the additive field.

## Rabbit Holes

- **Porting `/do-*` skills to Pi.** The PoC Dev works with native `read/bash/edit/write`. Do not attempt to recreate the full SDLC skill suite inside Pi — that is a large separate effort and unnecessary to prove the seam.
- **Multi-turn / RPC mode now.** `--mode rpc` is the right long-term multi-turn interface, but standing up the persistent RPC process, lifecycle, and steering wiring is its own slug. Single-turn `-p` proves the abstraction.
- **Generalizing the protocol to N harnesses up front.** Design `BuilderHarness` against exactly two implementations (claude, pi). Do not speculatively add hooks for Codex/others until a second non-PTY harness actually lands.
- **PTYPool changes for Pi.** Pi is a subprocess with no PTY slot; do not entangle it with `PTYPool`'s pair lifecycle. The pool stays claude-only; the Pi builder spawns/reaps its own process.
- **Rewriting the wrap-up guard.** It already handles empty dev reports. Leave it; the container caller (not the builder) keeps owning the `_last_dev_report` assignment so the guard's seed works identically across harnesses — see Risk 5.

## Risks

### Risk 1: `PtyClaudeBuilder` extraction changes claude behavior
**Impact:** Every existing granite session regresses — the highest-blast-radius part of this work.
**Mitigation:** Extraction lands as its own commit with regression tests asserting byte-identical relay for the claude path; bare `[/dev]` exercises the exact pre-refactor code path; code-review round dedicated to behavior preservation.

### Risk 2: Pi rails non-compliance (pushes to main, escapes worktree, weak output)
**Impact:** A non-claude builder may ignore rails, producing unsafe or low-quality changes.
**Mitigation:** `cwd=worktree` + `--tools read,bash,edit,write` bound the filesystem surface; the Pi persona file restates the rails; the PM is instructed to re-read the diff before completing a Pi turn (driver verification). Cloud-model demo only after the local plumbing is proven.

### Risk 3: NDJSON envelope shape drifts across Pi versions
**Impact:** `parse_pi_final_text` breaks on a Pi upgrade.
**Mitigation:** Parser keys on stable event names (`agent_end`, `type:"text"`) with a no-`agent_end` → `""` fallback (degrades to `DEV_REPORT_UNAVAILABLE`, never crashes); a prerequisite pins Pi ≥ 0.67; the fixture documents the validated shape.

### Risk 4: Merge collision with #1721 (lossless resume)
**Impact:** Both edit the Container dev-relay; second-to-merge rebases.
**Mitigation:** Keep the `BuilderHarness` extraction surgical and preserve the transcript-cursor surfaces #1721 depends on — **named explicitly:** `dev_transcript` (`result.dev_transcript_path`, `container.py:1343`), `dev_baseline` (`text_bearing_count(...)`, 1344), `text_bearing_count`, and `last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)` (1356-1360). These four are the cursor surfaces; #1721's checkpoint/resume reads from the same transcript path + baseline. The build-seam commit message must call them out by name (see Step 1) so the second-to-merge owner can rebase against a known surface. Coordinate merge order with the #1721 owner; the extraction is mechanical enough to rebase cleanly.

### Risk 5: `_last_dev_report` / empty-return fallback silently degrades on Pi turns
**Impact:** The two-critic CONCERN. Today, `self._last_dev_report = dev_text` (`container.py:1374`) and the empty-return fallback — the `if not dev_text:` gate that bumps `transcript_fallback_count` and substitutes `DEV_REPORT_UNAVAILABLE` (1361-1370) — live **inside** the dev-branch being extracted. The `transcript_fallback_count` increment and the warning are keyed on the **claude Dev JSONL transcript read** (`last_assistant_text` returning empty), a path a Pi turn never produces. If these move into `PtyClaudeBuilder` along with the transcript logic, then on every `[/dev:pi]` turn: (a) `PiSubprocessBuilder` never sets `_last_dev_report`, so the #1647 wrap-up guard seeds `DEV_REPORT_UNAVAILABLE` even when Pi produced a real report; and (b) the empty-return gate that should fire on Pi's empty output never runs.
**Mitigation — ownership rule:** The container's `_route_pm_classification` caller (not either `BuilderHarness` implementation) **owns** both:
1. the `_last_dev_report = <builder-returned-text>` assignment, applied to whatever `builder.run_turn(...)` returns, regardless of harness; and
2. the empty-return fallback gate — `if not <returned-text>:` → bump `transcript_fallback_count`, log, substitute `DEV_REPORT_UNAVAILABLE` — applied uniformly to the builder's return value.

`BuilderHarness.run_turn` returns *only* the final text (or `""`); it never touches `_last_dev_report`, `transcript_fallback_count`, or the `DEV_REPORT_UNAVAILABLE` substitution. `PtyClaudeBuilder` keeps the claude-specific transcript-cursor reads (`dev_transcript`/`dev_baseline`/`last_assistant_text`) internally and returns their result; the *interpretation* of an empty return stays caller-side. This makes the fallback gate harness-agnostic: an empty return from claude (empty transcript) and an empty return from Pi (timeout/crash/thinking-only) hit the identical caller-owned path.
**Regression test:** assert that a `PiSubprocessBuilder.run_turn` returning a non-empty string sets `_last_dev_report` to that string (not `DEV_REPORT_UNAVAILABLE`), and that a `run_turn` returning `""` bumps `transcript_fallback_count` and seeds `DEV_REPORT_UNAVAILABLE` — proving the gate is caller-owned and harness-agnostic. Added under Test Impact.

### Risk 6: Pi runs in the wrong directory (cwd not grounded in any container attribute)
**Impact:** The round-3 BLOCKER. The plan originally said "Pi runs in the worktree / `cwd=working_dir`", but **`Container` has no `worktree` or `working_dir` attribute.** The only cwd state is `self.cwd` (which can be `None`) and `self._sandbox` (set only on the self-spawned path). If `PiSubprocessBuilder` were spawned with `cwd=None`, `subprocess.Popen` inherits the **repo root** — so Pi's `bash/edit/write` would operate on the live repo, not the isolated worktree, defeating Risk 2's filesystem-isolation mitigation entirely. Verified at `container.py:617-619` (sandbox fallback when `self.cwd is None`) and `container.py:897` (`effective_cwd = self.cwd or (self._sandbox[0] if self._sandbox else "")`).
**Mitigation — thread the dev PTY's resolved cwd with a no-None guard:** the builder cwd is `self._dev_pty.cwd` (`PTYDriver` stores it at `pty_driver.py:322` and spawns claude with it at `:397`). This is the *identical* directory the claude builder runs in, correct across both the prewarmed-pool path (`pty_pool.py:445-447` constructs the dev PTY with `cwd=working_dir`; `bridge_adapter.py:424/443` passes the same `working_dir` to pool and Container) and the self-spawned path (`container.py:624` constructs the dev PTY with the resolved sandbox/real cwd). The resolver passes `builder_cwd = self._dev_pty.cwd` into `PiSubprocessBuilder`; the builder **raises** (does not silently `Popen(cwd=None)`) if `builder_cwd` is falsy. This guarantees Pi and claude run in the same dir and that the dir is never `None`.
**Caveat (documented, not a blocker):** in the self-spawned **sandbox** path the cwd is an empty tempdir with no git checkout (`_make_sandbox_cwd`), so the PM "re-read the diff" verification has nothing to read. The production bridge path always supplies a real worktree cwd; the sandbox path is a test/no-worktree fallback where `[/dev:pi]` is not exercised against git. The `[/dev:pi]` demo runs only on the real-worktree path.
**Regression test:** assert `PiSubprocessBuilder` is constructed with `builder_cwd == container._dev_pty.cwd` (same dir as the claude builder), and that a falsy `builder_cwd` raises rather than spawning with `cwd=None`. Added under Test Impact.

### Risk 7: Builder-selection rubric never authored → circular acceptance check
**Impact:** The round-3 BLOCKER. The plan's PM-routing acceptance criterion asserts "the PM emits `[/dev:pi]` for a pi-appropriate task and `[/dev]`/`[/dev:claude]` for a claude-appropriate one" — but Step 3 only said "update `prime-pm-role.md` with selector guidance" and defined **no decision rule**. Without a concrete rubric, the acceptance check is circular: there is no objective standard the PM's choice can be measured against, and "pi-appropriate" is undefined. Verified: `prime-pm-role.md` (read at round-3) has no harness-selection content and its `:30` regex line predates the `[/dev:pi]` token.
**Mitigation:** The concrete task-shape rubric is authored in full in the Agent Integration section (one-shot structured edits → `pi`; interactive/multi-turn/SDLC work → `claude`; default claude when unsure; PM re-reads the diff after a pi turn). The build inserts it verbatim into `prime-pm-role.md`, and updates the `:30` regex documentation to the harness-aware form. The PM-routing test (Test Impact / Success Criteria) asserts the PM follows *this specific rubric* on representative prompts, making the check non-circular.

## Race Conditions

### Race 1: Pi subprocess outlives its turn / orphan on container teardown
**Location:** `PiSubprocessBuilder.run_turn` / `close`
**Trigger:** Container exits (hang watchdog, exception) while a Pi subprocess is mid-run.
**Data prerequisite:** The subprocess handle must be tracked so it can be killed on `close()`.
**State prerequisite:** No second Pi turn starts for the same builder before the first returns (the container loop is single-threaded, so this holds).
**Mitigation:** `subprocess.run(..., timeout=PI_SUBPROCESS_TIMEOUT_S)` bounds each turn (see below); `close()` kills any live child; teardown mirrors the existing `pkill` orphan-reaping pattern in `pty_driver.close`.

> **Orphaned tool subtree on timeout (round-2 NON-BLOCKING, applied — it's cheap and directly tied to the timeout handling above).** A plain `subprocess.run(timeout=...)` SIGKILLs only the direct `pi` process on `TimeoutExpired`; any `bash`/`edit`/`write` tool subprocess Pi spawned can survive as an orphan, holding the worktree or a file lock. Launch Pi in its **own process group** and kill the whole group on timeout:
>
> ```python
> import os, signal, subprocess
> proc = subprocess.Popen(
>     [...pi args...], cwd=working_dir, stdin=subprocess.PIPE,
>     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
>     start_new_session=True,  # new session → new process group; pi + its tool subtree share the pgid
> )
> try:
>     out, err = proc.communicate(input=payload, timeout=PI_SUBPROCESS_TIMEOUT_S)
> except subprocess.TimeoutExpired:
>     os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # reap pi + every tool subprocess
>     proc.communicate()
>     logger.warning("Pi builder turn timed out after %ss; killed process group", PI_SUBPROCESS_TIMEOUT_S)
>     return ""
> ```
>
> Using `Popen` + `communicate(timeout=...)` (rather than `subprocess.run`) is what lets us hold the `proc` handle for `os.getpgid` and for `close()`-time reaping. `close()` applies the same `killpg` to any live child. This eliminates the orphan-subtree window without changing the timeout value or the empty-return contract.

> **CRITICAL — do NOT reuse `CYCLE_IDLE_TIMEOUT_S` here.** `CYCLE_IDLE_TIMEOUT_S` (`container.py:138`) is `12 * 60 * 60.0` — a **12-hour PTY-idle sanity ceiling**, explicitly *"not a hang signal."* It is the maximum a PTY may sit idle, not a per-turn execution bound. Passing it as `subprocess.run(timeout=...)` would let a runaway Pi process pin a worker turn for **12 hours**. Instead, add a dedicated turn bound:
>
> ```python
> # agent/granite_container/builder.py (or container.py constants block)
> PI_SUBPROCESS_TIMEOUT_S = 10 * 60  # 10 minutes — per-turn execution bound for a Pi builder subprocess
> ```
>
> `PiSubprocessBuilder.run_turn` calls `subprocess.run(..., timeout=PI_SUBPROCESS_TIMEOUT_S)` and wraps it in `try/except subprocess.TimeoutExpired:` → `logger.warning(...)` + `return ""` (which drives the container's existing `DEV_REPORT_UNAVAILABLE` fallback, owned by the caller per Risk 5). The 10-minute value is a provisional constant; tune after the demo if real Pi turns routinely run longer.

### Race 2: Pi `bash/edit/write` racing the claude Dev in the same worktree
**Location:** worktree filesystem
**Trigger:** Only if both builders ran concurrently in one session — they do not (single-threaded loop, one builder per `[/dev*]` turn).
**Data prerequisite:** N/A — serialized by the loop.
**State prerequisite:** One active builder turn at a time.
**Mitigation:** The container loop already serializes dev turns; document the invariant. No concurrent-builder support in scope.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Multi-turn persistent Pi builder via `--mode rpc` (steer/follow-up/get-state). The right long-term interface, but a distinct effort; the PoC is single-turn `-p`. *(Tracking issue to be filed as a fast-follow once the seam lands; not promised as done here.)*
- [SEPARATE-SLUG] Adversarial output verification of builder results by the driver (beyond the PoC's "PM re-reads the diff"). Filed as a fast-follow.
- [SEPARATE-SLUG] Additional harnesses (Codex, etc.). The protocol is designed against claude+pi only; a third harness is future work.
- [SEPARATE-SLUG] Porting the `/do-*` SDLC skill suite to Pi. Out of scope; PoC uses native Pi tools.

## Update System

- **`pi` CLI propagation:** Pi is installed via npm (`@mariozechner/pi-coding-agent`) under nvm, **not** currently managed by `/update`. The Pi builder makes Pi a runtime dependency on any machine that runs granite with `[/dev:pi]`. Add a check (and optional install) for `pi` to `scripts/update/verify.py` (verify-only: warn if absent, like the existing `gws` ladder) and document it as a soft prerequisite. Do **not** hard-gate the bridge/worker on Pi — bare `[/dev]`/claude must keep working without Pi installed.
- **New config file:** the Pi dev persona file (`config/personas/granite/pi_dev_rails.md`) is committed to the repo, so it propagates via `git pull` automatically — no extra sync wiring.
- **No new secrets** beyond the already-present provider keys; the cross-vendor cloud demo uses the existing `GEMINI_API_KEY` (OQ2: `--provider google --model gemini-2.5-pro`).
- Migration for existing installs: none — the feature is additive and dormant unless the PM emits `[/dev:pi]`.

## Agent Integration

- **No new MCP server or `.mcp.json` change.** This is entirely inside the granite container's builder path, which the worker already drives. The agent does not invoke the builder via a tool; the PM persona selects it by emitting a routing token.
- **Persona change is the integration surface:** `prime-pm-role.md` is updated to teach the PM the `[/dev:<harness>]` selector and when to choose `pi` vs `claude`. This is how the running agent "reaches" the new capability.

#### Concrete `[/dev:<harness>]` selector rubric (round-3 BLOCKER — authored, not deferred)

The build inserts this rubric verbatim into `prime-pm-role.md` (under "What you DO", item 3, the developer-routing bullet). It is a deterministic, task-shape-based rule so the PM-routing acceptance check (Success Criteria) is non-circular — the test asserts the PM follows *this* rule, not "whatever guidance exists":

> **Choosing a builder harness.** When you route to the developer, you may name the builder harness with `[/dev:<harness>]`. Default is claude (bare `[/dev]` ≡ `[/dev:claude]`). Pick by **task shape**:
> - **`[/dev:pi]`** — one-shot, self-contained, structured edits that complete in a single turn with no back-and-forth: a single-file or few-file change with a clear spec, a focused refactor, a well-scoped bug fix, generating a file from a precise description, or a mechanical transformation. Pi is a stateless single-turn subprocess builder — give it everything it needs in one instruction.
> - **`[/dev]` / `[/dev:claude]`** — interactive, multi-step, or exploratory work that needs iteration across turns: multi-file features requiring investigation, work where the developer must run tests and react to failures, anything needing the full `/do-*` SDLC skill suite, or tasks where you expect to relay several rounds with the developer. Claude is the persistent interactive TUI builder.
> - **When unsure, default to `[/dev]` (claude).** Pi is an optimization for cleanly-specifiable single-turn work, not the default.
> - After a `[/dev:pi]` turn, **re-read the resulting diff yourself before reporting `[/complete]`** — Pi is a non-claude builder and is not slash-rails-primed the way claude is; you are the verification layer for its output (driver verification, PoC-level).

The same edit updates the token-shape documentation at `prime-pm-role.md:30`: the regex line currently reads `^\[/(dev|user|complete)\]\s*$` and must be updated to the harness-aware strict form `^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`, with a note that the harness suffix (`:pi`, `:claude`) is optional and bare `[/dev]` defaults to claude — so the PM persona doc and the classifier (`granite_classifier.py:119`) describe the identical accepted token shape. Verified: `prime-pm-role.md:30` is the line carrying the old regex (read at round-3 revision time).
- **Bridge:** no change — the bridge has no SDLC/builder awareness (per architecture).
- **Integration test** verifies the end-to-end agent path: a PM turn emitting `[/dev:pi]` results in Pi executing and the result relayed (the e2e test under Test Impact covers this at the container level).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pluggable-builder-harness.md` — the `BuilderHarness` seam, the two implementations, the `[/dev:<harness>]` selector **and its concrete task-shape rubric** (one-shot edits → pi; interactive/multi-turn → claude), the Pi NDJSON envelope, the **two-`--append-system-prompt` rails+persona mechanism** (canonical `_prime-rails.md` + Pi-tuned delta — OQ1), the builder-cwd grounding (`self._dev_pty.cwd`, Risk 6), model policy (local ollama for tests; `gemini-2.5-pro` cross-vendor for the demo — OQ2), and the RPC fast-follow.
- [ ] Update `docs/features/granite-pty-production.md` — note that the dev relay now goes through `BuilderHarness`; bare `[/dev]` is unchanged.
- [ ] Update `docs/features/granite-interactive-tui.md` — classification taxonomy gains the optional harness selector.
- [ ] Update `docs/features/pty-driver.md` — clarify it is the claude builder's substrate, not the only builder substrate; **reconcile the README "PoC" vs header "Production" status mismatch** (`docs/features/README.md:125`).
- [ ] Add an entry to `docs/features/README.md` index for the new feature doc.

### Inline Documentation
- [ ] Docstrings on `BuilderHarness`, both implementations, and `parse_pi_final_text` (document the NDJSON contract + fallbacks).
- [ ] Comment the classifier regex change explaining the optional harness group and default.

## Success Criteria

- [ ] `BuilderHarness` protocol exists; `PtyClaudeBuilder` is the claude implementation; the container dev branch delegates to it.
- [ ] Regression tests prove the claude path is behavior-identical to pre-refactor (byte-identical relay on representative turns).
- [ ] `classify_pm_prefix` parses `[/dev:pi]`/`[/dev:claude]`/`[/dev]` correctly; unknown harness routes a compliance nudge, not a crash.
- [ ] `PiSubprocessBuilder` executes a task via `pi -p --mode json` in the worktree and relays the final text; `parse_pi_final_text` unit-tested against a real envelope fixture + edge cases.
- [ ] **Demo:** a real task routed `[/dev:pi]` on the cross-vendor cloud model (`--provider google --model gemini-2.5-pro`, OQ2) produces a committed change in the worktree and a user-facing result through normal granite flow (proof artifact: transcript + diff). The demo runs on the real-worktree bridge path, not the sandbox path (Risk 6 caveat).
- [ ] **PM routing judgment (NIT):** the user-facing change is the PM *choosing* `pi` vs `claude`, not just the plumbing. Add a persona-routing acceptance check: given a prompt-set where the PM is taught (via `prime-pm-role.md`) when to pick `pi`, assert the PM emits `[/dev:pi]` for the pi-appropriate task and `[/dev]`/`[/dev:claude]` for the claude-appropriate one. This validates the *selector judgment*, not just that `[/dev:pi]` executes. (Lightweight: a classifier-level assertion on PM output for two representative prompts, or a persona-doc review checkpoint if a live PM run is too expensive for CI — see Open Questions for which.)
- [ ] Failure paths covered: Pi non-zero exit / timeout / empty output each degrade to `DEV_REPORT_UNAVAILABLE` and still deliver a user message.
- [ ] `prime-pm-role.md` teaches the selector; `pi_dev_rails.md` persona file committed.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`), including the README status reconciliation.
- [ ] grep confirms `_route_pm_classification` references the `BuilderHarness` resolver (Agent Integration: "container calls builder seam").

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (harness-seam)**
  - Name: `seam-builder`
  - Role: Extract `BuilderHarness` + `PtyClaudeBuilder` (behavior-preserving) and the harness-aware classifier.
  - Agent Type: builder
  - Resume: true

- **Builder (pi-adapter)**
  - Name: `pi-builder`
  - Role: Implement `PiSubprocessBuilder` + `parse_pi_final_text` + Pi persona file + resolver wiring.
  - Agent Type: builder
  - Resume: true

- **Validator (seam)**
  - Name: `seam-validator`
  - Role: Verify claude-path behavior preservation + classifier correctness.
  - Agent Type: validator
  - Resume: true

- **Test engineer (pi)**
  - Name: `pi-tester`
  - Role: Unit + integration (local-ollama e2e) tests for the Pi builder.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `harness-doc`
  - Role: Feature doc + granite doc updates + README reconciliation.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types
(standard roster — builder, validator, test-engineer, documentarian)

## Step by Step Tasks

### 1. Extract BuilderHarness seam + PtyClaudeBuilder
- **Task ID**: build-seam
- **Depends On**: none
- **Validates**: existing granite container/classifier tests (must stay green), new harness cases in `tests/unit/granite_container/test_granite_classifier.py` (strict **and** fallback paths)
- **Informed By**: spike-1, spike-2
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/granite_container/builder.py` with the `BuilderHarness` protocol (`name`, `prepare(spec)`, `run_turn(prompt) -> str`, `close()`). Add `PI_SUBPROCESS_TIMEOUT_S = 10 * 60` here (or in the container constants block).
- Move the **claude-specific transcript-cursor reads** into `PtyClaudeBuilder.run_turn`: `_cycle_idle(dev)` → write → `dev_baseline = text_bearing_count(dev_transcript)` → `_cycle_idle(dev)` → `last_assistant_text(dev_transcript, baseline_text_count=dev_baseline)`. Return the text (or `""`). No behavior change to these reads.
- **Keep caller-owned in the container** (Risk 5): the `_last_dev_report = <returned-text>` assignment and the empty-return fallback gate (`if not <returned-text>:` → bump `transcript_fallback_count`, log, seed `DEV_REPORT_UNAVAILABLE`). `BuilderHarness.run_turn` never touches these. This is what makes the gate harness-agnostic.
- **Preserve and name the #1721 surfaces** (Risk 4) — `dev_transcript`/`dev_baseline`/`text_bearing_count`/`last_assistant_text` (`container.py:1343-1360`). The build-seam **commit message must list these four by name** so the second-to-merge owner can rebase against a known surface.
- Extend `classify_pm_prefix` to parse optional `[/dev:<harness>]`; default → claude. **Add the harness group to BOTH regexes** — strict `PREFIX_TOKEN_RE` (`:119`) *and* fallback `PREFIX_TOKEN_FALLBACK_RE` (`:120`) — and thread the capture through BOTH return branches: `m.group(2)` in the strict branch (`:200-214`) and `fallback.group(2)` in the fallback branch (`:224-230`). Set `harness=None` on both unknown returns (`:192-198`, `:232-237`). Missing the fallback path silently drops the `:pi` suffix when the PM emits `[/dev:pi]` mid-line / with leading whitespace / trailing text → routes Pi work to claude with no error signal (BLOCKER, round 2). Add `harness` to `ClassificationResult` (additive, defaults `None`).
- Wire `_route_pm_classification` to resolve and call the builder seam, applying the caller-owned `_last_dev_report` + fallback gate to `builder.run_turn(...)`'s return value.
- **Expose the dev PTY's cwd to the resolver (Risk 6).** The resolver/registry must be able to construct `PiSubprocessBuilder` with `builder_cwd = self._dev_pty.cwd` — the same directory the claude Dev PTY runs in (`pty_driver.py:322/397`). Do **not** read `self.cwd` (nullable) or invent a `worktree` attribute; the dev PTY's `cwd` is the single grounded source and is correct in both the prewarmed-pool and self-spawned paths. Guard against a falsy value (raise, never `Popen(cwd=None)`).

### 2. Validate seam behavior preservation
- **Task ID**: validate-seam
- **Depends On**: build-seam
- **Assigned To**: seam-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert claude relay is byte-identical to pre-refactor on representative turns.
- Assert classifier cases (pi/claude/default/unknown) behave per spec.

### 3. Implement Pi builder
- **Task ID**: build-pi
- **Depends On**: build-seam
- **Validates**: `tests/unit/test_pi_builder.py`
- **Informed By**: spike-2 (NDJSON envelope), spike-3 (explicit model required)
- **Assigned To**: pi-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `PiSubprocessBuilder.run_turn` (`subprocess.Popen(..., start_new_session=True)` + `communicate(timeout=...)`, `-p --mode json`, **`cwd=builder_cwd` where `builder_cwd = container._dev_pty.cwd`** — the dev PTY's resolved cwd, identical to the claude builder; **raise if falsy, never `Popen(cwd=None)`** (Risk 6 / round-3 BLOCKER)). Pass **two** `--append-system-prompt` flags — first `.claude/commands/granite/_prime-rails.md` (canonical rails, single source — OQ1), then the Pi-tuned persona file — plus explicit provider/model. On `TimeoutExpired`: `os.killpg(os.getpgid(proc.pid), SIGKILL)` to reap the tool subtree, log a warning, return `""`. Mirror the `killpg` in `close()`.
- Implement `parse_pi_final_text` with fallbacks (no `agent_end` → `""`).
- Add the Pi dev persona file (`config/personas/granite/pi_dev_rails.md` — **build creates the dir**; the file carries ONLY Pi-tuned dev-persona text + a header back-reference to `_prime-rails.md`, NOT a forked copy of the rails) and resolver registration (`pi` → builder). The resolver wires `builder_cwd` from `container._dev_pty.cwd` into the `PiSubprocessBuilder`.
- **Author the concrete `[/dev:<harness>]` selector rubric in `prime-pm-role.md` (Risk 7 / round-3 BLOCKER)** — see the exact rubric text in the Agent Integration section. Update the regex documentation at `prime-pm-role.md:30` from `^\[/(dev|user|complete)\]\s*$` to the harness-aware form so the PM persona doc and the classifier agree on the accepted token shape.

### 4. Test Pi builder (unit + e2e on local ollama)
- **Task ID**: test-pi
- **Depends On**: build-pi
- **Assigned To**: pi-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit-test `parse_pi_final_text` against a captured fixture + edge cases.
- Integration-test real `pi -p --mode json` (local ollama) in a temp worktree: assert a file write + relayed text.
- Cover failure paths (non-zero exit, timeout, empty output → `DEV_REPORT_UNAVAILABLE`).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-seam, test-pi
- **Assigned To**: harness-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pluggable-builder-harness.md`; update granite docs + README index; reconcile the PTY Driver status mismatch.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-seam, test-pi, document-feature
- **Assigned To**: seam-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands; confirm success criteria including the cloud demo proof artifact.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Classifier knows harness (strict) | `python -c "from agent.granite_container.granite_classifier import classify_pm_prefix as c; r=c('[/dev:pi]\nbuild it'); print(getattr(r,'harness',None))"` | output contains `pi` |
| Classifier knows harness (fallback/drift) | `python -c "from agent.granite_container.granite_classifier import classify_pm_prefix as c; r=c('output: [/dev:pi] please build'); print(r.destination, getattr(r,'harness',None), r.compliance_miss)"` | output contains `dev pi True` (suffix survives the drift path — round-2 BLOCKER guard) |
| Default harness preserved | `python -c "from agent.granite_container.granite_classifier import classify_pm_prefix as c; r=c('[/dev]\nbuild it'); print(r.destination, getattr(r,'harness',None) or 'claude')"` | output contains `dev claude` |
| Pi parser importable | `python -c "from agent.granite_container.builder import parse_pi_final_text; print(parse_pi_final_text(''))"` | exit code 0 |
| Pi available | `command -v pi` | exit code 0 |

## Critique Results

**Round 1 — NEEDS REVISION (revision applied 2026-06-18):**

1. **BLOCKER — wrong timeout constant for Pi subprocess.** RESOLVED. Confirmed `CYCLE_IDLE_TIMEOUT_S` (`container.py:138`) is a 12-hour PTY-idle ceiling, not a turn bound. Added dedicated `PI_SUBPROCESS_TIMEOUT_S = 10 * 60` (provisional); `PiSubprocessBuilder.run_turn` uses it with `try/except subprocess.TimeoutExpired → logger.warning + return ""`. Updated Race 1, Technical Approach, Step 1, Success Criteria, Test Impact.
2. **CONCERN (2 critics) — `_last_dev_report` / empty-return ownership after extraction.** RESOLVED via new **Risk 5** + ownership rule: the container caller (not either builder) owns the `_last_dev_report` assignment and the empty-return fallback gate (`transcript_fallback_count` bump + `DEV_REPORT_UNAVAILABLE`); `run_turn` returns only final text or `""`. Verified line refs: assignment at `container.py:1374`, fallback gate at 1361-1370. Added a harness-agnostic regression test (Test Impact) and updated Data Flow step 5, Technical Approach, Rabbit Holes, Step 1.
3. **CONCERN — #1721 rebase coordination under-specified.** RESOLVED: Risk 4 now names the four preserved surfaces (`dev_transcript`/`dev_baseline`/`text_bearing_count`/`last_assistant_text`, `container.py:1343-1360`); Step 1 mandates the build-seam commit message list them by name.
4. **NIT — cloud demo validates plumbing, not PM routing judgment.** ADDRESSED: added a persona-routing acceptance check (Success Criteria + Test Impact) asserting the PM emits `[/dev:pi]` vs `[/dev]`/`[/dev:claude]` per the `prime-pm-role.md` selector guidance; Open Question 4 records the CI-cost tradeoff (live PM run vs classifier-level assertion vs persona-doc review checkpoint).

**Round 2 — NEEDS REVISION (revision applied 2026-06-18):**

1. **BLOCKER — fallback regex silently drops the harness selector.** RESOLVED. Verified against the real file: `classify_pm_prefix` has **two** regexes — strict `PREFIX_TOKEN_RE` (`granite_classifier.py:119`) and fallback `PREFIX_TOKEN_FALLBACK_RE` (`:120`, used via `.search(pm_tail[:200])` at `:223`, returns `fallback.group(1)` at `:224-230`). Round 1 only modified the strict one. The fallback fires on PM drift (`[/dev:pi]` mid-line / leading whitespace / trailing text) and would drop the `:pi` suffix, routing Pi work to claude with `compliance_miss=True` and no error signal. Fix: harness group added to **both** regexes; capture threaded via `m.group(2)` (strict, `:200-214`) **and** `fallback.group(2)` (fallback, `:224-230`); `harness=None` on both unknown returns (`:192-198`, `:232-237`). Updated Technical Approach, Step 1, Verification table (new fallback-path check), Test Impact.
2. **CONCERN (coupled) — Test Impact named a nonexistent file.** RESOLVED. Verified: `tests/unit/test_granite_classifier.py` does not exist; the real file is `tests/unit/granite_container/test_granite_classifier.py` (360 lines) and its existing `test_fallback_*` cases (line ~29-99) assert destination recovery but never check harness — they would lock in the BLOCKER green. Corrected the path in Test Impact + Step 1 `Validates`; mandated strict **and** fallback `[/dev:pi]` cases asserting `harness=="pi"` + `compliance_miss=True`.
3. **NON-BLOCKING — orphaned tool subtree on timeout.** APPLIED (cheap, tied to existing timeout handling): `PiSubprocessBuilder` now uses `subprocess.Popen(..., start_new_session=True)` + `communicate(timeout=...)` and `os.killpg(os.getpgid(proc.pid), SIGKILL)` on `TimeoutExpired`, reaping Pi **and** its `bash/edit/write` subtree. Updated Race 1, Technical Approach, Data Flow, Step 3, Test Impact.
4. **NON-BLOCKING — speculative `prepare`/`close` vs the protocol No-Go.** RECORDED + reconciled in Technical Approach: the two methods stay because `PtyClaudeBuilder` genuinely uses them; for `PiSubprocessBuilder` they are no-ops (no RPC-lifecycle hooks now — those land with the rpc fast-follow).
5. **NON-BLOCKING — rails single-source-of-truth drift.** RECORDED: Solution + Open Question 1 require the Pi persona to transclude/`@file` or back-reference `.claude/commands/granite/_prime-rails.md` (the canonical rails), not silently fork a second copy.
6. **NON-BLOCKING — `config/personas/granite/` does not exist.** RECORDED: Solution notes the build must create the directory.

**Round 3 — NEEDS REVISION (revision applied 2026-06-18):**

1. **BLOCKER — `cwd=worktree` not grounded in any container attribute.** RESOLVED. Verified against live code: `Container` has no `worktree`/`working_dir` attribute; `self.cwd` can be `None` and falls back to a sandbox tempdir (`container.py:617-619`), `effective_cwd = self.cwd or (self._sandbox[0] if self._sandbox else "")` (`:897`), and the prewarmed-pool path touches neither (early return `:612-615`). A `Popen(cwd=None)` would inherit the repo root, defeating Risk 2's isolation. Fix: thread `builder_cwd = self._dev_pty.cwd` — the dev PTY's resolved cwd (`PTYDriver` stores it at `pty_driver.py:322`, spawns claude with it at `:397`), correct in both the prewarmed-pool path (`pty_pool.py:445-447` + `bridge_adapter.py:424/443`) and self-spawned path (`container.py:624`) — into `PiSubprocessBuilder`, with a no-None guard that raises rather than spawning with `cwd=None`. This is the *identical* dir the claude builder uses. Added **Risk 6**; updated Technical Approach, Data Flow, Step 1, Step 3, Test Impact, Success Criteria. Sandbox-path caveat (no git → nothing to re-read) documented; demo runs only on the real-worktree path.
2. **BLOCKER — builder-selection rubric never authored (circular acceptance check).** RESOLVED. Verified `prime-pm-role.md` has no harness-selection content and its `:30` regex line (`^\[/(dev|user|complete)\]\s*$`) predates the `[/dev:pi]` token. Authored a **concrete task-shape rubric** in the Agent Integration section (one-shot structured edits → `pi`; interactive/multi-turn/SDLC work → `claude`; default claude when unsure; PM re-reads the diff after a pi turn) inserted verbatim into `prime-pm-role.md`, plus the `:30` regex update to the harness-aware strict form `^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`. Added **Risk 7**; updated Step 3, Agent Integration, Test Impact. The PM-routing acceptance check now measures against this specific rubric (non-circular).
3. **OQ1 RESOLVED — rails single-source-of-truth.** Pass `--append-system-prompt` **twice**: first `.claude/commands/granite/_prime-rails.md` (canonical rails, verified live that pi appends file contents from a path), then the Pi-tuned persona delta file (no forked rails). Locked in Solution.
4. **OQ2 RESOLVED — cross-vendor cloud demo model:** `--provider google --model gemini-2.5-pro` (stable, tool-capable; verified via `pi --list-models`). Gemini over anthropic to prove genuine cross-vendor; `gemini-2.5-flash` is the cheaper fallback. Locked in Technical Approach, Prerequisites, Success Criteria, Update System.

---

## Open Questions

_All open questions resolved as of round 3 (2026-06-18). Retained below for the audit trail with their resolutions._

1. **Pi persona file location/format — RESOLVED (round 3).** Single source of rails truth via **two `--append-system-prompt` flags**: `.claude/commands/granite/_prime-rails.md` (canonical, shared with claude) then `config/personas/granite/pi_dev_rails.md` (Pi-tuned dev delta only, no forked rails; header back-references the rails). Verified live that `pi --append-system-prompt <path>` appends the file's contents and the flag repeats. See Solution.
2. **Cloud demo model — RESOLVED (round 3).** `--provider google --model gemini-2.5-pro` (stable, tool-capable per `pi --list-models`), chosen over anthropic to prove genuine cross-vendor execution — the harness's whole differentiator. Cheaper fallback: `gemini-2.5-flash`. Requires `GEMINI_API_KEY`. See Technical Approach / Prerequisites / Success Criteria.
3. **`/update` Pi handling — RESOLVED (default accepted).** Verify-only warning in `scripts/update/verify.py` (matches the `gws` ladder); no auto-install, no hard-gate on Pi. See Update System.
4. **PM routing-judgment check cost (NIT) — RESOLVED (default accepted).** (b) classifier-level assertion on two representative PM outputs for CI (deterministic), plus (c) a persona-doc review checkpoint at review time; escalate to (a) a live PM-run integration test only if the selector misroutes in the demo. The rubric the check measures against is now concrete (Risk 7 / Agent Integration). See Test Impact / Success Criteria.
